"""
Servicios de inventario — lógica ACID portada del microservicio Chem Movid.
Usa select_for_update() para evitar race conditions (equivalente al FOR UPDATE de PostgreSQL).
"""
import uuid
from decimal import Decimal
from django.db import transaction
from django.db.models import F, ExpressionWrapper, FloatField, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from rest_framework.exceptions import ValidationError
from .models import Articulo, MovimientoInventario, Kit, KitComponente, LoteInventario


class ArticuloService:

    @staticmethod
    def _con_totales_venta():
        """Anota total/valor vendido con un solo Sum() por artículo en vez de
        recalcularlo por artículo en el serializer (evita N+1)."""
        return Articulo.objects.annotate(
            total_vendido_calc=Coalesce(
                Sum('ventas__cantidad_vendida'), Value(Decimal('0')),
                output_field=DecimalField(max_digits=15, decimal_places=3),
            ),
            valor_vendido_calc=Coalesce(
                Sum('ventas__valor_total'), Value(Decimal('0')),
                output_field=DecimalField(max_digits=15, decimal_places=2),
            ),
        )

    @staticmethod
    def listar(buscar=None, categoria=None, tipo_producto=None, page=1, limit=50):
        qs = ArticuloService._con_totales_venta().filter(activo=True)
        if buscar:
            qs = qs.filter(descripcion__icontains=buscar) | qs.filter(codigo__icontains=buscar)
        if categoria:
            qs = qs.filter(categoria__icontains=categoria)
        if tipo_producto:
            qs = qs.filter(tipo_producto=tipo_producto)
        total = qs.count()
        offset = (page - 1) * limit
        return qs.order_by('id')[offset:offset + limit], total

    @staticmethod
    def crear(data: dict) -> Articulo:
        if Articulo.objects.filter(codigo=data['codigo']).exists():
            raise ValidationError(f"Ya existe un artículo con el código '{data['codigo']}'.")
        return Articulo.objects.create(**data)

    @staticmethod
    def obtener(pk: int) -> Articulo:
        try:
            return ArticuloService._con_totales_venta().get(id=pk)
        except Articulo.DoesNotExist:
            raise ValidationError('Artículo no encontrado.')

    @staticmethod
    def actualizar(pk: int, data: dict) -> Articulo:
        articulo = ArticuloService.obtener(pk)
        campos = ['descripcion', 'categoria', 'unidad_medida', 'tipo_producto',
                  'stock_minimo', 'stock_maximo', 'tiempo_reposicion_dias', 'activo',
                  'clasificacion_peligro', 'costo_pedido', 'costo_mantenimiento_anual',
                  'alerta_ignorada']
        if data.get('ficha_seguridad'):
            campos.append('ficha_seguridad')
        for campo in campos:
            if campo in data:
                setattr(articulo, campo, data[campo])
        if 'alerta_ignorada' in data:
            from django.utils import timezone
            articulo.fecha_alerta_ignorada = timezone.now() if data['alerta_ignorada'] else None
        articulo.save()
        return articulo

    @staticmethod
    def alertas_stock_critico(incluir_ignoradas: bool = False):
        """Req 6.1-6.5 Chem Movid: artículos con stock <= stock_minimo, ordenados por urgencia.
        Por defecto excluye los que el usuario marcó como "No me interesa"."""
        qs = Articulo.objects.filter(activo=True, stock_minimo__gt=0)
        if not incluir_ignoradas:
            qs = qs.filter(alerta_ignorada=False)
        return (
            qs.filter(stock_actual__lte=F('stock_minimo'))
            .annotate(
                porcentaje_stock=ExpressionWrapper(
                    F('stock_actual') * 100.0 / (F('stock_minimo') + 0.001),
                    output_field=FloatField()
                )
            )
            .order_by('porcentaje_stock')
        )


class MovimientoService:

    @staticmethod
    @transaction.atomic
    def registrar(articulo_id: int, tipo: str, cantidad: float,
                  usuario_id=None, observaciones: str = None, forzar: bool = False,
                  numero_lote: str = None, fecha_vencimiento=None):
        """
        Req 4.3-4.6 Chem Movid: transacción ACID con bloqueo de fila.
        - entrada: incrementa stock (crea un LoteInventario propio)
        - salida: decrementa; si deficit y forzar=False → ValidationError (consume lotes FIFO por vencimiento)
        - ajuste: establece stock en valor absoluto de cantidad (no toca lotes — ver nota abajo)
        - produccion_kit: decrementar (lo llama KitService; consume lotes FIFO)

        Nota: 'ajuste' fija el stock total sin reconciliar los lotes individuales,
        ya que un ajuste no tiene una semántica clara de "a qué lote pertenece" la
        diferencia. Es una limitación conocida y documentada.
        """
        articulo = Articulo.objects.select_for_update().get(id=articulo_id)

        if not articulo.activo:
            raise ValidationError('El artículo está inactivo.')

        stock_anterior = float(articulo.stock_actual)

        if tipo == 'entrada':
            stock_resultante = stock_anterior + cantidad
        elif tipo in ('salida', 'produccion_kit'):
            if cantidad > stock_anterior and not forzar:
                raise ValidationError({
                    'mensaje': 'Stock insuficiente para la salida solicitada.',
                    'stock_actual': stock_anterior,
                    'cantidad_solicitada': cantidad,
                    'deficit': cantidad - stock_anterior,
                    'accion': 'Envíe forzar=true para proceder de todas formas.',
                })
            stock_resultante = max(0.0, stock_anterior - cantidad)
        elif tipo == 'ajuste':
            stock_resultante = cantidad
        else:
            raise ValidationError(f"tipo_movimiento inválido: {tipo}")

        articulo.stock_actual = stock_resultante
        articulo.save(update_fields=['stock_actual', 'fecha_modificacion'])

        movimiento = MovimientoInventario.objects.create(
            articulo=articulo,
            tipo_movimiento=tipo,
            cantidad=cantidad,
            stock_anterior=stock_anterior,
            stock_resultante=stock_resultante,
            usuario_id=usuario_id,
            observaciones=observaciones,
        )

        if tipo == 'entrada':
            MovimientoService._crear_lote(articulo, movimiento, cantidad, numero_lote, fecha_vencimiento)
        elif tipo in ('salida', 'produccion_kit'):
            MovimientoService._consumir_lotes_fifo(articulo, cantidad)

        return movimiento

    @staticmethod
    def _crear_lote(articulo, movimiento, cantidad, numero_lote=None, fecha_vencimiento=None):
        LoteInventario.objects.create(
            articulo=articulo,
            numero_lote=numero_lote or f"LOTE-{movimiento.id}",
            cantidad_inicial=cantidad,
            cantidad_disponible=cantidad,
            fecha_vencimiento=fecha_vencimiento,
            movimiento_entrada=movimiento,
        )

    @staticmethod
    def _consumir_lotes_fifo(articulo, cantidad):
        """Descuenta `cantidad` de los lotes activos del artículo, empezando por
        el que vence más pronto (FIFO por vencimiento). Si el stock trazado en
        lotes no alcanza a cubrir toda la cantidad (p. ej. stock que existía
        antes de esta funcionalidad), se descuenta lo que haya y se detiene ahí
        — el stock total del artículo (fuente de verdad) ya quedó actualizado."""
        restante = Decimal(str(cantidad))
        lotes = (
            LoteInventario.objects
            .select_for_update()
            .filter(articulo=articulo, activo=True, cantidad_disponible__gt=0)
            .order_by(F('fecha_vencimiento').asc(nulls_last=True), 'fecha_ingreso')
        )
        for lote in lotes:
            if restante <= 0:
                break
            usar = min(lote.cantidad_disponible, restante)
            lote.cantidad_disponible -= usar
            if lote.cantidad_disponible <= 0:
                lote.activo = False
            lote.save(update_fields=['cantidad_disponible', 'activo'])
            restante -= usar

    @staticmethod
    def listar(articulo_id=None, tipo=None, fecha_desde=None, fecha_hasta=None, page=1, limit=50):
        qs = MovimientoInventario.objects.select_related('articulo', 'usuario')
        if articulo_id:
            qs = qs.filter(articulo_id=articulo_id)
        if tipo:
            qs = qs.filter(tipo_movimiento=tipo)
        if fecha_desde:
            qs = qs.filter(fecha_movimiento__gte=fecha_desde)
        if fecha_hasta:
            qs = qs.filter(fecha_movimiento__lte=fecha_hasta)
        total = qs.count()
        offset = (page - 1) * limit
        return qs[offset:offset + limit], total


class KitService:

    @staticmethod
    def listar():
        return Kit.objects.filter(activo=True).prefetch_related('componentes__articulo')

    @staticmethod
    def producibles():
        """Módulo de producción: cuántas unidades de cada kit se pueden armar
        HOY con el stock actual de sus componentes, y cuál componente es el
        cuello de botella (el que primero se agota)."""
        kits = Kit.objects.filter(activo=True).prefetch_related('componentes__articulo')
        resultado = []
        for kit in kits:
            componentes = list(kit.componentes.all())
            if not componentes:
                continue
            limitante, max_producible = None, None
            for comp in componentes:
                requerido = float(comp.cantidad_requerida)
                if requerido <= 0:
                    continue
                disponible = float(comp.articulo.stock_actual)
                posibles = int(disponible // requerido)
                if max_producible is None or posibles < max_producible:
                    max_producible, limitante = posibles, comp

            resultado.append({
                'kit_id': kit.id,
                'codigo': kit.codigo,
                'descripcion': kit.descripcion,
                'max_producible': max_producible or 0,
                'componente_limitante': {
                    'articulo_id': limitante.articulo_id,
                    'codigo': limitante.articulo.codigo,
                    'descripcion': limitante.articulo.descripcion,
                    'stock_disponible': float(limitante.articulo.stock_actual),
                    'requerido_por_unidad': float(limitante.cantidad_requerida),
                } if limitante else None,
            })
        return sorted(resultado, key=lambda x: x['max_producible'])

    @staticmethod
    @transaction.atomic
    def crear(codigo: str, descripcion: str, componentes: list) -> Kit:
        """Req 5.1 Chem Movid."""
        if not componentes:
            raise ValidationError('El kit debe tener al menos un componente.')
        if Kit.objects.filter(codigo=codigo).exists():
            raise ValidationError(f"Ya existe un kit con el código '{codigo}'.")

        kit = Kit.objects.create(codigo=codigo, descripcion=descripcion)

        # Si el código del kit coincide con un artículo del catálogo, lo etiquetamos
        # como 'kit' para que se pueda filtrar/distinguir en la pantalla de Artículos.
        Articulo.objects.filter(codigo=codigo).update(tipo_producto='kit')

        for comp in componentes:
            if not Articulo.objects.filter(id=comp['articulo_id'], activo=True).exists():
                raise ValidationError(f"Artículo {comp['articulo_id']} no encontrado o inactivo.")
            KitComponente.objects.create(
                kit=kit,
                articulo_id=comp['articulo_id'],
                cantidad_requerida=comp['cantidad_requerida'],
            )
        return kit

    @staticmethod
    @transaction.atomic
    def producir(kit_id: int, cantidad: int, usuario_id=None, forzar: bool = False) -> dict:
        """Req 5.3-5.4 Chem Movid: producir kit en transacción atómica."""
        try:
            kit = Kit.objects.prefetch_related('componentes__articulo').get(id=kit_id)
        except Kit.DoesNotExist:
            raise ValidationError('Kit no encontrado.')
        if not kit.activo:
            raise ValidationError('El kit está inactivo.')

        # Verificar stock de todos los componentes
        deficit = []
        for comp in kit.componentes.all():
            requerido = float(comp.cantidad_requerida) * cantidad
            disponible = float(comp.articulo.stock_actual)
            if requerido > disponible:
                deficit.append({
                    'articulo_id': comp.articulo_id,
                    'codigo': comp.articulo.codigo,
                    'descripcion': comp.articulo.descripcion,
                    'requerido': requerido,
                    'disponible': disponible,
                    'deficit': requerido - disponible,
                })

        if deficit and not forzar:
            raise ValidationError({
                'mensaje': 'Stock insuficiente para producir el kit.',
                'cantidad_solicitada': cantidad,
                'componentes_con_deficit': deficit,
                'accion': 'Envíe forzar=true para proceder de todas formas.',
            })

        movimientos = []
        for comp in kit.componentes.all():
            requerido = float(comp.cantidad_requerida) * cantidad
            mov = MovimientoService.registrar(
                articulo_id=comp.articulo_id,
                tipo='produccion_kit',
                cantidad=requerido,
                usuario_id=usuario_id,
                observaciones=f'Producción de kit {kit.codigo} x{cantidad}',
                forzar=True,  # ya validamos arriba
            )
            movimientos.append(mov)

        return {
            'kit_id': kit_id,
            'cantidad_producida': cantidad,
            'movimientos_generados': len(movimientos),
        }


def resumen_stock_mensual(meses_historico: int = 12) -> dict:
    """
    Evolución real del inventario total mes a mes, reconstruida a partir del
    saldo que trae CADA transacción del Kardex (SaldoHistorico) — no solo
    ventas. Para cada artículo se toma el saldo de su ÚLTIMA transacción de
    cada mes (cierre de mes) y se suman todos los artículos activos.

    El mínimo no tiene historial real (es un valor configurado, no algo que
    varíe mes a mes en los datos), así que se devuelve como un único total
    de referencia — el mínimo requerido HOY sumado entre todos los artículos
    activos — para comparar contra la curva de stock real.
    """
    from django.db.models import Sum
    from apps.ventas.models import SaldoHistorico

    articulos_activos_ids = set(Articulo.objects.filter(activo=True).values_list('id', flat=True))

    filas = (
        SaldoHistorico.objects
        .filter(articulo_id__in=articulos_activos_ids)
        .values_list('articulo_id', 'fecha', 'saldo')
        .order_by('fecha')
    )

    # Último saldo por (artículo, mes) — recorriendo en orden de fecha, el
    # último que se ve para esa clave es el cierre de ese mes.
    ultimo_por_articulo_mes: dict = {}
    for articulo_id, fecha, saldo in filas:
        mes = fecha.strftime('%Y-%m')
        ultimo_por_articulo_mes[(articulo_id, mes)] = (fecha, float(saldo))

    total_por_mes: dict = {}
    for (articulo_id, mes), (fecha, saldo) in ultimo_por_articulo_mes.items():
        total_por_mes[mes] = total_por_mes.get(mes, 0.0) + saldo

    meses_ordenados = sorted(total_por_mes.keys())[-meses_historico:]
    stock_mensual = [{'mes': m, 'total': round(total_por_mes[m], 3)} for m in meses_ordenados]

    minimo_total = float(
        Articulo.objects.filter(activo=True).aggregate(t=Sum('stock_minimo'))['t'] or 0
    )

    return {
        'stock_mensual': stock_mensual,
        'minimo_total': round(minimo_total, 3),
        'tiene_datos': len(stock_mensual) > 0,
    }
