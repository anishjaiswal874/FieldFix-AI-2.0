import asyncio
from contextlib import closing
from http.client import HTTPConnection
import json
import os
import unittest
from pathlib import Path
from secrets import token_urlsafe
from socket import create_server
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch

import jwt
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import Depends
from sqlmodel import Session

from app.auth import JWT_ALGORITHM, JWT_AUDIENCE, JWT_ISSUER, load_signing_key, require_roles, verify_password
from app.database import BACKEND_DIR, create_database_engine, get_session
from app.models import User, UserRole
from main import app


class SigningKeyTests(unittest.TestCase):
    def test_startup_configuration_rejects_missing_weak_and_malformed_keys(self):
        async def start_application():
            async with app.router.lifespan_context(app):
                pass

        with patch.dict(os.environ):
            os.environ.pop("FIELDFIX_JWT_SECRET", None)
            with self.assertRaisesRegex(RuntimeError, "FIELDFIX_JWT_SECRET"):
                asyncio.run(start_application())

            for invalid in (token_urlsafe(16), "A" * 43, "invalid base64url!"):
                os.environ["FIELDFIX_JWT_SECRET"] = invalid
                with self.assertRaisesRegex(RuntimeError, "FIELDFIX_JWT_SECRET"):
                    load_signing_key()


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = TemporaryDirectory(prefix="fieldfix-auth-test-", dir=BACKEND_DIR / ".venv")
        cls.addClassCleanup(folder.cleanup)
        cls.test_directory = Path(folder.name)
        cls.engine = create_database_engine(cls.test_directory / "test.db")
        cls.addClassCleanup(cls.engine.dispose)
        config = Config(BACKEND_DIR / "alembic.ini")
        with cls.engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        def test_session():
            with Session(cls.engine) as session:
                yield session

        app.dependency_overrides[get_session] = test_session
        cls.addClassCleanup(app.dependency_overrides.pop, get_session)
        original_routes = app.router.routes[:]
        cls.addClassCleanup(app.router.routes.__setitem__, slice(None), original_routes)
        for role in UserRole:
            def protected(user: User = Depends(require_roles(role))):
                return {"role": user.role}

            app.add_api_route(f"/_test/{role}", protected, include_in_schema=False)

        environment = patch.dict(os.environ, {"FIELDFIX_JWT_SECRET": token_urlsafe(64)})
        environment.start()
        cls.addClassCleanup(environment.stop)
        cls.key = load_signing_key()

        listener = create_server(("127.0.0.1", 0))
        cls.addClassCleanup(listener.close)
        cls.base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
        worker = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()

        def stop_server():
            server.should_exit = True
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("The disposable authentication server did not stop.")

        cls.addClassCleanup(stop_server)
        deadline = monotonic() + 10
        while not server.started and worker.is_alive() and monotonic() < deadline:
            sleep(0.05)
        if not server.started:
            raise RuntimeError("The disposable authentication server did not start.")

    def call(self, path, body=None, token=None, *, content_type="application/json", method=None):
        headers = {"Content-Type": content_type}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None and content_type == "application/json" else body
        # Read early 401/403/405 replies before closing the loopback socket.
        # urllib's forced Connection: close can race with an unread request body on Windows.
        with closing(HTTPConnection(self.base_url.removeprefix("http://"), timeout=10)) as client:
            client.request(method or ("POST" if data is not None else "GET"), path, body=data, headers=headers)
            response = client.getresponse()
            payload = response.read()
            response_type = response.headers.get_content_type()
            if response_type == "application/json":
                payload = json.loads(payload)
            elif response_type.startswith("text/"):
                payload = payload.decode()
            return response.status, response.headers, payload

    def register(self, label):
        credentials = {"email": f"{label}@example.com", "password": token_urlsafe(24)}
        status, _, profile = self.call("/api/auth/register", {"full_name": "  Temporary Requester  ", **credentials})
        self.assertEqual(status, 201)
        self.assertEqual(set(profile), {"id", "email", "full_name", "role", "is_active", "created_at"})
        self.assertEqual(profile["role"], "requester")
        return credentials, profile

    def login(self, credentials):
        status, headers, result = self.call("/api/auth/login", credentials)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(set(result), {"access_token", "token_type"})
        self.assertEqual(result["token_type"], "bearer")
        return result["access_token"]

class AuthTests(ApiTestCase):
    def assert_unauthorized(self, token=None):
        status, headers, result = self.call("/api/auth/me", token=token)
        self.assertEqual(status, 401)
        self.assertEqual(headers["WWW-Authenticate"], "Bearer")
        self.assertEqual(result, {"detail": "Authentication required."})

    def test_existing_api_endpoints(self):
        status, _, root = self.call("/")
        self.assertEqual((status, root), (200, {"name": "FieldFix AI 2.0 API"}))
        status, _, health = self.call("/api/health")
        self.assertEqual((status, health), (200, {"status": "ok", "service": "fieldfix-api"}))
        status, _, docs = self.call("/docs")
        self.assertEqual(status, 200)
        self.assertIn("SwaggerUIBundle", docs)

    def test_registration_hash_normalization_login_and_profile(self):
        credentials, profile = self.register("Mixed.Case")
        self.assertEqual(profile["email"], "mixed.case@example.com")
        self.assertEqual(profile["full_name"], "Temporary Requester")

        with Session(self.engine) as session:
            stored = session.get(User, profile["id"])
            self.assertTrue(stored.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(credentials["password"], stored.password_hash))
            self.assertTrue(stored.password_hash != credentials["password"])

        token = self.login({**credentials, "email": credentials["email"].upper()})
        claims = jwt.decode(token, self.key, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
        self.assertEqual(set(claims), {"sub", "iat", "exp", "iss", "aud", "rev"})
        self.assertEqual(claims["sub"], str(profile["id"]))
        self.assertEqual(claims["exp"] - claims["iat"], 1200)
        status, headers, authenticated = self.call("/api/auth/me", token=token)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(set(authenticated), set(profile))
        self.assertEqual(authenticated, profile)

        duplicate = {"full_name": "Duplicate", **credentials, "email": credentials["email"].upper()}
        status, _, _ = self.call("/api/auth/register", duplicate)
        self.assertEqual(status, 409)

    def test_invalid_registration_and_role_escalation(self):
        body = {"full_name": "Temporary", "email": "validation@example.com", "password": token_urlsafe(24)}
        for invalid_fields in (
            {"role": "admin"}, {"role": "reviewer"}, {"is_active": True},
            {"full_name": " "}, {"email": "invalid"}, {"password": "short"},
        ):
            with self.subTest(field=next(iter(invalid_fields))):
                status, _, result = self.call("/api/auth/register", {**body, **invalid_fields})
                self.assertEqual(status, 422)
                self.assertTrue(all(set(item) == {"loc", "msg", "type"} for item in result["detail"]))
        status, _, _ = self.call("/api/auth/register", body)
        self.assertEqual(status, 201)

    def test_failed_login_and_deactivation(self):
        credentials, profile = self.register("deactivation")
        token = self.login(credentials)
        for invalid in ({**credentials, "password": token_urlsafe(24)}, {**credentials, "email": "absent@example.com"}):
            status, _, result = self.call("/api/auth/login", invalid)
            self.assertEqual((status, result), (401, {"detail": "Invalid email or password."}))
        with Session(self.engine) as session:
            user = session.get(User, profile["id"])
            user.is_active = False
            session.add(user)
            session.commit()
        status, _, result = self.call("/api/auth/login", credentials)
        self.assertEqual((status, result), (401, {"detail": "Invalid email or password."}))
        self.assert_unauthorized(token)

    def test_token_rejections(self):
        credentials, _ = self.register("tokens")
        token = self.login(credentials)
        claims = jwt.decode(token, self.key, algorithms=[JWT_ALGORITHM], issuer=JWT_ISSUER, audience=JWT_AUDIENCE)
        self.assert_unauthorized()
        self.assert_unauthorized("malformed")

        hour_ago = claims["iat"] - 3600
        invalid_claims = {
            "expired": {"iat": hour_ago, "exp": hour_ago + 1200},
            "issuer": {"iss": "different-issuer"},
            "audience": {"aud": "different-audience"},
            "future iat": {"iat": claims["iat"] + 1800, "exp": claims["exp"] + 1800},
            "excess lifetime": {"exp": claims["exp"] + 1},
            "invalid subject": {"sub": str(2**63)},
            "absent user": {"sub": "99999999"},
        }
        for label, changes in invalid_claims.items():
            with self.subTest(case=label):
                self.assert_unauthorized(jwt.encode({**claims, **changes}, self.key, algorithm=JWT_ALGORITHM))
        # Tokens issued before account revisions use version zero.
        legacy = jwt.encode({key: value for key, value in claims.items() if key != "rev"}, self.key, algorithm=JWT_ALGORITHM)
        self.assertEqual(self.call("/api/auth/me", token=legacy)[0], 200)
        for claim in claims.keys() - {"rev"}:
            with self.subTest(missing=claim):
                self.assert_unauthorized(jwt.encode({key: value for key, value in claims.items() if key != claim}, self.key, algorithm=JWT_ALGORITHM))
        self.assert_unauthorized(jwt.encode(claims, self.key, algorithm="HS512"))
        self.assert_unauthorized(jwt.encode(claims, b"\x81" * 32, algorithm=JWT_ALGORITHM))

    def test_role_guards_use_the_current_database_role(self):
        credentials, profile = self.register("roles")
        token = self.login(credentials)
        for role in (UserRole.REQUESTER, UserRole.REVIEWER, UserRole.ADMIN):
            with self.subTest(role=role):
                with Session(self.engine) as session:
                    user = session.get(User, profile["id"])
                    user.role = role
                    session.add(user)
                    session.commit()
                if role != UserRole.REQUESTER:
                    self.assert_unauthorized(token)
                token = self.login(credentials)
                for candidate in UserRole:
                    status, _, _ = self.call(f"/_test/{candidate}", token=token)
                    self.assertEqual(status, 200 if candidate == role else 403)
                status, _, authenticated = self.call("/api/auth/me", token=token)
                self.assertEqual((status, authenticated["role"]), (200, role.value))
