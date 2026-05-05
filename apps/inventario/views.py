"""
Microservicio Inventario — portado a Django REST Framework.
Fuente: Chem Movid backend/inventario/main.py
"""
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError

from apps.auth.permissions import IsAdmin, IsAdminOrInventario, IsAuthenticated
from apps.auditoria.services import registrar_evento
from .models import Articulo, Kit
from .serializers import (
    ArticuloSerializer, MovimientoInventarioSerializer,
    RegistrarMovimientoSerializer, KitSerializer,
    ProducirKitSerializer, AlertaStockSerializer
)
from .services import ArticuloService, MovimientoService, KitService


def _get_ip(request):
    fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR', 'unknown')


def _get_usuario_id(request):
    token = getattr(request, 'auth', None)
    return token.get('usuario_id') if token else None


# ─── Artículos ────────────────────────────────────────────────────────────────

class ArticuloListView(APIView):
    def get_permissions(self):
        return [IsAdmin()] if self.request.method == 'POST' else [IsAdminOrInventario()]

    def get(self, request):
        buscar = request.query_params.get('buscar')
        categoria = request.query_params.get('categoria')
        page = int(request.query_params.get('page', 1))
        limit = min(int(request.query_params.get('limit', 50)), 500)
        articulos, total = ArticuloService.listar(buscar=buscar, categoria=categoria, page=page, limit=limit)
        return Response({'articulos': ArticuloSerializer(articulos, many=True).data,
                         'total': total, 'page': page, 'limit': limit})

    @extend_schema(request=ArticuloSerializer)
    def post(self, request):
        serializer = ArticuloSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = ArticuloService.crear(serializer.validated_data)
        registrar_evento('CREAR_ARTICULO', f"Artículo creado: {obj.codigo}",
                         usuario_id=_get_usuario_id(request), entidad='articulo',
                         entidad_id=obj.id, ip=_get_ip(request))
        return Response(ArticuloSerializer(obj).data, status=201)


class ArticuloDetailView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request, pk):
        return Response(ArticuloSerializer(ArticuloService.obtener(pk)).data)

    def put(self, request, pk):
        obj = ArticuloService.actualizar(pk, request.data)
        registrar_evento('ACTUALIZAR_ARTICULO', f'Artículo {pk} actualizado',
                         usuario_id=_get_usuario_id(request), entidad='articulo',
                         entidad_id=pk, ip=_get_ip(request))
        return Response(ArticuloSerializer(obj).data)


# ─── Movimientos ──────────────────────────────────────────────────────────────

class MovimientoListView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        articulo_id = request.query_params.get('articulo_id')
        tipo = request.query_params.get('tipo_movimiento')
        fecha_desde = request.query_params.get('fecha_desde')
        fecha_hasta = request.query_params.get('fecha_hasta')
        page = int(request.query_params.get('page', 1))
        limit = min(int(request.query_params.get('limit', 50)), 500)
        movimientos, total = MovimientoService.listar(
            articulo_id=articulo_id, tipo=tipo,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            page=page, limit=limit
        )
        return Response({'movimientos': MovimientoInventarioSerializer(movimientos, many=True).data,
                         'total': total, 'page': page, 'limit': limit})

    @extend_schema(request=RegistrarMovimientoSerializer)
    def post(self, request):
        serializer = RegistrarMovimientoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        mov = MovimientoService.registrar(
            articulo_id=d['articulo_id'],
            tipo=d['tipo_movimiento'],
            cantidad=float(d['cantidad']),
            usuario=None,
            observaciones=d.get('observaciones'),
            forzar=d.get('forzar', False),
        )
        registrar_evento('MOVIMIENTO_INVENTARIO',
                         f"Movimiento {d['tipo_movimiento']} de {d['cantidad']} en artículo {d['articulo_id']}",
                         usuario_id=_get_usuario_id(request), entidad='movimiento_inventario',
                         entidad_id=mov.id, ip=_get_ip(request))
        return Response(MovimientoInventarioSerializer(mov).data, status=201)


# ─── Alertas de stock crítico ─────────────────────────────────────────────────

class AlertasStockCriticoView(APIView):
    permission_classes = [IsAdminOrInventario]

    def get(self, request):
        alertas = ArticuloService.alertas_stock_critico()
        data = AlertaStockSerializer(alertas, many=True).data
        return Response({'alertas': data, 'total': len(data)})


# ─── Kits ─────────────────────────────────────────────────────────────────────

class KitListView(APIView):
    def get_permissions(self):
        return [IsAdmin()] if self.request.method == 'POST' else [IsAdminOrInventario()]

    def get(self, request):
        kits = KitService.listar()
        return Response({'kits': KitSerializer(kits, many=True).data, 'total': kits.count()})

    def post(self, request):
        codigo = request.data.get('codigo')
        descripcion = request.data.get('descripcion')
        componentes = request.data.get('componentes', [])
        kit = KitService.crear(codigo, descripcion, componentes)
        registrar_evento('CREAR_KIT', f'Kit creado: {kit.codigo}',
                         usuario_id=_get_usuario_id(request), entidad='kit',
                         entidad_id=kit.id, ip=_get_ip(request))
        return Response(KitSerializer(kit).data, status=201)


class KitProducirView(APIView):
    permission_classes = [IsAdmin]

    @extend_schema(request=ProducirKitSerializer)
    def post(self, request, pk):
        serializer = ProducirKitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resultado = KitService.producir(
            kit_id=pk,
            cantidad=serializer.validated_data['cantidad'],
            usuario=None,
            forzar=serializer.validated_data.get('forzar', False),
        )
        registrar_evento('PRODUCIR_KIT', f"Kit {pk} producido x{serializer.validated_data['cantidad']}",
                         usuario_id=_get_usuario_id(request), entidad='kit',
                         entidad_id=pk, ip=_get_ip(request))
        return Response(resultado, status=201)
