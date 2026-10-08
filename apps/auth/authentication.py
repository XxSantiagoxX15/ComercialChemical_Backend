"""
Backend de autenticación JWT personalizado.

Valida el token y carga el Usuario propio del sistema (no el auth.User de
Django). El rol y el estado activo se leen de la base de datos en cada
petición en vez de confiar en los claims del token: así, desactivar a un
usuario o cambiarle el rol tiene efecto inmediato y no cuando expire su token.
"""
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Usuario
from .modulos import ROL_ADMIN, modulos_de_rol


class ChemJWTAuthentication(JWTAuthentication):

    def get_user(self, validated_token):
        usuario = (
            Usuario.objects.select_related('rol')
            .filter(id=validated_token.get('usuario_id'))
            .first()
        )
        if usuario is None or not usuario.activo:
            raise AuthenticationFailed(
                'El usuario no existe o está inactivo.', code='user_inactive'
            )
        return _TokenUser(validated_token, usuario)


class _TokenUser:
    """Usuario liviano que DRF expone en request.user."""

    def __init__(self, token, usuario):
        self._token = token
        self.usuario = usuario
        self.is_authenticated = True
        self.is_active = True
        self._modulos = None

    @property
    def id(self):
        return self.usuario.id

    @property
    def pk(self):
        # DRF's UserRateThrottle (y otras utilidades) esperan .pk en el user.
        return self.id

    @property
    def email(self):
        return self.usuario.email

    @property
    def rol(self):
        """Nombre del rol ACTUAL del usuario (base de datos, no el del token)."""
        return self.usuario.rol.nombre

    @property
    def es_admin(self):
        return self.rol == ROL_ADMIN

    @property
    def modulos(self) -> set:
        """Módulos activos del rol; se consultan una sola vez por petición."""
        if self._modulos is None:
            self._modulos = modulos_de_rol(self.usuario.rol)
        return self._modulos

    def tiene_modulo(self, *modulos) -> bool:
        return self.es_admin or any(m in self.modulos for m in modulos)

    def __str__(self):
        return self.email