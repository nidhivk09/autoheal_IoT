"""
Internal API consumed only by the remediation Azure Function
(cloud/functions/remediation_dispatcher/remediation_logic.py). All writes to
Incident timestamp fields go through here so there's exactly one code path
that mutates the research dataset, per the build plan.

Not part of the public dashboard API - protected by InternalTokenAuthentication,
never exposed to the browser.
"""
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .internal_auth import InternalTokenAuthentication
from .models import Incident
from .serializers import IncidentSerializer, IncidentUpdateSerializer


@api_view(["GET"])
@authentication_classes([InternalTokenAuthentication])
@permission_classes([IsAuthenticated])
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

    incident = (
        Incident.objects.filter(device__device_id=device_id, verified_healthy_at__isnull=True)
        .order_by("-injected_at")
        .first()
    )
    if incident is None:
        return Response(status=404)

    return Response(IncidentSerializer(incident).data)


@api_view(["PATCH"])
@authentication_classes([InternalTokenAuthentication])
@permission_classes([IsAuthenticated])
def update_incident(request, incident_id: int):
    try:
        incident = Incident.objects.get(id=incident_id)
    except Incident.DoesNotExist:
        return Response(status=404)

    serializer = IncidentUpdateSerializer(incident, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(IncidentSerializer(incident).data)
