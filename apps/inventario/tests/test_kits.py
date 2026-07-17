"""Pruebas de KitService.producibles(): cálculo de cuántas unidades de cada
kit se pueden armar hoy con el stock actual de sus componentes."""
from django.test import TestCase

from apps.inventario.models import Articulo, Kit, KitComponente
from apps.inventario.services import KitService


class KitsProduciblesTestCase(TestCase):
    def _crear_articulo(self, codigo, stock):
        return Articulo.objects.create(
            codigo=codigo, descripcion=f'Artículo {codigo}', unidad_medida='UND',
            stock_actual=stock,
        )

    def test_max_producible_lo_limita_el_componente_mas_escaso(self):
        kit = Kit.objects.create(codigo='KIT-01', descripcion='Kit de prueba')
        comp_a = self._crear_articulo('COMP-A', stock=100)  # requiere 2 -> alcanzan 50
        comp_b = self._crear_articulo('COMP-B', stock=9)    # requiere 3 -> alcanzan 3 (cuello de botella)
        KitComponente.objects.create(kit=kit, articulo=comp_a, cantidad_requerida=2)
        KitComponente.objects.create(kit=kit, articulo=comp_b, cantidad_requerida=3)

        resultado = next(r for r in KitService.producibles() if r['codigo'] == 'KIT-01')

        self.assertEqual(resultado['max_producible'], 3)
        self.assertEqual(resultado['componente_limitante']['codigo'], 'COMP-B')

    def test_stock_negativo_produce_max_producible_negativo(self):
        kit = Kit.objects.create(codigo='KIT-NEG', descripcion='Kit con déficit')
        comp = self._crear_articulo('COMP-NEG', stock=-6)
        KitComponente.objects.create(kit=kit, articulo=comp, cantidad_requerida=2)

        resultado = next(r for r in KitService.producibles() if r['codigo'] == 'KIT-NEG')

        self.assertEqual(resultado['max_producible'], -3)  # -6 // 2

    def test_kit_sin_componentes_se_omite_del_resultado(self):
        Kit.objects.create(codigo='KIT-VACIO', descripcion='Sin componentes')
        codigos = [r['codigo'] for r in KitService.producibles()]
        self.assertNotIn('KIT-VACIO', codigos)

    def test_kit_inactivo_no_aparece_en_producibles(self):
        kit = Kit.objects.create(codigo='KIT-INACTIVO', descripcion='Inactivo', activo=False)
        comp = self._crear_articulo('COMP-X', stock=10)
        KitComponente.objects.create(kit=kit, articulo=comp, cantidad_requerida=1)

        codigos = [r['codigo'] for r in KitService.producibles()]
        self.assertNotIn('KIT-INACTIVO', codigos)

    def test_resultado_ordenado_de_menor_a_mayor_max_producible(self):
        kit_alto = Kit.objects.create(codigo='KIT-ALTO', descripcion='Mucho stock')
        KitComponente.objects.create(
            kit=kit_alto, articulo=self._crear_articulo('COMP-ALTO', stock=1000),
            cantidad_requerida=1,
        )
        kit_bajo = Kit.objects.create(codigo='KIT-BAJO', descripcion='Poco stock')
        KitComponente.objects.create(
            kit=kit_bajo, articulo=self._crear_articulo('COMP-BAJO', stock=1),
            cantidad_requerida=1,
        )

        resultado = KitService.producibles()
        codigos_en_orden = [r['codigo'] for r in resultado if r['codigo'] in ('KIT-ALTO', 'KIT-BAJO')]
        self.assertEqual(codigos_en_orden, ['KIT-BAJO', 'KIT-ALTO'])