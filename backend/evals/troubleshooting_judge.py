import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI


ROOT_DIR = Path(__file__).resolve().parents[2]
GOLDENS_PATH = Path(__file__).resolve().with_name("resolution_goldens.json")
REPORT_PATH = ROOT_DIR / "troubleshooting_judge_report.txt"

load_dotenv(ROOT_DIR / ".env")

RESOLUTION_EVAL_URL = os.getenv("RESOLUTION_EVAL_URL", "http://127.0.0.1:8000/api/resolution")
JUDGE_MODEL = os.getenv("TROUBLESHOOTING_JUDGE_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))


@dataclass(slots=True)
class JudgeResult:
    score: float
    valid: bool
    feedback: str


def _call_resolution_service(query: str) -> dict:
    timeout = httpx.Timeout(120.0, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                RESOLUTION_EVAL_URL,
                json={"query": query, "top_k": 4},
            )
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"Resolution service is not reachable at {RESOLUTION_EVAL_URL}: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise RuntimeError(f"Resolution service call failed ({response.status_code}): {response.text}")
    return response.json()


def _load_goldens() -> list[dict]:
    with GOLDENS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _judge_steps(
    client: OpenAI,
    incident_description: str,
    generated_steps: str,
    expected_solution: str,
) -> JudgeResult:
    system_prompt = (
        "You are an ITSM troubleshooting evaluator. "
        "Score the generated troubleshooting steps for correctness, relevance, and completeness. "
        "Return ONLY valid JSON with keys: score (0-1), valid (true/false), feedback (short)."
    )
    user_prompt = (
        "Incident description:\n"
        f"{incident_description}\n\n"
        "Expected resolution (ground truth):\n"
        f"{expected_solution}\n\n"
        "Generated troubleshooting steps:\n"
        f"{generated_steps}\n\n"
        "Evaluate and return JSON."
    )

    response = client.responses.create(
        model=JUDGE_MODEL,
        input=[
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
            {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
        ],
    )
    raw = response.output_text.strip()
    payload = _parse_json_payload(raw)
    if payload is None:
        payload = {"score": 0.0, "valid": False, "feedback": f"Non-JSON response from judge: {raw[:200]}"}

    score = float(payload.get("score", 0.0))
    valid = bool(payload.get("valid", False))
    feedback = str(payload.get("feedback", "")).strip()
    return JudgeResult(score=score, valid=valid, feedback=feedback)


def _parse_json_payload(text: str) -> dict | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json", "", 1).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    snippet = cleaned[start : end + 1]
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        return None


def run() -> int:
    if not os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") == "your_openai_api_key_here":
        raise RuntimeError("Set OPENAI_API_KEY in the root .env file before running the troubleshooting judge.")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    goldens = _load_goldens()

    results: list[dict] = []
    total_score = 0.0
    valid_count = 0

    for golden in goldens:
        try:
            payload = _call_resolution_service(golden["query"])
        except RuntimeError as exc:
            print(str(exc))
            print("Start the backend services (gateway + resolution) and retry.")
            return 1
        answer = payload.get("answer", "")

        judge = _judge_steps(
            client=client,
            incident_description=golden["query"],
            generated_steps=answer,
            expected_solution=golden["expected_output"],
        )

        total_score += judge.score
        valid_count += 1 if judge.valid else 0
        results.append(
            {
                "id": golden["id"],
                "query": golden["query"],
                "expected_output": golden["expected_output"],
                "answer": answer,
                "score": round(judge.score, 3),
                "valid": judge.valid,
                "feedback": judge.feedback,
            }
        )

    average_score = total_score / max(len(results), 1)
    valid_rate = valid_count / max(len(results), 1)

    report_lines = [
        "Troubleshooting Step Validation Report",
        f"Generated at: {datetime.utcnow().isoformat()}Z",
        f"Judge model: {JUDGE_MODEL}",
        f"Resolution endpoint: {RESOLUTION_EVAL_URL}",
        f"Cases: {len(results)}",
        f"Average score: {average_score:.3f}",
        f"Valid rate: {valid_rate:.2%}",
        "",
    ]

    for item in results:
        report_lines.extend(
            [
                f"Case: {item['id']}",
                f"Score: {item['score']}",
                f"Valid: {item['valid']}",
                f"Feedback: {item['feedback']}",
                "Incident description:",
                item["query"],
                "Expected resolution:",
                item["expected_output"],
                "Generated steps:",
                item["answer"],
                "-" * 60,
            ]
        )

    REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Saved report to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
