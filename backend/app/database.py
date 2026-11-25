from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import URL, Engine, event
from sqlalchemy.pool import ConnectionPoolEntry
from sqlmodel import Session, create_engine


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATABASE_PATH = BACKEND_DIR / "fieldfix.db"


def create_database_engine(path: Path = DATABASE_PATH) -> Engine:
    url = URL.create("sqlite", database=str(path.resolve()))
    database_engine = create_engine(
        url,
        connect_args={"autocommit": False, "check_same_thread": False},
    )

    @event.listens_for(database_engine, "connect")
    def enable_foreign_keys(connection: SQLiteConnection, _record: ConnectionPoolEntry) -> None:
        # SQLite must set this per connection and outside a transaction.
        autocommit = connection.autocommit
        connection.autocommit = True
        try:
            connection.execute("PRAGMA foreign_keys = ON").close()
        finally:
            connection.autocommit = autocommit

    return database_engine


engine = create_database_engine()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
