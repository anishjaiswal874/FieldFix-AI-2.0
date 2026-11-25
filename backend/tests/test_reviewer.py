from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from fastapi import HTTPException
from sqlmodel import Session, select

import test_auth
from test_requests import photo_bytes
from app.models import (
    Category, MaintenanceRequest, MaintenanceTeam, RequestAttachment, RequestStatus, ReviewDecision, User, UserRole, WorkOrder,
)
from app.request_uploads import get_upload_directory
from main import app


class ReviewerWorkflowTests(test_auth.ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uploads = cls.test_directory / "uploads"
        cls.uploads.mkdir()
        app.dependency_overrides[get_upload_directory] = lambda: cls.uploads
        cls.addClassCleanup(app.dependency_overrides.pop, get_upload_directory)
        with Session(cls.engine) as session:
            category = Category(name="Plumbing")
            inactive_category = Category(name="Retired category", is_active=False)
            team = MaintenanceTeam(name="Building Maintenance", specialty="Repairs")
            inactive_team = MaintenanceTeam(name="Retired team", specialty="Repairs", is_active=False)
            session.add_all([category, inactive_category, team, inactive_team])
            session.commit()
            cls.approval = {"decision": "approved", "final_category_id": category.id,
                            "final_priority": "medium", "assigned_team_id": team.id}
            cls.inactive_category_id = inactive_category.id
            cls.inactive_team_id = inactive_team.id

    def account(self, label, role=UserRole.REQUESTER, *, active=True):
        credentials, profile = self.register(label)
        if role != UserRole.REQUESTER:
            with Session(self.engine) as session:
                session.get(User, profile["id"]).role = role
                session.commit()
        token = self.login(credentials)
        if not active:
            with Session(self.engine) as session:
                session.get(User, profile["id"]).is_active = False
                session.commit()
        return token, profile["id"]

    def request(self, owner, title, *, status=RequestStatus.SUBMITTED, photo=False):
        with Session(self.engine) as session:
            record = MaintenanceRequest(requester_id=owner, title=title, description="Inspect the fixture.",
                                        location="Building A", status=status)
            session.add(record)
            session.flush()
            request_id = record.id
            attachment_id = None
            if photo:
                data = photo_bytes()
                attachment = RequestAttachment(request_id=request_id, original_filename="fixture.png",
                                               content_type="image/png", size_bytes=len(data))
                (self.uploads / attachment.stored_filename).write_bytes(data)
                session.add(attachment)
                session.flush()
                attachment_id = attachment.id
            session.commit()
        return request_id, attachment_id

    def decision(self, reviewer, request_id, body):
        return self.call(f"/api/reviewer/requests/{request_id}/decisions", body, reviewer)

    def record_counts(self, request_id):
        with Session(self.engine) as session:
            status = session.get(MaintenanceRequest, request_id).status
            decisions = session.exec(select(ReviewDecision).where(ReviewDecision.request_id == request_id)).all()
            orders = session.exec(select(WorkOrder).where(WorkOrder.request_id == request_id)).all()
            return status, len(decisions), len(orders)

    def test_shared_queue_evidence_and_role_boundaries(self):
        requester, owner = self.account("evidence-owner")
        other, other_id = self.account("evidence-other")
        reviewer, _ = self.account("evidence-reviewer", UserRole.REVIEWER)
        admin, _ = self.account("evidence-admin", UserRole.ADMIN)
        inactive, _ = self.account("evidence-inactive", UserRole.REVIEWER, active=False)
        first, image = self.request(owner, "Shared queue sink", photo=True)
        second, _ = self.request(other_id, "Shared queue cooling", status=RequestStatus.IN_REVIEW)
        path = f"/api/reviewer/requests/{first}"
        evidence = f"{path}/attachments/{image}"

        for token, expected in ((None, 401), (requester, 403), (admin, 403), (inactive, 401)):
            for url in ("/api/reviewer/dashboard", "/api/reviewer/requests", path, evidence, "/api/reviewer/options",
                        "/api/reviewer/history", "/api/reviewer/work-orders", "/api/reviewer/work-orders/1"):
                self.assertEqual(self.call(url, token=token)[0], expected)
            self.assertEqual(self.decision(token, first, self.approval)[0], expected)

        self.assertEqual(self.call(f"/api/requests/{first}", token=other)[0], 404)
        self.assertEqual(self.call(f"/api/requests/{first}/attachments/{image}", token=other)[0], 404)
        self.assertEqual(self.call(f"/api/requests/{first}", token=reviewer)[0], 403)
        code, _, details = self.call(path, token=reviewer)
        self.assertEqual(code, 200, details)
        self.assertEqual(details["requester"], {"full_name": "Temporary Requester"})
        self.assertNotIn("stored_filename", details["attachments"][0])
        code, headers, body = self.call(evidence, token=reviewer)
        self.assertEqual((code, headers["Content-Type"], headers["Cache-Control"]), (200, "image/png", "private, no-store"))
        self.assertEqual(len(body), details["attachments"][0]["size_bytes"])
        self.assertEqual(self.call(f"/api/reviewer/requests/{second}/attachments/{image}", token=reviewer)[0], 404)

        for page, expected in ((1, first), (2, second)):
            code, _, data = self.call(f"/api/reviewer/requests?q=Shared+queue&per_page=1&page={page}", token=reviewer)
            self.assertEqual(code, 200, data)
            self.assertEqual((data["total"], data["items"][0]["id"]), (2, expected))
        self.assertEqual(self.call("/api/reviewer/requests?q=Shared+queue&status=in_review", token=reviewer)[2]["items"][0]["id"], second)
        self.assertEqual(self.call("/api/reviewer/requests?status=approved", token=reviewer)[0], 200)

    def test_explicit_outcomes_and_real_order_history(self):
        requester, owner = self.account("outcomes-owner")
        reviewer, _ = self.account("outcomes-reviewer", UserRole.REVIEWER)
        approve, _ = self.request(owner, "Outcome plumbing", status=RequestStatus.IN_REVIEW)
        reject, _ = self.request(owner, "Outcome rejection")
        clarify, _ = self.request(owner, "Outcome clarification")
        before = self.call("/api/reviewer/dashboard", token=reviewer)[2]["work_orders"].get("open", 0)

        options = self.call("/api/reviewer/options", token=reviewer)[2]
        self.assertEqual([row["id"] for row in options["categories"]], [self.approval["final_category_id"]])
        self.assertEqual([row["id"] for row in options["teams"]], [self.approval["assigned_team_id"]])
        code, _, result = self.decision(reviewer, approve, self.approval | {"notes": "Manual inspection assigned."})
        self.assertEqual(code, 201, result)
        order_id = result["work_order_id"]
        self.assertEqual(result["status"], "approved")
        code, _, rejected = self.decision(reviewer, reject, {"decision": "rejected", "notes": "Duplicate report."})
        self.assertEqual(code, 201, rejected)
        self.assertIsNone(rejected["work_order_id"])
        code, _, needed = self.decision(reviewer, clarify, {"decision": "needs_info", "notes": "Which room number?"})
        self.assertEqual(code, 201, needed)
        self.assertIsNone(needed["work_order_id"])

        for request_id, status in ((approve, "approved"), (reject, "rejected"), (clarify, "needs_info")):
            data = self.call(f"/api/requests/{request_id}", token=requester)[2]
            self.assertEqual(data["status"], status)
            self.assertEqual(data.get("information_requested"), "Which room number?" if status == "needs_info" else None)
            self.assertEqual(self.decision(reviewer, request_id, self.approval)[0], 409)

        code, _, row = self.call(f"/api/reviewer/work-orders/{order_id}", token=reviewer)
        self.assertEqual(code, 200, row)
        self.assertEqual((row["status"], row["assigned_team"]["name"]), ("open", "Building Maintenance"))
        self.assertEqual(row["approval"]["notes"], "Manual inspection assigned.")
        self.assertEqual(row["approval"]["final_priority"], "medium")
        listing = self.call("/api/reviewer/work-orders?status=open&q=Outcome", token=reviewer)[2]
        self.assertEqual([row["id"] for row in listing["items"]], [order_id])
        history = self.call("/api/reviewer/history?q=Outcome&per_page=2&page=2", token=reviewer)[2]
        self.assertEqual((history["total"], len(history["items"])), (3, 1))
        self.assertEqual(history["items"][0]["request"]["id"], approve)
        rejected_history = self.call("/api/reviewer/history?q=Outcome&decision=rejected", token=reviewer)[2]
        self.assertEqual(rejected_history["items"][0]["notes"], "Duplicate report.")
        self.assertEqual(self.call("/api/reviewer/dashboard", token=reviewer)[2]["work_orders"]["open"], before + 1)
        self.assertEqual(self.record_counts(reject), (RequestStatus.REJECTED, 1, 0))
        self.assertEqual(self.record_counts(clarify), (RequestStatus.NEEDS_INFO, 1, 0))

    def test_invalid_assignments_and_atomic_rollback(self):
        _, owner = self.account("rollback-owner")
        reviewer, _ = self.account("rollback-reviewer", UserRole.REVIEWER)
        request_id, _ = self.request(owner, "Rollback fixture")
        invalid = (
            {"decision": "approved"},
            self.approval | {"final_category_id": self.inactive_category_id},
            self.approval | {"assigned_team_id": self.inactive_team_id},
            self.approval | {"assigned_team_id": 9999},
            self.approval | {"final_category_id": True},
            self.approval | {"final_priority": "urgent"},
            self.approval | {"reviewer_id": owner},
            self.approval | {"decision": "rejected"},
            {"decision": "needs_info", "notes": " "},
        )
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.decision(reviewer, request_id, body)[0], 422)
                self.assertEqual(self.record_counts(request_id), (RequestStatus.SUBMITTED, 0, 0))

        original_flush = Session.flush

        def fail_after_insert(session, *args, **kwargs):
            wrote_order = any(isinstance(row, WorkOrder) for row in session.new)
            original_flush(session, *args, **kwargs)
            if wrote_order:
                raise HTTPException(503, "Simulated failure after writing pending rows.")

        with patch.object(Session, "flush", fail_after_insert):
            self.assertEqual(self.decision(reviewer, request_id, self.approval)[0], 503)
        self.assertEqual(self.record_counts(request_id), (RequestStatus.SUBMITTED, 0, 0))
        self.assertEqual(self.decision(reviewer, request_id, self.approval)[0], 201)

    def test_concurrent_approval_creates_one_decision_and_order(self):
        _, owner = self.account("concurrent-owner")
        first, _ = self.account("concurrent-first", UserRole.REVIEWER)
        second, _ = self.account("concurrent-second", UserRole.REVIEWER)
        request_id, _ = self.request(owner, "Concurrent fixture")
        start = Barrier(2, timeout=5)

        def approve(token):
            start.wait()
            return self.decision(token, request_id, self.approval)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(approve, [first, second]))
        self.assertEqual(sorted(result[0] for result in results), [201, 409])
        self.assertEqual(self.record_counts(request_id), (RequestStatus.APPROVED, 1, 1))
