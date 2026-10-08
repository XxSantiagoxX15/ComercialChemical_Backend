"""Añade el módulo 'calculadora_precios' a la matriz de permisos por rol.

Queda activo para el Administrador y apagado para los demás roles, hasta que
el Administrador lo habilite desde Usuarios → Permisos por rol.
"""
from django.db import migrations, models


def sembrar(apps, schema_editor):
    Rol = apps.get_model('chem_auth', 'Rol')
    PermisoRol = apps.get_model('chem_auth', 'PermisoRol')
    for rol in Rol.objects.all():
        PermisoRol.objects.get_or_create(
            rol=rol, modulo='calculadora_precios',
            defaults={'activo': rol.nombre == 'Administrador'},
        )


def quitar(apps, schema_editor):
    PermisoRol = apps.get_model('chem_auth', 'PermisoRol')
    PermisoRol.objects.filter(modulo='calculadora_precios').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('chem_auth', '0003_seed_roles_permisos'),
    ]

    operations = [
        migrations.AlterField(
            model_name='permisorol',
            name='modulo',
            field=models.CharField(choices=[('dashboard', 'Dashboard'), ('inventario_articulos', 'Inventario - Artículos'), ('calculadora_precios', 'Calculadora de precios'), ('inventario_movimientos', 'Inventario - Movimientos'), ('inventario_alertas', 'Inventario - Alertas'), ('compras', 'Compras'), ('pronosticos', 'Pronósticos'), ('importacion', 'Importación'), ('auditoria', 'Auditoría'), ('usuarios', 'Usuarios')], max_length=50),
        ),
        migrations.RunPython(sembrar, quitar),
    ]