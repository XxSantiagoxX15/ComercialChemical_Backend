from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    HealthView, LoginView, LogoutView, MeView,
    UsuarioListView, UsuarioDetailView, ResetPasswordView,
    RolListView, PermisosRolView, SolicitarCodigoPasswordView, RestablecerPasswordView)

urlpatterns = [
    path('health/', HealthView.as_view()),
    path('login/', LoginView.as_view()),
    path('logout/', LogoutView.as_view()),
    path('refresh/', TokenRefreshView.as_view()),
    path('me/', MeView.as_view()),
    path('password/codigo/', SolicitarCodigoPasswordView.as_view()),
    path('password/restablecer/', RestablecerPasswordView.as_view()),
    path('roles/', RolListView.as_view()),
    path('permisos/', PermisosRolView.as_view()),
    path('usuarios/', UsuarioListView.as_view()),
    path('usuarios/<int:usuario_id>/', UsuarioDetailView.as_view()),
    path('usuarios/<int:usuario_id>/reset-password/', ResetPasswordView.as_view()),
]
