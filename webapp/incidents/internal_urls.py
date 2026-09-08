from django.urls import path

from . import internal_api

app_name = "incidents_internal"

urlpatterns = [
    path("incidents/active/", internal_api.active_incident, name="active"),
    path("incidents/<int:incident_id>/", internal_api.update_incident, name="update"),
]
