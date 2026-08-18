# apps/pricing/admin.py
from django.contrib import admin
from .models import CostosConfig, PriceSnapshot

@admin.register(CostosConfig)
class CostosConfigAdmin(admin.ModelAdmin):
    list_display = ('name','overhead_pct','tax_pct','margin_pct')

@admin.register(PriceSnapshot)
class PriceSnapshotAdmin(admin.ModelAdmin):
    list_display = ('articulo_id','final_price','final_total_value','computed_at')
    readonly_fields = ('computed_at',)