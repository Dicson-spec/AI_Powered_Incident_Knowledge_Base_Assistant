import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from .escalation import compute_l2_confidence, compute_resolution_confidence, decide_l1, decide_l2, log_escalation
from .knowledge_store import fetch_entries, push_entry, update_entry
from .web import add_cors


ROOT_DIR = Path(__file__).resolve().parents[2]
FEEDBACK_LOG_PATH = ROOT_DIR / "feedback_log.jsonl"
SERVICE_MAP = {
    "resolution": settings.resolution_service_url,
    "triage": settings.triage_service_url,
    "routing": settings.routing_service_url,
}


app = FastAPI(
    title="Incident Knowledge Base Gateway",
    version="1.0.0",
)

add_cors(app)


class FeedbackRequest(BaseModel):
    agent: str = Field(min_length=3, max_length=30)
    rating: int = Field(ge=1, le=5)
    feedback: str = Field(default="", max_length=2000)
    request: dict
    response: dict


class EscalationRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    ticket_summary: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=200)
    ci_category: str = Field(default="", max_length=200)
    ci_subcategory: str = Field(default="", max_length=200)
    subcategory: str = Field(default="", max_length=200)
    u_symptom: str = Field(default="", max_length=200)
    impact: str = Field(default="", max_length=50)
    urgency: str = Field(default="", max_length=50)
    contact_type: str = Field(default="", max_length=100)
    location: str = Field(default="", max_length=100)
    l1_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    l2_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class KnowledgePushRequest(BaseModel):
    content: str = Field(min_length=3, max_length=5000)
    source_agent: str = Field(min_length=2, max_length=30)
    metadata: dict = Field(default_factory=dict)


class KnowledgeFetchRequest(BaseModel):
    query: str = Field(default="", max_length=200)
    source_agent: str = Field(default="", max_length=30)
    limit: int = Field(default=50, ge=1, le=200)


class KnowledgeUpdateRequest(BaseModel):
    entry_id: str = Field(min_length=8, max_length=64)
    content: str | None = Field(default=None, max_length=5000)
    metadata: dict | None = None


async def _service_request(
    service: str,
    method: str,
    path: str,
    json_payload: dict | None = None,
) -> dict:
    base_url = SERVICE_MAP[service]
    timeout = httpx.Timeout(90.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method=method,
                url=f"{base_url}{path}",
                json=json_payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"{service} service is unavailable: {exc}",
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"detail": response.text or f"{service} service returned a non-JSON response."}

    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=payload.get("detail", "Service request failed."))

    return payload


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/api/health")
async def healthcheck() -> dict:
    async def service_health(service: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0)) as client:
                response = await client.get(f"{url}/health")
            if response.status_code >= 400:
                return {"status": "error", "detail": response.text}
            return response.json()
        except httpx.HTTPError as exc:
            return {"status": "down", "detail": str(exc)}

    results = await asyncio.gather(
        *(service_health(name, url) for name, url in SERVICE_MAP.items())
    )
    services = dict(zip(SERVICE_MAP.keys(), results))
    gateway_status = "ok" if all(item.get("status") == "ok" for item in services.values()) else "degraded"
    return {"status": gateway_status, "services": services}


@app.get("/api/stats")
async def stats() -> dict:
    resolution_stats, triage_stats, routing_stats = await asyncio.gather(
        _service_request("resolution", "GET", "/stats"),
        _service_request("triage", "GET", "/stats"),
        _service_request("routing", "GET", "/stats"),
    )
    return {
        "architecture": "microservices",
        "gateway_url": settings.gateway_url,
        "dataset": resolution_stats["dataset"],
        "indexed_incidents": resolution_stats["indexed_incidents"],
        "triage_dataset": triage_stats["dataset"],
        "triage_records": triage_stats["triage_records"],
        "routing_dataset": routing_stats["dataset"],
        "routing_records": routing_stats["routing_records"],
        "resolution": resolution_stats,
        "triage": triage_stats,
        "routing": routing_stats,
    }


@app.post("/api/query")
async def query_incidents(request: Request) -> dict:
    payload = await request.json()
    return await _service_request("resolution", "POST", "/resolution", payload)


@app.post("/api/resolution")
async def suggest_resolution(request: Request) -> dict:
    payload = await request.json()
    return await _service_request("resolution", "POST", "/resolution", payload)


@app.get("/api/triage/filters")
async def triage_filters() -> dict:
    return await _service_request("triage", "GET", "/filters")


@app.post("/api/triage")
async def triage_ticket(request: Request) -> dict:
    payload = await request.json()
    return await _service_request("triage", "POST", "/triage", payload)


@app.get("/api/routing/filters")
async def routing_filters() -> dict:
    return await _service_request("routing", "GET", "/filters")


@app.post("/api/routing")
async def route_ticket(request: Request) -> dict:
    payload = await request.json()
    return await _service_request("routing", "POST", "/routing", payload)


@app.post("/api/escalate")
async def escalate_ticket(payload: EscalationRequest) -> dict:
    l1_payload = {"query": payload.query, "top_k": 4}
    l1_response = await _service_request("resolution", "POST", "/resolution", l1_payload)
    l1_confidence = compute_resolution_confidence(l1_response.get("sources", []))
    l1_decision = decide_l1(l1_confidence, payload.l1_threshold)

    escalation_path: list[dict] = [
        {
            "stage": "L1",
            "resolved": l1_decision.resolved,
            "confidence": round(l1_decision.confidence, 4),
            "reason": l1_decision.reason,
        }
    ]

    if l1_decision.resolved:
        log_escalation(
            {
                "ticket_query": payload.query,
                "final_stage": "L1",
                "path": escalation_path,
            }
        )
        return {
            "final_stage": "L1",
            "resolved": True,
            "l1": l1_response,
            "l2": None,
            "l3": None,
            "escalation_path": escalation_path,
        }

    triage_payload = {
        "ticket_summary": payload.ticket_summary or payload.query,
        "category": payload.category,
        "ci_category": payload.ci_category,
        "ci_subcategory": payload.ci_subcategory,
        "top_k": 5,
    }
    routing_payload = {
        "description": payload.query,
        "category": payload.category,
        "subcategory": payload.subcategory,
        "u_symptom": payload.u_symptom,
        "impact": payload.impact,
        "urgency": payload.urgency,
        "contact_type": payload.contact_type,
        "location": payload.location,
        "top_k": 5,
    }

    l2_triage, l2_routing = await asyncio.gather(
        _service_request("triage", "POST", "/triage", triage_payload),
        _service_request("routing", "POST", "/routing", routing_payload),
    )

    l2_confidence = compute_l2_confidence(
        l2_triage.get("confidence"),
        l2_routing.get("confidence"),
    )
    l2_decision = decide_l2(l2_confidence, payload.l2_threshold)
    escalation_path.append(
        {
            "stage": "L2",
            "resolved": l2_decision.resolved,
            "confidence": round(l2_decision.confidence, 4),
            "reason": l2_decision.reason,
        }
    )

    if l2_decision.resolved:
        log_escalation(
            {
                "ticket_query": payload.query,
                "final_stage": "L2",
                "path": escalation_path,
            }
        )
        return {
            "final_stage": "L2",
            "resolved": True,
            "l1": l1_response,
            "l2": {"triage": l2_triage, "routing": l2_routing},
            "l3": None,
            "escalation_path": escalation_path,
        }

    escalation_path.append(
        {
            "stage": "L3",
            "resolved": False,
            "confidence": 0.0,
            "reason": "Escalate to specialist.",
        }
    )
    log_escalation(
        {
            "ticket_query": payload.query,
            "final_stage": "L3",
            "path": escalation_path,
        }
    )
    return {
        "final_stage": "L3",
        "resolved": False,
        "l1": l1_response,
        "l2": {"triage": l2_triage, "routing": l2_routing},
        "l3": {"assigned_to": "L3 specialist queue"},
        "escalation_path": escalation_path,
    }


@app.post("/api/feedback")
async def submit_feedback(payload: FeedbackRequest) -> dict:
    agent = payload.agent.strip().lower()
    if agent not in {"resolution", "triage", "routing"}:
        raise HTTPException(status_code=400, detail="Feedback agent must be one of: resolution, triage, routing.")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "rating": payload.rating,
        "feedback": payload.feedback.strip(),
        "request": payload.request,
        "response": payload.response,
    }
    FEEDBACK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return {"status": "ok"}


@app.post("/api/knowledge/push")
async def knowledge_push(payload: KnowledgePushRequest) -> dict:
    entry = push_entry(
        content=payload.content,
        source_agent=payload.source_agent,
        metadata=payload.metadata,
    )
    return {"entry": entry.__dict__}


@app.post("/api/knowledge/fetch")
async def knowledge_fetch(payload: KnowledgeFetchRequest) -> dict:
    entries = fetch_entries(
        query=payload.query,
        source_agent=payload.source_agent,
        limit=payload.limit,
    )
    return {"entries": [entry.__dict__ for entry in entries]}


@app.post("/api/knowledge/update")
async def knowledge_update(payload: KnowledgeUpdateRequest) -> dict:
    entry = update_entry(
        entry_id=payload.entry_id,
        content=payload.content,
        metadata=payload.metadata,
    )
    return {"entry": entry.__dict__}
