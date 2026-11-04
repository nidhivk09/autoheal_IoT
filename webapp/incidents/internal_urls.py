from django.urls import path

from . import internal_api

app_name = "incidents_internal"

urlpatterns = [
    path("incidents/", internal_api.create_organic_incident, name="create"),
    path("incidents/active/", internal_api.active_incident, name="active"),
    path("incidents/<int:incident_id>/", internal_api.incident_detail, name="update"),
    path("incidents/<int:incident_id>/claim/", internal_api.claim_incident, name="claim"),
    path("incidents/<int:incident_id>/actions/", internal_api.record_action, name="actions"),
    path("devices/<str:device_id>/recent-cloud-actions/", internal_api.recent_cloud_actions, name="recent_actions"),
    path("devices/<str:device_id>/push-config/", internal_api.push_config, name="push_config"),
    path("recovery-events/", internal_api.recovery_event, name="recovery_event"),
]
