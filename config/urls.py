from django.urls import include, path

from relay.views import health_live, health_ready

urlpatterns = [
    path("health/", health_live),
    path("health/live/", health_live),
    path("health/ready/", health_ready),
    path("api/v1/", include("relay.urls")),
    path("api/", include("relay.urls")),
]
