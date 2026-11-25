"""Create the FieldFix database foundation.

Revision ID: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maintenance_teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("specialty", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_teams")),
        sa.UniqueConstraint("name", name=op.f("uq_maintenance_teams_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("full_name", sa.String(160), nullable=False),
        sa.Column("email", sa.String(320, collation="NOCASE"), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(9), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("role IN ('requester', 'reviewer', 'admin')", name=op.f("ck_users_role")),
        sa.CheckConstraint("length(trim(password_hash)) > 0", name=op.f("ck_users_password_hash_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("default_team_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(
            ["default_team_id"], ["maintenance_teams.id"], ondelete="RESTRICT",
            name=op.f("fk_categories_default_team_id_maintenance_teams"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("name", name=op.f("uq_categories_name")),
    )
    op.create_index(op.f("ix_categories_default_team_id"), "categories", ["default_team_id"])

    op.create_table(
        "maintenance_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("requester_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(255), nullable=False),
        sa.Column("selected_category_id", sa.Integer()),
        sa.Column("status", sa.String(10), server_default="submitted", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.CheckConstraint(
            "status IN ('submitted', 'in_review', 'needs_info', 'approved', 'rejected', 'completed')",
            name=op.f("ck_maintenance_requests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_maintenance_requests_requester_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["selected_category_id"], ["categories.id"], ondelete="RESTRICT",
            name=op.f("fk_maintenance_requests_selected_category_id_categories"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_maintenance_requests")),
    )
    op.create_index(op.f("ix_maintenance_requests_requester_id"), "maintenance_requests", ["requester_id"])
    op.create_index(op.f("ix_maintenance_requests_selected_category_id"), "maintenance_requests", ["selected_category_id"])

    op.create_table(
        "request_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("stored_filename", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "typeof(stored_filename) = 'text' AND length(stored_filename) = 32 "
            "AND length(CAST(stored_filename AS BLOB)) = 32 "
            "AND stored_filename NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_request_attachments_safe_stored_filename"),
        ),
        sa.CheckConstraint(
            "typeof(size_bytes) = 'integer' AND size_bytes >= 0",
            name=op.f("ck_request_attachments_nonnegative_size"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["maintenance_requests.id"], ondelete="RESTRICT",
            name=op.f("fk_request_attachments_request_id_maintenance_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_attachments")),
        sa.UniqueConstraint("stored_filename", name=op.f("uq_request_attachments_stored_filename")),
    )
    op.create_index(op.f("ix_request_attachments_request_id"), "request_attachments", ["request_id"])

    op.create_table(
        "review_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("final_category_id", sa.Integer()),
        sa.Column("final_priority", sa.String(6)),
        sa.Column("assigned_team_id", sa.Integer()),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.CheckConstraint("decision IN ('approved', 'rejected', 'needs_info')", name=op.f("ck_review_decisions_decision")),
        sa.CheckConstraint("final_priority IN ('low', 'medium', 'high')", name=op.f("ck_review_decisions_final_priority")),
        sa.ForeignKeyConstraint(
            ["assigned_team_id"], ["maintenance_teams.id"], ondelete="RESTRICT",
            name=op.f("fk_review_decisions_assigned_team_id_maintenance_teams"),
        ),
        sa.ForeignKeyConstraint(
            ["final_category_id"], ["categories.id"], ondelete="RESTRICT",
            name=op.f("fk_review_decisions_final_category_id_categories"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["maintenance_requests.id"], ondelete="RESTRICT",
            name=op.f("fk_review_decisions_request_id_maintenance_requests"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], ondelete="RESTRICT",
            name=op.f("fk_review_decisions_reviewer_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_decisions")),
    )
    op.create_index(op.f("ix_review_decisions_assigned_team_id"), "review_decisions", ["assigned_team_id"])
    op.create_index(op.f("ix_review_decisions_final_category_id"), "review_decisions", ["final_category_id"])
    op.create_index(op.f("ix_review_decisions_request_id"), "review_decisions", ["request_id"])
    op.create_index(op.f("ix_review_decisions_reviewer_id"), "review_decisions", ["reviewer_id"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("assigned_team_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(11), server_default="open", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('open', 'in_progress', 'resolved')", name=op.f("ck_work_orders_status")),
        sa.ForeignKeyConstraint(
            ["assigned_team_id"], ["maintenance_teams.id"], ondelete="RESTRICT",
            name=op.f("fk_work_orders_assigned_team_id_maintenance_teams"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["maintenance_requests.id"], ondelete="RESTRICT",
            name=op.f("fk_work_orders_request_id_maintenance_requests"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_orders")),
        sa.UniqueConstraint("request_id", name=op.f("uq_work_orders_request_id")),
    )
    op.create_index(op.f("ix_work_orders_assigned_team_id"), "work_orders", ["assigned_team_id"])


def downgrade() -> None:
    op.drop_table("work_orders")
    op.drop_table("review_decisions")
    op.drop_table("request_attachments")
    op.drop_table("maintenance_requests")
    op.drop_table("categories")
    op.drop_table("users")
    op.drop_table("maintenance_teams")
