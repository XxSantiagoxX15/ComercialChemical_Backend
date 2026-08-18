"""
Microservicio Auth — portado a Django REST Framework.
Fuente: Chem Movid backend/auth/main.py
"""
import uuid
import bcrypt
import logging
from datetime import datetime, timezone, timedelta

from django.conf import settings
from django.utils import timezone as dj_tz
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Usuario, Rol, SesionJWT
from .serializers import (
    LoginSerializer, UsuarioSerializer, CrearUsuarioSerializer,
    ActualizarUsuarioSerializer, ResetPasswordSerializer
)
from .permissions import IsAdmin
from apps.auditoria.services import registrar_evento

logger = logging.getLogger(__name__)


def _hash_password(plain: str) -> str:
    rounds = max(getattr(settings, 'BCRYPT_ROUNDS', 12), 10)
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _get_client_ip(request) -> str:
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', 'unknown')


class RolListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        roles = Rol.objects.all()
        from .serializers import RolSerializer
        return Response(RolSerializer(roles, many=True).data)


class PermisosRolView(APIView):
    """GET /api/auth/permisos/ → todos los permisos por rol
       PUT /api/auth/permisos/ → actualizar permisos de un rol"""
    permission_classes = [IsAdmin]

    MODULOS_DEFAULT = {
        'Administrador': [
            'dashboard','inventario_articulos','inventario_movimientos',
            'inventario_alertas','compras','pronosticos',
            'importacion','auditoria','usuarios'
        ],
        'Analista_Inventarios': [
            'dashboard','inventario_articulos','inventario_movimientos',
            'inventario_alertas','pronosticos'
        ],
        'Responsable_Compras': [
            'dashboard','compras','inventario_alertas','pronosticos'
        ],
    }

    def _inicializar_permisos(self):
        """Crea los permisos por defecto si no existen."""
        from .models import PermisoRol
        todos_modulos = [m[0] for m in PermisoRol.MODULOS]
        for rol in Rol.objects.all():
            defaults = self.MODULOS_DEFAULT.get(rol.nombre, [])
            for modulo in todos_modulos:
                PermisoRol.objects.get_or_create(
                    rol=rol, modulo=modulo,
                    defaults={'activo': modulo in defaults}
                )

    def get(self, request):
        from .models import PermisoRol
        self._inicializar_permisos()
        roles = Rol.objects.prefetch_related('permisos').all()
        resultado = []
        for rol in roles:
            permisos = {p.modulo: p.activo for p in rol.permisos.all()}
            resultado.append({
                'rol_id': rol.id,
                'rol_nombre': rol.nombre,
                'descripcion': rol.descripcion,
                'permisos': permisos,
            })
        return Response(resultado)

    def put(self, request):
        from .models import PermisoRol
        rol_id  = request.data.get('rol_id')
        permisos = request.data.get('permisos', {})
        try:
            rol = Rol.objects.get(id=rol_id)
        except Rol.DoesNotExist:
            return Response({'error': 'Rol no encontrado'}, status=404)
        if rol.nombre == 'Administrador':
            return Response({'error': 'No se pueden modificar los permisos del Administrador'}, status=400)
        for modulo, activo in permisos.items():
            PermisoRol.objects.update_or_create(
                rol=rol, modulo=modulo,
                defaults={'activo': bool(activo)}
            )
        registrar_evento(
            'ACTUALIZAR_PERMISOS',
            f'Permisos del rol {rol.nombre} actualizados',
            usuario_id=request.auth.get('usuario_id') if request.auth else None,
            entidad='rol', entidad_id=rol_id,
            ip=_get_client_ip(request),
        )
        return Response({'message': f'Permisos de {rol.nombre} actualizados'})


class HealthView(APIView):
    permission_classes = []

    def get(self, request):
        return Response({'status': 'ok', 'service': 'auth'})


class LoginView(APIView):
    permission_classes = []

    @extend_schema(request=LoginSerializer)
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        correo = serializer.validated_data['correo']
        contrasena = serializer.validated_data['contrasena']
        ip = _get_client_ip(request)
        error_generico = Response({'error': 'Credenciales inválidas'}, status=401)

        try:
            usuario = Usuario.objects.select_related('rol').get(email=correo)
        except Usuario.DoesNotExist:
            return error_generico

        # Verificar bloqueo (Req 11.5 Chem Movid)
        if usuario.bloqueado_hasta and usuario.bloqueado_hasta > dj_tz.now():
            remaining = int((usuario.bloqueado_hasta - dj_tz.now()).total_seconds() / 60) + 1
            return Response(
                {'error': f'Cuenta bloqueada. Intente en {remaining} minuto(s).'},
                status=403
            )

        if not usuario.activo:
            return error_generico

        if not _verify_password(contrasena, usuario.password_hash):
            max_intentos = getattr(settings, 'MAX_FAILED_ATTEMPTS', 5)
            lockout_min = getattr(settings, 'LOCKOUT_MINUTES', 15)
            nuevos_intentos = usuario.intentos_fallidos + 1

            if nuevos_intentos >= max_intentos:
                usuario.bloqueado_hasta = dj_tz.now() + timedelta(minutes=lockout_min)
                registrar_evento(
                    tipo_accion='CUENTA_BLOQUEADA',
                    descripcion=f'Cuenta {correo} bloqueada por {max_intentos} intentos fallidos',
                    usuario_id=usuario.id,
                    entidad='usuario',
                    entidad_id=usuario.id,
                    ip=ip,
                )

            usuario.intentos_fallidos = nuevos_intentos
            usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta', 'fecha_modificacion'])
            return error_generico

        # Login exitoso — resetear intentos
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.save(update_fields=['intentos_fallidos', 'bloqueado_hasta', 'fecha_modificacion'])

        # Generar JWT con claims de rol (compatible con Chem Movid)
        refresh = RefreshToken()
        refresh['usuario_id'] = usuario.id
        refresh['email'] = usuario.email
        refresh['rol'] = usuario.rol.nombre
        access = refresh.access_token

        jti = str(access.get('jti', uuid.uuid4()))
        SesionJWT.objects.create(
            usuario=usuario,
            token_jti=jti,
            fecha_expiracion=dj_tz.now() + settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'],
        )

        registrar_evento(
            tipo_accion='LOGIN',
            descripcion=f'Inicio de sesión: {correo}',
            usuario_id=usuario.id,
            entidad='usuario',
            entidad_id=usuario.id,
            ip=ip,
        )

        return Response({
            'access_token': str(access),
            'refresh_token': str(refresh),
            'token_type': 'bearer',
            'expires_in': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
            'user': {
                'id': usuario.id,
                'nombre_completo': usuario.nombre_completo,
                'email': usuario.email,
                'rol': usuario.rol.nombre,
            }
        })


class LogoutView(APIView):
    @extend_schema(description='Cierra sesión e invalida el token JWT.')
    def post(self, request):
        token = request.auth
        usuario_id = token.get('usuario_id') if token else None
        try:
            if token:
                jti = token.get('jti')
                SesionJWT.objects.filter(token_jti=jti).delete()
                RefreshToken(str(token)).blacklist()
        except Exception:
            logger.exception('Error al invalidar el token JWT en logout (usuario_id=%s)', usuario_id)
        registrar_evento('LOGOUT', 'Cierre de sesión', usuario_id=usuario_id, ip=_get_client_ip(request))
        return Response({'message': 'Sesión cerrada exitosamente'})


class MeView(APIView):
    def get(self, request):
        token = request.auth
        if not token:
            return Response({'error': 'No autenticado'}, status=401)
        usuario_id = token.get('usuario_id')
        try:
            usuario = Usuario.objects.select_related('rol').get(id=usuario_id)
        except Usuario.DoesNotExist:
            return Response({'error': 'Usuario no encontrado'}, status=404)
        return Response(UsuarioSerializer(usuario).data)


class UsuarioListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        usuarios = Usuario.objects.select_related('rol').all()
        return Response({'usuarios': UsuarioSerializer(usuarios, many=True).data, 'total': usuarios.count()})

    @extend_schema(request=CrearUsuarioSerializer)
    def post(self, request):
        serializer = CrearUsuarioSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        d = serializer.validated_data
        rol = Rol.objects.get(id=d['rol_id'])
        usuario = Usuario.objects.create(
            nombre_completo=d['nombre_completo'],
            email=d['email'],
            password_hash=_hash_password(d['password']),
            rol=rol,
        )
        registrar_evento(
            tipo_accion='CREAR_USUARIO',
            descripcion=f"Usuario creado: {d['email']} con rol {rol.nombre}",
            usuario_id=usuario.id,
            entidad='usuario',
            entidad_id=usuario.id,
            ip=_get_client_ip(request),
        )
        return Response(UsuarioSerializer(usuario).data, status=201)


class UsuarioDetailView(APIView):
    permission_classes = [IsAdmin]

    def put(self, request, usuario_id):
        try:
            usuario = Usuario.objects.select_related('rol').get(id=usuario_id)
        except Usuario.DoesNotExist:
            return Response({'error': 'Usuario no encontrado'}, status=404)

        serializer = ActualizarUsuarioSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        d = serializer.validated_data
        if 'nombre_completo' in d:
            usuario.nombre_completo = d['nombre_completo']
        if 'rol_id' in d:
            usuario.rol = Rol.objects.get(id=d['rol_id'])
        if 'activo' in d:
            usuario.activo = d['activo']
            if not d['activo']:
                # Invalidar sesiones activas
                SesionJWT.objects.filter(usuario=usuario).delete()

        usuario.save()
        registrar_evento('ACTUALIZAR_USUARIO', f'Usuario {usuario_id} actualizado',
                         usuario_id=usuario_id, entidad='usuario', entidad_id=usuario_id,
                         ip=_get_client_ip(request))
        return Response(UsuarioSerializer(usuario).data)


class ResetPasswordView(APIView):
    permission_classes = [IsAdmin]

    def post(self, request, usuario_id):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        try:
            usuario = Usuario.objects.get(id=usuario_id)
        except Usuario.DoesNotExist:
            return Response({'error': 'Usuario no encontrado'}, status=404)

        usuario.password_hash = _hash_password(serializer.validated_data['nueva_password'])
        usuario.save(update_fields=['password_hash', 'fecha_modificacion'])
        registrar_evento('RESET_PASSWORD', f'Contraseña restablecida usuario {usuario_id}',
                         usuario_id=usuario_id, entidad='usuario', entidad_id=usuario_id,
                         ip=_get_client_ip(request))
<<<<<<< HEAD
        return Response({'message': 'Contraseña restablecida exitosamente'})
=======
        return Response({'message': 'Contraseña restablecida exitosamente'})
>>>>>>> 4cc41a2f222638035a5a565a4f6c56f3f6449790
