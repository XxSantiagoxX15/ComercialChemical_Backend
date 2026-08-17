from django.urls import path
from .views import (
    CalcularPrediccionView, PrediccionesArticuloView,
    AnalisisEstadisticoView, ProductosMasVendidosView, MasVendidosExportView,
    RecomendacionesView, EOQView, GenerarOrdenCompraView,
    ResumenHistoricoPronosticoView, CalcularPronosticosMasivoView,
    RiesgoAgotamientoView, ComparacionPeriodosView, ComparacionExportView,
    MesesPronosticadosView, ReporteNecesidadesView, ReporteNecesidadesPdfView,
)

urlpatterns = [
    path('calcular/<int:articulo_id>/', CalcularPrediccionView.as_view()),
    path('calcular-todos/', CalcularPronosticosMasivoView.as_view()),
    path('predicciones/<int:articulo_id>/', PrediccionesArticuloView.as_view()),
    path('analisis/<int:articulo_id>/', AnalisisEstadisticoView.as_view()),
    path('mas-vendidos/', ProductosMasVendidosView.as_view()),
    path('mas-vendidos/exportar/', MasVendidosExportView.as_view()),
    path('recomendaciones/', RecomendacionesView.as_view()),
    path('eoq/<int:articulo_id>/', EOQView.as_view()),
    path('recomendaciones/<int:articulo_id>/generar-orden/', GenerarOrdenCompraView.as_view()),
    path('resumen-historico/', ResumenHistoricoPronosticoView.as_view()),
    path('riesgo-agotamiento/', RiesgoAgotamientoView.as_view()),
    path('comparar-periodos/', ComparacionPeriodosView.as_view()),
    path('comparar-periodos/exportar/', ComparacionExportView.as_view()),
    path('meses-disponibles/', MesesPronosticadosView.as_view()),
    path('reporte-necesidades/', ReporteNecesidadesView.as_view()),
    path('reporte-necesidades/pdf/', ReporteNecesidadesPdfView.as_view()),
]
