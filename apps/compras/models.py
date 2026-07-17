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


class Proveedor(models.Model):
    """Catálogo de proveedores, mantenido manualmente (reemplaza la detección
    automática por ventas con valor negativo, que dejó de ser viable cuando
    la fuente de datos cambió al Kardex — ese reporte no trae nombre de
    cliente/proveedor)."""
    nombre = models.CharField(max_length=255, unique=True)
    nit = models.CharField(max_length=50, null=True, blank=True)
    contacto = models.CharField(max_length=255, null=True, blank=True)
    telefono = models.CharField(max_length=50, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'proveedor'
        ordering = ['nombre']

    def __str__(self):
        return self.nombre


class ProveedorArticulo(models.Model):
    """Un artículo que un proveedor específico suministra, con su precio
    vigente. Es el catálogo que alimenta al Cotizador."""
    proveedor = models.ForeignKey(Proveedor, on_delete=models.CASCADE, related_name='articulos_suministrados')
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='proveedores')
    precio_unitario = models.DecimalField(max_digits=15, decimal_places=2)
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'proveedor_articulo'
        unique_together = ('proveedor', 'articulo')
        ordering = ['precio_unitario']

    def __str__(self):
        return f"{self.proveedor.nombre} — {self.articulo.codigo} (${self.precio_unitario})"
