"""
Permisos RBAC — portados de shared/jwt_middleware.py del Chem Movid.
Se usan en todas las apps del proyecto.
"""
from rest_framework.permissions import BasePermission


def _get_rol(request):
    """Extrae el nombre del rol del token JWT."""
    # Primero intenta desde request.auth (token directo)
    token = getattr(request, 'auth', None)
    if token and hasattr(token, 'get'):
        rol = token.get('rol')
        if rol:
            return rol
    # Luego intenta desde request.user (ChemJWTAuthentication)
    user = getattr(request, 'user', None)
    if user and hasattr(user, 'rol'):
        return user.rol
    return None


class IsAdmin(BasePermission):
    """Solo Administrador."""
    def has_permission(self, request, view):
        return _get_rol(request) == 'Administrador'


class IsAdminOrInventario(BasePermission):
    """Administrador o Analista_Inventarios."""
    def has_permission(self, request, view):
        return _get_rol(request) in ('Administrador', 'Analista_Inventarios')


class IsAdminOrCompras(BasePermission):
    """Administrador o Responsable_Compras."""
    def has_permission(self, request, view):
        return _get_rol(request) in ('Administrador', 'Responsable_Compras')


class IsAuthenticated(BasePermission):
    """Cualquier rol autenticado."""
    def has_permission(self, request, view):
        return _get_rol(request) in ('Administrador', 'Analista_Inventarios', 'Responsable_Compras')
