from django.urls import path
from .views import AuditoriaListView

urlpatterns = [
    path('eventos/', AuditoriaListView.as_view()),
]
