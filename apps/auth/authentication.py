"""
Backend de autenticación JWT personalizado.
Valida el token y extrae el rol sin buscar en auth.User de Django.
"""
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import AccessToken


class ChemJWTAuthentication(JWTAuthentication):
    """
    Autenticación JWT que no requiere un usuario Django.
    Extrae los claims del token y los expone en request.auth.
    """

    def get_user(self, validated_token):
        """
        En lugar de buscar un usuario Django, retornamos un objeto
        simple con los datos del token.
        """
        return _TokenUser(validated_token)


class _TokenUser:
    """Objeto usuario liviano construido desde los claims del JWT."""

    def __init__(self, token):
        self._token = token
        self.is_authenticated = True
        self.is_active = True

    @property
    def id(self):
        return self._token.get('usuario_id')

    @property
    def pk(self):
        # DRF's UserRateThrottle (y otras utilidades) esperan .pk en el user.
        return self.id

    @property
    def email(self):
        return self._token.get('email', '')

    @property
    def rol(self):
        return self._token.get('rol', '')

    def __str__(self):
        return self.email
