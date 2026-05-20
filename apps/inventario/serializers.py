from rest_framework import serializers
from django.db.models import Sum
from .models import Articulo, MovimientoInventario, Kit, KitComponente


class ArticuloSerializer(serializers.ModelSerializer):
    en_stock_critico = serializers.BooleanField(read_only=True)
    total_vendido    = serializers.SerializerMethodField()
    valor_vendido    = serializers.SerializerMethodField()

    class Meta:
        model = Articulo
        fields = [
            'id', 'codigo', 'descripcion', 'categoria', 'unidad_medida',
            'stock_actual', 'stock_minimo', 'stock_maximo',
            'tiempo_reposicion_dias', 'activo',
            'en_stock_critico', 'total_vendido', 'valor_vendido',
            'fecha_creacion', 'fecha_modificacion'
        ]
        read_only_fields = ['id', 'stock_actual', 'fecha_creacion', 'fecha_modificacion']

    def get_total_vendido(self, obj):
        result = obj.ventas.aggregate(total=Sum('cantidad_vendida'))
        total = result.get('total')
        return float(total) if total is not None else 0.0

    def get_valor_vendido(self, obj):
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

    def get_nivel_urgencia(self, obj):
        return 'critico' if float(obj.porcentaje_stock) <= 50 else 'bajo'
