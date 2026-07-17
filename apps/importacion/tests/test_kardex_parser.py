"""
Pruebas del parser jerárquico del Informe Kardex (_procesar_kardex).

En vez de subir un .xlsx real, se construye directamente el DataFrame "crudo"
(header=None) tal como lo entrega pandas.read_excel, para probar la lógica de
parseo de forma aislada y rápida.
"""
import numpy as np
import pandas as pd
from django.test import TestCase

from apps.importacion.views import ImportacionUploadView
from apps.inventario.models import Articulo
from apps.ventas.models import VentaHistorico, SaldoHistorico

NA = np.nan


class ProcesarKardexTestCase(TestCase):
    def setUp(self):
        self.view = ImportacionUploadView()

    def _build_df(self, filas):
        return pd.DataFrame(filas)

    def test_parsea_ventas_y_saldos_ignorando_filas_estructurales(self):
        filas = [
            ['FECHA', 'PR', 'DOC No', 'CONCEPTO', 'ENTRADAS', 'SALIDAS', 'SALDO', 'VALOR', 'COSTO UNITARIO'],
            ['BODEGA PRINCIPAL', NA, NA, NA, NA, NA, NA, NA, NA],
            ['GRUPO 01', NA, NA, NA, NA, NA, NA, NA, NA],
            [NA, '1011.0', 'PRODUCTO UNO UNI', NA, NA, NA, NA, NA, NA],
            [pd.Timestamp('2026-01-15'), 'CI', 'DOC1', 'FACTURA A CLIENTES', NA, 10, 90, -50000, 5000],
            [pd.Timestamp('2026-01-20'), 'CI', 'DOC2', 'AJUSTE INVENTARIO', NA, 0, 85, NA, NA],
            ['SUBTOTAL ARTICULO', NA, NA, NA, NA, NA, NA, NA, NA],
            [NA, '2022', 'PRODUCTO DOS UNI', NA, NA, NA, NA, NA, NA],
            [pd.Timestamp('2026-01-18'), 'CI', 'DOC3', 'REMISION A CLIENTES', NA, 5, 45, -20000, 4000],
            ['TOTAL GENERAL', NA, NA, NA, NA, NA, NA, NA, NA],
        ]
        df = self._build_df(filas)

        procesados, errores = self.view._procesar_kardex(df)

        self.assertEqual(errores, [])
        self.assertEqual(procesados, 2)  # solo las 2 filas con concepto de venta

        # El código '1011.0' se limpia a '1011' y el sufijo ' UNI' se recorta del nombre.
        art1 = Articulo.objects.get(codigo='1011')
        self.assertEqual(art1.descripcion, 'PRODUCTO UNO')
        art2 = Articulo.objects.get(codigo='2022')

        ventas = list(VentaHistorico.objects.order_by('fecha'))
        self.assertEqual(len(ventas), 2)  # la fila 'AJUSTE INVENTARIO' no genera venta
        self.assertEqual(ventas[0].articulo_id, art1.id)
        self.assertEqual(float(ventas[0].cantidad_vendida), 10)
        self.assertEqual(float(ventas[0].valor_total), 50000)  # se guarda en valor absoluto
        self.assertEqual(ventas[1].articulo_id, art2.id)
        self.assertEqual(float(ventas[1].cantidad_vendida), 5)

        # El SALDO se guarda para TODAS las transacciones, sin importar el concepto.
        saldos = list(SaldoHistorico.objects.order_by('fecha'))
        self.assertEqual(len(saldos), 3)
        self.assertEqual(float(saldos[0].saldo), 90)   # venta
        self.assertEqual(float(saldos[1].saldo), 45)   # remisión del segundo artículo
        self.assertEqual(float(saldos[2].saldo), 85)   # ajuste (no es venta, sí saldo)

    def test_fila_sin_articulo_en_contexto_se_ignora_sin_error(self):
        filas = [
            ['FECHA', 'PR', 'DOC No', 'CONCEPTO', 'ENTRADAS', 'SALIDAS', 'SALDO', 'VALOR', 'COSTO UNITARIO'],
            # transacción antes de que aparezca ningún encabezado de artículo
            [pd.Timestamp('2026-01-15'), 'CI', 'DOC1', 'FACTURA A CLIENTES', NA, 10, 90, -50000, 5000],
        ]
        df = self._build_df(filas)

        procesados, errores = self.view._procesar_kardex(df)

        self.assertEqual(procesados, 0)
        self.assertEqual(errores, [])
        self.assertEqual(VentaHistorico.objects.count(), 0)
        self.assertEqual(SaldoHistorico.objects.count(), 0)

    def test_articulo_existente_se_reutiliza_en_vez_de_duplicarse(self):
        Articulo.objects.create(codigo='1011', descripcion='PRODUCTO UNO', unidad_medida='UNI')
        filas = [
            ['FECHA', 'PR', 'DOC No', 'CONCEPTO', 'ENTRADAS', 'SALIDAS', 'SALDO', 'VALOR', 'COSTO UNITARIO'],
            [NA, '1011', 'PRODUCTO UNO UNI', NA, NA, NA, NA, NA, NA],
            [pd.Timestamp('2026-01-15'), 'CI', 'DOC1', 'FACTURA A CLIENTES', NA, 10, 90, -50000, 5000],
        ]
        df = self._build_df(filas)

        self.view._procesar_kardex(df)

        self.assertEqual(Articulo.objects.filter(codigo='1011').count(), 1)