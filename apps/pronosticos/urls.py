from django.urls import path
from .views import (
    CalcularPrediccionView, PrediccionesArticuloView,
    AnalisisEstadisticoView, ProductosMasVendidosView,
    RecomendacionesView
)

urlpatterns = [
    path('calcular/<int:articulo_id>/', CalcularPrediccionView.as_view()),
    path('predicciones/<int:articulo_id>/', PrediccionesArticuloView.as_view()),
    path('analisis/<int:articulo_id>/', AnalisisEstadisticoView.as_view()),
    path('mas-vendidos/', ProductosMasVendidosView.as_view()),
    path('recomendaciones/', RecomendacionesView.as_view()),
]
