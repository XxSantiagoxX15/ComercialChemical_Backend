"""
Recuperación de contraseña con un código de un solo uso enviado al correo.

Reglas:
- El código tiene 6 dígitos y vence a los PASSWORD_RESET_CODE_MINUTES minutos.
- En la base solo se guarda su huella (HMAC), nunca el código.
- Cada usuario tiene como mucho un código vigente: pedir otro anula el anterior.
- Tras MAX_INTENTOS intentos fallidos el código deja de servir.
- No se envía más de un código por minuto al mismo usuario.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import CodigoRecuperacion

LONGITUD_CODIGO = 6
MAX_INTENTOS = 5
SEGUNDOS_ENTRE_ENVIOS = 60


def minutos_validez() -> int:
    return int(getattr(settings, 'PASSWORD_RESET_CODE_MINUTES', 15))


def _huella(usuario_id: int, codigo: str) -> str:
    mensaje = f'{usuario_id}:{codigo}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), mensaje, hashlib.sha256).hexdigest()


def segundos_para_reenviar(usuario) -> int:
    """Segundos que faltan para poder enviarle otro código (0 = ya se puede)."""
    ultimo = CodigoRecuperacion.objects.filter(usuario=usuario).order_by('-fecha_creacion').first()
    if ultimo is None:
        return 0
    transcurridos = (timezone.now() - ultimo.fecha_creacion).total_seconds()
    return max(0, int(SEGUNDOS_ENTRE_ENVIOS - transcurridos))


def generar_codigo(usuario) -> str:
    """Crea un código nuevo para el usuario (anulando los anteriores) y lo
    devuelve en claro para enviarlo por correo."""
    codigo = f'{secrets.randbelow(10 ** LONGITUD_CODIGO):0{LONGITUD_CODIGO}d}'
    with transaction.atomic():
        CodigoRecuperacion.objects.filter(usuario=usuario).delete()
        CodigoRecuperacion.objects.create(
            usuario=usuario,
            codigo_hash=_huella(usuario.id, codigo),
            fecha_expiracion=timezone.now() + timedelta(minutes=minutos_validez()),
        )
    return codigo


def descartar_codigos(usuario) -> None:
    """Borra los códigos del usuario (p. ej. si el correo no se pudo enviar)."""
    CodigoRecuperacion.objects.filter(usuario=usuario).delete()


def consumir_codigo(usuario, codigo: str) -> bool:
    """True si `codigo` es el código vigente del usuario; en ese caso queda
    usado y no vuelve a servir. Cada intento fallido se cuenta."""
    with transaction.atomic():
        registro = (
            CodigoRecuperacion.objects.select_for_update()
            .filter(usuario=usuario, usado=False)
            .order_by('-fecha_creacion')
            .first()
        )
        if registro is None:
            return False
        if registro.fecha_expiracion <= timezone.now() or registro.intentos >= MAX_INTENTOS:
            return False
        if not hmac.compare_digest(registro.codigo_hash, _huella(usuario.id, codigo)):
            registro.intentos += 1
            registro.save(update_fields=['intentos'])
            return False
        registro.usado = True
        registro.save(update_fields=['usado'])
        return True