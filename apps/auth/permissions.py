"""
Permisos del proyecto.

Las clases conservan sus nombres porque todas las apps las importan, pero ya
no deciden solo por el nombre del rol: primero miran si la ruta pedida está
gobernada por un módulo (modulos.ACCESO_POR_RUTA) y, si lo está, dejan pasar
a quien tenga ese módulo activo en la tabla permiso_rol. Si la ruta no está
en ese mapa, se comportan como siempre (por nombre de rol).

El Administrador pasa siempre.
"""
from rest_framework.permissions import BasePermission

from .modulos import ROL_ADMIN, modulos_de_ruta


def _usuario(request):
    """request.user cuando viene de ChemJWTAuthentication; si no, None."""
    user = getattr(request, 'user', None)
    return user if getattr(user, 'usuario', None) is not None else None


def _get_rol(request):
    """Nombre del rol actual del usuario autenticado (leído de la base)."""
    user = _usuario(request)
    return user.rol if user else None

def _campos_enviados(request):
    """Nombres de los campos del cuerpo de la petición (vacío en un GET)."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return []
    try:
        return list(request.data.keys())
    except Exception:
        return []
class _PermisoPorModulo(BasePermission):
    # Roles (además del Administrador) que pasan cuando la ruta NO está
    # gobernada por módulos: el comportamiento original de cada clase.
    roles_sin_modulo = ()
    message = 'Tu rol no tiene acceso a este módulo.'

    def has_permission(self, request, view):
        user = _usuario(request)
        if user is None:
            return False
        if user.es_admin:
            return True
        modulos = modulos_de_ruta(request.method, request.path_info, _campos_enviados(request))
        if modulos is None:
            return user.rol in self.roles_sin_modulo
        return user.tiene_modulo(*modulos)


class IsAdmin(_PermisoPorModulo):
    """Solo Administrador, salvo en rutas que el mapa asigna a un módulo
    (historial de importación y auditoría)."""
    roles_sin_modulo = ()


class IsAdminOrInventario(_PermisoPorModulo):
    """Según el módulo de la ruta; fuera del mapa, Administrador o Analista."""
    roles_sin_modulo = ('Analista_Inventarios',)


class IsAdminOrCompras(_PermisoPorModulo):
    """Según el módulo de la ruta; fuera del mapa, Administrador o Compras."""
    roles_sin_modulo = ('Responsable_Compras',)


class IsAuthenticated(BasePermission):
    """Cualquier usuario activo del sistema."""
    def has_permission(self, request, view):
        return _usuario(request) is not None