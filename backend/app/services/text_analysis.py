"""Server-side text analysis for complaint descriptions and voice transcriptions.

Uses spaCy (``en_core_web_sm`` by default, ``SPACY_MODEL`` to override) for named
entities, noun chunks and dependency parsing. When spaCy or its model is not
installed the analyser falls back to regular expressions, so the endpoint keeps
working, just with fewer entities. ``backend`` in the result says which ran.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from threading import Lock

from app.ai.incident_classifier import RULES, analyze_incident
from app.services.severity_service import keyword_hits, score_severity

logger = logging.getLogger(__name__)

# (category, issue label, trigger phrases). Plurals are listed explicitly so the
# regex fallback matches them too; spaCy additionally matches on lemmas.
ISSUES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("road", "pothole", ("pothole", "potholes", "crater", "road damage", "broken road", "damaged road")),
    ("road", "fallen tree", ("fallen tree", "tree fell", "tree has fallen", "uprooted tree", "tree branch", "fallen branch")),
    ("road", "road blockage", ("blocked road", "road blocked", "blocked highway", "highway blocked", "road closed")),
    ("garbage", "garbage overflow", ("garbage", "trash", "waste", "litter", "dustbin", "dustbins", "bin", "bins", "dump", "rubbish")),
    ("water", "water leak", ("water leak", "pipe burst", "burst pipe", "pipeline", "leakage", "leaking pipe", "no water supply")),
    ("water", "sewage / drainage", ("sewage", "drain", "drains", "drainage", "gutter", "manhole", "open manhole")),
    ("water", "waterlogging", ("waterlogging", "waterlogged", "flooded", "flood", "flooding")),
    ("electrical", "streetlight failure", ("streetlight", "streetlights", "street light", "street lights", "lamp post", "light not working")),
    ("electrical", "electrical hazard", ("live wire", "electric wire", "hanging wire", "transformer", "power cut", "sparking", "short circuit")),
    ("fire", "fire", ("fire", "smoke", "burning", "flames", "blaze")),
    ("accident", "road accident", ("accident", "collision", "crash", "hit and run", "overturned")),
)

LOCATION_PREPOSITIONS = ("near", "opposite", "behind", "beside", "next to", "in front of", "outside", "at", "on", "in")
LANDMARK_RE = re.compile(
    r"\b(near|opposite|behind|beside|next to|in front of|outside)\s+(?:the\s+)?"
    r"((?:[A-Z][\w'.-]*|\d+[A-Za-z]*)(?:\s+(?:[A-Z][\w'.-]*|\d+[A-Za-z]*|of|and))*)"
)

INTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "follow_up": (
        r"\bmy (previous |earlier |old )?(complaint|report|ticket)\b", r"\bany update\b", r"\bstatus of\b",
        r"\balready (reported|complained)\b", r"\bstill not (fixed|resolved|repaired|cleared)\b",
        r"\bcomplaint (number|no\.?|#)\s*\d+", r"#\d+\b", r"\bwhen will\b", r"\bno action (has been|was) taken\b",
    ),
    "feedback": (
        r"\bthank(s| you)\b", r"\bgreat (job|work)\b", r"\bgood (job|work)\b", r"\bappreciate\b",
        r"\bwell done\b", r"\bfixed (quickly|promptly|fast)\b",
    ),
    "emergency": (
        r"\bhelp\b", r"\bemergency\b", r"\burgent(ly)?\b", r"\bimmediately\b", r"\bright now\b", r"\bsos\b",
    ),
}
CRITICAL_TERMS = {"fire", "explosion", "electrocution", "electrocuted", "live wire", "gas leak", "collapsed",
                  "collapse", "trapped", "injured", "bleeding", "unconscious", "dead body"}
CATEGORY_DEPARTMENT = {incident_type: department for _, incident_type, _, department in RULES}
QUESTION_START = re.compile(r"^\s*(how|what|when|where|why|who|which|can|could|is|are|do|does|will)\b", re.I)


@dataclass
class _Nlp:
    model: object | None = None
    name: str = "rules"
    tried: bool = False


_nlp = _Nlp()
_nlp_lock = Lock()


def _load_spacy():
    """Load the spaCy pipeline once; remember a failure so we don't retry on every request."""
    if _nlp.tried:
        return _nlp.model
    with _nlp_lock:
        if not _nlp.tried:
            model_name = os.getenv("SPACY_MODEL", "en_core_web_sm")
            try:
                import spacy

                _nlp.model = spacy.load(model_name)
                _nlp.name = f"spacy:{model_name}"
            except Exception as exc:  # ImportError, OSError (model missing)
                logger.warning("spaCy unavailable (%s); text analysis uses rule-based fallback", exc)
            _nlp.tried = True
    return _nlp.model


def backend_name() -> str:
    _load_spacy()
    return _nlp.name


def _find_issues(text: str, lemma_text: str | None) -> list[dict]:
    lowered = text.lower()
    found: list[dict] = []
    for category, label, phrases in ISSUES:
        evidence: list[str] = []
        for phrase in phrases:
            pattern = rf"\b{re.escape(phrase)}\b"
            if re.search(pattern, lowered) or (lemma_text and re.search(pattern, lemma_text)):
                evidence.append(phrase)
        if evidence:
            found.append({"issue": label, "category": category, "evidence": evidence})
    # Most-evidenced issues first.
    return sorted(found, key=lambda f: -len(f["evidence"]))


def _intent(text: str, issues: list[dict]) -> dict:
    lowered = text.lower()
    scores: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                scores[intent] = scores.get(intent, 0) + 1
                reasons.setdefault(intent, f"“{match.group(0)}”")

    critical = [p for p, _ in keyword_hits(text) if p in CRITICAL_TERMS]
    if critical:
        scores["emergency"] = scores.get("emergency", 0) + 2
        reasons["emergency"] = f"“{critical[0]}”"
    if issues:
        report = 1 + 0.5 * min(len(issues), 2)
        # "Thanks for fixing the streetlight" / "any update on the garbage?" mention an issue
        # without reporting a new one.
        if "feedback" in scores or "follow_up" in scores:
            report -= 1
        scores["report_issue"] = scores.get("report_issue", 0) + report
        reasons.setdefault("report_issue", f"describes {issues[0]['issue']}")
    asks, wh_start = text.strip().endswith("?"), bool(QUESTION_START.match(text))
    if asks or wh_start:
        scores["question"] = scores.get("question", 0) + (2 if asks and wh_start else 1)
        reasons.setdefault("question", "phrased as a question")

    if not scores:
        return {"label": "other", "confidence": 0.3, "reason": "no clear intent signals", "alternatives": []}

    # An emergency report is still a report; emergency wins only with a critical/urgent signal.
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    label, top = ranked[0]
    total = sum(scores.values())
    confidence = round(min(0.95, 0.45 + 0.5 * top / total), 2)
    return {
        "label": label,
        "confidence": confidence,
        "reason": reasons.get(label, ""),
        "alternatives": [k for k, _ in ranked[1:]],
    }


def _spacy_parts(doc) -> tuple[list[dict], list[str], list[str], str]:
    entities = [
        {"text": ent.text, "label": ent.label_, "start": ent.start_char, "end": ent.end_char}
        for ent in doc.ents
    ]
    locations = [ent.text for ent in doc.ents if ent.label_ in {"GPE", "LOC", "FAC"}]
    # "near the city hospital", "opposite Rasulgarh square": objects of location prepositions.
    for token in doc:
        if token.dep_ == "pobj" and token.head.lower_ in LOCATION_PREPOSITIONS and token.head.lower_ not in {"on", "in", "at"}:
            span = doc[token.left_edge.i: token.right_edge.i + 1]
            phrase = re.sub(r"^(the|a|an)\s+", "", span.text, flags=re.I)
            if phrase and phrase.lower() not in {l.lower() for l in locations}:
                locations.append(phrase)
    times = [ent.text for ent in doc.ents if ent.label_ in {"DATE", "TIME"}]
    lemma_text = " ".join(token.lemma_.lower() for token in doc)
    return entities, locations, times, lemma_text


def _rule_parts(text: str) -> tuple[list[dict], list[str], list[str]]:
    locations = [m.group(2).strip() for m in LANDMARK_RE.finditer(text)]
    entities = [
        {"text": m.group(2).strip(), "label": "LOC", "start": m.start(2), "end": m.start(2) + len(m.group(2).strip())}
        for m in LANDMARK_RE.finditer(text)
    ]
    times = re.findall(r"\b(today|yesterday|tonight|this morning|last night|since \w+|for \d+ (?:days?|weeks?|hours?))\b", text, re.I)
    return entities, locations, times


def _dedupe_locations(locations: list[str]) -> list[str]:
    """Drop exact repeats and names contained in a longer one ("Patia" vs "Patia market")."""
    unique = list(dict.fromkeys(locations))
    return [loc for loc in unique if not any(loc != other and loc.lower() in other.lower() for other in unique)]


def _title(issues: list[dict], locations: list[str], fallback_category: str) -> str:
    issue = issues[0]["issue"] if issues else fallback_category
    title = issue[:1].upper() + issue[1:]
    if locations:
        title += f" near {locations[0]}"
    return title[:120]


def analyze_text(text: str, latitude: float | None = None, longitude: float | None = None) -> dict:
    text = re.sub(r"\s+", " ", text).strip()
    nlp = _load_spacy()
    if nlp is not None:
        entities, locations, times, lemma_text = _spacy_parts(nlp(text))
    else:
        (entities, locations, times), lemma_text = _rule_parts(text), None

    issues = _find_issues(text, lemma_text)
    routing = analyze_incident(text)
    # If the keyword router fell through to "general" but the lexicon found something, prefer it.
    category = routing["incident_type"]
    if category == "general" and issues:
        category = issues[0]["category"]
    severity = score_severity(category=category, text=text, latitude=latitude, longitude=longitude)
    # Lead with the issue that matches the routed category ("fire", not "transformer").
    issues.sort(key=lambda i: i["category"] != category)
    locations = _dedupe_locations(locations)

    return {
        "backend": _nlp.name,
        "text": text,
        "entities": entities,
        "locations": _dedupe_locations(locations),
        "time_expressions": list(dict.fromkeys(times)),
        "key_issues": issues,
        "intent": _intent(text, issues),
        "suggested_category": category,
        "suggested_department": routing["department"] if category == routing["incident_type"] else CATEGORY_DEPARTMENT.get(category),
        "suggested_title": _title(issues, locations, category),
        "severity": severity.as_dict(),
    }
