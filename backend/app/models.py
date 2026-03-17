import re
from typing import Any

from pydantic import BaseModel, Field, field_validator


PROMPT_INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "system prompt",
    "developer message",
    "developer instructions",
    "reveal hidden prompt",
)
MIN_ALPHA_TOKENS = 3


def _normalize_text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    return re.sub(r"\s+", " ", text).strip()


def _validate_free_text(value: Any, field_name: str) -> str:
    normalized = _normalize_text(value)
    lowered = normalized.lower()
    if not normalized:
        raise ValueError(f"{field_name} cannot be empty.")

    if any(pattern in lowered for pattern in PROMPT_INJECTION_PATTERNS):
        raise ValueError(f"{field_name} contains unsupported prompt-instruction text.")

    alpha_tokens = re.findall(r"[a-zA-Z]{2,}", normalized)
    if len(alpha_tokens) < MIN_ALPHA_TOKENS:
        raise ValueError(f"{field_name} needs a more specific issue description.")

    if re.search(r"(.)\1{7,}", normalized):
        raise ValueError(f"{field_name} contains repetitive text and needs a cleaner description.")

    return normalized


def _normalize_optional_text(value: Any) -> str:
    return _normalize_text(value)


class QueryRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    top_k: int = Field(default=4, ge=1, le=8)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        return _validate_free_text(value, "Query")


class TriageRequest(BaseModel):
    ticket_summary: str = Field(min_length=3, max_length=2000)
    category: str = Field(default="", max_length=200)
    ci_category: str = Field(default="", max_length=200)
    ci_subcategory: str = Field(default="", max_length=200)
    top_k: int = Field(default=5, ge=3, le=10)

    @field_validator("ticket_summary")
    @classmethod
    def validate_ticket_summary(cls, value: str) -> str:
        return _validate_free_text(value, "Ticket summary")

    @field_validator("category", "ci_category", "ci_subcategory")
    @classmethod
    def normalize_optional_fields(cls, value: str) -> str:
        return _normalize_optional_text(value)


class RoutingRequest(BaseModel):
    description: str = Field(default="", max_length=2000)
    category: str = Field(min_length=1, max_length=200)
    subcategory: str = Field(min_length=1, max_length=200)
    u_symptom: str = Field(min_length=1, max_length=200)
    impact: str = Field(min_length=1, max_length=50)
    urgency: str = Field(min_length=1, max_length=50)
    contact_type: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=100)
    top_k: int = Field(default=5, ge=3, le=10)

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value:
            return ""
        return _validate_free_text(value, "Description")

    @field_validator(
        "category",
        "subcategory",
        "u_symptom",
        "impact",
        "urgency",
        "contact_type",
        "location",
        mode="before",
    )
    @classmethod
    def normalize_optional_fields(cls, value: Any) -> str:
        return _normalize_optional_text(value)


class SourceChunk(BaseModel):
    incident_id: str
    ticket_id: str
    media_asset: str
    category: str
    incident_details: str
    description: str
    solution: str
    similarity: float
    semantic_score: float
    keyword_score: float


class TriageSourceChunk(BaseModel):
    incident_id: str
    status: str
    priority: str
    impact: str
    urgency: str
    category: str
    ci_name: str
    ci_cat: str
    ci_subcat: str
    wbs: str
    alert_status: str
    closure_code: str
    similarity: float
    keyword_score: float
    field_score: float


class RoutingSourceChunk(BaseModel):
    incident_id: str
    category: str
    subcategory: str
    symptom: str
    impact: str
    urgency: str
    assignment_group: str
    contact_type: str
    location: str
    similarity: float
    keyword_score: float
    field_score: float


class QueryResponse(BaseModel):
    answer: str
    sources: list[SourceChunk]


class TriageResponse(BaseModel):
    priority: str
    impact: str
    urgency: str
    confidence: float
    rationale: str
    predicted_resolution_time_minutes: float | None = None
    resolution_time_mae_minutes: float | None = None
    resolution_time_rmse_minutes: float | None = None
    resolution_time_train_samples: int | None = None
    resolution_time_test_samples: int | None = None
    sources: list[TriageSourceChunk]


class RoutingResponse(BaseModel):
    assignment_group: str
    confidence: float
    rationale: str
    sources: list[RoutingSourceChunk]
