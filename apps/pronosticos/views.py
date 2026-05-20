from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.auth.permissions import IsAdminOrInventario, IsAdminOrCompras
from .models import Prediccion
from .services import calcular_prediccion, analisis_estadistico, generar_recomendaciones_compras


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
