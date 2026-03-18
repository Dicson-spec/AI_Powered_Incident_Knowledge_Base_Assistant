# Incident Knowledge Base Assistant

This project now has three support workflows:

- A hybrid RAG incident-answering bot backed by `data/incident_response_dataset_150_rows.xlsx - Incident Data.csv`
- A triage agent for priority classification backed by `data/ITSM_data.csv`
- A routing agent for assignment-group recommendation backed by `data/incident_event_log.csv`

## Architecture

This project now runs as a small microservice architecture:

- `gateway` on port `8000`
- `resolution-service` on port `8001`
- `triage-service` on port `8002`
- `routing-service` on port `8003`

The React frontend talks only to the gateway. The gateway proxies requests to the individual backend services.

## Stack

- Backend: FastAPI
- Gateway proxy: FastAPI + `httpx`
- Vector store: ChromaDB
- Retrieval: hybrid search (semantic + keyword reranking)
- LLM: OpenAI `gpt-4o-mini`
- Embeddings: OpenAI `text-embedding-3-small`
- Frontend: React with Vite

## Project layout

- `backend/app/main.py`: API gateway
- `backend/app/resolution_service.py`: resolution microservice app
- `backend/app/triage_service.py`: triage microservice app
- `backend/app/routing_service.py`: routing microservice app
- `backend/app/services.py`: incident-answering retrieval over the curated 150-row dataset
- `backend/app/triage.py`: priority triage over the ITSM dataset
- `backend/app/routing.py`: assignment-group routing over the incident event log
- `backend/run_microservices.ps1`: helper script to start all backend services
- `frontend/src/App.jsx`: React interface for querying the assistant
- `.env`: runtime configuration with a placeholder OpenAI API key

## Setup

1. Activate the virtual environment:

```powershell
.venv\Scripts\Activate.ps1
```

2. Install backend dependencies:

```powershell
pip install -r backend\requirements.txt
```

3. Create .env file(refer line 242). Add your OpenAI API key to `.env`.

4. Install frontend dependencies:

```powershell
cd frontend
npm install
```

## Run

Start the backend microservices from the project root:

```powershell
.\backend\run_microservices.ps1
```

Or run them manually in separate terminals:

```powershell
.venv\Scripts\python -m uvicorn backend.app.main:app --reload --port 8000
```

```powershell
.venv\Scripts\python -m uvicorn backend.app.resolution_service:app --reload --port 8001
```

```powershell
.venv\Scripts\python -m uvicorn backend.app.triage_service:app --reload --port 8002
```

```powershell
.venv\Scripts\python -m uvicorn backend.app.routing_service:app --reload --port 8003
```

Start the frontend in another terminal:

```powershell
cd frontend
npm run dev
```

Then open the local URL shown in the terminal, usually `http://localhost:5173`.

If Windows has trouble launching the Vite wrapper through `npm`, the scripts are configured to call Vite through `node` directly.

## Latency Check

With all microservices running, you can measure latency across the gateway and each service:

```powershell
.\backend\latency_check.ps1
```

To change the number of runs:

```powershell
$env:LATENCY_RUNS=20
.\backend\latency_check.ps1
```

Optional tuning:

```powershell
$env:LATENCY_TIMEOUT_SEC=180
$env:LATENCY_DELAY_MS=200
.\backend\latency_check.ps1
```

## API

Gateway endpoints:

- `GET /api/health`: gateway health plus downstream service health
- `GET /api/stats`: aggregated service stats
- `POST /api/query`: alias for the resolution workflow
- `POST /api/resolution`: resolution suggestion via the resolution service
- `POST /api/triage`: ticket priority classification via the triage service
- `GET /api/triage/filters`: available `category`, `ci_category`, and `ci_subcategory` values for triage
- `POST /api/routing`: assignment-group routing via the routing service
- `GET /api/routing/filters`: available `category`, `subcategory`, `u_symptom`, `impact`, `urgency`, `contact_type`, and `location` values for routing
- `POST /api/feedback`: store human feedback for resolution/triage/routing responses
- `POST /api/escalate`: multi-tier L1/L2/L3 escalation flow using resolution + triage + routing
- `POST /api/knowledge/push`: store shared agent knowledge entries
- `POST /api/knowledge/fetch`: fetch shared knowledge entries
- `POST /api/knowledge/update`: update a shared knowledge entry

Example request body:

```json
{
  "query": "The application becomes slower over time and then stops responding. What should we check?",
  "top_k": 4
}
```

Example triage request body:

```json
{
  "ticket_summary": "Customer-facing web application is intermittently unavailable and multiple teams are blocked.",
  "category": "incident",
  "ci_category": "application",
  "ci_subcategory": "Web Based Application",
  "top_k": 5
}
```

Example routing request body:

```json
{
  "description": "Optional extra description for routing context.",
  "category": "Category 55",
  "subcategory": "Subcategory 170",
  "u_symptom": "Symptom 72",
  "impact": 2,
  "urgency": 2,
  "contact_type": "Phone",
  "location": "Location 143",
  "top_k": 5
}
```

Example feedback request body:

```json
{
  "agent": "resolution",
  "rating": 4,
  "feedback": "Good steps, but mention log checks.",
  "request": { "query": "Video stream stopped responding.", "top_k": 4 },
  "response": { "answer": "...", "sources": [] }
}
```

Example escalation request body:

```json
{
  "query": "Video stream stopped responding during broadcast.",
  "ticket_summary": "Streaming service halted mid-broadcast.",
  "category": "incident",
  "ci_category": "application",
  "ci_subcategory": "Web Based Application",
  "subcategory": "Subcategory 170",
  "u_symptom": "Symptom 72",
  "impact": "2 - Medium",
  "urgency": "2 - Medium",
  "contact_type": "Phone",
  "location": "Location 143",
  "l1_threshold": 0.7,
  "l2_threshold": 0.6
}
```

Example knowledge push request body:

```json
{
  "content": "Restart streaming service and reset the network interface to resolve stream hangs.",
  "source_agent": "resolution",
  "metadata": { "incident_id": "INC-5091", "tags": ["streaming", "network"] }
}
```

## Guardrails

The backend validates free-text inputs for the resolution, triage, and routing workflows. It rejects:

- empty text
- vague descriptions with too little issue detail
- repetitive spam-like input
- prompt-instruction text such as attempts to override the assistant

## DeepEval

The project includes a DeepEval benchmark for the resolution workflow at `backend/evals/test_resolution_quality.py`.

It evaluates the live resolution API against a small curated benchmark in `backend/evals/resolution_goldens.json` using:

- answer relevancy
- faithfulness to retrieved source incidents
- correctness against the expected remediation

Install the evaluation dependencies:

```powershell
pip install -r backend\requirements-evals.txt
```

Make sure the gateway and resolution service are running, then execute:

```powershell
deepeval test run backend\evals\test_resolution_quality.py
```

If you prefer, you can also run it through pytest:

```powershell
pytest backend\evals\test_resolution_quality.py
```

Optional environment variables:

- `RESOLUTION_EVAL_URL` to point the benchmark at a different resolution endpoint
- `DEEPEVAL_JUDGE_MODEL` to override the judge model used by DeepEval

## Environment Variables (.env)

The `.env` file is gitignored. Use the exact values below to recreate it:

```env
OPENAI_API_KEY=<api key>
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
CHROMA_PATH=backend/chroma_db
CHROMA_COLLECTION=incident-response-bot
BACKEND_PORT=8000
GATEWAY_PORT=8000
RESOLUTION_SERVICE_PORT=8001
TRIAGE_SERVICE_PORT=8002
ROUTING_SERVICE_PORT=8003
GATEWAY_URL=http://127.0.0.1:8000
RESOLUTION_SERVICE_URL=http://127.0.0.1:8001
TRIAGE_SERVICE_URL=http://127.0.0.1:8002
ROUTING_SERVICE_URL=http://127.0.0.1:8003
FRONTEND_API_URL=http://localhost:8000
FRONTEND_ORIGIN=http://localhost:5173
```

## Custom Metrics

There is a dataset-backed custom metrics suite at `backend/evals/test_custom_metrics.py` with reusable helpers in `backend/evals/custom_metrics.py`.

It uses all three CSVs in `data/`:

- `incident_response_dataset_150_rows.xlsx - Incident Data.csv` to build fix-accuracy benchmark cases from known incident solutions.
- `ITSM_data.csv` as the main source of ground-truth handle-time labels.
- `incident_event_log.csv` to add lifecycle-duration priors based on impact, urgency, and priority patterns.

The suite reports:

- Fix accuracy: answer pass rate, retrieval hit rate, token-F1 overlap, and sequence similarity against expected fixes.
- Resolution time prediction: blended historical baseline with MAE, median error, P90 error, and within-8-hours rate.

Run it with:

```powershell
pytest backend\evals\test_custom_metrics.py
```

Optional environment variables:

- `FIX_ACCURACY_CASES_PER_CATEGORY` to change how many dataset-derived fix cases are sampled per category. Default: `3`
- `ENABLE_DEEPEVAL_FIX_ACCURACY=true` to add an optional DeepEval semantic correctness pass for fix accuracy when `deepeval` and `OPENAI_API_KEY` are available
