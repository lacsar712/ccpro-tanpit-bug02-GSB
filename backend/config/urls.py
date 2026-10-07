from django.urls import path
from pits.api import api

urlpatterns = [
    path("api/", api.urls),
]
