"""Servicio de auditoría — equivalente al audit_client.py de Chem Movid."""
from .models import EventoAuditoria


def registrar_evento(
    tipo_accion: str,
    descripcion: str,
    usuario_id=None,
    entidad: str = None,
    entidad_id: int = None,
    datos_anteriores=None,
    datos_nuevos=None,
    ip: str = None,
) -> EventoAuditoria:
    """
    Registra un evento de auditoría en la base de datos.
    Compatible con la firma de registrar_evento() del Chem Movid.
    """
    try:
        return EventoAuditoria.objects.create(
            tipo_accion=tipo_accion,
            descripcion=descripcion,
            usuario_id=usuario_id,
            entidad_afectada=entidad,
            entidad_id=entidad_id,
            datos_anteriores=datos_anteriores,
            datos_nuevos=datos_nuevos,
            ip_cliente=ip,
        )
    except Exception:
        # Nunca fallar por auditoría — log silencioso
        return None
