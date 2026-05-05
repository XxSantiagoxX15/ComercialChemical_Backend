"""
Servicios de inventario — lógica ACID portada del microservicio Chem Movid.
Usa select_for_update() para evitar race conditions (equivalente al FOR UPDATE de PostgreSQL).
"""
from django.db import transaction
from django.db.models import F, ExpressionWrapper, FloatField
from rest_framework.exceptions import ValidationError
from .models import Articulo, MovimientoInventario, Kit, KitComponente


class ArticuloService:

    @staticmethod
    def listar(buscar=None, categoria=None, page=1, limit=50):
        qs = Articulo.objects.filter(activo=True)
        if buscar:
            qs = qs.filter(descripcion__icontains=buscar) | qs.filter(codigo__icontains=buscar)
        if categoria:
            qs = qs.filter(categoria__icontains=categoria)
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
            return Articulo.objects.get(id=pk)
        except Articulo.DoesNotExist:
            raise ValidationError('Artículo no encontrado.')

    @staticmethod
    def actualizar(pk: int, data: dict) -> Articulo:
        articulo = ArticuloService.obtener(pk)
        campos = ['descripcion', 'categoria', 'unidad_medida',
                  'stock_minimo', 'stock_maximo', 'tiempo_reposicion_dias', 'activo']
        for campo in campos:
            if campo in data:
                setattr(articulo, campo, data[campo])
        articulo.save()
        return articulo

    @staticmethod
    def alertas_stock_critico():
        """Req 6.1-6.5 Chem Movid: artículos con stock <= stock_minimo, ordenados por urgencia."""
        return (
            Articulo.objects.filter(activo=True, stock_minimo__gt=0)
            .filter(stock_actual__lte=F('stock_minimo'))
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
                  usuario=None, observaciones: str = None, forzar: bool = False):
        """
        Req 4.3-4.6 Chem Movid: transacción ACID con bloqueo de fila.
        - entrada: incrementa stock
        - salida: decrementa; si deficit y forzar=False → ValidationError
        - ajuste: establece stock en valor absoluto de cantidad
        - produccion_kit: decrementar (lo llama KitService)
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

        return MovimientoInventario.objects.create(
            articulo=articulo,
            tipo_movimiento=tipo,
            cantidad=cantidad,
            stock_anterior=stock_anterior,
            stock_resultante=stock_resultante,
            usuario=usuario,
            observaciones=observaciones,
        )

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
    @transaction.atomic
    def crear(codigo: str, descripcion: str, componentes: list) -> Kit:
        """Req 5.1 Chem Movid."""
        if not componentes:
            raise ValidationError('El kit debe tener al menos un componente.')
        if Kit.objects.filter(codigo=codigo).exists():
            raise ValidationError(f"Ya existe un kit con el código '{codigo}'.")

        kit = Kit.objects.create(codigo=codigo, descripcion=descripcion)
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
    def producir(kit_id: int, cantidad: int, usuario=None, forzar: bool = False) -> dict:
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
                usuario=usuario,
                observaciones=f'Producción de kit {kit.codigo} x{cantidad}',
                forzar=True,  # ya validamos arriba
            )
            movimientos.append(mov)

        return {
            'kit_id': kit_id,
            'cantidad_producida': cantidad,
            'movimientos_generados': len(movimientos),
        }
