from datetime import UTC, datetime
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from sqlalchemy import CheckConstraint, Enum as DatabaseEnum, String, Text, func, text, true
from sqlmodel import Field, Relationship, SQLModel


SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class UserRole(StrEnum):
    REQUESTER = "requester"
    REVIEWER = "reviewer"
    ADMIN = "admin"


class RequestStatus(StrEnum):
    SUBMITTED = "submitted"
    IN_REVIEW = "in_review"
    NEEDS_INFO = "needs_info"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"


class DecisionOutcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_INFO = "needs_info"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class WorkOrderStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


def enum_column(enum_class: type[StrEnum], name: str) -> DatabaseEnum:
    return DatabaseEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class Record(SQLModel):
    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"server_default": func.current_timestamp()},
    )


class User(Record, table=True):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("length(trim(password_hash)) > 0", name="password_hash_not_blank"),)

    full_name: str = Field(max_length=160, nullable=False)
    email: str = Field(max_length=320, sa_type=String(320, collation="NOCASE"), nullable=False, unique=True)
    password_hash: str = Field(sa_type=Text, nullable=False)
    role: UserRole = Field(sa_type=enum_column(UserRole, "role"), nullable=False)
    is_active: bool = Field(default=True, nullable=False, sa_column_kwargs={"server_default": true()})


class MaintenanceTeam(Record, table=True):
    __tablename__ = "maintenance_teams"

    name: str = Field(max_length=120, nullable=False, unique=True)
    specialty: str = Field(max_length=120, nullable=False)
    is_active: bool = Field(default=True, nullable=False, sa_column_kwargs={"server_default": true()})


class Category(Record, table=True):
    __tablename__ = "categories"

    name: str = Field(max_length=120, nullable=False, unique=True)
    default_team_id: int | None = Field(default=None, foreign_key="maintenance_teams.id", ondelete="RESTRICT", index=True)
    is_active: bool = Field(default=True, nullable=False, sa_column_kwargs={"server_default": true()})

    default_team: MaintenanceTeam | None = Relationship()


class MaintenanceRequest(Record, table=True):
    __tablename__ = "maintenance_requests"

    requester_id: int = Field(foreign_key="users.id", ondelete="RESTRICT", nullable=False, index=True)
    title: str = Field(max_length=255, nullable=False)
    description: str = Field(sa_type=Text, nullable=False)
    location: str = Field(max_length=255, nullable=False)
    selected_category_id: int | None = Field(default=None, foreign_key="categories.id", ondelete="RESTRICT", index=True)
    status: RequestStatus = Field(
        default=RequestStatus.SUBMITTED,
        sa_type=enum_column(RequestStatus, "status"),
        nullable=False,
        sa_column_kwargs={"server_default": RequestStatus.SUBMITTED.value},
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"server_default": func.current_timestamp(), "onupdate": utc_now},
    )

    requester: User = Relationship()
    selected_category: Category | None = Relationship()
    attachments: list["RequestAttachment"] = Relationship(back_populates="request", passive_deletes="all")
    review_decisions: list["ReviewDecision"] = Relationship(back_populates="request", passive_deletes="all")
    work_order: Optional["WorkOrder"] = Relationship(back_populates="request", passive_deletes="all")


class RequestAttachment(Record, table=True):
    __tablename__ = "request_attachments"
    __table_args__ = (
        CheckConstraint(
            "typeof(stored_filename) = 'text' AND length(stored_filename) = 32 "
            "AND length(CAST(stored_filename AS BLOB)) = 32 "
            "AND stored_filename NOT GLOB '*[^0-9a-f]*'",
            name="safe_stored_filename",
        ),
        CheckConstraint("typeof(size_bytes) = 'integer' AND size_bytes >= 0", name="nonnegative_size"),
    )

    request_id: int = Field(foreign_key="maintenance_requests.id", ondelete="RESTRICT", nullable=False, index=True)
    stored_filename: str = Field(default_factory=lambda: uuid4().hex, max_length=32, nullable=False, unique=True)
    original_filename: str = Field(max_length=255, nullable=False)
    content_type: str = Field(max_length=255, nullable=False)
    size_bytes: int = Field(nullable=False, ge=0)

    request: MaintenanceRequest = Relationship(back_populates="attachments")


class ReviewDecision(Record, table=True):
    __tablename__ = "review_decisions"

    request_id: int = Field(foreign_key="maintenance_requests.id", ondelete="RESTRICT", nullable=False, index=True)
    reviewer_id: int = Field(foreign_key="users.id", ondelete="RESTRICT", nullable=False, index=True)
    decision: DecisionOutcome = Field(sa_type=enum_column(DecisionOutcome, "decision"), nullable=False)
    final_category_id: int | None = Field(default=None, foreign_key="categories.id", ondelete="RESTRICT", index=True)
    final_priority: Priority | None = Field(default=None, sa_type=enum_column(Priority, "final_priority"))
    assigned_team_id: int | None = Field(default=None, foreign_key="maintenance_teams.id", ondelete="RESTRICT", index=True)
    notes: str = Field(default="", sa_type=Text, nullable=False, sa_column_kwargs={"server_default": text("''")})

    request: MaintenanceRequest = Relationship(back_populates="review_decisions")
    reviewer: User = Relationship()
    final_category: Category | None = Relationship()
    assigned_team: MaintenanceTeam | None = Relationship()


class WorkOrder(Record, table=True):
    __tablename__ = "work_orders"

    request_id: int = Field(foreign_key="maintenance_requests.id", ondelete="RESTRICT", nullable=False, unique=True)
    assigned_team_id: int = Field(foreign_key="maintenance_teams.id", ondelete="RESTRICT", nullable=False, index=True)
    status: WorkOrderStatus = Field(
        default=WorkOrderStatus.OPEN,
        sa_type=enum_column(WorkOrderStatus, "status"),
        nullable=False,
        sa_column_kwargs={"server_default": WorkOrderStatus.OPEN.value},
    )
    updated_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        sa_column_kwargs={"server_default": func.current_timestamp(), "onupdate": utc_now},
    )
    resolved_at: datetime | None = Field(default=None)

    request: MaintenanceRequest = Relationship(back_populates="work_order")
    assigned_team: MaintenanceTeam = Relationship()
