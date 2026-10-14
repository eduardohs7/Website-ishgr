import csv
import io
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.api_protocol import request_data
from accounts.models import User
from operations.models import AuditEntry
from operations.queries import registration_queryset
from registrations.models import Registration
from registrations.services import register
from registrations.tests import factories


class RegistrationExportApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("exporter@example.org", is_staff=True)
        cls.owner = factories.user()
        cls.other = factories.user("other@example.org")
        cls.event = factories.event()
        cls.price = factories.price(cls.event, requires_review=True)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.second, _ = register(user=cls.other, event_id=cls.event.pk, price_id=cls.price.pk)
        foreign_event = factories.event("foreign-event")
        foreign_price = factories.price(foreign_event)
        cls.foreign, _ = register(user=cls.owner, event_id=foreign_event.pk, price_id=foreign_price.pk)

    def setUp(self):
        self.client.force_login(self.actor)
        self.grant("view_registration", "export_registration")

    def grant(self, *codes):
        self.actor.user_permissions.set(Permission.objects.filter(content_type__app_label="registrations", codename__in=codes))

    def post(self, data=None, client=None, **extra):
        return (client or self.client).post(reverse("management_api:export"), data or {}, content_type="application/json", **extra)

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_anonymous_inactive_and_nonstaff_are_denied_without_csv(self):
        self.assertEqual(self.post(client=Client()).status_code, 401)
        User.objects.filter(pk=self.actor.pk).update(is_staff=False)
        response = self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "permission_denied")
        User.objects.filter(pk=self.actor.pk).update(is_staff=True, is_active=False)
        self.assertEqual(self.post().status_code, 401)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_export_requires_both_permissions_and_staff_alone_is_insufficient(self):
        for codes in ((), ("view_registration",), ("export_registration",), ("view_registration_indicators",),
                      ("view_registration", "review_registration", "cancel_registration")):
            self.grant(*codes)
            response = self.post()
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertNotContains(response, self.owner.email, status_code=403)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_existing_export_group_authorizes_export_without_granting_review(self):
        self.grant()
        call_command("setup_registration_roles", stdout=io.StringIO())
        self.actor.groups.add(Group.objects.get(name="CHAGS — Exportação de inscrições"))
        self.assertEqual(self.post().status_code, 200)
        data = self.client.get(reverse("management_api:session")).json()["data"]
        self.assertTrue(data["permissions"]["export_registrations"])
        self.assertFalse(data["permissions"]["review_category"])
        self.assertFalse(data["permissions"]["cancel_registration"])

    def test_bootstrap_capability_requires_view_plus_export(self):
        self.assertTrue(self.client.get(reverse("management_api:session")).json()["data"]["permissions"]["export_registrations"])
        self.grant("export_registration")
        self.assertFalse(self.client.get(reverse("management_api:session")).json()["data"]["permissions"]["export_registrations"])

    def test_success_has_download_headers_bom_and_only_current_event_rows(self):
        response = self.post()
        rows = self.rows(response)
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(response["Content-Disposition"], 'attachment; filename="chags14-inscricoes.csv"')
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 9 for row in rows))
        self.assertEqual({row[0] for row in rows[1:]}, {str(self.registration.pk), str(self.second.pk)})
        self.assertNotContains(response, str(self.foreign.pk))
        self.assertNotContains(response, self.owner.password)
        self.assertEqual(rows[1][6:8], ["123.45", "BRL"])
        entry = AuditEntry.objects.get(action="registration.exported")
        self.assertEqual(entry.actor_id, self.actor.pk)
        self.assertEqual(entry.target_id, str(self.event.pk))
        self.assertEqual(entry.details, {"rows": 2, "status": "", "review": "", "search_applied": False})

    def test_every_successful_generation_is_audited_including_repeats(self):
        for _ in range(2):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(AuditEntry.objects.filter(action="registration.exported").count(), 2)

    def test_legacy_and_api_csv_have_identical_content(self):
        api = self.post({"q": self.owner.email})
        legacy = self.client.post(reverse("operations:exports"), {"q": self.owner.email})
        self.assertEqual(api.content, legacy.content)
        self.assertEqual(api["Content-Disposition"], legacy["Content-Disposition"])
        self.assertEqual(AuditEntry.objects.filter(action="registration.exported").count(), 2)

    def test_email_name_and_uuid_search_match_the_list_and_never_save_search_text(self):
        User.objects.filter(pk=self.owner.pk).update(full_name="Pessoa Distinta")
        for query in (self.owner.email.upper(), "distinta", str(self.registration.pk)):
            rows = self.rows(self.post({"q": query}))
            listing = self.client.get(reverse("management_api:registrations"), {"q": query}).json()["data"]
            self.assertEqual({row[0] for row in rows[1:]}, {row["id"] for row in listing["registrations"]})
            entry = AuditEntry.objects.filter(action="registration.exported").latest("created_at")
            self.assertTrue(entry.details["search_applied"])
            self.assertNotIn(query, str(entry.details))
            self.assertNotIn("q", entry.details)

    def test_status_and_review_filters_are_combined(self):
        Registration.objects.filter(pk=self.second.pk).update(category_review="rejected")
        rows = self.rows(self.post({"status": "pending", "review": "rejected"}))
        self.assertEqual([row[0] for row in rows[1:]], [str(self.second.pk)])
        entry = AuditEntry.objects.get(action="registration.exported")
        self.assertEqual(entry.details["status"], "pending")
        self.assertEqual(entry.details["review"], "rejected")

    def test_zero_matches_return_headers_and_audit_zero_rows_for_current_event(self):
        rows = self.rows(self.post({"q": str(self.foreign.pk)}))
        self.assertEqual(len(rows), 1)
        entry = AuditEntry.objects.get(action="registration.exported")
        self.assertEqual(entry.details["rows"], 0)
        self.assertEqual(entry.target_id, str(self.event.pk))

    def test_snapshot_category_survives_catalog_rename(self):
        category = self.price.category
        category.name_pt = "New catalog name"
        category.save(update_fields=["name_pt"])
        rows = self.rows(self.post({"q": str(self.registration.pk)}))
        self.assertEqual(rows[1][3], self.registration.category_name)

    def test_formulas_controls_quotes_and_commas_do_not_create_extra_columns(self):
        for name in ('=HYPERLINK("https://example.org", "x")', " \t+1", "\ufeff@SUM(1)", "\x1f-1"):
            User.objects.filter(pk=self.owner.pk).update(full_name=name)
            row = self.rows(self.post({"q": str(self.registration.pk)}))[1]
            self.assertEqual(len(row), 9)
            self.assertTrue(row[1].startswith("'"))
        User.objects.filter(pk=self.owner.pk).update(full_name='Pessoa, "Ação"\nSegunda linha')
        row = self.rows(self.post({"q": str(self.registration.pk)}))[1]
        self.assertEqual(row[1], 'Pessoa, "Ação" Segunda linha')
        self.assertEqual(len(row), 9)

    def test_invalid_filters_return_json_fields_and_no_audit(self):
        for data in ({"status": "paid"}, {"review": "bad"}, {"q": "x" * 256}):
            response = self.post(data)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "validation_error")
            self.assertIn("fields", response.json()["error"])
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_unknown_event_pagination_or_format_fields_and_query_params_are_rejected(self):
        for field in ("event_id", "user", "actor", "page", "page_size", "format", "amount", "filename"):
            response = self.post({field: "injected"})
            self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        response = self.client.post(reverse("management_api:export") + "?status=pending", {}, content_type="application/json")
        self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_duplicate_keys_invalid_types_and_malformed_json_are_rejected(self):
        for raw in ('{"q":"one","q":"two"}', '{"q":null}', '{"status":true}', '[]', '{broken'):
            response = self.client.post(reverse("management_api:export"), raw, content_type="application/json")
            self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_body_limit_and_unsupported_content_type_do_not_export(self):
        self.assertEqual(self.post({"q": "x" * (33 * 1024)}).status_code, 413)
        response = self.client.post(reverse("management_api:export"), "q=test", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_urlencoded_form_is_supported_and_duplicate_fields_are_rejected(self):
        response = self.client.post(reverse("management_api:export"), "status=pending", content_type="application/x-www-form-urlencoded")
        self.assertEqual(len(self.rows(response)), 3)
        response = self.client.post(reverse("management_api:export"), "status=pending&status=cancelled", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        self.assertEqual(AuditEntry.objects.filter(action="registration.exported").count(), 1)

    def test_get_head_and_put_never_generate_an_export(self):
        url = reverse("management_api:export")
        for method in (self.client.get, self.client.head, self.client.put):
            response = method(url)
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    @override_settings(REGISTRATION_EVENT_CODE="missing-event")
    def test_missing_event_returns_json_404_and_no_csv_or_audit(self):
        response = self.post()
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "event_not_configured")
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    @override_settings(REGISTRATION_EXPORT_MAX_ROWS=1)
    def test_row_limit_requires_narrower_filters_and_never_returns_partial_data(self):
        response = self.post()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "export_limit_exceeded")
        self.assertNotContains(response, self.owner.email, status_code=413)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())
        self.assertEqual(len(self.rows(self.post({"q": str(self.registration.pk)}))), 2)

    @override_settings(REGISTRATION_EXPORT_MAX_ROWS=2)
    def test_exact_row_limit_is_allowed(self):
        self.assertEqual(len(self.rows(self.post())), 3)

    @override_settings(REGISTRATION_EXPORT_MAX_ROWS=1)
    def test_underestimated_initial_count_cannot_bypass_limit(self):
        queryset = registration_queryset(self.event, {})
        with patch.object(queryset, "count", return_value=0):
            with patch("operations.exports.registration_queryset", return_value=queryset):
                response = self.post()
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "export_limit_exceeded")
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_database_failure_in_audit_prevents_delivery(self):
        with patch("operations.exports.audit", side_effect=DatabaseError("Synthetic database failure")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "operation_unavailable")
        self.assertNotContains(response, self.owner.email, status_code=503)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_permissions_revoked_after_initial_check_prevent_generation(self):
        def revoke(request):
            self.actor.user_permissions.clear()
            return request_data(request)
        with patch("operations.api.request_data", side_effect=revoke):
            self.assertEqual(self.post().status_code, 403)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_staff_flag_revoked_after_initial_check_prevents_generation(self):
        def revoke(request):
            User.objects.filter(pk=self.actor.pk).update(is_staff=False)
            return request_data(request)
        with patch("operations.api.request_data", side_effect=revoke):
            self.assertEqual(self.post().status_code, 403)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_csrf_and_cross_origin_are_enforced_before_generating_csv(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        token = client.get(reverse("management_api:session")).json()["data"]["csrf_token"]
        for extra in ({}, {"HTTP_X_CSRFTOKEN": token, "HTTP_ORIGIN": "https://foreign.invalid"}):
            response = self.post(client=client, **extra)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "csrf_failed")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())
        self.assertEqual(self.post(client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)
