"""Conservative text rules for the reviewer pilot."""

import re
import unicodedata


VERSION = "m13-priority-v1"
POSITIVE_FAULTS = re.compile(
    r"\b(?:no (?:power|heat|cooling|water|airflow|functional impact)|"
    r"not (?:working|draining|flushing|cooling|heating)|(?:do not|does not) affect (?:use|function))\b"
)
UNCERTAIN = re.compile(
    r"\b(?:no|not|never|without|neither|either|or|maybe|might|may|could|seems|possibly|possible|suspected|unclear|if|"
    r"previously|historical|formerly|yesterday|last (?:week|month|year)|used to|"
    r"(?:has |was |now )?(?:resolved|repaired|fixed|restored)|stopped|working now|back on|"
    r"(?:was|were) (?:sparking|flooding|tripping|leaking|blocked|not working)|"
    r"(?:isn|aren|wasn|weren|don|doesn|didn|can|couldn|won|wouldn|hasn|haven|hadn|ain)'t)\b"
)
HIGH = (
    ("sparking", r"\b(?:(?:is|are|keeps) sparking|sparking (?:at|from)|(?:is|are) emitting sparks|sparks (?:coming|shooting) from)\b", "Reported electrical sparking"),
    ("active_flooding", r"\b(?:uncontrolled flooding|actively flooding|water is rising|water (?:is )?pouring|water (?:is )?gushing)\b", "Reported uncontrolled water or active flooding"),
    ("falling_material", r"\b(?:ceiling|concrete|masonry|debris) (?:is|are) falling\b", "Reported falling material"),
)
MEDIUM = (
    ("service_outage", r"\b(?:no (?:power|heat|cooling|water|airflow)|out of service|not (?:working|draining|cooling|heating)|unable to use|service outage)\b", "Reported loss of use or service"),
)
COSMETIC = re.compile(r"\b(?:cosmetic(?: only)?|scuff(?:s|ed)?|paint scratch(?:es)?|surface stain(?:s)?)\b")
NO_IMPACT = re.compile(r"\b(?:cosmetic only|no functional impact|(?:do not|does not) affect (?:use|function)|still works normally)\b")


def report_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).casefold().replace("’", "'")
    return " ".join(value.split())


def uncertain(text: str) -> bool:
    return "?" in text or UNCERTAIN.search(POSITIVE_FAULTS.sub(" ", text)) is not None


def first_match(rules, text):
    return next(((rule, fact) for rule, pattern, fact in rules if re.search(pattern, text)), None)


def recommend_priority(title: str, description: str) -> dict:
    text = report_text(f"{title}. {description}")
    abstention = {"status": "abstained", "priority": None, "fact": None, "rule_version": VERSION}
    if not description.strip() or uncertain(text):
        return {**abstention, "explanation": "Timing or impact is unclear. Choose priority after checking the report."}

    high, medium = first_match(HIGH, text), first_match(MEDIUM, text)
    low = COSMETIC.search(text) and NO_IMPACT.search(text)
    if NO_IMPACT.search(text) and (high or medium):
        return {**abstention, "explanation": "The report gives conflicting information about impact."}
    if high:
        priority, (rule, fact), explanation = "high", high, "The report describes an immediate hazard or active damage."
    elif medium:
        priority, (rule, fact), explanation = "medium", medium, "The report describes a service disruption."
    elif low:
        priority, (rule, fact) = "low", ("cosmetic_only", "Reported cosmetic issue with no functional impact")
        explanation = "The report explicitly describes a cosmetic issue with no loss of function."
    else:
        return {**abstention, "explanation": "The report does not establish urgency under the pilot rules."}
    return {"status": "suggested", "priority": priority, "fact": fact, "explanation": explanation,
            "rule_id": rule, "rule_version": VERSION}
