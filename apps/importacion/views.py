"""
Microservicio Importación — portado a Django.
Fuente: Chem Movid backend/importacion/parsers/ y main.py
"""
import io
import pandas as pd
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework import serializers
from django.db import transaction
from drf_spectacular.utils import extend_schema

from apps.auth.permissions import IsAdminOrInventario, IsAdmin
from apps.inventario.models import Articulo
from apps.ventas.models import Importacion, VentaHistorico


class ImportacionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Importacion
        fields = '__all__'


class VentaHistoricoSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)

    class Meta:
        model = VentaHistorico
        fields = '__all__'


def _leer_dataframe(file_bytes: bytes, filename: str) -> pd.DataFrame:
    """
    Parser inteligente — detecta automáticamente la fila de encabezado en Excel.
    Soporta tanto Toma de Inventario como Ventas por Cliente.
    """
    ext = filename.lower().rsplit('.', 1)[-1]
    buf = io.BytesIO(file_bytes)
    engine = 'openpyxl' if ext == 'xlsx' else 'xlrd' if ext == 'xls' else None

    HEADER_KEYWORDS = {
        'CÓDIGO', 'CODIGO', 'CÓDIGO ARTÍCULO', 'CODIGO ARTICULO',
        'CÓDIGO ITEM', 'CODIGO ITEM', 'SUCURSAL', 'CLIENTE', 'IDENTIFICACIÓN'
    }

    if engine:
        raw = pd.read_excel(buf, header=None, engine=engine)
        for i, row in raw.iterrows():
            vals = {str(v).strip().upper() for v in row if pd.notna(v)}
            if vals & HEADER_KEYWORDS:
                buf.seek(0)
                return pd.read_excel(buf, header=i, engine=engine)
        buf.seek(0)
        return pd.read_excel(buf, engine=engine)
    else:
        return pd.read_csv(buf, encoding='utf-8-sig')


class ImportacionUploadView(APIView):
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminOrInventario]

    @extend_schema(description='Cargar archivo Excel/CSV — Toma de Inventario o Ventas por Cliente')
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

        df.columns = [str(c).strip().upper() for c in df.columns]
        df = df.dropna(how='all')

        procesados = 0
        errores = []

        with transaction.atomic():
            if tipo_informe == 'Informe_Toma_Inventario':
                # Portado de Chem Movid: importacion/parsers/toma_inventario.py
                for i, row in df.iterrows():
                    codigo = str(row.get('CÓDIGO', row.get('CODIGO', ''))).strip()
                    if not codigo or codigo.lower() in ('nan', ''):
                        continue
                    if any(codigo.upper().startswith(p) for p in
                           ('SUBTOTAL', 'TOTAL', 'BODEGA', 'GRUPO', 'INVENTARIOS', 'MERCANCIAS')):
                        continue
                    nombre = str(row.get('NOMBRE', codigo)).strip()
                    uni = str(row.get('UNI', 'UNI')).strip()
                    conteo = row.get('CONTEO 1', 0)
                    stock = float(conteo) if pd.notna(conteo) else 0
                    try:
                        art, _ = Articulo.objects.get_or_create(
                            codigo=codigo,
                            defaults={'descripcion': nombre, 'unidad_medida': uni, 'stock_actual': stock}
                        )
                        art.stock_actual = stock
                        art.save(update_fields=['stock_actual', 'fecha_modificacion'])
                        procesados += 1
                    except Exception as e:
                        errores.append({'fila': i + 1, 'error': str(e)})

            else:  # Informe_Ventas_Cliente
                # Extraer fecha del encabezado del archivo (fila con "VENTAS DEL...")
                fecha_default = None
                try:
                    raw_check = pd.read_excel(io.BytesIO(file_bytes), header=None, engine='openpyxl' if ext == 'xlsx' else 'xlrd')
                    for _, row in raw_check.iterrows():
                        for val in row:
                            if pd.notna(val) and 'VENTAS DEL' in str(val).upper():
                                import re
                                fechas = re.findall(r'(\d{2}/\d{2}/\d{4})', str(val))
                                if fechas:
                                    fecha_default = pd.to_datetime(fechas[-1], dayfirst=True).date()
                                break
                        if fecha_default:
                            break
                except Exception:
                    pass

                for i, row in df.iterrows():
                    try:
                        # Soportar múltiples nombres de columna para código
                        codigo = str(
                            row.get('CÓDIGO ITEM',
                            row.get('CODIGO ITEM',
                            row.get('CÓDIGO ARTÍCULO',
                            row.get('CODIGO ARTICULO',
                            row.get('CÓDIGO',
                            row.get('CODIGO', ''))))))
                        ).strip()

                        if not codigo or codigo.lower() in ('nan', '', 'none'):
                            continue

                        # Soportar múltiples nombres de columna para descripción
                        descripcion = str(
                            row.get('NOMBRE',
                            row.get('DESCRIPCIÓN ARTÍCULO',
                            row.get('DESCRIPCION ARTICULO',
                            row.get('NOMBRE ARTÍCULO', codigo))))
                        ).strip()
                        if descripcion.lower() in ('nan', '', 'none'):
                            descripcion = codigo

                        # Fecha: columna FECHA o la extraída del encabezado
                        fecha_raw = row.get('FECHA', None)
                        if fecha_raw is not None and not (isinstance(fecha_raw, float) and pd.isna(fecha_raw)):
                            try:
                                fecha = pd.to_datetime(fecha_raw, dayfirst=True).date()
                            except Exception:
                                fecha = fecha_default
                        else:
                            fecha = fecha_default

                        if fecha is None:
                            from datetime import date
                            fecha = date.today()

                        # Cantidad
                        cantidad_raw = row.get('CANTIDAD', row.get('CANT', 0))
                        cantidad = float(cantidad_raw) if pd.notna(cantidad_raw) else 0
                        if cantidad <= 0:
                            continue

                        # Valor: puede llamarse VALOR, VALOR TOTAL, VALOR VENTA
                        valor_raw = row.get('VALOR',
                                    row.get('VALOR TOTAL',
                                    row.get('VALOR VENTA', None)))
                        valor = float(valor_raw) if valor_raw is not None and pd.notna(valor_raw) else None

                        # Cliente
                        codigo_cliente = str(row.get('IDENTIFICACIÓN',
                                            row.get('IDENTIFICACION',
                                            row.get('CÓDIGO CLIENTE',
                                            row.get('CODIGO CLIENTE', ''))))).strip()
                        if codigo_cliente.lower() in ('nan', '', 'none'):
                            codigo_cliente = None

                        nombre_cliente = str(row.get('CLIENTE',
                                            row.get('NOMBRE CLIENTE', ''))).strip()
                        if nombre_cliente.lower() in ('nan', '', 'none'):
                            nombre_cliente = None

                        art, _ = Articulo.objects.get_or_create(
                            codigo=codigo,
                            defaults={'descripcion': descripcion, 'unidad_medida': 'UNI'}
                        )
                        VentaHistorico.objects.create(
                            fecha=fecha,
                            codigo_cliente=codigo_cliente,
                            nombre_cliente=nombre_cliente,
                            articulo=art,
                            cantidad_vendida=cantidad,
                            valor_total=valor,
                        )
                        procesados += 1
                    except Exception as e:
                        errores.append({'fila': i + 1, 'error': str(e)})

        resultado = 'exito' if not errores else ('parcial' if procesados > 0 else 'error')
        imp = Importacion.objects.create(
            nombre_archivo=filename, tipo_informe=tipo_informe, formato=ext,
            registros_procesados=procesados, registros_error=len(errores),
            resultado=resultado, reporte_errores=errores or None,
        )
        return Response(ImportacionSerializer(imp).data)


class ImportacionHistorialView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        return Response(ImportacionSerializer(Importacion.objects.all(), many=True).data)


class VentaHistoricoListView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        articulo_id = request.query_params.get('articulo_id')
        qs = VentaHistorico.objects.select_related('articulo')
        if articulo_id:
            qs = qs.filter(articulo_id=articulo_id)
        return Response(VentaHistoricoSerializer(qs[:500], many=True).data)
