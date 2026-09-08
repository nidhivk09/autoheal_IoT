from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("dashboard.urls")),
    path("devices/", include("devices.urls")),
    path("incidents/", include("incidents.urls")),
    path("internal/", include("incidents.internal_urls")),
]
