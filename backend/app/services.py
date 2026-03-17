import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection
from openai import OpenAI

from .config import settings


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class IncidentRecord:
    media_asset: str
    category: str
    ticket_id: str
    incident_id: str
    incident_details: str
    description: str
    solution: str

    @property
    def document(self) -> str:
        return (
            f"Incident ID: {self.incident_id}\n"
            f"Ticket ID: {self.ticket_id}\n"
            f"Media Asset: {self.media_asset}\n"
            f"Category: {self.category}\n"
            f"Incident Details: {self.incident_details}\n"
            f"Description: {self.description}\n"
            f"Resolution: {self.solution}"
        )

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "incident_id": self.incident_id,
            "ticket_id": self.ticket_id,
            "media_asset": self.media_asset,
            "category": self.category,
            "incident_details": self.incident_details,
            "description": self.description,
            "solution": self.solution,
        }


class IncidentKnowledgeBase:
    def __init__(self) -> None:
        if settings.openai_api_key == "your_openai_api_key_here":
            raise RuntimeError("Set OPENAI_API_KEY in the root .env file before starting the backend.")

        self._client = OpenAI(api_key=settings.openai_api_key)
        settings.chroma_path.mkdir(parents=True, exist_ok=True)
        self._chroma = chromadb.PersistentClient(path=str(settings.chroma_path))
        self._collection = self._chroma.get_or_create_collection(
            name=settings.chroma_collection,
            metadata={"description": "Semantic search over curated incident-resolution examples."},
        )
        self._records: list[IncidentRecord] = []
        self._record_lookup: dict[str, IncidentRecord] = {}
        self._tokenized_records: dict[str, set[str]] = {}
        self._idf: dict[str, float] = {}

    @property
    def collection(self) -> Collection:
        return self._collection

    def load_records(self) -> list[IncidentRecord]:
        rows: list[IncidentRecord] = []
        with settings.dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(
                    IncidentRecord(
                        media_asset=row["Media Asset"].strip(),
                        category=row["Category"].strip(),
                        ticket_id=row["Ticket ID"].strip(),
                        incident_id=row["Incident ID"].strip(),
                        incident_details=row["Incident Details"].strip(),
                        description=row["Description"].strip(),
                        solution=row["Solution"].strip(),
                    )
                )
        return rows

    def ensure_index(self) -> None:
        records = self.load_records()
        self._records = records
        self._record_lookup = {record.incident_id: record for record in records}
        self._build_keyword_index(records)
        existing = self._collection.count()
        if existing == len(records):
            return

        if existing:
            self._chroma.delete_collection(settings.chroma_collection)
            self._collection = self._chroma.get_or_create_collection(
                name=settings.chroma_collection,
                metadata={"description": "Semantic search over curated incident-resolution examples."},
            )

        documents = [record.document for record in records]
        embeddings = self._embed_texts(documents)
        self._collection.add(
            ids=[record.incident_id for record in records],
            documents=documents,
            metadatas=[record.metadata for record in records],
            embeddings=embeddings,
        )

    def retrieve(self, query: str, top_k: int) -> list[dict]:
        if not self._records or self._collection.count() == 0:
            self.ensure_index()

        query_embedding = self._embed_texts([query])[0]
        candidate_count = min(max(top_k * 3, top_k), len(self._records))
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=candidate_count,
            include=["documents", "metadatas", "distances"],
        )

        semantic_scores: dict[str, float] = {}
        for metadata, distance in zip(
            results["metadatas"][0],
            results["distances"][0],
        ):
            semantic_scores[metadata["incident_id"]] = 1.0 / (1.0 + float(distance))

        keyword_scores = self._keyword_search(query=query)
        candidate_ids = set(keyword_scores) | set(semantic_scores)

        ranked_matches: list[dict] = []
        for incident_id in candidate_ids:
            record = self._record_lookup[incident_id]
            semantic_score = semantic_scores.get(incident_id, 0.0)
            keyword_score = keyword_scores.get(incident_id, 0.0)
            hybrid_score = (0.65 * semantic_score) + (0.35 * keyword_score)
            ranked_matches.append(
                {
                    **record.metadata,
                    "similarity": round(hybrid_score, 4),
                    "semantic_score": round(semantic_score, 4),
                    "keyword_score": round(keyword_score, 4),
                }
            )

        ranked_matches.sort(key=lambda match: match["similarity"], reverse=True)
        return ranked_matches[:top_k]

    def answer_query(self, query: str, top_k: int) -> tuple[str, list[dict]]:
        matches = self.retrieve(query=query, top_k=top_k)
        context = "\n\n".join(
            [
                (
                    f"Source {index + 1}\n"
                    f"Incident ID: {match['incident_id']}\n"
                    f"Ticket ID: {match['ticket_id']}\n"
                    f"Media Asset: {match['media_asset']}\n"
                    f"Category: {match['category']}\n"
                    f"Incident Details: {match['incident_details']}\n"
                    f"Description: {match['description']}\n"
                    f"Solution: {match['solution']}"
                )
                for index, match in enumerate(matches)
            ]
        )

        response = self._client.responses.create(
            model=settings.openai_chat_model,
            input=[
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "You are an IT incident response assistant. Answer only from the retrieved "
                                "incident examples. If the evidence is partial, say that clearly, summarize "
                                "the most likely cause, and provide concise remediation steps grounded in "
                                "the sources. Do not invent tools, logs, or resolution steps that are not "
                                "supported by the retrieved incidents."
                            ),
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                f"User question:\n{query}\n\n"
                                f"Retrieved incident examples:\n{context}\n\n"
                                "Write a short answer with:\n"
                                "1. Likely issue\n"
                                "2. What to check\n"
                                "3. Recommended fix\n"
                                "4. Mention the incident IDs used"
                            ),
                        }
                    ],
                },
            ],
        )

        return response.output_text, matches

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(
            model=settings.openai_embedding_model,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _build_keyword_index(self, records: list[IncidentRecord]) -> None:
        tokenized_records: dict[str, set[str]] = {}
        document_frequency: dict[str, int] = {}

        for record in records:
            tokens = self._tokenize(record.document)
            tokenized_records[record.incident_id] = tokens
            for token in tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1

        corpus_size = len(records)
        self._tokenized_records = tokenized_records
        self._idf = {
            token: math.log((1 + corpus_size) / (1 + frequency)) + 1.0
            for token, frequency in document_frequency.items()
        }

    def _keyword_search(self, query: str) -> dict[str, float]:
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return {}

        weighted_query_total = sum(self._idf.get(token, 1.0) for token in query_tokens)
        scores: dict[str, float] = {}
        for incident_id, record_tokens in self._tokenized_records.items():
            overlap = query_tokens & record_tokens
            if not overlap:
                continue

            overlap_weight = sum(self._idf.get(token, 1.0) for token in overlap)
            scores[incident_id] = overlap_weight / weighted_query_total
        return scores

    def _tokenize(self, text: str) -> set[str]:
        return set(TOKEN_PATTERN.findall(text.lower()))
