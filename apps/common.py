"""Utilidades compartidas entre apps."""
from rest_framework.exceptions import ValidationError


def get_pagination_params(request, default_limit=50, max_limit=500):
    """Parsea page/limit de la query string de forma segura.
    Levanta ValidationError (→ 400) en vez de dejar propagar un ValueError (→ 500)
    si vienen valores no numéricos."""
    try:
        page = int(request.query_params.get('page', 1))
        limit = int(request.query_params.get('limit', default_limit))
    except (TypeError, ValueError):
        raise ValidationError('Los parámetros page y limit deben ser números enteros.')
    if page < 1:
        raise ValidationError('El parámetro page debe ser mayor o igual a 1.')
    if limit < 1:
        raise ValidationError('El parámetro limit debe ser mayor o igual a 1.')
    return page, min(limit, max_limit)
