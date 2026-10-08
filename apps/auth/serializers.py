from rest_framework import serializers
from .models import Usuario, Rol, PermisoRol


class RolSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rol
        fields = ['id', 'nombre', 'descripcion']


class PermisoRolSerializer(serializers.ModelSerializer):
    class Meta:
        model = PermisoRol
        fields = ['id', 'rol', 'modulo', 'activo']


class UsuarioSerializer(serializers.ModelSerializer):
    rol_nombre = serializers.CharField(source='rol.nombre', read_only=True)

    class Meta:
        model = Usuario
        fields = ['id', 'nombre_completo', 'email', 'rol', 'rol_nombre', 'activo',
                  'fecha_creacion', 'fecha_modificacion']
        read_only_fields = ['id', 'fecha_creacion', 'fecha_modificacion']


class CrearUsuarioSerializer(serializers.Serializer):
    nombre_completo = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=6)
    rol_id = serializers.IntegerField()

    def validate_rol_id(self, value):
        if not Rol.objects.filter(id=value).exists():
            raise serializers.ValidationError("Rol no encontrado.")
        return value

    def validate_email(self, value):
        if Usuario.objects.filter(email=value).exists():
            raise serializers.ValidationError("El correo ya está registrado.")
        return value


class LoginSerializer(serializers.Serializer):
    correo = serializers.EmailField()
    contrasena = serializers.CharField(write_only=True)


class ActualizarUsuarioSerializer(serializers.Serializer):
    nombre_completo = serializers.CharField(required=False)
    rol_id = serializers.IntegerField(required=False)
    activo = serializers.BooleanField(required=False)


class ResetPasswordSerializer(serializers.Serializer):
    nueva_password = serializers.CharField(min_length=6)
class SolicitarCodigoSerializer(serializers.Serializer):
    correo = serializers.EmailField()


class RestablecerPasswordSerializer(serializers.Serializer):
    correo = serializers.EmailField()
    codigo = serializers.RegexField(
        r'^\d{6}$', error_messages={'invalid': 'El código tiene 6 dígitos.'})
    nueva_password = serializers.CharField(min_length=6, write_only=True)