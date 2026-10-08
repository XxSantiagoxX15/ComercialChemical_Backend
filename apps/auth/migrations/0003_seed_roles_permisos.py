"""Crea los tres roles del sistema y sus módulos por defecto si no existen.

Hasta ahora los roles se insertaban a mano por SQL; en la base solo existía
'Administrador', así que no se podían crear usuarios con los otros dos roles.
No modifica roles ni permisos que ya estén creados.
"""
from django.db import migrations

from apps.auth.modulos import DESCRIPCION_ROLES, MODULOS_DEFAULT

MODULOS = [
    'dashboard', 'inventario_articulos', 'inventario_movimientos',
    'inventario_alertas', 'compras', 'pronosticos',
    'importacion', 'auditoria', 'usuarios',
]


def sembrar(apps, schema_editor):
    Rol = apps.get_model('chem_auth', 'Rol')
    PermisoRol = apps.get_model('chem_auth', 'PermisoRol')

    for nombre, modulos_activos in MODULOS_DEFAULT.items():
        rol, _ = Rol.objects.get_or_create(
            nombre=nombre,
            defaults={'descripcion': DESCRIPCION_ROLES.get(nombre, '')},
        )
        for modulo in MODULOS:
            PermisoRol.objects.get_or_create(
                rol=rol, modulo=modulo,
                defaults={'activo': modulo in modulos_activos},
            )


def no_hacer_nada(apps, schema_editor):
    # Al revertir no se borran roles: puede haber usuarios que los usen.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('chem_auth', '0002_permisorol'),
    ]

    operations = [
        migrations.RunPython(sembrar, no_hacer_nada),
    ]