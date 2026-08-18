from django.urls import re_path
from .consumers import AlertasStockConsumer

websocket_urlpatterns = [
    re_path(r'ws/inventario/alertas/$', AlertasStockConsumer.as_asgi()),
]
