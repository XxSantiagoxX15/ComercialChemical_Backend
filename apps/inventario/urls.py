from django.urls import path
from .views import (
    ArticuloListView, ArticuloDetailView,
    MovimientoListView, AlertasStockCriticoView,
    KitListView, KitProducirView, KitsProduciblesView,
    LotesArticuloView, AlertasVencimientoView,
    ResumenStockMensualView,
)

urlpatterns = [
    path('health/', lambda r: __import__('rest_framework.response', fromlist=['Response']).Response({'status': 'ok', 'service': 'inventario'})),
    path('articulos/', ArticuloListView.as_view()),
    path('articulos/<int:pk>/', ArticuloDetailView.as_view()),
    path('articulos/<int:articulo_id>/lotes/', LotesArticuloView.as_view()),
    path('movimientos/', MovimientoListView.as_view()),
    path('alertas-stock-critico/', AlertasStockCriticoView.as_view()),
    path('alertas-vencimiento/', AlertasVencimientoView.as_view()),
    path('kits/', KitListView.as_view()),
    path('kits/producibles/', KitsProduciblesView.as_view()),
    path('kits/<int:pk>/producir/', KitProducirView.as_view()),
    path('resumen-stock-mensual/', ResumenStockMensualView.as_view()),
]
