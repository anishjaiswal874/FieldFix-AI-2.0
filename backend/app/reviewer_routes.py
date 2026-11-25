from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import String, cast, func, or_, update
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import select
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from app.auth import require_roles
from app.models import (
    Category, DecisionOutcome, MaintenanceRequest, MaintenanceTeam, Priority, RequestAttachment,
    RequestStatus, ReviewDecision, User, UserRole, WorkOrder, WorkOrderStatus,
)
from app.request_routes import AttachmentSummary, CategorySummary, Database, RecordId, RequestDetails, RequestSummary, UploadDirectory, bounded_body, text_field
from app.request_uploads import MAX_PHOTO_BYTES, attachment_response, display_filename, normalize_photo
from app.work_order_resolution import Resolution, close_work_order, proof_response, resolution_for
from app.decision_analytics import analysis_statuses, audit_decision


router = APIRouter(prefix="/api/reviewer", tags=["Reviewer workflow"])
Reviewer = Annotated[User, Depends(require_roles(UserRole.REVIEWER))]
REVIEWABLE = (RequestStatus.SUBMITTED, RequestStatus.IN_REVIEW)
QUEUE_STATUSES = (*REVIEWABLE, RequestStatus.NEEDS_INFO, RequestStatus.APPROVED)
PageNumber = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=50)]
Search = Annotated[str, Query(max_length=120)]
ForeignId = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]
Row = TypeVar("Row")


class Page(BaseModel, Generic[Row]):
    items: list[Row]
    total: int
    page: int
    per_page: int
    status_counts: dict[str, int]
    safety_count: int | None = None


class StaffName(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    full_name: str


class ReviewRequest(RequestDetails):
    requester: StaffName
    active_work_order_id: int | None = None


class QueueRequest(RequestSummary):
    active_work_order_id: int | None = None
    ai_analysis: Literal["not_analyzed", "abstained", "nlp", "cv", "nlp_cv", "priority", "analyzed"] = "not_analyzed"


class TeamSummary(CategorySummary):
    specialty: str


class ReviewOptions(BaseModel):
    categories: list[CategorySummary]
    teams: list[TeamSummary]
    priorities: list[Priority]


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: DecisionOutcome
    final_category_id: ForeignId | None = None
    final_priority: Priority | None = None
    assigned_team_id: ForeignId | None = None
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""
    override_reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class DecisionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    request: RequestSummary
    reviewer: StaffName
    decision: DecisionOutcome
    final_category: CategorySummary | None
    final_priority: Priority | None
    assigned_team: TeamSummary | None
    notes: str
    created_at: datetime
    resolution: Resolution | None = None


class ReviewResult(BaseModel):
    request_id: int
    decision_id: int
    status: RequestStatus
    work_order_id: int | None


class WorkOrderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    request: RequestSummary
    assigned_team: TeamSummary
    status: WorkOrderStatus
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    safety_alert: bool = False


class WorkOrderDetail(WorkOrderSummary):
    approval: DecisionRecord | None
    original_attachments: list[AttachmentSummary] = []
    resolution: Resolution | None = None


class Dashboard(BaseModel):
    requests: dict[RequestStatus, int]
    work_orders: dict[WorkOrderStatus, int]
    queue: list[RequestSummary]
    categories: dict[str, int]


def counts_by(session: Database, column, *conditions):
    return dict(session.exec(select(column, func.count()).where(*conditions).group_by(column)).all())


def paged(session: Database, model, records, conditions, page, per_page, counts, schema):
    total = session.exec(select(func.count()).select_from(model).where(*conditions)).one()
    page = min(page, max(1, (total + per_page - 1) // per_page))
    rows = session.exec(records.where(*conditions).offset((page - 1) * per_page).limit(per_page)).all()
    return Page(items=[schema.model_validate(row) for row in rows], total=total, page=page,
                per_page=per_page, status_counts=counts)


def request_matches(search: str):
    number = search.lower().removeprefix("ff-").lstrip("0")
    return or_(
        MaintenanceRequest.title.icontains(search, autoescape=True),
        MaintenanceRequest.location.icontains(search, autoescape=True),
        cast(MaintenanceRequest.id, String) == number,
    )


def queue_records():
    return select(MaintenanceRequest).options(joinedload(MaintenanceRequest.selected_category)).order_by(
        MaintenanceRequest.created_at, MaintenanceRequest.id,
    )


def history_records():
    return select(ReviewDecision).options(
        joinedload(ReviewDecision.request).joinedload(MaintenanceRequest.selected_category),
        joinedload(ReviewDecision.reviewer), joinedload(ReviewDecision.final_category), joinedload(ReviewDecision.assigned_team),
    ).order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc())


def order_records():
    return select(WorkOrder).options(
        joinedload(WorkOrder.request).joinedload(MaintenanceRequest.selected_category), joinedload(WorkOrder.assigned_team),
    ).order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc())


@router.get("/team-workload")
def team_workload(_reviewer: Reviewer, session: Database):
    active_orders = WorkOrder.status.in_((WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS))
    workload = (select(WorkOrder.assigned_team_id, func.count().label("active"))
                .where(active_orders).group_by(WorkOrder.assigned_team_id).subquery())
    categories = session.exec(select(Category.default_team_id, Category.name)
                              .where(Category.is_active.is_(True)).order_by(Category.name)).all()
    names = {}
    for team_id, name in categories:
        names.setdefault(team_id, []).append(name)
    teams = session.exec(select(MaintenanceTeam, func.coalesce(workload.c.active, 0))
                         .outerjoin(workload, MaintenanceTeam.id == workload.c.assigned_team_id)
                         .where(MaintenanceTeam.is_active.is_(True)).order_by(MaintenanceTeam.name))
    return [{"id": team.id, "name": team.name, "specialty": team.specialty,
             "active_work_orders": count, "categories": names.get(team.id, [])} for team, count in teams]


@router.get("/dashboard")
def dashboard(_reviewer: Reviewer, session: Database) -> Dashboard:
    queue = session.exec(queue_records().where(MaintenanceRequest.status.in_(REVIEWABLE)).limit(5))
    return Dashboard(
        requests=counts_by(session, MaintenanceRequest.status),
        work_orders=counts_by(session, WorkOrder.status),
        categories={name or "Unspecified": count for name, count in session.exec(
            select(Category.name, func.count()).select_from(MaintenanceRequest)
            .outerjoin(Category, Category.id == MaintenanceRequest.selected_category_id).group_by(Category.id, Category.name))},
        queue=[RequestSummary.model_validate(record) for record in queue],
    )


@router.get("/requests")
def queue(_reviewer: Reviewer, session: Database, status: RequestStatus | None = None,
          q: Search = "", page: PageNumber = 1, per_page: PageSize = 10) -> Page[QueueRequest]:
    in_queue = or_(MaintenanceRequest.status.in_((*REVIEWABLE, RequestStatus.NEEDS_INFO)),
                   (MaintenanceRequest.status == RequestStatus.APPROVED) & MaintenanceRequest.work_order.has(
                       WorkOrder.status.in_((WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS))))
    if status is not None and status not in QUEUE_STATUSES:
        raise HTTPException(422, "Select a queue status.")
    conditions = [in_queue]
    if status is not None:
        conditions.append(MaintenanceRequest.status == status)
    if q.strip():
        conditions.append(request_matches(q.strip()))
    result = paged(session, MaintenanceRequest, queue_records(), conditions, page, per_page,
                   counts_by(session, MaintenanceRequest.status, in_queue), QueueRequest)
    statuses = analysis_statuses(session, [item.id for item in result.items])
    approved_ids = [item.id for item in result.items if item.status == RequestStatus.APPROVED]
    orders = dict(session.exec(select(WorkOrder.request_id, WorkOrder.id)
                               .where(WorkOrder.request_id.in_(approved_ids))).all()) if approved_ids else {}
    for item in result.items:
        item.ai_analysis = statuses[item.id]
        item.active_work_order_id = orders.get(item.id)
    return result


@router.get("/options")
def review_options(_reviewer: Reviewer, session: Database) -> ReviewOptions:
    categories = session.exec(select(Category).where(Category.is_active.is_(True)).order_by(Category.name, Category.id)).all()
    teams = session.exec(select(MaintenanceTeam).where(MaintenanceTeam.is_active.is_(True))
                         .order_by(MaintenanceTeam.name, MaintenanceTeam.id)).all()
    return ReviewOptions(categories=categories, teams=teams, priorities=list(Priority))


@router.get("/requests/{request_id}")
def request_detail(request_id: RecordId, _reviewer: Reviewer, session: Database) -> ReviewRequest:
    record = session.exec(select(MaintenanceRequest).where(MaintenanceRequest.id == request_id).options(
        joinedload(MaintenanceRequest.requester), joinedload(MaintenanceRequest.selected_category),
        selectinload(MaintenanceRequest.attachments),
    )).one_or_none()
    if record is None:
        raise HTTPException(404, "Request not found.")
    result = ReviewRequest.model_validate(record)
    if record.status == RequestStatus.APPROVED and record.work_order is not None:
        result.active_work_order_id = record.work_order.id
    return result


@router.get("/requests/{request_id}/attachments/{attachment_id}", response_class=FileResponse)
def request_evidence(request_id: RecordId, attachment_id: RecordId, _reviewer: Reviewer,
                     session: Database, directory: UploadDirectory):
    attachment = session.exec(select(RequestAttachment).where(
        RequestAttachment.request_id == request_id, RequestAttachment.id == attachment_id,
    )).one_or_none()
    return attachment_response(directory, attachment)


@router.post("/requests/{request_id}/decisions", status_code=201)
def submit_decision(request_id: RecordId, body: ReviewInput, reviewer: Reviewer, session: Database) -> ReviewResult:
    approve = body.decision == DecisionOutcome.APPROVED
    assignment = (body.final_category_id, body.final_priority, body.assigned_team_id)
    if approve and any(value is None for value in assignment):
        raise HTTPException(422, "Approval requires a final category, priority, and receiving team.")
    if not approve and any(value is not None for value in assignment):
        raise HTTPException(422, "Provide an assignment only with an approval.")
    if body.decision == DecisionOutcome.NEEDS_INFO and not body.notes:
        raise HTTPException(422, "Tell the requester what information you need.")
    if "\0" in body.notes or "\0" in body.override_reason:
        raise HTTPException(422, "Notes contain an invalid character.")

    reviewer_id = reviewer.id
    next_status = RequestStatus(body.decision.value)
    # Release the auth read transaction. The conditional update is the first
    # database statement in the new transaction so SQLite serializes writers.
    session.rollback()
    with session.begin():
        existing_order = select(WorkOrder.id).where(WorkOrder.request_id == MaintenanceRequest.id).exists()
        claimed = session.exec(update(MaintenanceRequest).where(
            MaintenanceRequest.id == request_id, MaintenanceRequest.status.in_(REVIEWABLE), ~existing_order,
        ).values(status=next_status).returning(MaintenanceRequest.id)).one_or_none()

        current_reviewer = session.get(User, reviewer_id)
        if current_reviewer is None or not current_reviewer.is_active:
            raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
        if current_reviewer.role != UserRole.REVIEWER:
            raise HTTPException(403, "Insufficient permissions.")
        if claimed is None:
            if session.get(MaintenanceRequest, request_id) is None:
                raise HTTPException(404, "Request not found.")
            raise HTTPException(409, "This request is no longer awaiting a decision.")

        if approve:
            category = session.get(Category, body.final_category_id)
            if category is None or not category.is_active:
                raise HTTPException(422, "Select an active final category.")
            team = session.get(MaintenanceTeam, body.assigned_team_id)
            if team is None or not team.is_active:
                raise HTTPException(422, "Select an active receiving team.")

        decision = ReviewDecision(
            request_id=request_id, reviewer_id=reviewer_id, decision=body.decision,
            final_category_id=body.final_category_id, final_priority=body.final_priority,
            assigned_team_id=body.assigned_team_id, notes=body.notes,
        )
        session.add(decision)
        order = None
        if approve:
            order = WorkOrder(request_id=request_id, assigned_team_id=body.assigned_team_id)
            session.add(order)
        session.flush()
        audit_decision(session, decision, category.name if approve else None, body.override_reason)
        return ReviewResult(request_id=request_id, decision_id=decision.id, status=next_status,
                            work_order_id=order.id if order else None)


@router.get("/history")
def history(_reviewer: Reviewer, session: Database, decision: DecisionOutcome | None = None,
            q: Search = "", page: PageNumber = 1, per_page: PageSize = 10) -> Page[DecisionRecord]:
    conditions = []
    if decision is not None:
        conditions.append(ReviewDecision.decision == decision)
    if q.strip():
        conditions.append(ReviewDecision.request.has(request_matches(q.strip())))
    result = paged(session, ReviewDecision, history_records(), conditions, page, per_page,
                   counts_by(session, ReviewDecision.decision), DecisionRecord)
    for item in result.items:
        if item.decision == DecisionOutcome.APPROVED and item.request.status == RequestStatus.COMPLETED:
            item.resolution = resolution_for(session, item.request.id)
    return result


@router.get("/work-orders")
def work_orders(_reviewer: Reviewer, session: Database, status: WorkOrderStatus | None = None,
                q: Search = "", page: PageNumber = 1, per_page: PageSize = 10,
                safety: bool = False) -> Page[WorkOrderSummary]:
    conditions = []
    if status is not None:
        conditions.append(WorkOrder.status == status)
    search = q.strip()
    if search:
        order_number = search.lower().removeprefix("wo-").lstrip("0")
        conditions.append(or_(
            cast(WorkOrder.id, String) == order_number,
            WorkOrder.request.has(request_matches(search)),
            WorkOrder.assigned_team.has(MaintenanceTeam.name.icontains(search, autoescape=True)),
        ))
    high_priority = select(ReviewDecision.request_id).where(
        ReviewDecision.decision == DecisionOutcome.APPROVED, ReviewDecision.final_priority == Priority.HIGH)
    alert = WorkOrder.request_id.in_(high_priority)
    if safety:
        conditions.append(alert)
    result = paged(session, WorkOrder, order_records(), conditions, page, per_page,
                   counts_by(session, WorkOrder.status), WorkOrderSummary)
    result.safety_count = session.exec(select(func.count()).select_from(WorkOrder).where(alert)).one()
    visible = [item.request.id for item in result.items]
    tagged = set(session.exec(high_priority.where(ReviewDecision.request_id.in_(visible))))
    for item in result.items:
        item.safety_alert = item.request.id in tagged
    return result


@router.get("/work-orders/{order_id}")
def work_order(order_id: RecordId, _reviewer: Reviewer, session: Database) -> WorkOrderDetail:
    order = session.exec(order_records().where(WorkOrder.id == order_id)).one_or_none()
    if order is None:
        raise HTTPException(404, "Work order not found.")
    approval = session.exec(history_records().where(
        ReviewDecision.request_id == order.request_id, ReviewDecision.decision == DecisionOutcome.APPROVED,
    ).limit(1)).first()
    return WorkOrderDetail(**WorkOrderSummary.model_validate(order).model_dump(), approval=approval,
                           original_attachments=[AttachmentSummary.model_validate(photo) for photo in order.request.attachments],
                           resolution=resolution_for(session, order.request_id))


@router.post("/work-orders/{order_id}/resolve")
async def resolve_order(order_id: RecordId, request: Request, reviewer: Reviewer, session: Database,
                        directory: UploadDirectory) -> Resolution:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Send the resolution as multipart form data.")
    try:
        form = await MultiPartParser(request.headers, bounded_body(request, MAX_PHOTO_BYTES + 65536),
                                    max_files=1, max_fields=2, max_part_size=8192).parse()
    except (ClientDisconnect, MultiPartException):
        raise HTTPException(422, "Send valid resolution fields and at most one proof image.") from None
    try:
        if set(form) - {"resolution_notes", "resolution_confirmed", "resolution_image"}:
            raise HTTPException(422, "Unexpected resolution field.")
        if form.getlist("resolution_confirmed") != ["true"]:
            raise HTTPException(422, "Confirm that this maintenance task is completed.")
        notes = text_field(form, "resolution_notes", "resolution notes", 2000)
        uploads = form.getlist("resolution_image")
        photo = None
        if uploads:
            if len(uploads) != 1 or not isinstance(uploads[0], UploadFile):
                raise HTTPException(422, "Select one proof image.")
            image, media = await run_in_threadpool(normalize_photo, uploads[0])
            photo = image, media, display_filename(uploads[0].filename)
        return await run_in_threadpool(close_work_order, session, order_id, reviewer, notes, photo, directory)
    finally:
        await form.close()


@router.get("/requests/{request_id}/resolution-proof", response_class=FileResponse)
def resolution_photo(request_id: RecordId, _reviewer: Reviewer, session: Database, directory: UploadDirectory):
    return proof_response(session, directory, request_id)
