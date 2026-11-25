"""Mailbox ownership, bounded OTP challenges and opt-in browser sessions."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE verified_mailbox (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        email TEXT NOT NULL UNIQUE, verified_at INTEGER NOT NULL)""")
    op.execute("""CREATE TABLE email_challenge (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        purpose TEXT NOT NULL, email TEXT NOT NULL, digest TEXT NOT NULL,
        expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        UNIQUE(user_id, purpose))""")
    op.execute("""CREATE TABLE auth_limit (
        bucket TEXT PRIMARY KEY, since INTEGER NOT NULL, hits INTEGER NOT NULL)""")
    op.execute("""CREATE TABLE browser_session (
        id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        digest TEXT NOT NULL UNIQUE, created INTEGER NOT NULL, used INTEGER NOT NULL,
        expires INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)""")
    op.execute("CREATE INDEX browser_session_user ON browser_session(user_id)")
    op.execute("""CREATE TABLE spent_refresh (
        digest TEXT PRIMARY KEY, family TEXT NOT NULL REFERENCES browser_session(id)
        ON DELETE CASCADE, rotated INTEGER NOT NULL)""")
    op.execute("CREATE INDEX spent_refresh_family ON spent_refresh(family)")
    op.execute("""CREATE TRIGGER invalidate_auth AFTER UPDATE OF email, password_hash, role, is_active ON users
        WHEN OLD.email IS NOT NEW.email OR OLD.password_hash IS NOT NEW.password_hash OR
             OLD.role IS NOT NEW.role OR OLD.is_active IS NOT NEW.is_active
        BEGIN
          UPDATE browser_session SET revoked=1 WHERE user_id=NEW.id;
          DELETE FROM email_challenge WHERE user_id=NEW.id;
          DELETE FROM verified_mailbox WHERE user_id=NEW.id AND OLD.email IS NOT NEW.email;
        END""")


def downgrade():
    op.execute("DROP TRIGGER invalidate_auth")
    for table in ("spent_refresh", "browser_session", "auth_limit", "email_challenge", "verified_mailbox"):
        op.drop_table(table)
