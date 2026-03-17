import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
KNOWLEDGE_STORE_PATH = ROOT_DIR / "shared_knowledge_store.jsonl"


@dataclass(slots=True)
class KnowledgeEntry:
    entry_id: str
    content: str
    source_agent: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_entries() -> list[KnowledgeEntry]:
    if not KNOWLEDGE_STORE_PATH.exists():
        return []
    entries: list[KnowledgeEntry] = []
    with KNOWLEDGE_STORE_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            entries.append(
                KnowledgeEntry(
                    entry_id=payload["entry_id"],
                    content=payload["content"],
                    source_agent=payload["source_agent"],
                    created_at=payload["created_at"],
                    updated_at=payload["updated_at"],
                    metadata=payload.get("metadata", {}),
                )
            )
    return entries


def _write_entries(entries: list[KnowledgeEntry]) -> None:
    KNOWLEDGE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with KNOWLEDGE_STORE_PATH.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry.__dict__) + "\n")


def push_entry(content: str, source_agent: str, metadata: dict[str, Any] | None = None) -> KnowledgeEntry:
    now = _utc_now()
    entry = KnowledgeEntry(
        entry_id=str(uuid.uuid4()),
        content=content.strip(),
        source_agent=source_agent.strip().lower(),
        created_at=now,
        updated_at=now,
        metadata=metadata or {},
    )
    KNOWLEDGE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with KNOWLEDGE_STORE_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry.__dict__) + "\n")
    return entry


def fetch_entries(
    query: str = "",
    source_agent: str = "",
    limit: int = 50,
) -> list[KnowledgeEntry]:
    query_lower = query.strip().lower()
    agent_lower = source_agent.strip().lower()
    entries = _load_entries()
    if query_lower:
        entries = [entry for entry in entries if query_lower in entry.content.lower()]
    if agent_lower:
        entries = [entry for entry in entries if entry.source_agent == agent_lower]
    entries.sort(key=lambda entry: entry.updated_at, reverse=True)
    return entries[:limit]


def update_entry(
    entry_id: str,
    content: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> KnowledgeEntry:
    entries = _load_entries()
    for index, entry in enumerate(entries):
        if entry.entry_id == entry_id:
            updated_content = content.strip() if content is not None else entry.content
            updated_metadata = metadata if metadata is not None else entry.metadata
            entries[index] = KnowledgeEntry(
                entry_id=entry.entry_id,
                content=updated_content,
                source_agent=entry.source_agent,
                created_at=entry.created_at,
                updated_at=_utc_now(),
                metadata=updated_metadata,
            )
            _write_entries(entries)
            return entries[index]
    raise ValueError(f"Knowledge entry '{entry_id}' was not found.")
