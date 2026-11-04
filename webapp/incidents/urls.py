from django.urls import path

from . import views

app_name = "incidents"

urlpatterns = [
    path("", views.injection_panel, name="panel"),
    path("inject/", views.inject, name="inject"),
    path("log/", views.log, name="log"),
    path("<int:incident_id>/", views.detail, name="detail"),
    path("<int:incident_id>/status/", views.detail_status_partial, name="detail_status"),
    path("<int:incident_id>/resolve/", views.resolve_manually, name="resolve"),
    path("<int:incident_id>/approve/", views.approve_action, name="approve"),
]
