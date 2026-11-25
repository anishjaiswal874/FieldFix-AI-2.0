"""Read-only reviewer analytics, with explicit per-photo screening receipts."""

from collections import Counter, defaultdict
import json
from typing import Annotated, Literal
import re
from math import isfinite

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import func
from sqlmodel import select

from app import ai_services as ai
from app.account_policy import account_policy
from app.ai_routes import CV_LIMITATION, DISABLED, JPEG_CHECK, UNAVAILABLE, evidence_record, private, read_evidence, record_screening
from app.auth import authentication_required, require_roles
from app.auth_routes import AuthRoute
from app.browser_sessions import query, write_lock
from app.models import MaintenanceRequest, RequestAttachment, User, UserRole
from app.priority_rules import recommend_priority, report_text, uncertain
from app.decision_analytics import evaluate_priority, log_evaluation, performance_metrics
from app.request_routes import Database, RecordId, UploadDirectory
from app.reviewer_routes import PageNumber, Reviewer


router = APIRouter(prefix="/api/reviewer/analytics", dependencies=[Depends(private)],
                   tags=["Reviewer analytics"], route_class=AuthRoute)
RECENT_LIMIT = 24
SCORE_THRESHOLD = 0.86
UnitScore = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
PreviewImage = Annotated[str, StringConstraints(min_length=1, max_length=100000, pattern=r"^[A-Za-z0-9+/=]+$")]
HeatmapImage = Annotated[str, StringConstraints(min_length=1, max_length=250000, pattern=r"^[A-Za-z0-9+/=]+$")]
Analyst = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.REVIEWER))]


class InspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Annotated[int, Field(strict=True, ge=1, le=2**63-1)] | None = None
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)] | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.request_id is None) == (self.text is None):
            raise ValueError("Select a saved complaint or enter test text.")
        return self


class Prediction(BaseModel):
    label: Literal["plumbing", "electrical", "hvac"] | None
    margin: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None


class Keyword(BaseModel):
    term: str
    weight: UnitScore


class InspectionResult(BaseModel):
    items: list[Prediction]
    normalized: str = ""
    tokens: list[str] = []
    token_count: int = 0
    stopwords_removed: bool = False
    terms: list[Keyword] = []


class Quality(BaseModel):
    width: int
    height: int
    sharpness: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    brightness: Annotated[float, Field(ge=0, le=255, allow_inf_nan=False)]
    blur: Literal["pass", "warning"]
    exposure: Literal["pass", "warning"]
    resolution: Literal["pass", "warning"]


class CVPreview(BaseModel):
    crack_detected: bool
    score: UnitScore
    threshold: Literal[0.86]
    resized: PreviewImage
    grayscale: PreviewImage
    normalized: PreviewImage
    grad_cam: HeatmapImage | None
    quality: Quality


class PhotoConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concrete_closeup: Literal[True]


def actor_stamp(session, reviewer):
    return reviewer.id, account_policy(session, reviewer.id).revision


def verify_actor(session, stamp):
    reviewer_id, revision = stamp
    user = session.get(User, reviewer_id)
    if user is None or not user.is_active or account_policy(session, reviewer_id).revision != revision:
        raise authentication_required()
    if user.role != UserRole.REVIEWER:
        raise HTTPException(403, "Insufficient permissions.")


def run_nlp(texts, explain=False):
    payload = json.dumps({"texts": texts, "explain": explain}, ensure_ascii=False).encode("utf-8")
    try:
        result = InspectionResult.model_validate(ai.run_worker("nlp_inspect", payload))
    except ValidationError:
        return None
    return result if len(result.items) == len(texts) else None


def scoped_prediction(prediction, scope):
    return prediction.label if scope and prediction.label == scope and prediction.margin is not None else None


def saved_complaints(session, exclude=None):
    return query(session, """SELECT r.id,r.title,r.description,r.status,c.name category,t.name team,
        EXISTS(SELECT 1 FROM reviewer_evaluations e WHERE r.id=e.request_id AND e.feature='nlp' AND e.outcome='abstained') abstained,
        EXISTS(SELECT 1 FROM review_decisions d JOIN review_decision_audits a ON d.id=a.decision_id
               WHERE d.request_id=r.id AND a.was_overridden=1) overridden
        FROM maintenance_requests r LEFT JOIN categories c ON c.id=r.selected_category_id
        LEFT JOIN work_orders w ON w.request_id=r.id LEFT JOIN maintenance_teams t ON t.id=w.assigned_team_id
        WHERE :excluded IS NULL OR r.id<:excluded ORDER BY r.created_at DESC,r.id DESC LIMIT 200""",
                 excluded=exclude).mappings().all()


def corpus(rows, text=None):
    if not rows:
        return {"points": None, "scores": []}
    return ai.run_worker("nlp_corpus", json.dumps({"texts": [row["description"] for row in rows], "query": text},
                                                 ensure_ascii=False).encode())


def similar_complaints(rows, text):
    result = corpus(rows, text)
    scores = result.get("scores") if result else None
    if not isinstance(scores, list) or len(scores) != len(rows):
        return None
    possible = [(score, row) for score, row in zip(scores, rows) if type(score) in (int, float) and isfinite(score) and score > 0]
    return [{"id": row["id"], "title": row["title"], "status": row["status"], "team": row["team"],
             "similarity": round(min(score, 1.0) * 100, 1), "likely_duplicate": score >= .65}
            for score, row in sorted(possible, key=lambda item: -item[0])[:3]]


def consistency(description, crack_detected, nlp_label=None):
    text = report_text(description)
    if uncertain(text):
        return {"score": None, "mismatch": False, "explanation": "The complaint text is uncertain, so the alignment rule abstained."}
    structural = bool(re.search(r"\b(?:crack(?:ed|s|ing)?|concrete|structural|masonry|beam|column)\b", text))
    other_system = nlp_label or next((name for name, pattern in ai.SYSTEMS.items() if re.search(pattern, text)), None)
    if structural and other_system:
        return {"score": 50, "mismatch": True, "explanation": "The text mixes structural and other maintenance cues."}
    if structural and crack_detected:
        return {"score": 100, "mismatch": False, "explanation": "Structural text cues align with a possible crack result."}
    if structural:
        return {"score": 50, "mismatch": True, "explanation": "The text names a structural concern and screening stayed below the crack threshold."}
    if other_system and crack_detected:
        return {"score": 25, "mismatch": True, "explanation": "The text describes another system and the selected image screened as a possible crack."}
    return {"score": None, "mismatch": False, "explanation": "The available evidence cannot establish text–image alignment."}


@router.get("/performance")
def performance(_actor: Analyst, session: Database):
    return performance_metrics(session)


@router.get("/complaints")
def recent_complaints(_reviewer: Reviewer, session: Database):
    rows = session.exec(select(MaintenanceRequest.id, MaintenanceRequest.title)
                        .order_by(MaintenanceRequest.created_at.desc(), MaintenanceRequest.id.desc())
                        .limit(RECENT_LIMIT))
    return [{"id": item.id, "title": item.title} for item in rows]


@router.get("/embedding")
def embedding(reviewer: Reviewer, session: Database):
    rows = saved_complaints(session)
    if len(rows) < 2:
        return {"points": [], "status": "insufficient_data"}
    stamp = actor_stamp(session, reviewer)
    session.rollback()
    result = corpus(rows)
    verify_actor(session, stamp)
    points = result.get("points") if result else None
    if not isinstance(points, list) or len(points) != len(rows):
        return {"points": [], "status": "unavailable"}
    items = []
    for row, point in zip(rows, points):
        if not isinstance(point, list) or len(point) != 2 or not all(type(value) in (int, float) and isfinite(value) for value in point):
            return {"points": [], "status": "unavailable"}
        items.append({**{key: row[key] for key in ("id", "title", "category", "status", "abstained", "overridden")},
                      "x": point[0], "y": point[1]})
    return {"points": items, "status": "ready", "limit": 200}


@router.post("/requests/{request_id}/fused")
def fused(request_id: RecordId, reviewer: Reviewer, session: Database):
    request = session.get(MaintenanceRequest, request_id)
    if request is None:
        raise HTTPException(404, "Complaint not found.")
    title, description = request.title, request.description
    rows = saved_complaints(session, request_id)
    stamp = actor_stamp(session, reviewer)
    session.rollback()
    enabled = ai.enabled("nlp_demo")
    scope = ai.eligible_system(description) if enabled else None
    result = run_nlp([description]) if scope else None
    label = scoped_prediction(result.items[0], scope) if result else None
    outcome = "disabled" if not enabled else "unavailable" if scope and result is None else "suggested" if label else "abstained"
    matches = similar_complaints(rows, description) if enabled else None
    write_lock(session)
    verify_actor(session, stamp)
    if outcome in ("suggested", "abstained"):
        log_evaluation(session, "nlp", outcome, request_id, label=ai.LABELS.get(label))
    cv = query(session, """SELECT s.crack_detected,s.score,s.threshold,s.quality_json
        FROM reviewer_screenings s JOIN request_attachments a ON a.id=s.attachment_id
        WHERE a.request_id=:id AND s.model_version=:model
        ORDER BY s.crack_detected DESC, a.id DESC LIMIT 1""", id=request_id, model=ai.CV_MODEL).mappings().first()
    image = None
    if cv:
        image = {"crack_detected": bool(cv["crack_detected"]), "score": cv["score"], "threshold": cv["threshold"],
                 "quality": json.loads(cv["quality_json"]) if cv["quality_json"] else None,
                 "consistency": consistency(description, cv["crack_detected"], label)}
    priority = evaluate_priority(session, request_id, title, description) if ai.enabled("priority") else DISABLED
    session.commit()
    return {"nlp": {"label": ai.LABELS.get(label), "status": outcome},
            "priority": priority,
            "cv": image, "similar": matches, "advisory_only": True}


@router.get("/summary")
def summary(reviewer: Reviewer, session: Database):
    recent = session.exec(select(MaintenanceRequest.id, MaintenanceRequest.title, MaintenanceRequest.description)
                          .order_by(MaintenanceRequest.created_at.desc(), MaintenanceRequest.id.desc())
                          .limit(RECENT_LIMIT)).all()
    attachments = session.exec(select(func.count()).select_from(RequestAttachment)).one()
    screenings = query(session, """SELECT crack_detected,
        CASE WHEN score IS NULL OR threshold IS NULL THEN 'unrecorded'
             WHEN score >= threshold THEN 'at_or_above' ELSE 'below' END AS bucket, COUNT(*)
        FROM reviewer_screenings WHERE model_version=:model GROUP BY crack_detected,bucket""",
                       model=ai.CV_MODEL).all()
    screened = Counter()
    score_bins = Counter()
    for crack, bucket, count in screenings:
        screened["possible_crack" if crack else "below_threshold"] += count
        score_bins[bucket] += count
    screened["unscreened"] = attachments - sum(screened.values())

    stamp = actor_stamp(session, reviewer)
    session.rollback()
    priority = Counter()
    if ai.enabled("priority"):
        for row in recent:
            priority[recommend_priority(row.title, row.description).get("priority") or "abstained"] += 1
    eligible = [(row.description, scope) for row in recent if len(row.description.encode("utf-8")) <= 4000
                and (scope := ai.eligible_system(row.description))]
    counts = Counter(abstained=len(recent) - len(eligible))
    margins = defaultdict(list)
    nlp_state = "disabled"
    if ai.enabled("nlp_demo"):
        result = run_nlp([description for description, _ in eligible]) if eligible else None
        nlp_state = "unavailable" if eligible and result is None else "ready"
        if result:
            for output, (_, scope) in zip(result.items, eligible):
                label = scoped_prediction(output, scope)
                counts[label or "abstained"] += 1
                if label:
                    margins[label].append(output.margin)
    verify_actor(session, stamp)
    return {"recent": [{"id": row.id, "title": row.title} for row in recent], "sample_limit": RECENT_LIMIT,
            "nlp": {"state": nlp_state, "counts": counts if nlp_state == "ready" else {},
                    "mean_margin": {key: sum(values)/len(values) for key, values in margins.items()}},
            "priority": dict(priority), "cv": {"counts": dict(screened), "scores": dict(score_bins),
                                               "threshold": SCORE_THRESHOLD}}


@router.post("/nlp-inspection")
def inspect_nlp(body: InspectionInput, reviewer: Reviewer, session: Database):
    if not ai.enabled("nlp_demo"):
        return DISABLED
    description = body.text
    if body.request_id:
        request = session.get(MaintenanceRequest, body.request_id)
        if request is None:
            raise HTTPException(404, "Complaint not found.")
        description = request.description
    if len(description.encode("utf-8")) > 4000:
        raise HTTPException(422, "Inspect up to 4,000 UTF-8 bytes of text.")
    stamp = actor_stamp(session, reviewer)
    scope = ai.eligible_system(description)
    rows = saved_complaints(session, body.request_id)
    session.rollback()
    result = run_nlp([description], explain=True)
    matches = similar_complaints(rows, description)
    write_lock(session)
    verify_actor(session, stamp)
    if result is None:
        return UNAVAILABLE
    label = scoped_prediction(result.items[0], scope)
    log_evaluation(session, "nlp", "suggested" if label else "abstained", body.request_id, label=ai.LABELS.get(label))
    session.commit()
    return {"status": "research_only" if label else "abstained", "label": ai.LABELS.get(label),
            "margin": result.items[0].margin if label else None, "normalized": result.normalized,
            "tokens": result.tokens, "token_count": result.token_count,
            "stopwords_removed": result.stopwords_removed, "terms": result.terms, "similar": matches}


@router.get("/attachments")
def attachments(_reviewer: Reviewer, session: Database, page: PageNumber = 1):
    per_page = 15
    total = session.exec(select(func.count()).select_from(RequestAttachment)).one()
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = session.exec(select(RequestAttachment, MaintenanceRequest.title)
                       .join(MaintenanceRequest, RequestAttachment.request_id == MaintenanceRequest.id)
                       .order_by(RequestAttachment.id.desc()).offset((page-1)*per_page).limit(per_page))
    return {"page": page, "pages": pages, "items": [
        {"id": photo.id, "request_id": photo.request_id, "title": title, "original_filename": photo.original_filename,
         "content_type": photo.content_type} for photo, title in rows]}


@router.post("/requests/{request_id}/attachments/{attachment_id}/preview")
def inspect_photo(request_id: RecordId, attachment_id: RecordId, _confirmation: PhotoConfirmation,
                  reviewer: Reviewer, session: Database, directory: UploadDirectory):
    reference = evidence_record(session, request_id, attachment_id)
    if not ai.enabled("cv"):
        return DISABLED
    stamp = actor_stamp(session, reviewer)
    session.rollback()
    if not JPEG_CHECK.acquire(blocking=False):
        return UNAVAILABLE
    try:
        data = read_evidence(directory, reference)
    finally:
        JPEG_CHECK.release()
    if data is None:
        return {"status": "unsuitable", "explanation": "Select a valid still JPEG within the upload limits."}
    result = ai.run_worker("cv_preview", data)
    try:
        preview = CVPreview.model_validate(result)
    except ValidationError:
        return UNAVAILABLE
    if preview.crack_detected != (preview.score >= preview.threshold):
        return UNAVAILABLE
    write_lock(session)
    verify_actor(session, stamp)
    if evidence_record(session, request_id, attachment_id) != reference:
        raise HTTPException(409, "The attachment changed. Select it again.")
    request = session.get(MaintenanceRequest, request_id)
    last_label = query(session, """SELECT label FROM reviewer_evaluations
        WHERE request_id=:id AND feature='nlp' ORDER BY id DESC LIMIT 1""", id=request_id).scalar()
    alignment = consistency(request.description, preview.crack_detected, last_label)
    record_screening(session, attachment_id, preview.model_dump())
    return {"status": "screened", "limitation": CV_LIMITATION, **preview.model_dump(), "consistency": alignment}
