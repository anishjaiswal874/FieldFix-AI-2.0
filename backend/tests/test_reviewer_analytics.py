from unittest.mock import patch

from sqlmodel import Session

import test_auth
from app import ai_services
from app.browser_sessions import query
from app.models import Category, MaintenanceRequest, MaintenanceTeam, RequestAttachment, User, UserRole


API = "/api/reviewer/analytics"


class AnalysisStatusTests(test_auth.ApiTestCase):
    def test_queue_uses_saved_complaint_evidence(self):
        credentials, profile = self.register("analytics-status")
        with Session(self.engine) as session:
            session.get(User, profile["id"]).role = UserRole.REVIEWER
            complaint = MaintenanceRequest(requester_id=profile["id"], title="Leaking sink",
                                           description="The sink is clogged. Water is gushing.", location="Hall")
            unclear = MaintenanceRequest(requester_id=profile["id"], title="Unclear issue",
                                         description="The roof needs patching.", location="Hall")
            team = MaintenanceTeam(name="Status test team", specialty="Plumbing")
            category = Category(name="Status test category", default_team=team)
            session.add_all([complaint, unclear, team, category])
            session.commit()
            complaint_id, unclear_id, team_id, category_id = complaint.id, unclear.id, team.id, category.id
        reviewer = self.login(credentials)
        complaint_path = f"/api/reviewer/requests/{complaint_id}"

        def status(request_id, filters=""):
            code, _, queue = self.call("/api/reviewer/requests" + filters, token=reviewer)
            self.assertEqual(code, 200)
            return next(row["ai_analysis"] for row in queue["items"] if row["id"] == request_id)

        with patch.dict("os.environ", FIELDFIX_AI_PRIORITY_ENABLED="0"):
            disabled = self.call(complaint_path + "/recommendations/priority", {}, reviewer)[2]
            self.assertEqual(disabled["status"], "disabled")
        self.assertEqual(status(complaint_id), "not_analyzed")
        with patch.dict("os.environ", FIELDFIX_AI_PRIORITY_ENABLED="1"):
            result = self.call(complaint_path + "/recommendations/priority", {}, reviewer)[2]
            self.assertEqual(result["priority"], "high")
            self.assertEqual(status(complaint_id), "priority")
            self.assertEqual(status(unclear_id), "not_analyzed")
            result = self.call(f"{API}/requests/{unclear_id}/fused", {}, reviewer)[2]
            self.assertEqual(result["priority"]["status"], "abstained")
            self.assertEqual(status(unclear_id), "abstained")

        worker_result = {"items": [{"label": "plumbing", "margin": 0.5}]}
        with patch.dict("os.environ", FIELDFIX_AI_NLP_DEMO_ENABLED="1"), \
                patch.object(ai_services, "run_worker", return_value=worker_result):
            inspection = API + "/nlp-inspection"
            self.assertEqual(self.call(inspection, {"text": "The sink is clogged."}, reviewer)[2]["status"], "research_only")
            self.assertEqual(status(complaint_id), "priority")
            self.assertEqual(self.call(inspection, {"request_id": complaint_id}, reviewer)[2]["status"], "research_only")
            self.assertEqual(status(complaint_id), "analyzed")

        # A screening receipt from before per-run audit logging still counts.
        with Session(self.engine) as session:
            photo = RequestAttachment(request_id=complaint_id, original_filename="photo.jpg",
                                      content_type="image/jpeg", size_bytes=32)
            session.add(photo)
            session.flush()
            query(session, """INSERT INTO reviewer_screenings(attachment_id,model_version,crack_detected)
                              VALUES(:id,:model,0)""", id=photo.id, model=ai_services.CV_MODEL)
            session.commit()
        self.assertEqual(status(complaint_id), "nlp_cv")
        decision = {"decision": "approved", "final_category_id": category_id,
                    "final_priority": "medium", "assigned_team_id": team_id}
        self.assertEqual(self.call(complaint_path + "/decisions", decision, reviewer)[0], 201)
        self.assertEqual(status(complaint_id, "?status=approved"), "nlp_cv")


class AnalyticsTests(test_auth.ApiTestCase):
    def test_review_access_and_empty_charts(self):
        credentials, user = self.register("analytics-reviewer")
        requester = self.login(credentials)
        with Session(self.engine) as session:
            session.get(User, user["id"]).role = UserRole.REVIEWER
            session.commit()
        reviewer = self.login(credentials)
        other, _ = self.register("analytics-other")
        self.assertEqual(self.call(API + "/summary")[0], 401)
        self.assertEqual(self.call(API + "/summary", token=self.login(other))[0], 403)
        self.assertEqual(self.call(API + "/summary", token=requester)[0], 401)
        status, headers, summary = self.call(API + "/summary", token=reviewer)
        self.assertEqual((status, headers["Cache-Control"]), (200, "no-store"))
        self.assertEqual(summary["recent"], [])
        self.assertEqual(summary["cv"]["counts"], {"unscreened": 0})

    def test_source_and_concrete_confirmation_contracts(self):
        credentials, profile = self.register("analytics-contract")
        with Session(self.engine) as session:
            session.get(User, profile["id"]).role = UserRole.REVIEWER
            complaint = MaintenanceRequest(requester_id=profile["id"], title="Concrete photo",
                                           description="A concrete close-up.", location="Hall")
            session.add(complaint)
            session.flush()
            photo = RequestAttachment(request_id=complaint.id, original_filename="preview.png",
                                      content_type="image/png", size_bytes=32)
            session.add(photo)
            session.commit()
            path = API + f"/requests/{complaint.id}/attachments/{photo.id}/preview"
        reviewer = self.login(credentials)
        with patch.object(ai_services, "run_worker") as worker:
            self.assertEqual(self.call(API + "/nlp-inspection", {"request_id":1,"text":"Other"}, reviewer)[0], 422)
            self.assertEqual(self.call(path, {"concrete_closeup": False}, reviewer)[0], 422)
            with patch.dict("os.environ", FIELDFIX_AI_CV_ENABLED="1"):
                self.assertEqual(self.call(path, {"concrete_closeup": True}, reviewer)[2]["status"], "unsuitable")
            worker.assert_not_called()

    def test_team_workload_and_safety_filter_use_saved_decisions(self):
        credentials, profile = self.register("analytics-safety")
        with Session(self.engine) as session:
            session.get(User, profile["id"]).role = UserRole.REVIEWER
            team = MaintenanceTeam(name="Safety maintenance", specialty="Electrical")
            category = Category(name="Analytics Electrical", default_team=team)
            session.add_all([team, category])
            complaint = MaintenanceRequest(requester_id=profile["id"], title="Circuit breaker",
                                           description="The circuit breaker is tripping.", location="Hall")
            session.add(complaint)
            session.commit()
            complaint_id, category_id, team_id = complaint.id, category.id, team.id
        reviewer = self.login(credentials)
        data = {"decision":"approved", "final_category_id":category_id,
                "final_priority":"high", "assigned_team_id":team_id}
        self.assertEqual(self.call(f"/api/reviewer/requests/{complaint_id}/decisions", data, reviewer)[0], 201)
        teams = self.call("/api/reviewer/team-workload", token=reviewer)[2]
        self.assertEqual((teams[0]["active_work_orders"], teams[0]["categories"]), (1, ["Analytics Electrical"]))
        orders = self.call("/api/reviewer/work-orders?safety=true", token=reviewer)[2]
        self.assertEqual((orders["safety_count"], orders["total"], orders["items"][0]["safety_alert"]), (1,1,True))
