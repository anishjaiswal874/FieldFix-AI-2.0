"""Cookie credentials are accepted only by the authentication endpoints."""
import hashlib
import hmac
import os
import secrets
import time
from ipaddress import ip_address
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import text

from app.account_policy import account_policy
from app.models import User

PATH = "/api/auth"
SECURE_COOKIE = "__Secure-fieldfix_refresh"
LOCAL_COOKIE = "fieldfix_local_refresh"


def query(db, statement, **params):
    return db.execute(text(statement), params)


def write_lock(db):
    # The SQLite connection uses transactional DDL and deferred transactions.
    # Start fresh and reserve the writer before reading authorization state.
    db.rollback()
    query(db, "UPDATE auth_limit SET hits=hits WHERE 0")


def digest(key, purpose, value):
    return hmac.new(key, (purpose + "\0" + value).encode(), hashlib.sha256).hexdigest()


def now():
    return int(time.time())


def days(name, default):
    try:
        value = int(os.environ.get(name, default))
        if 1 <= value <= 365:
            return value * 86400
    except ValueError:
        pass
    raise HTTPException(503, "Invalid session lifetime configuration.")


def settings():
    origin = os.environ.get("FIELDFIX_AUTH_ORIGIN", "")
    parsed = urlsplit(origin)
    if not parsed.hostname or parsed.username or origin != f"{parsed.scheme}://{parsed.netloc}":
        raise HTTPException(503, "Browser authentication origin is unavailable.")
    local = (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
             and os.environ.get("FIELDFIX_AUTH_LOOPBACK") == "1")
    if parsed.scheme != "https" and not local:
        raise HTTPException(503, "Browser authentication requires HTTPS.")
    return origin, local


def guard(request, bootstrap=False):
    origin, local = settings()
    if local:
        try:
            peer_ok = ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            peer_ok = False
        if not peer_ok or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(403, "Loopback access required.")
    supplied = request.headers.get("origin")
    # Same-origin GET may omit Origin. A custom header requires preflight
    # cross-origin, and the application does not grant credentialed CORS.
    if bootstrap and supplied is None and request.headers.get("x-fieldfix-bootstrap") == "1":
        if request.headers.get("sec-fetch-site", "same-origin") == "same-origin":
            return
    if supplied != origin:
        raise HTTPException(403, "Untrusted authentication origin.")


def cookie_name():
    return LOCAL_COOKIE if settings()[1] else SECURE_COOKIE


def has_cookie(request):
    return any(name in request.cookies for name in (SECURE_COOKIE, LOCAL_COOKIE))


def credential(request):
    return request.cookies.get(cookie_name(), "")


def csrf(request, key):
    value = credential(request)
    return digest(key, "browser csrf", value) if value else None


def guard_change(request, key):
    guard(request)
    expected = csrf(request, key)
    if expected and not secrets.compare_digest(request.headers.get("x-fieldfix-csrf", ""), expected):
        raise HTTPException(403, "Browser session verification failed.")


def set_cookie(response, value, age):
    response.set_cookie(cookie_name(), value, max_age=age, path=PATH,
                        httponly=True, secure=not settings()[1], samesite="strict")


def revoke(db, value, key):
    if value:
        query(db, """UPDATE browser_session SET revoked=1 WHERE digest=:d OR id IN
              (SELECT family FROM spent_refresh WHERE digest=:d)""",
              d=digest(key, "refresh", value))


def finish_login(db, request, response, user_id, remember, key):
    if not remember and not has_cookie(request):
        return
    guard_change(request, key)
    revoke(db, credential(request), key)
    if remember:
        token = secrets.token_urlsafe(48)
        stamp = now()
        maximum = days("FIELDFIX_SESSION_ABSOLUTE_DAYS", 30)
        days("FIELDFIX_SESSION_IDLE_DAYS", 7)
        query(db, """INSERT INTO browser_session(id,user_id,digest,created,used,expires)
              VALUES (:id,:u,:d,:t,:t,:end)""", id=secrets.token_urlsafe(24), u=user_id,
              d=digest(key, "refresh", token), t=stamp, end=stamp + maximum)
        set_cookie(response, token, maximum)
    else:
        set_cookie(response, "", 0)


def rotate(db, request, response, key):
    guard_change(request, key)
    value = credential(request)
    if not value:
        raise HTTPException(401, "Sign in again.")
    write_lock(db)
    stamp = now()
    hashed = digest(key, "refresh", value)
    row = query(db, "SELECT * FROM browser_session WHERE digest=:d", d=hashed).mappings().first()
    if not row:
        spent = query(db, "SELECT * FROM spent_refresh WHERE digest=:d", d=hashed).mappings().first()
        if spent:
            # Racing requests never receive another access or refresh token.
            if stamp - spent["rotated"] <= 5:
                raise HTTPException(409, "A concurrent refresh completed.")
            revoke(db, value, key)
            db.commit()
        raise HTTPException(401, "Sign in again.")
    user = db.get(User, row["user_id"])
    policy = account_policy(db, row["user_id"])
    idle = days("FIELDFIX_SESSION_IDLE_DAYS", 7)
    if (row["revoked"] or row["expires"] <= stamp or row["used"] + idle <= stamp
            or not user or not user.is_active or policy.archived or policy.temporary_until is not None):
        revoke(db, value, key)
        db.commit()
        raise HTTPException(401, "Sign in again.")
    token = secrets.token_urlsafe(48)
    query(db, "INSERT INTO spent_refresh VALUES(:d,:f,:t)", d=hashed, f=row["id"], t=stamp)
    query(db, "UPDATE browser_session SET digest=:d,used=:t WHERE id=:f",
          d=digest(key, "refresh", token), t=stamp, f=row["id"])
    uid = user.id
    db.commit()
    set_cookie(response, token, row["expires"] - stamp)
    return uid, policy.revision


def cleanup(db, stamp):
    query(db, "DELETE FROM email_challenge WHERE expires<=:t", t=stamp)
    query(db, "DELETE FROM auth_limit WHERE since<=:t", t=stamp-3600)
    query(db, "DELETE FROM browser_session WHERE expires<=:t", t=stamp)
