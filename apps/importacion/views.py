"""
Microservicio Importación — portado a Django.
Fuente: Chem Movid backend/importacion/parsers/ y main.py

Estrategia de importación:
  - Kardex: cada archivo trae solo el periodo nuevo (no el historial completo),
    así que sus filas se SUMAN al histórico ya importado. Las filas que ya
    existían de una importación anterior se detectan y se omiten para no
    duplicar nada si el mismo periodo se sube dos veces por error.
  - Saldos vs Existencias Mínimas: es una foto del stock físico A HOY, así que
    cada artículo del archivo actualiza su stock actual/mínimo, generando un
    movimiento de "Ajuste" que concilia contra el último movimiento conocido
    (para que el historial de Movimientos siempre cuadre con Stock Actual).
"""
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from django.db import transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import IsAdmin, IsAdminOrInventario
from apps.inventario.models import Articulo, MovimientoInventario
from apps.ventas.models import Importacion, VentaHistorico, SaldoHistorico


# ── Serializers ────────────────────────────────────────────────────────────────

class ImportacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Importacion
        fields = '__all__'


class VentaHistoricoSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)

    class Meta:
        model = VentaHistorico
        fields = '__all__'


# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm(s) -> str:
    """Quita tildes, espacios extra y pasa a mayúsculas."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(s))
        if unicodedata.category(c) != 'Mn'
    ).upper().strip()


def _leer_raw(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """Lee el archivo completo SIN encabezado (todas las columnas quedan indexadas
    0,1,2...) para poder parsear manualmente reportes jerárquicos fila por fila."""
    ext = filename.lower().rsplit('.', 1)[-1]
    engine = 'openpyxl' if ext == 'xlsx' else 'xlrd' if ext == 'xls' else None
    if engine:
        return pd.read_excel(io.BytesIO(file_bytes), header=None, engine=engine)
    return pd.read_csv(io.BytesIO(file_bytes), header=None, encoding='utf-8-sig')


def _encontrar_fila_header(df: pd.DataFrame, keywords_requeridas: set, max_filas: int = 15) -> int:
    """Busca la primera fila (entre las primeras max_filas) cuyas celdas contengan
    todas las keywords_requeridas (normalizadas). Los reportes traen unas filas de
    título/metadata antes del encabezado real de columnas."""
    limite = min(max_filas, len(df))
    for i in range(limite):
        vals = {_norm(v) for v in df.iloc[i] if pd.notna(v) and str(v).strip()}
        if keywords_requeridas <= vals:
            return i
    return 0


def _safe_float(val) -> float | None:
    """Convierte a float tolerando comas como separador de miles."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _dec(val: float | None, places: int) -> Decimal | None:
    """Convierte un float a Decimal redondeado, para comparar contra los campos
    DecimalField ya guardados en base de datos. Comparar float contra Decimal
    directamente es poco confiable (ej: 8575.1 == Decimal('8575.10') da False
    por cómo los binarios de punto flotante representan los decimales), lo que
    rompe cualquier detección de duplicados basada en esos valores."""
    if val is None:
        return None
    return Decimal(str(round(val, places)))


def _parse_fecha(val):
    """Parsea una celda de fecha, ya venga como Timestamp/datetime (Excel la
    tipó como fecha) o como texto 'dd/mm/yyyy'."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if hasattr(val, 'date') and callable(getattr(val, 'date')):
        try:
            return val.date()
        except Exception:
            pass
    try:
        return pd.to_datetime(str(val), dayfirst=True).date()
    except Exception:
        return None


# ── Vista principal ────────────────────────────────────────────────────────────

class ImportacionUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminOrInventario]

    @extend_schema(
        description=(
            'Sube un archivo Excel/CSV de Saldos vs Existencias Mínimas o Kardex. '
            'Los datos se SUMAN al histórico ya importado (no lo reemplazan); '
            'las filas que ya existían de una importación anterior se omiten.'
        )
    )
    def post(self, request):
        file = request.FILES.get('file')
        tipo_informe = request.data.get('tipo_informe')

        if not file:
            return Response({'error': 'Archivo requerido.'}, status=400)
        if tipo_informe not in ('Informe_Saldos_Existencias_Minimas', 'Informe_Kardex'):
            return Response({'error': 'tipo_informe inválido.'}, status=400)

        filename = file.name
        ext = filename.lower().rsplit('.', 1)[-1]
        file_bytes = file.read()

        try:
            df = _leer_raw(file_bytes, filename)
        except Exception as e:
            return Response({'error': f'No se pudo leer el archivo: {e}'}, status=400)

        procesados = 0
        errores = []
        movimiento_ids = []
        omitidos = 0

        with transaction.atomic():
            if tipo_informe == 'Informe_Saldos_Existencias_Minimas':
                # Limpiar artículos con código float antes de reimportar
                arts_float = Articulo.objects.filter(codigo__regex=r'^\d+\.\d+$')
                for art_f in arts_float:
                    try:
                        cod_limpio = str(int(float(art_f.codigo)))
                        art_ok = Articulo.objects.filter(codigo=cod_limpio).exclude(pk=art_f.pk).first()
                        if art_ok:
                            VentaHistorico.objects.filter(articulo=art_f).update(articulo=art_ok)
                            art_f.delete()
                        else:
                            art_f.codigo = cod_limpio
                            art_f.save(update_fields=['codigo'])
                    except Exception:
                        pass

            # ── Procesar nuevo archivo: se SUMA al histórico existente ─────────
            # (cada Kardex trae solo el periodo nuevo, no el historial completo,
            # así que ya no se borra nada de importaciones anteriores; los
            # procesadores se encargan de no duplicar filas ya importadas).
            if tipo_informe == 'Informe_Saldos_Existencias_Minimas':
                procesados, errores, movimiento_ids = self._procesar_saldos_existencias_minimas(df)

            else:  # Informe_Kardex
                procesados, errores, movimiento_ids, omitidos = self._procesar_kardex(df)

        resultado = 'exito' if not errores else ('parcial' if procesados > 0 else 'error')
        imp = Importacion.objects.create(
            nombre_archivo=filename,
            tipo_informe=tipo_informe,
            formato=ext,
            registros_procesados=procesados,
            registros_error=len(errores),
            resultado=resultado,
            reporte_errores=errores or None,
        )

        # Asociar ventas, saldos y movimientos recién creados a esta importación.
        # importacion__isnull=True solo matchea lo que se acaba de insertar en esta
        # corrida: lo de importaciones anteriores ya tiene su propio FK puesto, y
        # los movimientos registrados a mano desde la app nunca se tocan porque se
        # identifican por id explícito, no por este filtro.
        if tipo_informe == 'Informe_Kardex':
            VentaHistorico.objects.filter(importacion__isnull=True).update(importacion=imp)
            SaldoHistorico.objects.filter(importacion__isnull=True).update(importacion=imp)

        if movimiento_ids:
            MovimientoInventario.objects.filter(id__in=movimiento_ids).update(importacion=imp)

        mensaje = f'{procesados} registros importados correctamente.'
        if omitidos:
            mensaje += f' {omitidos} filas ya existían de una importación anterior y se omitieron.'
        if errores:
            mensaje += f' {len(errores)} filas con error.'

        return Response({
            **ImportacionSerializer(imp).data,
            'mensaje': mensaje,
        })

    # ── Procesadores ──────────────────────────────────────────────────────────

    def _procesar_saldos_existencias_minimas(self, df: pd.DataFrame):
        """
        Parsea el Informe "Saldos vs Existencias Mínimas" de Comercial Chemical.

        Estructura del Excel (jerárquico por bodega, sin encabezado fijo):
          CÓDIGO | NOMBRE | UNI | EXISTENCIA MINIMA | SALDO | CANTIDAD A REORDENAR | COSTO TOTAL

        El mismo artículo puede aparecer en múltiples bodegas. Estrategia:
        consolidar por código sumando el saldo (stock total real) de todas las
        bodegas, y tomando la existencia mínima más alta configurada.
        """
        header_idx = _encontrar_fila_header(df, {'CODIGO', 'NOMBRE', 'SALDO'})
        consolidado: dict = {}

        for i in range(header_idx + 1, len(df)):
            row = df.iloc[i]
            raw_codigo = row[0]
            if pd.isna(raw_codigo):
                continue
            codigo_str = str(raw_codigo).strip()
            if not codigo_str:
                continue

            codigo_norm = _norm(codigo_str)
            if any(codigo_norm.startswith(p) for p in ('SUBTOTAL', 'TOTAL', 'CODIGO')):
                continue

            # Limpiar código (quitar .0 si pandas lo convirtió a float)
            try:
                codigo_limpio = str(int(float(codigo_str)))
            except (ValueError, TypeError):
                codigo_limpio = codigo_str  # alfanumérico, mantener tal cual

            if codigo_limpio in ('9999', '0'):
                continue

            nombre = str(row[1]).strip() if pd.notna(row[1]) else codigo_limpio
            if not nombre or nombre.lower() == 'nan':
                nombre = codigo_limpio

            uni = str(row[2]).strip() if pd.notna(row[2]) else 'UNI'
            if not uni or uni.lower() == 'nan':
                uni = 'UNI'

            existencia_minima = _safe_float(row[3]) or 0
            saldo = _safe_float(row[4]) or 0

            if codigo_limpio in consolidado:
                consolidado[codigo_limpio]['saldo'] += saldo
                consolidado[codigo_limpio]['existencia_minima'] = max(
                    consolidado[codigo_limpio]['existencia_minima'], existencia_minima
                )
            else:
                consolidado[codigo_limpio] = {
                    'nombre': nombre, 'uni': uni,
                    'saldo': saldo, 'existencia_minima': existencia_minima,
                }

        procesados, errores = 0, []
        ajustes_batch = []
        momento_import = timezone.now()

        for codigo, datos in consolidado.items():
            try:
                art, creado = Articulo.objects.get_or_create(
                    codigo=codigo,
                    defaults={
                        'descripcion': datos['nombre'],
                        'unidad_medida': datos['uni'],
                        'stock_actual': datos['saldo'],
                        'stock_minimo': datos['existencia_minima'],
                    },
                )
                saldo_nuevo = datos['saldo']

                # Este archivo trae el stock físico real A HOY, pero el Kardex (u otro
                # ajuste anterior) puede dejar el artículo en un valor distinto — sin un
                # movimiento que lo explique, el historial no cuadra con Stock Actual.
                # Si el artículo es nuevo no hay nada que conciliar todavía.
                if not creado:
                    ultimo_mov = (
                        MovimientoInventario.objects
                        .filter(articulo=art)
                        .order_by('-fecha_movimiento', '-id')
                        .first()
                    )
                    # Comparar en Decimal, no en float: saldo_nuevo puede venir de
                    # sumar el saldo de varias bodegas (arrastra ruido de punto
                    # flotante) y stock_resultante ya es Decimal — comparándolos
                    # como float aparecían "ajustes" de 0.001 que no eran reales.
                    stock_esperado = ultimo_mov.stock_resultante if ultimo_mov else _dec(float(art.stock_actual), 3)
                    saldo_nuevo_dec = _dec(saldo_nuevo, 3)
                    diferencia = saldo_nuevo_dec - stock_esperado
                    if abs(diferencia) >= Decimal('0.001'):
                        ajustes_batch.append(MovimientoInventario(
                            articulo=art,
                            tipo_movimiento='ajuste',
                            cantidad=abs(diferencia),
                            stock_anterior=stock_esperado,
                            stock_resultante=saldo_nuevo_dec,
                            usuario=None,
                            observaciones=(
                                'Ajuste automático de conciliación con Informe Saldos vs '
                                'Existencias Mínimas (stock físico real a la fecha de importación).'
                            ),
                            importacion=None,
                            fecha_movimiento=momento_import,
                        ))

                art.stock_actual  = saldo_nuevo
                art.stock_minimo  = datos['existencia_minima']
                art.descripcion   = datos['nombre']
                art.save(update_fields=['stock_actual', 'stock_minimo', 'descripcion', 'fecha_modificacion'])
                procesados += 1
            except Exception as e:
                errores.append({'codigo': codigo, 'error': str(e)})

        MovimientoInventario.objects.bulk_create(ajustes_batch, batch_size=2000)

        # Mismo problema que con el Kardex: bulk_create vía pymysql no devuelve ids.
        # Se recuperan por ventana de tiempo (momento_import) en vez de por texto,
        # porque en una futura reimportación ya podría haber ajustes viejos con el
        # mismo texto y con importacion=None (al borrarse la Importación anterior,
        # el FK se pone en null por on_delete=SET_NULL).
        movimiento_ids = list(
            MovimientoInventario.objects.filter(
                importacion__isnull=True,
                tipo_movimiento='ajuste',
                fecha_movimiento__gte=momento_import,
            ).values_list('id', flat=True)
        )
        return procesados, errores, movimiento_ids

    # Conceptos del Kardex que representan una venta real a cliente (demanda).
    # Se excluyen deliberadamente: "SALDO QUE VIENE" (saldo inicial, no es movimiento),
    # "CREACION DE KITS DE PRODUCTOS" (ensamblaje interno, no demanda de cliente —
    # infla la demanda tanto del kit como de sus componentes si se cuenta),
    # compras a proveedor, traslados entre bodegas y uso propio.
    CONCEPTOS_VENTA = {'FACTURACION PUNTO DE VENTA', 'FACTURA A CLIENTES', 'REMISION A CLIENTES'}

    def _procesar_kardex(self, df: pd.DataFrame):
        """
        Parsea el Informe Kardex de Comercial Chemical: un libro de movimientos
        jerárquico (bodega → grupo → artículo → transacciones), donde cada
        artículo trae una fila de encabezado (código + nombre) seguida de sus
        filas de transacción (fecha, concepto, entradas, salidas, saldo, valor).

        Estructura de columnas: FECHA | PR | DOC No | CONCEPTO | ENTRADAS |
        SALIDAS | SALDO | VALOR | COSTO UNITARIO

        Además de las ventas (VentaHistorico), guarda el SALDO de CADA
        transacción —sin filtrar por concepto— en SaldoHistorico. Es la única
        fuente que permite reconstruir cómo evolucionó el inventario real mes
        a mes (VentaHistorico solo captura demanda, no existencias).

        También reconstruye cada transacción con entradas/salidas como un
        MovimientoInventario histórico, para que el Kardex importado se vea
        en la pantalla de Movimientos junto con los movimientos manuales
        (marcados con importacion=None para poder distinguirlos siempre).

        Cada archivo trae solo el periodo nuevo (no el historial completo), así
        que este importador SUMA sus filas al histórico ya existente en vez de
        reemplazarlo. Para no duplicar nada si el mismo periodo se sube dos
        veces por error, se compara contra lo que ya hay en base de datos antes
        de insertar (ventas por artículo+fecha+cantidad+valor, saldos por
        artículo+fecha+saldo, movimientos por artículo+fecha+observaciones —
        el texto de observaciones incluye el Doc No, así que es prácticamente
        único por transacción real).
        """
        header_idx = _encontrar_fila_header(df, {'FECHA', 'CONCEPTO', 'SALDO'})
        procesados, errores, omitidos = 0, [], 0
        codigo_actual, nombre_actual = None, None
        articulos_cache: dict = {}
        saldos_batch = []
        movimientos_batch = []
        ultimo_saldo_por_articulo: dict = {}

        def _get_articulo(codigo, nombre):
            if codigo not in articulos_cache:
                art, _ = Articulo.objects.get_or_create(
                    codigo=codigo,
                    defaults={'descripcion': nombre or codigo, 'unidad_medida': 'UNI'},
                )
                articulos_cache[codigo] = art
            return articulos_cache[codigo]

        ventas_existentes = set(
            VentaHistorico.objects
            .values_list('articulo__codigo', 'fecha', 'cantidad_vendida', 'valor_total')
        )
        saldos_existentes = set(
            SaldoHistorico.objects
            .values_list('articulo__codigo', 'fecha', 'saldo')
        )
        movimientos_existentes = set(
            MovimientoInventario.objects
            .filter(observaciones__startswith='Importado del Kardex —')
            .values_list('articulo__codigo', 'fecha_movimiento', 'observaciones')
        )

        for i in range(header_idx + 1, len(df)):
            row = df.iloc[i]
            col0, col1, col2, col3 = row[0], row[1], row[2], row[3]
            col0_str = str(col0).strip() if pd.notna(col0) else ''
            col0_norm = _norm(col0_str)

            # ── Fila de encabezado de artículo: col0 vacío, código y nombre en col1/col2 ──
            if not col0_str and pd.notna(col1) and pd.notna(col2):
                codigo_raw = str(col1).strip()
                try:
                    codigo_actual = str(int(float(codigo_raw)))
                except (ValueError, TypeError):
                    codigo_actual = codigo_raw
                nombre_raw = str(col2).strip()
                nombre_actual = re.sub(r'\s+UNI$', '', nombre_raw, flags=re.IGNORECASE).strip() or nombre_raw
                # Si este artículo ya tiene movimientos de una importación anterior,
                # se retoma su último saldo conocido en vez de adivinarlo — si no,
                # la primera transacción de este archivo quedaría con un
                # stock_anterior estimado en vez del real.
                if codigo_actual not in ultimo_saldo_por_articulo:
                    ultimo_mov = (
                        MovimientoInventario.objects
                        .filter(articulo__codigo=codigo_actual)
                        .order_by('-fecha_movimiento', '-id')
                        .first()
                    )
                    if ultimo_mov:
                        ultimo_saldo_por_articulo[codigo_actual] = float(ultimo_mov.stock_resultante)
                continue

            # ── Filas estructurales: subtotales, encabezados de bodega/grupo ──
            if any(col0_norm.startswith(p) for p in
                   ('SUBTOTAL', 'TOTAL', 'BODEGA', 'GRUPO', 'INVENTARIOS')):
                continue

            # ── Fila de transacción: requiere artículo en contexto y fecha ──
            if not codigo_actual or not col0_str:
                continue

            fecha = _parse_fecha(col0)
            if not fecha:
                continue

            # Saldo de ESTA transacción, sea cual sea su concepto — alimenta
            # el histórico de existencias reales (no solo de ventas).
            saldo_val = _safe_float(row[6])
            if saldo_val is not None:
                saldo_key = (codigo_actual, fecha, _dec(saldo_val, 3))
                if saldo_key not in saldos_existentes:
                    saldos_batch.append((codigo_actual, nombre_actual, fecha, saldo_val))
                    saldos_existentes.add(saldo_key)

            concepto = str(col3).strip() if pd.notna(col3) else ''
            concepto_norm = _norm(concepto)
            doc_no = str(col2).strip() if pd.notna(col2) else ''
            entradas = _safe_float(row[4]) or 0
            salidas = _safe_float(row[5]) or 0

            # ── Reconstruir como movimiento histórico (independiente de si es venta) ──
            if saldo_val is not None and (entradas > 0 or salidas > 0):
                if 'CREACION DE KITS' in concepto_norm:
                    tipo_mov = 'produccion_kit'
                elif 'AJUSTE' in concepto_norm:
                    tipo_mov = 'ajuste'
                elif entradas > 0:
                    tipo_mov = 'entrada'
                else:
                    tipo_mov = 'salida'
                cantidad_mov = entradas if entradas > 0 else salidas

                anterior = ultimo_saldo_por_articulo.get(codigo_actual)
                if anterior is None:
                    # Sin historial previo ni en este archivo ni en la base: se
                    # estima el saldo previo restando/sumando esta misma cantidad.
                    anterior = saldo_val - cantidad_mov if tipo_mov != 'salida' else saldo_val + cantidad_mov

                observaciones = f"Importado del Kardex — Doc {doc_no or 's/n'}, {concepto}"
                fecha_aware = timezone.make_aware(datetime.combine(fecha, datetime.min.time()))
                mov_key = (codigo_actual, fecha_aware, observaciones)
                if mov_key in movimientos_existentes:
                    omitidos += 1
                else:
                    movimientos_existentes.add(mov_key)
                    movimientos_batch.append({
                        'codigo': codigo_actual, 'nombre': nombre_actual, 'fecha': fecha,
                        'tipo': tipo_mov, 'cantidad': cantidad_mov,
                        'stock_anterior': anterior, 'stock_resultante': saldo_val,
                        'observaciones': observaciones,
                    })

            if saldo_val is not None:
                ultimo_saldo_por_articulo[codigo_actual] = saldo_val

            # ── Ventas (histórico de demanda para pronósticos) ──
            if concepto_norm not in self.CONCEPTOS_VENTA:
                continue
            if salidas <= 0:
                continue

            valor = _safe_float(row[7])
            valor_abs = abs(valor) if valor is not None else None

            venta_key = (codigo_actual, fecha, _dec(salidas, 3), _dec(valor_abs, 2))
            if venta_key in ventas_existentes:
                omitidos += 1
                continue
            ventas_existentes.add(venta_key)

            try:
                art = _get_articulo(codigo_actual, nombre_actual)
                VentaHistorico.objects.create(
                    fecha=fecha,
                    articulo=art,
                    cantidad_vendida=salidas,
                    valor_total=valor_abs,
                    importacion=None,
                )
                procesados += 1
            except Exception as e:
                errores.append({'fila': i + 1, 'codigo': codigo_actual, 'error': str(e)})

        saldo_objs = [
            SaldoHistorico(articulo=_get_articulo(codigo, nombre), fecha=fecha, saldo=saldo_val, importacion=None)
            for codigo, nombre, fecha, saldo_val in saldos_batch
        ]
        SaldoHistorico.objects.bulk_create(saldo_objs, batch_size=2000)

        mov_objs = [
            MovimientoInventario(
                articulo=_get_articulo(m['codigo'], m['nombre']),
                tipo_movimiento=m['tipo'],
                cantidad=m['cantidad'],
                stock_anterior=m['stock_anterior'],
                stock_resultante=m['stock_resultante'],
                usuario=None,
                observaciones=m['observaciones'],
                importacion=None,
                fecha_movimiento=timezone.make_aware(datetime.combine(m['fecha'], datetime.min.time())),
            )
            for m in movimientos_batch
        ]
        MovimientoInventario.objects.bulk_create(mov_objs, batch_size=2000)

        # MySQL vía pymysql no devuelve los ids generados en bulk_create, así que
        # no podemos usar los objetos de `mov_objs` para identificar qué filas son
        # de esta importación. Como ya no se borran las Importaciones anteriores,
        # los movimientos de corridas previas siempre tienen su importacion_id ya
        # puesto — importacion__isnull=True solo puede matchear lo recién
        # insertado acá (los movimientos manuales usan otro texto en observaciones).
        movimiento_ids = list(
            MovimientoInventario.objects.filter(
                importacion__isnull=True,
                observaciones__startswith='Importado del Kardex —',
            ).values_list('id', flat=True)
        )
        return procesados, errores, movimiento_ids, omitidos


# ── Vistas auxiliares ──────────────────────────────────────────────────────────

class ImportacionHistorialView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(
            ImportacionSerializer(Importacion.objects.all(), many=True).data
        )


class VentaHistoricoListView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        articulo_id = request.query_params.get('articulo_id')
        qs = VentaHistorico.objects.select_related('articulo')
        if articulo_id:
            qs = qs.filter(articulo_id=articulo_id)
        return Response(VentaHistoricoSerializer(qs[:500], many=True).data)


class LimpiarArticulosFloatView(APIView):
    """
    Limpia artículos con código en formato float (ej: '1011.0' → '1011').
    Fusiona ventas del duplicado al artículo correcto y elimina el duplicado.
    """
    permission_classes = [IsAdmin]

    def post(self, request):
        from django.db.models import Q
        import re

        fusionados = 0
        eliminados = 0
        errores = []

        # Buscar artículos cuyo código termina en .0 o tiene decimales innecesarios
        articulos_float = Articulo.objects.filter(codigo__regex=r'^\d+\.\d+$')

        with transaction.atomic():
            for art_float in articulos_float:
                try:
                    codigo_limpio = str(int(float(art_float.codigo)))
                    art_correcto = Articulo.objects.filter(codigo=codigo_limpio).first()

                    if art_correcto and art_correcto.pk != art_float.pk:
                        # Reasignar ventas al artículo correcto
                        VentaHistorico.objects.filter(articulo=art_float).update(articulo=art_correcto)
                        art_float.delete()
                        fusionados += 1
                    elif not art_correcto:
                        # Renombrar el artículo float al código limpio
                        art_float.codigo = codigo_limpio
                        art_float.save(update_fields=['codigo'])
                        fusionados += 1
                    eliminados += 1
                except Exception as e:
                    errores.append({'codigo': art_float.codigo, 'error': str(e)})

        return Response({
            'fusionados': fusionados,
            'eliminados': eliminados,
            'errores': errores,
            'mensaje': f'{fusionados} artículos corregidos.'
        })
