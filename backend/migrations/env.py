from alembic import context
from sqlalchemy import Connection
from sqlmodel import SQLModel

import app.models  # Register the application tables with SQLModel.metadata.
from app.database import engine


def run_migrations(connection: Connection | None = None) -> None:
    settings = {
        "target_metadata": SQLModel.metadata,
        "render_as_batch": True,
        "compare_server_default": True,
        "transactional_ddl": True,
    }
    if connection is None:
        context.configure(url=engine.url, literal_binds=True, **settings)
    else:
        context.configure(connection=connection, **settings)

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations()
else:
    supplied_connection = context.config.attributes.get("connection")
    if supplied_connection is not None:
        run_migrations(supplied_connection)
    else:
        with engine.connect() as connection:
            run_migrations(connection)
