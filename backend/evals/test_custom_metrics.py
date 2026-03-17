import os
import sys
from pathlib import Path

import httpx
import pytest
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.evals.custom_metrics import build_fix_accuracy_cases
from backend.evals.custom_metrics import compute_resolution_time_metrics
from backend.evals.custom_metrics import evaluate_fix_accuracy


load_dotenv(ROOT_DIR / ".env")

RESOLUTION_EVAL_URL = os.getenv("RESOLUTION_EVAL_URL", "http://127.0.0.1:8000/api/resolution")


def _call_resolution_service(query: str) -> dict:
    timeout = httpx.Timeout(120.0, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                RESOLUTION_EVAL_URL,
                json={"query": query, "top_k": 4},
            )
    except httpx.HTTPError as exc:  # pragma: no cover - depends on local service availability
        pytest.skip(f"Resolution service is not reachable at {RESOLUTION_EVAL_URL}: {exc}")

    if response.status_code >= 400:
        raise RuntimeError(f"Resolution service call failed ({response.status_code}): {response.text}")
    return response.json()


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "your_openai_api_key_here",
    reason="Set OPENAI_API_KEY in .env before running fix-accuracy metrics.",
)
def test_fix_accuracy_metric():
    per_category = int(os.getenv("FIX_ACCURACY_CASES_PER_CATEGORY", "3"))
    cases = build_fix_accuracy_cases(max_cases_per_category=per_category)
    responses = {case.case_id: _call_resolution_service(case.query) for case in cases}
    metrics = evaluate_fix_accuracy(
        cases=cases,
        responses=responses,
        use_deepeval=os.getenv("ENABLE_DEEPEVAL_FIX_ACCURACY", "").lower() in {"1", "true", "yes"},
    )

    deepeval_fragment = ""
    if metrics.deepeval_average_score is not None and metrics.deepeval_pass_rate is not None:
        deepeval_fragment = (
            f", DeepEvalAvg={metrics.deepeval_average_score:.3f}, "
            f"DeepEvalPassRate={metrics.deepeval_pass_rate:.2%}"
        )

    print(
        "Fix accuracy: "
        f"AnswerPassRate={metrics.answer_pass_rate:.2%}, "
        f"RetrievalHitRate={metrics.retrieval_hit_rate:.2%}, "
        f"AvgTokenF1={metrics.average_token_f1:.3f}, "
        f"AvgSeqSim={metrics.average_sequence_similarity:.3f}, "
        f"Cases={metrics.total_cases}"
        f"{deepeval_fragment}"
    )
    assert metrics.answer_pass_rate >= 0.65
    assert metrics.retrieval_hit_rate >= 0.80


def test_resolution_time_prediction_metric():
    metrics = compute_resolution_time_metrics()
    print(
        "Resolution time prediction: "
        f"MAE={metrics.mae_hours:.3f}h, "
        f"MedianError={metrics.median_error_hours:.3f}h, "
        f"P90Error={metrics.p90_error_hours:.3f}h, "
        f"Within8h={metrics.within_8h_rate:.2%}, "
        f"Samples={metrics.sample_count}"
    )
    assert metrics.mae_hours <= 4.25
    assert metrics.within_8h_rate >= 0.90


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    raise SystemExit(pytest.main([__file__]))
