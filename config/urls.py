from django.urls import include, path
from relay.views import health

urlpatterns = [path("health/", health), path("api/", include("relay.urls"))]

