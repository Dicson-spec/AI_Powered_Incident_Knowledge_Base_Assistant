import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(slots=True)
class TokenStats:
    before_tokens: int
    after_tokens: int


class EmbeddingCache:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._store: dict[str, list[float]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        if not self._path.exists():
            self._loaded = True
            return
        with self._path.open("r", encoding="utf-8") as handle:
            self._store = json.load(handle)
        self._loaded = True

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as handle:
            json.dump(self._store, handle)

    def get_or_compute(
        self,
        text: str,
        embed_fn: Callable[[str], list[float]],
    ) -> list[float]:
        self._load()
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if key in self._store:
            return self._store[key]
        vector = embed_fn(text)
        self._store[key] = vector
        self._save()
        return vector


def estimate_tokens(text: str) -> int:
    tokens = TOKEN_PATTERN.findall(text)
    if not tokens:
        return 0
    # Rough heuristic: 1 token ~= 0.75 words
    return math.ceil(len(tokens) / 0.75)


def summarize_incident(incident: dict, max_chars: int = 360) -> str:
    fields = []
    for key in ("incident_id", "ticket_id", "category", "ci_cat", "ci_subcat", "incident_details", "description", "solution"):
        value = (incident.get(key) or "").strip()
        if value:
            fields.append(f"{key.replace('_', ' ').title()}: {value}")

    summary = " | ".join(fields)
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def summarize_incidents(incidents: list[dict], max_chars: int = 360) -> list[str]:
    return [summarize_incident(incident, max_chars=max_chars) for incident in incidents]


def rank_top_k_by_relevance(
    query: str,
    incidents: list[dict],
    top_k: int = 4,
    summary_max_chars: int = 360,
) -> list[dict]:
    summaries = summarize_incidents(incidents, max_chars=summary_max_chars)
    corpus = [query] + summaries
    vectorizer = TfidfVectorizer(stop_words="english")
    matrix = vectorizer.fit_transform(corpus)
    query_vector = matrix[0:1]
    summary_vectors = matrix[1:]
    similarities = cosine_similarity(query_vector, summary_vectors).flatten()

    ranked = sorted(
        zip(incidents, summaries, similarities),
        key=lambda item: item[2],
        reverse=True,
    )
    selected = []
    for incident, summary, score in ranked[:top_k]:
        enriched = dict(incident)
        enriched["summary"] = summary
        enriched["relevance"] = float(score)
        selected.append(enriched)
    return selected


def batch_incidents(
    query: str,
    incidents: list[dict],
    max_batch_tokens: int = 1500,
) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current_batch: list[dict] = []
    current_tokens = estimate_tokens(query)

    for incident in incidents:
        summary = incident.get("summary") or summarize_incident(incident)
        incident_tokens = estimate_tokens(summary)
        if current_batch and current_tokens + incident_tokens > max_batch_tokens:
            batches.append(current_batch)
            current_batch = []
            current_tokens = estimate_tokens(query)
        current_batch.append({**incident, "summary": summary})
        current_tokens += incident_tokens

    if current_batch:
        batches.append(current_batch)
    return batches


def build_prompt(query: str, batch: list[dict]) -> str:
    incident_lines = "\n".join(
        f"- {incident.get('summary') or summarize_incident(incident)}" for incident in batch
    )
    return (
        "You are an IT incident response assistant. "
        "Use only the incident summaries to answer.\n\n"
        f"User query:\n{query}\n\n"
        "Incident summaries:\n"
        f"{incident_lines}\n\n"
        "Answer with:\n"
        "1. Likely issue\n"
        "2. What to check\n"
        "3. Recommended fix\n"
        "4. Mention incident IDs used"
    )


def optimize_prompts(
    query: str,
    incidents: list[dict],
    top_k: int = 4,
    summary_max_chars: int = 360,
    max_batch_tokens: int = 1500,
) -> tuple[list[str], TokenStats]:
    raw_context = "\n".join(json.dumps(incident, ensure_ascii=True) for incident in incidents)
    before_tokens = estimate_tokens(query + "\n" + raw_context)

    top_incidents = rank_top_k_by_relevance(
        query=query,
        incidents=incidents,
        top_k=top_k,
        summary_max_chars=summary_max_chars,
    )
    batches = batch_incidents(query=query, incidents=top_incidents, max_batch_tokens=max_batch_tokens)
    prompts = [build_prompt(query, batch) for batch in batches]

    after_tokens = sum(estimate_tokens(prompt) for prompt in prompts)
    return prompts, TokenStats(before_tokens=before_tokens, after_tokens=after_tokens)


def cache_embeddings(
    texts: Iterable[str],
    embed_fn: Callable[[str], list[float]],
    cache_path: Path,
) -> list[list[float]]:
    cache = EmbeddingCache(cache_path)
    return [cache.get_or_compute(text, embed_fn) for text in texts]
