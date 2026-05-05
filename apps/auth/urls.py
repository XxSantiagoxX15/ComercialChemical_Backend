from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from .views import (
    HealthView, LoginView, LogoutView, MeView,
    UsuarioListView, UsuarioDetailView, ResetPasswordView
)

urlpatterns = [
    path('health/', HealthView.as_view()),
    path('login/', LoginView.as_view()),
    path('logout/', LogoutView.as_view()),
    path('refresh/', TokenRefreshView.as_view()),   # POST { refresh } → { access }
    path('me/', MeView.as_view()),
    path('usuarios/', UsuarioListView.as_view()),
    path('usuarios/<int:usuario_id>/', UsuarioDetailView.as_view()),
    path('usuarios/<int:usuario_id>/reset-password/', ResetPasswordView.as_view()),
]
