from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .models import TriageRequest, TriageResponse, TriageSourceChunk
from .resolution_time import ResolutionTimePredictor
from .triage import TriageAgent
from .web import apply_validation_handler


triage_agent = TriageAgent()
resolution_time_predictor = ResolutionTimePredictor()


@asynccontextmanager
async def lifespan(_: FastAPI):
    triage_agent.ensure_ready()
    resolution_time_predictor.train()
    yield


app = FastAPI(
    title="Triage Service",
    version="1.0.0",
    lifespan=lifespan,
)

apply_validation_handler(app)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "triage"}


@app.get("/stats")
def stats() -> dict[str, int | str]:
    return {
        "service": "triage",
        "dataset": "ITSM_data.csv",
        "triage_records": len(triage_agent._records),
    }


@app.get("/filters")
def triage_filters() -> dict[str, list[str]]:
    return triage_agent.filter_options


@app.post("/triage", response_model=TriageResponse)
def triage_ticket(payload: TriageRequest) -> TriageResponse:
    try:
        result, sources = triage_agent.classify(
            ticket_summary=payload.ticket_summary.strip(),
            category=payload.category.strip(),
            ci_category=payload.ci_category.strip(),
            ci_subcategory=payload.ci_subcategory.strip(),
            top_k=payload.top_k,
        )
        predicted_minutes = resolution_time_predictor.predict(
            category=payload.category.strip(),
            ci_category=payload.ci_category.strip(),
            ci_subcategory=payload.ci_subcategory.strip(),
        )
        metrics = resolution_time_predictor.metrics
    except Exception as exc:  # pragma: no cover - surface service errors to gateway
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return TriageResponse(
        **result,
        predicted_resolution_time_minutes=round(predicted_minutes, 2),
        resolution_time_mae_minutes=round(metrics.mae_minutes, 2) if metrics else None,
        resolution_time_rmse_minutes=round(metrics.rmse_minutes, 2) if metrics else None,
        resolution_time_train_samples=metrics.train_samples if metrics else None,
        resolution_time_test_samples=metrics.test_samples if metrics else None,
        sources=[TriageSourceChunk(**source) for source in sources],
    )
