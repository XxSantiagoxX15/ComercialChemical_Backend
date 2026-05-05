import uuid
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.auth.permissions import IsAdminOrCompras
from apps.auditoria.services import registrar_evento
from apps.inventario.services import MovimientoService
from .models import OrdenCompra


class OrdenCompraSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)

    class Meta:
        model = OrdenCompra
        fields = '__all__'


def _get_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', 'unknown')

def _get_usuario_id(request):
    token = getattr(request, 'auth', None)
    return token.get('usuario_id') if token else None


class OrdenCompraListView(APIView):
    permission_classes = [IsAdminOrCompras]

    def get(self, request):
        estado = request.query_params.get('estado')
        articulo_id = request.query_params.get('articulo_id')
        qs = OrdenCompra.objects.select_related('articulo', 'usuario')
        if estado:
            qs = qs.filter(estado=estado)
        if articulo_id:
            qs = qs.filter(articulo_id=articulo_id)
        return Response({'ordenes': OrdenCompraSerializer(qs, many=True).data, 'total': qs.count()})

    def post(self, request):
        data = request.data.copy()
        data['numero_orden'] = f"OC-{uuid.uuid4().hex[:8].upper()}"
        serializer = OrdenCompraSerializer(data=data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        obj = serializer.save()
        registrar_evento('CREAR_ORDEN_COMPRA', f'Orden {obj.numero_orden} creada',
                         usuario_id=_get_usuario_id(request), entidad='orden_compra',
                         entidad_id=obj.id, ip=_get_ip(request))
        return Response(OrdenCompraSerializer(obj).data, status=201)


class OrdenCompraDetailView(APIView):
    permission_classes = [IsAdminOrCompras]

    def get(self, request, pk):
        try:
            orden = OrdenCompra.objects.select_related('articulo', 'usuario').get(id=pk)
        except OrdenCompra.DoesNotExist:
            return Response({'error': 'Orden no encontrada'}, status=404)
        return Response(OrdenCompraSerializer(orden).data)

    def put(self, request, pk):
        try:
            orden = OrdenCompra.objects.select_related('articulo').get(id=pk)
        except OrdenCompra.DoesNotExist:
            return Response({'error': 'Orden no encontrada'}, status=404)

        nuevo_estado = request.data.get('estado')
        if nuevo_estado:
            orden.estado = nuevo_estado
            orden.save(update_fields=['estado', 'fecha_modificacion'])

            # Al recibir la orden → movimiento de entrada (portado del Chem Movid compras service)
            if nuevo_estado == 'recibida':
                MovimientoService.registrar(
                    articulo_id=orden.articulo_id,
                    tipo='entrada',
                    cantidad=float(orden.cantidad_solicitada),
                    usuario=None,
                    observaciones=f'Recepción orden {orden.numero_orden}',
                )
                registrar_evento('RECIBIR_ORDEN_COMPRA', f'Orden {orden.numero_orden} recibida',
                                 usuario_id=_get_usuario_id(request), entidad='orden_compra',
                                 entidad_id=pk, ip=_get_ip(request))

        return Response(OrdenCompraSerializer(orden).data)
