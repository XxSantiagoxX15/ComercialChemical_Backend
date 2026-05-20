import uuid
from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from django.db.models import Sum, Avg, Min, Max, Count, Q
from drf_spectacular.utils import extend_schema
from apps.auth.permissions import IsAdminOrCompras, IsAdminOrInventario
from apps.auditoria.services import registrar_evento
from apps.inventario.services import MovimientoService
from apps.inventario.models import Articulo
from apps.ventas.models import VentaHistorico
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
            orden.estado = nuevo_estado
            orden.save(update_fields=['estado', 'fecha_modificacion'])

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


# ── Proveedores ────────────────────────────────────────────────────────────────

class ProveedoresView(APIView):
    """
    Lista todos los proveedores detectados automáticamente:
    son los clientes cuyo valor_total acumulado es negativo.
    """
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        # Agrupar por nombre_cliente y sumar valor_total
        # Proveedor = suma de valor_total < 0
        from django.db.models.functions import Coalesce
        from django.db.models import FloatField

        proveedores = (
            VentaHistorico.objects
            .filter(valor_total__lt=0)
            .values('nombre_cliente', 'codigo_cliente')
            .annotate(
                total_comprado=Sum('valor_total'),
                num_articulos=Count('articulo', distinct=True),
                num_transacciones=Count('id'),
            )
            .order_by('nombre_cliente')
        )

        return Response({
            'proveedores': [
                {
                    'nombre': p['nombre_cliente'] or 'Sin nombre',
                    'codigo': p['codigo_cliente'],
                    'total_comprado': float(p['total_comprado'] or 0),
                    'total_comprado_abs': abs(float(p['total_comprado'] or 0)),
                    'num_articulos': p['num_articulos'],
                    'num_transacciones': p['num_transacciones'],
                }
                for p in proveedores
            ],
            'total': proveedores.count(),
        })


# ── Cotizador ──────────────────────────────────────────────────────────────────

class CotizadorView(APIView):
    """
    Cotizador inteligente basado en historial de compras a proveedores.

    Modos:
      - ?articulo_id=X  → precios de ese artículo por proveedor
      - ?proveedor=NOMBRE → todos los artículos comprados a ese proveedor
      - ?articulos=1,2,3 → cotización de múltiples artículos (el más barato por artículo)
    """
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        articulo_id  = request.query_params.get('articulo_id')
        proveedor    = request.query_params.get('proveedor')
        articulos_ids = request.query_params.get('articulos')  # "1,2,3"

        # ── Modo 1: precios de un artículo por proveedor ──────────────────────
        if articulo_id:
            return self._cotizar_articulo(articulo_id)

        # ── Modo 2: catálogo de un proveedor ─────────────────────────────────
        if proveedor:
            return self._catalogo_proveedor(proveedor)

        # ── Modo 3: cotización de lista de artículos ──────────────────────────
        if articulos_ids:
            ids = [int(x.strip()) for x in articulos_ids.split(',') if x.strip().isdigit()]
            return self._cotizar_lista(ids)

        return Response({'error': 'Parámetro requerido: articulo_id, proveedor o articulos'}, status=400)

    def _precio_unitario(self, valor_total, cantidad):
        """Calcula precio unitario. valor_total es negativo → lo hace positivo."""
        if not cantidad or cantidad == 0:
            return None
        return abs(float(valor_total)) / float(cantidad)

    def _cotizar_articulo(self, articulo_id):
        """Precios históricos de un artículo por cada proveedor."""
        try:
            art = Articulo.objects.get(id=articulo_id)
        except Articulo.DoesNotExist:
            return Response({'error': 'Artículo no encontrado'}, status=404)

        # Compras = registros con valor_total negativo
        compras = (
            VentaHistorico.objects
            .filter(articulo_id=articulo_id, valor_total__lt=0)
            .values('nombre_cliente', 'codigo_cliente')
            .annotate(
                total_cantidad=Sum('cantidad_vendida'),
                total_valor=Sum('valor_total'),
                num_compras=Count('id'),
                ultima_fecha=Max('fecha'),
            )
            .order_by('total_valor')  # más barato primero (más negativo = más barato)
        )

        precios = []
        for c in compras:
            precio_unit = self._precio_unitario(c['total_valor'], c['total_cantidad'])
            precios.append({
                'proveedor': c['nombre_cliente'] or 'Sin nombre',
                'codigo_proveedor': c['codigo_cliente'],
                'cantidad_total_comprada': float(c['total_cantidad'] or 0),
                'valor_total_pagado': abs(float(c['total_valor'] or 0)),
                'precio_unitario': round(precio_unit, 2) if precio_unit else None,
                'num_compras': c['num_compras'],
                'ultima_compra': str(c['ultima_fecha']) if c['ultima_fecha'] else None,
            })

        # Ordenar por precio unitario ascendente
        precios.sort(key=lambda x: x['precio_unitario'] or float('inf'))

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

    def _catalogo_proveedor(self, nombre_proveedor):
        """Todos los artículos comprados a un proveedor con su precio unitario."""
        compras = (
            VentaHistorico.objects
            .filter(
                nombre_cliente__icontains=nombre_proveedor,
                valor_total__lt=0
            )
            .select_related('articulo')
            .values(
                'articulo__id', 'articulo__codigo',
                'articulo__descripcion', 'articulo__unidad_medida',
                'articulo__stock_actual',
            )
            .annotate(
                total_cantidad=Sum('cantidad_vendida'),
                total_valor=Sum('valor_total'),
                num_compras=Count('id'),
                ultima_fecha=Max('fecha'),
            )
            .order_by('articulo__descripcion')
        )

        catalogo = []
        for c in compras:
            precio_unit = self._precio_unitario(c['total_valor'], c['total_cantidad'])
            catalogo.append({
                'articulo_id': c['articulo__id'],
                'codigo': c['articulo__codigo'],
                'descripcion': c['articulo__descripcion'],
                'unidad_medida': c['articulo__unidad_medida'],
                'stock_actual': float(c['articulo__stock_actual'] or 0),
                'cantidad_total_comprada': float(c['total_cantidad'] or 0),
                'valor_total_pagado': abs(float(c['total_valor'] or 0)),
                'precio_unitario': round(precio_unit, 2) if precio_unit else None,
                'num_compras': c['num_compras'],
                'ultima_compra': str(c['ultima_fecha']) if c['ultima_fecha'] else None,
            })

        return Response({
            'proveedor': nombre_proveedor,
            'catalogo': catalogo,
            'total_articulos': len(catalogo),
            'valor_total_historico': sum(c['valor_total_pagado'] for c in catalogo),
        })

    def _cotizar_lista(self, articulo_ids):
        """
        Para una lista de artículos, devuelve el mejor precio (más barato)
        por artículo y el total de la cotización.
        """
        resultado = []
        total_cotizacion = 0

        for art_id in articulo_ids:
            try:
                art = Articulo.objects.get(id=art_id)
            except Articulo.DoesNotExist:
                continue

            compras = (
                VentaHistorico.objects
                .filter(articulo_id=art_id, valor_total__lt=0)
                .values('nombre_cliente', 'codigo_cliente')
                .annotate(
                    total_cantidad=Sum('cantidad_vendida'),
                    total_valor=Sum('valor_total'),
                )
            )

            precios = []
            for c in compras:
                pu = self._precio_unitario(c['total_valor'], c['total_cantidad'])
                if pu:
                    precios.append({
                        'proveedor': c['nombre_cliente'],
                        'precio_unitario': round(pu, 2),
                    })

            precios.sort(key=lambda x: x['precio_unitario'])
            mejor = precios[0] if precios else None

            item = {
                'articulo_id': art.id,
                'codigo': art.codigo,
                'descripcion': art.descripcion,
                'unidad_medida': art.unidad_medida,
                'stock_actual': float(art.stock_actual),
                'mejor_precio': mejor,
                'todos_los_precios': precios,
            }
            resultado.append(item)
            if mejor:
                total_cotizacion += mejor['precio_unitario']

        return Response({
            'items': resultado,
            'total_articulos': len(resultado),
            'total_cotizacion_referencial': round(total_cotizacion, 2),
        })
