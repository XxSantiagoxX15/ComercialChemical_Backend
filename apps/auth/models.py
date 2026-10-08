"""
App: auth — Usuarios, roles, sesiones JWT y auditoría de acceso.
"""
from django.db import models


class Rol(models.Model):
    ROLES = [
        ('Administrador', 'Administrador'),
        ('Analista_Inventarios', 'Analista de Inventarios'),
        ('Responsable_Compras', 'Responsable de Compras'),
    ]
    nombre = models.CharField(max_length=50, unique=True, choices=ROLES)
    descripcion = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        db_table = 'rol'
        verbose_name = 'Rol'

    def __str__(self):
        return self.nombre


class PermisoRol(models.Model):
    """Permisos de acceso a módulos por rol — configurable por el Administrador."""
    MODULOS = [
        ('dashboard',             'Dashboard'),
        ('inventario_articulos',  'Inventario - Artículos'),
        ('calculadora_precios', 'Calculadora de precios'),
        ('inventario_movimientos','Inventario - Movimientos'),
        ('inventario_alertas',    'Inventario - Alertas'),
        ('compras',               'Compras'),
        ('pronosticos',           'Pronósticos'),
        ('importacion',           'Importación'),
        ('auditoria',             'Auditoría'),
        ('usuarios',              'Usuarios'),
    ]
    rol    = models.ForeignKey(Rol, on_delete=models.CASCADE, related_name='permisos')
    modulo = models.CharField(max_length=50, choices=MODULOS)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = 'permiso_rol'
        unique_together = ('rol', 'modulo')
        verbose_name = 'Permiso de Rol'

    def __str__(self):
        return f"{self.rol.nombre} → {self.modulo} ({'✓' if self.activo else '✗'})"


class Usuario(models.Model):
    nombre_completo = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    password_hash = models.CharField(max_length=255)
    rol = models.ForeignKey(Rol, on_delete=models.PROTECT, db_column='id_rol')
    activo = models.BooleanField(default=True)
    intentos_fallidos = models.IntegerField(default=0)
    bloqueado_hasta = models.DateTimeField(null=True, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_modificacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'usuario'
        verbose_name = 'Usuario'

    def __str__(self):
        return f"{self.nombre_completo} ({self.email})"


class SesionJWT(models.Model):
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='sesiones')
    token_jti = models.CharField(max_length=255, unique=True)
    fecha_expiracion = models.DateTimeField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sesion_jwt'
        verbose_name = 'Sesión JWT'


class CodigoRecuperacion(models.Model):
    """Código de un solo uso que se envía al correo para restablecer la
    contraseña. Solo se guarda su huella (nunca el código), vence a los pocos
    minutos y se invalida tras varios intentos fallidos."""
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='codigos_recuperacion')
    codigo_hash = models.CharField(max_length=64)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_expiracion = models.DateTimeField()
    intentos = models.PositiveSmallIntegerField(default=0)
    usado = models.BooleanField(default=False)

    class Meta:
        db_table = 'codigo_recuperacion'
        verbose_name = 'Código de recuperación'

    def __str__(self):
        return f"Código de {self.usuario.email} (vence {self.fecha_expiracion:%Y-%m-%d %H:%M})"