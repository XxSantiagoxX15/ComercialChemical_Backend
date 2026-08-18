"""
Reconstruye el módulo de Kits (Kit + KitComponente) a partir de las
transacciones "CREACION DE KITS DE PRODUCTOS" del Informe Kardex.

Cada documento (DOC No) que agrupa un artículo con ENTRADA (el producto
resultante, p.ej. "OXIGENO ACTIVO LIBRA") y uno o más artículos con SALIDA
(los componentes consumidos, p.ej. "OXIGENO ACTIVO KILO") es un lote de
producción real. Se usa la MEDIANA de la proporción componente/producto
entre todos los lotes históricos de cada receta (más robusta que el
promedio ante datos ruidosos) — se incluyen todas las recetas encontradas,
incluso las que varían bastante entre lotes.
"""
import statistics
import unicodedata
from collections import defaultdict

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.inventario.models import Articulo, Kit, KitComponente


def _norm(s) -> str:
    return ''.join(
        c for c in unicodedata.normalize('NFD', str(s))
        if unicodedata.category(c) != 'Mn'
    ).upper().strip()


class Command(BaseCommand):
    help = 'Reconstruye Kits/KitComponente a partir del Informe Kardex (transacciones de creación de kits).'

    def add_arguments(self, parser):
        parser.add_argument(
            'archivo', nargs='?',
            default='Archivos Importados/Informe Kardex.xlsx',
            help='Ruta al archivo Informe Kardex.xlsx',
        )

    def handle(self, *args, **options):
        ruta = options['archivo']
        try:
            df = pd.read_excel(ruta, header=None, engine='openpyxl')
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'No se pudo leer {ruta}: {e}'))
            return

        header_idx = None
        for i in range(min(15, len(df))):
            vals = {_norm(v) for v in df.iloc[i] if pd.notna(v) and str(v).strip()}
            if {'FECHA', 'CONCEPTO', 'SALDO'} <= vals:
                header_idx = i
                break
        if header_idx is None:
            self.stderr.write(self.style.ERROR('No se encontró el encabezado del Kardex.'))
            return

        codigo_actual = None
        grupos = defaultdict(list)

        for i in range(header_idx + 1, len(df)):
            row = df.iloc[i]
            col0, col1, col2, col3 = row[0], row[1], row[2], row[3]
            col0_str = str(col0).strip() if pd.notna(col0) else ''
            col0_norm = _norm(col0_str)

            if not col0_str and pd.notna(col1) and pd.notna(col2):
                codigo_raw = str(col1).strip()
                try:
                    codigo_actual = str(int(float(codigo_raw)))
                except (ValueError, TypeError):
                    codigo_actual = codigo_raw
                continue

            if any(col0_norm.startswith(p) for p in
                   ('SUBTOTAL', 'TOTAL', 'BODEGA', 'GRUPO', 'INVENTARIOS')):
                continue
            if not codigo_actual or not col0_str:
                continue

            concepto = str(col3).strip() if pd.notna(col3) else ''
            if 'KIT' not in _norm(concepto):
                continue

            doc_no = str(row[2]).strip()
            entradas = float(row[4]) if pd.notna(row[4]) else 0.0
            salidas = float(row[5]) if pd.notna(row[5]) else 0.0
            grupos[doc_no].append({'codigo': codigo_actual, 'entradas': entradas, 'salidas': salidas})

        # ── Construir lotes de producción: kit_codigo -> [(cantidad_producida, {componente: cantidad_usada})] ──
        kit_batches = defaultdict(list)
        for items in grupos.values():
            entradas = [it for it in items if it['entradas'] > 0]
            salidas = [it for it in items if it['salidas'] > 0]
            codigos_entrada = {it['codigo'] for it in entradas}
            codigos_salida = {it['codigo'] for it in salidas}
            # Descartar auto-referencias (mismo artículo con entrada Y salida en el mismo doc)
            if codigos_entrada & codigos_salida:
                continue
            # Solo nos sirven documentos con exactamente UN artículo resultante
            if len(codigos_entrada) != 1 or not salidas:
                continue
            kit_codigo = entradas[0]['codigo']
            entrada_qty = sum(it['entradas'] for it in entradas)
            if entrada_qty <= 0:
                continue
            componentes = {}
            for s in salidas:
                componentes[s['codigo']] = componentes.get(s['codigo'], 0) + s['salidas']
            kit_batches[kit_codigo].append((entrada_qty, componentes))

        self.stdout.write(f'Recetas candidatas encontradas en el Kardex: {len(kit_batches)}')

        creados, actualizados, omitidos_sin_articulo = 0, 0, 0
        componentes_creados = 0

        with transaction.atomic():
            for kit_codigo, batches in kit_batches.items():
                try:
                    articulo_kit = Articulo.objects.get(codigo=kit_codigo)
                except Articulo.DoesNotExist:
                    omitidos_sin_articulo += 1
                    continue

                ratios_por_componente = defaultdict(list)
                for entrada_qty, componentes in batches:
                    for comp_codigo, cant in componentes.items():
                        ratios_por_componente[comp_codigo].append(cant / entrada_qty)

                kit, kit_creado = Kit.objects.get_or_create(
                    codigo=kit_codigo,
                    defaults={'descripcion': articulo_kit.descripcion},
                )
                if not kit_creado:
                    kit.descripcion = articulo_kit.descripcion
                    kit.activo = True
                    kit.save(update_fields=['descripcion', 'activo'])
                creados += 1 if kit_creado else 0
                actualizados += 0 if kit_creado else 1

                if articulo_kit.tipo_producto != 'kit':
                    articulo_kit.tipo_producto = 'kit'
                    articulo_kit.save(update_fields=['tipo_producto'])

                for comp_codigo, ratios in ratios_por_componente.items():
                    if comp_codigo == kit_codigo:
                        continue
                    try:
                        articulo_comp = Articulo.objects.get(codigo=comp_codigo)
                    except Articulo.DoesNotExist:
                        continue
                    mediana = statistics.median(ratios)
                    if mediana <= 0:
                        continue
                    _, comp_creado = KitComponente.objects.update_or_create(
                        kit=kit, articulo=articulo_comp,
                        defaults={'cantidad_requerida': round(mediana, 4)},
                    )
                    if comp_creado:
                        componentes_creados += 1

        self.stdout.write(self.style.SUCCESS(
            f'Kits creados: {creados} | actualizados: {actualizados} | '
            f'omitidos (artículo no existe): {omitidos_sin_articulo} | '
            f'componentes escritos: {componentes_creados}'
        ))