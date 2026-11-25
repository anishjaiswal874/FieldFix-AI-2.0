"""Record complaint-level priority rule evaluations."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE reviewer_priority_evaluations (
        id INTEGER PRIMARY KEY,
        request_id INTEGER NOT NULL REFERENCES maintenance_requests(id) ON DELETE RESTRICT,
        priority TEXT CHECK(priority IN ('low','medium','high')),
        evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute("CREATE INDEX ix_reviewer_priority_request ON reviewer_priority_evaluations(request_id)")
    op.execute("CREATE INDEX ix_reviewer_evaluations_request ON reviewer_evaluations(request_id)")


def downgrade():
    raise RuntimeError("Revision 0006 retains priority audit records and cannot be downgraded.")
