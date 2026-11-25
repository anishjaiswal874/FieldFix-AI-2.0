import base64
from io import BytesIO
import os
from pathlib import Path
from secrets import token_hex
from unittest import skipUnless
from unittest.mock import patch

from PIL import Image
from sqlmodel import Session
from starlette.datastructures import UploadFile
from fastapi import HTTPException

from app.models import Category, MaintenanceRequest, MaintenanceTeam, User, UserRole
from app.request_uploads import MAX_PHOTO_BYTES, get_upload_directory, normalize_photo
from main import app
from test_auth import ApiTestCase


ANALYTICS = "/api/reviewer/analytics"


def image(format):
    with BytesIO() as stream:
        Image.new("RGB", (32, 32), "#526178").save(stream, format=format)
        return stream.getvalue()


class ResolutionTests(ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uploads = cls.test_directory / "uploads"
        app.dependency_overrides[get_upload_directory] = lambda: cls.uploads
        cls.addClassCleanup(app.dependency_overrides.pop, get_upload_directory)

    def setup_case(self, label):
        users = {}
        for role in UserRole:
            credentials, profile = self.register(f"{label}-{role.value}")
            with Session(self.engine) as db:
                db.get(User, profile["id"]).role = role
                db.commit()
            users[role.value] = (self.login(credentials), profile["id"])
        with Session(self.engine) as db:
            team = MaintenanceTeam(name=f"{label} response", specialty="Plumbing")
            category = Category(name=f"{label} Plumbing")
            req = MaintenanceRequest(requester_id=users["requester"][1], title="Leaking sink",
                                     description="The sink is clogged. Water is gushing from the pipe.", location="Hall")
            db.add_all([team, category, req])
            db.commit()
            return users, req.id, category.id, team.id

    def multipart(self, url, token, fields, proof=None):
        boundary = token_hex(16)
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
                 for name, value in fields.items()]
        if proof:
            name, filename, payload = proof
            parts += [f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n\r\n'.encode(),
                      payload, b"\r\n"]
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call(url, b"".join(parts), token, content_type=f"multipart/form-data; boundary={boundary}")

    def test_close_order_history_dashboard_and_owner_proof(self):
        users, request_id, category, team = self.setup_case("resolve")
        reviewer = users["reviewer"][0]
        owner = users["requester"][0]
        other, _ = self.register("outside-owner")
        with patch.dict(os.environ, FIELDFIX_AI_PRIORITY_ENABLED="1"):
            code, _, approval = self.call(f"/api/reviewer/requests/{request_id}/decisions", {
                "decision":"approved", "final_category_id":category, "final_priority":"low",
                "assigned_team_id":team, "override_reason":"Containment already installed",
            }, reviewer)
        self.assertEqual(code, 201)
        order_id = approval["work_order_id"]
        queued = self.call("/api/reviewer/requests?status=approved", token=reviewer)[2]["items"]
        self.assertTrue(any(row["active_work_order_id"] == order_id for row in queued))
        path = f"/api/reviewer/work-orders/{order_id}/resolve"
        fields = {"resolution_confirmed":"true","resolution_notes":"Cleared and inspected the fitting."}
        self.assertEqual(self.multipart(path, owner, fields)[0], 403)
        self.assertEqual(self.multipart(path, reviewer, {**fields,"resolution_confirmed":"false"})[0], 422)
        self.assertEqual(self.multipart(path, reviewer, fields, ("resolution_image","repair.bmp",image("BMP")))[0], 200)
        self.assertEqual(self.multipart(path, reviewer, fields)[0], 409)
        detail = self.call(f"/api/requests/{request_id}", token=owner)[2]
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["resolution"]["notes"], fields["resolution_notes"])
        code, headers, _ = self.call(f"/api/requests/{request_id}/resolution-proof", token=owner)
        self.assertEqual((code, headers["Content-Type"], headers["Cache-Control"]), (200, "image/webp", "private, no-store"))
        self.assertEqual(self.call(f"/api/requests/{request_id}/resolution-proof", token=self.login(other))[0], 404)
        self.assertEqual(self.call("/api/requests?status=completed", token=owner)[2]["status_counts"]["completed"], 1)
        history = self.call("/api/reviewer/history?decision=approved", token=reviewer)[2]
        self.assertEqual(next(row["resolution"]["has_proof"] for row in history["items"] if row["request"]["id"] == request_id), True)
        self.assertGreaterEqual(self.call("/api/reviewer/dashboard", token=reviewer)[2]["work_orders"]["resolved"], 1)
        code, _, audit = self.call(ANALYTICS + "/performance", token=users["admin"][0])
        self.assertEqual((code, audit["priority_overrides"], audit["resolved"]), (200, 1, 1))
        self.assertEqual(audit["resolution_rate"], 100.0)
        self.assertEqual(self.call(ANALYTICS + "/performance", token=owner)[0], 403)

    def test_profile_and_public_registration_state(self):
        users, _, _, _ = self.setup_case("profile")
        requester, admin = (users[role][0] for role in ("requester","admin"))
        path = "/api/requests/profile"
        fields = {"full_name":"Edited Requester","phone":"+1 555 0100","department":"Science",
                  "staff_or_student_id":"A1","default_location":"Lab 2","bio":"Room contact"}
        self.assertEqual(self.call(path, fields, requester, method="PATCH")[0], 200)
        self.assertEqual(self.call(path, {"phone":"555-0101"}, requester, method="PATCH")[0], 200)
        profile = self.call(path, token=requester)[2]
        self.assertEqual({key:profile[key] for key in fields}, {**fields,"phone":"555-0101"})
        self.assertEqual(self.call(path, {"role":"admin"}, requester, method="PATCH")[0], 422)
        self.assertEqual(self.call("/api/admin/settings", {"requester_self_registration":False}, admin, method="PUT")[0], 200)
        try:
            self.assertEqual(self.call("/api/auth/registration-status")[2], {"allowed": False})
            self.assertEqual(self.call("/api/auth/register", {
                "email":"registration-disabled@example.com", "password":"long-password-for-qa","full_name":"Guest"
            })[0], 403)
        finally:
            self.assertEqual(self.call("/api/admin/settings", {"requester_self_registration":True}, admin, method="PUT")[0], 200)

    def test_expanded_formats_and_size_boundary(self):
        self.assertEqual(MAX_PHOTO_BYTES, 15*1024*1024)
        for format in ("JPEG","PNG","WEBP","BMP","GIF","TIFF","AVIF","HEIF"):
            with self.subTest(format=format):
                output, media = normalize_photo(UploadFile(BytesIO(image(format)), filename="test." + format))
                with Image.open(BytesIO(output)) as result:
                    self.assertEqual(result.size, (32,32))
                    self.assertIn(media, ("image/jpeg","image/png","image/webp"))
        source = image("JPEG")
        upload = UploadFile(BytesIO(source + bytes(MAX_PHOTO_BYTES-len(source))), filename="at-limit.jpg")
        self.assertEqual(normalize_photo(upload)[1], "image/jpeg")
        with self.assertRaises(HTTPException) as rejected:
            normalize_photo(UploadFile(BytesIO(source), size=MAX_PHOTO_BYTES+1))
        self.assertEqual(rejected.exception.status_code, 413)

    @skipUnless(os.environ.get("FIELDFIX_TEST_FROZEN_MODELS")=="1", "Requires the existing local frozen NLP and CUDA runtimes")
    def test_frozen_analysis_png_quality_similarity_and_embedding(self):
        users, request_id, _, _ = self.setup_case("frozen")
        reviewer, owner = (users[role][0] for role in ("reviewer","requester"))
        root = Path(os.environ["LOCALAPPDATA"])/"FieldFixAI/ml/processed/metu-concrete-cracks/v2"
        jpeg = (root/"images/crack/00002.jpg").read_bytes()
        _, _, second = self.multipart("/api/requests", owner, {
            "title":"Clogged sink again","description":"The sink is clogged. Water is gushing from the pipe.","location":"Hall"
        }, ("photos","concrete.jpg",jpeg))
        with patch.dict(os.environ, FIELDFIX_AI_CV_ENABLED="1",FIELDFIX_AI_NLP_DEMO_ENABLED="1",FIELDFIX_AI_PRIORITY_ENABLED="1"):
            preview = self.call(ANALYTICS+f"/requests/{second['id']}/attachments/{second['attachments'][0]['id']}/preview",
                                {"concrete_closeup":True}, reviewer)[2]
            self.assertEqual(preview["status"], "screened")
            self.assertTrue(base64.b64decode(preview["grad_cam"]).startswith(b"\x89PNG\r\n\x1a\n"))
            with Image.open(BytesIO(jpeg)) as original, Image.open(BytesIO(base64.b64decode(preview["grad_cam"]))) as heatmap:
                self.assertEqual((preview["quality"]["width"], preview["quality"]["height"]), original.size)
                self.assertEqual(heatmap.size, (224,224))
            self.assertTrue(preview["consistency"]["mismatch"])
            combined = self.call(ANALYTICS+f"/requests/{second['id']}/fused", {}, reviewer)[2]
            self.assertTrue(combined["advisory_only"])
            self.assertEqual(combined["nlp"]["label"], "Plumbing")
            self.assertEqual(combined["priority"]["priority"], "high")
            self.assertTrue(any(row["id"]==request_id and row["likely_duplicate"] for row in combined["similar"]))
            mapped = self.call(ANALYTICS+"/embedding", token=reviewer)[2]
            self.assertEqual(mapped["status"], "ready")
            self.assertTrue({request_id,second["id"]}<={row["id"] for row in mapped["points"]})
