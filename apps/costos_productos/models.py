# apps/costos_productos/models.py
from django.db import models

class CostosConfig(models.Model):
    name = models.CharField(max_length=120, unique=True)
    overhead_pct = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    import_cost_pct = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    tax_pct = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    margin_pct = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pricing_costosconfig"

    def __str__(self):
        return self.name


class PriceSnapshot(models.Model):
    # referencia opcional al modelo Articulo: usa 'inventario.Articulo' (app_label.ModelName)
    # Ajusta 'inventario' si tu app tiene otro app_label en INSTALLED_APPS.
    articulo = models.ForeignKey(
        'inventario.Articulo',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='price_snapshots'
    )

    # guardamos también el id numérico por si no existe FK al crear snapshot
    articulo_id_number = models.IntegerField(null=True, blank=True)

    # FK a la configuración de costos (opcional)
    config = models.ForeignKey(
        CostosConfig,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='snapshots'
    )

    base_unit_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    calculated_total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    final_price = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    final_total_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    # breakdown como JSON — requiere MySQL 5.7+ o PostgreSQL
    breakdown = models.JSONField(null=True, blank=True)

    note = models.TextField(null=True, blank=True)
    computed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pricing_pricesnapshot"
        indexes = [
            models.Index(fields=['config'], name='idx_pricing_snapshot_config'),
            models.Index(fields=['articulo'], name='idx_pricing_snapshot_articulo'),
        ]

    def __str__(self):
        return f"Snapshot {self.pk} - articulo_id_number={self.articulo_id_number}"