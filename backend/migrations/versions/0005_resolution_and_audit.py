"""Preserve resolution, profile and advisory audit data in additive tables."""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    columns = {row[1] for row in op.get_bind().exec_driver_sql("PRAGMA table_info(reviewer_screenings)")}
    if "quality_json" not in columns:
        op.execute("ALTER TABLE reviewer_screenings ADD COLUMN quality_json TEXT")
    op.execute("""CREATE TABLE IF NOT EXISTS work_order_resolutions (
        work_order_id INTEGER PRIMARY KEY REFERENCES work_orders(id) ON DELETE RESTRICT,
        resolution_notes TEXT NOT NULL,
        resolution_image_path TEXT,
        image_type TEXT,
        image_name TEXT,
        resolution_confirmed INTEGER NOT NULL CHECK(resolution_confirmed=1),
        resolved_at TEXT NOT NULL,
        resolved_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS requester_profiles (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,
        phone TEXT NOT NULL DEFAULT '',
        department TEXT NOT NULL DEFAULT '',
        staff_or_student_id TEXT NOT NULL DEFAULT '',
        default_location TEXT NOT NULL DEFAULT '',
        bio TEXT NOT NULL DEFAULT ''
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS reviewer_evaluations (
        id INTEGER PRIMARY KEY,
        request_id INTEGER REFERENCES maintenance_requests(id) ON DELETE RESTRICT,
        attachment_id INTEGER REFERENCES request_attachments(id) ON DELETE RESTRICT,
        feature TEXT NOT NULL CHECK(feature IN ('nlp','cv')),
        outcome TEXT NOT NULL CHECK(outcome IN ('suggested','abstained','screened')),
        label TEXT,
        evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS review_decision_audits (
        decision_id INTEGER PRIMARY KEY REFERENCES review_decisions(id) ON DELETE RESTRICT,
        ai_suggested_category TEXT,
        final_category TEXT,
        ai_suggested_priority TEXT,
        final_priority TEXT,
        cv_flagged INTEGER,
        category_overridden INTEGER,
        priority_overridden INTEGER,
        was_overridden INTEGER,
        override_reason TEXT NOT NULL DEFAULT '',
        decided_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def downgrade():
    raise RuntimeError("Revision 0005 retains resolution, profile and audit records and cannot be downgraded.")
