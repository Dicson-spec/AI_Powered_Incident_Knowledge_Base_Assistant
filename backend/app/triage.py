import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from openai import OpenAI

from .config import settings


TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
VALID_PRIORITIES = {"1", "2", "3", "4", "5"}


@dataclass(slots=True)
class TriageRecord:
    incident_id: str
    status: str
    priority: str
    impact: str
    urgency: str
    category: str
    ci_name: str
    ci_cat: str
    ci_subcat: str
    wbs: str
    alert_status: str
    closure_code: str

    @property
    def document(self) -> str:
        return (
            f"Incident ID: {self.incident_id}\n"
            f"Ticket Category: {self.category}\n"
            f"CI Name: {self.ci_name}\n"
            f"CI Category: {self.ci_cat}\n"
            f"CI Subcategory: {self.ci_subcat}\n"
            f"WBS: {self.wbs}\n"
            f"Alert Status: {self.alert_status}\n"
            f"Closure Code: {self.closure_code}\n"
            f"Impact: {self.impact}\n"
            f"Urgency: {self.urgency}\n"
            f"Priority: {self.priority}\n"
            f"Status: {self.status}"
        )

    @property
    def keyword_text(self) -> str:
        return (
            f"{self.incident_id} {self.category} {self.ci_name} {self.ci_cat} "
            f"{self.ci_subcat} {self.alert_status} {self.closure_code} "
            f"{self.impact} {self.urgency} {self.priority} {self.status}"
        )

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "incident_id": self.incident_id,
            "status": self.status,
            "priority": self.priority,
            "impact": self.impact,
            "urgency": self.urgency,
            "category": self.category,
            "ci_name": self.ci_name,
            "ci_cat": self.ci_cat,
            "ci_subcat": self.ci_subcat,
            "wbs": self.wbs,
            "alert_status": self.alert_status,
            "closure_code": self.closure_code,
        }


class TriageAgent:
    def __init__(self) -> None:
        if settings.openai_api_key == "your_openai_api_key_here":
            raise RuntimeError("Set OPENAI_API_KEY in the root .env file before starting the backend.")

        self._client = OpenAI(api_key=settings.openai_api_key)
        self._records: list[TriageRecord] = []
        self._record_lookup: dict[str, TriageRecord] = {}
        self._priority_distribution: Counter[str] = Counter()
        self._filter_options: dict[str, list[str]] = {
            "category": [],
            "ci_category": [],
            "ci_subcategory": [],
        }

    def ensure_ready(self) -> None:
        records = self.load_records()
        self._records = records
        self._record_lookup = {record.incident_id: record for record in records}
        self._priority_distribution = Counter(record.priority for record in records)
        self._filter_options = {
            "category": sorted({record.category for record in records}),
            "ci_category": sorted({record.ci_cat for record in records if record.ci_cat}),
            "ci_subcategory": sorted({record.ci_subcat for record in records if record.ci_subcat}),
        }

    @property
    def filter_options(self) -> dict[str, list[str]]:
        return self._filter_options

    def load_records(self) -> list[TriageRecord]:
        rows: list[TriageRecord] = []
        with settings.itsm_dataset_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                priority = row["Priority"].strip()
                if priority not in VALID_PRIORITIES:
                    continue

                rows.append(
                    TriageRecord(
                        incident_id=row["Incident_ID"].strip(),
                        status=row["Status"].strip(),
                        priority=priority,
                        impact=row["Impact"].strip(),
                        urgency=row["Urgency"].strip(),
                        category=row["Category"].strip(),
                        ci_name=row["CI_Name"].strip(),
                        ci_cat=row["CI_Cat"].strip(),
                        ci_subcat=row["CI_Subcat"].strip(),
                        wbs=row["WBS"].strip(),
                        alert_status=row["Alert_Status"].strip(),
                        closure_code=row["Closure_Code"].strip(),
                    )
                )
        return rows

    def classify(
        self,
        ticket_summary: str,
        category: str,
        ci_category: str,
        ci_subcategory: str,
        top_k: int,
    ) -> tuple[dict, list[dict]]:
        if not self._records:
            self.ensure_ready()

        query_text = self._build_query_text(
            ticket_summary=ticket_summary,
            category=category,
            ci_category=ci_category,
            ci_subcategory=ci_subcategory,
        )
        query_tokens = self._tokenize(query_text)

        scored_matches: list[dict] = []
        for record in self._records:
            record_tokens = self._tokenize(record.keyword_text)
            keyword_score = self._keyword_score(query_tokens, record_tokens)
            field_score = self._field_score(
                record=record,
                category=category,
                ci_category=ci_category,
                ci_subcategory=ci_subcategory,
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
            raise RuntimeError("No matching ITSM records were found for the selected metadata filters.")

        priority_scores: defaultdict[str, float] = defaultdict(float)
        impact_scores: defaultdict[str, float] = defaultdict(float)
        urgency_scores: defaultdict[str, float] = defaultdict(float)
        total_score = 0.0

        for match in top_matches:
            weight = max(match["similarity"], 0.01)
            total_score += weight
            priority_scores[match["priority"]] += weight
            impact_scores[match["impact"]] += weight
            urgency_scores[match["urgency"]] += weight

        predicted_priority = max(priority_scores.items(), key=lambda item: item[1])[0]
        predicted_impact = max(impact_scores.items(), key=lambda item: item[1])[0]
        predicted_urgency = max(urgency_scores.items(), key=lambda item: item[1])[0]
        confidence = priority_scores[predicted_priority] / total_score if total_score else 0.0

        explanation = self._generate_explanation(
            ticket_summary=ticket_summary,
            predicted_priority=predicted_priority,
            predicted_impact=predicted_impact,
            predicted_urgency=predicted_urgency,
            confidence=confidence,
            matches=top_matches,
        )

        result = {
            "priority": predicted_priority,
            "impact": predicted_impact,
            "urgency": predicted_urgency,
            "confidence": round(confidence, 4),
            "rationale": explanation,
        }
        return result, top_matches

    def _generate_explanation(
        self,
        ticket_summary: str,
        predicted_priority: str,
        predicted_impact: str,
        predicted_urgency: str,
        confidence: float,
        matches: list[dict],
    ) -> str:
        context = "\n\n".join(
            [
                (
                    f"Match {index + 1}\n"
                    f"Incident ID: {match['incident_id']}\n"
                    f"Priority: {match['priority']}\n"
                    f"Impact: {match['impact']}\n"
                    f"Urgency: {match['urgency']}\n"
                    f"Ticket Category: {match['category']}\n"
                    f"CI Category: {match['ci_cat']}\n"
                    f"CI Subcategory: {match['ci_subcat']}\n"
                    f"Alert Status: {match['alert_status']}\n"
                    f"Closure Code: {match['closure_code']}"
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
                                "You are an ITSM triage assistant. Explain the recommended priority using only "
                                "the historical tickets provided. Keep the explanation concise and grounded. "
                                "If the confidence is low or the examples skew heavily to lower priorities, "
                                "say that explicitly."
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
                                f"New ticket summary:\n{ticket_summary}\n\n"
                                f"Predicted priority: {predicted_priority}\n"
                                f"Predicted impact: {predicted_impact}\n"
                                f"Predicted urgency: {predicted_urgency}\n"
                                f"Confidence: {confidence:.2f}\n\n"
                                f"Historical matches:\n{context}\n\n"
                                "Write a short triage rationale with:\n"
                                "1. Recommended priority\n"
                                "2. Why it fits the historical matches\n"
                                "3. Any caution about confidence or class imbalance\n"
                                "4. Mention the incident IDs used"
                            ),
                        }
                    ],
                },
            ],
        )
        return response.output_text

    def _build_query_text(
        self,
        ticket_summary: str,
        category: str,
        ci_category: str,
        ci_subcategory: str,
    ) -> str:
        return (
            f"Summary: {ticket_summary}\n"
            f"Ticket Category: {category}\n"
            f"CI Category: {ci_category}\n"
            f"CI Subcategory: {ci_subcategory}"
        )

    def _keyword_score(self, query_tokens: set[str], record_tokens: set[str]) -> float:
        if not query_tokens:
            return 0.0

        overlap = query_tokens & record_tokens
        if not overlap:
            return 0.0

        return len(overlap) / len(query_tokens)

    def _field_score(
        self,
        record: TriageRecord,
        category: str,
        ci_category: str,
        ci_subcategory: str,
    ) -> float:
        score = 0.0
        if category and record.category.lower() == category.lower():
            score += 0.25
        if ci_category and record.ci_cat.lower() == ci_category.lower():
            score += 0.3
        if ci_subcategory and record.ci_subcat.lower() == ci_subcategory.lower():
            score += 0.35
        return score

    def _tokenize(self, text: str) -> set[str]:
        return set(TOKEN_PATTERN.findall(text.lower()))
