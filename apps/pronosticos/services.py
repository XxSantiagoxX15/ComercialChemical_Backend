"""
Motor de forecasting portado del Chem Movid.
Fuente: backend/forecasting/engine/ (arima.py, exponential_smoothing.py, linear_regression.py)
"""
import math
import numpy as np
import pandas as pd
from apps.ventas.models import VentaHistorico
from apps.inventario.models import Articulo
from .models import Prediccion


def productos_mas_vendidos(limit: int = 20) -> list[dict]:
    """Top de artículos por unidades vendidas en todo el histórico del Kardex."""
    from django.db.models import Sum

    top = (
        VentaHistorico.objects
        .values('articulo__codigo', 'articulo__descripcion')
        .annotate(total_vendido=Sum('cantidad_vendida'))
        .order_by('-total_vendido')[:limit]
    )
    return [
        {
            'codigo': r['articulo__codigo'],
            'descripcion': r['articulo__descripcion'],
            'total_vendido': round(float(r['total_vendido'] or 0), 3),
        }
        for r in top
    ]


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


def _ajustar_y_pronosticar(valores: np.ndarray, modelo: str, periodos: int) -> tuple[np.ndarray, dict]:
    """Ajusta `modelo` sobre `valores` y devuelve (pronóstico, parámetros).
    No atrapa excepciones — si el modelo no puede ajustar con estos datos
    (muy pocos puntos, ARIMA que no converge, etc.) lanza la excepción y
    quien llama decide qué hacer (descartar el modelo, reportar el error)."""
    n = len(valores)

    if modelo == 'suavizacion_exponencial':
        if n >= 12:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
            fit = ExponentialSmoothing(
                valores, trend='add', seasonal=None, initialization_method='estimated').fit()
            forecast = np.maximum(fit.forecast(periodos), 0)
            params = {'alpha': round(float(fit.params.get('smoothing_level', 0)), 4), 'metodo': 'Holt-Winters'}
        elif n >= 3:
            from statsmodels.tsa.holtwinters import SimpleExpSmoothing
            fit = SimpleExpSmoothing(valores, initialization_method='estimated').fit(optimized=True)
            forecast = np.maximum(fit.forecast(periodos), 0)
            params = {'alpha': round(float(fit.params.get('smoothing_level', 0)), 4), 'metodo': 'Simple'}
        elif n == 2:
            # Regresión lineal con 2 puntos
            pendiente = float(valores[1] - valores[0])
            base = float(valores[-1])
            forecast = np.array([max(base + pendiente * (i + 1), 0) for i in range(periodos)])
            params = {'metodo': 'Lineal simple', 'pendiente': round(pendiente, 3)}
        elif n == 1:
            forecast = np.full(periodos, max(float(valores[0]), 0))
            params = {'metodo': 'Promedio constante', 'valor_base': round(float(valores[0]), 3)}
        else:
            raise ValueError('Sin datos suficientes para suavización exponencial')

    elif modelo == 'arima':
        if n < 12:
            raise ValueError('ARIMA requiere mínimo 12 períodos mensuales')
        from statsmodels.tsa.arima.model import ARIMA
        fit = ARIMA(valores, order=(1, 1, 1)).fit()
        forecast = np.maximum(fit.forecast(periodos), 0)
        params = {'order': '(1,1,1)', 'aic': round(float(fit.aic), 2)}

    elif modelo == 'regresion_minimos_cuadrados':
        if n >= 2:
            x = np.arange(n)
            coef = np.polyfit(x, valores, 1)
            x_fut = np.arange(n, n + periodos)
            forecast = np.maximum(np.polyval(coef, x_fut), 0)
            params = {'pendiente': round(float(coef[0]), 4), 'intercepto': round(float(coef[1]), 4)}
        elif n == 1:
            forecast = np.full(periodos, max(float(valores[0]), 0))
            params = {'metodo': 'Promedio constante'}
        else:
            raise ValueError('Sin datos suficientes para regresión de mínimos cuadrados')

    else:
        raise ValueError(f'Modelo no soportado: {modelo}')

    return forecast, params


def _evaluar_modelos(valores: np.ndarray, holdout: int) -> dict:
    """Backtesting simple para elegir el modelo más preciso PARA ESTE artículo:
    entrena cada modelo candidato con todo menos los últimos `holdout` meses,
    pronostica esos meses, y mide qué tan lejos quedó del valor real (MAE).
    Los modelos que no logran ajustar con estos datos (ej. ARIMA sin
    suficiente historial, o que no converge) simplemente se excluyen de la
    comparación en vez de hacer fallar todo el cálculo."""
    entrenamiento = valores[:-holdout]
    real = valores[-holdout:]
    n_train = len(entrenamiento)

    candidatos = ['suavizacion_exponencial']
    if n_train >= 2:
        candidatos.append('regresion_minimos_cuadrados')
    if n_train >= 12:
        candidatos.append('arima')

    resultados = {}
    for modelo in candidatos:
        try:
            forecast, _ = _ajustar_y_pronosticar(entrenamiento, modelo, holdout)
            mae = float(np.mean(np.abs(np.asarray(forecast) - real)))
            resultados[modelo] = {'mae': round(mae, 3)}
        except Exception:
            continue

    return resultados


def calcular_prediccion(articulo_id: int, modelo: str = 'suavizacion_exponencial', periodos: int = 3) -> dict:
    """
    Motor de forecasting — funciona con cualquier cantidad de datos históricos.
    - 1 período: proyección lineal simple (promedio constante)
    - 2 períodos: regresión lineal simple
    - 3+ períodos: SimpleExpSmoothing
    - 12+ períodos: Holt-Winters (más preciso)
    - ARIMA requiere mínimo 12 períodos
    - modelo='auto': prueba los modelos aplicables según cuántos datos haya
      y usa el que dio menor error en un backtest (ver calcular_prediccion_auto)
    """
    if modelo == 'auto':
        return calcular_prediccion_auto(articulo_id, periodos=periodos)

    serie = _get_serie(articulo_id)

    if serie is None or len(serie) == 0:
        return {
            'error': 'Este artículo no tiene historial de ventas registrado.',
            'periodos_disponibles': 0,
        }

    if modelo == 'arima' and len(serie) < 12:
        return {
            'error': 'ARIMA requiere mínimo 12 períodos mensuales',
            'periodos_disponibles': len(serie),
            'periodos_faltantes': 12 - len(serie),
        }

    valores = serie.values.astype(float)
    fechas_futuras = [serie.index[-1] + pd.DateOffset(months=i + 1) for i in range(periodos)]

    try:
        n = len(valores)
        forecast, params = _ajustar_y_pronosticar(valores, modelo, periodos)

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
            predicciones.append({
                'fecha': fecha.strftime('%Y-%m-%d'),
                'demanda_proyectada': round(max(float(valor), 0), 3)
            })

        return {
            'articulo_id': articulo_id,
            'modelo': modelo,
            'periodos_historicos': n,
            'predicciones': predicciones,
            'parametros': params,
        }

    except Exception as e:
        return {'error': str(e)}


def calcular_prediccion_auto(articulo_id: int, periodos: int = 3) -> dict:
    """
    Corre los modelos aplicables (suavización exponencial siempre; regresión
    y ARIMA cuando hay datos suficientes) en un backtest y se queda con el que
    menos error dio, en vez de asumir un único modelo fijo para todo el
    catálogo. Con muy poco historial (<5 meses) no hay margen para un backtest
    confiable, así que se usa directo suavización exponencial (que igual se
    degrada sola a lineal/promedio según cuántos puntos haya).

    El resultado final se guarda con modelo='auto' — un tag propio, separado
    de correr cada modelo a mano — y `parametros_modelo` guarda tanto los
    parámetros del modelo ganador como el detalle de la comparación, para que
    quede trazable por qué se eligió.
    """
    serie = _get_serie(articulo_id)
    if serie is None or len(serie) == 0:
        return {
            'error': 'Este artículo no tiene historial de ventas registrado.',
            'periodos_disponibles': 0,
        }

    valores = serie.values.astype(float)
    n = len(valores)
    fechas_futuras = [serie.index[-1] + pd.DateOffset(months=i + 1) for i in range(periodos)]

    comparacion = {}
    if n >= 5:
        holdout = min(3, n // 2)
        comparacion = _evaluar_modelos(valores, holdout)

    modelo_ganador = min(comparacion, key=lambda m: comparacion[m]['mae']) if comparacion else 'suavizacion_exponencial'

    try:
        forecast, params = _ajustar_y_pronosticar(valores, modelo_ganador, periodos)
    except Exception as e:
        return {'error': str(e)}

    try:
        articulo = Articulo.objects.get(id=articulo_id)
    except Articulo.DoesNotExist:
        return {'error': 'Artículo no encontrado'}

    params_guardados = {**params, 'modelo_ganador': modelo_ganador, 'comparacion_modelos': comparacion}

    predicciones = []
    for fecha, valor in zip(fechas_futuras, forecast):
        Prediccion.objects.update_or_create(
            articulo=articulo,
            modelo='auto',
            fecha_prediccion=fecha.date(),
            defaults={'demanda_proyectada': max(float(valor), 0), 'parametros_modelo': params_guardados}
        )
        predicciones.append({
            'fecha': fecha.strftime('%Y-%m-%d'),
            'demanda_proyectada': round(max(float(valor), 0), 3)
        })

    return {
        'articulo_id': articulo_id,
        'modelo': 'auto',
        'modelo_ganador': modelo_ganador,
        'comparacion_modelos': comparacion,
        'periodos_historicos': n,
        'predicciones': predicciones,
        'parametros': params,
    }


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
    """Portado de Chem Movid compras service: recomendaciones basadas en forecasting.
    Excluye artículos cuya alerta el usuario marcó como "No me interesa"."""
    articulos = list(Articulo.objects.filter(activo=True, alerta_ignorada=False))

    # Una sola query para todas las predicciones en vez de una por artículo (N+1).
    # modelo='auto': es el que generan el botón "Generar Pronóstico" y "Pronóstico
    # masivo" hoy. Sin este filtro, un artículo con predicciones de varios modelos
    # (ej. corridos a mano por consola) podía mostrar la de cualquiera de ellos
    # según cuál quedara de última por fecha, no necesariamente la más precisa.
    predicciones = (
        Prediccion.objects
        .filter(articulo_id__in=[a.id for a in articulos], modelo='auto')
        .order_by('articulo_id', '-fecha_prediccion')
    )
    ultima_prediccion_por_articulo = {}
    for pred in predicciones:
        ultima_prediccion_por_articulo.setdefault(pred.articulo_id, pred)

    recomendaciones = []
    for art in articulos:
        pred = ultima_prediccion_por_articulo.get(art.id)
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


def calcular_eoq(articulo_id: int) -> dict:
    """
    Cantidad económica de pedido (EOQ = sqrt(2*D*S/H)):
    - D: demanda anual estimada (última predicción mensual x 12)
    - S: costo_pedido del artículo
    - H: costo_mantenimiento_anual del artículo
    Requiere que el artículo tenga costos configurados y al menos una predicción calculada.
    """
    try:
        articulo = Articulo.objects.get(id=articulo_id)
    except Articulo.DoesNotExist:
        return {'error': 'Artículo no encontrado'}

    if not articulo.costo_pedido or not articulo.costo_mantenimiento_anual:
        return {
            'error': 'Este artículo no tiene costo de pedido y/o costo de mantenimiento configurados.',
            'articulo_id': articulo_id,
        }

    pred = Prediccion.objects.filter(articulo_id=articulo_id).order_by('-fecha_prediccion').first()
    if not pred:
        return {'error': 'Este artículo no tiene un pronóstico calculado todavía.'}

    demanda_anual = float(pred.demanda_proyectada) * 12
    costo_pedido = float(articulo.costo_pedido)
    costo_mantenimiento = float(articulo.costo_mantenimiento_anual)

    if demanda_anual <= 0 or costo_mantenimiento <= 0:
        return {'error': 'No se puede calcular EOQ: demanda proyectada o costo de mantenimiento inválidos.'}

    eoq = math.sqrt((2 * demanda_anual * costo_pedido) / costo_mantenimiento)
    num_pedidos_anual = demanda_anual / eoq if eoq > 0 else 0

    return {
        'articulo_id': articulo_id,
        'demanda_anual_estimada': round(demanda_anual, 3),
        'costo_pedido': costo_pedido,
        'costo_mantenimiento_anual': costo_mantenimiento,
        'cantidad_economica_pedido': round(eoq, 3),
        'numero_pedidos_por_anio': round(num_pedidos_anual, 2) if num_pedidos_anual else None,
        'dias_entre_pedidos': round(365 / num_pedidos_anual, 1) if num_pedidos_anual > 0 else None,
    }


def resumen_historico_pronostico(meses_historico: int = 12) -> dict:
    """
    Línea de tiempo global para el Dashboard: demanda histórica total (suma de
    todos los artículos, por mes) seguida de la demanda proyectada total
    (suma de las predicciones ya calculadas con modelo='auto', el que generan
    por defecto la pantalla de Pronósticos y el botón "Pronóstico masivo").
    """
    from django.db.models import Sum
    from django.db.models.functions import TruncMonth
    from apps.ventas.models import VentaHistorico

    historico_qs = (
        VentaHistorico.objects
        .annotate(mes=TruncMonth('fecha'))
        .values('mes')
        .annotate(total=Sum('cantidad_vendida'))
        .order_by('mes')
    )
    historico = list(historico_qs)[-meses_historico:]

    # Solo meses POSTERIORES al último mes histórico: las predicciones se
    # recalculan con distinta fecha base cada vez que se corre el forecast,
    # así que en la BD pueden quedar filas viejas para meses que ya pasaron
    # a formar parte del histórico real (duplicarían el eje X del gráfico).
    pronostico_qs = (
        Prediccion.objects
        .filter(modelo='auto')
        .annotate(mes=TruncMonth('fecha_prediccion'))
    )
    if historico:
        pronostico_qs = pronostico_qs.filter(mes__gt=historico[-1]['mes'])
    pronostico_qs = pronostico_qs.values('mes').annotate(total=Sum('demanda_proyectada')).order_by('mes')
    pronostico = list(pronostico_qs)

    return {
        'historico':  [{'mes': h['mes'].strftime('%Y-%m'), 'total': round(float(h['total']), 3)} for h in historico],
        'pronostico': [{'mes': p['mes'].strftime('%Y-%m'), 'total': round(float(p['total']), 3)} for p in pronostico],
        'tiene_pronostico': len(pronostico) > 0,
    }


def calcular_pronosticos_masivo(modelo: str = 'auto', periodos: int = 3) -> dict:
    """
    Calcula (o recalcula) el pronóstico de TODOS los artículos activos que
    tengan al menos una venta registrada, en vez de tener que abrir cada
    artículo uno por uno desde la pantalla de Pronósticos. Usado tanto por
    el botón "Pronóstico masivo" del frontend como por el management command
    `calcular_pronosticos_masivo` (para poder correrlo también por consola).
    """
    from apps.ventas.models import VentaHistorico

    articulos_con_ventas = set(VentaHistorico.objects.values_list('articulo_id', flat=True).distinct())
    articulos = list(
        Articulo.objects.filter(activo=True, id__in=articulos_con_ventas).values_list('id', flat=True)
    )

    ok, errores = 0, []
    for articulo_id in articulos:
        resultado = calcular_prediccion(articulo_id, modelo=modelo, periodos=periodos)
        if 'error' in resultado:
            errores.append({'articulo_id': articulo_id, 'error': resultado['error']})
        else:
            ok += 1

    return {
        'total_articulos': len(articulos),
        'calculados': ok,
        'con_error': len(errores),
        'errores': errores[:10],  # muestra solo los primeros 10 para no inflar la respuesta
        'modelo': modelo,
        'periodos': periodos,
    }


def articulos_riesgo_agotamiento(dias_umbral: int = 30, limit: int = 10) -> dict:
    """
    Días de cobertura restante por artículo = stock_actual / demanda_diaria_proyectada.
    Es la versión que SÍ es útil a nivel de negocio: un stock agregado de toda
    la empresa (millones de unidades) esconde que artículos puntuales se
    pueden agotar en días — acá se identifican esos artículos uno por uno,
    ordenados del más urgente al menos urgente.

    Solo incluye artículos activos con una predicción de demanda > 0 (sin
    demanda proyectada no hay forma de estimar cuándo se agotarían).
    """
    articulos = list(Articulo.objects.filter(activo=True))
    predicciones = (
        Prediccion.objects
        .filter(modelo='auto', articulo_id__in=[a.id for a in articulos])
        .order_by('articulo_id', '-fecha_prediccion')
    )
    ultima_prediccion_por_articulo = {}
    for pred in predicciones:
        ultima_prediccion_por_articulo.setdefault(pred.articulo_id, pred)

    resultado = []
    for art in articulos:
        pred = ultima_prediccion_por_articulo.get(art.id)
        if not pred:
            continue
        demanda_mensual = float(pred.demanda_proyectada)
        if demanda_mensual <= 0:
            continue
        stock = float(art.stock_actual)
        demanda_diaria = demanda_mensual / 30
        dias_cobertura = stock / demanda_diaria if demanda_diaria > 0 else None

        resultado.append({
            'articulo_id': art.id,
            'codigo': art.codigo,
            'descripcion': art.descripcion,
            'stock_actual': round(stock, 3),
            'demanda_proyectada_mensual': round(demanda_mensual, 3),
            'dias_cobertura': round(dias_cobertura, 1) if dias_cobertura is not None else None,
        })

    resultado.sort(key=lambda x: x['dias_cobertura'] if x['dias_cobertura'] is not None else float('inf'))
    en_riesgo = [r for r in resultado if r['dias_cobertura'] is not None and r['dias_cobertura'] <= dias_umbral]

    return {
        'total_con_pronostico': len(resultado),
        'total_en_riesgo': len(en_riesgo),
        'dias_umbral': dias_umbral,
        'top': resultado[:limit],
    }


def _variacion_pct(base: float, nuevo: float) -> float | None:
    """% de cambio de `base` (periodo 2, el de referencia) a `nuevo`
    (periodo 1) — positivo si el periodo 1 vendió más que el periodo 2.
    None si no es calculable (ambos en cero) — no se puede sacar un % de
    variación desde una base de 0 sin inventar un número."""
    if base == 0:
        return None if nuevo == 0 else 100.0
    return round((nuevo - base) / base * 100, 2)


def comparar_periodos(desde1, hasta1, desde2, hasta2, articulo_id: int | None = None, limit: int = 20) -> dict:
    """
    Compara ventas (Kardex → VentaHistorico) entre dos rangos de fechas
    cualquiera, en total y por artículo. Los dos rangos son completamente
    libres (no tienen que ser del mismo tamaño ni ser consecutivos) para
    poder comparar, por ejemplo, "este mes vs. el mes pasado", "este año vs.
    el año pasado" o cualquier par de fechas que el usuario elija.
    """
    from django.db.models import Sum, Case, When, F, DecimalField, Q
    from apps.ventas.models import VentaHistorico

    base_qs = VentaHistorico.objects.filter(
        Q(fecha__gte=desde1, fecha__lte=hasta1) | Q(fecha__gte=desde2, fecha__lte=hasta2)
    )
    if articulo_id:
        base_qs = base_qs.filter(articulo_id=articulo_id)

    def _suma(campo, desde, hasta):
        return Sum(Case(
            When(fecha__gte=desde, fecha__lte=hasta, then=F(campo)),
            default=0, output_field=DecimalField(max_digits=18, decimal_places=3),
        ))

    totales = base_qs.aggregate(
        unidades_1=_suma('cantidad_vendida', desde1, hasta1),
        unidades_2=_suma('cantidad_vendida', desde2, hasta2),
        valor_1=_suma('valor_total', desde1, hasta1),
        valor_2=_suma('valor_total', desde2, hasta2),
    )
    u1 = float(totales['unidades_1'] or 0)
    u2 = float(totales['unidades_2'] or 0)
    v1 = float(totales['valor_1'] or 0)
    v2 = float(totales['valor_2'] or 0)

    por_articulo = (
        base_qs
        .values('articulo_id', 'articulo__codigo', 'articulo__descripcion')
        .annotate(
            unidades_1=_suma('cantidad_vendida', desde1, hasta1),
            unidades_2=_suma('cantidad_vendida', desde2, hasta2),
            valor_1=_suma('valor_total', desde1, hasta1),
            valor_2=_suma('valor_total', desde2, hasta2),
        )
    )

    detalle = []
    for fila in por_articulo:
        fu1 = float(fila['unidades_1'] or 0)
        fu2 = float(fila['unidades_2'] or 0)
        detalle.append({
            'articulo_id': fila['articulo_id'],
            'codigo': fila['articulo__codigo'],
            'descripcion': fila['articulo__descripcion'],
            'unidades_periodo_1': round(fu1, 3),
            'unidades_periodo_2': round(fu2, 3),
            'valor_periodo_1': round(float(fila['valor_1'] or 0), 2),
            'valor_periodo_2': round(float(fila['valor_2'] or 0), 2),
            'variacion_pct': _variacion_pct(fu2, fu1),
        })
    detalle.sort(key=lambda d: d['unidades_periodo_1'] + d['unidades_periodo_2'], reverse=True)

    return {
        'periodo_1': {'desde': str(desde1), 'hasta': str(hasta1), 'unidades': round(u1, 3), 'valor': round(v1, 2)},
        'periodo_2': {'desde': str(desde2), 'hasta': str(hasta2), 'unidades': round(u2, 3), 'valor': round(v2, 2)},
        'variacion_pct_unidades': _variacion_pct(u2, u1),
        'variacion_pct_valor': _variacion_pct(v2, v1),
        'total_articulos_con_venta': len(detalle),
        'detalle': detalle[:limit],
    }


def meses_pronosticados_disponibles() -> list[str]:
    """Meses (YYYY-MM), desde el mes actual en adelante, para los que hay al
    menos un pronóstico calculado con el modelo 'auto' — alimenta el
    selector del reporte de necesidades. Se excluyen meses ya pasados: un
    reporte de "necesidades futuras" para un mes que ya ocurrió no tiene
    utilidad práctica."""
    from django.db.models.functions import TruncMonth
    from django.utils import timezone

    inicio_mes_actual = timezone.now().date().replace(day=1)
    meses = (
        Prediccion.objects
        .filter(modelo='auto', fecha_prediccion__gte=inicio_mes_actual)
        .annotate(mes=TruncMonth('fecha_prediccion'))
        .values_list('mes', flat=True)
        .distinct()
        .order_by('mes')
    )
    return [m.strftime('%Y-%m') for m in meses]


def reporte_necesidades_mes(mes: str) -> dict:
    """
    Reporte de necesidades de compra para un mes futuro ya pronosticado:
    por cada artículo, cuánto se necesita ese mes y cuánto hay que comprar,
    descontando el stock que se estima que quedará disponible después de
    cubrir la demanda proyectada de los meses anteriores dentro del mismo
    horizonte pronosticado (no solo el stock de hoy).
    """
    import calendar
    import datetime

    anio, mes_num = (int(x) for x in mes.split('-'))
    inicio_mes = datetime.date(anio, mes_num, 1)
    fin_mes = datetime.date(anio, mes_num, calendar.monthrange(anio, mes_num)[1])

    predicciones = (
        Prediccion.objects
        .filter(modelo='auto', fecha_prediccion__lte=fin_mes)
        .select_related('articulo')
        .order_by('articulo_id', 'fecha_prediccion')
    )

    por_articulo: dict[int, list] = {}
    for pred in predicciones:
        por_articulo.setdefault(pred.articulo_id, []).append(pred)

    items = []
    for articulo_id, preds in por_articulo.items():
        pred_mes_objetivo = next((p for p in preds if inicio_mes <= p.fecha_prediccion <= fin_mes), None)
        if not pred_mes_objetivo:
            continue
        articulo = pred_mes_objetivo.articulo
        demanda_meses_anteriores = sum(
            float(p.demanda_proyectada) for p in preds if p.fecha_prediccion < inicio_mes
        )
        stock_disponible_estimado = max(float(articulo.stock_actual) - demanda_meses_anteriores, 0)
        demanda_mes = float(pred_mes_objetivo.demanda_proyectada)
        cantidad_a_comprar = max(demanda_mes - stock_disponible_estimado, 0)

        items.append({
            'articulo_id': articulo_id,
            'codigo': articulo.codigo,
            'descripcion': articulo.descripcion,
            'unidad_medida': articulo.unidad_medida,
            'demanda_proyectada': round(demanda_mes, 3),
            'stock_disponible_estimado': round(stock_disponible_estimado, 3),
            'cantidad_a_comprar': round(cantidad_a_comprar, 3),
        })

    items.sort(key=lambda it: it['cantidad_a_comprar'], reverse=True)

    return {
        'mes': mes,
        'total_articulos': len(items),
        'total_requieren_compra': len([it for it in items if it['cantidad_a_comprar'] > 0]),
        'items': items,
    }
