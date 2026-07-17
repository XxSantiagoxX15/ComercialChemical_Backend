"""CHEM-MODINV — URL principal."""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Microservicio: Auth & Usuarios
    path('api/auth/', include('apps.auth.urls')),

    # Microservicio: Inventario (artículos, movimientos, kits, alertas)
    path('api/inventario/', include('apps.inventario.urls')),

    # Microservicio: Compras (órdenes, recomendaciones)
    path('api/compras/', include('apps.compras.urls')),

    # Microservicio: Ventas / Importación
    path('api/importacion/', include('apps.importacion.urls')),

    # Microservicio: Pronósticos / Forecasting
    path('api/pronosticos/', include('apps.pronosticos.urls')),

    # Microservicio: Auditoría
    path('api/auditoria/', include('apps.auditoria.urls')),

    # Documentación OpenAPI
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/swagger/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
