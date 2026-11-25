import os
from base64 import b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from collections.abc import Callable
from secrets import token_urlsafe
from time import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import HashVerificationError, UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlmodel import Session

from app.account_policy import account_policy
from app.database import get_session
from app.models import User, UserRole


JWT_ALGORITHM = "HS256"
JWT_ISSUER = "fieldfix-ai"
JWT_AUDIENCE = "fieldfix-api"
ACCESS_TOKEN_SECONDS = 20 * 60

password_hasher = PasswordHash((Argon2Hasher(),))
dummy_password_hash = password_hasher.hash(token_urlsafe(32))
bearer_scheme = HTTPBearer(auto_error=False)


def load_signing_key() -> bytes:
    secret = os.environ.get("FIELDFIX_JWT_SECRET", "")
    config_error = (
        "Set FIELDFIX_JWT_SECRET to at least 32 random bytes encoded as unpadded base64url. "
        "Generate it with secrets.token_urlsafe(32)."
    )
    try:
        encoded = secret.encode("ascii")
        key = b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (Base64Error, UnicodeEncodeError):
        raise RuntimeError(config_error) from None

    if len(key) < 32 or len(set(key)) < 16 or urlsafe_b64encode(key).rstrip(b"=") != encoded:
        raise RuntimeError(config_error)
    return key


def get_signing_key(request: Request) -> bytes:
    return request.app.state.jwt_signing_key


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, stored_hash: str | None) -> bool:
    # Verify a dummy hash when an account is absent to reduce timing differences.
    try:
        matches = password_hasher.verify(password, stored_hash or dummy_password_hash)
    except (HashVerificationError, UnknownHashError):
        return False
    return bool(stored_hash) and matches


def create_access_token(user_id: int, key: bytes, revision: int = 0) -> str:
    issued_at = int(time())
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": issued_at,
            "exp": issued_at + ACCESS_TOKEN_SECONDS,
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "rev": revision,
        },
        key,
        algorithm=JWT_ALGORITHM,
    )


def verify_access_token(token: str, key: bytes) -> tuple[int, int]:
    claims = jwt.decode(
        token,
        key,
        algorithms=[JWT_ALGORITHM],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        options={"require": ["sub", "iat", "exp", "iss", "aud"], "strict_aud": True},
    )
    issued_at, expires_at = claims["iat"], claims["exp"]
    if type(issued_at) is not int or type(expires_at) is not int or expires_at - issued_at != ACCESS_TOKEN_SECONDS:
        raise InvalidTokenError("Invalid token lifetime")

    subject = claims["sub"]
    if not subject.isascii() or not subject.isdecimal() or len(subject) > 19:
        raise InvalidTokenError("Invalid token subject")
    user_id = int(subject)
    if not 1 <= user_id <= 2**63 - 1 or str(user_id) != subject:
        raise InvalidTokenError("Invalid token subject")
    revision = claims.get("rev", 0)
    if type(revision) is not int or revision < 0:
        raise InvalidTokenError("Invalid account revision")
    return user_id, revision


def authentication_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    key: Annotated[bytes, Depends(get_signing_key)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    if credentials is None:
        raise authentication_required()
    try:
        user_id, revision = verify_access_token(credentials.credentials, key)
    except (InvalidTokenError, ValueError, TypeError, OverflowError):
        raise authentication_required() from None

    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise authentication_required()
    policy = account_policy(session, user_id)
    if policy.revision != revision or policy.archived or policy.temporary_until is not None:
        raise authentication_required()
    return user


def require_roles(*allowed_roles: UserRole) -> Callable[..., User]:
    def check_roles(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
        return user

    return check_roles
