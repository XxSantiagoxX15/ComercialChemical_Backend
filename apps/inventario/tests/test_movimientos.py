"""
Pruebas del consumo FIFO de lotes en MovimientoService.registrar().

Cubren la lógica más compleja del módulo de inventario: al registrar una
salida, los lotes deben descontarse en orden de vencimiento (el que vence
más pronto primero), pudiendo repartirse entre varios lotes si uno solo no
alcanza a cubrir la cantidad solicitada.
"""
from datetime import date

from django.test import TestCase
from rest_framework.exceptions import ValidationError

from apps.inventario.models import Articulo, LoteInventario, MovimientoInventario
from apps.inventario.services import MovimientoService


class FifoConsumoTestCase(TestCase):
    def setUp(self):
        self.articulo = Articulo.objects.create(
            codigo='TEST-FIFO-01',
            descripcion='Artículo de prueba FIFO',
            unidad_medida='UND',
            stock_actual=30,
            stock_minimo=5,
        )
        self.lote_vence_pronto = LoteInventario.objects.create(
            articulo=self.articulo, numero_lote='L1',
            cantidad_inicial=10, cantidad_disponible=10,
            fecha_vencimiento=date(2026, 8, 1),
        )
        self.lote_vence_despues = LoteInventario.objects.create(
            articulo=self.articulo, numero_lote='L2',
            cantidad_inicial=20, cantidad_disponible=20,
            fecha_vencimiento=date(2026, 12, 1),
        )

    def test_salida_consume_primero_el_lote_que_vence_mas_pronto(self):
        MovimientoService.registrar(articulo_id=self.articulo.id, tipo='salida', cantidad=6)

        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 4)
        self.assertEqual(self.lote_vence_despues.cantidad_disponible, 20)

    def test_salida_reparte_entre_varios_lotes_cuando_uno_no_alcanza(self):
        MovimientoService.registrar(articulo_id=self.articulo.id, tipo='salida', cantidad=15)

        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 0)
        self.assertFalse(self.lote_vence_pronto.activo)  # se agotó -> se desactiva
        self.assertEqual(self.lote_vence_despues.cantidad_disponible, 15)
        self.assertTrue(self.lote_vence_despues.activo)

    def test_lote_sin_fecha_vencimiento_se_consume_de_ultimo(self):
        lote_sin_fecha = LoteInventario.objects.create(
            articulo=self.articulo, numero_lote='L0-SIN-FECHA',
            cantidad_inicial=5, cantidad_disponible=5,
            fecha_vencimiento=None,
        )
        # stock_actual ya incluye estos 5, ajustamos el escenario a 35 en total
        self.articulo.stock_actual = 35
        self.articulo.save(update_fields=['stock_actual'])

        MovimientoService.registrar(articulo_id=self.articulo.id, tipo='salida', cantidad=30)

        lote_sin_fecha.refresh_from_db()
        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 0)
        self.assertEqual(self.lote_vence_despues.cantidad_disponible, 0)
        self.assertEqual(lote_sin_fecha.cantidad_disponible, 5)  # nunca se tocó

    def test_entrada_crea_lote_propio_con_la_cantidad_completa(self):
        MovimientoService.registrar(
            articulo_id=self.articulo.id, tipo='entrada', cantidad=50,
            numero_lote='L-NUEVO', fecha_vencimiento=date(2027, 1, 1),
        )
        self.articulo.refresh_from_db()
        self.assertEqual(self.articulo.stock_actual, 80)
        nuevo = LoteInventario.objects.get(numero_lote='L-NUEVO')
        self.assertEqual(nuevo.cantidad_disponible, 50)

    def test_salida_sin_stock_suficiente_sin_forzar_lanza_error_y_no_modifica_nada(self):
        with self.assertRaises(ValidationError):
            MovimientoService.registrar(articulo_id=self.articulo.id, tipo='salida', cantidad=100)

        self.articulo.refresh_from_db()
        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.articulo.stock_actual, 30)          # sin cambios
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 10)  # sin cambios
        self.assertEqual(MovimientoInventario.objects.count(), 0)  # no quedó registrado

    def test_salida_con_forzar_permite_deficit_y_stock_queda_en_cero(self):
        MovimientoService.registrar(
            articulo_id=self.articulo.id, tipo='salida', cantidad=100, forzar=True,
        )
        self.articulo.refresh_from_db()
        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.articulo.stock_actual, 0)  # max(0, 30-100)
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 0)
        self.assertEqual(self.lote_vence_despues.cantidad_disponible, 0)

    def test_ajuste_fija_stock_absoluto_sin_tocar_los_lotes(self):
        MovimientoService.registrar(articulo_id=self.articulo.id, tipo='ajuste', cantidad=12)

        self.articulo.refresh_from_db()
        self.lote_vence_pronto.refresh_from_db()
        self.lote_vence_despues.refresh_from_db()
        self.assertEqual(self.articulo.stock_actual, 12)
        # comportamiento documentado: 'ajuste' no reconcilia lotes individuales
        self.assertEqual(self.lote_vence_pronto.cantidad_disponible, 10)
        self.assertEqual(self.lote_vence_despues.cantidad_disponible, 20)

    def test_tipo_movimiento_invalido_lanza_error(self):
        with self.assertRaises(ValidationError):
            MovimientoService.registrar(articulo_id=self.articulo.id, tipo='no_existe', cantidad=1)

    def test_articulo_inactivo_no_permite_movimientos(self):
        self.articulo.activo = False
        self.articulo.save(update_fields=['activo'])
        with self.assertRaises(ValidationError):
            MovimientoService.registrar(articulo_id=self.articulo.id, tipo='entrada', cantidad=1)