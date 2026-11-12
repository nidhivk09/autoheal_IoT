from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    path("", views.device_list, name="list"),
    path("partial/", views.device_list_partial, name="list_partial"),
    path("firmware/", views.firmware, name="firmware"),
    path("firmware/rollout/", views.start_rollout, name="start_rollout"),
    path("<str:device_id>/", views.device_detail, name="detail"),
    path("<str:device_id>/config/", views.update_config, name="update_config"),
    path("<str:device_id>/metrics.json", views.device_metrics, name="metrics"),
]
