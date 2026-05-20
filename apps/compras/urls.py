from django.urls import path
from .views import OrdenCompraListView, OrdenCompraDetailView, CotizadorView, ProveedoresView

urlpatterns = [
    path('ordenes/',           OrdenCompraListView.as_view()),
    path('ordenes/<int:pk>/',  OrdenCompraDetailView.as_view()),
    path('cotizador/',         CotizadorView.as_view()),
    path('proveedores/',       ProveedoresView.as_view()),
]
