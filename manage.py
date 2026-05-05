#!/usr/bin/env python
"""CHEM-MODINV Django management utility."""
import os
import sys

import pymysql
pymysql.install_as_MySQLdb()


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "No se pudo importar Django. Asegúrate de que esté instalado y "
            "el entorno virtual esté activado."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
