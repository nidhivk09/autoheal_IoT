import hmac

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class InternalTokenUser:
    """Stand-in for a Django user - the internal API is service-to-service
    (called by the remediation Azure Function), not a logged-in person."""

    is_authenticated = True


class InternalTokenAuthentication(BaseAuthentication):
    """Requires `Authorization: Bearer <INTERNAL_API_TOKEN>`.

    This endpoint is only meant to be reachable by the remediation Function
    (see cloud/functions/remediation_dispatcher/remediation_logic.py) - it
    writes Incident timestamps that the MTTD/MTTR research numbers depend on,
    so keep INTERNAL_API_TOKEN out of source control and rotate it if it
    ever leaks.
    """

    def authenticate(self, request):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header.removeprefix("Bearer ").strip()
        expected = settings.INTERNAL_API_TOKEN
        if not expected or not hmac.compare_digest(token, expected):
            raise AuthenticationFailed("invalid internal API token")

        return (InternalTokenUser(), None)
