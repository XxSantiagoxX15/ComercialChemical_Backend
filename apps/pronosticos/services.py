"""
Motor de forecasting portado del Chem Movid.
Fuente: backend/forecasting/engine/ (arima.py, exponential_smoothing.py, linear_regression.py)
"""
import numpy as np
import pandas as pd
from apps.ventas.models import VentaHistorico
from apps.inventario.models import Articulo
from .models import Prediccion


def _get_serie(articulo_id: int) -> pd.Series | None:
    """Serie temporal mensual de ventas para un artículo."""
    ventas = (
        VentaHistorico.objects
        .filter(articulo_id=articulo_id)
        .values('fecha', 'cantidad_vendida')
        .order_by('fecha')
    )
    if not ventas:
        return None
    df = pd.DataFrame(list(ventas))
    df['fecha'] = pd.to_datetime(df['fecha'])
    return df.set_index('fecha').resample('ME')['cantidad_vendida'].sum()


def calcular_prediccion(articulo_id: int, modelo: str = 'suavizacion_exponencial', periodos: int = 3) -> dict:
    """
    Portado de Chem Movid forecasting/engine/*.py
    Modelos soportados:
      - suavizacion_exponencial  (mín. 12 períodos)
      - arima                    (mín. 36 períodos)
      - regresion_minimos_cuadrados (mín. 12 períodos)
    """
    serie = _get_serie(articulo_id)
    if serie is None or len(serie) < 12:
        return {
            'error': 'Datos insuficientes (se requieren al menos 12 meses de historial)',
            'periodos_disponibles': len(serie) if serie is not None else 0,
        }

    if modelo == 'arima' and len(serie) < 36:
        return {
            'error': 'ARIMA requiere mínimo 36 períodos mensuales',
            'periodos_disponibles': len(serie),
            'periodos_faltantes': 36 - len(serie),
        }

    valores = serie.values.astype(float)
    fechas_futuras = [serie.index[-1] + pd.DateOffset(months=i + 1) for i in range(periodos)]
    params = {}

    try:
        if modelo == 'suavizacion_exponencial':
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            fit = ExponentialSmoothing(valores, trend='add', seasonal=None).fit()
            forecast = np.maximum(fit.forecast(periodos), 0)
            params = {'alpha': round(float(fit.params.get('smoothing_level', 0)), 4)}

        elif modelo == 'arima':
            from statsmodels.tsa.arima.model import ARIMA
            fit = ARIMA(valores, order=(1, 1, 1)).fit()
            forecast = np.maximum(fit.forecast(periodos), 0)
            params = {'order': '(1,1,1)', 'aic': round(float(fit.aic), 2)}

        elif modelo == 'regresion_minimos_cuadrados':
            x = np.arange(len(valores))
            coef = np.polyfit(x, valores, 1)
            x_fut = np.arange(len(valores), len(valores) + periodos)
            forecast = np.maximum(np.polyval(coef, x_fut), 0)
            params = {'pendiente': round(float(coef[0]), 4), 'intercepto': round(float(coef[1]), 4)}

        else:
            return {'error': f'Modelo no soportado: {modelo}'}

        # Guardar predicciones en BD
        try:
            articulo = Articulo.objects.get(id=articulo_id)
        except Articulo.DoesNotExist:
            return {'error': 'Artículo no encontrado'}

        predicciones = []
        for fecha, valor in zip(fechas_futuras, forecast):
            Prediccion.objects.update_or_create(
                articulo=articulo,
                modelo=modelo,
                fecha_prediccion=fecha.date(),
                defaults={'demanda_proyectada': max(float(valor), 0), 'parametros_modelo': params}
            )
            predicciones.append({'fecha': fecha.strftime('%Y-%m-%d'),
                                  'demanda_proyectada': round(max(float(valor), 0), 3)})

        return {
            'articulo_id': articulo_id,
            'modelo': modelo,
            'periodos_historicos': len(serie),
            'predicciones': predicciones,
            'parametros': params,
        }

    except Exception as e:
        return {'error': str(e)}


def analisis_estadistico(articulo_id: int) -> dict:
    """Portado de Chem Movid: análisis básico de la serie temporal."""
    serie = _get_serie(articulo_id)
    if serie is None or len(serie) == 0:
        return {'error': 'Sin datos históricos'}
    valores = serie.values.astype(float)
    tendencia = 'creciente' if len(valores) > 1 and valores[-1] > valores[0] else 'decreciente'
    return {
        'articulo_id': articulo_id,
        'periodos': len(valores),
        'media': round(float(np.mean(valores)), 3),
        'desviacion_estandar': round(float(np.std(valores)), 3),
        'minimo': round(float(np.min(valores)), 3),
        'maximo': round(float(np.max(valores)), 3),
        'tendencia': tendencia,
    }


def generar_recomendaciones_compras() -> list:
    """Portado de Chem Movid compras service: recomendaciones basadas en forecasting."""
    articulos = Articulo.objects.filter(activo=True)
    recomendaciones = []
    for art in articulos:
        pred = Prediccion.objects.filter(articulo=art).order_by('-fecha_prediccion').first()
        demanda = float(pred.demanda_proyectada) if pred else 0
        stock = float(art.stock_actual)
        stock_min = float(art.stock_minimo)
        cantidad_recomendada = max(demanda - (stock - stock_min), 0)

        if stock < stock_min:
            prioridad = 'critico'
        elif stock < demanda:
            prioridad = 'alto'
        elif stock < 1.5 * demanda:
            prioridad = 'medio'
        else:
            prioridad = 'bajo'

        recomendaciones.append({
            'articulo_id': art.id, 'codigo': art.codigo, 'descripcion': art.descripcion,
            'stock_actual': stock, 'demanda_proyectada': demanda,
            'cantidad_recomendada': round(cantidad_recomendada, 3),
            'nivel_prioridad': prioridad,
            'exceso_inventario': stock > 2 * demanda if demanda > 0 else False,
        })
    orden = {'critico': 0, 'alto': 1, 'medio': 2, 'bajo': 3}
    return sorted(recomendaciones, key=lambda x: orden.get(x['nivel_prioridad'], 4))
