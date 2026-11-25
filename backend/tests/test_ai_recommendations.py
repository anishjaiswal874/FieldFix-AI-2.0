import json
import os
import sys
import unittest
from time import monotonic
from unittest.mock import patch

from sqlmodel import Session, select

import test_auth
from test_requests import photo_bytes
from app import ai_services as ai
from app.models import (
    Category, MaintenanceRequest, MaintenanceTeam, RequestAttachment, RequestStatus, ReviewDecision, User, UserRole, WorkOrder,
)
from app.priority_rules import recommend_priority
from app.request_uploads import MAX_PHOTO_BYTES, get_upload_directory
from main import app


FEATURES = {f"FIELDFIX_AI_{name.upper()}_ENABLED": "1" for name in ai.FEATURES}
FLAGS = "/api/reviewer/recommendations/features"


class RuleAndTransportTests(unittest.TestCase):
    def test_alternative_faults_do_not_become_confirmed_suggestions(self):
        priority = recommend_priority("Reported alternatives", "The outlet is sparking or the sink is actively flooding.")
        self.assertEqual(priority["status"], "abstained")
        self.assertIsNone(priority["priority"])
        self.assertIsNone(ai.eligible_system("The sink is clogged or it is a different issue."))

    def test_scope_requires_supported_system_and_clear_fault(self):
        for text, label in (
            ("The sink is leaking.", "plumbing"),
            ("The thermostat is not cooling the room.", "hvac"),
            ("The light fixture is flickering.", "electrical"),
            ("", None), ("Maintenance needed.", None), ("Please inspect the sink.", None),
            ("Patch the wall near the leaking sink.", None), ("A window is cracked.", None),
            ("The sink is clogged and the thermostat is rattling.", None),
            ("The sink is not leaking.", None), ("The sink was leaking.", None),
            ("Maybe the sink is clogged.", None),
            ("The sink is clean. A passage is blocked.", None),
            ("The sink is clogged but works fine.", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(ai.eligible_system(text), label)

    def test_explainable_priority_and_abstention(self):
        for text, expected in (
            ("The outlet is sparking.", "high"), ("Water is gushing from the pipe.", "high"),
            ("The room has no heat.", "medium"), ("The lift is out of service.", "medium"),
            ("Paint scratches with no functional impact.", "low"),
            ("The outlet is not sparking.", None), ("The outlet wasn't sparking.", None),
            ("The plug was sparking.", None), ("The sink was fixed yesterday.", None),
            ("Could the thermostat be sparking?", None), ("Lights and plumbing", None),
            ("Cosmetic only. The light is not working.", None), ("A scuff on the door.", None),
            ("Sparking investigation complete.", None), ("Service outage, power is back on.", None),
        ):
            with self.subTest(text=text):
                result = recommend_priority("Fixture report", text)
                self.assertEqual(result["priority"], expected)
                self.assertEqual(result["rule_version"], "m13-priority-v1")
                self.assertTrue(result["explanation"])
                self.assertEqual(bool(result["fact"]), expected is not None)

    def test_transport_output_cap_timeout_and_secrets(self):
        with patch.dict(os.environ, {"FIELDFIX_JWT_SECRET": "disposable-secret"}):
            env = ai.worker_environment(os.environ["LOCALAPPDATA"])
        self.assertNotIn("FIELDFIX_JWT_SECRET", env)
        command = [sys.executable, "-I", "-B", "-c"]
        self.assertEqual(ai.exchange(command + ["import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                     b"fixture", 4, env), b"fixture")
        self.assertIsNone(ai.exchange(command + ["import sys; sys.stdout.buffer.write(b'x' * 8192)"],
                                      b"x", 4, env))
        started = monotonic()
        self.assertIsNone(ai.exchange(command + ["import time; time.sleep(15)"], b"x", 0.2, env))
        self.assertLess(monotonic() - started, 6)

    def test_over_limit_or_busy_does_not_spawn(self):
        with patch.dict(os.environ, FEATURES), patch.object(ai, "exchange") as exchange:
            self.assertIsNone(ai.run_worker("nlp", b"x" * 4001))
            self.assertIsNone(ai.run_worker("cv", b"x" * (MAX_PHOTO_BYTES + 1)))
            with ai.SLOTS["nlp"]:
                self.assertIsNone(ai.run_worker("nlp", b"fixture"))
            exchange.assert_not_called()


class RecommendationTests(test_auth.ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uploads = cls.test_directory / "uploads"
        cls.uploads.mkdir()
        app.dependency_overrides[get_upload_directory] = lambda: cls.uploads
        cls.addClassCleanup(app.dependency_overrides.pop, get_upload_directory)
        with Session(cls.engine) as session:
            category = Category(name="Plumbing")
            team = MaintenanceTeam(name="Repairs", specialty="Facilities")
            session.add_all([category, team])
            session.commit()
            cls.category_id, cls.team_id = category.id, team.id

    def account(self, label, role=UserRole.REQUESTER, inactive=False):
        credentials, profile = self.register(label)
        user_id = profile["id"]
        if role != UserRole.REQUESTER:
            with Session(self.engine) as session:
                session.get(User, user_id).role = role
                session.commit()
        token = self.login(credentials)
        if inactive:
            with Session(self.engine) as session:
                session.get(User, user_id).is_active = False
                session.commit()
        return token, user_id

    def fixture(self, label, description="The sink is clogged."):
        requester, owner = self.account(f"{label}-owner")
        reviewer, reviewer_id = self.account(f"{label}-reviewer", UserRole.REVIEWER)
        with Session(self.engine) as session:
            record = MaintenanceRequest(requester_id=owner, title="Report", description=description, location="A")
            session.add(record)
            session.commit()
            return requester, reviewer, reviewer_id, record.id

    def attach(self, request_id, data, *, content_type="image/jpeg", size=None):
        with Session(self.engine) as session:
            row = RequestAttachment(request_id=request_id, original_filename="fixture.jpg",
                                    content_type=content_type, size_bytes=len(data) if size is None else size)
            (self.uploads / row.stored_filename).write_bytes(data)
            session.add(row)
            session.commit()
            return row.id

    def post(self, path, token=None, body=None):
        return self.call(path, body=body, token=token, method="POST")

    def counts(self, request_id):
        with Session(self.engine) as session:
            return (
                session.get(MaintenanceRequest, request_id).status,
                len(session.exec(select(ReviewDecision).where(ReviewDecision.request_id == request_id)).all()),
                len(session.exec(select(WorkOrder).where(WorkOrder.request_id == request_id)).all()),
            )

    def test_role_guards_and_wrong_attachment_before_worker(self):
        requester, reviewer, _, record = self.fixture("role")
        _, _, _, other = self.fixture("other")
        admin, _ = self.account("ai-admin", UserRole.ADMIN)
        deactivated, _ = self.account("ai-inactive", UserRole.REVIEWER, inactive=True)
        attachment = self.attach(record, photo_bytes("JPEG"))
        base = f"/api/reviewer/requests/{record}"
        screening = f"{base}/attachments/{attachment}/screening?concrete_closeup=true"
        with patch.dict(os.environ, FEATURES), patch.object(ai, "run_worker") as worker:
            for token, status in ((None, 401), (requester, 403), (admin, 403), (deactivated, 401)):
                self.assertEqual(self.call(FLAGS, token=token)[0], status)
                for route in (f"{base}/recommendations/nlp", f"{base}/recommendations/nlp-demo",
                              f"{base}/recommendations/priority", screening):
                    self.assertEqual(self.post(route, token)[0], status)
            self.assertEqual(self.post(f"/api/reviewer/requests/{other}/attachments/{attachment}/screening?concrete_closeup=true",
                                       reviewer)[0], 404)
            self.assertEqual(self.post(f"{base}/attachments/0/screening", reviewer)[0], 422)
            self.assertEqual(self.post("/api/reviewer/requests/0/recommendations/nlp", reviewer)[0], 422)
            self.assertEqual(self.post("/api/reviewer/requests/99999/recommendations/nlp", reviewer)[0], 404)
            worker.assert_not_called()
        self.assertEqual(self.call(f"{base}/attachments/{attachment}", token=None)[0], 401)
        self.assertEqual(self.call(f"{base}/attachments/{attachment}", token=reviewer)[1]["Cache-Control"], "private, no-store")

    def test_flags_fail_closed_without_workers_or_ml_environments(self):
        _, reviewer, _, record = self.fixture("disabled")
        attachment = self.attach(record, photo_bytes("JPEG"))
        base = f"/api/reviewer/requests/{record}"
        with patch.dict(os.environ), patch.object(ai, "run_worker") as worker:
            for k in FEATURES:
                os.environ.pop(k, None)
            os.environ["LOCALAPPDATA"] = str(self.test_directory / "missing-ml")
            for malformed in (None, "true", "yes", " 1", "2", ""):
                if malformed is not None:
                    os.environ.update(dict.fromkeys(FEATURES, malformed))
                self.assertEqual(self.call(FLAGS, token=reviewer)[2], dict.fromkeys(ai.FEATURES, False))
                for path in ("/recommendations/nlp", "/recommendations/nlp-demo", "/recommendations/priority",
                             f"/attachments/{attachment}/screening?concrete_closeup=true"):
                    code, headers, body = self.post(base + path, reviewer)
                    self.assertEqual((code, headers["Cache-Control"], body["status"]), (200, "private, no-store", "disabled"))
            worker.assert_not_called()
            self.assertEqual(self.call(f"{base}", token=reviewer)[0], 200)

    def test_nlp_demo_is_independent_and_never_returns_a_category_binding(self):
        _, reviewer, _, record = self.fixture("demo")
        _, _, _, mixed = self.fixture("demo-mixed", "The sink is clogged and the air conditioner is rattling.")
        _, _, _, unsupported = self.fixture("demo-roof", "The roof needs patching.")
        base = f"/api/reviewer/requests/{record}/recommendations/"
        flags = {"FIELDFIX_AI_NLP_ENABLED": "0", "FIELDFIX_AI_NLP_DEMO_ENABLED": "1"}
        with patch.dict(os.environ, flags), patch.object(ai, "run_worker", return_value={"label": "plumbing"}) as worker:
            self.assertEqual(self.post(base + "nlp", reviewer)[2]["status"], "disabled")
            for case in (mixed, unsupported):
                result = self.post(f"/api/reviewer/requests/{case}/recommendations/nlp-demo", reviewer)[2]
                self.assertEqual(result["status"], "abstained")
            worker.assert_not_called()

            # Even a matching approved binding or rollout cannot activate the demo output.
            binding = {"version": ai.BINDING_VERSION, "model": ai.NLP_MODEL,
                       "categories": {"plumbing": {"id": self.category_id, "name": "Plumbing"}}}
            with patch.object(ai, "NLP_ROLLOUT_APPROVED", True), patch.dict(os.environ, FIELDFIX_AI_NLP_BINDINGS=json.dumps(binding)):
                status, headers, result = self.post(base + "nlp-demo", reviewer, {"description": "Ignored"})
            self.assertEqual((status, headers["Cache-Control"]), (200, "private, no-store"))
            worker.assert_called_once_with("nlp_demo", b"The sink is clogged.")
            self.assertEqual((result["status"], result["actionable"], result["label_name"]), ("research_only", False, "Plumbing"))
            self.assertNotIn("category", result)
            self.assertNotIn("confidence", result)
            for output, state in (({"label": "electrical"}, "abstained"), ({"label": None}, "abstained"), (None, "unavailable")):
                worker.return_value = output
                self.assertEqual(self.post(base + "nlp-demo", reviewer)[2]["status"], state)
        self.assertEqual(self.counts(record), ("submitted", 0, 0))

    def test_nlp_stored_input_abstention_and_catalog_drift(self):
        _, reviewer, _, record = self.fixture("nlp")
        _, _, _, mixed = self.fixture("mixed", "The sink is clogged and the air conditioner is rattling.")
        base = f"/api/reviewer/requests/{record}/recommendations/nlp"
        binding = {"version": ai.BINDING_VERSION, "model": ai.NLP_MODEL,
                   "categories": {"plumbing": {"id": self.category_id, "name": "Plumbing"}}}
        with patch.dict(os.environ, FEATURES), patch.object(ai, "run_worker", return_value={"label": "plumbing"}) as worker:
            os.environ.pop("FIELDFIX_AI_NLP_BINDINGS", None)
            self.assertEqual(self.post(f"/api/reviewer/requests/{mixed}/recommendations/nlp", reviewer)[2]["status"], "abstained")
            worker.assert_not_called()
            result = self.post(base, reviewer, {"description": "Replacement from browser"})[2]
            worker.assert_called_once_with("nlp", b"The sink is clogged.")
            self.assertEqual((result["status"], result["actionable"]), ("research_only", False))
            self.assertNotIn("category", result)
            os.environ["FIELDFIX_AI_NLP_BINDINGS"] = json.dumps(binding)
            result = self.post(base, reviewer)[2]
            self.assertFalse(result["actionable"])
            self.assertIn("pending validation", result["explanation"])
            try:
                for name, active in (("Plumbing", False), ("Renamed Plumbing", True)):
                    with Session(self.engine) as session:
                        category = session.get(Category, self.category_id)
                        category.name, category.is_active = name, active
                        session.commit()
                    result = self.post(base, reviewer)[2]
                    self.assertEqual((result["status"], result["actionable"]), ("research_only", False))
                    self.assertIn("No current approved", result["explanation"])
            finally:
                with Session(self.engine) as session:
                    category = session.get(Category, self.category_id)
                    category.name, category.is_active = "Plumbing", True
                    session.commit()
            worker.return_value = {"label": "electrical"}
            self.assertEqual(self.post(base, reviewer)[2]["status"], "abstained")
            worker.return_value = None
            self.assertEqual(self.post(base, reviewer)[2]["status"], "unavailable")
        self.assertEqual(self.counts(record), ("submitted", 0, 0))

    def test_cv_validates_actual_still_jpeg_and_requires_confirmation(self):
        _, reviewer, _, record = self.fixture("cv")
        jpeg = photo_bytes("JPEG")
        good = self.attach(record, jpeg)
        bad = [
            self.attach(record, b"wrong bytes"),
            self.attach(record, photo_bytes("PNG")),
            self.attach(record, photo_bytes("PNG"), content_type="image/png"),
            self.attach(record, jpeg[:-24]),
            self.attach(record, jpeg, size=MAX_PHOTO_BYTES + 1),
        ]

        def endpoint(attachment, query=""):
            return f"/api/reviewer/requests/{record}/attachments/{attachment}/screening{query}"

        query = "?concrete_closeup=true"
        with patch.dict(os.environ, FEATURES), patch.object(ai, "run_worker", return_value={"crack_detected": True}) as worker:
            for suffix in ("", "?concrete_closeup=1", "?concrete_closeup=false", "?concrete_closeup=true&concrete_closeup=true"):
                self.assertEqual(self.post(endpoint(good, suffix), reviewer)[2]["status"], "unsuitable")
            for item in bad:
                self.assertEqual(self.post(endpoint(item, query), reviewer)[2]["status"], "unsuitable")
            with patch("app.ai_routes.MAX_IMAGE_PIXELS", 100):
                self.assertEqual(self.post(endpoint(good, query), reviewer)[2]["status"], "unsuitable")
            worker.assert_not_called()
            result = self.post(endpoint(good, query), reviewer, {"path": "client-controlled"})[2]
            worker.assert_called_once_with("cv", jpeg)
            self.assertEqual((result["status"], result["outcome"]), ("screened", "possible_crack"))
            self.assertIn("qualified person", result["limitation"])
            worker.return_value = {"crack_detected": False}
            self.assertEqual(self.post(endpoint(good, query), reviewer)[2]["outcome"], "threshold_not_met")
            worker.return_value = None
            self.assertEqual(self.post(endpoint(good, query), reviewer)[2]["status"], "unavailable")
        self.assertEqual(self.counts(record), ("submitted", 0, 0))

    def test_worker_failure_leaves_manual_decision_and_priority_available(self):
        _, reviewer, _, record = self.fixture("manual")
        attachment = self.attach(record, photo_bytes("JPEG"))
        url = f"/api/reviewer/requests/{record}"
        with patch.dict(os.environ, {**FEATURES, "LOCALAPPDATA": str(self.test_directory / "no-ml")}):
            self.assertEqual(self.post(url + "/recommendations/nlp", reviewer)[2]["status"], "unavailable")
            self.assertEqual(self.post(url + f"/attachments/{attachment}/screening?concrete_closeup=true", reviewer)[2]["status"],
                             "unavailable")
            self.assertEqual(self.post(url + "/recommendations/priority", reviewer)[2]["status"], "abstained")
            self.assertEqual(self.call("/api/reviewer/requests", token=reviewer)[0], 200)
            decision = {"decision": "approved", "final_category_id": self.category_id, "final_priority": "medium",
                        "assigned_team_id": self.team_id}
            self.assertEqual(self.post(url + "/decisions", reviewer, decision)[0], 201)
            self.assertEqual(self.post(url + "/recommendations/nlp", reviewer)[0], 409)
        self.assertEqual(self.counts(record), ("approved", 1, 1))

    def test_rechecks_current_reviewer_after_inference(self):
        _, reviewer, reviewer_id, record = self.fixture("revoked")

        def revoke(_profile, _payload):
            with Session(self.engine) as session:
                session.get(User, reviewer_id).is_active = False
                session.commit()
            return {"label": "plumbing"}

        with patch.dict(os.environ, FEATURES), patch.object(ai, "run_worker", side_effect=revoke):
            self.assertEqual(self.post(f"/api/reviewer/requests/{record}/recommendations/nlp", reviewer)[0], 401)

    def test_discards_screening_when_request_status_changes(self):
        _, reviewer, _, record = self.fixture("stale-screen")
        attachment = self.attach(record, photo_bytes("JPEG"))

        def change_status(_profile, _payload):
            with Session(self.engine) as session:
                session.get(MaintenanceRequest, record).status = RequestStatus.NEEDS_INFO
                session.commit()
            return {"crack_detected": True}

        with patch.dict(os.environ, FEATURES), patch.object(ai, "run_worker", side_effect=change_status):
            route = f"/api/reviewer/requests/{record}/attachments/{attachment}/screening?concrete_closeup=true"
            self.assertEqual(self.post(route, reviewer)[0], 409)
        self.assertEqual(self.counts(record), ("needs_info", 0, 0))
