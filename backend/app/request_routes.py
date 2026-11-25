import logging
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import Session, select
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.requests import ClientDisconnect

from app.auth import require_roles
from app.account_policy import account_policy
from app.browser_sessions import query, write_lock
from app.database import get_session
from app.models import Category, DecisionOutcome, MaintenanceRequest, RequestAttachment, RequestStatus, ReviewDecision, User, UserRole
from app.request_uploads import (
    MAX_PHOTO_BYTES, MAX_PHOTOS, attachment_path, attachment_response, display_filename,
    get_upload_directory, normalize_photo,
)
from app.work_order_resolution import Resolution, proof_response, resolution_for

router = APIRouter(prefix="/api/requests", tags=["Requester maintenance"])
logger = logging.getLogger(__name__)
Requester = Annotated[User, Depends(require_roles(UserRole.REQUESTER))]
Database = Annotated[Session, Depends(get_session)]
UploadDirectory = Annotated[Path, Depends(get_upload_directory)]
RecordId = Annotated[int, PathParam(ge=1, le=2**63 - 1)]
MAX_BODY_BYTES = MAX_PHOTOS * MAX_PHOTO_BYTES + 64 * 1024


class CategorySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class AttachmentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    original_filename: str
    content_type: str
    size_bytes: int


class RequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    location: str
    selected_category: CategorySummary | None
    status: RequestStatus
    created_at: datetime
    updated_at: datetime


class RequestDetails(RequestSummary):
    description: str
    attachments: list[AttachmentSummary]


class OwnedRequestDetails(RequestDetails):
    information_requested: str | None = None
    resolution: Resolution | None = None


class RequestList(BaseModel):
    items: list[RequestSummary]
    total: int
    page: int
    per_page: int
    status_counts: dict[RequestStatus, int]


async def bounded_body(request: Request, limit: int = MAX_BODY_BYTES) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, "The request exceeds the upload limit.")
        yield chunk


def text_field(form: FormData, name: str, label: str, maximum: int) -> str:
    values = form.getlist(name)
    if len(values) == 1 and isinstance(values[0], str):
        value = values[0].strip()
        if value and len(value) <= maximum and "\0" not in value:
            return value
    raise HTTPException(422, f"Enter {label} using 1–{maximum} characters.")


def selected_category(form: FormData) -> int | None:
    values = form.getlist("category_id")
    if not values or values == [""]:
        return None
    if len(values) == 1 and isinstance(values[0], str):
        value = values[0]
        if len(value) <= 19 and value.isascii() and value.isdecimal() and 1 <= int(value) <= 2**63 - 1:
            return int(value)
    raise HTTPException(422, "Select an available category.")


def save_submission(form: FormData, requester_id: int, session: Session, directory: Path) -> RequestDetails:
    allowed_fields = {"title", "description", "location", "category_id", "photos"}
    if set(form) - allowed_fields:
        raise HTTPException(422, "The form contains an unexpected field.")
    record = MaintenanceRequest(
        requester_id=requester_id,
        title=text_field(form, "title", "a title", 255),
        description=text_field(form, "description", "a description", 1000),
        location=text_field(form, "location", "a location", 255),
        status=RequestStatus.SUBMITTED,
    )
    category_id = selected_category(form)
    uploads = form.getlist("photos")
    if any(not isinstance(upload, UploadFile) for upload in uploads):
        raise HTTPException(422, "Photos must be uploaded as files.")

    written = []
    try:
        # Release the read transaction opened by the authentication lookup.
        session.rollback()
        if uploads:
            directory.mkdir(parents=True, exist_ok=True)
        for upload in uploads:
            content, content_type = normalize_photo(upload)
            attachment = RequestAttachment(
                original_filename=display_filename(upload.filename), content_type=content_type, size_bytes=len(content),
            )
            path = attachment_path(directory, attachment.stored_filename)
            with path.open("xb") as target:
                written.append(path)
                target.write(content)
            record.attachments.append(attachment)

        if category_id is not None:
            category = session.get(Category, category_id)
            if category is None or not category.is_active:
                raise HTTPException(422, "Select an available category.")
            record.selected_category = category
        session.add(record)
        session.flush()
        result = RequestDetails.model_validate(record)
        session.commit()
    except BaseException:
        try:
            session.rollback()
        finally:
            for path in written:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Could not remove an uncommitted requester attachment.")
        raise
    return result


@router.post("", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
    "schema": {
        "type": "object", "required": ["title", "description", "location"],
        "properties": {
            "title": {"type": "string", "maxLength": 255}, "description": {"type": "string", "maxLength": 1000},
            "location": {"type": "string", "maxLength": 255}, "category_id": {"type": "integer"},
            "photos": {"type": "array", "maxItems": MAX_PHOTOS, "items": {"type": "string", "format": "binary"}},
        },
    },
}}}})
async def create_request(request: Request, user: Requester, session: Database, directory: UploadDirectory) -> RequestDetails:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
        raise HTTPException(415, "Send the request as multipart form data.")
    try:
        form = await MultiPartParser(request.headers, bounded_body(request), max_files=MAX_PHOTOS, max_fields=4, max_part_size=8192).parse()
    except ClientDisconnect:
        raise HTTPException(400, "The upload was interrupted.") from None
    except MultiPartException as error:
        detail = "You can upload at most five photos." if error.message.startswith("Too many files") else "Invalid multipart request."
        raise HTTPException(422, detail) from None

    try:
        return await run_in_threadpool(save_submission, form, user.id, session, directory)
    finally:
        await form.close()


@router.get("/categories")
def list_categories(_user: Requester, session: Database) -> list[CategorySummary]:
    categories = session.exec(select(Category).where(Category.is_active.is_(True)).order_by(Category.name, Category.id))
    return [CategorySummary.model_validate(category) for category in categories]


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)] | None = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    department: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    staff_or_student_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    default_location: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] = ""
    bio: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] = ""


@router.get("/profile")
def requester_profile(user: Requester, session: Database):
    row = query(session, """SELECT phone,department,staff_or_student_id,default_location,bio
        FROM requester_profiles WHERE user_id=:id""", id=user.id).mappings().first()
    return {**ProfileUpdate(full_name=user.full_name, **(dict(row) if row else {})).model_dump(), "email": user.email}


@router.patch("/profile")
def update_requester_profile(body: ProfileUpdate, user: Requester, session: Database):
    updates = body.model_dump(exclude_unset=True, exclude_none=True)
    if any("\0" in value for value in updates.values()):
        raise HTTPException(422, "Profile fields contain an invalid character.")
    uid = user.id
    previous = account_policy(session, uid)
    write_lock(session)
    current = session.get(User, uid)
    if current is None or not current.is_active or account_policy(session, uid) != previous:
        raise HTTPException(401, "Authentication required.")
    if current.role != UserRole.REQUESTER:
        raise HTTPException(403, "Insufficient permissions.")
    profile = {**requester_profile(current, session), **updates}
    current.full_name = profile["full_name"]
    query(session, """INSERT INTO requester_profiles VALUES(:id,:phone,:department,:staff_or_student_id,:default_location,:bio)
        ON CONFLICT(user_id) DO UPDATE SET phone=excluded.phone,department=excluded.department,
            staff_or_student_id=excluded.staff_or_student_id,default_location=excluded.default_location,bio=excluded.bio""",
          id=uid, **{key: profile[key] for key in ProfileUpdate.model_fields if key != "full_name"})
    session.commit()
    return profile


@router.get("")
def list_requests(
    user: Requester, session: Database,
    status: RequestStatus | Literal["pending_review"] | None = None,
    q: Annotated[str, Query(max_length=120)] = "",
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=50)] = 5,
) -> RequestList:
    owned = MaintenanceRequest.requester_id == user.id
    conditions = [owned]
    if status == "pending_review":
        conditions.append(MaintenanceRequest.status.in_([RequestStatus.SUBMITTED, RequestStatus.IN_REVIEW]))
    elif status is not None:
        conditions.append(MaintenanceRequest.status == status)

    search = q.strip()
    if search:
        request_number = search.lower().removeprefix("ff-").lstrip("0")
        conditions.append(or_(
            MaintenanceRequest.title.icontains(search, autoescape=True),
            MaintenanceRequest.location.icontains(search, autoescape=True),
            MaintenanceRequest.selected_category.has(Category.name.icontains(search, autoescape=True)),
            cast(MaintenanceRequest.id, String) == request_number,
        ))

    counts = dict(session.exec(select(MaintenanceRequest.status, func.count()).where(owned).group_by(MaintenanceRequest.status)).all())
    total = session.exec(select(func.count()).select_from(MaintenanceRequest).where(*conditions)).one()
    page = min(page, max(1, (total + per_page - 1) // per_page))
    records = session.exec(select(MaintenanceRequest).where(*conditions).options(joinedload(MaintenanceRequest.selected_category))
                           .order_by(MaintenanceRequest.created_at.desc(), MaintenanceRequest.id.desc())
                           .offset((page - 1) * per_page).limit(per_page))
    return RequestList(items=[RequestSummary.model_validate(record) for record in records], total=total,
                       page=page, per_page=per_page, status_counts=counts)


@router.get("/{request_id}", response_model_exclude_unset=True)
def read_request(request_id: RecordId, user: Requester, session: Database) -> OwnedRequestDetails:
    record = session.exec(select(MaintenanceRequest).where(
        MaintenanceRequest.id == request_id, MaintenanceRequest.requester_id == user.id,
    ).options(joinedload(MaintenanceRequest.selected_category), selectinload(MaintenanceRequest.attachments))).one_or_none()
    if record is None:
        raise HTTPException(404, "Request not found.")
    result = OwnedRequestDetails.model_validate(record)
    if record.status == RequestStatus.NEEDS_INFO:
        result.information_requested = session.exec(
            select(ReviewDecision.notes).where(
                ReviewDecision.request_id == record.id, ReviewDecision.decision == DecisionOutcome.NEEDS_INFO,
            ).order_by(ReviewDecision.created_at.desc(), ReviewDecision.id.desc()).limit(1)
        ).first()
    if record.status == RequestStatus.COMPLETED:
        result.resolution = resolution_for(session, record.id)
    return result


@router.get("/{request_id}/attachments/{attachment_id}", response_class=FileResponse)
def read_attachment(request_id: RecordId, attachment_id: RecordId, user: Requester, session: Database, directory: UploadDirectory):
    attachment = session.exec(select(RequestAttachment).join(MaintenanceRequest).where(
        MaintenanceRequest.requester_id == user.id, RequestAttachment.request_id == request_id,
        RequestAttachment.id == attachment_id,
    )).one_or_none()
    return attachment_response(directory, attachment)


@router.get("/{request_id}/resolution-proof", response_class=FileResponse)
def read_resolution_photo(request_id: RecordId, user: Requester, session: Database, directory: UploadDirectory):
    return proof_response(session, directory, request_id, owner=user.id)
