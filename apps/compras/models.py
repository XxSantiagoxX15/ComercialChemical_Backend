import uuid
from django.db import models
from apps.inventario.models import Articulo
from apps.auth.models import Usuario


class OrdenCompra(models.Model):
    ESTADO_CHOICES = [
        ('pendiente', 'Pendiente'),
        ('aprobada', 'Aprobada'),
        ('rechazada', 'Rechazada'),
        ('recibida', 'Recibida'),
    ]
    numero_orden = models.CharField(max_length=100, unique=True)
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='ordenes')
    cantidad_solicitada = models.DecimalField(max_digits=15, decimal_places=3)
    precio_unitario = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    proveedor = models.CharField(max_length=255, null=True, blank=True)
    estado = models.CharField(max_length=50, choices=ESTADO_CHOICES, default='pendiente')
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True)
    observaciones = models.TextField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'orden_compra'
        ordering = ['-fecha_creacion']

    def __str__(self):
        return f"{self.numero_orden} — {self.articulo.codigo}"
