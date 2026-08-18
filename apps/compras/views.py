import uuid
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.db.models import Sum, Avg, Min, Max, Count, Q
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from apps.auth.permissions import IsAdminOrCompras
from apps.auditoria.services import registrar_evento
from apps.inventario.services import MovimientoService
from apps.inventario.models import Articulo
from .models import OrdenCompra, Proveedor, ProveedorArticulo
from .pdf import generar_pdf_orden


class OrdenCompraSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)

    class Meta:
        model = OrdenCompra
        fields = '__all__'


class ProveedorSerializer(serializers.ModelSerializer):
    num_articulos = serializers.IntegerField(read_only=True, required=False)

    class Meta:
        model = Proveedor
        fields = ['id', 'nombre', 'nit', 'contacto', 'telefono', 'email', 'activo', 'fecha_creacion', 'num_articulos']
        read_only_fields = ['id', 'fecha_creacion']


class ProveedorArticuloSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)
    articulo_unidad_medida = serializers.CharField(source='articulo.unidad_medida', read_only=True)
    articulo_stock_actual = serializers.DecimalField(source='articulo.stock_actual', max_digits=15, decimal_places=3, read_only=True)
    proveedor_nombre = serializers.CharField(source='proveedor.nombre', read_only=True)

    class Meta:
        model = ProveedorArticulo
        fields = [
            'id', 'proveedor', 'proveedor_nombre', 'articulo',
            'articulo_codigo', 'articulo_descripcion', 'articulo_unidad_medida', 'articulo_stock_actual',
            'precio_unitario', 'fecha_actualizacion',
        ]
        read_only_fields = ['id', 'fecha_actualizacion']


def _get_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', 'unknown')

def _get_usuario_id(request):
    token = getattr(request, 'auth', None)
    return token.get('usuario_id') if token else None


# ── Órdenes de compra ──────────────────────────────────────────────────────────

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
            estados_validos = dict(OrdenCompra.ESTADO_CHOICES)
            if nuevo_estado not in estados_validos:
                return Response(
                    {'error': f"Estado inválido. Debe ser uno de: {', '.join(estados_validos)}."},
                    status=400,
                )

            estado_anterior = orden.estado
            orden.estado = nuevo_estado
            orden.save(update_fields=['estado', 'fecha_modificacion'])

            if nuevo_estado == 'recibida' and estado_anterior != 'recibida':
                MovimientoService.registrar(
                    articulo_id=orden.articulo_id,
                    tipo='entrada',
                    cantidad=float(orden.cantidad_solicitada),
                    usuario_id=_get_usuario_id(request),
                    observaciones=f'Recepción orden {orden.numero_orden}',
                )
                registrar_evento('RECIBIR_ORDEN_COMPRA', f'Orden {orden.numero_orden} recibida',
                                 usuario_id=_get_usuario_id(request), entidad='orden_compra',
                                 entidad_id=pk, ip=_get_ip(request))

        return Response(OrdenCompraSerializer(orden).data)


class OrdenCompraPdfView(APIView):
    permission_classes = [IsAdminOrCompras]

    def get(self, request, pk):
        try:
            orden = OrdenCompra.objects.select_related('articulo', 'usuario').get(id=pk)
        except OrdenCompra.DoesNotExist:
            return Response({'error': 'Orden no encontrada'}, status=404)

        pdf_bytes = generar_pdf_orden(orden)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="{orden.numero_orden}.pdf"'
        return response


# ── Proveedores ────────────────────────────────────────────────────────────────

class ProveedoresView(APIView):
    """Catálogo de proveedores — CRUD manual (reemplaza la detección automática
    por ventas negativas, inviable desde que la fuente pasó a ser el Kardex)."""
    permission_classes = [IsAdminOrCompras]

    def get(self, request):
        proveedores = Proveedor.objects.annotate(num_articulos=Count('articulos_suministrados')).order_by('nombre')
        return Response({
            'proveedores': ProveedorSerializer(proveedores, many=True).data,
            'total': proveedores.count(),
        })

    def post(self, request):
        serializer = ProveedorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        proveedor = serializer.save()
        registrar_evento('CREAR_PROVEEDOR', f'Proveedor creado: {proveedor.nombre}',
                         usuario_id=_get_usuario_id(request), entidad='proveedor',
                         entidad_id=proveedor.id, ip=_get_ip(request))
        return Response(ProveedorSerializer(proveedor).data, status=201)


class ProveedorDetailView(APIView):
    permission_classes = [IsAdminOrCompras]

    def get(self, request, pk):
        try:
            proveedor = Proveedor.objects.annotate(num_articulos=Count('articulos_suministrados')).get(id=pk)
        except Proveedor.DoesNotExist:
            return Response({'error': 'Proveedor no encontrado'}, status=404)
        return Response(ProveedorSerializer(proveedor).data)

    def put(self, request, pk):
        try:
            proveedor = Proveedor.objects.get(id=pk)
        except Proveedor.DoesNotExist:
            return Response({'error': 'Proveedor no encontrado'}, status=404)
        serializer = ProveedorSerializer(proveedor, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        registrar_evento('ACTUALIZAR_PROVEEDOR', f'Proveedor {pk} actualizado',
                         usuario_id=_get_usuario_id(request), entidad='proveedor',
                         entidad_id=pk, ip=_get_ip(request))
        return Response(ProveedorSerializer(proveedor).data)

    def delete(self, request, pk):
        try:
            proveedor = Proveedor.objects.get(id=pk)
        except Proveedor.DoesNotExist:
            return Response({'error': 'Proveedor no encontrado'}, status=404)
        nombre = proveedor.nombre
        proveedor.delete()
        registrar_evento('ELIMINAR_PROVEEDOR', f'Proveedor eliminado: {nombre}',
                         usuario_id=_get_usuario_id(request), entidad='proveedor',
                         entidad_id=pk, ip=_get_ip(request))
        return Response({'message': 'Proveedor eliminado correctamente'})


# ── Catálogo proveedor-artículo ──────────────────────────────────────────────────

class ProveedorArticuloListView(APIView):
    """GET ?proveedor_id=X o ?articulo_id=X — lista el catálogo filtrado.
       POST — agrega un artículo al catálogo de un proveedor con su precio."""
    permission_classes = [IsAdminOrCompras]

    def get(self, request):
        qs = ProveedorArticulo.objects.select_related('proveedor', 'articulo')
        proveedor_id = request.query_params.get('proveedor_id')
        articulo_id = request.query_params.get('articulo_id')
        if proveedor_id:
            qs = qs.filter(proveedor_id=proveedor_id)
        if articulo_id:
            qs = qs.filter(articulo_id=articulo_id)
        qs = qs.order_by('precio_unitario')
        return Response({'catalogo': ProveedorArticuloSerializer(qs, many=True).data, 'total': qs.count()})

    def post(self, request):
        proveedor_id = request.data.get('proveedor')
        articulo_id = request.data.get('articulo')
        if ProveedorArticulo.objects.filter(proveedor_id=proveedor_id, articulo_id=articulo_id).exists():
            return Response({'error': 'Este artículo ya está en el catálogo de este proveedor. Actualiza el precio en vez de duplicarlo.'}, status=400)
        serializer = ProveedorArticuloSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save()
        registrar_evento('AGREGAR_ARTICULO_PROVEEDOR',
                         f'Artículo {item.articulo.codigo} agregado al catálogo de {item.proveedor.nombre}',
                         usuario_id=_get_usuario_id(request), entidad='proveedor_articulo',
                         entidad_id=item.id, ip=_get_ip(request))
        return Response(ProveedorArticuloSerializer(item).data, status=201)


class ProveedorArticuloDetailView(APIView):
    permission_classes = [IsAdminOrCompras]

    def put(self, request, pk):
        try:
            item = ProveedorArticulo.objects.get(id=pk)
        except ProveedorArticulo.DoesNotExist:
            return Response({'error': 'No encontrado'}, status=404)
        serializer = ProveedorArticuloSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProveedorArticuloSerializer(item).data)

    def delete(self, request, pk):
        try:
            item = ProveedorArticulo.objects.get(id=pk)
        except ProveedorArticulo.DoesNotExist:
            return Response({'error': 'No encontrado'}, status=404)
        item.delete()
        return Response({'message': 'Artículo quitado del catálogo del proveedor'})


# ── Cotizador ──────────────────────────────────────────────────────────────────

class CotizadorView(APIView):
    """
    Cotizador basado en el catálogo de precios por proveedor (ProveedorArticulo).

    Modos:
      - ?articulo_id=X    → precios de ese artículo por proveedor
      - ?proveedor_id=X   → todos los artículos que ofrece ese proveedor
      - ?articulos=1,2,3  → cotización de múltiples artículos (el más barato por artículo)
    """
    permission_classes = [IsAdminOrCompras]

    def get(self, request):
        articulo_id   = request.query_params.get('articulo_id')
        proveedor_id  = request.query_params.get('proveedor_id')
        articulos_ids = request.query_params.get('articulos')  # "1,2,3"

        if articulo_id:
            return self._cotizar_articulo(articulo_id)
        if proveedor_id:
            return self._catalogo_proveedor(proveedor_id)
        if articulos_ids:
            ids = [int(x.strip()) for x in articulos_ids.split(',') if x.strip().isdigit()]
            return self._cotizar_lista(ids)

        return Response({'error': 'Parámetro requerido: articulo_id, proveedor_id o articulos'}, status=400)

    def _cotizar_articulo(self, articulo_id):
        """Precios vigentes de un artículo por cada proveedor que lo ofrece."""
        try:
            art = Articulo.objects.get(id=articulo_id)
        except Articulo.DoesNotExist:
            return Response({'error': 'Artículo no encontrado'}, status=404)

        ofertas = (
            ProveedorArticulo.objects
            .filter(articulo_id=articulo_id)
            .select_related('proveedor')
            .order_by('precio_unitario')
        )

        precios = [{
            'proveedor_articulo_id': o.id,
            'proveedor_id': o.proveedor_id,
            'proveedor': o.proveedor.nombre,
            'precio_unitario': float(o.precio_unitario),
            'fecha_actualizacion': o.fecha_actualizacion.date().isoformat(),
        } for o in ofertas]

        return Response({
            'articulo': {
                'id': art.id,
                'codigo': art.codigo,
                'descripcion': art.descripcion,
                'unidad_medida': art.unidad_medida,
                'stock_actual': float(art.stock_actual),
            },
            'precios_por_proveedor': precios,
            'mejor_precio': precios[0] if precios else None,
            'total_proveedores': len(precios),
        })

    def _catalogo_proveedor(self, proveedor_id):
        """Todos los artículos que ofrece un proveedor, con su precio vigente."""
        try:
            proveedor = Proveedor.objects.get(id=proveedor_id)
        except Proveedor.DoesNotExist:
            return Response({'error': 'Proveedor no encontrado'}, status=404)

        ofertas = (
            ProveedorArticulo.objects
            .filter(proveedor_id=proveedor_id)
            .select_related('articulo')
            .order_by('articulo__descripcion')
        )

        catalogo = [{
            'proveedor_articulo_id': o.id,
            'articulo_id': o.articulo_id,
            'codigo': o.articulo.codigo,
            'descripcion': o.articulo.descripcion,
            'unidad_medida': o.articulo.unidad_medida,
            'stock_actual': float(o.articulo.stock_actual),
            'precio_unitario': float(o.precio_unitario),
            'fecha_actualizacion': o.fecha_actualizacion.date().isoformat(),
        } for o in ofertas]

        return Response({
            'proveedor': proveedor.nombre,
            'proveedor_id': proveedor.id,
            'catalogo': catalogo,
            'total_articulos': len(catalogo),
        })

    def _cotizar_lista(self, articulo_ids):
        """Para una lista de artículos, el mejor precio (más barato) por artículo."""
        resultado = []
        total_cotizacion = 0

        ofertas_por_articulo = {}
        for o in (ProveedorArticulo.objects
                  .filter(articulo_id__in=articulo_ids)
                  .select_related('proveedor')
                  .order_by('precio_unitario')):
            ofertas_por_articulo.setdefault(o.articulo_id, []).append(o)

        for art_id in articulo_ids:
            try:
                art = Articulo.objects.get(id=art_id)
            except Articulo.DoesNotExist:
                continue

            ofertas = ofertas_por_articulo.get(art_id, [])
            precios = [{'proveedor': o.proveedor.nombre, 'precio_unitario': float(o.precio_unitario)} for o in ofertas]
            mejor = precios[0] if precios else None

            resultado.append({
                'articulo_id': art.id,
                'codigo': art.codigo,
                'descripcion': art.descripcion,
                'unidad_medida': art.unidad_medida,
                'stock_actual': float(art.stock_actual),
                'mejor_precio': mejor,
                'todos_los_precios': precios,
            })
            if mejor:
                total_cotizacion += mejor['precio_unitario']

        return Response({
            'items': resultado,
            'total_articulos': len(resultado),
            'total_cotizacion_referencial': round(total_cotizacion, 2),
        })
