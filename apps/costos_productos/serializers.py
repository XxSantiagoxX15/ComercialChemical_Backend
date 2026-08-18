# apps/costos_productos/serializers.py
from rest_framework import serializers
from .models import PriceSnapshot, CostosConfig


class CostosConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostosConfig
        fields = '__all__'


class PriceSnapshotSerializer(serializers.ModelSerializer):
    # Id de artículo resuelto: usa la FK si existe, si no cae al número
    # guardado en articulo_id_number (por si el artículo fue borrado o el
    # snapshot se guardó sin poder resolver la FK). Útil para el frontend
    # sin que tenga que hacer ese fallback él mismo.
    articulo_resuelto = serializers.SerializerMethodField()

    class Meta:
        model = PriceSnapshot
        fields = '__all__'

    def get_articulo_resuelto(self, obj):
        return obj.articulo_id if obj.articulo_id else obj.articulo_id_number