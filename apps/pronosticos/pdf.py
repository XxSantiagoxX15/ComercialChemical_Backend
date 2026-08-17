"""Genera el PDF del reporte de necesidades de compra para un mes futuro pronosticado."""
import io
import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable, Image,
)

AZUL = colors.HexColor('#1a237e')
GRIS = colors.HexColor('#616161')
GRIS_CLARO = colors.HexColor('#f5f5f5')

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')

MESES_ES = {
    1: 'Enero', 2: 'Febrero', 3: 'Marzo', 4: 'Abril', 5: 'Mayo', 6: 'Junio',
    7: 'Julio', 8: 'Agosto', 9: 'Septiembre', 10: 'Octubre', 11: 'Noviembre', 12: 'Diciembre',
}


def _fmt_cantidad(valor) -> str:
    numero = float(valor)
    return f"{numero:,.0f}" if numero == int(numero) else f"{numero:,.3f}"


def _nombre_mes(mes: str) -> str:
    anio, mes_num = (int(x) for x in mes.split('-'))
    return f"{MESES_ES.get(mes_num, mes_num)} {anio}"


def _doc_base():
    """Documento y set de estilos comunes a todos los PDFs de este módulo."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
        leftMargin=1.8 * cm, rightMargin=1.8 * cm,
    )
    styles = getSampleStyleSheet()
    estilos = {
        'normal': ParagraphStyle('normal', parent=styles['Normal'], fontSize=9, leading=13),
        'titulo': ParagraphStyle('titulo', parent=styles['Heading1'], fontSize=15, textColor=AZUL, spaceAfter=0),
        'numero': ParagraphStyle('numero', parent=styles['Heading2'], fontSize=13, alignment=TA_RIGHT, textColor=AZUL, spaceAfter=0),
    }
    estilos['celda'] = ParagraphStyle('celda', parent=estilos['normal'], fontSize=8.5, leading=11)
    estilos['resumen'] = ParagraphStyle('resumen', parent=estilos['normal'], alignment=1, fontSize=10, leading=14)
    return buffer, doc, estilos


def _encabezado(estilos: dict, titulo_derecha: str) -> Table:
    """Logo + nombre de la empresa a la izquierda, título del reporte a la derecha."""
    logo_img = Image(LOGO_PATH, width=1.8 * cm, height=1.8 * cm)
    nombre_empresa = [Paragraph('COMERCIAL CHEMICAL S.A.S.', estilos['titulo'])]
    encabezado = Table(
        [[logo_img, nombre_empresa, Paragraph(titulo_derecha, estilos['numero'])]],
        colWidths=[2.2 * cm, 8.7 * cm, 6.5 * cm],
    )
    encabezado.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return encabezado


def generar_pdf_reporte_necesidades(reporte: dict) -> bytes:
    buffer, doc, estilos = _doc_base()
    estilo_normal = estilos['normal']
    estilo_celda = estilos['celda']
    estilo_resumen = estilos['resumen']

    elementos = []
    mes_label = _nombre_mes(reporte['mes'])

    # ── Encabezado ────────────────────────────────────────────────────────
    elementos.append(_encabezado(estilos, f"REPORTE DE NECESIDADES<br/>{mes_label}"))
    elementos.append(Spacer(1, 0.4 * cm))
    elementos.append(HRFlowable(width='100%', thickness=1.2, color=AZUL))
    elementos.append(Spacer(1, 0.5 * cm))

    elementos.append(Paragraph(
        f"Proyección de necesidades de compra para <b>{mes_label}</b>, basada en el "
        f"pronóstico de demanda por artículo (modelo seleccionado automáticamente por "
        f"menor error de backtesting) y el stock que se estima disponible después de "
        f"cubrir la demanda proyectada de los meses anteriores dentro del mismo horizonte.",
        estilo_normal,
    ))
    elementos.append(Spacer(1, 0.5 * cm))

    items = [it for it in reporte['items'] if it['cantidad_a_comprar'] > 0]

    # ── Resumen ───────────────────────────────────────────────────────────
    resumen = Table(
        [[
            Paragraph(f"<b>{reporte['total_articulos']}</b><br/>artículos con pronóstico", estilo_resumen),
            Paragraph(f"<b>{len(items)}</b><br/>requieren compra", estilo_resumen),
        ]],
        colWidths=[8.7 * cm, 8.7 * cm],
    )
    resumen.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GRIS_CLARO),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elementos.append(resumen)
    elementos.append(Spacer(1, 0.6 * cm))

    # ── Tabla de artículos a comprar ─────────────────────────────────────
    if items:
        filas = [['Código', 'Descripción', 'Demanda Proy.', 'Stock Estimado', 'Comprar']]
        for it in items:
            filas.append([
                it['codigo'],
                Paragraph(it['descripcion'], estilo_celda),
                _fmt_cantidad(it['demanda_proyectada']),
                _fmt_cantidad(it['stock_disponible_estimado']),
                _fmt_cantidad(it['cantidad_a_comprar']),
            ])
        tabla = Table(filas, colWidths=[2.2 * cm, 7.3 * cm, 2.6 * cm, 2.6 * cm, 2.5 * cm], repeatRows=1)
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elementos.append(tabla)
    else:
        elementos.append(Paragraph('No hay artículos que requieran compra para este mes.', estilo_normal))

    doc.build(elementos)
    return buffer.getvalue()


def generar_pdf_mas_vendidos(datos: list[dict]) -> bytes:
    """PDF del top de artículos más vendidos (histórico completo del Kardex)."""
    buffer, doc, estilos = _doc_base()
    estilo_normal = estilos['normal']
    estilo_celda = estilos['celda']

    elementos = [
        _encabezado(estilos, 'PRODUCTOS MÁS<br/>VENDIDOS'),
        Spacer(1, 0.4 * cm),
        HRFlowable(width='100%', thickness=1.2, color=AZUL),
        Spacer(1, 0.5 * cm),
        Paragraph(
            f"Ranking de los <b>{len(datos)}</b> artículos con mayor volumen de unidades "
            f"vendidas, sumando todo el histórico de ventas registrado en el Kardex.",
            estilo_normal,
        ),
        Spacer(1, 0.5 * cm),
    ]

    if datos:
        filas = [['#', 'Código', 'Descripción', 'Total Vendido']]
        for i, it in enumerate(datos, start=1):
            filas.append([
                str(i),
                it['codigo'] or '—',
                Paragraph(it['descripcion'] or '—', estilo_celda),
                _fmt_cantidad(it['total_vendido']),
            ])
        tabla = Table(filas, colWidths=[1.2 * cm, 2.6 * cm, 9.4 * cm, 3.4 * cm], repeatRows=1)
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ALIGN', (3, 0), (3, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elementos.append(tabla)
    else:
        elementos.append(Paragraph('No hay ventas registradas todavía.', estilo_normal))

    doc.build(elementos)
    return buffer.getvalue()


def generar_pdf_comparacion(reporte: dict) -> bytes:
    """PDF de la comparación de ventas entre dos periodos (total y por artículo)."""
    buffer, doc, estilos = _doc_base()
    estilo_normal = estilos['normal']
    estilo_celda = estilos['celda']
    estilo_resumen = estilos['resumen']

    p1, p2 = reporte['periodo_1'], reporte['periodo_2']
    var_uds = reporte['variacion_pct_unidades']
    var_txt = f"{'+' if var_uds and var_uds > 0 else ''}{var_uds}%" if var_uds is not None else 'N/D'

    elementos = [
        _encabezado(estilos, 'COMPARACIÓN DE<br/>VENTAS POR PERIODO'),
        Spacer(1, 0.4 * cm),
        HRFlowable(width='100%', thickness=1.2, color=AZUL),
        Spacer(1, 0.5 * cm),
        Paragraph(
            f"Comparación de unidades y valor vendido entre el <b>Periodo 1</b> "
            f"({p1['desde']} a {p1['hasta']}) y el <b>Periodo 2</b> ({p2['desde']} a {p2['hasta']}), "
            f"con base en los registros de venta del Kardex.",
            estilo_normal,
        ),
        Spacer(1, 0.5 * cm),
    ]

    resumen = Table(
        [[
            Paragraph(f"<b>Periodo 1</b><br/>{p1['desde']} a {p1['hasta']}<br/>{_fmt_cantidad(p1['unidades'])} uds", estilo_resumen),
            Paragraph(f"<b>Variación</b><br/>{var_txt}", estilo_resumen),
            Paragraph(f"<b>Periodo 2</b><br/>{p2['desde']} a {p2['hasta']}<br/>{_fmt_cantidad(p2['unidades'])} uds", estilo_resumen),
        ]],
        colWidths=[5.8 * cm, 5.8 * cm, 5.8 * cm],
    )
    resumen.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GRIS_CLARO),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elementos.append(resumen)
    elementos.append(Spacer(1, 0.6 * cm))

    detalle = reporte.get('detalle', [])
    if detalle:
        filas = [['Código', 'Descripción', 'Periodo 1', 'Periodo 2', 'Variación']]
        for it in detalle:
            var = it['variacion_pct']
            var_str = f"{'+' if var and var > 0 else ''}{var}%" if var is not None else 'N/D'
            filas.append([
                it['codigo'],
                Paragraph(it['descripcion'], estilo_celda),
                _fmt_cantidad(it['unidades_periodo_1']),
                _fmt_cantidad(it['unidades_periodo_2']),
                var_str,
            ])
        tabla = Table(filas, colWidths=[2.2 * cm, 7.3 * cm, 2.6 * cm, 2.6 * cm, 2.5 * cm], repeatRows=1)
        tabla.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, GRIS_CLARO]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dddddd')),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elementos.append(tabla)
    else:
        elementos.append(Paragraph('No hay ventas registradas en ninguno de los dos periodos.', estilo_normal))

    doc.build(elementos)
    return buffer.getvalue()