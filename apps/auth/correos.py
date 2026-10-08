"""
Correos que envía el módulo de autenticación: bienvenida al crear un usuario
y código para restablecer la contraseña.

Cada correo va en HTML (plantillas en templates/chem_auth/correos/) con una
versión en texto plano para los clientes que no muestran HTML. Las funciones
devuelven True/False y nunca lanzan excepción: un fallo del servidor de correo
no debe tumbar la creación de un usuario.
"""
import logging
from email.mime.image import MIMEImage
from pathlib import Path

import django
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)

EMPRESA = 'Comercial Chemical S.A.S.'
SISTEMA = 'CHEM-MODINV'
LOGO = Path(__file__).resolve().parent / 'recursos' / 'logo_correo.png'

NOMBRES_ROL = {
    'Administrador': 'Administrador',
    'Analista_Inventarios': 'Analista de Inventarios',
    'Responsable_Compras': 'Responsable de Compras',
}


def _se_imprime_en_consola() -> bool:
    return settings.EMAIL_BACKEND.endswith('console.EmailBackend')


class _CorreoConLogo(EmailMultiAlternatives):
    """Correo HTML con el logo incrustado (se referencia como cid:logo).

    El logo viaja dentro del correo y no como enlace, así se ve aunque el
    sistema corra en una red interna. Django cambió por dentro la forma de
    armar los correos en la versión 6, por eso hay dos caminos."""
    logo: bytes | None = None

    def message(self, *args, **kwargs):
        mensaje = super().message(*args, **kwargs)
        if self.logo and hasattr(mensaje, 'get_body'):
            # Django 6 o superior: la imagen se cuelga de la parte HTML.
            html = mensaje.get_body(preferencelist=('html',))
            if html is not None:
                html.add_related(
                    self.logo, maintype='image', subtype='png',
                    cid='<logo>', disposition='inline', filename='logo.png',
                )
        return mensaje

    def incrustar_logo(self, contenido: bytes) -> None:
        self.logo = contenido
        if django.VERSION < (6, 0):
            # Django 5: se adjunta la imagen y se marca el correo como "related".
            self.mixed_subtype = 'related'
            imagen = MIMEImage(contenido)
            imagen.add_header('Content-ID', '<logo>')
            imagen.add_header('Content-Disposition', 'inline', filename='logo.png')
            self.attach(imagen)


def _enviar(asunto: str, destinatario: str, plantilla: str, contexto: dict, texto: str) -> bool:
    # En desarrollo (correo a la consola) no se adjunta el logo: llenaría la
    # terminal de texto codificado y costaría encontrar el código.
    con_logo = LOGO.exists() and not _se_imprime_en_consola()
    html = render_to_string(f'chem_auth/correos/{plantilla}.html', {
        **contexto,
        'asunto': asunto,
        'con_logo': con_logo,
        'empresa': EMPRESA,
        'sistema': SISTEMA,
        'url_sistema': settings.FRONTEND_URL,
    })
    try:
        mensaje = _CorreoConLogo(asunto, texto, settings.DEFAULT_FROM_EMAIL, [destinatario])
        mensaje.attach_alternative(html, 'text/html')
        if con_logo:
            mensaje.incrustar_logo(LOGO.read_bytes())
        mensaje.send()
        return True
    except Exception:
        logger.exception('No se pudo enviar el correo "%s" a %s', asunto, destinatario)
        return False


def enviar_bienvenida(usuario) -> bool:
    """Correo que recibe un usuario cuando el Administrador crea su cuenta.
    No incluye la contraseña: un correo no es un lugar seguro para enviarla."""
    rol = NOMBRES_ROL.get(usuario.rol.nombre, usuario.rol.nombre)
    url_ingreso = f'{settings.FRONTEND_URL}/login'
    texto = (
        f'Hola, {usuario.nombre_completo}:\n\n'
        f'Se creó tu cuenta en {SISTEMA}, el sistema de inventarios de {EMPRESA}\n\n'
        f'Usuario: {usuario.email}\n'
        f'Rol: {rol}\n\n'
        f'Ingresa aquí: {url_ingreso}\n\n'
        'El administrador te entregará tu contraseña inicial. Si prefieres '
        'crear la tuya, usa "¿Olvidaste tu contraseña?" en la pantalla de '
        'ingreso y te llegará un código a este correo.\n'
    )
    return _enviar(
        f'Bienvenido a {SISTEMA}', usuario.email, 'bienvenida',
        {'nombre': usuario.nombre_completo, 'correo': usuario.email, 'rol': rol,
         'url_ingreso': url_ingreso},
        texto,
    )


def enviar_codigo_recuperacion(usuario, codigo: str, minutos: int,
                               solicitado_por_admin: bool = False) -> bool:
    """Correo con el código de un solo uso para restablecer la contraseña."""
    from urllib.parse import quote
    url_restablecer = f'{settings.FRONTEND_URL}/recuperar?correo={quote(usuario.email)}'
    motivo = (
        'Un administrador solicitó restablecer la contraseña de tu cuenta.'
        if solicitado_por_admin
        else 'Recibimos una solicitud para restablecer la contraseña de tu cuenta.'
    )
    texto = (
        f'Hola, {usuario.nombre_completo}:\n\n'
        f'{motivo}\n\n'
        f'Tu código es: {codigo}\n\n'
        f'Vence en {minutos} minutos y solo se puede usar una vez.\n'
        f'Escríbelo aquí: {url_restablecer}\n\n'
        'Si no esperabas este correo, ignóralo: tu contraseña sigue siendo la misma.\n'
    )
    return _enviar(
        f'{codigo} es tu código para restablecer la contraseña', usuario.email, 'codigo_recuperacion',
        {'nombre': usuario.nombre_completo, 'codigo': codigo, 'minutos': minutos,
         'motivo': motivo, 'url_restablecer': url_restablecer},
        texto,
    )