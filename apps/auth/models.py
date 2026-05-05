"""
App: auth — Usuarios, roles, sesiones JWT y auditoría de acceso.
Lógica portada del microservicio auth/main.py de Chem Movid.
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
    """Registro de tokens JWT activos — permite invalidación (blacklist propia)."""
    usuario = models.ForeignKey(Usuario, on_delete=models.CASCADE, related_name='sesiones')
    token_jti = models.CharField(max_length=255, unique=True)
    fecha_expiracion = models.DateTimeField()
    fecha_creacion = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'sesion_jwt'
        verbose_name = 'Sesión JWT'
