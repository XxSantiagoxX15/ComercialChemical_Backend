# apps/pricing/services.py
from decimal import Decimal, ROUND_HALF_UP
from django.db import connection
from .models import CostosConfig

def _to_dec(x):
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal('0')

def round_dec(v, places=2):
    q = Decimal(10) ** -places
    return v.quantize(q, rounding=ROUND_HALF_UP)

def _table_has_table(table_name: str) -> bool:
    """Check if a table exists in current database."""
    with connection.cursor() as c:
        c.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_name = %s
        """, [table_name])
        r = c.fetchone()
    return bool(r and r[0])

def _get_columns_for_table(table_name: str):
    """Return list of column names for a table in current database."""
    with connection.cursor() as c:
        c.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
        """, [table_name])
        rows = c.fetchall()
    return [r[0] for r in rows] if rows else []

def get_latest_unit_cost_from_articulo(articulo_id):
    """
    Intenta leer el costo unitario y la cantidad de la tabla 'articulo'
    de forma segura: detecta columnas existentes y selecciona las que existan.
    Devuelve (costo_unitario_decimal, cantidad_decimal)
    """
    table = 'articulo'  # ajusta si tu tabla se llama distinto (ej. 'articulos')
    if not _table_has_table(table):
        # no existe la tabla -> devolvemos ceros
        return Decimal('0'), Decimal('0')

    cols = _get_columns_for_table(table)
    # posibles nombres que buscamos en orden de preferencia
    costo_candidates = ['costo_unitario', 'costo', 'unit_cost', 'cost', 'precio', 'price']
    qty_candidates = ['cantidad_existencia', 'stock_actual', 'cantidad', 'qty', 'stock', 'cantidad_stock']

    costo_field = next((c for c in costo_candidates if c in cols), None)
    qty_field = next((c for c in qty_candidates if c in cols), None)

    # construimos SELECT dinámico pero seguro: si no existe la columna ponemos literal 0
    select_parts = []
    if costo_field:
        select_parts.append(f"{costo_field} as costo_unitario")
    else:
        select_parts.append("0 as costo_unitario")
    if qty_field:
        select_parts.append(f"{qty_field} as cantidad_existencia")
    else:
        select_parts.append("0 as cantidad_existencia")

    sql = f"SELECT {', '.join(select_parts)} FROM {table} WHERE id = %s LIMIT 1"
    with connection.cursor() as c:
        c.execute(sql, [articulo_id])
        row = c.fetchone()

    if not row:
        return Decimal('0'), Decimal('0')

    costo_val, qty_val = row[0], row[1]
    return _to_dec(costo_val), _to_dec(qty_val)


def get_importation_prorated_cost(articulo_id):
    """
    Intenta sumar un campo de importación prorrateada desde 'importacion_detalle'
    si la tabla existe y si tiene una columna de valor. Si no, devuelve 0.
    """
    table = 'importacion_detalle'
    if not _table_has_table(table):
        return Decimal('0')

    cols = _get_columns_for_table(table)
    # buscar un candidate razonable que represente valor/valor_total/valor_unitario
    value_candidates = ['valor', 'valor_total', 'total', 'monto', 'amount', 'value']
    value_field = next((c for c in value_candidates if c in cols), None)
    # buscar columna de relacion con articulo
    articulo_candidates = ['articulo_id', 'articulo', 'product_id', 'id_articulo']
    articulo_field = next((c for c in articulo_candidates if c in cols), None)

    if not value_field or not articulo_field:
        return Decimal('0')

    sql = f"SELECT SUM({value_field}) FROM {table} WHERE {articulo_field} = %s"
    with connection.cursor() as c:
        c.execute(sql, [articulo_id])
        row = c.fetchone()
    return _to_dec(row[0]) if row and row[0] is not None else Decimal('0')


def calculate_price_for_product(articulo_id, quantity=None, config_name=None, overrides=None):
    """
    Calcula el precio y devuelve un dict. Usa las funciones seguras anteriores
    para leer costo y prorrateo de importación (no rompe si columnas cambian).
    """
    overrides = overrides or {}
    cfg = None
    if config_name:
        try:
            cfg = CostosConfig.objects.get(name=config_name)
        except CostosConfig.DoesNotExist:
            cfg = None

    overhead_pct = _to_dec(overrides.get("overhead_pct") or (cfg.overhead_pct if cfg else 0))
    import_cost_pct = _to_dec(overrides.get("import_cost_pct") or (cfg.import_cost_pct if cfg else 0))
    tax_pct = _to_dec(overrides.get("tax_pct") or (cfg.tax_pct if cfg else 0))
    margin_pct = _to_dec(overrides.get("margin_pct") or (cfg.margin_pct if cfg else 0))

    base_unit_cost, stock_qty = get_latest_unit_cost_from_articulo(articulo_id)
    if quantity is None:
        quantity = stock_qty if stock_qty and stock_qty != 0 else Decimal('1')

    # import cost absoluto (ejemplo)
    import_cost = get_importation_prorated_cost(articulo_id)
    import_cost_per_unit = import_cost / Decimal(quantity) if Decimal(quantity) and Decimal(quantity) != 0 else Decimal('0')

    subtotal = base_unit_cost + import_cost_per_unit
    overhead = subtotal * overhead_pct
    subtotal_with_overhead = subtotal + overhead
    tax = subtotal_with_overhead * tax_pct
    subtotal_with_tax = subtotal_with_overhead + tax
    margin = subtotal_with_tax * margin_pct
    final_unit_price = subtotal_with_tax + margin

    # redondeos
    base_unit_cost_r = round_dec(base_unit_cost, 4)
    calculated_total = round_dec(base_unit_cost_r * Decimal(quantity), 2)
    final_unit_price_r = round_dec(final_unit_price, 2)
    final_total_value = round_dec(final_unit_price_r * Decimal(quantity), 2)

    breakdown = {
        "base_unit_cost": str(base_unit_cost_r),
        "import_cost_per_unit": str(round_dec(import_cost_per_unit, 4)),
        "overhead": str(round_dec(overhead, 4)),
        "tax": str(round_dec(tax, 4)),
        "margin": str(round_dec(margin, 4)),
        "subtotal": str(round_dec(subtotal, 4)),
        "subtotal_with_overhead": str(round_dec(subtotal_with_overhead, 4)),
        "subtotal_with_tax": str(round_dec(subtotal_with_tax, 4)),
    }

    return {
        "articulo_id": articulo_id,
        "config": cfg.name if cfg else None,
        "base_unit_cost": str(base_unit_cost_r),
        "quantity": float(quantity),
        "calculated_total": str(calculated_total),
        "final_unit_price": str(final_unit_price_r),
        "final_total_value": str(final_total_value),
        "breakdown": breakdown
    }