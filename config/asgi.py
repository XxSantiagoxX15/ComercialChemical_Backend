"""CHEM-MODINV — ASGI config con WebSockets (Django Channels)."""
import os
import pymysql
pymysql.install_as_MySQLdb()
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import apps.inventario.routing as inventario_routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(
        URLRouter(
            inventario_routing.websocket_urlpatterns
        )
    ),
})
