from collections.abc import Awaitable, Callable
from datetime import datetime
from sqlite3 import SQLITE_CONSTRAINT_UNIQUE
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from app import browser_sessions as browser, email_otp as otp
from app.account_policy import account_policy, registration_enabled
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.auth import create_access_token, get_current_user, get_signing_key, hash_password, verify_password
from app.database import get_session
from app.models import User, UserRole


class AuthRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handle = super().get_route_handler()

        async def handle_without_echoing_credentials(request: Request) -> Response:
            try:
                response = await handle(request)
                response.headers["Cache-Control"] = "no-store"
                return response
            except HTTPException as error:
                error.headers = {**(error.headers or {}), "Cache-Control": "no-store"}
                raise
            except RequestValidationError as error:
                # FastAPI's default errors can include the original request body or field value.
                details = [{key: issue[key] for key in ("loc", "msg", "type")} for issue in error.errors()]
                return JSONResponse(status_code=422, content={"detail": details}, headers={"Cache-Control": "no-store"})

        return handle_without_echoing_credentials


router = APIRouter(prefix="/api/auth", tags=["Authentication"], route_class=AuthRoute)
DatabaseSession = Annotated[Session, Depends(get_session)]


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        normalized = email.casefold()
        if len(normalized) > 320:
            raise ValueError("Email address is too long.")
        return normalized


class RegisterInput(EmailInput):
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    password: SecretStr = Field(min_length=15, max_length=128)


class LoginInput(EmailInput):
    password: SecretStr = Field(max_length=128)
    remember: bool = Field(default=False, strict=True)
    portal: UserRole | None = None


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    full_name: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime


class AccessToken(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterInput, session: DatabaseSession) -> UserProfile:
    if not registration_enabled(session):
        raise HTTPException(403, "Requester registration is currently closed. Contact your administrator.")
    password_hash = hash_password(body.password.get_secret_value())
    browser.write_lock(session)
    if not registration_enabled(session):
        raise HTTPException(403, "Requester registration is currently closed. Contact your administrator.")
    user = User(
        full_name=body.full_name,
        email=body.email,
        password_hash=password_hash,
        role=UserRole.REQUESTER,
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        # The unique email constraint also handles simultaneous registration attempts.
        if getattr(error.orig, "sqlite_errorcode", None) == SQLITE_CONSTRAINT_UNIQUE:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email address is already registered.") from None
        raise
    session.refresh(user)
    return UserProfile.model_validate(user)


@router.get("/registration-status")
def registration_status(session: DatabaseSession):
    return {"allowed": registration_enabled(session)}


@router.post("/login")
def login(
    body: LoginInput,
    request: Request,
    response: Response,
    session: DatabaseSession,
    key: Annotated[bytes, Depends(get_signing_key)],
) -> AccessToken:
    if body.remember or browser.has_cookie(request):
        browser.guard_change(request, key)
        browser.write_lock(session)
        browser.cleanup(session, browser.now())
    user = session.exec(select(User).where(User.email == body.email)).one_or_none()
    valid = verify_password(body.password.get_secret_value(), user.password_hash if user else None)
    if not valid or user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")
    if body.portal is not None and body.portal != user.role:
        raise HTTPException(409, "Choose the sign-in tab for your assigned role.")
    policy = account_policy(session, user.id)
    if policy.archived or (policy.temporary_until is not None and policy.temporary_until <= browser.now()):
        raise HTTPException(401, "Invalid email or password.")
    if policy.temporary_until is not None:
        raise HTTPException(428, "Set a new password using your temporary password.")

    response.headers["Cache-Control"] = "no-store"
    uid = user.id
    browser.finish_login(session, request, response, uid, body.remember, key)
    session.commit()
    return AccessToken(access_token=create_access_token(uid, key, policy.revision))


@router.get("/me")
def read_my_profile(response: Response, user: Annotated[User, Depends(get_current_user)]) -> UserProfile:
    response.headers["Cache-Control"] = "no-store"
    return UserProfile.model_validate(user)


SigningKey = Annotated[bytes, Depends(get_signing_key)]
Identity = Annotated[User, Depends(get_current_user)]
Mailer = Annotated[otp.SmtpMailer, Depends(otp.get_mailer)]


class VerifyCode(EmailInput):
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{32}$")
    code: SecretStr = Field(min_length=6, max_length=6)

    @field_validator("code")
    @classmethod
    def numeric(cls, code):
        value = code.get_secret_value()
        if not value.isascii() or not value.isdecimal():
            raise ValueError("Enter six digits.")
        return code


class VerifyLogin(VerifyCode):
    remember: bool = Field(default=False, strict=True)
    portal: UserRole | None = None


class EnrollmentProof(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(max_length=128)


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemporaryPasswordChange(EmailInput):
    temporary_password: SecretStr = Field(max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)


@router.post("/change-temporary-password")
def change_temporary_password(body: TemporaryPasswordChange, session: DatabaseSession):
    user = session.exec(select(User).where(User.email == body.email)).one_or_none()
    valid = verify_password(body.temporary_password.get_secret_value(), user.password_hash if user else None)
    policy = account_policy(session, user.id) if user else None
    if (not valid or not user or not user.is_active or policy.archived
            or policy.temporary_until is None or policy.temporary_until <= browser.now()):
        raise HTTPException(401, "Invalid or expired temporary password.")
    if body.new_password.get_secret_value() == body.temporary_password.get_secret_value():
        raise HTTPException(422, "Choose a different new password.")
    uid, current_hash = user.id, user.password_hash
    new_hash = hash_password(body.new_password.get_secret_value())
    browser.write_lock(session)
    user = session.get(User, uid)
    if (not user or not user.is_active or user.email != body.email or user.password_hash != current_hash
            or account_policy(session, uid) != policy or policy.temporary_until <= browser.now()):
        raise HTTPException(401, "Invalid or expired temporary password.")
    user.password_hash = new_hash
    session.flush()
    browser.query(session, "UPDATE account_policy SET temporary_until=NULL WHERE user_id=:id", id=user.id)
    session.commit()
    return {"changed": True}


@router.post("/otp/request", status_code=202)
def request_code(body: EmailInput, request: Request, session: DatabaseSession, key: SigningKey,
                 mailer: Mailer, tasks: BackgroundTasks):
    return otp.issue(session, request, body.email, "login", key, mailer, tasks)


@router.post("/otp/verify")
def verify_code(body: VerifyLogin, request: Request, response: Response, session: DatabaseSession, key: SigningKey):
    browser.guard_change(request, key)
    uid = otp.consume(session, request, body, "login", key)
    policy = account_policy(session, uid)
    if policy.archived or policy.temporary_until is not None:
        session.commit()
        raise HTTPException(401, "Invalid or expired verification code.")
    if body.portal is not None and body.portal != session.get(User, uid).role:
        session.commit()  # A verified code is single-use even on the wrong tab.
        raise HTTPException(409, "Choose the sign-in tab for your assigned role and request a fresh code.")
    browser.finish_login(session, request, response, uid, body.remember, key)
    session.commit()
    return AccessToken(access_token=create_access_token(uid, key, policy.revision))


@router.post("/enroll/request", status_code=202)
def enroll(body: EnrollmentProof, user: Identity, request: Request, session: DatabaseSession,
           key: SigningKey, mailer: Mailer, tasks: BackgroundTasks):
    uid = user.id
    browser.guard(request)
    browser.write_lock(session)
    current = session.get(User, uid)
    if not current or not current.is_active or not verify_password(body.password.get_secret_value(), current.password_hash):
        raise HTTPException(401, "Password authentication required.")
    return otp.issue(session, request, current.email, "enroll", key, mailer, tasks, uid)


@router.post("/enroll/verify")
def verify_enrollment(body: VerifyCode, user: Identity, request: Request, session: DatabaseSession, key: SigningKey):
    uid = otp.consume(session, request, body, "enroll", key, user.id)
    browser.query(session, """INSERT OR REPLACE INTO verified_mailbox VALUES(:u,:e,:t)""",
                  u=uid, e=body.email, t=browser.now())
    session.commit()
    return {"verified": True}


@router.get("/session")
def bootstrap(request: Request, key: SigningKey):
    if not browser.has_cookie(request):
        return {"csrf": None}
    browser.guard(request, bootstrap=True)
    return {"csrf": browser.csrf(request, key)}


@router.post("/refresh")
def refresh(body: Empty, request: Request, response: Response, session: DatabaseSession, key: SigningKey):
    uid, revision = browser.rotate(session, request, response, key)
    return AccessToken(access_token=create_access_token(uid, key, revision))


@router.post("/logout")
def logout(body: Empty, request: Request, response: Response, session: DatabaseSession, key: SigningKey):
    if browser.has_cookie(request):
        browser.guard_change(request, key)
        browser.write_lock(session)
        browser.revoke(session, browser.credential(request), key)
        session.commit()
        browser.set_cookie(response, "", 0)
    return {"signed_out": True}
