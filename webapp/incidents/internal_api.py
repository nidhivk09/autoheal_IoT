"""
Internal API consumed by the remediation Azure Function
(cloud/functions/shared_code/remediation_logic.py) and the AKS ingestion
service (cloud/ingestion_service/main.py). All writes to Incident timestamp
fields from outside Django go through here so there's exactly one code path
that mutates the research dataset, per the build plan.

Not part of the public dashboard API - protected by InternalTokenAuthentication,
never exposed to the browser.
"""
from datetime import timedelta

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from devices.models import Device
from devices.services import push_desired_config

from .internal_auth import InternalTokenAuthentication
from .models import FaultType, Incident, RemediationAction, RemediationMode, Tier
from .serializers import IncidentSerializer, IncidentUpdateSerializer, RemediationActionSerializer
from .services import active_incident_for, apply_recovery_event

# Ladder bookkeeping steps that don't touch the device; not counted by the circuit breaker.
NON_REMEDIATING_ACTIONS = ["probe", "escalate", "request_approval"]


def internal_view(methods):
    def decorate(fn):
        fn = permission_classes([IsAuthenticated])(fn)
        fn = authentication_classes([InternalTokenAuthentication])(fn)
        return api_view(methods)(fn)
    return decorate


@internal_view(["GET"])
def active_incident(request):
    """Returns the most recent unresolved Incident for a device.

    "Unresolved" = verified_healthy_at is still null. Only one incident
    should be active per device at a time in this project's trial-by-trial
    workflow, but if more than one exists (e.g. a stuck trial), the most
    recently injected one wins.
    """
    device_id = request.query_params.get("device_id")
    if not device_id:
        return Response({"error": "device_id is required"}, status=400)

    incident = active_incident_for(get_object_or_404(Device, device_id=device_id))
    if incident is None:
        return Response(status=404)
    return Response(IncidentSerializer(incident).data)


@internal_view(["POST"])
def create_organic_incident(request):
    """A cloud detection with no trial running: record it as an organic
    incident so real failures are remediated and counted too."""
    device = get_object_or_404(Device, device_id=request.data.get("device_id"))
    detected_at = parse_datetime(request.data.get("detected_at") or "") or timezone.now()
    incident = Incident.objects.create(
        device=device,
        fault_type=FaultType.ORGANIC,
        remediation_mode=request.data.get("remediation_mode", RemediationMode.AUTOMATED),
        edge_healing=device.desired_edge_healing,
        injected_at=detected_at,
        detected_at=detected_at,
        detected_by=Tier.CLOUD,
        detection_signal=request.data.get("detection_signal", ""),
    )
    return Response(IncidentSerializer(incident).data, status=201)


@internal_view(["GET", "PATCH"])
def incident_detail(request, incident_id: int):
    with transaction.atomic():
        incident = get_object_or_404(Incident.objects.select_for_update(), id=incident_id)
        if request.method == "GET":
            return Response(IncidentSerializer(incident).data)

        # First tier to verify recovery wins; a late claim must not overwrite it.
        if incident.verified_healthy_at and "verified_healthy_at" in request.data:
            return Response({"error": "incident already resolved", **IncidentSerializer(incident).data}, status=409)

        serializer = IncidentUpdateSerializer(incident, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
    return Response(IncidentSerializer(incident).data)


@internal_view(["POST"])
def claim_incident(request, incident_id: int):
    """Atomically marks an incident as being remediated by the cloud, so two
    detections for the same incident can't start two remediation ladders."""
    claimed = Incident.objects.filter(
        id=incident_id, remediation_started_at__isnull=True, verified_healthy_at__isnull=True,
    ).update(remediation_started_at=timezone.now())
    if not claimed:
        return Response({"claimed": False}, status=409)
    return Response({"claimed": True})


@internal_view(["POST"])
def record_action(request, incident_id: int):
    incident = get_object_or_404(Incident, id=incident_id)
    serializer = RemediationActionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    serializer.save(incident=incident)
    return Response(serializer.data, status=201)


@internal_view(["GET"])
def recent_cloud_actions(request, device_id: str):
    """Circuit-breaker input: how many cloud remediation actions this device
    has had in the last window_s seconds."""
    window = int(request.query_params.get("window_s", 1800))
    count = RemediationAction.objects.filter(
        incident__device__device_id=device_id,
        tier=Tier.CLOUD,
        started_at__gte=timezone.now() - timedelta(seconds=window),
    ).exclude(action__in=NON_REMEDIATING_ACTIONS).count()
    return Response({"device_id": device_id, "window_s": window, "count": count})


@internal_view(["POST"])
def push_config(request, device_id: str):
    device = get_object_or_404(Device, device_id=device_id)
    push_desired_config(device, reconcile=bool(request.data.get("reconcile")))
    return Response({"pushed": device.desired_config()})


@internal_view(["POST"])
def recovery_event(request):
    """Firmware "recovered" events, forwarded by the ingestion service."""
    device = get_object_or_404(Device, device_id=request.data.get("device_id"))
    event = request.data.get("event") or {}
    if event.get("event") != "recovered":
        return Response({"error": "unsupported event"}, status=400)
    incident = apply_recovery_event(device, event)
    return Response({"incident_id": incident.id if incident else None})
