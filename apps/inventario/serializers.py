from rest_framework import serializers
from django.db.models import Sum
from .models import Articulo, MovimientoInventario, Kit, KitComponente, LoteInventario


class ArticuloSerializer(serializers.ModelSerializer):
    en_stock_critico = serializers.BooleanField(read_only=True)
    total_vendido    = serializers.SerializerMethodField()
    valor_vendido    = serializers.SerializerMethodField()

    class Meta:
        model = Articulo
        fields = [
            'id', 'codigo', 'descripcion', 'categoria', 'unidad_medida', 'tipo_producto',
            'stock_actual', 'stock_minimo', 'stock_maximo',
            'tiempo_reposicion_dias', 'activo',
            'ficha_seguridad', 'clasificacion_peligro',
            'costo_pedido', 'costo_mantenimiento_anual',
            'alerta_ignorada', 'fecha_alerta_ignorada',
            'en_stock_critico', 'total_vendido', 'valor_vendido',
            'fecha_creacion', 'fecha_modificacion'
        ]
        read_only_fields = ['id', 'stock_actual', 'fecha_alerta_ignorada', 'fecha_creacion', 'fecha_modificacion']

    def get_total_vendido(self, obj):
        # Si el queryset viene anotado (listar/obtener), evita una query extra por artículo.
        if hasattr(obj, 'total_vendido_calc'):
            return float(obj.total_vendido_calc)
        result = obj.ventas.aggregate(total=Sum('cantidad_vendida'))
        total = result.get('total')
        return float(total) if total is not None else 0.0

    def get_valor_vendido(self, obj):
        if hasattr(obj, 'valor_vendido_calc'):
            return float(obj.valor_vendido_calc)
        result = obj.ventas.aggregate(total=Sum('valor_total'))
        total = result.get('total')
        return float(total) if total is not None else 0.0


class MovimientoInventarioSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)
    usuario_nombre = serializers.SerializerMethodField()

    class Meta:
        model = MovimientoInventario
        fields = [
            'id', 'articulo', 'articulo_codigo', 'articulo_descripcion',
            'tipo_movimiento', 'cantidad', 'stock_anterior', 'stock_resultante',
            'usuario', 'usuario_nombre', 'observaciones', 'fecha_movimiento'
        ]

    def get_usuario_nombre(self, obj):
        return obj.usuario.nombre_completo if obj.usuario else None


class RegistrarMovimientoSerializer(serializers.Serializer):
    articulo_id = serializers.IntegerField()
    tipo_movimiento = serializers.ChoiceField(choices=['entrada', 'salida', 'ajuste', 'produccion_kit'])
    cantidad = serializers.DecimalField(max_digits=15, decimal_places=3, min_value=0.001)
    observaciones = serializers.CharField(required=False, allow_blank=True)
    forzar = serializers.BooleanField(default=False)
    numero_lote = serializers.CharField(required=False, allow_blank=True)
    fecha_vencimiento = serializers.DateField(required=False, allow_null=True)


class LoteInventarioSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)
    dias_para_vencer = serializers.SerializerMethodField()

    class Meta:
        model = LoteInventario
        fields = [
            'id', 'articulo', 'articulo_codigo', 'articulo_descripcion',
            'numero_lote', 'cantidad_inicial', 'cantidad_disponible',
            'fecha_vencimiento', 'fecha_ingreso', 'activo', 'dias_para_vencer',
        ]

    def get_dias_para_vencer(self, obj):
        if not obj.fecha_vencimiento:
            return None
        from django.utils import timezone
        return (obj.fecha_vencimiento - timezone.now().date()).days


class KitComponenteSerializer(serializers.ModelSerializer):
    articulo_codigo = serializers.CharField(source='articulo.codigo', read_only=True)
    articulo_descripcion = serializers.CharField(source='articulo.descripcion', read_only=True)
    stock_disponible = serializers.DecimalField(
        source='articulo.stock_actual', max_digits=15, decimal_places=3, read_only=True
    )

    class Meta:
        model = KitComponente
        fields = ['id', 'articulo', 'articulo_codigo', 'articulo_descripcion',
                  'cantidad_requerida', 'stock_disponible']


class KitSerializer(serializers.ModelSerializer):
    componentes = KitComponenteSerializer(many=True, read_only=True)

    class Meta:
        model = Kit
        fields = ['id', 'codigo', 'descripcion', 'activo', 'fecha_creacion', 'componentes']


class ProducirKitSerializer(serializers.Serializer):
    cantidad = serializers.IntegerField(min_value=1)
    forzar = serializers.BooleanField(default=False)


class AlertaStockSerializer(serializers.Serializer):
    articulo_id = serializers.IntegerField(source='id')
    codigo = serializers.CharField()
    descripcion = serializers.CharField()
    categoria = serializers.CharField()
    unidad_medida = serializers.CharField()
    stock_actual = serializers.DecimalField(max_digits=15, decimal_places=3)
    stock_minimo = serializers.DecimalField(max_digits=15, decimal_places=3)
    porcentaje_stock = serializers.FloatField()
    nivel_urgencia = serializers.SerializerMethodField()
    alerta_ignorada = serializers.BooleanField()
    fecha_alerta_ignorada = serializers.DateTimeField(allow_null=True)

    def get_nivel_urgencia(self, obj):
        return 'critico' if float(obj.porcentaje_stock) <= 50 else 'bajo'
