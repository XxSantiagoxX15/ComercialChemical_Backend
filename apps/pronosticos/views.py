import uuid
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.auth.permissions import IsAdminOrInventario, IsAdminOrCompras
from apps.auditoria.services import registrar_evento
from .models import Prediccion
from .services import (
    calcular_prediccion, analisis_estadistico, generar_recomendaciones_compras, calcular_eoq,
    resumen_historico_pronostico, calcular_pronosticos_masivo, articulos_riesgo_agotamiento,
    comparar_periodos, meses_pronosticados_disponibles, reporte_necesidades_mes,
    productos_mas_vendidos,
)


def _get_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', 'unknown')


def _get_usuario_id(request):
    token = getattr(request, 'auth', None)
    return token.get('usuario_id') if token else None


class PrediccionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Prediccion
        fields = '__all__'


class CalcularPrediccionView(APIView):
    permission_classes = [IsAdminOrInventario]

    def post(self, request, articulo_id):
        modelo  = request.data.get('modelo', 'auto')
        periodos = int(request.data.get('periodos', 6))
        resultado = calcular_prediccion(articulo_id, modelo=modelo, periodos=periodos)
        # Solo retorna 400 si hay un error real (no por datos insuficientes)
        if 'error' in resultado:
            return Response(resultado, status=400)
        return Response(resultado)


class PrediccionesArticuloView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request, articulo_id):
        modelo = request.query_params.get('modelo')
        qs = Prediccion.objects.filter(articulo_id=articulo_id)
        if modelo:
            qs = qs.filter(modelo=modelo)
        return Response(PrediccionSerializer(qs, many=True).data)


class AnalisisEstadisticoView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request, articulo_id):
        return Response(analisis_estadistico(articulo_id))


class ResumenHistoricoPronosticoView(APIView):
    """Línea de tiempo global (todos los artículos) para el widget del Dashboard:
    demanda histórica mensual vs. demanda proyectada mensual."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        meses = int(request.query_params.get('meses', 12))
        return Response(resumen_historico_pronostico(meses_historico=meses))


class CalcularPronosticosMasivoView(APIView):
    """Calcula el pronóstico de TODOS los artículos activos con historial de
    ventas de una sola vez, en vez de tener que hacerlo uno por uno desde la
    pantalla de Pronósticos."""
    permission_classes = [IsAdminOrInventario]

    def post(self, request):
        modelo = request.data.get('modelo', 'auto')
        periodos = int(request.data.get('periodos', 3))
        resultado = calcular_pronosticos_masivo(modelo=modelo, periodos=periodos)
        registrar_evento(
            'CALCULAR_PRONOSTICOS_MASIVO',
            f"Pronóstico masivo ({modelo}): {resultado['calculados']}/{resultado['total_articulos']} artículos",
            usuario_id=_get_usuario_id(request), entidad='prediccion', ip=_get_ip(request),
        )
        return Response(resultado)


class RiesgoAgotamientoView(APIView):
    """Días de cobertura restante por artículo (stock_actual / demanda diaria
    proyectada), ordenado del más urgente al menos urgente. Es el widget del
    Dashboard que reemplaza al análisis agregado (que esconde los artículos
    puntuales en riesgo dentro del sobre-stock del resto del catálogo)."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        dias_umbral = int(request.query_params.get('dias_umbral', 30))
        limit = int(request.query_params.get('limit', 10))
        return Response(articulos_riesgo_agotamiento(dias_umbral=dias_umbral, limit=limit))


class ProductosMasVendidosView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        limit = int(request.query_params.get('limit', 20))
        return Response(productos_mas_vendidos(limit=limit))


class MasVendidosExportView(APIView):
    """Descarga del ranking de más vendidos en PDF o Excel, a elección del usuario."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        from django.http import HttpResponse
        from .pdf import generar_pdf_mas_vendidos
        from .excel import generar_excel_mas_vendidos

        formato = request.query_params.get('formato', 'pdf')
        limit = int(request.query_params.get('limit', 20))
        datos = productos_mas_vendidos(limit=limit)

        if formato == 'excel':
            contenido = generar_excel_mas_vendidos(datos)
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            filename = 'mas_vendidos.xlsx'
        elif formato == 'pdf':
            contenido = generar_pdf_mas_vendidos(datos)
            content_type = 'application/pdf'
            filename = 'mas_vendidos.pdf'
        else:
            return Response({'error': 'El parámetro "formato" debe ser "pdf" o "excel".'}, status=400)

        response = HttpResponse(contenido, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


def _parse_parametros_comparacion(request):
    """Parsea y valida los parámetros comunes de comparar-periodos, usados
    tanto por la vista JSON como por la de exportación. Devuelve
    (kwargs_para_comparar_periodos, respuesta_de_error_o_None)."""
    import datetime

    def _parse_fecha(nombre):
        valor = request.query_params.get(nombre)
        if not valor:
            return None, Response({'error': f'Falta el parámetro "{nombre}".'}, status=400)
        try:
            return datetime.date.fromisoformat(valor), None
        except ValueError:
            return None, Response({'error': f'"{nombre}" debe tener formato YYYY-MM-DD.'}, status=400)

    desde1, err = _parse_fecha('desde1')
    if err: return None, err
    hasta1, err = _parse_fecha('hasta1')
    if err: return None, err
    desde2, err = _parse_fecha('desde2')
    if err: return None, err
    hasta2, err = _parse_fecha('hasta2')
    if err: return None, err

    articulo_id = request.query_params.get('articulo_id')
    articulo_id = int(articulo_id) if articulo_id else None
    limit = int(request.query_params.get('limit', 20))

    return {
        'desde1': desde1, 'hasta1': hasta1, 'desde2': desde2, 'hasta2': hasta2,
        'articulo_id': articulo_id, 'limit': limit,
    }, None


class ComparacionPeriodosView(APIView):
    """Compara ventas (Kardex) entre dos rangos de fechas, en total y por
    artículo — 'este mes vs. el mes pasado', 'este año vs. el año pasado', o
    cualquier par de rangos que el usuario elija."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        kwargs, err = _parse_parametros_comparacion(request)
        if err: return err
        return Response(comparar_periodos(**kwargs))


class ComparacionExportView(APIView):
    """Descarga de la comparación de periodos en PDF o Excel, a elección del usuario."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        from django.http import HttpResponse
        from .pdf import generar_pdf_comparacion
        from .excel import generar_excel_comparacion

        kwargs, err = _parse_parametros_comparacion(request)
        if err: return err
        reporte = comparar_periodos(**kwargs)

        formato = request.query_params.get('formato', 'pdf')
        if formato == 'excel':
            contenido = generar_excel_comparacion(reporte)
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            filename = 'comparacion_periodos.xlsx'
        elif formato == 'pdf':
            contenido = generar_pdf_comparacion(reporte)
            content_type = 'application/pdf'
            filename = 'comparacion_periodos.pdf'
        else:
            return Response({'error': 'El parámetro "formato" debe ser "pdf" o "excel".'}, status=400)

        response = HttpResponse(contenido, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class MesesPronosticadosView(APIView):
    """Meses (YYYY-MM) con al menos un pronóstico calculado — alimenta el
    selector del reporte de necesidades por mes."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        return Response({'meses': meses_pronosticados_disponibles()})


class ReporteNecesidadesView(APIView):
    """Reporte de necesidades de compra para un mes futuro ya pronosticado:
    qué artículos y en qué cantidad hay que comprar ese mes."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        mes = request.query_params.get('mes')
        if not mes:
            return Response({'error': 'Falta el parámetro "mes" (formato YYYY-MM).'}, status=400)
        try:
            return Response(reporte_necesidades_mes(mes))
        except ValueError:
            return Response({'error': 'El parámetro "mes" debe tener formato YYYY-MM.'}, status=400)


class ReporteNecesidadesPdfView(APIView):
    """Descarga en PDF del reporte de necesidades de compra de un mes."""
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        from django.http import HttpResponse
        from .pdf import generar_pdf_reporte_necesidades

        mes = request.query_params.get('mes')
        if not mes:
            return Response({'error': 'Falta el parámetro "mes" (formato YYYY-MM).'}, status=400)
        try:
            reporte = reporte_necesidades_mes(mes)
        except ValueError:
            return Response({'error': 'El parámetro "mes" debe tener formato YYYY-MM.'}, status=400)

        pdf_bytes = generar_pdf_reporte_necesidades(reporte)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="reporte_necesidades_{mes}.pdf"'
        return response


class RecomendacionesView(APIView):
    """Integra forecasting + compras — equiv. al Chem Movid compras service."""
    permission_classes = [IsAdminOrCompras]

    def get(self, request):
        data = generar_recomendaciones_compras()
        return Response({'recomendaciones': data, 'total': len(data)})


class EOQView(APIView):
    """Cantidad económica de pedido para un artículo."""
    permission_classes = [IsAdminOrCompras]

    def get(self, request, articulo_id):
        resultado = calcular_eoq(articulo_id)
        if 'error' in resultado:
            return Response(resultado, status=400)
        return Response(resultado)


class GenerarOrdenCompraView(APIView):
    """Cierra el ciclo pronóstico → compra: crea una OrdenCompra en estado
    'pendiente' a partir de la recomendación de pronóstico vigente para un artículo."""
    permission_classes = [IsAdminOrCompras]

    def post(self, request, articulo_id):
        from apps.compras.models import OrdenCompra
        from apps.compras.views import OrdenCompraSerializer

        recomendaciones = generar_recomendaciones_compras()
        rec = next((r for r in recomendaciones if r['articulo_id'] == articulo_id), None)
        if not rec:
            return Response({'error': 'No hay recomendación disponible para este artículo.'}, status=404)
        if rec['cantidad_recomendada'] <= 0:
            return Response({'error': 'La cantidad recomendada es 0; no se requiere generar una orden.'}, status=400)

        usuario_id = _get_usuario_id(request)
        orden = OrdenCompra.objects.create(
            numero_orden=f"OC-{uuid.uuid4().hex[:8].upper()}",
            articulo_id=articulo_id,
            cantidad_solicitada=rec['cantidad_recomendada'],
            estado='pendiente',
            usuario_id=usuario_id,
            observaciones=(
                f"Generada automáticamente desde recomendación de pronóstico "
                f"(prioridad: {rec['nivel_prioridad']}, demanda proyectada: {rec['demanda_proyectada']})."
            ),
        )
        registrar_evento(
            'CREAR_ORDEN_COMPRA', f'Orden {orden.numero_orden} generada desde recomendación de pronóstico',
            usuario_id=usuario_id, entidad='orden_compra', entidad_id=orden.id, ip=_get_ip(request),
        )
        return Response(OrdenCompraSerializer(orden).data, status=201)
