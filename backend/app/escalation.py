import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
ESCALATION_LOG_PATH = ROOT_DIR / "escalation_log.jsonl"


@dataclass(slots=True)
class EscalationDecision:
    stage: str
    resolved: bool
    confidence: float
    reason: str


def compute_resolution_confidence(sources: list[dict]) -> float:
    if not sources:
        return 0.0
    scores = [float(source.get("similarity", 0.0)) for source in sources]
    return max(scores, default=0.0)


def compute_l2_confidence(triage_confidence: float | None, routing_confidence: float | None) -> float:
    values = [value for value in (triage_confidence, routing_confidence) if value is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


def decide_l1(l1_confidence: float, threshold: float) -> EscalationDecision:
    if l1_confidence >= threshold:
        return EscalationDecision(
            stage="L1",
            resolved=True,
            confidence=l1_confidence,
            reason="L1 confidence met threshold.",
        )
    return EscalationDecision(
        stage="L1",
        resolved=False,
        confidence=l1_confidence,
        reason="L1 confidence below threshold.",
    )


def decide_l2(l2_confidence: float, threshold: float) -> EscalationDecision:
    if l2_confidence >= threshold:
        return EscalationDecision(
            stage="L2",
            resolved=True,
            confidence=l2_confidence,
            reason="L2 confidence met threshold.",
        )
    return EscalationDecision(
        stage="L2",
        resolved=False,
        confidence=l2_confidence,
        reason="L2 confidence below threshold.",
    )


def log_escalation(entry: dict) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    ESCALATION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ESCALATION_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
