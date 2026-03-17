from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    openai_api_key: str
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    chroma_path: Path = ROOT_DIR / "backend" / "chroma_db"
    chroma_collection: str = "incident-response-bot"
    dataset_path: Path = ROOT_DIR / "data" / "incident_response_dataset_150_rows.xlsx - Incident Data.csv"
    itsm_dataset_path: Path = ROOT_DIR / "data" / "ITSM_data.csv"
    event_log_dataset_path: Path = ROOT_DIR / "data" / "incident_event_log.csv"
    backend_port: int = 8000
    gateway_port: int = 8000
    resolution_service_port: int = 8001
    triage_service_port: int = 8002
    routing_service_port: int = 8003
    gateway_url: str = "http://127.0.0.1:8000"
    resolution_service_url: str = "http://127.0.0.1:8001"
    triage_service_url: str = "http://127.0.0.1:8002"
    routing_service_url: str = "http://127.0.0.1:8003"
    frontend_api_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("chroma_path", "dataset_path", "itsm_dataset_path", "event_log_dataset_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: str | Path) -> Path:
        return Path(value)


settings = Settings()
