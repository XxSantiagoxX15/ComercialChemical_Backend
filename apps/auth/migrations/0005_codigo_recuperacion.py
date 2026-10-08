import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('chem_auth', '0004_modulo_calculadora_precios'),
    ]

    operations = [
        migrations.CreateModel(
            name='CodigoRecuperacion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('codigo_hash', models.CharField(max_length=64)),
                ('fecha_creacion', models.DateTimeField(auto_now_add=True)),
                ('fecha_expiracion', models.DateTimeField()),
                ('intentos', models.PositiveSmallIntegerField(default=0)),
                ('usado', models.BooleanField(default=False)),
                ('usuario', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='codigos_recuperacion', to='chem_auth.usuario')),
            ],
            options={
                'verbose_name': 'Código de recuperación',
                'db_table': 'codigo_recuperacion',
            },
        ),
    ]