from django.db import models


class EventoAuditoria(models.Model):
    tipo_accion = models.CharField(max_length=100)
    descripcion = models.TextField()
    usuario_id = models.IntegerField(null=True, blank=True)
    entidad_afectada = models.CharField(max_length=100, null=True, blank=True)
    entidad_id = models.IntegerField(null=True, blank=True)
    datos_anteriores = models.JSONField(null=True, blank=True)
    datos_nuevos = models.JSONField(null=True, blank=True)
    ip_cliente = models.GenericIPAddressField(null=True, blank=True)
    fecha_evento = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'evento_auditoria'
        ordering = ['-fecha_evento']
        indexes = [
            models.Index(fields=['tipo_accion']),
            models.Index(fields=['usuario_id']),
            models.Index(fields=['-fecha_evento']),
        ]

    def __str__(self):
        return f"{self.tipo_accion} — {self.fecha_evento}"
