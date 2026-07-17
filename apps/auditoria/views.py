from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.response import Response
from apps.auth.permissions import IsAdmin
from apps.common import get_pagination_params
from .models import EventoAuditoria


class EventoAuditoriaSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventoAuditoria
        fields = '__all__'


class AuditoriaListView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        tipo = request.query_params.get('tipo_accion')
        usuario_id = request.query_params.get('usuario_id')
        page, limit = get_pagination_params(request, default_limit=50, max_limit=200)

        qs = EventoAuditoria.objects.all()
        if tipo:
            qs = qs.filter(tipo_accion=tipo)
        if usuario_id:
            qs = qs.filter(usuario_id=usuario_id)

        total = qs.count()
        offset = (page - 1) * limit
        eventos = qs[offset:offset + limit]
        return Response({
            'eventos': EventoAuditoriaSerializer(eventos, many=True).data,
            'total': total, 'page': page, 'limit': limit
        })
