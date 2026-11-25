"""Record the latest reviewer screening for each attachment and model."""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE reviewer_screenings (
        attachment_id INTEGER NOT NULL REFERENCES request_attachments(id) ON DELETE CASCADE,
        model_version TEXT NOT NULL,
        crack_detected INTEGER NOT NULL CHECK(crack_detected IN (0, 1)),
        score REAL CHECK(score BETWEEN 0 AND 1),
        threshold REAL CHECK(threshold BETWEEN 0 AND 1),
        PRIMARY KEY (attachment_id, model_version)
    )""")


def downgrade():
    op.drop_table("reviewer_screenings")
