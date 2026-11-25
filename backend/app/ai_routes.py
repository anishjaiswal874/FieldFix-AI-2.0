"""Advisory, reviewer-only recommendations for existing maintenance requests."""

from io import BytesIO
import json
from threading import BoundedSemaphore
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image
from sqlmodel import select

from app import ai_services as ai
from app.browser_sessions import query, write_lock
from app.decision_analytics import evaluate_priority, log_evaluation
from app.auth import authentication_required
from app.models import Category, MaintenanceRequest, RequestAttachment, User, UserRole
from app.request_routes import Database, RecordId, UploadDirectory
from app.request_uploads import MAX_IMAGE_PIXELS, MAX_PHOTO_BYTES, attachment_path
from app.reviewer_routes import REVIEWABLE, Reviewer


def private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


router = APIRouter(prefix="/api/reviewer", tags=["Reviewer recommendations"],
                   dependencies=[Depends(private)])
CV_LIMITATION = (
    "This model was evaluated on concrete patches that may share source photos. "
    "A qualified person must assess structural safety."
)
DISABLED = {"status": "disabled", "explanation": "This recommendation is disabled."}
UNAVAILABLE = {"status": "unavailable", "explanation": "This service is temporarily unavailable. Continue manual review."}
NLP_ABSTAIN = {"status": "abstained", "actionable": False,
               "explanation": "No reliable AI suggestion. Review the description manually."}
JPEG_CHECK = BoundedSemaphore(1)


def record_screening(session, attachment_id, result):
    query(session, """INSERT INTO reviewer_screenings(attachment_id,model_version,crack_detected,score,threshold,quality_json)
        VALUES(:id,:model,:crack,:score,:threshold,:quality)
        ON CONFLICT(attachment_id,model_version) DO UPDATE SET crack_detected=excluded.crack_detected,
        score=CASE WHEN reviewer_screenings.crack_detected=excluded.crack_detected
                   THEN COALESCE(excluded.score,reviewer_screenings.score) ELSE excluded.score END,
        threshold=COALESCE(excluded.threshold,reviewer_screenings.threshold),
        quality_json=COALESCE(excluded.quality_json,reviewer_screenings.quality_json)""", id=attachment_id, model=ai.CV_MODEL,
        crack=int(result["crack_detected"]), score=result.get("score"), threshold=result.get("threshold"),
        quality=json.dumps(result["quality"]) if result.get("quality") else None)
    request_id = query(session, "SELECT request_id FROM request_attachments WHERE id=:id", id=attachment_id).scalar_one()
    log_evaluation(session, "cv", "screened", request_id, attachment_id,
                   "possible_crack" if result["crack_detected"] else "below_threshold")
    session.commit()


def request_record(session, request_id):
    record = session.get(MaintenanceRequest, request_id)
    if record is None:
        raise HTTPException(404, "Request not found.")
    if record.status not in REVIEWABLE:
        raise HTTPException(409, "This request is no longer awaiting a decision.")
    return record


def request_version(record):
    return record.status, record.updated_at, record.title, record.description, record.selected_category_id


def recheck(session, reviewer_id, request_id, previous):
    reviewer = session.get(User, reviewer_id)
    if reviewer is None or not reviewer.is_active:
        raise authentication_required()
    if reviewer.role != UserRole.REVIEWER:
        raise HTTPException(403, "Insufficient permissions.")
    current = request_record(session, request_id)
    if request_version(current) != previous:
        raise HTTPException(409, "The request changed. Refresh before requesting a recommendation.")
    return current


def evidence_record(session, request_id, attachment_id):
    row = session.exec(select(RequestAttachment).where(
        RequestAttachment.request_id == request_id, RequestAttachment.id == attachment_id,
    )).one_or_none()
    if row is None:
        raise HTTPException(404, "Attachment not found.")
    return row.stored_filename, row.content_type, row.size_bytes


def read_evidence(directory, reference):
    filename, content_type, size = reference
    if content_type != "image/jpeg" or not 0 < size <= MAX_PHOTO_BYTES:
        return None
    try:
        file = attachment_path(directory, filename)
        if file.resolve().parent != directory.resolve() or not file.is_file():
            return None
        with file.open("rb") as stream:
            data = stream.read(MAX_PHOTO_BYTES + 1)
        if len(data) != size:
            return None
        with Image.open(BytesIO(data), formats=["JPEG"]) as image:
            if getattr(image, "is_animated", False) or image.width * image.height > MAX_IMAGE_PIXELS:
                return None
            image.verify()
        with Image.open(BytesIO(data), formats=["JPEG"]) as image:
            image.load()
        return data
    except (HTTPException, Image.DecompressionBombError, OSError, ValueError, SyntaxError, EOFError, OverflowError):
        return None


@router.get("/recommendations/features")
def recommendation_features(_reviewer: Reviewer):
    return {feature: ai.enabled(feature) for feature in ai.FEATURES}


@router.get("/recommendations/status")
def recommendation_status(_reviewer: Reviewer):
    return ai.availability()


@router.post("/requests/{request_id}/recommendations/priority")
def priority(request_id: RecordId, reviewer: Reviewer, session: Database):
    record = request_record(session, request_id)
    if not ai.enabled("priority"):
        return DISABLED
    reviewer_id, previous = reviewer.id, request_version(record)
    write_lock(session)
    record = recheck(session, reviewer_id, request_id, previous)
    result = evaluate_priority(session, record.id, record.title, record.description)
    session.commit()
    return result


def nlp_result(request_id, reviewer, session, feature):
    record = request_record(session, request_id)
    if not ai.enabled(feature):
        return DISABLED
    previous = request_version(record)
    description = record.description
    scoped_label = ai.eligible_system(description)
    selected = session.get(Category, record.selected_category_id) if record.selected_category_id else None
    if scoped_label is None or (selected is not None and selected.name != ai.LABELS[scoped_label]):
        log_evaluation(session, "nlp", "abstained", request_id)
        session.commit()
        return NLP_ABSTAIN

    reviewer_id = reviewer.id
    session.rollback()
    result = ai.run_worker(feature, description.encode("utf-8"))
    current = recheck(session, reviewer_id, request_id, previous)
    if not ai.enabled(feature):
        return DISABLED
    if result is None:
        return UNAVAILABLE
    label = result["label"]
    if label != scoped_label:
        log_evaluation(session, "nlp", "abstained", request_id)
        session.commit()
        return NLP_ABSTAIN
    if current.selected_category_id:
        selected = session.get(Category, current.selected_category_id)
        if selected is None or selected.name != ai.LABELS[label]:
            log_evaluation(session, "nlp", "abstained", request_id)
            session.commit()
            return NLP_ABSTAIN

    suggestion = {"label": label, "label_name": ai.LABELS[label], "model_version": ai.NLP_MODEL}
    log_evaluation(session, "nlp", "suggested", request_id, label=ai.LABELS[label])
    session.commit()
    if feature == "nlp_demo":
        return {**suggestion, "status": "research_only", "actionable": False,
                "explanation": "The model output and the scope rule agree on this label.",
                "limitation": "Research only. This output is not validated for assignment decisions."}
    binding = ai.binding_for(label)
    category = session.get(Category, binding[0]) if binding else None
    if category is None or not category.is_active or category.name != binding[1]:
        return {**suggestion, "status": "research_only", "actionable": False,
                "explanation": "No current approved category binding. Review this research suggestion manually."}
    if not ai.NLP_ROLLOUT_APPROVED:
        return {**suggestion, "status": "research_only", "actionable": False,
                "explanation": "Actionable suggestions are pending validation and scope-policy approval."}
    return {**suggestion, "status": "suggested", "actionable": True,
            "category": {"id": category.id, "name": category.name}, "binding_version": ai.BINDING_VERSION,
            "explanation": "The reported fault and the model agree on this system. Confirm the category manually."}


@router.post("/requests/{request_id}/recommendations/nlp")
def nlp(request_id: RecordId, reviewer: Reviewer, session: Database):
    return nlp_result(request_id, reviewer, session, "nlp")


@router.post("/requests/{request_id}/recommendations/nlp-demo")
def nlp_demo(request_id: RecordId, reviewer: Reviewer, session: Database):
    return nlp_result(request_id, reviewer, session, "nlp_demo")


@router.post("/requests/{request_id}/attachments/{attachment_id}/screening")
def screen(request_id: RecordId, attachment_id: RecordId, request: Request,
           reviewer: Reviewer, session: Database, directory: UploadDirectory):
    record = request_record(session, request_id)
    reference = evidence_record(session, request_id, attachment_id)
    if not ai.enabled("cv"):
        return DISABLED
    if request.query_params.getlist("concrete_closeup") != ["true"]:
        return {"status": "unsuitable", "explanation": "Confirm that the selected photo is a close-up of concrete.",
                "limitation": CV_LIMITATION}

    previous, reviewer_id = request_version(record), reviewer.id
    session.rollback()
    if not JPEG_CHECK.acquire(blocking=False):
        return {**UNAVAILABLE, "limitation": CV_LIMITATION}
    try:
        data = read_evidence(directory, reference)
    finally:
        JPEG_CHECK.release()
    result = ai.run_worker("cv", data) if data is not None else None
    if result is not None:
        # Reserve the SQLite writer before rechecking request and photo state.
        write_lock(session)
    recheck(session, reviewer_id, request_id, previous)
    if evidence_record(session, request_id, attachment_id) != reference:
        raise HTTPException(409, "The attachment changed. Refresh before requesting screening.")
    if not ai.enabled("cv"):
        return DISABLED
    if data is None:
        return {"status": "unsuitable", "explanation": "Screening requires a valid still JPEG within the upload limits.",
                "limitation": CV_LIMITATION}
    if result is None:
        return {**UNAVAILABLE, "limitation": CV_LIMITATION}
    record_screening(session, attachment_id, result)
    return {
        "status": "screened", "model_version": ai.CV_MODEL, "limitation": CV_LIMITATION,
        "outcome": "possible_crack" if result["crack_detected"] else "threshold_not_met",
        "explanation": "Possible concrete crack pattern detected." if result["crack_detected"]
        else "Crack screening threshold not met.",
    }
