from concurrent.futures import ThreadPoolExecutor
from secrets import token_urlsafe
from threading import Barrier

from sqlmodel import Session, select

import test_auth
from app.auth import verify_password
from app.browser_sessions import query
from app.auth_routes import RegisterInput
from app.bootstrap_admin import BootstrapError, create_first_admin
from app.models import MaintenanceRequest, User, UserRole, WorkOrder, WorkOrderStatus


class AdminTests(test_auth.ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.credentials = {"email": "bootstrap@example.com", "password": token_urlsafe(24)}
        seed = RegisterInput(full_name="Temporary Admin", **cls.credentials)
        with Session(cls.engine) as session:
            cls.admin_id = create_first_admin(session, seed)

    def setUp(self):
        self.admin = self.login(self.credentials)

    def admin_call(self, path, body=None, *, token=None, method=None):
        return self.call("/api/admin" + path, body, self.admin if token is None else token, method=method)

    def edit_user(self, user_id, role, active, *, token=None):
        return self.admin_call(f"/users/{user_id}", {"role": role, "is_active": active}, token=token, method="PUT")

    def test_profile_reset_and_archive(self):
        credentials, profile = self.register("managed-user")
        path = f"/users/{profile['id']}"
        bearer = self.login(credentials)
        with Session(self.engine) as session:
            request = MaintenanceRequest(requester_id=profile["id"], title="Keep history",
                                         description="A maintenance report", location="Gym")
            session.add(request)
            query(session, "INSERT INTO verified_mailbox VALUES(:uid,:email,1)",
                  uid=profile["id"], email=credentials["email"])
            session.commit()
            request_id = request.id

        body = {"full_name": "Updated account", "email": "updated-member@example.com", "role": "requester", "is_active": True}
        self.assertEqual(self.admin_call(path, body, token=bearer, method="PUT")[0], 403)
        self.assertEqual(self.admin_call(path, {**body, "email": self.credentials["email"]}, method="PUT")[0], 409)
        code, _, user = self.admin_call(path, body, method="PUT")
        self.assertEqual((code, user["full_name"], user["email"]), (200, body["full_name"], body["email"]))
        self.assertEqual(self.call("/api/auth/me", token=bearer)[0], 401)
        with Session(self.engine) as session:
            self.assertIsNone(query(session, "SELECT email FROM verified_mailbox WHERE user_id=:id", id=profile["id"]).first())
        credentials["email"] = body["email"]
        bearer = self.login(credentials)
        self.assertEqual(self.admin_call(path + "/password-reset", {"confirm": False})[0], 422)
        code, headers, reset = self.admin_call(path + "/password-reset", {"confirm": True})
        self.assertEqual((code, headers["Cache-Control"], reset["expires_in"]), (200, "no-store", 86400))
        self.assertEqual(self.call("/api/auth/me", token=bearer)[0], 401)
        self.assertEqual(self.call("/api/auth/login", credentials)[0], 401)
        temporary = reset["temporary_password"]
        self.assertEqual(self.call("/api/auth/login", {**credentials, "password": temporary})[0], 428)
        changed = {"email": body["email"], "temporary_password": temporary, "new_password": token_urlsafe(24)}
        self.assertEqual(self.call("/api/auth/change-temporary-password", changed)[0], 200)
        self.assertEqual(self.call("/api/auth/change-temporary-password", changed)[0], 401)
        bearer = self.login({"email": body["email"], "password": changed["new_password"]})
        self.assertEqual(self.admin_call(path + "/archive", {"confirm": False})[0], 422)
        code, _, archived = self.admin_call(path + "/archive", {"confirm": True})
        self.assertEqual((code, archived["archived"], archived["is_active"]), (200, True, False))
        self.assertEqual(self.call("/api/auth/me", token=bearer)[0], 401)
        self.assertEqual(self.admin_call(path, body, method="PUT")[0], 409)
        self.assertEqual(self.admin_call(f"/users/{self.admin_id}/archive", {"confirm": True})[0], 409)
        with Session(self.engine) as session:
            self.assertEqual(session.get(MaintenanceRequest, request_id).requester_id, profile["id"])

    def test_registration_setting_persists_and_protects_policy(self):
        field = {"requester_self_registration": False}
        credentials, _ = self.register("settings-requester")
        self.assertEqual(self.admin_call("/settings", field, token=self.login(credentials), method="PUT")[0], 403)
        self.assertEqual(self.admin_call("/settings", {"requester_self_registration": "false"}, method="PUT")[0], 422)
        self.assertEqual(self.admin_call("/settings", field, method="PUT")[0], 200)
        try:
            self.assertFalse(self.admin_call("/settings")[2]["requester_self_registration"])
            self.assertEqual(self.call("/api/auth/register", {"full_name":"Paused request", **credentials})[0], 403)
        finally:
            self.assertEqual(self.admin_call("/settings", {"requester_self_registration": True}, method="PUT")[0], 200)

    def test_admin_guards_user_updates_and_last_admin(self):
        credentials, profile = self.register("admin-tested-user")
        requester = self.login(credentials)
        self.assertEqual(self.call("/api/admin/dashboard")[0], 401)
        for path in ("/dashboard", "/users", "/categories", "/teams", "/team-options", "/settings"):
            self.assertEqual(self.admin_call(path, token=requester)[0], 403)
        self.assertEqual(self.edit_user(profile["id"], "admin", True, token=requester)[0], 403)
        self.assertEqual(self.admin_call("/categories", {"name": "Unauthorized"}, token=requester)[0], 403)
        self.assertEqual(self.admin_call("/teams", {"name": "Unauthorized", "specialty": "None"}, token=requester)[0], 403)
        self.assertEqual(self.call("/api/auth/register", {
            "email": "escalation@example.com", "full_name": "Example", "password": token_urlsafe(24), "role": "admin",
        })[0], 422)

        code, _, row = self.edit_user(profile["id"], "reviewer", True)
        self.assertEqual(code, 200, row)
        self.assertEqual(row["role"], "reviewer")
        self.assertNotIn("password_hash", row)
        self.assertEqual(self.call("/api/auth/me", token=requester)[0], 401)
        requester = self.login(credentials)
        self.assertEqual(self.call("/api/reviewer/dashboard", token=requester)[0], 200)
        self.assertEqual(self.call("/api/requests", token=requester)[0], 403)
        self.assertEqual(self.admin_call("/users", token=requester)[0], 403)
        users = self.admin_call("/users?q=admin-tested-user&role=reviewer&per_page=1")[2]
        self.assertEqual((users["total"], users["items"]), (1, [row]))
        self.assertEqual(self.edit_user(profile["id"], "reviewer", False)[0], 200)
        self.assertEqual(self.call("/api/reviewer/dashboard", token=requester)[0], 401)
        self.assertEqual(self.call("/api/auth/login", credentials)[0], 401)
        self.assertEqual(self.edit_user(self.admin_id, "admin", False)[0], 409)
        self.assertEqual(self.edit_user(self.admin_id, "reviewer", True)[0], 409)
        self.assertEqual(self.edit_user(99999, "requester", True)[0], 404)
        self.assertEqual(self.admin_call("/users/1", {"role": "admin", "is_active": "true"}, method="PUT")[0], 422)

    def test_bootstrap_is_one_time_and_argon2id(self):
        duplicate = RegisterInput(full_name="Another Operator", email="another@example.com", password=token_urlsafe(24))
        with Session(self.engine) as session:
            with self.assertRaisesRegex(BootstrapError, "already exists"):
                create_first_admin(session, duplicate)
            user = session.get(User, self.admin_id)
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            self.assertTrue(verify_password(self.credentials["password"], user.password_hash))
            self.assertIsNone(session.exec(select(User).where(User.email == duplicate.email)).first())

    def test_catalog_integrity_workload_and_settings(self):
        code, _, team = self.admin_call("/teams", {"name": "Building Services", "specialty": "Repairs"})
        self.assertEqual(code, 201, team)
        self.assertEqual(team["active_work_orders"], 0)
        self.assertEqual(self.admin_call("/teams", {"name": "building services", "specialty": "Other"})[0], 409)
        category_input = {"name": "Drainage", "default_team_id": team["id"]}
        code, _, category = self.admin_call("/categories", category_input)
        self.assertEqual(code, 201, category)
        self.assertEqual(category["default_team"]["id"], team["id"])
        self.assertEqual(self.admin_call("/categories", {"name": "  drainage  "})[0], 409)
        self.assertEqual(self.admin_call("/categories", {"name": "Invalid Team", "default_team_id": 9999})[0], 422)
        self.assertEqual(self.admin_call("/categories", {"name": " "})[0], 422)
        self.assertEqual(self.admin_call("/categories", {"name": "No ID", "default_team_id": True})[0], 422)

        requester_credentials, requester = self.register("catalog-requester")
        requester_token = self.login(requester_credentials)
        self.assertIn(category["id"], [row["id"] for row in self.call("/api/requests/categories", token=requester_token)[2]])
        with Session(self.engine) as session:
            for status in (WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS, WorkOrderStatus.RESOLVED):
                request = MaintenanceRequest(requester_id=requester["id"], title="Fixture", description="Fixture",
                                             location="Building", selected_category_id=category["id"])
                session.add(request)
                session.flush()
                session.add(WorkOrder(request_id=request.id, assigned_team_id=team["id"], status=status))
            session.commit()

        team_path = f'/teams/{team["id"]}'
        code, _, saved = self.admin_call(team_path, {
            "name": "Building Services", "specialty": "General maintenance", "is_active": False,
        }, method="PUT")
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["active_work_orders"], 2)
        self.assertEqual(self.admin_call("/teams?q=General+maintenance")[2]["items"], [saved])
        self.assertNotIn(team["id"], [row["id"] for row in self.admin_call("/team-options")[2]])
        self.assertEqual(self.admin_call("/categories", {"name": "Inactive assignment", "default_team_id": team["id"]})[0], 422)

        code, _, renamed = self.admin_call(f'/categories/{category["id"]}', category_input | {
            "name": "Water Systems", "is_active": False,
        }, method="PUT")
        self.assertEqual(code, 200, renamed)
        self.assertEqual((renamed["id"], renamed["default_team"]["is_active"]), (category["id"], False))
        self.assertNotIn(category["id"], [row["id"] for row in self.call("/api/requests/categories", token=requester_token)[2]])
        self.assertEqual(self.admin_call("/categories?q=Water+Systems&per_page=1")[2]["items"], [renamed])
        with Session(self.engine) as session:
            records = session.exec(select(MaintenanceRequest).where(MaintenanceRequest.requester_id == requester["id"])).all()
            self.assertEqual(len(records), 3)
            self.assertTrue(all(record.selected_category.name == "Water Systems" for record in records))
            self.assertTrue(all(record.work_order.assigned_team_id == team["id"] for record in records))

        dashboard = self.admin_call("/dashboard")[2]
        self.assertEqual(dashboard["active_work_orders"], 2)
        settings = self.admin_call("/settings")[2]
        self.assertTrue(settings["requester_self_registration"])
        self.assertIn("structural safety", settings["ai_safety_disclaimer"])
        self.assertEqual(self.admin_call("/settings", {"ai_safety_disclaimer": ""}, method="PUT")[0], 422)

    def test_simultaneous_demotions_preserve_an_active_admin(self):
        credentials, account = self.register("second-admin")
        second_id = account["id"]
        self.assertEqual(self.edit_user(second_id, "admin", True)[0], 200)
        second_token = self.login(credentials)
        start = Barrier(2, timeout=5)

        def demote(case):
            identifier, token = case
            start.wait()
            return self.edit_user(identifier, "reviewer", True, token=token)[0]

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(demote, [(self.admin_id, self.admin), (second_id, second_token)]))
        self.assertEqual(sorted(results), [200, 409])
        with Session(self.engine) as session:
            remaining = session.exec(select(User.id).where(User.role == UserRole.ADMIN, User.is_active.is_(True))).all()
        self.assertEqual(len(remaining), 1)
        if remaining[0] == second_id:
            self.assertEqual(self.edit_user(self.admin_id, "admin", True, token=second_token)[0], 200)
            self.admin = self.login(self.credentials)
        self.assertEqual(self.edit_user(second_id, "reviewer", True)[0], 200)
