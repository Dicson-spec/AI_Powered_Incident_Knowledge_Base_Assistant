from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .models import RoutingRequest, RoutingResponse, RoutingSourceChunk
from .routing import RoutingAgent
from .web import apply_validation_handler


routing_agent = RoutingAgent()


@asynccontextmanager
async def lifespan(_: FastAPI):
    routing_agent.ensure_ready()
    yield


app = FastAPI(
    title="Routing Service",
    version="1.0.0",
    lifespan=lifespan,
)

apply_validation_handler(app)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "routing"}


@app.get("/stats")
def stats() -> dict[str, int | str]:
    return {
        "service": "routing",
        "dataset": "incident_event_log.csv",
        "routing_records": len(routing_agent._records),
    }


@app.get("/filters")
def routing_filters() -> dict[str, list[str]]:
    return routing_agent.filter_options


@app.post("/routing", response_model=RoutingResponse)
def route_ticket(payload: RoutingRequest) -> RoutingResponse:
    try:
        result, sources = routing_agent.route(
            description=payload.description.strip(),
            category=payload.category.strip(),
            subcategory=payload.subcategory.strip(),
            u_symptom=payload.u_symptom.strip(),
            impact=payload.impact.strip(),
            urgency=payload.urgency.strip(),
            contact_type=payload.contact_type.strip(),
            location=payload.location.strip(),
            top_k=payload.top_k,
        )
    except Exception as exc:  # pragma: no cover - surface service errors to gateway
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RoutingResponse(
        **result,
        sources=[RoutingSourceChunk(**source) for source in sources],
    )
