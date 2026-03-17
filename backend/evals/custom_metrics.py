import csv
import hashlib
import json
import os
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
RESOLUTION_DATASET_PATH = ROOT_DIR / "data" / "incident_response_dataset_150_rows.xlsx - Incident Data.csv"
ITSM_DATASET_PATH = ROOT_DIR / "data" / "ITSM_data.csv"
EVENT_LOG_DATASET_PATH = ROOT_DIR / "data" / "incident_event_log.csv"
GOLDENS_PATH = Path(__file__).resolve().with_name("resolution_goldens.json")

EVENT_LOG_DATETIME_FORMATS = (
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M",
    "%m/%d/%Y %H:%M",
    "%m-%d-%Y %H:%M",
    "%d-%m-%Y %H:%M",
)
ITSM_DATETIME_FORMATS = (
    "%m/%d/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%d/%m/%Y %H:%M",
)
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "should",
    "support",
    "the",
    "to",
    "what",
    "with",
}


@dataclass(slots=True)
class FixAccuracyCase:
    case_id: str
    query: str
    expected_solution: str
    expected_incident_id: str = ""
    category: str = ""
    source: str = ""


@dataclass(slots=True)
class FixAccuracyMetrics:
    total_cases: int
    retrieval_hit_rate: float
    answer_pass_rate: float
    average_token_f1: float
    average_sequence_similarity: float
    deepeval_average_score: float | None = None
    deepeval_pass_rate: float | None = None


@dataclass(slots=True)
class ResolutionTimeMetrics:
    sample_count: int
    mae_hours: float
    median_error_hours: float
    p90_error_hours: float
    within_8h_rate: float


def normalize_text(value: str) -> str:
    return " ".join((value or "").lower().split())


def stable_bucket(value: str) -> int:
    digest = hashlib.md5(value.encode("utf-8")).hexdigest()
    return int(digest[:2], 16)


def parse_itsm_handle_time(value: str) -> float | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.upper() in {"NA", "NS"}:
        return None
    parts = cleaned.split(",")
    if not parts or not all(part.isdigit() for part in parts):
        return None
    whole = parts[0]
    frac = "".join(parts[1:])
    try:
        return float(f"{whole}.{frac}")
    except ValueError:
        return None


def parse_datetime(value: str, formats: tuple[str, ...]) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned or cleaned == "?":
        return None
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    return None


def extract_leading_number(value: str) -> str:
    cleaned = normalize_text(value)
    if not cleaned or cleaned in {"na", "ns", "?"}:
        return ""

    digits: list[str] = []
    for char in cleaned:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    return "".join(digits)


def content_tokens(value: str) -> set[str]:
    tokens = []
    for raw_token in normalize_text(value).replace("/", " ").replace("-", " ").split():
        token = "".join(char for char in raw_token if char.isalnum())
        if len(token) <= 1 or token in STOP_WORDS:
            continue
        tokens.append(token)
    return set(tokens)


def build_fix_accuracy_cases(max_cases_per_category: int = 3) -> list[FixAccuracyCase]:
    cases: list[FixAccuracyCase] = []

    if GOLDENS_PATH.exists():
        with GOLDENS_PATH.open("r", encoding="utf-8") as handle:
            goldens = json.load(handle)
        for golden in goldens:
            cases.append(
                FixAccuracyCase(
                    case_id=golden["id"],
                    query=golden["query"],
                    expected_solution=golden["expected_output"],
                    source="curated-goldens",
                )
            )

    rows_by_category: dict[str, list[dict]] = defaultdict(list)
    with RESOLUTION_DATASET_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            category = (row.get("Category") or "").strip()
            rows_by_category[category].append(row)

    for category, rows in rows_by_category.items():
        ranked_rows = sorted(
            rows,
            key=lambda row: (
                stable_bucket((row.get("Incident ID") or "").strip() or (row.get("Ticket ID") or "").strip()),
                (row.get("Incident ID") or "").strip(),
            ),
        )
        for row in ranked_rows[:max_cases_per_category]:
            incident_id = (row.get("Incident ID") or "").strip()
            incident_details = (row.get("Incident Details") or "").strip()
            description = (row.get("Description") or "").strip()
            query = (
                f"We have a {category.lower()} incident. "
                f"Issue: {incident_details}. "
                f"Symptoms: {description}. "
                "What fix should support recommend?"
            )
            cases.append(
                FixAccuracyCase(
                    case_id=f"dataset-{incident_id.lower()}",
                    query=query,
                    expected_solution=(row.get("Solution") or "").strip(),
                    expected_incident_id=incident_id,
                    category=category,
                    source="resolution-dataset",
                )
            )

    deduped: dict[str, FixAccuracyCase] = {}
    for case in cases:
        deduped.setdefault(case.case_id, case)
    return list(deduped.values())


def evaluate_fix_accuracy(
    cases: list[FixAccuracyCase],
    responses: dict[str, dict],
    use_deepeval: bool = False,
    deepeval_threshold: float = 0.6,
) -> FixAccuracyMetrics:
    retrieval_hits = 0
    answer_hits = 0
    token_f1_scores: list[float] = []
    sequence_scores: list[float] = []
    deepeval_scores: list[float] = []
    deepeval_hits = 0

    deepeval_enabled = use_deepeval and os.getenv("OPENAI_API_KEY") not in {None, "", "your_openai_api_key_here"}
    g_eval = None
    llm_test_case = None
    llm_test_case_params = None
    if deepeval_enabled:
        try:
            from deepeval.metrics import GEval
            from deepeval.test_case import LLMTestCase, LLMTestCaseParams

            g_eval = GEval
            llm_test_case = LLMTestCase
            llm_test_case_params = LLMTestCaseParams
        except Exception:
            deepeval_enabled = False

    for case in cases:
        payload = responses[case.case_id]
        answer = payload.get("answer", "")
        sources = payload.get("sources", [])
        expected_solution = case.expected_solution
        expected_solution_normalized = normalize_text(expected_solution)

        retrieval_hit = False
        for source in sources:
            source_solution = normalize_text(source.get("solution", ""))
            source_incident_id = (source.get("incident_id") or "").strip()
            if source_solution == expected_solution_normalized:
                retrieval_hit = True
                break
            if case.expected_incident_id and source_incident_id == case.expected_incident_id:
                retrieval_hit = True
                break
        if retrieval_hit:
            retrieval_hits += 1

        expected_tokens = content_tokens(expected_solution)
        answer_tokens = content_tokens(answer)
        overlap = expected_tokens & answer_tokens
        if expected_tokens and answer_tokens:
            precision = len(overlap) / len(answer_tokens)
            recall = len(overlap) / len(expected_tokens)
            token_f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        else:
            token_f1 = 0.0

        sequence_similarity = SequenceMatcher(
            None,
            expected_solution_normalized,
            normalize_text(answer),
        ).ratio()
        token_f1_scores.append(token_f1)
        sequence_scores.append(sequence_similarity)

        lexical_pass = (
            expected_solution_normalized in normalize_text(answer)
            or token_f1 >= 0.45
            or sequence_similarity >= 0.55
        )

        semantic_pass = False
        if deepeval_enabled and g_eval and llm_test_case and llm_test_case_params:
            metric = g_eval(
                name="FixAccuracy",
                criteria=(
                    "Decide whether the actual output recommends the same core remediation as the expected "
                    "solution, even if the wording differs."
                ),
                evaluation_params=[
                    llm_test_case_params.INPUT,
                    llm_test_case_params.ACTUAL_OUTPUT,
                    llm_test_case_params.EXPECTED_OUTPUT,
                ],
                threshold=deepeval_threshold,
                model=os.getenv("DEEPEVAL_JUDGE_MODEL", os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")),
            )
            score = metric.measure(
                llm_test_case(
                    input=case.query,
                    actual_output=answer,
                    expected_output=expected_solution,
                ),
                _show_indicator=False,
            )
            deepeval_scores.append(score)
            semantic_pass = score >= deepeval_threshold
            if semantic_pass:
                deepeval_hits += 1

        if lexical_pass or semantic_pass:
            answer_hits += 1

    total_cases = max(len(cases), 1)
    deepeval_average_score = statistics.fmean(deepeval_scores) if deepeval_scores else None
    deepeval_pass_rate = deepeval_hits / len(deepeval_scores) if deepeval_scores else None

    return FixAccuracyMetrics(
        total_cases=len(cases),
        retrieval_hit_rate=retrieval_hits / total_cases,
        answer_pass_rate=answer_hits / total_cases,
        average_token_f1=statistics.fmean(token_f1_scores) if token_f1_scores else 0.0,
        average_sequence_similarity=statistics.fmean(sequence_scores) if sequence_scores else 0.0,
        deepeval_average_score=deepeval_average_score,
        deepeval_pass_rate=deepeval_pass_rate,
    )


def load_itsm_records() -> list[dict]:
    rows: list[dict] = []
    with ITSM_DATASET_PATH.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            handle_time = parse_itsm_handle_time(row.get("Handle_Time_hrs", ""))
            if handle_time is None:
                continue

            rows.append(
                {
                    "incident_id": (row.get("Incident_ID") or "").strip(),
                    "category": normalize_text(row.get("Category", "")),
                    "ci_cat": normalize_text(row.get("CI_Cat", "")),
                    "ci_subcat": normalize_text(row.get("CI_Subcat", "")),
                    "impact": extract_leading_number(row.get("Impact", "")),
                    "urgency": extract_leading_number(row.get("Urgency", "")),
                    "priority": extract_leading_number(row.get("Priority", "")),
                    "closure_code": normalize_text(row.get("Closure_Code", "")),
                    "open_time": parse_datetime(row.get("Open_Time", ""), ITSM_DATETIME_FORMATS),
                    "resolved_time": parse_datetime(row.get("Resolved_Time", ""), ITSM_DATETIME_FORMATS),
                    "handle_time": handle_time,
                }
            )
    return rows


def load_event_log_priors() -> dict[str, dict[tuple[str, ...], float]]:
    aggregated: dict[str, dict] = {}
    with EVENT_LOG_DATASET_PATH.open("r", encoding="utf-8", errors="ignore") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            incident_number = (row.get("number") or "").strip()
            if not incident_number:
                continue

            item = aggregated.setdefault(
                incident_number,
                {
                    "opened_at": None,
                    "resolved_at": None,
                    "impact": extract_leading_number(row.get("impact", "")),
                    "urgency": extract_leading_number(row.get("urgency", "")),
                    "priority": extract_leading_number(row.get("priority", "")),
                    "category": normalize_text(row.get("category", "")),
                },
            )

            opened_at = parse_datetime(row.get("opened_at", ""), EVENT_LOG_DATETIME_FORMATS)
            resolved_at = parse_datetime(row.get("resolved_at", ""), EVENT_LOG_DATETIME_FORMATS)
            if opened_at and (item["opened_at"] is None or opened_at < item["opened_at"]):
                item["opened_at"] = opened_at
            if resolved_at and (item["resolved_at"] is None or resolved_at > item["resolved_at"]):
                item["resolved_at"] = resolved_at

    duration_rows: list[dict] = []
    for item in aggregated.values():
        opened_at = item["opened_at"]
        resolved_at = item["resolved_at"]
        if not opened_at or not resolved_at or resolved_at < opened_at:
            continue

        duration_rows.append(
            {
                "impact": item["impact"],
                "urgency": item["urgency"],
                "priority": item["priority"],
                "category": item["category"],
                "resolution_hours": (resolved_at - opened_at).total_seconds() / 3600,
            }
        )

    priors: dict[str, dict[tuple[str, ...], float]] = {}
    field_sets = {
        "impact_urgency_priority": ("impact", "urgency", "priority"),
        "impact_urgency": ("impact", "urgency"),
        "impact_priority": ("impact", "priority"),
        "urgency_priority": ("urgency", "priority"),
        "priority": ("priority",),
    }
    for name, fields in field_sets.items():
        grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
        for row in duration_rows:
            grouped[tuple(row[field] for field in fields)].append(row["resolution_hours"])
        priors[name] = {key: statistics.median(values) for key, values in grouped.items()}
    return priors


def compute_resolution_time_metrics() -> ResolutionTimeMetrics:
    rows = load_itsm_records()
    train: list[dict] = []
    test: list[dict] = []
    for row in rows:
        incident_id = row["incident_id"]
        if not incident_id or stable_bucket(incident_id) < 204:
            train.append(row)
        else:
            test.append(row)

    if not train or not test:
        raise RuntimeError("Not enough ITSM rows to compute a train/test split.")

    def build_priors(records: list[dict], keys: dict[str, tuple[str, ...]]) -> dict[str, dict[tuple[str, ...], float]]:
        priors: dict[str, dict[tuple[str, ...], float]] = {}
        for name, fields in keys.items():
            grouped: dict[tuple[str, ...], list[float]] = defaultdict(list)
            for row in records:
                grouped[tuple(row[field] for field in fields)].append(row["handle_time"])
            priors[name] = {key: statistics.median(values) for key, values in grouped.items()}
        return priors

    itsm_priors = build_priors(
        train,
        {
            "rich_profile": ("category", "ci_cat", "ci_subcat", "impact", "urgency", "priority", "closure_code"),
            "service_profile": ("category", "ci_cat", "ci_subcat", "impact", "urgency", "priority"),
            "support_profile": ("ci_cat", "ci_subcat", "impact", "urgency"),
            "ci_profile": ("ci_cat", "ci_subcat"),
            "category_profile": ("category", "ci_cat"),
        },
    )
    event_priors = load_event_log_priors()
    global_median = statistics.median(row["handle_time"] for row in train)

    def lookup(prior_map: dict[tuple[str, ...], float], row: dict, fields: tuple[str, ...]) -> float | None:
        return prior_map.get(tuple(row[field] for field in fields))

    def predict(row: dict) -> float:
        components: list[tuple[float, float]] = []

        itsm_components = (
            ("rich_profile", ("category", "ci_cat", "ci_subcat", "impact", "urgency", "priority", "closure_code"), 0.50),
            ("service_profile", ("category", "ci_cat", "ci_subcat", "impact", "urgency", "priority"), 0.35),
            ("support_profile", ("ci_cat", "ci_subcat", "impact", "urgency"), 0.20),
            ("ci_profile", ("ci_cat", "ci_subcat"), 0.10),
            ("category_profile", ("category", "ci_cat"), 0.10),
        )
        for name, fields, weight in itsm_components:
            value = lookup(itsm_priors[name], row, fields)
            if value is not None:
                components.append((weight, value))

        event_components = (
            ("impact_urgency_priority", ("impact", "urgency", "priority"), 0.18),
            ("impact_urgency", ("impact", "urgency"), 0.14),
            ("impact_priority", ("impact", "priority"), 0.10),
            ("urgency_priority", ("urgency", "priority"), 0.10),
            ("priority", ("priority",), 0.08),
        )
        for name, fields, weight in event_components:
            value = lookup(event_priors[name], row, fields)
            if value is not None:
                components.append((weight, value))

        if not components:
            return global_median

        total_weight = sum(weight for weight, _ in components)
        return sum(weight * value for weight, value in components) / total_weight

    absolute_errors: list[float] = []
    for row in test:
        prediction = predict(row)
        absolute_errors.append(abs(prediction - row["handle_time"]))

    sorted_errors = sorted(absolute_errors)
    p90_index = min(max(int(0.9 * len(sorted_errors)) - 1, 0), len(sorted_errors) - 1)
    within_8h_count = sum(1 for error in absolute_errors if error <= 8.0)

    return ResolutionTimeMetrics(
        sample_count=len(test),
        mae_hours=statistics.fmean(absolute_errors),
        median_error_hours=statistics.median(absolute_errors),
        p90_error_hours=sorted_errors[p90_index],
        within_8h_rate=within_8h_count / len(test),
    )
