"""
Corre calcular_prediccion() para todos los artículos activos que tengan
historial de ventas, así el pronóstico global del Dashboard (línea punteada
de 'Demanda Proyectada') refleja todo el catálogo y no solo el puñado de
artículos que se hayan pronosticado manualmente desde la pantalla de
Pronósticos. Equivalente por consola al botón "Pronóstico masivo" del frontend
(ambos llaman a services.calcular_pronosticos_masivo()).

Uso:
    manage.py calcular_pronosticos_masivo
    manage.py calcular_pronosticos_masivo --modelo arima --periodos 6

Por defecto usa --modelo auto: para cada artículo prueba los modelos que
apliquen según su historial (suavización exponencial, regresión, ARIMA si
hay 12+ meses) y se queda con el más preciso en un backtest, en vez de
forzar el mismo modelo para todo el catálogo.
"""
from django.core.management.base import BaseCommand

from apps.pronosticos.services import calcular_pronosticos_masivo


class Command(BaseCommand):
    help = 'Calcula el pronóstico de demanda para todos los artículos activos con historial de ventas.'

    def add_arguments(self, parser):
        parser.add_argument('--modelo', default='auto',
                             choices=['auto', 'suavizacion_exponencial', 'arima', 'regresion_minimos_cuadrados'])
        parser.add_argument('--periodos', type=int, default=3)

    def handle(self, *args, **options):
        modelo = options['modelo']
        periodos = options['periodos']

        self.stdout.write(f'Calculando pronóstico ({modelo}, {periodos} periodos)...')
        resultado = calcular_pronosticos_masivo(modelo=modelo, periodos=periodos)

        self.stdout.write(self.style.SUCCESS(
            f"Listo: {resultado['calculados']}/{resultado['total_articulos']} artículos con pronóstico calculado, "
            f"{resultado['con_error']} con errores/sin datos suficientes."
        ))
        if resultado['errores']:
            self.stdout.write('Ejemplos de errores:')
            for e in resultado['errores']:
                self.stdout.write(f"  articulo_id={e['articulo_id']}: {e['error']}")