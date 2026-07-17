"""Pruebas de calcular_eoq() — cantidad económica de pedido (EOQ = sqrt(2*D*S/H))."""
import math
from datetime import date

from django.test import TestCase

from apps.inventario.models import Articulo
from apps.pronosticos.models import Prediccion
from apps.pronosticos.services import calcular_eoq


class CalcularEoqTestCase(TestCase):
    def setUp(self):
        self.articulo = Articulo.objects.create(
            codigo='EOQ-01', descripcion='Artículo con costos', unidad_medida='UND',
            costo_pedido=50000, costo_mantenimiento_anual=2000,
        )

    def test_eoq_correcto_con_costos_y_prediccion(self):
        Prediccion.objects.create(
            articulo=self.articulo, modelo='suavizacion_exponencial',
            fecha_prediccion=date(2026, 8, 1), demanda_proyectada=100,
        )

        resultado = calcular_eoq(self.articulo.id)

        demanda_anual = 100 * 12
        eoq_esperado = math.sqrt((2 * demanda_anual * 50000) / 2000)
        self.assertNotIn('error', resultado)
        self.assertEqual(resultado['demanda_anual_estimada'], round(demanda_anual, 3))
        self.assertAlmostEqual(resultado['cantidad_economica_pedido'], round(eoq_esperado, 3), places=2)
        self.assertIsNotNone(resultado['numero_pedidos_por_anio'])
        self.assertIsNotNone(resultado['dias_entre_pedidos'])

    def test_usa_la_prediccion_mas_reciente_cuando_hay_varias(self):
        Prediccion.objects.create(
            articulo=self.articulo, modelo='suavizacion_exponencial',
            fecha_prediccion=date(2026, 6, 1), demanda_proyectada=10,
        )
        Prediccion.objects.create(
            articulo=self.articulo, modelo='arima',
            fecha_prediccion=date(2026, 9, 1), demanda_proyectada=200,
        )

        resultado = calcular_eoq(self.articulo.id)

        self.assertEqual(resultado['demanda_anual_estimada'], 200 * 12)

    def test_error_si_articulo_no_existe(self):
        resultado = calcular_eoq(999999)
        self.assertIn('error', resultado)

    def test_error_si_faltan_costos_configurados(self):
        art_sin_costos = Articulo.objects.create(
            codigo='EOQ-02', descripcion='Sin costos', unidad_medida='UND',
        )
        resultado = calcular_eoq(art_sin_costos.id)
        self.assertIn('error', resultado)

    def test_error_si_no_hay_prediccion_calculada(self):
        resultado = calcular_eoq(self.articulo.id)
        self.assertIn('error', resultado)
        self.assertIn('pronóstico', resultado['error'])

    def test_error_si_demanda_proyectada_es_cero(self):
        Prediccion.objects.create(
            articulo=self.articulo, modelo='suavizacion_exponencial',
            fecha_prediccion=date(2026, 8, 1), demanda_proyectada=0,
        )
        resultado = calcular_eoq(self.articulo.id)
        self.assertIn('error', resultado)