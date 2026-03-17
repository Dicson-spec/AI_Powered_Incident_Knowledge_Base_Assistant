import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from .config import settings


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class RoutingRecord:
    record_key: str
    incident_id: str
    category: str
    subcategory: str
    symptom: str
    cmdb_ci: str
    impact: str
    urgency: str
    assignment_group: str
    contact_type: str
    location: str
    support_count: int

    @property
    def document(self) -> str:
        return (
            f"Category: {self.category}\n"
            f"Subcategory: {self.subcategory}\n"
            f"Symptom: {self.symptom}\n"
            f"Configuration Item: {self.cmdb_ci}\n"
            f"Impact: {self.impact}\n"
            f"Urgency: {self.urgency}\n"
            f"Assignment Group: {self.assignment_group}\n"
            f"Contact Type: {self.contact_type}\n"
            f"Location: {self.location}\n"
            f"Support Count: {self.support_count}"
        )

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "incident_id": self.incident_id,
            "category": self.category,
            "subcategory": self.subcategory,
            "symptom": self.symptom,
            "impact": self.impact,
            "urgency": self.urgency,
            "assignment_group": self.assignment_group,
            "contact_type": self.contact_type,
            "location": self.location,
            "support_count": str(self.support_count),
        }


class RoutingAgent:
    def __init__(self) -> None:
        self._records: list[RoutingRecord] = []
        self._tokenized_records: dict[str, set[str]] = {}
        self._group_distribution: Counter[str] = Counter()
        self._filter_options: dict[str, list[str]] = {
            "category": [],
            "subcategory": [],
            "u_symptom": [],
            "impact": [],
            "urgency": [],
            "contact_type": [],
            "location": [],
        }

    def ensure_ready(self) -> None:
        records = self.load_records()
        self._records = records
        self._group_distribution = Counter(record.assignment_group for record in records)
        self._filter_options = {
            "category": sorted({record.category for record in records if record.category}),
            "subcategory": sorted({record.subcategory for record in records if record.subcategory}),
            "u_symptom": sorted({record.symptom for record in records if record.symptom}),
            "impact": sorted({record.impact for record in records if record.impact}),
            "urgency": sorted({record.urgency for record in records if record.urgency}),
            "contact_type": sorted({record.contact_type for record in records if record.contact_type}),
            "location": sorted({record.location for record in records if record.location}),
        }
        self._build_keyword_index(records)

    @property
    def filter_options(self) -> dict[str, list[str]]:
        return self._filter_options

    def load_records(self) -> list[RoutingRecord]:
        combos: dict[tuple[str, str, str, str], dict] = {}
        with settings.event_log_dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                incident_id = row["number"].strip()
                assignment_group = row["assignment_group"].strip()
                if not incident_id or assignment_group in {"", "?"}:
                    continue

                parsed_updated_at = self._parse_datetime(row["sys_updated_at"].strip())
                parsed_opened_at = self._parse_datetime(row["opened_at"].strip())
                category = self._clean_value(row["category"])
                subcategory = self._clean_value(row["subcategory"])
                symptom = self._clean_value(row["u_symptom"])
                cmdb_ci = self._clean_value(row["cmdb_ci"])
                impact = self._clean_value(row["impact"])
                urgency = self._clean_value(row["urgency"])
                contact_type = self._clean_value(row["contact_type"])
                location = self._clean_value(row["location"])
                combo_key = (category, subcategory, symptom, assignment_group)
                combo = combos.setdefault(
                    combo_key,
                    {
                        "incident_id": incident_id,
                        "latest_ts": parsed_updated_at or parsed_opened_at or datetime.min,
                        "category": category,
                        "subcategory": subcategory,
                        "symptom": symptom,
                        "cmdb_ci": cmdb_ci,
                        "impact": impact,
                        "urgency": urgency,
                        "contact_type": contact_type,
                        "location": location,
                        "support_count": 0,
                    },
                )

                combo["support_count"] += 1
                candidate_ts = parsed_updated_at or parsed_opened_at or datetime.min
                if candidate_ts >= combo["latest_ts"]:
                    combo["incident_id"] = incident_id
                    combo["latest_ts"] = candidate_ts
                    combo["cmdb_ci"] = cmdb_ci or combo["cmdb_ci"]
                    combo["impact"] = impact or combo["impact"]
                    combo["urgency"] = urgency or combo["urgency"]
                    combo["contact_type"] = contact_type or combo["contact_type"]
                    combo["location"] = location or combo["location"]

        records: list[RoutingRecord] = []
        for (category, subcategory, symptom, assignment_group), data in combos.items():
            record_key = " | ".join([category, subcategory, symptom, assignment_group])
            records.append(
                RoutingRecord(
                    record_key=record_key,
                    incident_id=data["incident_id"],
                    category=category,
                    subcategory=subcategory,
                    symptom=symptom,
                    cmdb_ci=data["cmdb_ci"],
                    impact=data["impact"],
                    urgency=data["urgency"],
                    assignment_group=assignment_group,
                    contact_type=data["contact_type"],
                    location=data["location"],
                    support_count=data["support_count"],
                )
            )
        return records

    def route(
        self,
        description: str,
        category: str,
        subcategory: str,
        u_symptom: str,
        impact: str,
        urgency: str,
        contact_type: str,
        location: str,
        top_k: int,
    ) -> tuple[dict, list[dict]]:
        if not self._records:
            self.ensure_ready()

        query_text = (
            f"Description: {description}\n"
            f"Category: {category}\n"
            f"Subcategory: {subcategory}\n"
            f"Symptom: {u_symptom}\n"
            f"Impact: {impact}\n"
            f"Urgency: {urgency}\n"
            f"Contact Type: {contact_type}\n"
            f"Location: {location}"
        )
        query_tokens = self._tokenize(query_text)

        scored_matches: list[dict] = []
        for record in self._records:
            keyword_score = self._keyword_score(query_tokens, self._tokenized_records[record.record_key])
            field_score = self._field_score(
                record=record,
                category=category,
                subcategory=subcategory,
                u_symptom=u_symptom,
                impact=impact,
                urgency=urgency,
                contact_type=contact_type,
                location=location,
            )
            combined_score = keyword_score + field_score
            if combined_score <= 0:
                continue
            scored_matches.append(
                {
                    **record.metadata,
                    "similarity": round(combined_score, 4),
                    "keyword_score": round(keyword_score, 4),
                    "field_score": round(field_score, 4),
                }
            )

        scored_matches.sort(key=lambda item: item["similarity"], reverse=True)
        top_matches = scored_matches[:top_k]
        if not top_matches:
            raise RuntimeError("No similar routing examples were found. Add more issue details or metadata.")

        group_scores: defaultdict[str, float] = defaultdict(float)
        total_score = 0.0
        for match in top_matches:
            weight = max(match["similarity"], 0.01)
            total_score += weight
            group_scores[match["assignment_group"]] += weight

        recommended_group = max(group_scores.items(), key=lambda item: item[1])[0]
        confidence = group_scores[recommended_group] / total_score if total_score else 0.0
        related_incidents = ", ".join(match["incident_id"] for match in top_matches)
        rationale = (
            f"Recommended assignment group: {recommended_group}. "
            f"This suggestion is based on the closest historical incidents matching the category, "
            f"subcategory, symptom, impact, urgency, and optional contact context. "
            f"The strongest routing examples were {related_incidents}."
        )

        return {
            "assignment_group": recommended_group,
            "confidence": round(confidence, 4),
            "rationale": rationale,
        }, top_matches

    def _build_keyword_index(self, records: list[RoutingRecord]) -> None:
        tokenized_records: dict[str, set[str]] = {}
        for record in records:
            tokens = self._tokenize(record.document)
            tokenized_records[record.record_key] = tokens
        self._tokenized_records = tokenized_records

    def _keyword_score(self, query_tokens: set[str], record_tokens: set[str]) -> float:
        if not query_tokens:
            return 0.0
        overlap = query_tokens & record_tokens
        if not overlap:
            return 0.0
        return len(overlap) / len(query_tokens)

    def _field_score(
        self,
        record: RoutingRecord,
        category: str,
        subcategory: str,
        u_symptom: str,
        impact: str,
        urgency: str,
        contact_type: str,
        location: str,
    ) -> float:
        score = 0.0
        if category and record.category.lower() == category.lower():
            score += 0.22
        if subcategory and record.subcategory.lower() == subcategory.lower():
            score += 0.24
        if u_symptom and record.symptom.lower() == u_symptom.lower():
            score += 0.22
        if impact and record.impact.lower() == impact.lower():
            score += 0.12
        if urgency and record.urgency.lower() == urgency.lower():
            score += 0.12
        if contact_type and record.contact_type.lower() == contact_type.lower():
            score += 0.04
        if location and record.location.lower() == location.lower():
            score += 0.04
        return score

    def _tokenize(self, text: str) -> set[str]:
        return set(TOKEN_PATTERN.findall(text.lower()))

    def _clean_value(self, value: str) -> str:
        cleaned = value.strip()
        return "" if cleaned == "?" else cleaned

    def _parse_datetime(self, value: str) -> datetime | None:
        if not value or value == "?":
            return None
        for fmt in ("%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return None
