"""Add administrative account and registration controls."""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE account_policy (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0,1)),
        temporary_until INTEGER
    )""")
    op.execute("""CREATE TABLE app_settings (
        id INTEGER PRIMARY KEY CHECK(id=1),
        requester_self_registration INTEGER NOT NULL CHECK(requester_self_registration IN (0,1))
    )""")
    op.execute("""CREATE TRIGGER revise_account_access AFTER UPDATE OF email, password_hash, role, is_active ON users
        WHEN OLD.email IS NOT NEW.email OR OLD.password_hash IS NOT NEW.password_hash
            OR OLD.role IS NOT NEW.role OR OLD.is_active IS NOT NEW.is_active
        BEGIN
            INSERT INTO account_policy(user_id,revision) VALUES(NEW.id,1)
            ON CONFLICT(user_id) DO UPDATE SET revision=revision+1;
        END""")


def downgrade():
    op.execute("DROP TRIGGER revise_account_access")
    # Preserve the inactive accounts and password hashes. A disposable rollback
    # is supported, but an outstanding temporary password cannot become a
    # permanent credential on revision 0002.
    op.execute("""UPDATE users SET is_active=0 WHERE id IN
        (SELECT user_id FROM account_policy WHERE temporary_until IS NOT NULL)""")
    op.drop_table("app_settings")
    op.drop_table("account_policy")
