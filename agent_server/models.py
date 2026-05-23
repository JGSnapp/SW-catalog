from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    id: str
    label: str
    url: str
    enabled: bool = True
    created_at: str
    updated_at: str
    last_run_at: str | None = None
    next_run_at: str | None = None


class AppConfig(BaseModel):
    company_profile: str = ""
    global_prompt: str = ""
    target_institutions: str = ""
    search_directions: str = "логистика, сертификация, разработка, мероприятия, робототехника"
    min_amount: str = ""
    max_amount: str = ""
    funding_types: str = ""
    regions: str = ""
    deadline_window: str = ""
    eligibility_requirements: str = ""
    excluded_restrictions: str = ""
    keywords: str = ""
    interval_hours: int = Field(default=24, ge=1, le=720)
    iterations_per_site: int = Field(default=12, ge=1, le=50)
    source_discovery_enabled: bool = False
    source_discovery_interval_hours: int = Field(default=168, ge=1, le=2160)
    source_discovery_iterations: int = Field(default=10, ge=1, le=50)
    source_discovery_last_run_at: str | None = None
    source_discovery_next_run_at: str | None = None
    sites: list[SiteConfig] = Field(default_factory=list)


GrantWorkflowStatus = Literal["new", "reviewed", "suitable", "not_suitable", "applied"]


class GrantRecord(BaseModel):
    id: str
    title: str
    institution: str = ""
    amount: str = ""
    funding_type: str = ""
    category: str = ""
    conditions: str
    restrictions: str = ""
    deadline: str
    application_url: str = ""
    status: GrantWorkflowStatus = "new"
    site: str
    site_id: str
    description: str
    fit_reason: str = ""
    how_to_apply: str = ""
    source: str
    site_url: str
    discovered_at: str
    updated_at: str
    last_run_id: str
    telegram_notified_at: str | None = None


class GrantStatusUpdate(BaseModel):
    status: GrantWorkflowStatus


class ProductComponent(BaseModel):
    id: str
    name: str
    specification: str = ""
    quantity: float = Field(default=1, ge=0)
    target_price: str = ""
    notes: str = ""


class ProductRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    target_volume: str = ""
    components: list[ProductComponent] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    target_volume: str = Field(default="", max_length=1000)
    components: list[ProductComponent] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    target_volume: str = Field(default="", max_length=1000)
    components: list[ProductComponent] = Field(default_factory=list)


class RunRecord(BaseModel):
    id: str
    site_id: str
    site_url: str
    started_at: str
    finished_at: str | None = None
    status: Literal["queued", "running", "completed", "failed"]
    summary: str = ""
    error: str | None = None


SourceCandidateStatus = Literal["new", "added", "dismissed"]


class SourceCandidate(BaseModel):
    id: str
    label: str
    url: str
    reason: str
    evidence: str = ""
    status: SourceCandidateStatus = "new"
    discovered_at: str
    updated_at: str
    last_run_id: str
    telegram_notified_at: str | None = None


class SourceCandidateStatusUpdate(BaseModel):
    status: SourceCandidateStatus


class RunEventRecord(BaseModel):
    id: str
    run_id: str
    site_id: str
    site_url: str
    event_type: str
    message: str
    created_at: str
    metadata: dict[str, str] = Field(default_factory=dict)


class SiteCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = True


class SourceCandidateCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    reason: str = Field(default="", max_length=4000)
    evidence: str = Field(default="", max_length=4000)


class SiteUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = True


class SiteTextResponse(BaseModel):
    content: str
    updated_at: str | None = None


class AppState(BaseModel):
    config: AppConfig
    grants: list[GrantRecord]
    runs: list[RunRecord]
    source_candidates: list[SourceCandidate] = Field(default_factory=list)
    products: list[ProductRecord] = Field(default_factory=list)


def utc_now() -> datetime:
    return datetime.utcnow()


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat() + "Z"
