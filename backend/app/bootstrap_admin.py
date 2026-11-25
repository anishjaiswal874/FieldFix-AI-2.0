"""Create the first administrator from an interactive local terminal."""

import sys
import warnings
from getpass import GetPassWarning, getpass
from sqlite3 import SQLITE_CONSTRAINT_UNIQUE

from pydantic import ValidationError
from sqlalchemy import literal
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.auth import hash_password
from app.auth_routes import RegisterInput
from app.database import DATABASE_PATH, engine
from app.models import User, UserRole, utc_now


class BootstrapError(Exception):
    pass


def admin_exists():
    return select(User.id).where(User.role == UserRole.ADMIN).exists()


def create_first_admin(session: Session, credentials: RegisterInput) -> int:
    table = User.__table__
    password_hash = hash_password(credentials.password.get_secret_value())
    # One conditional write. Concurrent operators cannot both create an admin,
    # and an existing administrator account blocks reuse even if it is inactive.
    statement = table.insert().from_select(
        ["full_name", "email", "password_hash", "role", "is_active", "created_at"],
        select(literal(credentials.full_name), literal(credentials.email), literal(password_hash),
               literal(UserRole.ADMIN.value), literal(True), literal(utc_now()))
        .where(~admin_exists()),
    ).returning(table.c.id)
    try:
        with session.begin():
            user_id = session.execute(statement).scalar_one_or_none()
            if user_id is None:
                raise BootstrapError("An administrator account already exists.")
            return user_id
    except IntegrityError as error:
        if getattr(error.orig, "sqlite_errorcode", None) == SQLITE_CONSTRAINT_UNIQUE:
            raise BootstrapError("That email address is already registered. Use a new staff email for bootstrap.") from None
        raise


def main() -> int:
    if not sys.stdin.isatty():
        print("Open an interactive PowerShell terminal to bootstrap the administrator.", file=sys.stderr)
        return 1
    try:
        if not DATABASE_PATH.is_file():
            raise BootstrapError("The local database does not exist. Apply the Alembic migrations first.")
        with Session(engine) as session:
            if session.exec(select(admin_exists())).one():
                raise BootstrapError("An administrator account already exists.")

        name = input("Administrator full name: ")
        email = input("Administrator email: ")
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Password (15–128 characters): ")
            confirmation = getpass("Confirm password: ")
        if password != confirmation:
            raise BootstrapError("Passwords do not match.")
        credentials = RegisterInput(full_name=name, email=email, password=password)
        with Session(engine) as session:
            user_id = create_first_admin(session, credentials)
    except BootstrapError as error:
        print(error, file=sys.stderr)
    except ValidationError:
        print("Enter a valid name, email address, and password of 15–128 characters.", file=sys.stderr)
    except (GetPassWarning, EOFError, KeyboardInterrupt):
        print("Administrator bootstrap cancelled before completion.", file=sys.stderr)
    except SQLAlchemyError:
        print("Administrator bootstrap could not write to the database. Check migration and local access.", file=sys.stderr)
    else:
        print(f"Administrator account created (user ID {user_id}).")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
