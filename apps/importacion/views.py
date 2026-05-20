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
from apps.ventas.models import Importacion, VentaHistorico


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


def _leer_dataframe(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Detecta automáticamente la fila de encabezado en el Excel.
    Devuelve un DataFrame con columnas normalizadas (sin tildes, mayúsculas).
    """
    ext = filename.lower().rsplit('.', 1)[-1]
    engine = 'openpyxl' if ext == 'xlsx' else 'xlrd' if ext == 'xls' else None

    HEADER_KEYWORDS = {
        'CODIGO', 'CODIGO ARTICULO', 'CODIGO ITEM',
        'NOMBRE', 'CANTIDAD', 'VALOR', 'SUCURSAL',
        'CLIENTE', 'IDENTIFICACION', 'CONTEO 1', 'UNI',
        'DOC NO', 'MARCA', 'FECHA',
    }

    if engine:
        raw = pd.read_excel(io.BytesIO(file_bytes), header=None, engine=engine)
        best_row, best_score = None, 0
        for i, row in raw.iterrows():
            vals = {_norm(v) for v in row if pd.notna(v) and str(v).strip()}
            score = len(vals & HEADER_KEYWORDS)
            if score > best_score:
                best_score, best_row = score, i
        header = best_row if (best_row is not None and best_score >= 2) else 0
        df = pd.read_excel(io.BytesIO(file_bytes), header=header, engine=engine)
    else:
        df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8-sig')

    df.columns = [_norm(c) for c in df.columns]
    return df.dropna(how='all')


def _safe_float(val) -> float | None:
    """Convierte a float tolerando comas como separador de miles."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return None


def _extraer_fecha_encabezado(file_bytes: bytes, ext: str):
    """Busca 'VENTAS DEL dd/mm/yyyy AL dd/mm/yyyy' en las primeras filas."""
    try:
        engine = 'openpyxl' if ext == 'xlsx' else 'xlrd'
        raw = pd.read_excel(io.BytesIO(file_bytes), header=None, engine=engine)
        for _, row in raw.iterrows():
            for val in row:
                if pd.notna(val) and 'VENTAS DEL' in str(val).upper():
                    fechas = re.findall(r'(\d{2}/\d{2}/\d{4})', str(val))
                    if fechas:
                        return pd.to_datetime(fechas[-1], dayfirst=True).date()
    except Exception:
        pass
    return None


# ── Vista principal ────────────────────────────────────────────────────────────

class ImportacionUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminOrInventario]

    @extend_schema(
        description=(
            'Sube un archivo Excel/CSV de Toma de Inventario o Ventas por Cliente. '
            'Los datos anteriores del mismo tipo se reemplazan automáticamente.'
        )
    )
    def post(self, request):
        file = request.FILES.get('file')
        tipo_informe = request.data.get('tipo_informe')

        if not file:
            return Response({'error': 'Archivo requerido.'}, status=400)
        if tipo_informe not in ('Informe_Toma_Inventario', 'Informe_Ventas_Cliente'):
            return Response({'error': 'tipo_informe inválido.'}, status=400)

        filename = file.name
        ext = filename.lower().rsplit('.', 1)[-1]
        file_bytes = file.read()

        try:
            df = _leer_dataframe(file_bytes, filename)
        except Exception as e:
            return Response({'error': f'No se pudo leer el archivo: {e}'}, status=400)

        procesados = 0
        errores = []

        with transaction.atomic():
            # ── REEMPLAZAR datos anteriores del mismo tipo ─────────────────────
            importaciones_anteriores = Importacion.objects.filter(tipo_informe=tipo_informe)

            if tipo_informe == 'Informe_Ventas_Cliente':
                VentaHistorico.objects.filter(
                    importacion__in=importaciones_anteriores
                ).delete()
                VentaHistorico.objects.filter(importacion__isnull=True).delete()

            if tipo_informe == 'Informe_Toma_Inventario':
                # Limpiar artículos con código float antes de reimportar
                from django.db.models import Q
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
            if tipo_informe == 'Informe_Toma_Inventario':
                procesados, errores = self._procesar_toma_inventario(df)

            else:  # Informe_Ventas_Cliente
                fecha_default = _extraer_fecha_encabezado(file_bytes, ext)
                procesados, errores, articulos_creados = self._procesar_ventas_cliente(
                    df, fecha_default
                )

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

        # Asociar ventas recién creadas a esta importación
        if tipo_informe == 'Informe_Ventas_Cliente':
            VentaHistorico.objects.filter(importacion__isnull=True).update(importacion=imp)

        return Response({
            **ImportacionSerializer(imp).data,
            'mensaje': (
                f'Datos anteriores reemplazados. '
                f'{procesados} registros importados correctamente.'
                + (f' {len(errores)} filas con error.' if errores else '')
            ),
        })

    # ── Procesadores ──────────────────────────────────────────────────────────

    def _procesar_toma_inventario(self, df: pd.DataFrame):
        """
        Parsea el Informe de Toma de Inventario de Comercial Chemical.

        Estructura del Excel (sin encabezado fijo por bodega):
          CÓDIGO | CÓDIGO BARRAS | NOMBRE | UNI | CONTEO 1 | CONTEO 2

        El mismo artículo puede aparecer en múltiples bodegas.
        Estrategia: consolidar por código sumando los conteos de todas las bodegas.
        También captura GRUPO como categoría.
        """
        # ── Paso 1: leer el raw sin encabezado para capturar BODEGA y GRUPO ──
        # El df ya viene con encabezado detectado. Necesitamos rastrear
        # las filas de contexto (BODEGA:, GRUPO:) que están en la columna CODIGO.

        # Acumular: { codigo: { nombre, uni, stock_total, categoria } }
        consolidado: dict = {}
        categoria_actual = ''

        for i, row in df.iterrows():
            raw_codigo = row.get('CODIGO', row.get('CODIGO BARRAS', ''))
            codigo_str = str(raw_codigo).strip()

            if not codigo_str or codigo_str.lower() in ('nan', '', 'none'):
                # Puede ser fila de GRUPO en otra columna
                # Buscar en todas las columnas si hay texto de GRUPO
                for col_val in row:
                    val_str = str(col_val).strip()
                    if val_str.upper().startswith('GRUPO:'):
                        # Extraer nombre del grupo: "GRUPO: 001 COSMETICOS" → "COSMETICOS"
                        partes = val_str.split(' ', 2)
                        if len(partes) >= 3:
                            # Quitar número: "001 COSMETICOS" → "COSMETICOS"
                            grupo_parts = partes[2].split(' ', 1)
                            if len(grupo_parts) >= 2 and grupo_parts[0].isdigit():
                                categoria_actual = grupo_parts[1].title()
                            else:
                                categoria_actual = partes[2].title()
                        break
                continue

            codigo_norm = _norm(codigo_str)

            # Ignorar filas de encabezado/subtotal/bodega/grupo
            if any(codigo_norm.startswith(p) for p in (
                'SUBTOTAL', 'TOTAL', 'BODEGA', 'GRUPO', 'SUBGRUPO',
                'INVENTARIOS', 'MERCANCIAS', 'CODIGO', 'FECHA',
            )):
                # Detectar GRUPO en la columna CODIGO también
                if codigo_norm.startswith('GRUPO:') or 'GRUPO:' in codigo_norm:
                    partes = codigo_str.split(' ', 2)
                    if len(partes) >= 3:
                        grupo_parts = partes[2].split(' ', 1)
                        if len(grupo_parts) >= 2 and grupo_parts[0].isdigit():
                            categoria_actual = grupo_parts[1].title()
                        else:
                            categoria_actual = partes[2].title()
                continue

            # Limpiar código (quitar .0 si pandas lo convirtió a float)
            try:
                codigo_limpio = str(int(float(codigo_str)))
            except (ValueError, TypeError):
                # Código alfanumérico (ej: "001", "9999") — mantener como está
                codigo_limpio = codigo_str

            # Ignorar código "9999" (artículo genérico de prueba)
            if codigo_limpio in ('9999', '0'):
                continue

            nombre = str(row.get('NOMBRE', codigo_limpio)).strip()
            if nombre.lower() in ('nan', '', 'none'):
                nombre = codigo_limpio

            uni = str(row.get('UNI', 'UNI')).strip()
            if uni.lower() in ('nan', '', 'none'):
                uni = 'UNI'

            # Conteo 1 y Conteo 2 — sumar ambos si existen
            conteo1 = _safe_float(row.get('CONTEO 1', 0)) or 0
            conteo2 = _safe_float(row.get('CONTEO 2', 0)) or 0
            # Si solo hay conteo1, usar ese. Si hay ambos, usar conteo1 (el oficial)
            stock_fila = conteo1 if conteo1 > 0 else conteo2

            # Consolidar: si el código ya existe, sumar el stock
            if codigo_limpio in consolidado:
                consolidado[codigo_limpio]['stock'] += stock_fila
                # Actualizar categoría si la actual es más específica
                if categoria_actual and not consolidado[codigo_limpio]['categoria']:
                    consolidado[codigo_limpio]['categoria'] = categoria_actual
            else:
                consolidado[codigo_limpio] = {
                    'nombre': nombre,
                    'uni': uni,
                    'stock': stock_fila,
                    'categoria': categoria_actual,
                }

        # ── Paso 2: guardar en BD ──────────────────────────────────────────
        procesados, errores = 0, []

        for codigo, datos in consolidado.items():
            try:
                art, created = Articulo.objects.get_or_create(
                    codigo=codigo,
                    defaults={
                        'descripcion': datos['nombre'],
                        'unidad_medida': datos['uni'],
                        'stock_actual': datos['stock'],
                        'categoria': datos['categoria'] or None,
                    },
                )
                # Actualizar siempre con los datos del nuevo archivo
                art.stock_actual = datos['stock']
                art.descripcion  = datos['nombre']
                if datos['categoria']:
                    art.categoria = datos['categoria']
                art.save(update_fields=['stock_actual', 'descripcion', 'categoria', 'fecha_modificacion'])
                procesados += 1
            except Exception as e:
                errores.append({'codigo': codigo, 'error': str(e)})

        return procesados, errores

    def _procesar_ventas_cliente(self, df: pd.DataFrame, fecha_default):
        """
        Parsea el informe de Ventas por Cliente de Comercial Chemical.

        Estructura del Excel:
          FECHA | DOC No | MARCA | CÓDIGO | NOMBRE | UNI | CANTIDAD | VALOR | %
          (las filas de artículo tienen FECHA/DOC/MARCA vacíos)
          Hay filas de encabezado de cliente (NIT + nombre) y filas SUBTOTAL.

        Estrategia:
          - Rastrear el cliente actual leyendo filas donde CODIGO es un NIT (texto largo).
          - Procesar solo filas donde CODIGO sea numérico (código de artículo).
        """
        procesados, errores = 0, []
        articulos_creados = 0

        cliente_actual_codigo = None
        cliente_actual_nombre = None

        for i, row in df.iterrows():
            # Leer el valor de la columna CODIGO (ya normalizada)
            raw_codigo = row.get('CODIGO', '')
            codigo_str = str(raw_codigo).strip()

            if not codigo_str or codigo_str.lower() in ('nan', '', 'none'):
                continue

            codigo_norm = _norm(codigo_str)

            # ── Detectar fila de cliente (NIT o texto largo) ───────────────
            # Las filas de cliente tienen el NIT en CODIGO y el nombre en NOMBRE
            # Ej: "222.222.222.222" o "35.221.260"
            if re.match(r'^[\d\.\s]+$', codigo_str) and len(codigo_str) > 6:
                # Podría ser NIT — verificar que no sea código de artículo (≤4 dígitos)
                solo_digitos = re.sub(r'[\.\s]', '', codigo_str)
                if len(solo_digitos) > 6:
                    # Es un NIT de cliente
                    cliente_actual_codigo = codigo_str
                    nombre_raw = row.get('NOMBRE', '')
                    cliente_actual_nombre = str(nombre_raw).strip() if pd.notna(nombre_raw) else None
                    if cliente_actual_nombre and cliente_actual_nombre.lower() in ('nan', '', 'none'):
                        cliente_actual_nombre = None
                    continue

            # ── Ignorar filas de subtotal / total / servicios ──────────────
            if any(codigo_norm.startswith(p) for p in
                   ('SUBTOTAL', 'TOTAL', 'SERVICIOS', 'EN.', 'FECHA', 'DOC')):
                continue

            # ── Solo procesar si el código es numérico (artículo) ──────────
            try:
                # Limpiar código: quitar decimales si es entero (1011.0 → 1011)
                codigo_limpio = str(int(float(codigo_str)))
            except (ValueError, TypeError):
                continue

            try:
                descripcion = str(row.get('NOMBRE', codigo_str)).strip()
                if descripcion.lower() in ('nan', '', 'none'):
                    descripcion = codigo_str

                uni = str(row.get('UNI', 'UNI')).strip()
                if uni.lower() in ('nan', '', 'none'):
                    uni = 'UNI'

                # Fecha: columna FECHA o la del encabezado del reporte
                fecha_raw = row.get('FECHA', None)
                if fecha_raw is not None and not (isinstance(fecha_raw, float) and pd.isna(fecha_raw)):
                    try:
                        fecha = pd.to_datetime(fecha_raw, dayfirst=True).date()
                    except Exception:
                        fecha = fecha_default
                else:
                    fecha = fecha_default or date.today()

                cantidad = _safe_float(row.get('CANTIDAD', row.get('CANT', 0))) or 0
                if cantidad <= 0:
                    continue

                valor = _safe_float(row.get('VALOR', row.get('VALOR TOTAL', None)))

                # Crear o recuperar artículo usando código limpio
                art, created = Articulo.objects.get_or_create(
                    codigo=codigo_limpio,
                    defaults={'descripcion': descripcion, 'unidad_medida': uni},
                )
                if created:
                    articulos_creados += 1

                VentaHistorico.objects.create(
                    fecha=fecha,
                    codigo_cliente=cliente_actual_codigo,
                    nombre_cliente=cliente_actual_nombre,
                    articulo=art,
                    cantidad_vendida=cantidad,
                    valor_total=valor,
                    importacion=None,
                )
                procesados += 1

            except Exception as e:
                errores.append({'fila': i + 1, 'codigo': codigo_str, 'error': str(e)})

        return procesados, errores, articulos_creados


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
