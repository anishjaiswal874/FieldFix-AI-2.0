"""Disposable HTTP acceptance tests. The fake mailbox exists only in test memory."""
from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Condition
import unittest
from urllib.request import Request, build_opener, ProxyHandler
from urllib.error import HTTPError
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlmodel import Session

import test_auth
from app import browser_sessions as browser, email_otp as otp
from app.database import BACKEND_DIR, create_database_engine
from app.models import User, UserRole
from main import app


class Mailbox:
    def __init__(self):
        self.messages = {}
        self.fail = False

    def send(self, email, code, purpose):
        if self.fail:
            raise OSError("delivery failed")
        self.messages[email] = code


class EmailSessions(test_auth.ApiTestCase):
    def setUp(self):
        self.mail = Mailbox()
        self.mail_condition = Condition()
        self.finished_deliveries = set()
        deliver = otp.mail_after_commit

        def observed_delivery(engine, mailer, email, code, purpose, challenge):
            deliver(engine, mailer, email, code, purpose, challenge)
            # Includes server-side challenge invalidation when delivery fails.
            with self.mail_condition:
                self.finished_deliveries.add(challenge)
                self.mail_condition.notify_all()

        self.enterContext(patch.object(otp, "mail_after_commit", observed_delivery))
        app.dependency_overrides[otp.get_mailer] = lambda: self.mail
        self.addCleanup(app.dependency_overrides.pop, otp.get_mailer)
        self.enterContext(patch.dict(os.environ, FIELDFIX_AUTH_ORIGIN=self.base_url, FIELDFIX_AUTH_LOOPBACK="1"))
        self.clock = self.enterContext(patch.object(browser, "now", return_value=1800000000))
        # Each test reuses the HTTP fixture but starts with independent rate budgets.
        with Session(self.engine) as db:
            browser.query(db, "DELETE FROM auth_limit")
            db.commit()
        self.creds, self.profile = self.register(self._testMethodName.replace("_", "-"))
        self.email = self.creds["email"]

    def api(self, route, body=None, *, cookie="", csrf="", token="", origin=None):
        headers = {"Origin": self.base_url if origin is None else origin, "Content-Type": "application/json",
                   "X-FieldFix-Bootstrap": "1", "X-FieldFix-CSRF": csrf}
        if cookie:
            headers["Cookie"] = cookie
        if token:
            headers["Authorization"] = "Bearer " + token
        req = Request(self.base_url + "/api/auth/" + route, headers=headers,
                      data=json.dumps(body).encode() if body is not None else None)
        try:
            res = build_opener(ProxyHandler({})).open(req, timeout=10)
        except HTTPError as error:
            res = error
        with res:
            return res.status, json.load(res), res.headers.get("Set-Cookie", "")

    def wait_for_delivery(self, challenge):
        with self.mail_condition:
            self.assertTrue(self.mail_condition.wait_for(
                lambda: challenge in self.finished_deliveries, timeout=3
            ), "Background delivery did not finish for the requested challenge")

    def challenge(self, *, delivery_expected=True):
        status, data, _ = self.api("otp/request", {"email": self.email})
        self.assertEqual(status, 202)
        if delivery_expected:
            self.wait_for_delivery(data["challenge"])
        return {"challenge": data["challenge"], "email": self.email}

    def enroll(self):
        token = self.login(self.creds)
        result = self.api("enroll/request", {"password": self.creds["password"]}, token=token)
        self.assertEqual(result[0], 202)
        self.wait_for_delivery(result[1]["challenge"])
        self.assertNotIn(self.mail.messages[self.email], str(result[1]))
        proof = {"email": self.email, "challenge": result[1]["challenge"], "code": self.mail.messages[self.email]}
        self.assertEqual(self.api("enroll/verify", proof, token=token)[0], 200)
        self.clock.return_value += 61

    def remembered(self):
        status, _, header = self.api("login", {**self.creds, "remember": True})
        self.assertEqual(status, 200)
        parsed = SimpleCookie(header)
        item = next(iter(parsed.values()))
        self.assertTrue(item["httponly"] and item["samesite"].lower() == "strict")
        self.assertEqual((item["domain"], item["path"], item["max-age"]), ("", "/api/auth", "2592000"))
        return header.split(";")[0]

    def mutation(self, name, cookie, body=None):
        csrf = self.api("session", cookie=cookie)[1]["csrf"]
        return self.api(name, body or {}, cookie=cookie, csrf=csrf)

    def test_enrollment_and_concurrent_consumption(self):
        self.challenge(delivery_expected=False)
        self.assertNotIn(self.email, self.mail.messages)
        self.clock.return_value += 61
        self.enroll()
        proof = self.challenge()
        proof["code"] = self.mail.messages[self.email]
        with ThreadPoolExecutor(2) as pool:
            statuses = list(pool.map(lambda _: self.api("otp/verify", {**proof,"remember":True})[0], range(2)))
        self.assertEqual(sorted(statuses), [200, 401])
        self.assertEqual(self.api("otp/verify", proof)[0], 401)

    def test_cooldown_resend_expiry_attempts(self):
        self.enroll()
        old = self.challenge()
        old["code"] = self.mail.messages[self.email]
        self.assertEqual(self.api("otp/request", {"email":self.email})[0], 429)
        self.clock.return_value += 61
        proof = self.challenge()
        code = self.mail.messages[self.email]
        self.assertEqual(self.api("otp/verify", old)[0], 401)
        for _ in range(5):
            self.assertEqual(self.api("otp/verify", {**proof,"code": "000000" if code != "000000" else "999999"})[0], 401)
        self.assertEqual(self.api("otp/verify", {**proof,"code":code})[0], 401)
        self.clock.return_value += 61
        proof = self.challenge()
        self.clock.return_value += 300
        self.assertEqual(self.api("otp/verify", {**proof,"code":self.mail.messages[self.email]})[0], 401)

    def test_delivery_failure_and_unknown(self):
        self.enroll()
        self.mail.fail = True
        proof = self.challenge()
        self.assertEqual(self.api("otp/verify", {**proof,"code":"123456"})[0], 401)
        result = self.api("otp/request", {"email":"unknown@example.com"})
        self.assertEqual((result[0],set(result[1])), (202,{"challenge","retry_after"}))
        with Session(self.engine) as db:
            self.assertFalse(browser.query(db, "SELECT id FROM email_challenge WHERE id=:c", c=proof["challenge"]).first())

    def test_privileged_roles_and_inactive(self):
        for role in (UserRole.REVIEWER, UserRole.ADMIN):
            with Session(self.engine) as db:
                user = db.get(User, self.profile["id"])
                user.role = role
                db.commit()
            self.enroll()
            proof = self.challenge()
            status, result, _ = self.api("otp/verify", {**proof,"code":self.mail.messages[self.email], "portal":role.value})
            self.assertEqual(status, 200)
            self.assertEqual(self.api("me", token=result["access_token"])[1]["role"], role.value)
            self.clock.return_value += 61
        proof = self.challenge()
        with Session(self.engine) as db:
            user = db.get(User, self.profile["id"])
            user.is_active = False
            db.commit()
        self.assertEqual(self.api("otp/verify", {**proof,"code":self.mail.messages[self.email]})[0], 401)

    def test_portal_mismatch_requires_proof_and_creates_no_session(self):
        for portal in ("reviewer", "admin"):
            status, result, cookie = self.api("login", {**self.creds, "portal":portal, "remember":True})
            self.assertEqual((status, cookie), (409, ""))
            self.assertNotIn("access_token", result)
        for fields in ({"password":"incorrect"}, {"email":"unknown@example.com"}):
            self.assertEqual(self.api("login", {**self.creds, **fields, "portal":"admin"})[0], 401)
        self.enroll()
        proof = self.challenge()
        code = self.mail.messages[self.email]
        wrong = "111111" if code != "111111" else "222222"
        self.assertEqual(self.api("otp/verify", {**proof,"code":wrong,"portal":"admin"})[0], 401)
        status, result, cookie = self.api("otp/verify", {**proof,"code":code,"portal":"admin","remember":True})
        self.assertEqual((status, cookie), (409, ""))
        self.assertNotIn("access_token", result)
        self.assertEqual(self.api("otp/verify", {**proof,"code":code,"portal":"requester"})[0], 401)
        with Session(self.engine) as db:
            self.assertFalse(browser.query(db, "SELECT id FROM browser_session WHERE user_id=:u", u=self.profile["id"]).first())

    def test_cookie_security_expiry(self):
        self.assertEqual(self.api("login", self.creds)[2], "")
        cookie = self.remembered()
        self.assertEqual(self.api("me", cookie=cookie)[0], 401)
        self.assertEqual(self.api("refresh", {}, cookie=cookie)[0], 403)
        csrf = self.api("session", cookie=cookie)[1]["csrf"]
        self.assertEqual(self.api("refresh", {}, cookie=cookie, csrf=csrf, origin="https://example.org")[0], 403)
        self.clock.return_value += 7 * 86400
        self.assertEqual(self.mutation("refresh", cookie)[0], 401)
        cookie = self.remembered()
        for _ in range(4):
            self.clock.return_value += 6 * 86400
            status, _, header = self.mutation("refresh", cookie)
            self.assertEqual(status, 200)
            cookie = header.split(";")[0]
        self.clock.return_value += 6 * 86400
        self.assertEqual(self.mutation("refresh", cookie)[0], 401)
        with patch.dict(os.environ, FIELDFIX_AUTH_ORIGIN="https://fieldfix.example"):
            status, _, header = self.api("login", {**self.creds,"remember":True}, origin="https://fieldfix.example")
            self.assertEqual(status, 200)
            self.assertIn("; Secure", header)
            self.assertTrue(header.startswith("__Secure-"))
        with patch.dict(os.environ, FIELDFIX_AUTH_ORIGIN="http://fieldfix.example"):
            self.assertEqual(self.api("login", {**self.creds,"remember":True}, origin="http://fieldfix.example")[0],503)

    def test_concurrent_rotation_reuse_and_logout(self):
        cookie = self.remembered()
        csrf = self.api("session", cookie=cookie)[1]["csrf"]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: self.api("refresh", {}, cookie=cookie, csrf=csrf), range(2)))
        self.assertEqual(sorted(r[0] for r in results), [200,409])
        current = next(r[2].split(";")[0] for r in results if r[0] == 200)
        self.assertNotEqual(cookie, current)
        self.clock.return_value += 6
        self.assertEqual(self.mutation("refresh", cookie)[0], 401)
        self.assertEqual(self.mutation("refresh", current)[0], 401)
        cookie = self.remembered()
        self.assertIn("Max-Age=0", self.mutation("logout", cookie)[2])
        self.assertEqual(self.mutation("refresh", cookie)[0], 401)
        self.assertEqual(self.mutation("refresh","fieldfix_local_refresh=invalid")[0], 401)

    def test_switch_and_security_changes(self):
        cookie = self.remembered()
        other, _ = self.register("switch-target")
        status, _, header = self.mutation("login", cookie, other)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", header)
        self.assertEqual(self.mutation("refresh", cookie)[0], 401)
        for attr, value in (("role",UserRole.REVIEWER),("is_active",False)):
            cookie = self.remembered()
            with Session(self.engine) as db:
                setattr(db.get(User, self.profile["id"]), attr, value)
                db.commit()
            self.assertEqual(self.mutation("refresh",cookie)[0], 401)

    def test_send_verify_limits(self):
        for i in range(26):
            result = self.api("otp/verify", {"email":self.email,"challenge":"a"*32,"code":"000000"})
        self.assertEqual(result[0],429)
        for i in range(41):
            result = self.api("otp/request", {"email":f"absent{i}@example.com"})
        self.assertEqual(result[0],429)


class MigrationTest(unittest.TestCase):
    def test_round_trip_preserves_account(self):
        with TemporaryDirectory(dir=BACKEND_DIR / ".venv") as folder:
            engine = create_database_engine(Path(folder) / "disposable.db")
            try:
                config = Config(BACKEND_DIR / "alembic.ini")
                with engine.connect() as conn:
                    config.attributes["connection"] = conn
                    command.upgrade(config, "0001")
                    conn.exec_driver_sql("INSERT INTO users(full_name,email,password_hash,role,is_active) VALUES ('Test','a@example.com','original','requester',1)")
                    conn.commit()
                    before = conn.exec_driver_sql("SELECT * FROM users").all()
                    tables = set(inspect(conn).get_table_names())
                    command.upgrade(config, "0004")
                    command.downgrade(config, "0001")
                    self.assertEqual(set(inspect(conn).get_table_names()), tables)
                    self.assertEqual(conn.exec_driver_sql("SELECT * FROM users").all(), before)
                    command.upgrade(config, "head")
                    self.assertEqual(conn.exec_driver_sql("PRAGMA integrity_check").scalar(), "ok")
            finally:
                engine.dispose()
