"""
Módulos del sistema: catálogo, módulos por defecto de cada rol y qué módulo
da acceso a cada ruta de la API.

Todo el control de acceso por módulos vive en esta app. Las demás apps no
cambian: siguen usando IsAdmin, IsAdminOrInventario e IsAdminOrCompras, y son
esas clases (permissions.py) las que consultan este archivo.

La tabla `permiso_rol` es la fuente de verdad: el Administrador la edita desde
Usuarios → Permisos por rol y se consulta en cada petición, así que un cambio
aplica de inmediato, sin que el usuario tenga que volver a iniciar sesión.
"""
import re

ROL_ADMIN = 'Administrador'

# Módulos que nunca se pueden habilitar a un rol distinto del Administrador.
# Gestionar usuarios permite crear cuentas y asignar roles, así que darlo a
# otro rol equivaldría a darle la posibilidad de volverse Administrador.
MODULOS_SOLO_ADMIN = frozenset({'usuarios'})

# Módulos con los que arranca cada rol mientras el Administrador no los cambie.
MODULOS_DEFAULT = {
    'Administrador': [
        'dashboard', 'inventario_articulos', 'calculadora_precios',
        'inventario_movimientos', 'inventario_alertas', 'compras',
        'pronosticos', 'importacion', 'auditoria', 'usuarios',
    ],
    'Analista_Inventarios': [
        'dashboard', 'inventario_articulos', 'inventario_movimientos',
        'inventario_alertas', 'pronosticos',
    ],
    'Responsable_Compras': [
        'dashboard', 'compras', 'inventario_alertas', 'pronosticos',
    ],
}

DESCRIPCION_ROLES = {
    'Administrador': 'Acceso completo al sistema',
    'Analista_Inventarios': 'Gestión de inventario, alertas y pronósticos',
    'Responsable_Compras': 'Órdenes de compra, proveedores y cotizaciones',
}

# ── Qué módulo da acceso a cada ruta ─────────────────────────────────────────
# (métodos HTTP o None para todos, expresión regular de la ruta, módulos).
# Basta con tener UNO de los módulos listados. Gana la primera regla que
# coincide, así que las más específicas van antes que las generales.
#
# Una ruta que no aparece aquí conserva su permiso original por nombre de rol
# (por ejemplo, crear y editar artículos o producir kits siguen siendo solo
# del Administrador).
ACCESO_POR_RUTA = [
    # Inventario. El listado de artículos lo usan también los buscadores de
    # Movimientos, Compras y Pronósticos y el contador del Dashboard.
    (('GET',), r'^/api/inventario/articulos/$',
        ('inventario_articulos', 'inventario_movimientos', 'compras', 'pronosticos', 'dashboard')),
    (('GET',), r'^/api/inventario/articulos/\d+/lotes/$',
        ('inventario_articulos', 'inventario_movimientos')),
    (None,     r'^/api/inventario/movimientos/$',           ('inventario_movimientos',)),
    (('GET',), r'^/api/inventario/alertas-stock-critico/$', ('inventario_alertas',)),
    (('GET',), r'^/api/inventario/alertas-vencimiento/$',   ('inventario_alertas',)),
    (('GET',), r'^/api/inventario/kits/$',                  ('pronosticos', 'inventario_articulos')),
    (('GET',), r'^/api/inventario/kits/producibles/$',      ('pronosticos',)),
    (('GET',), r'^/api/inventario/resumen-stock-mensual/$', ('dashboard', 'pronosticos')),

    # Compras: órdenes, proveedores, catálogo de precios y cotizador.
    (None, r'^/api/compras/', ('compras',)),

    # Pronósticos. Las recomendaciones y el EOQ también las ve Compras;
    # convertir una recomendación en orden es una acción de Compras.
    (('POST',), r'^/api/pronosticos/recomendaciones/\d+/generar-orden/$', ('compras',)),
    (('GET',),  r'^/api/pronosticos/recomendaciones/$',                   ('pronosticos', 'compras')),
    (('GET',),  r'^/api/pronosticos/eoq/\d+/$',                           ('pronosticos', 'compras')),
    (None,      r'^/api/pronosticos/',                                    ('pronosticos',)),

    # Importación.
    (('POST',), r'^/api/importacion/upload/$',    ('importacion',)),
    (('GET',),  r'^/api/importacion/historial/$', ('importacion',)),
    (('GET',),  r'^/api/importacion/ventas/$',    ('pronosticos',)),

    # Auditoría.
    (('GET',), r'^/api/auditoria/eventos/$', ('auditoria',)),
]

ACCESO_PARCIAL = [
    # "No me interesa" y "Reactivar" de la pantalla Alertas usan el PUT del
    # artículo: quien tenga el módulo de alertas puede enviarlo, pero
    # únicamente con el campo alerta_ignorada.
    (('PUT',), r'^/api/inventario/articulos/\d+/$', ('inventario_alertas',), ('alerta_ignorada',)),
]

_REGLAS = [(metodos, re.compile(patron), modulos) for metodos, patron, modulos in ACCESO_POR_RUTA]
_REGLAS_PARCIALES = [
    (metodos, re.compile(patron), modulos, frozenset(campos))
    for metodos, patron, modulos, campos in ACCESO_PARCIAL
]


def modulos_de_ruta(metodo: str, ruta: str, campos=None):
    """Módulos que dan acceso a esa petición, o None si la ruta no está
    gobernada por módulos. `campos` son los nombres de los datos enviados: solo
    se usan para las reglas de acceso parcial."""
    for metodos, patron, modulos in _REGLAS:
        if (metodos is None or metodo in metodos) and patron.match(ruta):
            return modulos
    if campos:
        for metodos, patron, modulos, permitidos in _REGLAS_PARCIALES:
            if metodo in metodos and patron.match(ruta) and set(campos) <= permitidos:
                return modulos
    return None


def todos_los_modulos() -> list:
    from .models import PermisoRol
    return [clave for clave, _ in PermisoRol.MODULOS]


def modulos_de_rol(rol) -> set:
    """Módulos activos de un rol. El Administrador siempre los tiene todos.
    Si a un rol le falta la fila de algún módulo en `permiso_rol`, se usa su
    valor por defecto, de modo que el sistema funciona aunque nadie haya
    abierto todavía la pantalla de permisos."""
    from .models import PermisoRol

    catalogo = set(todos_los_modulos())
    if rol is None:
        return set()
    if rol.nombre == ROL_ADMIN:
        return catalogo

    activos = set(MODULOS_DEFAULT.get(rol.nombre, []))
    for modulo, activo in PermisoRol.objects.filter(rol=rol).values_list('modulo', 'activo'):
        if activo:
            activos.add(modulo)
        else:
            activos.discard(modulo)
    return (activos & catalogo) - MODULOS_SOLO_ADMIN


def inicializar_permisos() -> None:
    """Crea en `permiso_rol` las filas que falten, con el valor por defecto de
    cada rol. No toca las que ya existen."""
    from .models import PermisoRol, Rol

    for rol in Rol.objects.all():
        defaults = MODULOS_DEFAULT.get(rol.nombre, [])
        for modulo in todos_los_modulos():
            PermisoRol.objects.get_or_create(
                rol=rol, modulo=modulo,
                defaults={'activo': modulo in defaults},
            )