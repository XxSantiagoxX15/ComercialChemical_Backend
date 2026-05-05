from django.urls import path
from .views import (
    ArticuloListView, ArticuloDetailView,
    MovimientoListView, AlertasStockCriticoView,
    KitListView, KitProducirView
)

urlpatterns = [
    path('health/', lambda r: __import__('rest_framework.response', fromlist=['Response']).Response({'status': 'ok', 'service': 'inventario'})),
    path('articulos/', ArticuloListView.as_view()),
    path('articulos/<int:pk>/', ArticuloDetailView.as_view()),
    path('movimientos/', MovimientoListView.as_view()),
    path('alertas-stock-critico/', AlertasStockCriticoView.as_view()),
    path('kits/', KitListView.as_view()),
    path('kits/<int:pk>/producir/', KitProducirView.as_view()),
]
