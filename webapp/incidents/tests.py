from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from devices.models import Device

from .models import FaultType, Incident, RemediationAction, RemediationMode, Tier

TOKEN = "test-internal-token"


def epoch(dt):
    return dt.timestamp()


@override_settings(INTERNAL_API_TOKEN=TOKEN)
class InternalApiTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(device_id="esp32-01", display_name="esp32-01")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}

    def trial(self, fault_type=FaultType.TASK_HANG, **kwargs):
        defaults = {"remediation_mode": RemediationMode.AUTOMATED, "edge_healing": True,
                    "injected_at": timezone.now() - timedelta(seconds=10)}
        defaults.update(kwargs)
        return Incident.objects.create(device=self.device, fault_type=fault_type, **defaults)

    def post_event(self, **event):
        body = {"event": "recovered", "healer": "edge", "fw": "1.0.1", "dropped_during": 0, **event}
        return self.client.post(
            "/internal/recovery-events/", {"device_id": "esp32-01", "event": body},
            content_type="application/json", **self.auth,
        )

    def test_requires_token(self):
        resp = self.client.get("/internal/incidents/active/", {"device_id": "esp32-01"})
        self.assertIn(resp.status_code, (401, 403))

    def test_edge_event_resolves_matching_trial(self):
        incident = self.trial()
        t0 = incident.injected_at
        resp = self.post_event(signal="heartbeat_lost", action="restart_app",
                               detected_at=epoch(t0 + timedelta(seconds=5)),
                               remediated_at=epoch(t0 + timedelta(seconds=5.2)),
                               verified_at=epoch(t0 + timedelta(seconds=6)))
        self.assertEqual(resp.status_code, 200)
        incident.refresh_from_db()
        self.assertEqual(incident.resolved_by, Tier.EDGE)
        self.assertEqual(incident.detected_by, Tier.EDGE)
        self.assertAlmostEqual(incident.mttd_seconds, 5, places=2)
        self.assertAlmostEqual(incident.mttr_seconds, 6, places=2)
        self.assertEqual(incident.actions.get().tier, Tier.EDGE)

    def test_event_with_unrelated_signal_is_logged_not_credited(self):
        incident = self.trial(fault_type=FaultType.MEMORY_LEAK)
        self.post_event(signal="link_lost", action="reconnect_backoff",
                        detected_at=epoch(timezone.now()), verified_at=epoch(timezone.now()))
        incident.refresh_from_db()
        self.assertFalse(incident.is_resolved)
        self.assertEqual(incident.actions.count(), 1)

    def test_device_clock_skew_is_clamped_to_injection(self):
        incident = self.trial(fault_type=FaultType.NETWORK_LOSS)
        early = incident.injected_at - timedelta(seconds=1)
        self.post_event(signal="link_lost", healer="none", action="fixed_interval_retry",
                        detected_at=epoch(early), remediated_at=epoch(timezone.now()),
                        verified_at=epoch(timezone.now()), dropped_during=7)
        incident.refresh_from_db()
        self.assertEqual(incident.resolved_by, Tier.SELF)
        self.assertEqual(incident.messages_dropped, 7)
        self.assertGreaterEqual(incident.mttr_seconds, 0)
        self.assertEqual(incident.detected_by, "")  # baseline firmware doesn't count as edge detection

    def test_edge_event_without_trial_creates_organic_incident(self):
        self.post_event(signal="heap_low", action="restart_app",
                        detected_at=epoch(timezone.now()), verified_at=epoch(timezone.now()))
        organic = Incident.objects.get()
        self.assertEqual(organic.fault_type, FaultType.ORGANIC)
        self.assertEqual(organic.resolved_by, Tier.EDGE)

        self.post_event(signal="link_lost", healer="none", action="fixed_interval_retry",
                        detected_at=epoch(timezone.now()), verified_at=epoch(timezone.now()))
        self.assertEqual(Incident.objects.count(), 1)  # self-recoveries alone aren't recorded

    def test_first_tier_to_verify_wins(self):
        incident = self.trial()
        url = f"/internal/incidents/{incident.id}/"
        first = self.client.patch(url, {"verified_healthy_at": timezone.now().isoformat(), "resolved_by": "cloud"},
                                  content_type="application/json", **self.auth)
        self.assertEqual(first.status_code, 200)
        second = self.client.patch(url, {"verified_healthy_at": timezone.now().isoformat(), "resolved_by": "edge"},
                                   content_type="application/json", **self.auth)
        self.assertEqual(second.status_code, 409)
        incident.refresh_from_db()
        self.assertEqual(incident.resolved_by, Tier.CLOUD)

    def test_claim_is_exclusive(self):
        incident = self.trial()
        url = f"/internal/incidents/{incident.id}/claim/"
        self.assertEqual(self.client.post(url, **self.auth).status_code, 200)
        self.assertEqual(self.client.post(url, **self.auth).status_code, 409)

    def test_circuit_breaker_counts_only_remediating_cloud_actions(self):
        incident = self.trial()
        now = timezone.now()
        for action in ("probe", "restart_service", "reboot", "escalate"):
            RemediationAction.objects.create(incident=incident, tier=Tier.CLOUD, action=action, started_at=now)
        RemediationAction.objects.create(incident=incident, tier=Tier.EDGE, action="restart_app", started_at=now)
        resp = self.client.get("/internal/devices/esp32-01/recent-cloud-actions/", {"window_s": 600}, **self.auth)
        self.assertEqual(resp.json()["count"], 2)

    def test_create_organic_incident(self):
        resp = self.client.post("/internal/incidents/", {"device_id": "esp32-01", "detection_signal": "telemetry_stale"},
                                content_type="application/json", **self.auth)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["fault_type"], "organic")
        self.assertEqual(resp.json()["detected_by"], "cloud")

    @mock.patch("incidents.internal_api.push_desired_config")
    def test_push_config_endpoint(self, push):
        resp = self.client.post("/internal/devices/esp32-01/push-config/", {"reconcile": True},
                                content_type="application/json", **self.auth)
        self.assertEqual(resp.status_code, 200)
        push.assert_called_once_with(self.device, reconcile=True)

    def test_ingestion_style_insert_works_with_db_defaults(self):
        """The ingestion service inserts devices with raw SQL; new columns must have DB defaults."""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO devices_device (device_id, display_name, device_type, is_physical, ip_address,
                                            status, last_seen, firmware_version)
                VALUES (%s, %s, 'esp32', TRUE, '', 'online', now(), COALESCE(%s, ''))
                ON CONFLICT (device_id) DO UPDATE
                    SET last_seen = EXCLUDED.last_seen, status = 'online',
                        firmware_version = COALESCE(%s, devices_device.firmware_version)
                RETURNING id, desired_telemetry_interval_s, desired_heap_low_bytes, desired_edge_healing
                """,
                ["esp32-09", "esp32-09", None, None],
            )
            _, interval, heap_low, edge = cursor.fetchone()
        self.assertEqual((interval, heap_low, edge), (5, 70000, True))


class PageTests(TestCase):
    def setUp(self):
        self.device = Device.objects.create(device_id="esp32-01", display_name="Bench board")
        self.incident = Incident.objects.create(
            device=self.device, fault_type=FaultType.BAD_FIRMWARE, remediation_mode=RemediationMode.APPROVAL,
            edge_healing=False, injected_at=timezone.now(), pending_action="telemetry_stale:rollback_firmware",
        )

    def test_pages_render(self):
        for url in [
            reverse("devices:list"), reverse("devices:list_partial"),
            reverse("devices:detail", args=["esp32-01"]), reverse("incidents:panel"),
            reverse("incidents:log") + "?edge_healing=off", reverse("incidents:detail", args=[self.incident.id]),
            reverse("dashboard:analytics"), reverse("dashboard:chart_data"), reverse("dashboard:csv_export"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_firmware_page_survives_missing_storage(self):
        with mock.patch.dict("os.environ", {"FIRMWARE_STORAGE_CONNECTION_STRING": ""}):
            resp = self.client.get(reverse("devices:firmware"))
        self.assertContains(resp, "Could not list firmware releases")

    def test_detail_shows_approval_button(self):
        resp = self.client.get(reverse("incidents:detail", args=[self.incident.id]))
        self.assertContains(resp, "Approve rollback_firmware")

    @mock.patch("controller.fault_injector.inject_fault", return_value=(True, "ok"))
    def test_inject_passes_network_duration(self, inject):
        resp = self.client.post(reverse("incidents:inject"), {
            "device_id": "esp32-01", "fault_type": "network_loss", "remediation_mode": "automated",
            "duration_s": "45",
        })
        self.assertEqual(resp.status_code, 302)
        inject.assert_called_once_with("esp32-01", "network_loss", {"duration_s": 45})
        created = Incident.objects.get(fault_type="network_loss")
        self.assertTrue(created.edge_healing)

    @mock.patch("devices.views.push_desired_config")
    def test_config_form_saves_and_pushes(self, push):
        self.client.post(reverse("devices:update_config", args=["esp32-01"]),
                         {"telemetry_interval_s": "10", "heap_low_bytes": "60000"})
        self.device.refresh_from_db()
        self.assertEqual(self.device.desired_telemetry_interval_s, 10)
        self.assertFalse(self.device.desired_edge_healing)  # unchecked box
        push.assert_called_once()
