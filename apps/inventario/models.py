"""
App: inventario — Artículos, movimientos, kits y alertas de stock.
Lógica portada del microservicio inventario/main.py de Chem Movid.
"""
from django.db import models
from apps.auth.models import Usuario


class Articulo(models.Model):
    codigo = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField()
    categoria = models.CharField(max_length=100, null=True, blank=True)
    unidad_medida = models.CharField(max_length=50)
    stock_actual = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    stock_minimo = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    stock_maximo = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    tiempo_reposicion_dias = models.IntegerField(default=7)
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'articulo'
        indexes = [
            models.Index(fields=['codigo']),
            models.Index(fields=['categoria']),
        ]

    def __str__(self):
        return f"{self.codigo} — {self.descripcion}"

    @property
    def en_stock_critico(self):
        return self.stock_actual <= self.stock_minimo


class MovimientoInventario(models.Model):
    TIPO_CHOICES = [
        ('entrada', 'Entrada'),
        ('salida', 'Salida'),
        ('ajuste', 'Ajuste'),
        ('produccion_kit', 'Producción de Kit'),
    ]

    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='movimientos')
    tipo_movimiento = models.CharField(max_length=50, choices=TIPO_CHOICES)
    cantidad = models.DecimalField(max_digits=15, decimal_places=3)
    stock_anterior = models.DecimalField(max_digits=15, decimal_places=3)
    stock_resultante = models.DecimalField(max_digits=15, decimal_places=3)
    usuario = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True)
    observaciones = models.TextField(null=True, blank=True)
    fecha_movimiento = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'movimiento_inventario'
        ordering = ['-fecha_movimiento']
        indexes = [
            models.Index(fields=['articulo', '-fecha_movimiento']),
            models.Index(fields=['tipo_movimiento']),
        ]


class Kit(models.Model):
    codigo = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField()
    activo = models.BooleanField(default=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'kit'

    def __str__(self):
        return f"{self.codigo} — {self.descripcion}"


class KitComponente(models.Model):
    kit = models.ForeignKey(Kit, on_delete=models.CASCADE, related_name='componentes')
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE)
    cantidad_requerida = models.DecimalField(max_digits=15, decimal_places=3)

    class Meta:
        db_table = 'kit_componente'
        unique_together = ('kit', 'articulo')
