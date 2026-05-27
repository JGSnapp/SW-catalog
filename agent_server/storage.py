from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg.types.json import Jsonb

try:
    from .models import (
        AppConfig,
        ProjectCreate,
        ProjectItem,
        ProjectItemDraft,
        ProjectRecord,
        ProjectUpdate,
        RunEventRecord,
        RunRecord,
        SourceSite,
        SourceSiteCreate,
        SourceSiteUpdate,
        SupplierChange,
        SupplierCreate,
        SupplierRecord,
        SupplierUpdate,
        UploadCreate,
        UploadRecord,
        utc_now,
        utc_now_iso,
    )
except ImportError:  # pragma: no cover
    from models import (  # type: ignore
        AppConfig,
        ProjectCreate,
        ProjectItem,
        ProjectItemDraft,
        ProjectRecord,
        ProjectUpdate,
        RunEventRecord,
        RunRecord,
        SourceSite,
        SourceSiteCreate,
        SourceSiteUpdate,
        SupplierChange,
        SupplierCreate,
        SupplierRecord,
        SupplierUpdate,
        UploadCreate,
        UploadRecord,
        utc_now,
        utc_now_iso,
    )


def parse_price_value(text: str) -> float | None:
    if not text:
        return None
    cleaned = re.sub(r"[^0-9,.\-]", " ", text).strip()
    cleaned = cleaned.replace(",", ".")
    matches = re.findall(r"\d+(?:\.\d+)?", cleaned)
    if not matches:
        return None
    values = [float(value) for value in matches if float(value) > 0]
    if not values:
        return None
    return min(values)


def normalize_url_key(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    path = re.sub(r"/+$", "", parsed.path or "")
    return urlunparse((scheme, netloc, path, "", "", ""))


class JsonStorage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.notes_dir = data_dir / "notes"
        self.runs_dir = data_dir / "runs"
        self.events_dir = data_dir / "events"
        self.uploads_dir = data_dir / "uploads"
        self.images_dir = data_dir / "images"
        self.config_path = data_dir / "config.json"
        self.projects_path = data_dir / "projects.json"
        self.suppliers_path = data_dir / "suppliers.json"
        self.uploads_index_path = data_dir / "uploads.json"
        self.changes_path = data_dir / "changes.json"
        self.postgres_dsn: str | None = None
        self.postgres_user_id: str | None = None
        self._lock = threading.RLock()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        for path in [
            self.data_dir,
            self.notes_dir,
            self.runs_dir,
            self.events_dir,
            self.uploads_dir,
            self.images_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self._write_json(self.config_path, AppConfig().model_dump())
        for path in [self.projects_path, self.suppliers_path, self.uploads_index_path, self.changes_path]:
            if not path.exists():
                self._write_json(path, [])

    # ----- low-level helpers -----

    def _read_json(self, path: Path, default: Any) -> Any:
        persisted = self._read_postgres_doc(path)
        if persisted is not None:
            return persisted
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        self._write_postgres_doc(path, payload)

    def _read_text(self, path: Path) -> tuple[str, str | None]:
        persisted = self._read_postgres_doc(path)
        if isinstance(persisted, dict) and "text" in persisted:
            return str(persisted.get("text") or ""), persisted.get("mtime")
        if not path.exists():
            return "", None
        return path.read_text(encoding="utf-8"), self._mtime(path)

    def _write_text(self, path: Path, content: str) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        body = (content or "").strip()
        path.write_text(body + "\n" if body else "", encoding="utf-8")
        mtime = self._mtime(path) or utc_now_iso()
        self._write_postgres_doc(path, {"text": body, "mtime": mtime})
        return mtime

    def enable_postgres_sync(self, *, dsn: str, user_id: str) -> None:
        self.postgres_dsn = dsn
        self.postgres_user_id = user_id
        self._ensure_postgres_docs()

    def _postgres_key(self, path: Path) -> str | None:
        if not self.postgres_dsn or not self.postgres_user_id:
            return None
        try:
            return path.relative_to(self.data_dir).as_posix()
        except ValueError:
            return path.name

    def _ensure_postgres_docs(self) -> None:
        if not self.postgres_dsn:
            return
        with psycopg.connect(self.postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app_storage_documents (
                      user_id uuid NOT NULL,
                      key text NOT NULL,
                      payload jsonb NOT NULL,
                      updated_at timestamptz NOT NULL DEFAULT now(),
                      PRIMARY KEY (user_id, key)
                    )
                    """
                )

    def _read_postgres_doc(self, path: Path) -> Any | None:
        key = self._postgres_key(path)
        if key is None:
            return None
        try:
            with psycopg.connect(self.postgres_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT payload FROM app_storage_documents WHERE user_id = %s AND key = %s",
                        (self.postgres_user_id, key),
                    )
                    row = cur.fetchone()
        except Exception:
            return None
        return row[0] if row else None

    def _write_postgres_doc(self, path: Path, payload: Any) -> None:
        key = self._postgres_key(path)
        if key is None:
            return
        try:
            with psycopg.connect(self.postgres_dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO app_storage_documents (user_id, key, payload, updated_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (user_id, key)
                        DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                        """,
                        (self.postgres_user_id, key, Jsonb(payload)),
                    )
        except Exception:
            return

    def _mtime(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat() + "Z"

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", ""))

    # ----- config -----

    def load_config(self) -> AppConfig:
        with self._lock:
            return AppConfig.model_validate(self._read_json(self.config_path, AppConfig().model_dump()))

    def save_config(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._write_json(self.config_path, config.model_dump())
            return config

    def replace_config(self, config: AppConfig) -> AppConfig:
        config = config.model_copy(update={"default_currency": "RUB"})
        return self.save_config(config)

    # ----- source sites -----

    def list_sites(self) -> list[SourceSite]:
        return self.load_config().sites

    def add_site(self, payload: SourceSiteCreate) -> SourceSite:
        with self._lock:
            config = self.load_config()
            now = utc_now_iso()
            site = SourceSite(
                id=str(uuid.uuid4()),
                label=payload.label.strip(),
                url=payload.url.strip(),
                category=payload.category.strip(),
                notes=payload.notes.strip(),
                enabled=payload.enabled,
                created_at=now,
                updated_at=now,
            )
            config.sites.append(site)
            self.save_config(config)
            return site

    def update_site(self, site_id: str, payload: SourceSiteUpdate) -> SourceSite:
        with self._lock:
            config = self.load_config()
            for index, site in enumerate(config.sites):
                if site.id != site_id:
                    continue
                updated = site.model_copy(
                    update={
                        "label": payload.label.strip(),
                        "url": payload.url.strip(),
                        "category": payload.category.strip(),
                        "notes": payload.notes.strip(),
                        "enabled": payload.enabled,
                        "updated_at": utc_now_iso(),
                    }
                )
                config.sites[index] = updated
                self.save_config(config)
                return updated
        raise KeyError(site_id)

    def delete_site(self, site_id: str) -> None:
        with self._lock:
            config = self.load_config()
            config.sites = [site for site in config.sites if site.id != site_id]
            self.save_config(config)

    # ----- projects -----

    def list_projects(self) -> list[ProjectRecord]:
        with self._lock:
            raw = self._read_json(self.projects_path, [])
            projects = [ProjectRecord.model_validate(item) for item in raw]
            return sorted(projects, key=lambda item: item.updated_at, reverse=True)

    def _save_projects(self, projects: Iterable[ProjectRecord]) -> None:
        self._write_json(self.projects_path, [item.model_dump() for item in projects])

    def get_project(self, project_id: str) -> ProjectRecord:
        for project in self.list_projects():
            if project.id == project_id:
                return project
        raise KeyError(project_id)

    def _items_from_drafts(self, drafts: Iterable[ProjectItemDraft]) -> list[ProjectItem]:
        now = utc_now_iso()
        items: list[ProjectItem] = []
        for draft in drafts:
            name = draft.name.strip()
            if not name:
                continue
            items.append(
                ProjectItem(
                    id=str(uuid.uuid4()),
                    name=name,
                    specification=draft.specification.strip(),
                    quantity=draft.quantity,
                    unit=(draft.unit or "шт").strip() or "шт",
                    target_price=draft.target_price.strip(),
                    notes=draft.notes.strip(),
                    image_url=draft.image_url.strip(),
                    monitoring_enabled=draft.monitoring_enabled,
                    ai_notes="",
                    created_at=now,
                    updated_at=now,
                )
            )
        return items

    def add_project(self, payload: ProjectCreate) -> ProjectRecord:
        with self._lock:
            now = utc_now_iso()
            project = ProjectRecord(
                id=str(uuid.uuid4()),
                name=payload.name.strip(),
                description=payload.description.strip(),
                status=payload.status,
                target_volume=payload.target_volume.strip(),
                budget=payload.budget.strip(),
                currency="RUB",
                category=payload.category.strip(),
                cover_image_url=payload.cover_image_url.strip(),
                items=self._items_from_drafts(payload.items),
                created_at=now,
                updated_at=now,
            )
            projects = self.list_projects()
            projects.append(project)
            self._save_projects(projects)
            return project

    def update_project(self, project_id: str, payload: ProjectUpdate) -> ProjectRecord:
        with self._lock:
            projects = self.list_projects()
            for index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                existing_by_id = {item.id: item for item in project.items}
                new_items: list[ProjectItem] = []
                now = utc_now_iso()
                for draft in payload.items:
                    name = draft.name.strip()
                    if not name:
                        continue
                    existing = existing_by_id.get(getattr(draft, "id", "")) if hasattr(draft, "id") else None
                    if existing is None:
                        new_items.append(
                            ProjectItem(
                                id=str(uuid.uuid4()),
                                name=name,
                                specification=draft.specification.strip(),
                                quantity=draft.quantity,
                                unit=(draft.unit or "шт").strip() or "шт",
                                target_price=draft.target_price.strip(),
                                notes=draft.notes.strip(),
                                image_url=draft.image_url.strip(),
                                monitoring_enabled=draft.monitoring_enabled,
                                ai_notes="",
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    else:
                        new_items.append(
                            existing.model_copy(
                                update={
                                    "name": name,
                                    "specification": draft.specification.strip(),
                                    "quantity": draft.quantity,
                                    "unit": (draft.unit or "шт").strip() or "шт",
                                    "target_price": draft.target_price.strip(),
                                    "notes": draft.notes.strip(),
                                    "image_url": draft.image_url.strip() or existing.image_url,
                                    "monitoring_enabled": draft.monitoring_enabled,
                                    "updated_at": now,
                                }
                            )
                        )
                updated = project.model_copy(
                    update={
                        "name": payload.name.strip(),
                        "description": payload.description.strip(),
                        "status": payload.status,
                        "target_volume": payload.target_volume.strip(),
                        "budget": payload.budget.strip(),
                        "currency": "RUB",
                        "category": payload.category.strip(),
                        "cover_image_url": payload.cover_image_url.strip() or project.cover_image_url,
                        "items": new_items,
                        "updated_at": now,
                    }
                )
                projects[index] = updated
                self._save_projects(projects)
                return updated
        raise KeyError(project_id)

    def delete_project(self, project_id: str) -> None:
        with self._lock:
            projects = [project for project in self.list_projects() if project.id != project_id]
            self._save_projects(projects)
            suppliers = [supplier for supplier in self.list_suppliers() if supplier.project_id != project_id]
            self._write_json(self.suppliers_path, [item.model_dump() for item in suppliers])

    def add_item(self, project_id: str, draft: ProjectItemDraft) -> ProjectItem:
        with self._lock:
            projects = self.list_projects()
            for index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                items = self._items_from_drafts([draft])
                if not items:
                    raise ValueError("item name is required")
                item = items[0]
                project = project.model_copy(update={"items": project.items + [item], "updated_at": utc_now_iso()})
                projects[index] = project
                self._save_projects(projects)
                return item
        raise KeyError(project_id)

    def update_item(self, project_id: str, item_id: str, draft: ProjectItemDraft) -> ProjectItem:
        with self._lock:
            projects = self.list_projects()
            for project_index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                for item_index, item in enumerate(project.items):
                    if item.id != item_id:
                        continue
                    updated_item = item.model_copy(
                        update={
                            "name": draft.name.strip() or item.name,
                            "specification": draft.specification.strip(),
                            "quantity": draft.quantity,
                            "unit": (draft.unit or "шт").strip() or "шт",
                            "target_price": draft.target_price.strip(),
                            "notes": draft.notes.strip(),
                            "image_url": draft.image_url.strip() or item.image_url,
                            "monitoring_enabled": draft.monitoring_enabled,
                            "updated_at": utc_now_iso(),
                        }
                    )
                    new_items = list(project.items)
                    new_items[item_index] = updated_item
                    projects[project_index] = project.model_copy(update={"items": new_items, "updated_at": utc_now_iso()})
                    self._save_projects(projects)
                    return updated_item
                raise KeyError(item_id)
        raise KeyError(project_id)

    def delete_item(self, project_id: str, item_id: str) -> None:
        with self._lock:
            projects = self.list_projects()
            for index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                new_items = [item for item in project.items if item.id != item_id]
                projects[index] = project.model_copy(update={"items": new_items, "updated_at": utc_now_iso()})
                self._save_projects(projects)
                suppliers = [supplier for supplier in self.list_suppliers() if supplier.item_id != item_id]
                self._write_json(self.suppliers_path, [supplier.model_dump() for supplier in suppliers])
                return

    def set_item_image(self, project_id: str, item_id: str, image_url: str) -> ProjectItem:
        with self._lock:
            projects = self.list_projects()
            for project_index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                for item_index, item in enumerate(project.items):
                    if item.id != item_id:
                        continue
                    updated = item.model_copy(update={"image_url": image_url, "updated_at": utc_now_iso()})
                    new_items = list(project.items)
                    new_items[item_index] = updated
                    projects[project_index] = project.model_copy(update={"items": new_items, "updated_at": utc_now_iso()})
                    self._save_projects(projects)
                    return updated
                raise KeyError(item_id)
        raise KeyError(project_id)

    def set_item_notes(self, project_id: str, item_id: str, ai_notes: str) -> ProjectItem:
        with self._lock:
            projects = self.list_projects()
            for project_index, project in enumerate(projects):
                if project.id != project_id:
                    continue
                for item_index, item in enumerate(project.items):
                    if item.id != item_id:
                        continue
                    updated = item.model_copy(update={"ai_notes": ai_notes.strip(), "updated_at": utc_now_iso()})
                    new_items = list(project.items)
                    new_items[item_index] = updated
                    projects[project_index] = project.model_copy(update={"items": new_items, "updated_at": utc_now_iso()})
                    self._save_projects(projects)
                    return updated
                raise KeyError(item_id)
        raise KeyError(project_id)

    # ----- suppliers -----

    def list_suppliers(self) -> list[SupplierRecord]:
        with self._lock:
            raw = self._read_json(self.suppliers_path, [])
            suppliers = [SupplierRecord.model_validate(item) for item in raw]
            return sorted(suppliers, key=lambda item: item.updated_at, reverse=True)

    def _save_suppliers(self, suppliers: Iterable[SupplierRecord]) -> None:
        self._write_json(self.suppliers_path, [item.model_dump() for item in suppliers])

    def get_supplier(self, supplier_id: str) -> SupplierRecord:
        for supplier in self.list_suppliers():
            if supplier.id == supplier_id:
                return supplier
        raise KeyError(supplier_id)

    def list_suppliers_for_item(self, item_id: str) -> list[SupplierRecord]:
        return [supplier for supplier in self.list_suppliers() if supplier.item_id == item_id]

    def add_supplier(self, project_id: str, item_id: str, payload: SupplierCreate) -> SupplierRecord:
        with self._lock:
            now = utc_now_iso()
            price = payload.price if payload.price is not None else parse_price_value(payload.price_text)
            supplier = SupplierRecord(
                id=str(uuid.uuid4()),
                project_id=project_id,
                item_id=item_id,
                name=payload.name.strip(),
                offer_title=payload.offer_title.strip(),
                price=price,
                price_text=payload.price_text.strip(),
                currency="RUB",
                lead_time=payload.lead_time.strip(),
                country=payload.country.strip(),
                category=payload.category.strip(),
                description=payload.description.strip(),
                terms=payload.terms.strip(),
                restrictions=payload.restrictions.strip(),
                url=payload.url.strip(),
                source_url=payload.source_url.strip(),
                contact=payload.contact.strip(),
                image_url=payload.image_url.strip(),
                status=payload.status,
                is_existing=payload.is_existing,
                monitoring_enabled=payload.monitoring_enabled,
                discovered_at=now,
                updated_at=now,
                last_checked_at=None,
                last_run_id=None,
                ai_notes="",
                price_history=[{"value": str(price) if price is not None else "", "at": now}] if price is not None else [],
            )
            suppliers = self.list_suppliers()
            suppliers.append(supplier)
            self._save_suppliers(suppliers)
            return supplier

    def upsert_discovered_supplier(
        self,
        *,
        project_id: str,
        item_id: str,
        run_id: str,
        name: str,
        offer_title: str = "",
        price_text: str = "",
        price: float | None = None,
        currency: str = "RUB",
        lead_time: str = "",
        country: str = "",
        category: str = "",
        description: str = "",
        terms: str = "",
        restrictions: str = "",
        url: str = "",
        source_url: str = "",
        contact: str = "",
        image_url: str = "",
        ai_notes: str = "",
    ) -> SupplierRecord:
        with self._lock:
            now = utc_now_iso()
            suppliers = self.list_suppliers()
            url_key = normalize_url_key(url) if url else ""
            name_key = name.strip().casefold()
            existing: SupplierRecord | None = None
            for supplier in suppliers:
                if supplier.item_id != item_id:
                    continue
                if url_key and supplier.url and normalize_url_key(supplier.url) == url_key:
                    existing = supplier
                    break
                if not url_key and supplier.name.strip().casefold() == name_key:
                    existing = supplier
                    break
            resolved_price = price if price is not None else parse_price_value(price_text)
            if existing is None:
                supplier = SupplierRecord(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    item_id=item_id,
                    name=name.strip(),
                    offer_title=offer_title.strip(),
                    price=resolved_price,
                    price_text=price_text.strip(),
                    currency="RUB",
                    lead_time=lead_time.strip(),
                    country=country.strip(),
                    category=category.strip(),
                    description=description.strip(),
                    terms=terms.strip(),
                    restrictions=restrictions.strip(),
                    url=url.strip(),
                    source_url=source_url.strip(),
                    contact=contact.strip(),
                    image_url=image_url.strip(),
                    status="new",
                    is_existing=False,
                    monitoring_enabled=False,
                    discovered_at=now,
                    updated_at=now,
                    last_checked_at=now,
                    last_run_id=run_id,
                    ai_notes=ai_notes.strip(),
                    price_history=[{"value": str(resolved_price) if resolved_price is not None else "", "at": now}],
                )
                suppliers.append(supplier)
            else:
                old_price = existing.price
                history = list(existing.price_history)
                if resolved_price is not None and resolved_price != old_price:
                    history.append({"value": str(resolved_price), "at": now})
                    if old_price is not None:
                        change_type = "price_down" if resolved_price < old_price else "price_up"
                        self._append_change(
                            SupplierChange(
                                id=str(uuid.uuid4()),
                                supplier_id=existing.id,
                                project_id=existing.project_id,
                                item_id=existing.item_id,
                                supplier_name=existing.name,
                                item_name=self._item_name(existing.project_id, existing.item_id),
                                change_type=change_type,
                                old_value=f"{old_price} RUB",
                                new_value=f"{resolved_price} RUB",
                                summary=f"Цена изменилась с {old_price} на {resolved_price}",
                                detected_at=now,
                            )
                        )
                supplier = existing.model_copy(
                    update={
                        "name": name.strip() or existing.name,
                        "offer_title": offer_title.strip() or existing.offer_title,
                        "price": resolved_price if resolved_price is not None else existing.price,
                        "price_text": price_text.strip() or existing.price_text,
                        "currency": "RUB",
                        "lead_time": lead_time.strip() or existing.lead_time,
                        "country": country.strip() or existing.country,
                        "category": category.strip() or existing.category,
                        "description": description.strip() or existing.description,
                        "terms": terms.strip() or existing.terms,
                        "restrictions": restrictions.strip() or existing.restrictions,
                        "url": url.strip() or existing.url,
                        "source_url": source_url.strip() or existing.source_url,
                        "contact": contact.strip() or existing.contact,
                        "image_url": image_url.strip() or existing.image_url,
                        "ai_notes": ai_notes.strip() or existing.ai_notes,
                        "updated_at": now,
                        "last_checked_at": now,
                        "last_run_id": run_id,
                        "price_history": history,
                    }
                )
                suppliers = [supplier if item.id == existing.id else item for item in suppliers]
            self._save_suppliers(suppliers)
            return supplier

    def update_supplier(self, supplier_id: str, payload: SupplierUpdate) -> SupplierRecord:
        with self._lock:
            suppliers = self.list_suppliers()
            for index, supplier in enumerate(suppliers):
                if supplier.id != supplier_id:
                    continue
                resolved_price = payload.price if payload.price is not None else parse_price_value(payload.price_text)
                updated = supplier.model_copy(
                    update={
                        "name": payload.name.strip() or supplier.name,
                        "offer_title": payload.offer_title.strip(),
                        "price": resolved_price,
                        "price_text": payload.price_text.strip(),
                        "currency": "RUB",
                        "lead_time": payload.lead_time.strip(),
                        "country": payload.country.strip(),
                        "category": payload.category.strip(),
                        "description": payload.description.strip(),
                        "terms": payload.terms.strip(),
                        "restrictions": payload.restrictions.strip(),
                        "url": payload.url.strip(),
                        "source_url": payload.source_url.strip() or supplier.source_url,
                        "contact": payload.contact.strip(),
                        "image_url": payload.image_url.strip() or supplier.image_url,
                        "status": payload.status,
                        "is_existing": payload.is_existing,
                        "monitoring_enabled": payload.monitoring_enabled,
                        "updated_at": utc_now_iso(),
                    }
                )
                suppliers[index] = updated
                self._save_suppliers(suppliers)
                return updated
        raise KeyError(supplier_id)

    def set_supplier_monitoring(self, supplier_id: str, enabled: bool) -> SupplierRecord:
        with self._lock:
            suppliers = self.list_suppliers()
            for index, supplier in enumerate(suppliers):
                if supplier.id != supplier_id:
                    continue
                updated = supplier.model_copy(update={"monitoring_enabled": enabled, "updated_at": utc_now_iso()})
                suppliers[index] = updated
                self._save_suppliers(suppliers)
                return updated
        raise KeyError(supplier_id)

    def set_supplier_status(self, supplier_id: str, status: str) -> SupplierRecord:
        with self._lock:
            suppliers = self.list_suppliers()
            for index, supplier in enumerate(suppliers):
                if supplier.id != supplier_id:
                    continue
                updated = supplier.model_copy(update={"status": status, "updated_at": utc_now_iso()})
                suppliers[index] = updated
                self._save_suppliers(suppliers)
                return updated
        raise KeyError(supplier_id)

    def delete_supplier(self, supplier_id: str) -> None:
        with self._lock:
            suppliers = [supplier for supplier in self.list_suppliers() if supplier.id != supplier_id]
            self._save_suppliers(suppliers)

    def _item_name(self, project_id: str, item_id: str) -> str:
        try:
            project = self.get_project(project_id)
        except KeyError:
            return ""
        for item in project.items:
            if item.id == item_id:
                return item.name
        return ""

    # ----- runs and events -----

    def create_run(self, *, kind: str, label: str, project_id: str | None = None, item_id: str | None = None) -> RunRecord:
        with self._lock:
            run = RunRecord(
                id=str(uuid.uuid4()),
                kind=kind,  # type: ignore[arg-type]
                project_id=project_id,
                item_id=item_id,
                label=label,
                started_at=utc_now_iso(),
                status="queued",
                summary="",
                error=None,
            )
            self._write_json(self.runs_dir / f"{run.id}.json", run.model_dump())
            self._write_json(self.events_dir / f"{run.id}.json", [])
            return run

    def update_run(self, run_id: str, **updates: Any) -> RunRecord:
        with self._lock:
            path = self.runs_dir / f"{run_id}.json"
            run = RunRecord.model_validate(self._read_json(path, {}))
            merged = run.model_copy(update=updates)
            self._write_json(path, merged.model_dump())
            return merged

    def list_runs(self, limit: int = 80) -> list[RunRecord]:
        with self._lock:
            runs: list[RunRecord] = []
            for path in sorted(self.runs_dir.glob("*.json")):
                runs.append(RunRecord.model_validate(self._read_json(path, {})))
            runs.sort(key=lambda item: item.started_at, reverse=True)
            return runs[:limit]

    def append_run_event(
        self,
        *,
        run_id: str,
        event_type: str,
        message: str,
        project_id: str | None = None,
        item_id: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> RunEventRecord:
        with self._lock:
            path = self.events_dir / f"{run_id}.json"
            raw = self._read_json(path, [])
            event = RunEventRecord(
                id=str(uuid.uuid4()),
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type=event_type.strip(),
                message=message.strip(),
                created_at=utc_now_iso(),
                metadata=metadata or {},
            )
            raw.append(event.model_dump())
            self._write_json(path, raw)
            return event

    def list_run_events(self, run_id: str, limit: int = 300) -> list[RunEventRecord]:
        with self._lock:
            path = self.events_dir / f"{run_id}.json"
            raw = self._read_json(path, [])
            events = [RunEventRecord.model_validate(item) for item in raw]
            return events[-max(1, int(limit)) :]

    # ----- uploads -----

    def list_uploads(self) -> list[UploadRecord]:
        with self._lock:
            raw = self._read_json(self.uploads_index_path, [])
            uploads = [UploadRecord.model_validate(item) for item in raw]
            return sorted(uploads, key=lambda item: item.received_at, reverse=True)

    def _save_uploads(self, uploads: Iterable[UploadRecord]) -> None:
        self._write_json(self.uploads_index_path, [item.model_dump() for item in uploads])

    def add_upload(self, payload: UploadCreate) -> UploadRecord:
        with self._lock:
            now = utc_now_iso()
            upload = UploadRecord(
                id=str(uuid.uuid4()),
                name=payload.name.strip(),
                kind=payload.kind,
                size=len(payload.content.encode("utf-8")),
                received_at=now,
                parsed_at=None,
                status="received",
                summary="",
                error=None,
                created_project_ids=[],
            )
            (self.uploads_dir / f"{upload.id}.txt").write_text(payload.content, encoding="utf-8")
            uploads = self.list_uploads()
            uploads.append(upload)
            self._save_uploads(uploads)
            return upload

    def read_upload(self, upload_id: str) -> str:
        path = self.uploads_dir / f"{upload_id}.txt"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def update_upload(self, upload_id: str, **updates: Any) -> UploadRecord:
        with self._lock:
            uploads = self.list_uploads()
            for index, upload in enumerate(uploads):
                if upload.id != upload_id:
                    continue
                merged = upload.model_copy(update=updates)
                uploads[index] = merged
                self._save_uploads(uploads)
                return merged
        raise KeyError(upload_id)

    # ----- changes -----

    def list_changes(self, limit: int = 100) -> list[SupplierChange]:
        with self._lock:
            raw = self._read_json(self.changes_path, [])
            changes = [SupplierChange.model_validate(item) for item in raw]
            changes.sort(key=lambda item: item.detected_at, reverse=True)
            return changes[:limit]

    def _append_change(self, change: SupplierChange) -> None:
        raw = self._read_json(self.changes_path, [])
        raw.append(change.model_dump())
        self._write_json(self.changes_path, raw[-500:])

    # ----- notes (per item) -----

    def read_item_notes_file(self, item_id: str) -> tuple[str, str | None]:
        return self._read_text(self.notes_dir / f"item-{item_id}.txt")

    def write_item_notes_file(self, item_id: str, content: str) -> str:
        return self._write_text(self.notes_dir / f"item-{item_id}.txt", content)

    # ----- monitoring scheduler helpers -----

    def items_due_for_monitoring(self, interval_hours: int) -> list[tuple[ProjectRecord, ProjectItem]]:
        threshold = utc_now() - timedelta(hours=max(1, interval_hours))
        due: list[tuple[ProjectRecord, ProjectItem]] = []
        for project in self.list_projects():
            for item in project.items:
                if not item.monitoring_enabled:
                    continue
                monitored_suppliers = [
                    supplier
                    for supplier in self.list_suppliers_for_item(item.id)
                    if supplier.monitoring_enabled
                ]
                if not monitored_suppliers:
                    continue
                last_checks = [supplier.last_checked_at for supplier in monitored_suppliers if supplier.last_checked_at]
                if not last_checks:
                    due.append((project, item))
                    continue
                latest = max(self._parse_iso(value) for value in last_checks)
                if latest <= threshold:
                    due.append((project, item))
        return due

    # ----- aggregate state -----

    def compute_stats(self) -> dict[str, float | int | str]:
        suppliers = self.list_suppliers()
        projects = self.list_projects()
        monitored = [supplier for supplier in suppliers if supplier.monitoring_enabled]
        spent_total = 0.0
        baseline_total = 0.0
        savings_total = 0.0
        currency = "RUB"
        active_projects = sum(1 for project in projects if project.status in {"planning", "in_progress", "review"})
        for project in projects:
            for item in project.items:
                item_suppliers = [
                    supplier for supplier in suppliers if supplier.item_id == item.id and supplier.price is not None
                ]
                if not item_suppliers:
                    continue
                preferred = [
                    supplier
                    for supplier in item_suppliers
                    if supplier.is_existing or supplier.status in {"preferred", "verified"}
                ]
                baseline_price = max(
                    (supplier.price for supplier in (preferred or item_suppliers) if supplier.price is not None),
                    default=0.0,
                )
                best_price = min(
                    (supplier.price for supplier in item_suppliers if supplier.price is not None),
                    default=0.0,
                )
                quantity = float(item.quantity or 0)
                spent_total += best_price * quantity
                baseline_total += baseline_price * quantity
                savings_total += max(0.0, (baseline_price - best_price) * quantity)
        return {
            "currency": currency,
            "projects_total": len(projects),
            "projects_active": active_projects,
            "items_total": sum(len(project.items) for project in projects),
            "suppliers_total": len(suppliers),
            "suppliers_monitored": len(monitored),
            "spent_estimate": round(spent_total, 2),
            "baseline_estimate": round(baseline_total, 2),
            "savings_estimate": round(savings_total, 2),
            "savings_pct": round((savings_total / baseline_total) * 100, 1) if baseline_total else 0.0,
        }

    def build_state(self) -> dict[str, Any]:
        return {
            "config": self.load_config(),
            "projects": self.list_projects(),
            "suppliers": self.list_suppliers(),
            "runs": self.list_runs(),
            "uploads": self.list_uploads(),
            "changes": self.list_changes(),
            "stats": self.compute_stats(),
        }
