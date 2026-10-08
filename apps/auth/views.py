"""
Microservicio Auth — portado a Django REST Framework.
Fuente: Chem Movid backend/auth/main.py
"""
import uuid
import bcrypt
import logging
from rest_framework.throttling import AnonRateThrottle
from datetime import datetime, timezone, timedelta

from django.conf import settings
from django.utils import timezone as dj_tz
from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from django.db import transaction
from .models import Usuario, Rol, SesionJWT, PermisoRol
from .serializers import (
    LoginSerializer, UsuarioSerializer, CrearUsuarioSerializer,
    ActualizarUsuarioSerializer, ResetPasswordSerializer, RolSerializer,
    SolicitarCodigoSerializer, RestablecerPasswordSerializer,
)
from .correos import enviar_bienvenida, enviar_codigo_recuperacion
from .recuperacion import (
    consumir_codigo, descartar_codigos, generar_codigo, minutos_validez,
    segundos_para_reenviar,
)
from .permissions import IsAdmin
from .modulos import (
    ROL_ADMIN, MODULOS_SOLO_ADMIN, todos_los_modulos, modulos_de_rol,
    inicializar_permisos,
)
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
        return Response(RolSerializer(Rol.objects.all(), many=True).data)


class PermisosRolView(APIView):
    """GET /api/auth/permisos/ → módulos activos de cada rol
       PUT /api/auth/permisos/ → actualizar los módulos de un rol

    Lo que se guarda aquí es lo que el backend exige en cada endpoint
    (permissions.requiere_modulo) y lo que el frontend usa para armar el menú."""
    permission_classes = [IsAdmin]

    def get(self, request):
        inicializar_permisos()
        resultado = []
        for rol in Rol.objects.all().order_by('id'):
            activos = modulos_de_rol(rol)
            resultado.append({
                'rol_id': rol.id,
                'rol_nombre': rol.nombre,
                'descripcion': rol.descripcion,
                'permisos': {m: m in activos for m in todos_los_modulos()},
                # Módulos que no se pueden cambiar para este rol (la pantalla
                # de permisos los muestra bloqueados).
                'modulos_bloqueados': (
                    todos_los_modulos() if rol.nombre == ROL_ADMIN
                    else sorted(MODULOS_SOLO_ADMIN)
                ),
            })
        return Response(resultado)

    def put(self, request):
        rol_id = request.data.get('rol_id')
        permisos = request.data.get('permisos')
        try:
            rol = Rol.objects.get(id=rol_id)
        except (Rol.DoesNotExist, ValueError, TypeError):
            return Response({'error': 'Rol no encontrado'}, status=404)
        if rol.nombre == ROL_ADMIN:
            return Response({'error': 'No se pueden modificar los permisos del Administrador'}, status=400)
        if not isinstance(permisos, dict) or not permisos:
            return Response({'error': 'Envía "permisos" como un objeto {modulo: true/false}.'}, status=400)

        catalogo = set(todos_los_modulos())
        desconocidos = sorted(set(permisos) - catalogo)
        if desconocidos:
            return Response({'error': f"Módulos desconocidos: {', '.join(desconocidos)}"}, status=400)
        no_booleanos = sorted(m for m, v in permisos.items() if not isinstance(v, bool))
        if no_booleanos:
            return Response({'error': f"El valor debe ser true o false en: {', '.join(no_booleanos)}"}, status=400)
        reservados = sorted(m for m, v in permisos.items() if v and m in MODULOS_SOLO_ADMIN)
        if reservados:
            return Response(
                {'error': f"Solo el Administrador puede tener: {', '.join(reservados)}"}, status=400)

        antes = sorted(modulos_de_rol(rol))
        with transaction.atomic():
            for modulo, activo in permisos.items():
                PermisoRol.objects.update_or_create(
                    rol=rol, modulo=modulo, defaults={'activo': activo})
        despues = sorted(modulos_de_rol(rol))

        registrar_evento(
            'ACTUALIZAR_PERMISOS',
            f'Permisos del rol {rol.nombre} actualizados',
            usuario_id=request.user.id,
            entidad='rol', entidad_id=rol.id,
            datos_anteriores={'modulos': antes},
            datos_nuevos={'modulos': despues},
            ip=_get_client_ip(request),
        )
        return Response({
            'message': f'Permisos de {rol.nombre} actualizados',
            'rol_id': rol.id,
            'permisos': {m: m in despues for m in todos_los_modulos()},
        })

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
                'modulos': sorted(modulos_de_rol(usuario.rol)),
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
    """Perfil del usuario autenticado con su rol y módulos ACTUALES. El
    frontend lo consulta al cargar para armar el menú, de modo que un cambio
    de permisos se refleja sin tener que volver a iniciar sesión."""

    def get(self, request):
        usuario = request.user.usuario
        data = UsuarioSerializer(usuario).data
        data['modulos'] = sorted(request.user.modulos)
        data['es_admin'] = request.user.es_admin
        return Response(data)

# ── Recuperación de contraseña con código enviado al correo ──────────────────

class LimiteRecuperacion(AnonRateThrottle):
    """Frena el abuso de los endpoints públicos de recuperación (por IP)."""
    scope = 'recuperacion_password'
    rate = '10/min'


class SolicitarCodigoPasswordView(APIView):
    """POST /api/auth/password/codigo/  {"correo": "..."}

    Envía al correo un código de un solo uso para restablecer la contraseña.
    Es público (lo usa «¿Olvidaste tu contraseña?» en el login) y responde
    siempre lo mismo, exista o no el correo, para no revelar qué cuentas hay."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [LimiteRecuperacion]

    @extend_schema(request=SolicitarCodigoSerializer)
    def post(self, request):
        serializer = SolicitarCodigoSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        correo = serializer.validated_data['correo']
        usuario = (
            Usuario.objects.select_related('rol')
            .filter(email__iexact=correo, activo=True).first()
        )
        # Solo se envía si la cuenta existe, está activa y no se le mandó otro
        # código hace menos de un minuto.
        if usuario is not None and segundos_para_reenviar(usuario) == 0:
            codigo = generar_codigo(usuario)
            if enviar_codigo_recuperacion(usuario, codigo, minutos_validez()):
                registrar_evento(
                    'SOLICITAR_CODIGO_PASSWORD',
                    f'Código para restablecer contraseña enviado a {usuario.email}',
                    usuario_id=usuario.id, entidad='usuario', entidad_id=usuario.id,
                    ip=_get_client_ip(request),
                )
            else:
                descartar_codigos(usuario)

        return Response({
            'message': 'Si el correo está registrado, enviamos un código para restablecer la contraseña.',
            'minutos_validez': minutos_validez(),
        })


class RestablecerPasswordView(APIView):
    """POST /api/auth/password/restablecer/
       {"correo": "...", "codigo": "123456", "nueva_password": "..."}

    Cambia la contraseña si el código es el vigente de esa cuenta."""
    authentication_classes = []
    permission_classes = []
    throttle_classes = [LimiteRecuperacion]

    @extend_schema(request=RestablecerPasswordSerializer)
    def post(self, request):
        serializer = RestablecerPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)

        d = serializer.validated_data
        usuario = Usuario.objects.filter(email__iexact=d['correo'], activo=True).first()
        if usuario is None or not consumir_codigo(usuario, d['codigo']):
            return Response(
                {'error': 'El código no es válido o ya venció. Solicita uno nuevo.'}, status=400)

        usuario.password_hash = _hash_password(d['nueva_password'])
        # Quien demuestra tener acceso al correo recupera la cuenta aunque
        # estuviera bloqueada por intentos fallidos.
        usuario.intentos_fallidos = 0
        usuario.bloqueado_hasta = None
        usuario.save(update_fields=[
            'password_hash', 'intentos_fallidos', 'bloqueado_hasta', 'fecha_modificacion'])
        SesionJWT.objects.filter(usuario=usuario).delete()

        registrar_evento(
            'RESET_PASSWORD', f'Contraseña restablecida con código por {usuario.email}',
            usuario_id=usuario.id, entidad='usuario', entidad_id=usuario.id,
            ip=_get_client_ip(request),
        )
        return Response({'message': 'Contraseña actualizada. Ya puedes iniciar sesión.'})
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
        correo_enviado = enviar_bienvenida(usuario)
        data = UsuarioSerializer(usuario).data
        data['correo_enviado'] = correo_enviado
        return Response(data, status=201)


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
    """POST /api/auth/usuarios/<id>/reset-password/

    El Administrador ya no escribe la contraseña de otra persona: este
    endpoint le envía al usuario un código a su correo para que él mismo
    defina la nueva en la pantalla de recuperación."""
    permission_classes = [IsAdmin]

    def post(self, request, usuario_id):
        try:
            usuario = Usuario.objects.select_related('rol').get(id=usuario_id)
        except Usuario.DoesNotExist:
            return Response({'error': 'Usuario no encontrado'}, status=404)
        if not usuario.activo:
            return Response(
                {'error': 'El usuario está inactivo. Actívalo antes de restablecer su contraseña.'},
                status=400)

        espera = segundos_para_reenviar(usuario)
        if espera > 0:
            return Response(
                {'error': f'Ya se le envió un código hace un momento. Espera {espera} segundos para enviar otro.'},
                status=429)

        codigo = generar_codigo(usuario)
        if not enviar_codigo_recuperacion(usuario, codigo, minutos_validez(), solicitado_por_admin=True):
            descartar_codigos(usuario)
            return Response(
                {'error': 'No se pudo enviar el correo. Revisa la configuración de correo del servidor.'},
                status=502)

        registrar_evento('RESET_PASSWORD_SOLICITADO',
                         f'Código para restablecer contraseña enviado a {usuario.email}',
                         usuario_id=request.user.id, entidad='usuario', entidad_id=usuario.id,
                         ip=_get_client_ip(request))
        return Response({
            'message': f'Se envió un código a {usuario.email}.',
            'correo': usuario.email,
            'minutos_validez': minutos_validez(),
        })