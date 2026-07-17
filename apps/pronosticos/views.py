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
    resumen_historico_pronostico,
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
        modelo  = request.data.get('modelo', 'suavizacion_exponencial')
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


class ProductosMasVendidosView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        from apps.ventas.models import VentaHistorico
        from django.db.models import Sum
        top = (
            VentaHistorico.objects
            .values('articulo__codigo', 'articulo__descripcion')
            .annotate(total_vendido=Sum('cantidad_vendida'))
            .order_by('-total_vendido')[:20]
        )
        return Response(list(top))


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
