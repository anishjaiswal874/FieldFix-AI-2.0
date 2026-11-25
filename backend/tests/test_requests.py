from io import BytesIO
from secrets import token_hex
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import HTTPException
from PIL import Image, PngImagePlugin
from sqlmodel import Session, select

import test_auth
from app.models import Category, MaintenanceRequest, RequestAttachment, RequestStatus, User, UserRole
from app.request_uploads import MAX_PHOTO_BYTES, get_upload_directory
from main import app


def photo_bytes(image_format="PNG", size=(12, 12)):
    with Image.new("RGB", size, "#2a6893") as image, BytesIO() as data:
        options = {}
        if image_format == "PNG":
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Comment", "test metadata")
            options["pnginfo"] = metadata
        image.save(data, image_format, **options)
        return data.getvalue()


class RequestTests(test_auth.ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.uploads = cls.test_directory / "uploads"
        app.dependency_overrides[get_upload_directory] = lambda: cls.uploads
        cls.addClassCleanup(app.dependency_overrides.pop, get_upload_directory)

    def new_requester(self, label):
        credentials, profile = self.register(label)
        return self.login(credentials), profile["id"]

    def submit(self, token, *, photos=(), **fields):
        inputs = {"title": "Leaking sink", "description": "Water under the cabinet.", "location": "Building A"} | fields
        boundary = token_hex(16)
        parts = []
        for name, value in inputs.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for filename, data in photos:
            parts.extend([
                (f'--{boundary}\r\nContent-Disposition: form-data; name="photos"; filename="{filename}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n').encode(), data, b"\r\n",
            ])
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call("/api/requests", b"".join(parts), token, content_type=f"multipart/form-data; boundary={boundary}")

    def get_requests(self, token, **query):
        code, _, result = self.call("/api/requests?" + urlencode(query), token=token)
        self.assertEqual(code, 200, result)
        return result

    def test_submission_list_detail_and_ownership(self):
        owner, _ = self.new_requester("owner")
        other, _ = self.new_requester("other")
        reviewer_credentials, reviewer_profile = self.register("reviewer")
        with Session(self.engine) as session:
            category = Category(name="Plumbing")
            session.add(category)
            session.add(Category(name="Retired category", is_active=False))
            session.get(User, reviewer_profile["id"]).role = UserRole.REVIEWER
            session.commit()
            category_id = category.id
        reviewer = self.login(reviewer_credentials)

        for token, expected in ((None, 401), (reviewer, 403)):
            self.assertEqual(self.submit(token)[0], expected)
            for path in ("/api/requests", "/api/requests/categories", "/api/requests/1", "/api/requests/1/attachments/1"):
                self.assertEqual(self.call(path, token=token)[0], expected)
        self.assertEqual(self.call("/api/requests/categories", token=owner)[2], [{"id": category_id, "name": "Plumbing"}])

        code, _, plain = self.submit(owner)
        self.assertEqual(code, 201, plain)
        self.assertEqual((plain["status"], plain["attachments"], plain["selected_category"]), ("submitted", [], None))
        code, _, with_photos = self.submit(owner, title="Cooling 100% issue", category_id=category_id,
                                           photos=[("../photo.txt", photo_bytes()), ("b.jpeg", photo_bytes("JPEG")),
                                                   ("c.webp", photo_bytes("WEBP"))])
        self.assertEqual(code, 201, with_photos)
        self.assertEqual(with_photos["selected_category"], {"id": category_id, "name": "Plumbing"})
        self.assertEqual([file["content_type"] for file in with_photos["attachments"]], ["image/png", "image/jpeg", "image/webp"])
        self.assertEqual(with_photos["attachments"][0]["original_filename"], "photo.txt")
        self.assertEqual(self.submit(other, title="Other request")[0], 201)

        with Session(self.engine) as session:
            session.get(MaintenanceRequest, plain["id"]).status = RequestStatus.NEEDS_INFO
            session.commit()

        listed = self.get_requests(owner, per_page=1)
        self.assertEqual(listed["total"], 2)
        self.assertEqual(listed["status_counts"], {"submitted": 1, "needs_info": 1})
        self.assertEqual(listed["items"][0]["id"], with_photos["id"])
        self.assertEqual(self.get_requests(owner, page=2, per_page=1)["items"][0]["id"], plain["id"])
        self.assertEqual(self.get_requests(owner, status="pending_review")["total"], 1)
        self.assertEqual(self.get_requests(owner, status="needs_info")["total"], 1)
        self.assertEqual(self.get_requests(owner, q="100%")["total"], 1)
        self.assertEqual(self.get_requests(owner, q="%")["total"], 1)
        self.assertEqual(self.get_requests(owner, q="plum")["total"], 1)
        self.assertEqual(self.get_requests(owner, q=f'FF-{plain["id"]:03}')["items"][0]["id"], plain["id"])
        self.assertEqual(self.get_requests(other)["total"], 1)

        details_path = f'/api/requests/{with_photos["id"]}'
        self.assertEqual(self.call(details_path, token=owner)[2], with_photos)
        self.assertEqual(self.call(details_path, token=other)[0], 404)
        attachment = with_photos["attachments"][0]
        self.assertEqual(set(attachment), {"id", "original_filename", "content_type", "size_bytes"})
        path = f'{details_path}/attachments/{attachment["id"]}'
        self.assertEqual(self.call(path)[0], 401)
        self.assertEqual(self.call(path, token=other)[0], 404)
        self.assertEqual(self.call(f'/api/requests/{plain["id"]}/attachments/{attachment["id"]}', token=owner)[0], 404)
        code, headers, body = self.call(path, token=owner)
        self.assertEqual((code, headers["Content-Type"], headers["Cache-Control"]), (200, "image/png", "private, no-store"))
        self.assertEqual(len(body), attachment["size_bytes"])
        with Image.open(BytesIO(body)) as image:
            self.assertEqual(image.size, (12, 12))
            self.assertNotIn("Comment", image.info)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(RequestAttachment)).all()), 3)
        self.assertTrue(all(len(path.name) == 32 for path in self.uploads.iterdir()))

    def test_validation_and_cleanup(self):
        token, _ = self.new_requester("validation")
        with Session(self.engine) as session:
            inactive = Category(name="Unavailable", is_active=False)
            session.add(inactive)
            session.commit()
            inactive_id = inactive.id
        valid = ("valid.png", photo_bytes())
        existing_files = set(self.uploads.glob("*"))
        cases = (
            (422, {"photos": [valid, ("invalid.png", b"not an image")]}),
            (413, {"photos": [("large.png", b"x" * (MAX_PHOTO_BYTES + 1))]}),
            (413, {"photos": [("huge.png", photo_bytes(size=(4001, 4000)))]}),
            (422, {"photos": [valid] * 6}),
            (422, {"title": "  "}),
            (422, {"description": "x" * 1001}),
            (422, {"location": ""}),
            (422, {"category_id": "wrong"}),
            (422, {"category_id": 9999, "photos": [valid]}),
            (422, {"category_id": inactive_id}),
        )
        for expected, fields in cases:
            with self.subTest(inputs=tuple(fields), status=expected):
                code, _, result = self.submit(token, **fields)
                self.assertEqual(code, expected, result)
                self.assertEqual(set(self.uploads.glob("*")), existing_files)

        with patch.object(Session, "commit", side_effect=HTTPException(503, "Simulated commit failure")):
            self.assertEqual(self.submit(token, photos=[valid])[0], 503)
        self.assertEqual(set(self.uploads.glob("*")), existing_files)
        self.assertEqual(self.get_requests(token)["total"], 0)

        code, _, result = self.submit(token, photos=[valid] * 5)
        self.assertEqual(code, 201, result)
        self.assertEqual(len(result["attachments"]), 5)
