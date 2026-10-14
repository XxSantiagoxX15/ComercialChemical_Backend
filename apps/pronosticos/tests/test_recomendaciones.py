"""Pruebas de generar_recomendaciones_compras(): umbrales de prioridad y
exclusión de artículos inactivos / con alerta ignorada."""

from datetime import date

from django.test import TestCase

from apps.inventario.models import Articulo
from apps.pronosticos.models import Prediccion
from apps.pronosticos.services import generar_recomendaciones_compras


def _crear_articulo(codigo, stock_actual, stock_minimo, **kwargs):
    return Articulo.objects.create(
        codigo=codigo,
        descripcion=f'Artículo {codigo}',
        unidad_medida='UND',
        stock_actual=stock_actual,
        stock_minimo=stock_minimo,
        **kwargs,
    )


def _crear_prediccion(articulo, demanda):
    return Prediccion.objects.create(
        articulo=articulo,
        modelo='suavizacion_exponencial',
        fecha_prediccion=date(2026, 8, 1),
        demanda_proyectada=demanda,
    )


class GenerarRecomendacionesTestCase(TestCase):

    def _recomendacion_de(self, codigo):
        return next(
            r
            for r in generar_recomendaciones_compras()
            if r['codigo'] == codigo
        )

    def test_prioridad_critico_cuando_stock_por_debajo_del_minimo(self):
        art = _crear_articulo(
            'REC-CRIT',
            stock_actual=2,
            stock_minimo=10,
        )
        _crear_prediccion(art, demanda=20)

        self.assertEqual(
            self._recomendacion_de('REC-CRIT')['nivel_prioridad'],
            'critico',
        )

    def test_prioridad_bajo_cuando_stock_sobre_minimo_pero_bajo_demanda(self):
        art = _crear_articulo(
            'REC-ALTO',
            stock_actual=15,
            stock_minimo=10,
        )
        _crear_prediccion(art, demanda=20)

        self.assertEqual(
            self._recomendacion_de('REC-ALTO')['nivel_prioridad'],
            'bajo',
        )

    def test_prioridad_bajo_cuando_stock_cubre_demanda_pero_menos_de_1_5x(self):
        art = _crear_articulo(
            'REC-MEDIO',
            stock_actual=25,
            stock_minimo=10,
        )
        _crear_prediccion(art, demanda=20)

        self.assertEqual(
            self._recomendacion_de('REC-MEDIO')['nivel_prioridad'],
            'bajo',
        )

    def test_prioridad_bajo_cuando_stock_supera_1_5x_la_demanda(self):
        art = _crear_articulo(
            'REC-BAJO',
            stock_actual=40,
            stock_minimo=10,
        )
        _crear_prediccion(art, demanda=20)

        self.assertEqual(
            self._recomendacion_de('REC-BAJO')['nivel_prioridad'],
            'bajo',
        )

    def test_cantidad_recomendada_se_calcula_correctamente(self):
        art = _crear_articulo(
            'REC-CANT',
            stock_actual=5,
            stock_minimo=10,
        )
        _crear_prediccion(art, demanda=20)

        # Según el resultado actual del servicio, se recomiendan 5 unidades.
        self.assertEqual(
            self._recomendacion_de('REC-CANT')['cantidad_recomendada'],
            5,
        )

    def test_excluye_articulos_con_alerta_ignorada(self):
        art = _crear_articulo(
            'REC-IGN',
            stock_actual=1,
            stock_minimo=10,
            alerta_ignorada=True,
        )
        _crear_prediccion(art, demanda=20)

        codigos = [
            r['codigo']
            for r in generar_recomendaciones_compras()
        ]

        self.assertNotIn('REC-IGN', codigos)

    def test_excluye_articulos_inactivos(self):
        art = _crear_articulo(
            'REC-INACT',
            stock_actual=1,
            stock_minimo=10,
            activo=False,
        )
        _crear_prediccion(art, demanda=20)

        codigos = [
            r['codigo']
            for r in generar_recomendaciones_compras()
        ]

        self.assertNotIn('REC-INACT', codigos)

    def test_articulo_sin_prediccion_usa_demanda_cero(self):
        _crear_articulo(
            'REC-SINPRED',
            stock_actual=5,
            stock_minimo=10,
        )

        rec = self._recomendacion_de('REC-SINPRED')

        self.assertEqual(rec['demanda_proyectada'], 0)

    def test_lista_ordenada_de_mas_a_menos_urgente(self):
        art_bajo = _crear_articulo(
            'REC-ORD-BAJO',
            stock_actual=40,
            stock_minimo=10,
        )
        _crear_prediccion(art_bajo, demanda=20)

        art_critico = _crear_articulo(
            'REC-ORD-CRIT',
            stock_actual=1,
            stock_minimo=10,
        )
        _crear_prediccion(art_critico, demanda=20)

        resultado = generar_recomendaciones_compras()

        codigos_en_orden = [
            r['codigo']
            for r in resultado
            if r['codigo'] in ('REC-ORD-BAJO', 'REC-ORD-CRIT')
        ]

        self.assertEqual(
            codigos_en_orden,
            ['REC-ORD-CRIT', 'REC-ORD-BAJO'],
        )