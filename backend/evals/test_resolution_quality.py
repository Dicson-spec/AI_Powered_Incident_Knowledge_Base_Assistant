import json
import os
from pathlib import Path

import httpx
import pytest
from deepeval import assert_test
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams
from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
GOLDENS_PATH = Path(__file__).resolve().with_name("resolution_goldens.json")

load_dotenv(ROOT_DIR / ".env")

RESOLUTION_EVAL_URL = os.getenv("RESOLUTION_EVAL_URL", "http://127.0.0.1:8000/api/resolution")
JUDGE_MODEL = os.getenv("DEEPEVAL_JUDGE_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))


def _load_goldens() -> list[dict]:
    with GOLDENS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _call_resolution_service(query: str) -> dict:
    timeout = httpx.Timeout(120.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            RESOLUTION_EVAL_URL,
            json={"query": query, "top_k": 4},
        )

    if response.status_code >= 400:
        raise RuntimeError(f"Resolution service call failed ({response.status_code}): {response.text}")
    return response.json()


def _build_retrieval_context(sources: list[dict]) -> list[str]:
    context_chunks: list[str] = []
    for source in sources:
        context_chunks.append(
            "\n".join(
                [
                    f"Incident ID: {source['incident_id']}",
                    f"Category: {source['category']}",
                    f"Incident Details: {source['incident_details']}",
                    f"Description: {source['description']}",
                    f"Solution: {source['solution']}",
                ]
            )
        )
    return context_chunks


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _metrics():
    return [
        AnswerRelevancyMetric(
            threshold=0.7,
            model=JUDGE_MODEL,
            include_reason=True,
        ),
        FaithfulnessMetric(
            threshold=0.7,
            model=JUDGE_MODEL,
            include_reason=True,
        ),
        GEval(
            name="ResolutionCorrectness",
            criteria=(
                "Determine whether the actual output recommends the same core remediation as the expected output "
                "and stays focused on resolving the user's incident."
            ),
            evaluation_params=[
                LLMTestCaseParams.INPUT,
                LLMTestCaseParams.ACTUAL_OUTPUT,
                LLMTestCaseParams.EXPECTED_OUTPUT,
            ],
            threshold=0.6,
            model=JUDGE_MODEL,
        ),
    ]


pytestmark = pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "your_openai_api_key_here",
    reason="Set OPENAI_API_KEY in .env before running DeepEval.",
)


@pytest.mark.parametrize("golden", _load_goldens(), ids=lambda golden: golden["id"])
def test_resolution_quality(golden: dict):
    payload = _call_resolution_service(golden["query"])
    sources = payload.get("sources", [])
    normalized_source_solutions = {
        _normalize_text(source["solution"])
        for source in sources
    }
    expected_solution = _normalize_text(golden["expected_solution_in_sources"])

    assert expected_solution in normalized_source_solutions, (
        "Expected the retrieved sources to contain the benchmark solution. "
        f"Expected: {golden['expected_solution_in_sources']!r}. "
        f"Got: {[source['solution'] for source in sources]!r}"
    )

    test_case = LLMTestCase(
        input=golden["query"],
        actual_output=payload["answer"],
        expected_output=golden["expected_output"],
        retrieval_context=_build_retrieval_context(sources),
    )

    assert_test(test_case=test_case, metrics=_metrics())