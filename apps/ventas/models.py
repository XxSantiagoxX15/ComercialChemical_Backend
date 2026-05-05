from django.db import models
from apps.inventario.models import Articulo


class Importacion(models.Model):
    TIPO_CHOICES = [
        ('Informe_Toma_Inventario', 'Informe Toma Inventario'),
        ('Informe_Ventas_Cliente', 'Informe Ventas Cliente'),
    ]
    RESULTADO_CHOICES = [
        ('exito', 'Éxito'),
        ('error', 'Error'),
        ('parcial', 'Parcial'),
    ]
    nombre_archivo = models.CharField(max_length=255)
    tipo_informe = models.CharField(max_length=50, choices=TIPO_CHOICES)
    formato = models.CharField(max_length=10)
    registros_procesados = models.IntegerField(default=0)
    registros_error = models.IntegerField(default=0)
    resultado = models.CharField(max_length=20, choices=RESULTADO_CHOICES)
    reporte_errores = models.JSONField(null=True, blank=True)
    fecha_importacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'importacion'
        ordering = ['-fecha_importacion']


class VentaHistorico(models.Model):
    fecha = models.DateField()
    codigo_cliente = models.CharField(max_length=100, null=True, blank=True)
    nombre_cliente = models.CharField(max_length=255, null=True, blank=True)
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='ventas')
    cantidad_vendida = models.DecimalField(max_digits=15, decimal_places=3)
    valor_total = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    importacion = models.ForeignKey(Importacion, on_delete=models.SET_NULL, null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'venta_historico'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['articulo', '-fecha']),
        ]
