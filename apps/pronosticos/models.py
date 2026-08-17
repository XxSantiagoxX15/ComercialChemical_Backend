from django.db import models
from apps.inventario.models import Articulo


class Prediccion(models.Model):
    MODELO_CHOICES = [
        ('suavizacion_exponencial', 'Suavización Exponencial'),
        ('arima', 'ARIMA'),
        ('regresion_minimos_cuadrados', 'Regresión Mínimos Cuadrados'),
        ('auto', 'Automático (el más preciso por backtesting)'),
    ]
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='predicciones')
    modelo = models.CharField(max_length=50, choices=MODELO_CHOICES)
    fecha_prediccion = models.DateField()
    demanda_proyectada = models.DecimalField(max_digits=15, decimal_places=3)
    intervalo_confianza_inferior = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    intervalo_confianza_superior = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    parametros_modelo = models.JSONField(null=True, blank=True)
    fecha_calculo = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'prediccion'
        unique_together = ('articulo', 'modelo', 'fecha_prediccion')
        ordering = ['fecha_prediccion']
