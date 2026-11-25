from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import select

from app.account_policy import account_policy
from app.auth import authentication_required
from app.browser_sessions import query, write_lock
from app.models import MaintenanceRequest, RequestStatus, User, UserRole, WorkOrder, WorkOrderStatus, utc_now
from app.request_uploads import IMAGE_TYPES, attachment_path


class Resolution(BaseModel):
    notes: str
    has_proof: bool
    proof_name: str | None
    resolved_at: datetime
    resolved_by: str


def resolution_for(session, request_id):
    row = query(session, """SELECT r.*, u.full_name FROM work_order_resolutions r
        JOIN work_orders w ON w.id=r.work_order_id JOIN users u ON u.id=r.resolved_by
        WHERE w.request_id=:request_id""", request_id=request_id).mappings().first()
    return Resolution(notes=row["resolution_notes"], has_proof=bool(row["resolution_image_path"]),
                      proof_name=row["image_name"], resolved_at=row["resolved_at"], resolved_by=row["full_name"]) if row else None


def proof_response(session, directory, request_id, owner=None):
    row = query(session, """SELECT r.resolution_image_path, r.image_type
        FROM work_order_resolutions r JOIN work_orders w ON w.id=r.work_order_id
        JOIN maintenance_requests m ON m.id=w.request_id
        WHERE m.id=:request_id AND (:owner IS NULL OR m.requester_id=:owner)""",
                request_id=request_id, owner=owner).first()
    if not row or not row.resolution_image_path or row.image_type not in IMAGE_TYPES.values():
        raise HTTPException(404, "Resolution photo not found.")
    path = attachment_path(directory, row.resolution_image_path)
    if not path.is_file():
        raise HTTPException(404, "Resolution photo not found.")
    return FileResponse(path, media_type=row.image_type, headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


def close_work_order(session, order_id, reviewer, notes, photo, directory: Path):
    uid = reviewer.id
    policy = account_policy(session, uid)
    write_lock(session)
    actor = session.get(User, uid)
    if actor is None or not actor.is_active or account_policy(session, uid) != policy:
        raise authentication_required()
    if actor.role != UserRole.REVIEWER:
        raise HTTPException(403, "Insufficient permissions.")
    order = session.get(WorkOrder, order_id)
    if order is None:
        raise HTTPException(404, "Work order not found.")
    if order.status not in (WorkOrderStatus.OPEN, WorkOrderStatus.IN_PROGRESS) or order.request.status != RequestStatus.APPROVED:
        raise HTTPException(409, "This task is no longer awaiting resolution.")

    path = None
    try:
        stored = media = filename = None
        if photo is not None:
            image, media, filename = photo
            stored = uuid4().hex
            directory.mkdir(parents=True, exist_ok=True)
            target = attachment_path(directory, stored)
            with target.open("xb") as stream:
                path = target
                stream.write(image)
        now = utc_now()
        order.status = WorkOrderStatus.RESOLVED
        order.resolved_at = now
        order.updated_at = now
        order.request.status = RequestStatus.COMPLETED
        order.request.updated_at = now
        query(session, """INSERT INTO work_order_resolutions
            VALUES(:id,:notes,:path,:media,:filename,1,:stamp,:uid)""",
              id=order_id, notes=notes, path=stored, media=media, filename=filename, stamp=now.isoformat(), uid=uid)
        result = resolution_for(session, order.request_id)
        session.commit()
        return result
    except BaseException:
        try:
            session.rollback()
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
        raise
