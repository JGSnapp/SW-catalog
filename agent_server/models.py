from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


ProjectStatus = Literal["planning", "in_progress", "review", "completed", "archived"]
SupplierStatus = Literal["new", "verified", "preferred", "rejected", "contacted"]


class ProjectItem(BaseModel):
    id: str
    name: str
    specification: str = ""
    quantity: float = Field(default=1, ge=0)
    unit: str = "шт"
    target_price: str = ""
    notes: str = ""
    image_url: str = ""
    monitoring_enabled: bool = True
    ai_notes: str = ""
    created_at: str
    updated_at: str


class ProjectRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    status: ProjectStatus = "planning"
    target_volume: str = ""
    budget: str = ""
    currency: str = "RUB"
    category: str = ""
    cover_image_url: str = ""
    items: list[ProjectItem] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ProjectItemDraft(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    specification: str = Field(default="", max_length=2000)
    quantity: float = Field(default=1, ge=0)
    unit: str = Field(default="шт", max_length=50)
    target_price: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=4000)
    image_url: str = Field(default="", max_length=2000)
    monitoring_enabled: bool = True


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=8000)
    status: ProjectStatus = "planning"
    target_volume: str = Field(default="", max_length=1000)
    budget: str = Field(default="", max_length=200)
    currency: str = Field(default="RUB", max_length=10)
    category: str = Field(default="", max_length=200)
    cover_image_url: str = Field(default="", max_length=2000)
    items: list[ProjectItemDraft] = Field(default_factory=list)


class ProjectUpdate(ProjectCreate):
    pass


class SupplierRecord(BaseModel):
    id: str
    project_id: str
    item_id: str
    name: str
    offer_title: str = ""
    price: float | None = None
    price_text: str = ""
    currency: str = "RUB"
    lead_time: str = ""
    country: str = ""
    category: str = ""
    description: str = ""
    terms: str = ""
    restrictions: str = ""
    url: str = ""
    source_url: str = ""
    contact: str = ""
    image_url: str = ""
    status: SupplierStatus = "new"
    is_existing: bool = False
    monitoring_enabled: bool = False
    discovered_at: str
    updated_at: str
    last_checked_at: str | None = None
    last_run_id: str | None = None
    ai_notes: str = ""
    price_history: list[dict[str, str]] = Field(default_factory=list)


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    offer_title: str = Field(default="", max_length=500)
    price: float | None = None
    price_text: str = Field(default="", max_length=200)
    currency: str = Field(default="RUB", max_length=10)
    lead_time: str = Field(default="", max_length=200)
    country: str = Field(default="", max_length=200)
    category: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=4000)
    terms: str = Field(default="", max_length=2000)
    restrictions: str = Field(default="", max_length=2000)
    url: str = Field(default="", max_length=2000)
    source_url: str = Field(default="", max_length=2000)
    contact: str = Field(default="", max_length=2000)
    image_url: str = Field(default="", max_length=2000)
    status: SupplierStatus = "new"
    is_existing: bool = True
    monitoring_enabled: bool = False


class SupplierUpdate(SupplierCreate):
    pass


class SupplierStatusUpdate(BaseModel):
    status: SupplierStatus


class SupplierMonitorUpdate(BaseModel):
    monitoring_enabled: bool


class SourceSite(BaseModel):
    id: str
    label: str
    url: str
    enabled: bool = True
    category: str = ""
    notes: str = ""
    created_at: str
    updated_at: str


class SourceSiteCreate(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=2000)
    enabled: bool = True


class SourceSiteUpdate(SourceSiteCreate):
    pass


class AppConfig(BaseModel):
    company_profile: str = (
        "Малый fashion-бренд. Закупаем ткани, фурнитуру, упаковку, бирки и услуги контрактного производства "
        "для регулярных коллекций."
    )
    global_prompt: str = ""
    default_currency: str = "RUB"
    monitored_categories: str = "ткани, фурнитура, упаковка, бирки, отшив"
    preferred_regions: str = "EU, Turkey, Portugal, Italy"
    excluded_regions: str = ""
    max_lead_time: str = "45 дней"
    discovery_iterations: int = Field(default=10, ge=1, le=50)
    monitor_iterations: int = Field(default=6, ge=1, le=30)
    monitor_interval_hours: int = Field(default=24, ge=1, le=720)
    sites: list[SourceSite] = Field(default_factory=list)


class RunRecord(BaseModel):
    id: str
    kind: Literal["item_discovery", "item_monitor", "upload_parse", "image_search"]
    project_id: str | None = None
    item_id: str | None = None
    label: str
    started_at: str
    finished_at: str | None = None
    status: Literal["queued", "running", "completed", "failed"]
    summary: str = ""
    error: str | None = None


class RunEventRecord(BaseModel):
    id: str
    run_id: str
    project_id: str | None = None
    item_id: str | None = None
    event_type: str
    message: str
    created_at: str
    metadata: dict[str, str] = Field(default_factory=dict)


class UploadRecord(BaseModel):
    id: str
    name: str
    kind: Literal["text", "table", "file"]
    size: int = 0
    received_at: str
    parsed_at: str | None = None
    status: Literal["received", "parsing", "parsed", "failed"] = "received"
    summary: str = ""
    error: str | None = None
    created_project_ids: list[str] = Field(default_factory=list)


class UploadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    kind: Literal["text", "table", "file"] = "text"
    content: str = Field(min_length=1, max_length=400_000)


class SupplierChange(BaseModel):
    id: str
    supplier_id: str
    project_id: str
    item_id: str
    supplier_name: str
    item_name: str
    change_type: Literal["price_up", "price_down", "stock", "lead_time", "terms", "added", "removed", "info"]
    old_value: str = ""
    new_value: str = ""
    summary: str = ""
    detected_at: str


class AppState(BaseModel):
    config: AppConfig
    projects: list[ProjectRecord] = Field(default_factory=list)
    suppliers: list[SupplierRecord] = Field(default_factory=list)
    runs: list[RunRecord] = Field(default_factory=list)
    uploads: list[UploadRecord] = Field(default_factory=list)
    changes: list[SupplierChange] = Field(default_factory=list)
    stats: dict[str, float | int | str] = Field(default_factory=dict)


class UserPublic(BaseModel):
    id: str
    email: str
    name: str = ""
    created_at: str


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=200)
    name: str = Field(default="", max_length=200)


class AuthResponse(BaseModel):
    user: UserPublic
    token: str


def utc_now() -> datetime:
    return datetime.utcnow()


def utc_now_iso() -> str:
    return utc_now().replace(microsecond=0).isoformat() + "Z"
