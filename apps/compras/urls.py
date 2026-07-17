from django.urls import path
from .views import (
    OrdenCompraListView, OrdenCompraDetailView, CotizadorView, ProveedoresView,
    ProveedorDetailView, ProveedorArticuloListView, ProveedorArticuloDetailView,
)

urlpatterns = [
    path('ordenes/',           OrdenCompraListView.as_view()),
    path('ordenes/<int:pk>/',  OrdenCompraDetailView.as_view()),
    path('cotizador/',         CotizadorView.as_view()),
    path('proveedores/',       ProveedoresView.as_view()),
    path('proveedores/<int:pk>/', ProveedorDetailView.as_view()),
    path('proveedores-articulos/', ProveedorArticuloListView.as_view()),
    path('proveedores-articulos/<int:pk>/', ProveedorArticuloDetailView.as_view()),
]
