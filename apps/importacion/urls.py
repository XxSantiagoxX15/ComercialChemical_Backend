from django.urls import path
from .views import ImportacionUploadView, ImportacionHistorialView, VentaHistoricoListView

urlpatterns = [
    path('upload/', ImportacionUploadView.as_view()),
    path('historial/', ImportacionHistorialView.as_view()),
    path('ventas/', VentaHistoricoListView.as_view()),
]
