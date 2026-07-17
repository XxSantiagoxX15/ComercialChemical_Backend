"""
Microservicio Importación — portado a Django.
Fuente: Chem Movid backend/importacion/parsers/ y main.py

Estrategia de importación:
  - Al subir un nuevo archivo del mismo tipo (tipo_informe), se BORRAN los datos
    anteriores de ese tipo y se reemplazan con los del nuevo archivo.
  - Esto garantiza que siempre se trabaja con la información más reciente.
"""
import io
import re
import unicodedata
from datetime import date

import pandas as pd
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth.permissions import IsAdmin, IsAdminOrInventario
from apps.inventario.models import Articulo
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
            'Los datos anteriores del mismo tipo se reemplazan automáticamente.'
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

        with transaction.atomic():
            # ── REEMPLAZAR datos anteriores del mismo tipo ─────────────────────
            importaciones_anteriores = Importacion.objects.filter(tipo_informe=tipo_informe)

            if tipo_informe == 'Informe_Kardex':
                VentaHistorico.objects.filter(
                    importacion__in=importaciones_anteriores
                ).delete()
                VentaHistorico.objects.filter(importacion__isnull=True).delete()
                SaldoHistorico.objects.all().delete()

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

            importaciones_anteriores.delete()

            # ── Procesar nuevo archivo ─────────────────────────────────────────
            if tipo_informe == 'Informe_Saldos_Existencias_Minimas':
                procesados, errores = self._procesar_saldos_existencias_minimas(df)

            else:  # Informe_Kardex
                procesados, errores = self._procesar_kardex(df)

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

        # Asociar ventas y saldos recién creados a esta importación
        if tipo_informe == 'Informe_Kardex':
            VentaHistorico.objects.filter(importacion__isnull=True).update(importacion=imp)
            SaldoHistorico.objects.filter(importacion__isnull=True).update(importacion=imp)

        return Response({
            **ImportacionSerializer(imp).data,
            'mensaje': (
                f'Datos anteriores reemplazados. '
                f'{procesados} registros importados correctamente.'
                + (f' {len(errores)} filas con error.' if errores else '')
            ),
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
        for codigo, datos in consolidado.items():
            try:
                art, _ = Articulo.objects.get_or_create(
                    codigo=codigo,
                    defaults={
                        'descripcion': datos['nombre'],
                        'unidad_medida': datos['uni'],
                        'stock_actual': datos['saldo'],
                        'stock_minimo': datos['existencia_minima'],
                    },
                )
                art.stock_actual  = datos['saldo']
                art.stock_minimo  = datos['existencia_minima']
                art.descripcion   = datos['nombre']
                art.save(update_fields=['stock_actual', 'stock_minimo', 'descripcion', 'fecha_modificacion'])
                procesados += 1
            except Exception as e:
                errores.append({'codigo': codigo, 'error': str(e)})

        return procesados, errores

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
        """
        header_idx = _encontrar_fila_header(df, {'FECHA', 'CONCEPTO', 'SALDO'})
        procesados, errores = 0, []
        codigo_actual, nombre_actual = None, None
        articulos_cache: dict = {}
        saldos_batch = []

        def _get_articulo(codigo, nombre):
            if codigo not in articulos_cache:
                art, _ = Articulo.objects.get_or_create(
                    codigo=codigo,
                    defaults={'descripcion': nombre or codigo, 'unidad_medida': 'UNI'},
                )
                articulos_cache[codigo] = art
            return articulos_cache[codigo]

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
                saldos_batch.append((codigo_actual, nombre_actual, fecha, saldo_val))

            concepto = str(col3).strip() if pd.notna(col3) else ''
            if _norm(concepto) not in self.CONCEPTOS_VENTA:
                continue

            salidas = _safe_float(row[5]) or 0
            if salidas <= 0:
                continue

            valor = _safe_float(row[7])
            valor_abs = abs(valor) if valor is not None else None

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

        return procesados, errores


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
