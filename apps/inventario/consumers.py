"""
WebSocket consumer para alertas de stock crítico en tiempo real.
Equivalente al WebSocket /inventario/ws/alertas del microservicio Chem Movid.
"""
import asyncio
import json
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import F, ExpressionWrapper, FloatField
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken
from .models import Articulo


class AlertasStockConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        if not await self._autenticar():
            await self.close(code=4401)
            return
        await self.channel_layer.group_add('alertas_stock', self.channel_name)
        await self.accept()
        # Enviar alertas iniciales al conectar
        alertas = await self._get_alertas()
        await self.send(text_data=json.dumps({'tipo': 'alertas_stock', 'data': alertas}))
        # Iniciar loop de 30 segundos
        self._loop_task = asyncio.create_task(self._periodic_push())

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard('alertas_stock', self.channel_name)
        if hasattr(self, '_loop_task'):
            self._loop_task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        if text_data == 'ping':
            alertas = await self._get_alertas()
            await self.send(text_data=json.dumps({'tipo': 'alertas_stock', 'data': alertas}))

    async def alertas_stock_message(self, event):
        await self.send(text_data=json.dumps(event['data']))

    async def _periodic_push(self):
        while True:
            await asyncio.sleep(30)
            try:
                alertas = await self._get_alertas()
                await self.send(text_data=json.dumps({'tipo': 'alertas_stock', 'data': alertas}))
            except Exception:
                break

    @database_sync_to_async
    def _autenticar(self):
        """El navegador no puede enviar headers en el handshake WS,
        así que el JWT se recibe como query param: ws/...?token=<jwt>."""
        query_string = self.scope.get('query_string', b'').decode()
        token = parse_qs(query_string).get('token', [None])[0]
        if not token:
            return False
        try:
            AccessToken(token)
            return True
        except (TokenError, InvalidToken):
            return False

    @database_sync_to_async
    def _get_alertas(self):
        qs = (
            Articulo.objects.filter(activo=True, stock_minimo__gt=0)
            .filter(stock_actual__lte=F('stock_minimo'))
            .annotate(
                porcentaje_stock=ExpressionWrapper(
                    F('stock_actual') * 100.0 / (F('stock_minimo') + 0.001),
                    output_field=FloatField()
                )
            )
            .order_by('porcentaje_stock')
        )
        return [
            {
                'articulo_id': a.id,
                'codigo': a.codigo,
                'descripcion': a.descripcion,
                'categoria': a.categoria,
                'unidad_medida': a.unidad_medida,
                'stock_actual': float(a.stock_actual),
                'stock_minimo': float(a.stock_minimo),
                'porcentaje_stock': round(float(a.porcentaje_stock), 2),
                'nivel_urgencia': 'critico' if float(a.porcentaje_stock) <= 50 else 'bajo',
            }
            for a in qs
        ]
