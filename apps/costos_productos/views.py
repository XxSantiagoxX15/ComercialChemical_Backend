# src/apps/costos/views.py  (ajusta la ruta según tu proyecto)
import json
import ast
from typing import Any, Dict

import logging

logger = logging.getLogger(__name__)


from decimal import Decimal

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions, generics

from django.db import models as dj_models

from .models import PriceSnapshot, CostosConfig
from .serializers import PriceSnapshotSerializer, CostosConfigSerializer

# OPTIONAL: si existe el modelo Articulo en app inventario, lo usamos para
# resolver la FK y para el listado de artículos.
try:
    from apps.inventario.models import Articulo
except Exception:
    Articulo = None


def _to_decimal_safe(x, default=Decimal('0')):
    try:
        if x is None or x == '':
            return default
        return Decimal(str(x))
    except Exception:
        return default


def _get_or_create_config(config_data):
    """
    Resuelve (y si hace falta, crea/actualiza) la CostosConfig a partir de
    lo que venga en el payload del mismo insert. Acepta tres formas:

      - None                         -> no hay config, se guarda el snapshot sin config.
      - "nombre_config" (str)        -> busca una config YA existente con ese nombre
                                         (no la crea si no existe).
      - {"name": "...", "overhead_pct": ..., "import_cost_pct": ...,
         "tax_pct": ..., "margin_pct": ...}  (dict)
                                         -> crea la config si no existe, o la
                                         actualiza si ya existe con ese nombre.
                                         Así el insert de precio (single o bulk)
                                         guarda TODO en una sola llamada, sin
                                         necesitar pegarle antes a /config/.
    """
    if not config_data:
        return None

    if isinstance(config_data, str):
        return CostosConfig.objects.filter(name=config_data).first()

    if isinstance(config_data, dict):
        name = config_data.get("name") or "default"
        defaults = {
            "overhead_pct": _to_decimal_safe(config_data.get("overhead_pct", 0)),
            "import_cost_pct": _to_decimal_safe(config_data.get("import_cost_pct", 0)),
            "tax_pct": _to_decimal_safe(config_data.get("tax_pct", 0)),
            "margin_pct": _to_decimal_safe(config_data.get("margin_pct", 0)),
        }
        config_obj, _created = CostosConfig.objects.update_or_create(name=name, defaults=defaults)
        return config_obj

    return None


class CostosConfigListCreateAPIView(generics.ListCreateAPIView):
    """
    GET  /api/costos/config/   -> lista todas las configuraciones
    POST /api/costos/config/   -> crea una nueva configuración

    Este endpoint queda como utilitario opcional (para listar/administrar
    configs por separado). El flujo normal ya NO depende de él: los
    endpoints de guardar precio (single/bulk) crean o actualizan la config
    solos si el payload trae el objeto "config" completo.
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = CostosConfig.objects.all().order_by('name')
    serializer_class = CostosConfigSerializer


class CostosConfigDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/costos/config/<pk>/   -> detalle
    PUT    /api/costos/config/<pk>/   -> reemplaza
    PATCH  /api/costos/config/<pk>/   -> actualiza parcial
    DELETE /api/costos/config/<pk>/   -> elimina
    """
    permission_classes = [permissions.IsAuthenticated]
    queryset = CostosConfig.objects.all()
    serializer_class = CostosConfigSerializer


class SaveSinglePriceAPIView(APIView):
    """
    POST /api/costos/compute/<producto_id>/save/

    El frontend ya calculó todo. Body esperado:
    {
        "quantity": 10,
        "base_unit_cost": 1234.56,
        "calculated_total": 12345.60,
        "final_unit_price": 1500.00,   // también se acepta "final_price"
        "final_total_value": 15000.00,
        "breakdown": {...},            // opcional, cualquier JSON

        // "config" acepta un string (nombre de config ya existente) o un
        // objeto con los porcentajes — si es objeto, se crea/actualiza la
        // CostosConfig en el mismo insert, sin necesitar otra llamada:
        "config": {
            "name": "estandar",
            "overhead_pct": 0.10,
            "import_cost_pct": 0.05,
            "tax_pct": 0.19,
            "margin_pct": 0.25
        },
        "note": "..."                  // opcional
    }
    Solo inserta (y de paso guarda/actualiza la config si viene como objeto).
    No recalcula nada.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, producto_id):
        data = request.data or {}

        try:
            producto_id = int(producto_id)
        except (TypeError, ValueError):
            return Response({"detail": "producto_id inválido."}, status=status.HTTP_400_BAD_REQUEST)

        note = data.get("note", "") or ""

        try:
            config_obj = _get_or_create_config(data.get("config"))
            articulo_obj = Articulo.objects.filter(pk=producto_id).first() if Articulo else None

            snap = PriceSnapshot.objects.create(
                articulo=articulo_obj,
                articulo_id_number=producto_id,
                config=config_obj,
                base_unit_cost=_to_decimal_safe(data.get("base_unit_cost")),
                quantity=_to_decimal_safe(data.get("quantity")),
                calculated_total=_to_decimal_safe(data.get("calculated_total")),
                final_price=_to_decimal_safe(data.get("final_unit_price", data.get("final_price"))),
                final_total_value=_to_decimal_safe(data.get("final_total_value")),
                breakdown=data.get("breakdown") or {},
                note=note,
            )
            serializer = PriceSnapshotSerializer(snap)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("Error guardando snapshot single")
            return Response(
                {"detail": "Error guardando snapshot: " + str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class BulkSavePriceAPIView(APIView):
    """
    POST /api/costos/compute/bulk/save/

    El frontend ya calculó todo para cada producto. Body esperado:
    {
        "items": [
            {
                "producto_id": 123,
                "quantity": 10,
                "base_unit_cost": 1234.56,
                "calculated_total": 12345.60,
                "final_unit_price": 1500.00,   // también se acepta "final_price"
                "final_total_value": 15000.00,
                "breakdown": {...}             // opcional
            },
            ...
        ],

        // igual que en el single: string (config existente) u objeto
        // (se crea/actualiza en el mismo insert). Se aplica a TODOS los
        // items del batch.
        "config": {
            "name": "estandar",
            "overhead_pct": 0.10,
            "import_cost_pct": 0.05,
            "tax_pct": 0.19,
            "margin_pct": 0.25
        },
        "note": "..."                // opcional, aplica a todos los items
    }
    Inserta un PriceSnapshot por cada item de la lista (y de paso guarda/
    actualiza la config si viene como objeto). No recalcula nada.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        data = request.data or {}
        items = data.get("items", []) or []
        note = data.get("note", "") or ""

        config_obj = _get_or_create_config(data.get("config"))

        saved = []
        for item in items:
            try:
                producto_id = int(item.get("producto_id"))
            except (TypeError, ValueError):
                saved.append({"producto_id": item.get("producto_id"), "error": "producto_id inválido"})
                continue

            try:
                articulo_obj = Articulo.objects.filter(pk=producto_id).first() if Articulo else None

                snap = PriceSnapshot.objects.create(
                    articulo=articulo_obj,
                    articulo_id_number=producto_id,
                    config=config_obj,
                    base_unit_cost=_to_decimal_safe(item.get("base_unit_cost")),
                    quantity=_to_decimal_safe(item.get("quantity")),
                    calculated_total=_to_decimal_safe(item.get("calculated_total")),
                    final_price=_to_decimal_safe(item.get("final_unit_price", item.get("final_price"))),
                    final_total_value=_to_decimal_safe(item.get("final_total_value")),
                    breakdown=item.get("breakdown") or {},
                    note=note,
                )
                saved.append(PriceSnapshotSerializer(snap).data)

            except Exception as e:
                logger.exception("Error procesando bulk save para producto_id=%s", producto_id)
                saved.append({"producto_id": producto_id, "error": str(e)})

        return Response({"saved": saved}, status=status.HTTP_201_CREATED)


class PriceSnapshotListAPIView(APIView):
    """
    GET /api/costos/pricing/snapshots/<producto_id>/
    Devuelve el historial de snapshots guardados para un artículo (los
    últimos 50, más recientes primero).
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, producto_id):
        if Articulo:
            snaps = PriceSnapshot.objects.filter(
                dj_models.Q(articulo__id=producto_id) | dj_models.Q(articulo_id_number=producto_id)
            )
        else:
            snaps = PriceSnapshot.objects.filter(articulo_id_number=producto_id)

        snaps = snaps.order_by("-computed_at")[:50]
        return Response(PriceSnapshotSerializer(snaps, many=True).data)


def pick_field(model, candidates, allow_relations=False):
    """Devuelve el primer nombre de campo de `candidates` que exista en el modelo."""
    if model is None:
        return None
    field_names = set()
    for f in model._meta.get_fields():
        if getattr(f, 'concrete', False) or (allow_relations and getattr(f, 'is_relation', False)):
            field_names.add(f.name)
    for c in candidates:
        if c in field_names:
            return c
    return None


class ArticuloListAPIView(APIView):
    """
    GET /api/costos/articulos/
    Lista mínima de artículos para que el frontend pueda hacer sus propios
    cálculos de precio — ahora incluyendo campos opcionales de costeo.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if Articulo is None:
            return Response(
                {"detail": "Modelo Articulo no disponible en backend."},
                status=status.HTTP_501_NOT_IMPLEMENTED
            )

        # candidatos ya existentes
        codigo_cands = ['codigo', 'sku', 'ref', 'referencia']
        descripcion_cands = ['descripcion', 'nombre', 'title', 'titulo']
        stock_cands = ['cantidad_existencia', 'stock_actual', 'stock', 'cantidad']

        # candidatos NUEVOS para campos de coste / snapshot que mencionaste
        costo_cands = ['costo_unitario', 'unit_cost', 'cost', 'precio_base']
        import_cands = ['import_cost_total', 'import_total', 'import_cost']
        overhead_cands = ['overhead_pct', 'overhead', 'costos_indirectos']
        tax_cands = ['tax_pct', 'tax', 'impuesto_pct', 'impuesto']
        margin_cands = ['margin_pct', 'margin', 'margen_pct', 'margen']
        precio_unit_cands = ['precio_unitario_final', 'final_unit_price', 'precio_unitario']
        precio_total_cands = ['precio_total', 'final_total_value', 'total_price']
        breakdown_cands = ['breakdown', 'breakdawn', 'breakdown_data']  # cuidado con typo 'breakdawn'
        created_at_cands = ['created_at', 'computed_at', 'snapshot_created_at', 'fecha_creacion']

        # resolver campos reales en el modelo
        codigo_field = pick_field(Articulo, codigo_cands) or 'id'
        descripcion_field = pick_field(Articulo, descripcion_cands) or ''
        stock_field = pick_field(Articulo, stock_cands) or ''

        costo_field = pick_field(Articulo, costo_cands) or ''
        import_field = pick_field(Articulo, import_cands) or ''
        overhead_field = pick_field(Articulo, overhead_cands) or ''
        tax_field = pick_field(Articulo, tax_cands) or ''
        margin_field = pick_field(Articulo, margin_cands) or ''
        precio_unit_field = pick_field(Articulo, precio_unit_cands) or ''
        precio_total_field = pick_field(Articulo, precio_total_cands) or ''
        breakdown_field = pick_field(Articulo, breakdown_cands) or ''
        created_at_field = pick_field(Articulo, created_at_cands) or ''

        # armar lista de fields para .values()
        values_fields = ['id']
        for f in [codigo_field, descripcion_field, stock_field, costo_field, import_field,
                  overhead_field, tax_field, margin_field, precio_unit_field,
                  precio_total_field, breakdown_field, created_at_field]:
            if f and f not in values_fields:
                values_fields.append(f)

        try:
            qs = Articulo.objects.all().values(*values_fields)
        except Exception as e:
            logger.exception("Error consultando artículos")
            return Response({"detail": "Error consultando artículos: " + str(e)},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        def normalize_value(v: Any):
            """Convierte Decimal -> float, bytes -> str, JSON-string -> dict, deja dict/list tal cual."""
            try:
                if v is None:
                    return None
                if isinstance(v, Decimal):
                    # convertir Decimal a float para frontend (puedes cambiarlo a str si prefieres preservar precisión)
                    return float(v)
                if isinstance(v, (int, float, bool)):
                    return v
                if isinstance(v, (dict, list)):
                    return v
                if isinstance(v, bytes):
                    v = v.decode('utf-8', errors='ignore')
                if isinstance(v, str):
                    s = v.strip()
                    # try valid JSON first (dobles comillas)
                    if (s.startswith('{') or s.startswith('[') or s.startswith('"')):
                        try:
                            return json.loads(s)
                        except Exception:
                            # fallback: puede ser un dict en formato Python (comillas simples)
                            try:
                                return ast.literal_eval(s)
                            except Exception:
                                # no es JSON ni dict python, dejar string
                                return s
                    # it's just a plain string or number-like
                    # si es numérico en string, intentar convertir
                    try:
                        if '.' in s:
                            return float(s.replace(',', '.'))
                        return int(s)
                    except Exception:
                        return s
                return v
            except Exception:
                return v

        result = []

        # --- debug: imprimir sample del queryset crudo (valores) ---
        try:
            sample_raw = list(qs[:3])
            logger.debug("ArticuloListAPIView -> raw qs sample (first 3): %s", json.dumps(sample_raw, default=str, ensure_ascii=False, indent=2))
        except Exception:
            logger.exception("Error imprimiendo sample_raw qs")

        for r in qs:
            item: Dict[str, Any] = {
                'id': r.get('id'),
                'codigo': r.get(codigo_field) if codigo_field else r.get('id'),
                'descripcion': r.get(descripcion_field) if descripcion_field else '',
                'cantidad_existencia': normalize_value(r.get(stock_field)) or 0,
            }

            # campos opcionales (sólo incluir si existen)
            if costo_field:
                # único canonical name que usa frontend: 'costo_unitario'
                v = normalize_value(r.get(costo_field))
                item['costo_unitario'] = v or 0

            if import_field:
                v = normalize_value(r.get(import_field))
                # devolver con nombre "import_cost_total" que usa el frontend
                item['import_cost_total'] = v or 0
                # también mantener alias por si otra parte del frontend mira otro nombre
                item['import_total'] = v or 0
                item['import_cost'] = v or 0

            if overhead_field:
                v = normalize_value(r.get(overhead_field))
                # algunos frontends esperan 'overhead' y otros 'overhead_pct'
                item['overhead'] = v or 0
                item['overhead_pct'] = v or 0

            if tax_field:
                v = normalize_value(r.get(tax_field))
                # garantizar tanto 'tax' como 'tax_pct' (frontend acepta ambas)
                item['tax'] = v if v is not None else 0
                item['tax_pct'] = v if v is not None else 0

            if margin_field:
                v = normalize_value(r.get(margin_field))
                item['margin'] = v if v is not None else 0
                item['margin_pct'] = v if v is not None else 0

            if precio_unit_field:
                v = normalize_value(r.get(precio_unit_field))
                item['precio_unitario_final'] = v if v is not None else None

            if precio_total_field:
                v = normalize_value(r.get(precio_total_field))
                item['precio_total'] = v if v is not None else None

            if breakdown_field:
                item['breakdown'] = normalize_value(r.get(breakdown_field)) or {}

            if created_at_field:
                item['created_at'] = normalize_value(r.get(created_at_field))

            # ENRICH (opcional): tratar de leer el último snapshot y mezclar valores
            # para que el frontend no tenga que llamar a /snapshots/ por separado.
            try:
                snaps_qs = PriceSnapshot.objects.filter(
                    dj_models.Q(articulo__id=item['id']) | dj_models.Q(articulo_id_number=item['id'])
                ).order_by('-computed_at')
                latest_snap = snaps_qs.first()
                if latest_snap:
                    # base_unit_cost -> costo_unitario si no está presente o es cero
                    if (item.get('costo_unitario') is None or item.get('costo_unitario') == 0) and latest_snap.base_unit_cost is not None:
                        item['costo_unitario'] = float(latest_snap.base_unit_cost)
                    # final price/total
                    if latest_snap.final_price is not None:
                        item['precio_unitario_final'] = float(latest_snap.final_price)
                    if latest_snap.final_total_value is not None:
                        item['precio_total'] = float(latest_snap.final_total_value)
                    # breakdown (si es JSONField ya viene como dict, si string intentamos parsear)
                    if latest_snap.breakdown:
                        if isinstance(latest_snap.breakdown, (dict, list)):
                            item['breakdown'] = latest_snap.breakdown
                        else:
                            try:
                                item['breakdown'] = json.loads(latest_snap.breakdown)
                            except Exception:
                                try:
                                    item['breakdown'] = ast.literal_eval(latest_snap.breakdown)
                                except Exception:
                                    item['breakdown'] = latest_snap.breakdown
                    # computed_at -> snapshot_computed_at (informativo)
                    if hasattr(latest_snap, 'computed_at'):
                        item['snapshot_computed_at'] = normalize_value(latest_snap.computed_at)
                    # si el snapshot contiene porcentajes explícitos (tax_pct/margin_pct), mapearlos también
                    if getattr(latest_snap, 'config', None):
                        try:
                            cfg = latest_snap.config
                            item['tax_pct'] = float(getattr(cfg, 'tax_pct', item.get('tax') or item.get('tax_pct', 19)))
                            item['margin_pct'] = float(getattr(cfg, 'margin_pct', item.get('margin') or item.get('margin_pct', 15)))
                        except Exception:
                            pass
            except Exception:
                logger.exception("Error leyendo snapshot para articulo id=%s", item.get('id'))

            result.append(item)

        # --- debug: imprimir el payload que se enviará al frontend ---
        try:
            # imprimimos muestra completa (puedes ajustar truncate si es muy grande)
            logger.debug("ArticuloListAPIView -> response payload (first 50 items): %s",
                         json.dumps(result[:50], default=str, ensure_ascii=False, indent=2))
        except Exception:
            # fallback con print para garantizar salida en consola en caso de fallo del logger
            try:
                print("ArticuloListAPIView -> response payload (debug print fallback):")
                print(json.dumps(result[:50], default=str, ensure_ascii=False)[:20000])
            except Exception:
                logger.exception("Error imprimiendo result para debugging")

        return Response(result)