"""
CHEM-MODINV — Django Settings
Backend unificado con la lógica de microservicios de Chem Movid.
"""
import sys
import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

DEBUG = os.environ.get('DEBUG', 'False') == 'False'

SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-not-for-production'
    else:
        raise ImproperlyConfigured('SECRET_KEY debe definirse en las variables de entorno en producción (DEBUG=False).')

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Terceros
    'rest_framework',
    'drf_spectacular',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'channels',

    # Apps del proyecto
    'apps.costos_productos.apps.CostosProductosConfig',
    'apps.auth.apps.AuthConfig',
    'apps.inventario.apps.InventarioConfig',
    'apps.compras.apps.ComprasConfig',
    'apps.ventas.apps.VentasConfig',
    'apps.pronosticos.apps.PronosticosConfig',
    'apps.importacion.apps.ImportacionConfig',
    'apps.auditoria.apps.AuditoriaConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'apps.auth.cifrado.CifradoMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'  # Para WebSockets (alertas de stock)

# ─── Base de datos ────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': os.environ.get('DB_NAME', 'comercial_chemical_db'),
        'USER': os.environ.get('DB_USER', 'root'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'admin123'),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

# ─── Channel Layers (WebSocket para alertas stock) ────────────────────────────
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
        # En producción usar Redis:
        # 'BACKEND': 'channels_redis.core.RedisChannelLayer',
        # 'CONFIG': {'hosts': [('127.0.0.1', 6379)]},
    }
}

# ─── Django REST Framework ────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'apps.auth.authentication.ChemJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_THROTTLE_CLASSES': (
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ),
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.environ.get('THROTTLE_RATE_ANON', '20/min'),
        'user': os.environ.get('THROTTLE_RATE_USER', '120/min'),
    },
}

# ─── JWT ──────────────────────────────────────────────────────────────────────
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', SECRET_KEY)
MAX_FAILED_ATTEMPTS = int(os.environ.get('MAX_FAILED_ATTEMPTS', '5'))
LOCKOUT_MINUTES = int(os.environ.get('LOCKOUT_MINUTES', '15'))

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(seconds=int(os.environ.get('ACCESS_TOKEN_EXPIRE_SECONDS', '3600'))),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'usuario_id',
    'SIGNING_KEY': JWT_SECRET_KEY,
}

# ─── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = os.environ.get(
    'CORS_ALLOWED_ORIGINS', 'http://localhost:4200,http://127.0.0.1:4200'
).split(',')
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True
from corsheaders.defaults import default_headers
CORS_ALLOW_HEADERS = (*default_headers, 'x-canal', 'x-canal-consulta')
# ─── Localización ─────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-co'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Swagger / OpenAPI ────────────────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    'TITLE': 'CHEM-MODINV API',
    'DESCRIPTION': (
        'API unificada de gestión de inventarios, compras, pronósticos y auditoría '
        '— Comercial Chemical S.A.S.\n\n'
        'Arquitectura inspirada en los microservicios de Chem Movid, '
        'implementada con Django REST Framework.'
    ),
    'VERSION': '2.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
}

# ── Correo ───────────────────────────────────────────────────────────────────
# Se usa para el correo de bienvenida y para el código de restablecer contraseña.
# Sin EMAIL_HOST en el .env los correos NO se envían: se imprimen en la consola
# del servidor, que es lo cómodo para desarrollar (ahí se ve el código).
EMAIL_HOST = os.environ.get('EMAIL_HOST', '')
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.smtp.EmailBackend' if EMAIL_HOST
    else 'django.core.mail.backends.console.EmailBackend',
)
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'False') == 'True'
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.environ.get(
    'DEFAULT_FROM_EMAIL',
    f"CHEM-MODINV <{EMAIL_HOST_USER or 'no-responder@comercialchemical.local'}>",
)

# Dirección del frontend. Los botones de los correos enlazan a ella:
#   bienvenida -> {FRONTEND_URL}/login
#   código     -> {FRONTEND_URL}/recuperar?correo=...
# En desarrollo el frontend corre en http://localhost:4200 (ng serve), que es
# el valor por defecto. "localhost" solo funciona en este mismo computador:
# quien abra el correo en otro equipo o en el celular no podrá usar el botón.
# Al publicar el sistema, definir FRONTEND_URL en el .env con la dirección real.
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:4200').rstrip('/')

# Minutos de validez del código para restablecer la contraseña.
PASSWORD_RESET_CODE_MINUTES = int(os.environ.get('PASSWORD_RESET_CODE_MINUTES', '15'))

CIFRADO_ACTIVO = os.environ.get('CIFRADO_ACTIVO', 'True') == 'True'
if 'test' in sys.argv:
    CIFRADO_ACTIVO = False

# Prefijos de las rutas cifradas. Activar un módulo = agregar aquí su prefijo.
# El frontend lee esta lista del backend; no hay que tocarlo.
CIFRADO_RUTAS = [
    '/api/',
]