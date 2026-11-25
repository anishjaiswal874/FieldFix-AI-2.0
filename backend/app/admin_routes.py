from collections.abc import Iterator
from contextlib import contextmanager
from secrets import token_urlsafe
from sqlite3 import SQLITE_BUSY, SQLITE_LOCKED
from typing import Annotated, Generic, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints
from sqlalchemy import func, or_, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import joinedload
from sqlmodel import Session, select

from app import ai_services
from app import browser_sessions as browser
from app.account_policy import account_policy, registration_enabled
from app.auth import authentication_required, hash_password, require_roles
from app.auth_routes import AuthRoute, UserProfile
from app.models import Category, MaintenanceTeam, User, UserRole, WorkOrder, WorkOrderStatus
from app.request_routes import Database, RecordId

router = APIRouter(prefix="/api/admin", tags=["Administration"], route_class=AuthRoute)
Administrator = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120, pattern=r"^[^\x00-\x1f\x7f]+$")]
Identifier = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Active = Annotated[bool, Field(strict=True)]
Search = Annotated[str, Query(max_length=120)]
PageNumber = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=50)]
Row = TypeVar("Row")
ACTIVE_ORDERS = (WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS)
AI_SAFETY_DISCLAIMER = "AI outputs are recommendations only. A qualified person must assess structural safety."


class Page(BaseModel, Generic[Row]):
    items: list[Row]
    total: int
    page: int
    per_page: int


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: UserRole
    is_active: Active
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160,
                                               pattern=r"^[^\x00-\x1f\x7f]+$")] | None = None
    email: EmailStr | None = None


class ManagedUser(UserProfile):
    archived: bool = False
    password_reset_pending: bool = False


class ConfirmAccountAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Active


def managed_user(session: Session, user: User) -> ManagedUser:
    policy = account_policy(session, user.id)
    return ManagedUser(**UserProfile.model_validate(user).model_dump(), archived=policy.archived,
                       password_reset_pending=policy.temporary_until is not None)


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    default_team_id: Identifier | None = None
    is_active: Active = True


class TeamInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Name
    specialty: Name
    is_active: Active = True


class TeamOption(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool


class CategoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    default_team: TeamOption | None
    is_active: bool


class TeamView(TeamOption):
    specialty: str
    active_work_orders: int


class Dashboard(BaseModel):
    total_users: int
    active_roles: dict[UserRole, int]
    active_categories: int
    active_teams: int
    active_work_orders: int


class SettingsView(BaseModel):
    ai_safety_disclaimer: str = AI_SAFETY_DISCLAIMER
    requester_self_registration: bool = True
    recommendations: dict[str, str]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requester_self_registration: Active


@contextmanager
def admin_write(session: Session, actor_id: int) -> Iterator[None]:
    # Release the authorization read snapshot. Take SQLite's write lock before
    # rechecking the actor or reading invariants that another writer could change.
    revision = account_policy(session, actor_id).revision
    session.rollback()
    try:
        with session.begin():
            authorized = session.exec(update(User).where(
                User.id == actor_id, User.role == UserRole.ADMIN, User.is_active.is_(True),
            ).values(is_active=User.is_active).returning(User.id)).one_or_none()
            if authorized is None:
                actor = session.get(User, actor_id)
                if actor is None or not actor.is_active:
                    raise authentication_required()
                raise HTTPException(403, "Insufficient permissions.")
            if account_policy(session, actor_id).revision != revision:
                raise authentication_required()
            yield
    except OperationalError as error:
        if getattr(error.orig, "sqlite_errorcode", 0) & 0xff in (SQLITE_BUSY, SQLITE_LOCKED):
            raise HTTPException(503, "The database is busy. Try again.", headers={"Retry-After": "1"}) from None
        raise


def page_rows(session, model, records, conditions, page, per_page):
    total = session.exec(select(func.count()).select_from(model).where(*conditions)).one()
    page = min(page, max(1, (total + per_page - 1) // per_page))
    rows = session.exec(records.where(*conditions).offset((page - 1) * per_page).limit(per_page)).all()
    return rows, {"total": total, "page": page, "per_page": per_page}


def ensure_unique_name(session, model, name, current_id=None):
    match = select(model.id).where(model.name.collate("NOCASE") == name)
    if current_id is not None:
        match = match.where(model.id != current_id)
    if session.exec(match.limit(1)).first() is not None:
        raise HTTPException(409, "That name is already in use.")


def require_record(session: Session, model, identifier: int, label: str):
    record = session.get(model, identifier)
    if record is None:
        raise HTTPException(404, f"{label} not found.")
    return record


@router.get("/dashboard")
def dashboard(_admin: Administrator, session: Database) -> Dashboard:
    roles = session.exec(select(User.role, func.count()).where(User.is_active.is_(True)).group_by(User.role))
    return Dashboard(
        total_users=session.exec(select(func.count()).select_from(User)).one(),
        active_roles=dict(roles.all()),
        active_categories=session.exec(select(func.count()).select_from(Category).where(Category.is_active.is_(True))).one(),
        active_teams=session.exec(select(func.count()).select_from(MaintenanceTeam).where(MaintenanceTeam.is_active.is_(True))).one(),
        active_work_orders=session.exec(select(func.count()).select_from(WorkOrder).where(WorkOrder.status.in_(ACTIVE_ORDERS))).one(),
    )


@router.get("/users")
def users(_admin: Administrator, session: Database, q: Search = "", role: UserRole | None = None,
          is_active: bool | None = None, page: PageNumber = 1, per_page: PageSize = 10) -> Page[ManagedUser]:
    search = q.strip()
    conditions = []
    if search:
        conditions.append(or_(User.full_name.icontains(search, autoescape=True), User.email.icontains(search, autoescape=True)))
    if role is not None:
        conditions.append(User.role == role)
    if is_active is not None:
        conditions.append(User.is_active.is_(is_active))
    rows, pagination = page_rows(session, User, select(User).order_by(User.full_name.collate("NOCASE"), User.id),
                                 conditions, page, per_page)
    return Page(items=[managed_user(session, row) for row in rows], **pagination)


@router.put("/users/{user_id}")
def update_user(user_id: RecordId, body: UserUpdate, admin: Administrator, session: Database) -> ManagedUser:
    with admin_write(session, admin.id):
        user = require_record(session, User, user_id, "User")
        policy = account_policy(session, user.id)
        if policy.archived:
            raise HTTPException(409, "Archived accounts cannot be edited.")
        if user.role == UserRole.ADMIN and user.is_active and (body.role != UserRole.ADMIN or not body.is_active):
            other_admin = select(User.id).where(
                User.role == UserRole.ADMIN, User.is_active.is_(True), User.id != user.id,
                text("NOT EXISTS (SELECT 1 FROM account_policy WHERE user_id=users.id "
                     "AND (temporary_until IS NOT NULL OR archived=1))"),
            )
            if session.exec(other_admin.limit(1)).first() is None:
                raise HTTPException(409, "At least one administrator must remain active.")
        user.role = body.role
        user.is_active = body.is_active
        if body.full_name is not None:
            user.full_name = body.full_name
        if body.email is not None:
            email = body.email.casefold()
            if len(email) > 320:
                raise HTTPException(422, "Email address is too long.")
            if session.exec(select(User.id).where(User.email == email, User.id != user.id)).first() is not None:
                raise HTTPException(409, "That email address is already in use.")
            user.email = email  # Revision 0002 invalidates the previous verified mailbox.
        session.flush()
        return managed_user(session, user)


@router.post("/users/{user_id}/archive")
def archive_user(user_id: RecordId, body: ConfirmAccountAction, admin: Administrator,
                 session: Database) -> ManagedUser:
    if not body.confirm:
        raise HTTPException(422, "Confirm that this account should be archived.")
    with admin_write(session, admin.id):
        user = require_record(session, User, user_id, "User")
        if user.role == UserRole.ADMIN:
            raise HTTPException(409, "Administrator accounts cannot be archived.")
        user.is_active = False
        session.flush()
        browser.query(session, """INSERT INTO account_policy(user_id,archived) VALUES(:id,1)
            ON CONFLICT(user_id) DO UPDATE SET archived=1""", id=user.id)
        return managed_user(session, user)


@router.post("/users/{user_id}/password-reset")
def reset_password(user_id: RecordId, body: ConfirmAccountAction, admin: Administrator, session: Database):
    if not body.confirm:
        raise HTTPException(422, "Confirm that you want to issue a temporary password.")
    actor = admin.id
    with admin_write(session, actor):
        user = require_record(session, User, user_id, "User")
        if user.id == actor or not user.is_active or account_policy(session, user.id).archived:
            raise HTTPException(409, "Choose another active, unarchived account.")
        temporary = token_urlsafe(24)
        user.password_hash = hash_password(temporary)
        session.flush()
        browser.query(session, "UPDATE account_policy SET temporary_until=:until WHERE user_id=:id",
                      until=browser.now() + 86400, id=user.id)
        return {"temporary_password": temporary, "expires_in": 86400}


@router.get("/categories")
def categories(_admin: Administrator, session: Database, q: Search = "", page: PageNumber = 1,
               per_page: PageSize = 10) -> Page[CategoryView]:
    search = q.strip()
    conditions = [Category.name.icontains(search, autoescape=True)] if search else []
    records = select(Category).options(joinedload(Category.default_team)).order_by(Category.name.collate("NOCASE"), Category.id)
    rows, pagination = page_rows(session, Category, records, conditions, page, per_page)
    return Page(items=[CategoryView.model_validate(row) for row in rows], **pagination)


def save_category(session: Session, body: CategoryInput, category_id: int | None = None) -> CategoryView:
    category = Category() if category_id is None else require_record(session, Category, category_id, "Category")
    ensure_unique_name(session, Category, body.name, category_id)
    team = session.get(MaintenanceTeam, body.default_team_id) if body.default_team_id is not None else None
    if body.default_team_id is not None and (team is None or (not team.is_active and category.default_team_id != team.id)):
        raise HTTPException(422, "Select an active default team.")
    category.name = body.name
    category.is_active = body.is_active
    category.default_team = team
    session.add(category)
    session.flush()
    return CategoryView.model_validate(category)


@router.post("/categories", status_code=201)
def create_category(body: CategoryInput, admin: Administrator, session: Database) -> CategoryView:
    with admin_write(session, admin.id):
        return save_category(session, body)


@router.put("/categories/{category_id}")
def update_category(category_id: RecordId, body: CategoryInput, admin: Administrator, session: Database) -> CategoryView:
    with admin_write(session, admin.id):
        return save_category(session, body, category_id)


def team_workloads():
    return select(WorkOrder.assigned_team_id.label("team_id"), func.count().label("orders")).where(
        WorkOrder.status.in_(ACTIVE_ORDERS),
    ).group_by(WorkOrder.assigned_team_id).subquery()


@router.get("/teams")
def teams(_admin: Administrator, session: Database, q: Search = "", page: PageNumber = 1,
          per_page: PageSize = 10) -> Page[TeamView]:
    search = q.strip()
    conditions = [or_(MaintenanceTeam.name.icontains(search, autoescape=True),
                      MaintenanceTeam.specialty.icontains(search, autoescape=True))] if search else []
    workloads = team_workloads()
    records = select(MaintenanceTeam, func.coalesce(workloads.c.orders, 0)).outerjoin(
        workloads, MaintenanceTeam.id == workloads.c.team_id,
    ).order_by(MaintenanceTeam.name.collate("NOCASE"), MaintenanceTeam.id)
    rows, pagination = page_rows(session, MaintenanceTeam, records, conditions, page, per_page)
    return Page(items=[TeamView(**row.model_dump(), active_work_orders=count) for row, count in rows], **pagination)


@router.get("/team-options")
def active_team_options(_admin: Administrator, session: Database) -> list[TeamOption]:
    teams = session.exec(select(MaintenanceTeam).where(MaintenanceTeam.is_active.is_(True))
                         .order_by(MaintenanceTeam.name.collate("NOCASE"), MaintenanceTeam.id))
    return [TeamOption.model_validate(team) for team in teams]


def save_team(session: Session, body: TeamInput, team_id: int | None = None) -> TeamView:
    team = MaintenanceTeam() if team_id is None else require_record(session, MaintenanceTeam, team_id, "Team")
    ensure_unique_name(session, MaintenanceTeam, body.name, team_id)
    team.sqlmodel_update(body.model_dump())
    session.add(team)
    session.flush()
    orders = session.exec(select(func.count()).select_from(WorkOrder).where(
        WorkOrder.assigned_team_id == team.id, WorkOrder.status.in_(ACTIVE_ORDERS),
    )).one()
    return TeamView(**team.model_dump(), active_work_orders=orders)


@router.post("/teams", status_code=201)
def create_team(body: TeamInput, admin: Administrator, session: Database) -> TeamView:
    with admin_write(session, admin.id):
        return save_team(session, body)


@router.put("/teams/{team_id}")
def update_team(team_id: RecordId, body: TeamInput, admin: Administrator, session: Database) -> TeamView:
    with admin_write(session, admin.id):
        return save_team(session, body, team_id)


def settings_view(session: Session) -> SettingsView:
    return SettingsView(requester_self_registration=registration_enabled(session),
                        recommendations=ai_services.availability())


@router.get("/settings")
def settings(_admin: Administrator, session: Database) -> SettingsView:
    return settings_view(session)


@router.put("/settings")
def update_settings(body: SettingsUpdate, admin: Administrator, session: Database) -> SettingsView:
    with admin_write(session, admin.id):
        browser.query(session, """INSERT INTO app_settings VALUES(1,:allowed)
            ON CONFLICT(id) DO UPDATE SET requester_self_registration=excluded.requester_self_registration""",
            allowed=int(body.requester_self_registration))
        return settings_view(session)
