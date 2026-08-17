"""Genera el PDF de una Orden de Compra para enviar al proveedor."""
import io
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image,
)

from .models import Proveedor

AZUL = colors.HexColor('#1a237e')
GRIS = colors.HexColor('#616161')
GRIS_CLARO = colors.HexColor('#f5f5f5')

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')

ESTADO_LABELS = {
    'pendiente': 'Pendiente',
    'aprobada': 'Aprobada',
    'rechazada': 'Rechazada',
    'recibida': 'Recibida',
}


def _fmt_cantidad(valor) -> str:
    if valor is None:
        return '—'
    numero = float(valor)
    return f"{numero:,.0f}" if numero == int(numero) else f"{numero:,.3f}"


def generar_pdf_orden(orden) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    estilo_normal = ParagraphStyle('normal', parent=styles['Normal'], fontSize=9, leading=13)
    estilo_label = ParagraphStyle('label', parent=estilo_normal, textColor=GRIS, fontSize=8)
    estilo_titulo = ParagraphStyle(
        'titulo', parent=styles['Heading1'], fontSize=15, textColor=AZUL, spaceAfter=0,
    )
    estilo_subtitulo = ParagraphStyle(
        'subtitulo', parent=estilo_normal, textColor=GRIS, fontSize=10,
    )
    estilo_numero = ParagraphStyle(
        'numero', parent=styles['Heading2'], fontSize=13, alignment=TA_RIGHT, textColor=AZUL, spaceAfter=0,
    )
    estilo_estado = ParagraphStyle(
        'estado', parent=estilo_normal, alignment=TA_RIGHT, textColor=GRIS,
    )
    estilo_solicitud = ParagraphStyle(
        'solicitud', parent=estilo_normal, alignment=TA_JUSTIFY, fontSize=10, leading=15,
    )

    elementos = []

    # ── Encabezado (logo + nombre a la izquierda, número de orden a la derecha) ──
    logo_img = Image(LOGO_PATH, width=1.8 * cm, height=1.8 * cm)
    nombre_empresa = [
        Paragraph('COMERCIAL CHEMICAL S.A.S.', estilo_titulo),
    ]
    encabezado = Table(
        [[logo_img, nombre_empresa, Paragraph(f"ORDEN DE COMPRA<br/>{orden.numero_orden}", estilo_numero)]],
        colWidths=[2.2 * cm, 8.7 * cm, 6.5 * cm],
    )
    encabezado.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elementos.append(encabezado)
    elementos.append(Spacer(1, 0.4 * cm))
    elementos.append(HRFlowable(width='100%', thickness=1.2, color=AZUL))
    elementos.append(Spacer(1, 0.6 * cm))

    # ── Enunciado de solicitud ───────────────────────────────────────────
    unidad = orden.articulo.unidad_medida or 'unidades'
    elementos.append(Paragraph(
        f"Por medio de la presente, solicito la emisión de una orden de compra para la "
        f"adquisición de <b>{orden.articulo.descripcion}</b>, en la cantidad de "
        f"<b>{_fmt_cantidad(float(orden.cantidad_solicitada))} {unidad}</b>, con el fin de atender "
        f"las necesidades operativas del área. Agradezco su gestión y aprobación para dar "
        f"continuidad al proceso de compra.",
        estilo_solicitud,
    ))
    elementos.append(Spacer(1, 0.6 * cm))

    # ── Datos de la orden / proveedor ───────────────────────────────────
    proveedor_obj = None
    if orden.proveedor:
        proveedor_obj = Proveedor.objects.filter(nombre__iexact=orden.proveedor.strip()).first()

    lineas_proveedor = [f"<b>{orden.proveedor or 'Sin proveedor asignado'}</b>"]
    if proveedor_obj:
        if proveedor_obj.nit:
            lineas_proveedor.append(f"NIT: {proveedor_obj.nit}")
        if proveedor_obj.contacto:
            lineas_proveedor.append(f"Contacto: {proveedor_obj.contacto}")
        if proveedor_obj.telefono:
            lineas_proveedor.append(f"Tel: {proveedor_obj.telefono}")
        if proveedor_obj.email:
            lineas_proveedor.append(f"Email: {proveedor_obj.email}")

    bloque_proveedor = [
        Paragraph('PROVEEDOR', estilo_label),
        Paragraph('<br/>'.join(lineas_proveedor), estilo_normal),
    ]
    bloque_datos = [
        Paragraph('DATOS DE LA ORDEN', estilo_label),
        Paragraph(
            f"Fecha: {orden.fecha_creacion.strftime('%d/%m/%Y')}<br/>"
            f"Estado: <b>{ESTADO_LABELS.get(orden.estado, orden.estado)}</b><br/>"
            f"Última modificación: {orden.fecha_modificacion.strftime('%d/%m/%Y')}",
            estilo_normal,
        ),
    ]

    info_table = Table(
        [[bloque_proveedor, bloque_datos]],
        colWidths=[8.7 * cm, 8.7 * cm],
    )
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, -1), GRIS_CLARO),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elementos.append(info_table)
    elementos.append(Spacer(1, 0.7 * cm))

    # ── Observaciones ────────────────────────────────────────────────────
    if orden.observaciones:
        elementos.append(Paragraph('OBSERVACIONES', estilo_label))
        elementos.append(Spacer(1, 0.15 * cm))
        elementos.append(Paragraph(orden.observaciones, estilo_normal))
        elementos.append(Spacer(1, 0.6 * cm))

    # ── Condiciones (cláusulas estándar de entrega, calidad, facturación, garantía) ──
    elementos.append(Paragraph('CONDICIONES', estilo_label))
    elementos.append(Spacer(1, 0.15 * cm))
    elementos.append(Paragraph(
        'Los productos deberán entregarse en perfectas condiciones, con su empaque '
        'original y dentro del plazo acordado. La recepción estará sujeta a '
        'verificación de cantidad y calidad. La factura deberá hacer referencia al '
        'número de la orden de compra y respetar las condiciones de pago establecidas. '
        'Cualquier modificación a la presente orden deberá contar con autorización '
        'previa por escrito.',
        estilo_normal,
    ))
    elementos.append(Spacer(1, 1 * cm))

    # ── Firmas ───────────────────────────────────────────────────────────
    elementos.append(Spacer(1, 1.2 * cm))
    firmas = Table(
        [
            ['_______________________________', '_______________________________'],
            ['Elaborado por', 'Aprobado por'],
        ],
        colWidths=[8.7 * cm, 8.7 * cm],
    )
    firmas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TEXTCOLOR', (0, 1), (-1, 1), GRIS),
        ('FONTSIZE', (0, 1), (-1, 1), 8.5),
        ('TOPPADDING', (0, 1), (-1, 1), 4),
    ]))
    elementos.append(firmas)

    doc.build(elementos)
    return buffer.getvalue()