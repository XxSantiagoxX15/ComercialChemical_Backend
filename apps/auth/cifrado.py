"""
Canal cifrado entre el navegador y la API (cifrado de punta a punta de la
carga útil, además de HTTPS).

Qué hace
--------
En las rutas listadas en settings.CIFRADO_RUTAS viaja cifrado con AES-256-GCM:
  - el cuerpo de la petición (JSON y formularios con archivos),
  - los parámetros de la URL (?buscar=...&limit=...),
  - el cuerpo de la respuesta (JSON y descargas PDF/Excel).
En la pestaña Network del navegador no se lee nada de eso. Las vistas no se
enteran: este middleware descifra antes de que la vista lea la petición y
cifra lo que la vista responde. Lo único visible es la ruta (p. ej.
/api/compras/ordenes/5/) y el código de estado.

Cómo se acuerda la clave (sin que viaje por la red)
---------------------------------------------------
1. El servidor tiene un par de claves ECDH (curva P-256). La privada se deriva
   de SECRET_KEY, así que es la misma tras un reinicio y entre procesos, y no
   hay que guardarla en ningún lado. La pública se entrega en GET /api/auth/canal/.
2. El navegador genera su propio par al cargar la aplicación y envía su clave
   pública en el encabezado X-Canal de cada petición.
3. Ambos lados calculan el mismo secreto (ECDH) y de él derivan dos claves
   AES, una por sentido (HKDF-SHA256). Las claves AES nunca se transmiten.

Cada mensaje usa un vector de inicialización aleatorio y queda atado al
método y la ruta de la petición: un mensaje copiado de una ruta no sirve en otra.

Formatos en la red
------------------
  JSON      → {"cifrado": "v1", "datos": base64(iv + cifrado)}
  Binario   → MAGIA + iv + cifrado, con Content-Type application/octet-stream.
              El texto cifrado es "<content-type original>\\n" + los bytes.
              Se usa para formularios con archivos y para descargas.
  Consulta  → encabezado X-Canal-Consulta: base64(iv + cifrado) de "a=1&b=2".
"""
import base64
import binascii
import functools
import hashlib
import json
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

VERSION = 'v1'
ENCABEZADO_CANAL = 'X-Canal'
ENCABEZADO_CONSULTA = 'X-Canal-Consulta'
MAGIA = b'\x00CHEM-CIFRADO-v1\x00'
_CURVA = ec.SECP256R1()
_ORDEN_P256 = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_TAM_IV = 12
_TAM_SELLO = 16

# Rutas que nunca se cifran aunque un prefijo de CIFRADO_RUTAS las abarque:
#  - canal:   el navegador la pide antes de poder cifrar nada;
#  - health:  las consultan herramientas de monitoreo y no devuelven datos;
#  - schema, swagger, redoc: la documentación de la API se abre en el navegador.
# Se puede cambiar con settings.CIFRADO_RUTAS_PUBLICAS.
RUTAS_PUBLICAS = (
    '/api/auth/canal/',
    '/api/auth/health/',
    '/api/inventario/health/',
    '/api/schema/',
    '/api/swagger/',
    '/api/redoc/',
)


class ErrorDeCifrado(Exception):
    """El mensaje no se pudo descifrar (clave distinta, datos alterados o mal formados)."""


class _SinCifrar(Exception):
    """Llegó contenido en claro a una ruta que exige cifrado."""


# ── Configuración ────────────────────────────────────────────────────────────

def cifrado_activo() -> bool:
    return bool(getattr(settings, 'CIFRADO_ACTIVO', True))


def rutas_cifradas() -> list:
    return list(getattr(settings, 'CIFRADO_RUTAS', [])) if cifrado_activo() else []


def rutas_publicas() -> list:
    return list(getattr(settings, 'CIFRADO_RUTAS_PUBLICAS', RUTAS_PUBLICAS))


def ruta_protegida(ruta: str) -> bool:
    if any(ruta.startswith(publica) for publica in rutas_publicas()):
        return False
    return any(ruta.startswith(prefijo) for prefijo in rutas_cifradas())


def _maximo_bytes() -> int:
    return int(getattr(settings, 'CIFRADO_MAX_BYTES', 60 * 1024 * 1024))


# ── Claves ───────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=4)
def _clave_privada_servidor(secret_key: str):
    """Clave privada ECDH del servidor, derivada de SECRET_KEY."""
    semilla = HKDF(
        algorithm=hashes.SHA256(), length=48, salt=None,
        info=b'chem-modinv/canal/servidor/v1',
    ).derive(secret_key.encode())
    escalar = int.from_bytes(semilla, 'big') % (_ORDEN_P256 - 1) + 1
    return ec.derive_private_key(escalar, _CURVA)


def clave_publica_servidor() -> bytes:
    """Clave pública del servidor (punto sin comprimir, 65 bytes)."""
    return _clave_privada_servidor(settings.SECRET_KEY).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


@functools.lru_cache(maxsize=4096)
def _derivar(secret_key: str, publica_cliente: bytes) -> tuple:
    """(clave para peticiones, clave para respuestas) acordadas con ese navegador."""
    privada = _clave_privada_servidor(secret_key)
    try:
        punto = ec.EllipticCurvePublicKey.from_encoded_point(_CURVA, publica_cliente)
    except ValueError as exc:
        raise ErrorDeCifrado('Clave pública del navegador inválida.') from exc
    secreto = privada.exchange(ec.ECDH(), punto)
    sal = hashlib.sha256(publica_cliente + clave_publica_servidor()).digest()

    def clave(sentido: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(), length=32, salt=sal,
            info=b'chem-modinv/canal/v1/' + sentido,
        ).derive(secreto)

    return clave(b'peticion'), clave(b'respuesta')


def claves_del_canal(encabezado: str) -> tuple:
    """Claves AES a partir del encabezado X-Canal (clave pública en base64)."""
    try:
        publica = base64.b64decode(encabezado, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ErrorDeCifrado('Encabezado X-Canal mal formado.') from exc
    return _derivar(settings.SECRET_KEY, publica)


# ── Cifrar y descifrar ───────────────────────────────────────────────────────

def _contexto(sentido: str, metodo: str, ruta: str) -> bytes:
    """Dato autenticado: ata el mensaje a su sentido, método y ruta.
    Sentidos: PET (cuerpo de petición), CON (consulta de la URL), RES (respuesta)."""
    return f'{sentido}|{metodo.upper()}|{ruta}'.encode()


def _sellar(clave: bytes, datos: bytes, contexto: bytes) -> bytes:
    iv = os.urandom(_TAM_IV)
    return iv + AESGCM(clave).encrypt(iv, datos, contexto)


def _abrir(clave: bytes, crudo: bytes, contexto: bytes) -> bytes:
    if len(crudo) < _TAM_IV + _TAM_SELLO:
        raise ErrorDeCifrado('Mensaje demasiado corto.')
    try:
        return AESGCM(clave).decrypt(crudo[:_TAM_IV], crudo[_TAM_IV:], contexto)
    except (InvalidTag, ValueError) as exc:
        raise ErrorDeCifrado('No se pudo descifrar el mensaje.') from exc


def cifrar(clave: bytes, datos: bytes, contexto: bytes) -> str:
    """Texto: devuelve base64(iv + cifrado)."""
    return base64.b64encode(_sellar(clave, datos, contexto)).decode()


def descifrar(clave: bytes, sobre_b64: str, contexto: bytes) -> bytes:
    try:
        crudo = base64.b64decode(sobre_b64, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ErrorDeCifrado('No se pudo descifrar el mensaje.') from exc
    return _abrir(clave, crudo, contexto)


def cifrar_binario(clave: bytes, tipo: str, datos: bytes, contexto: bytes) -> bytes:
    """Binario: MAGIA + iv + cifrado de "<tipo>\\n<datos>"."""
    return MAGIA + _sellar(clave, tipo.encode() + b'\n' + datos, contexto)


def descifrar_binario(clave: bytes, crudo: bytes, contexto: bytes) -> tuple:
    """Devuelve (content-type original, datos)."""
    if not crudo.startswith(MAGIA):
        raise _SinCifrar()
    claro = _abrir(clave, crudo[len(MAGIA):], contexto)
    tipo, separador, datos = claro.partition(b'\n')
    if not separador:
        raise ErrorDeCifrado('Mensaje binario mal formado.')
    return tipo.decode(errors='replace'), datos


def es_sobre(valor) -> bool:
    return (
        isinstance(valor, dict) and set(valor) == {'cifrado', 'datos'}
        and valor['cifrado'] == VERSION and isinstance(valor['datos'], str)
    )


# ── Middleware ───────────────────────────────────────────────────────────────

def _rechazo(mensaje: str, codigo: str, status: int = 400) -> JsonResponse:
    return JsonResponse({'error': mensaje, 'codigo': codigo}, status=status)


class CifradoMiddleware:
    """Descifra las peticiones y cifra las respuestas de las rutas protegidas."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'OPTIONS' or not ruta_protegida(request.path):
            return self.get_response(request)

        encabezado = request.headers.get(ENCABEZADO_CANAL)
        if not encabezado:
            return _rechazo('Esta ruta solo acepta peticiones cifradas.', 'cifrado_requerido')
        try:
            clave_peticion, clave_respuesta = claves_del_canal(encabezado)
            self._descifrar_consulta(request, clave_peticion)
            self._descifrar_cuerpo(request, clave_peticion)
        except ErrorDeCifrado as exc:
            # El navegador reacciona a este código pidiendo de nuevo la clave
            # pública del servidor (por si cambió) y reintentando una vez.
            return _rechazo(str(exc), 'cifrado_invalido')
        except _SinCifrar:
            return _rechazo('Esta ruta solo acepta peticiones cifradas.', 'cifrado_requerido')
        except _Demasiado:
            return _rechazo('La petición es demasiado grande.', 'demasiado_grande', status=413)

        response = self.get_response(request)
        return self._cifrar_respuesta(request, response, clave_respuesta)

    # ── Petición ──────────────────────────────────────────────────────────

    @staticmethod
    def _descifrar_consulta(request, clave: bytes) -> None:
        """Los parámetros de la URL llegan cifrados en un encabezado y se
        reponen donde la vista los espera (request.GET / query_params)."""
        cifrada = request.headers.get(ENCABEZADO_CONSULTA)
        if request.META.get('QUERY_STRING'):
            raise _SinCifrar()          # parámetros en claro en la URL
        if not cifrada:
            return
        consulta = descifrar(clave, cifrada, _contexto('CON', request.method, request.path))
        # WSGI guarda la consulta como texto latin-1 que representa los bytes.
        request.META['QUERY_STRING'] = consulta.decode('iso-8859-1')
        request.__dict__.pop('GET', None)   # por si alguien ya la había leído

    @staticmethod
    def _descifrar_cuerpo(request, clave: bytes) -> None:
        try:
            largo = int(request.META.get('CONTENT_LENGTH') or 0)
        except ValueError:
            largo = 0
        if largo <= 0:
            return
        if largo > _maximo_bytes():
            raise _Demasiado()

        # Se lee del flujo (no de request.body) para que el límite de Django
        # pensado para formularios no rechace un archivo grande ya cifrado.
        crudo = request.read()
        tipo = (request.content_type or '').lower()
        contexto = _contexto('PET', request.method, request.path)

        if tipo == 'application/json':
            try:
                sobre = json.loads(crudo)
            except ValueError:
                sobre = None
            if not es_sobre(sobre):
                raise _SinCifrar()
            claro, tipo_real = descifrar(clave, sobre['datos'], contexto), None
        elif tipo == 'application/octet-stream':
            tipo_real, claro = descifrar_binario(clave, crudo, contexto)
        else:
            raise _SinCifrar()          # formulario u otro contenido en claro

        # La vista (DRF) lee el cuerpo ya descifrado como si hubiera llegado así.
        request._body = claro
        request.META['CONTENT_LENGTH'] = str(len(claro))
        if tipo_real:
            request.META['CONTENT_TYPE'] = tipo_real   # p. ej. multipart/form-data; boundary=...

    # ── Respuesta ─────────────────────────────────────────────────────────

    @staticmethod
    def _cifrar_respuesta(request, response, clave: bytes):
        if getattr(response, 'streaming', False):
            # Archivos servidos por trozos: se juntan para poder cifrarlos.
            contenido = b''.join(response.streaming_content)
            nueva = HttpResponse(contenido, status=response.status_code)
            for nombre, valor in response.items():
                if nombre.lower() != 'content-length':
                    nueva[nombre] = valor
            if hasattr(response, 'close'):
                response.close()
            response = nueva
        if not response.content:
            return response

        contexto = _contexto('RES', request.method, request.path)
        tipo = response.get('Content-Type', '')
        if tipo.lower().startswith('application/json'):
            datos = cifrar(clave, response.content, contexto)
            response.content = json.dumps({'cifrado': VERSION, 'datos': datos}).encode()
            response['Content-Type'] = 'application/json'
        else:
            # Descargas (PDF, Excel...): el tipo real viaja dentro del cifrado.
            response.content = cifrar_binario(clave, tipo, response.content, contexto)
            response['Content-Type'] = 'application/octet-stream'
        response['Content-Length'] = str(len(response.content))
        return response


class _Demasiado(Exception):
    """El cuerpo supera settings.CIFRADO_MAX_BYTES."""


# ── Vista: datos que el navegador necesita para abrir el canal ───────────────

class CanalView(APIView):
    """GET /api/auth/canal/ → clave pública del servidor y rutas cifradas.

    Es pública y nunca va cifrada: es lo primero que pide el navegador. La
    lista de rutas vive solo aquí (settings.CIFRADO_RUTAS); el frontend la lee
    de esta respuesta, así que activar un módulo no requiere tocar el frontend."""
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        # Solo lo que el navegador necesita para cifrar. Nada de esto es
        # secreto: la clave es la pública y las rutas se ven en cada petición.
        activo = cifrado_activo()
        return Response({
            'activo': activo,
            'clave_publica': base64.b64encode(clave_publica_servidor()).decode(),
            'rutas': rutas_cifradas(),
            'publicas': rutas_publicas() if activo else [],
        })