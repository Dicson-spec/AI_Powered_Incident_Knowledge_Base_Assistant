from fastapi import FastAPI, HTTPException

from .models import QueryRequest, QueryResponse, SourceChunk
from .services import IncidentKnowledgeBase
from .web import apply_validation_handler


kb = IncidentKnowledgeBase()

app = FastAPI(
    title="Resolution Service",
    version="1.0.0",
)

apply_validation_handler(app)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "resolution"}


@app.get("/stats")
def stats() -> dict[str, int | str]:
    return {
        "service": "resolution",
        "dataset": "incident_response_dataset_150_rows.xlsx - Incident Data.csv",
        "resolution_records": len(kb.load_records()),
        "indexed_incidents": kb.collection.count(),
    }


@app.post("/resolution", response_model=QueryResponse)
def suggest_resolution(payload: QueryRequest) -> QueryResponse:
    try:
        answer, sources = kb.answer_query(query=payload.query.strip(), top_k=payload.top_k)
    except Exception as exc:  # pragma: no cover - surface service errors to gateway
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return QueryResponse(
        answer=answer,
        sources=[SourceChunk(**source) for source in sources],
    )
