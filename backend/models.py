"""Pydantic data models shared across the application."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"

    @property
    def rank(self) -> int:
        return {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}[self.value]


class Disposition(str, Enum):
    OPEN = "Open"
    IGNORED = "Ignored"
    REMEDIATE = "Remediate"
    LATER = "Later"


class LLMProvider(str, Enum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    OLLAMA = "ollama"
    LMSTUDIO = "lmstudio"


# ----------------------------- UniFi source config -----------------------------

class UniFiSettings(BaseModel):
    host: str = "https://10.0.0.1"
    port: int = 443
    username: str = ""
    password: str = ""
    site: str = "default"
    verify_ssl: bool = False  # default UniFi consoles ship a self-signed cert
    is_unifi_os: bool = True  # UDM/UDM-Pro/Cloud Key Gen2+ use /proxy/network prefix


# ----------------------------- LLM config -----------------------------

class LLMSettings(BaseModel):
    provider: LLMProvider = LLMProvider.OLLAMA
    model: str = "qwen2.5-coder:7b"
    api_key: str = ""
    # base_url lets local runtimes (Ollama / LM Studio) or proxies be targeted.
    base_url: str = ""
    temperature: float = 0.1
    max_output_tokens: int = 8000


class AppSettings(BaseModel):
    unifi: UniFiSettings = Field(default_factory=UniFiSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)


# ----------------------------- Findings -----------------------------

class Remediation(BaseModel):
    summary: str = ""
    manual_steps: list[str] = Field(default_factory=list)
    # Optional automated action the app knows how to apply against the controller.
    automation: Optional["AutomationAction"] = None


class AutomationAction(BaseModel):
    """A structured, reviewable change the app can push to the controller."""
    description: str
    method: str  # GET/POST/PUT/DELETE
    endpoint: str  # relative to the network API root, e.g. "/rest/firewallrule/<id>"
    payload: dict[str, Any] = Field(default_factory=dict)


class Issue(BaseModel):
    id: str
    title: str
    severity: Severity
    category: str = "General"
    description: str
    evidence: str = ""
    affected_objects: list[str] = Field(default_factory=list)
    remediation: Remediation = Field(default_factory=Remediation)
    disposition: Disposition = Disposition.OPEN
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Snapshot(BaseModel):
    """A stored, point-in-time collection of controller config + traffic."""
    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    site: str = "default"
    data: dict[str, Any] = Field(default_factory=dict)
    object_counts: dict[str, int] = Field(default_factory=dict)


class AnalysisResult(BaseModel):
    id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    snapshot_id: str = ""
    model: str = ""
    provider: str = ""
    summary: str = ""
    issues: list[Issue] = Field(default_factory=list)


Remediation.model_rebuild()
