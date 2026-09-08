from django.urls import path

from . import views

app_name = "devices"

urlpatterns = [
    path("", views.device_list, name="list"),
    path("partial/", views.device_list_partial, name="list_partial"),
]
