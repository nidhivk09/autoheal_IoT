from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.analytics, name="analytics"),
    path("chart-data/", views.chart_data, name="chart_data"),
    path("batch-trial/start/", views.start_batch_trial, name="start_batch_trial"),
    path("export/csv/", views.csv_export, name="csv_export"),
]
