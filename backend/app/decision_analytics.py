"""Advisory measurements and immutable snapshots of human decisions."""
from app.ai_services import enabled
from app.browser_sessions import query
from app.priority_rules import recommend_priority
from sqlalchemy import bindparam, text


def log_evaluation(session, feature, outcome, request_id=None, attachment_id=None, label=None):
    query(session, """INSERT INTO reviewer_evaluations(request_id,attachment_id,feature,outcome,label)
        VALUES(:request_id,:attachment_id,:feature,:outcome,:label)""",
          request_id=request_id, attachment_id=attachment_id, feature=feature, outcome=outcome, label=label)


def evaluate_priority(session, request_id, title, description):
    result = recommend_priority(title, description)
    # A null priority is an explicit abstention when an evaluation record exists.
    query(session, "INSERT INTO reviewer_priority_evaluations(request_id,priority) VALUES(:id,:priority)",
          id=request_id, priority=result["priority"])
    return result


def analysis_statuses(session, request_ids):
    if not request_ids:
        return {}
    statement = text("""
        SELECT request_id, feature, outcome FROM reviewer_evaluations WHERE request_id IN :ids
        UNION
        SELECT a.request_id, 'cv', 'screened' FROM request_attachments a
        JOIN reviewer_screenings s ON s.attachment_id=a.id WHERE a.request_id IN :ids
        UNION
        SELECT request_id, 'priority', CASE WHEN priority IS NULL THEN 'abstained' ELSE 'suggested' END
        FROM reviewer_priority_evaluations WHERE request_id IN :ids
        UNION
        SELECT d.request_id, 'priority', 'suggested' FROM review_decisions d
        JOIN review_decision_audits a ON a.decision_id=d.id
        WHERE d.request_id IN :ids AND a.ai_suggested_priority IS NOT NULL
    """).bindparams(bindparam("ids", expanding=True))
    analyzed = {request_id: set() for request_id in request_ids}
    abstained = set()
    for request_id, feature, outcome in session.execute(statement, {"ids": request_ids}):
        if outcome == "abstained":
            abstained.add(request_id)
        else:
            analyzed[request_id].add(feature)

    statuses = {}
    for request_id, features in analyzed.items():
        if {"nlp", "cv"} <= features:
            status = "nlp_cv"
        elif "cv" in features:
            status = "cv"
        elif len(features) > 1:
            status = "analyzed"
        elif features:
            status = next(iter(features))
        else:
            status = "abstained" if request_id in abstained else "not_analyzed"
        statuses[request_id] = status
    return statuses


def audit_decision(session, decision, final_category, reason):
    request = decision.request
    label = query(session, """SELECT label FROM reviewer_evaluations
        WHERE request_id=:id AND feature='nlp' ORDER BY id DESC LIMIT 1""", id=request.id).scalar()
    suggested_priority = None
    if enabled("priority"):
        suggested_priority = evaluate_priority(session, request.id, request.title, request.description)["priority"]
    cv = query(session, """SELECT MAX(s.crack_detected) FROM reviewer_screenings s
        JOIN request_attachments a ON a.id=s.attachment_id WHERE a.request_id=:id""", id=request.id).scalar()
    category_override = int(label.casefold() != final_category.casefold()) if label and final_category else None
    priority_override = int(suggested_priority != decision.final_priority) if suggested_priority and decision.final_priority else None
    comparisons = [value for value in (category_override, priority_override) if value is not None]
    query(session, """INSERT INTO review_decision_audits(
        decision_id, ai_suggested_category, final_category, ai_suggested_priority, final_priority,
        cv_flagged, category_overridden, priority_overridden, was_overridden, override_reason)
        VALUES(:id,:category,:final_category,:priority,:final_priority,:cv,:category_override,
               :priority_override,:override,:reason)""",
          id=decision.id, category=label, final_category=final_category,
          priority=suggested_priority, final_priority=decision.final_priority, cv=cv,
          category_override=category_override, priority_override=priority_override,
          override=int(any(comparisons)) if comparisons else None, reason=reason)


def percentage(number, total):
    return round(100 * number / total, 1) if total else None


def performance_metrics(session):
    nlp, cv, abstained = query(session, """SELECT COUNT(CASE WHEN feature='nlp' THEN 1 END),
        COUNT(CASE WHEN feature='cv' THEN 1 END),COUNT(CASE WHEN outcome='abstained' THEN 1 END)
        FROM reviewer_evaluations""").one()
    row = query(session, """SELECT COUNT(*) decisions, COUNT(was_overridden) compared,
        COUNT(CASE WHEN was_overridden=1 THEN 1 END) overrides, COUNT(category_overridden) categories_compared,
        COUNT(CASE WHEN category_overridden=1 THEN 1 END) category_overrides,
        COUNT(priority_overridden) priorities_compared, COUNT(CASE WHEN priority_overridden=1 THEN 1 END) priority_overrides,
        COUNT(CASE WHEN cv_flagged=1 THEN 1 END) flagged_decisions
        FROM review_decision_audits""").mappings().one()
    compared, overrides = row["compared"], row["overrides"]
    recent_category, recent_priority = query(session, """SELECT
        COUNT(CASE WHEN category_overridden=1 THEN 1 END), COUNT(CASE WHEN priority_overridden=1 THEN 1 END)
        FROM (SELECT category_overridden,priority_overridden FROM review_decision_audits ORDER BY decision_id DESC LIMIT 30)""").one()
    total, resolved = query(session, "SELECT COUNT(*),COUNT(CASE WHEN status='resolved' THEN 1 END) FROM work_orders").one()
    teams = query(session, """SELECT t.name,
        COUNT(CASE WHEN w.status IN ('open','in_progress') THEN 1 END) active,
        COUNT(CASE WHEN w.status='resolved' THEN 1 END) resolved
        FROM maintenance_teams t LEFT JOIN work_orders w ON t.id=w.assigned_team_id
        WHERE t.is_active=1 OR w.id IS NOT NULL GROUP BY t.id ORDER BY t.name""").mappings().all()
    return {**dict(row), "nlp_evaluations": nlp, "cv_screenings": cv, "total_ai_evaluations": nlp+cv,
            "abstained": abstained, "abstention_rate": percentage(abstained, nlp),
            "agreement_rate": percentage(compared-overrides, compared), "override_rate": percentage(overrides, compared),
            "category_agreement_rate": percentage(row["categories_compared"]-row["category_overrides"], row["categories_compared"]),
            "priority_agreement_rate": percentage(row["priorities_compared"]-row["priority_overrides"], row["priorities_compared"]),
            "resolved": resolved, "total_work_orders": total,
            "recent_category_overrides": recent_category, "recent_priority_overrides": recent_priority,
            "resolution_rate": percentage(resolved,total), "teams": [dict(team) for team in teams]}
