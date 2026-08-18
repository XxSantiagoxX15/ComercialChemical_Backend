from django.urls import path
from .views import (
    SaveSinglePriceAPIView, BulkSavePriceAPIView,
    PriceSnapshotListAPIView, ArticuloListAPIView,
    CostosConfigListCreateAPIView, CostosConfigDetailAPIView,
)

urlpatterns = [
    # Ruta real que usa el frontend actualmente: /api/costos/compute/<id>/save/
    # (se mantiene tal cual para no tener que tocar el frontend).
    path('compute/<int:producto_id>/save/', SaveSinglePriceAPIView.as_view(), name='pricing-save-single'),
    path('compute/bulk/save/', BulkSavePriceAPIView.as_view(), name='pricing-save-bulk'),

    # Alias con prefijo 'pricing/' por si en algún otro punto del frontend
    # (u otro cliente) se usa este patrón en vez del anterior. Apuntan a las
    # mismas vistas, no hay duplicación de lógica.
    path('pricing/save/<int:producto_id>/', SaveSinglePriceAPIView.as_view(), name='pricing-save-single-alt'),
    path('pricing/save/bulk/', BulkSavePriceAPIView.as_view(), name='pricing-save-bulk-alt'),

    # Consultar historial de snapshots de un artículo
    path('pricing/snapshots/<int:producto_id>/', PriceSnapshotListAPIView.as_view(), name='pricing-snapshots'),

    # CRUD opcional de configuraciones de costeo (ya no es requisito usarlo:
    # el guardado single/bulk crea/actualiza la config solo si se la envías
    # completa dentro del mismo payload).
    path('config/', CostosConfigListCreateAPIView.as_view(), name='costos-config-list-create'),
    path('config/<int:pk>/', CostosConfigDetailAPIView.as_view(), name='costos-config-detail'),

    # Datos base de artículos, para que el frontend calcule
    path('articulos/', ArticuloListAPIView.as_view(), name='articulos-list'),
]