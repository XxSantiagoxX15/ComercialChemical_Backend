"""
App: inventario — Artículos, movimientos, kits y alertas de stock.
Lógica portada del microservicio inventario/main.py de Chem Movid.
"""
from django.db import models
from django.utils import timezone
from apps.auth.models import Usuario


class Articulo(models.Model):
    CLASIFICACION_PELIGRO_CHOICES = [
        ('no_peligroso', 'No peligroso'),
        ('inflamable', 'Inflamable'),
        ('corrosivo', 'Corrosivo'),
        ('toxico', 'Tóxico'),
        ('oxidante', 'Oxidante'),
        ('irritante', 'Irritante'),
        ('explosivo', 'Explosivo'),
    ]

    TIPO_PRODUCTO_CHOICES = [
        ('producto', 'Producto'),
        ('kit', 'Kit'),
    ]

    codigo = models.CharField(max_length=100, unique=True)
    descripcion = models.TextField()
    categoria = models.CharField(max_length=100, null=True, blank=True)
    unidad_medida = models.CharField(max_length=50)
    tipo_producto = models.CharField(
        max_length=20, choices=TIPO_PRODUCTO_CHOICES, default='producto',
        help_text='Producto normal o kit (se arma a partir de otros artículos).'
    )
    stock_actual = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    stock_minimo = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    stock_maximo = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    tiempo_reposicion_dias = models.IntegerField(default=7)
    activo = models.BooleanField(default=True)

    # Permite silenciar la alerta de stock crítico de un artículo puntual (botón
    # "No me interesa"). Se excluye tanto de alertas como de recomendaciones de
    # compra hasta que se reactive manualmente.
    alerta_ignorada = models.BooleanField(default=False)
    fecha_alerta_ignorada = models.DateTimeField(null=True, blank=True)

    # Ficha de seguridad y clasificación de peligro (requisito de distribución química)
    ficha_seguridad = models.FileField(upload_to='fichas_seguridad/', null=True, blank=True)
    clasificacion_peligro = models.CharField(
        max_length=30, choices=CLASIFICACION_PELIGRO_CHOICES, default='no_peligroso', blank=True
    )

    # Costos para cálculo de EOQ (cantidad económica de pedido)
    costo_pedido = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Costo fijo de realizar un pedido (S)'
    )
    costo_mantenimiento_anual = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text='Costo de mantener 1 unidad en inventario durante un año (H)'
    )

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
    # default=timezone.now (no auto_now_add) para poder fijar una fecha histórica
    # real al reconstruir movimientos desde el Kardex; en la creación manual normal
    # se comporta igual que auto_now_add (se estampa "ahora" si no se pasa nada).
    fecha_movimiento = models.DateTimeField(default=timezone.now)

    # Si no es null, este movimiento fue reconstruido automáticamente al
    # importar el Kardex (historial), no registrado a mano desde la app.
    # Permite limpiarlos de forma segura en una reimportación sin tocar
    # jamás los movimientos manuales (que siempre tienen importacion=None).
    importacion = models.ForeignKey(
        'ventas.Importacion', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='movimientos_generados',
    )

    class Meta:
        db_table = 'movimiento_inventario'
        ordering = ['-fecha_movimiento']
        indexes = [
            models.Index(fields=['articulo', '-fecha_movimiento']),
            models.Index(fields=['tipo_movimiento']),
        ]


class LoteInventario(models.Model):
    """Lote recibido en una entrada de inventario, con su propio vencimiento.
    Las salidas se descuentan de los lotes por orden de vencimiento (FIFO)."""
    articulo = models.ForeignKey(Articulo, on_delete=models.CASCADE, related_name='lotes')
    numero_lote = models.CharField(max_length=100)
    cantidad_inicial = models.DecimalField(max_digits=15, decimal_places=3)
    cantidad_disponible = models.DecimalField(max_digits=15, decimal_places=3)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    fecha_ingreso = models.DateTimeField(auto_now_add=True)
    movimiento_entrada = models.ForeignKey(
        MovimientoInventario, on_delete=models.SET_NULL, null=True, blank=True, related_name='lote_generado'
    )
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'lote_inventario'
        ordering = ['fecha_vencimiento', 'fecha_ingreso']
        indexes = [
            models.Index(fields=['articulo', 'fecha_vencimiento']),
        ]

    def __str__(self):
        return f"{self.articulo.codigo} — Lote {self.numero_lote}"


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
