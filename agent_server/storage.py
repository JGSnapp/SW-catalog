from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse, urlunparse

try:
    from .models import (
        AppConfig,
        GrantRecord,
        ProductComponent,
        ProductCreate,
        ProductRecord,
        ProductUpdate,
        RunEventRecord,
        RunRecord,
        SiteConfig,
        SiteCreate,
        SiteUpdate,
        SourceCandidate,
        utc_now,
        utc_now_iso,
    )
except ImportError:  # pragma: no cover
    from models import (
        AppConfig,
        GrantRecord,
        ProductComponent,
        ProductCreate,
        ProductRecord,
        ProductUpdate,
        RunEventRecord,
        RunRecord,
        SiteConfig,
        SiteCreate,
        SiteUpdate,
        SourceCandidate,
        utc_now,
        utc_now_iso,
    )


class JsonStorage:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.notes_dir = data_dir / "notes"
        self.status_dir = data_dir / "status"
        self.runs_dir = data_dir / "runs"
        self.events_dir = data_dir / "events"
        self.config_path = data_dir / "config.json"
        self.grants_path = data_dir / "grants.json"
        self.products_path = data_dir / "products.json"
        self.source_candidates_path = data_dir / "source_candidates.json"
        self._lock = threading.RLock()
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self._write_json(self.config_path, AppConfig().model_dump())
        if not self.grants_path.exists():
            self._write_json(self.grants_path, [])
        if not self.products_path.exists():
            self._write_json(self.products_path, [])
        if not self.source_candidates_path.exists():
            self._write_json(self.source_candidates_path, [])

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)

    @staticmethod
    def _grant_dedupe_key(site_id: str, source: str, title: str) -> tuple[str, str, str]:
        normalized_title = re.sub(r"\s+", " ", title.strip().casefold())
        return site_id.strip(), source.strip(), normalized_title

    @staticmethod
    def normalize_url_key(url: str) -> str:
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower() or "https"
        netloc = parsed.netloc.lower()
        path = re.sub(r"/+$", "", parsed.path or "")
        return urlunparse((scheme, netloc, path, "", "", ""))

    def load_config(self) -> AppConfig:
        with self._lock:
            return AppConfig.model_validate(self._read_json(self.config_path, AppConfig().model_dump()))

    def save_config(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._write_json(self.config_path, config.model_dump())
            return config

    def replace_config(self, config: AppConfig) -> AppConfig:
        now = utc_now_iso()
        sites: list[SiteConfig] = []
        for site in config.sites:
            sites.append(
                SiteConfig(
                    id=site.id,
                    label=site.label,
                    url=site.url,
                    enabled=site.enabled,
                    created_at=site.created_at,
                    updated_at=now,
                    last_run_at=site.last_run_at,
                    next_run_at=site.next_run_at,
                )
            )
        return self.save_config(
            AppConfig(
                company_profile=config.company_profile,
                global_prompt=config.global_prompt,
                target_institutions=config.target_institutions,
                search_directions=config.search_directions,
                min_amount=config.min_amount,
                max_amount=config.max_amount,
                funding_types=config.funding_types,
                regions=config.regions,
                deadline_window=config.deadline_window,
                eligibility_requirements=config.eligibility_requirements,
                excluded_restrictions=config.excluded_restrictions,
                keywords=config.keywords,
                interval_hours=config.interval_hours,
                iterations_per_site=config.iterations_per_site,
                source_discovery_enabled=config.source_discovery_enabled,
                source_discovery_interval_hours=config.source_discovery_interval_hours,
                source_discovery_iterations=config.source_discovery_iterations,
                source_discovery_last_run_at=config.source_discovery_last_run_at,
                source_discovery_next_run_at=config.source_discovery_next_run_at,
                sites=sites,
            )
        )

    def add_site(self, payload: SiteCreate) -> SiteConfig:
        with self._lock:
            config = self.load_config()
            now = utc_now_iso()
            site = SiteConfig(
                id=str(uuid.uuid4()),
                label=payload.label.strip(),
                url=payload.url.strip(),
                enabled=payload.enabled,
                created_at=now,
                updated_at=now,
                last_run_at=None,
                next_run_at=now if payload.enabled else None,
            )
            config.sites.append(site)
            self.save_config(config)
            return site

    def update_site(self, site_id: str, payload: SiteUpdate) -> SiteConfig:
        with self._lock:
            config = self.load_config()
            for index, site in enumerate(config.sites):
                if site.id != site_id:
                    continue
                updated = site.model_copy(
                    update={
                        "label": payload.label.strip(),
                        "url": payload.url.strip(),
                        "enabled": payload.enabled,
                        "updated_at": utc_now_iso(),
                        "next_run_at": site.next_run_at or utc_now_iso() if payload.enabled else None,
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
            grants = [grant for grant in self.list_grants() if grant.site_id != site_id]
            self._write_json(self.grants_path, [grant.model_dump() for grant in grants])
            self._delete_if_exists(self.notes_dir / f"{site_id}.txt")
            self._delete_if_exists(self.status_dir / f"{site_id}.txt")

    def get_site(self, site_id: str) -> SiteConfig:
        config = self.load_config()
        for site in config.sites:
            if site.id == site_id:
                return site
        raise KeyError(site_id)

    def due_sites(self) -> list[SiteConfig]:
        config = self.load_config()
        now = utc_now()
        return [
            site
            for site in config.sites
            if site.enabled and (site.next_run_at is None or self._parse_iso(site.next_run_at) <= now)
        ]

    def is_source_discovery_due(self) -> bool:
        config = self.load_config()
        if not config.source_discovery_enabled:
            return False
        if not config.source_discovery_next_run_at:
            return True
        return self._parse_iso(config.source_discovery_next_run_at) <= utc_now()

    def mark_source_discovery_started(self) -> AppConfig:
        with self._lock:
            config = self.load_config()
            updated = config.model_copy(update={"source_discovery_last_run_at": utc_now_iso()})
            self.save_config(updated)
            return updated

    def mark_source_discovery_finished(self, interval_hours: int) -> AppConfig:
        with self._lock:
            now = utc_now()
            next_run_at = (now + timedelta(hours=interval_hours)).replace(microsecond=0).isoformat() + "Z"
            config = self.load_config()
            updated = config.model_copy(update={"source_discovery_next_run_at": next_run_at})
            self.save_config(updated)
            return updated

    def mark_site_run_started(self, site_id: str) -> SiteConfig:
        with self._lock:
            config = self.load_config()
            now = utc_now_iso()
            for index, site in enumerate(config.sites):
                if site.id != site_id:
                    continue
                updated = site.model_copy(update={"last_run_at": now, "updated_at": now})
                config.sites[index] = updated
                self.save_config(config)
                return updated
        raise KeyError(site_id)

    def mark_site_run_finished(self, site_id: str, interval_hours: int) -> SiteConfig:
        with self._lock:
            config = self.load_config()
            now = utc_now()
            next_run_at = (now + timedelta(hours=interval_hours)).replace(microsecond=0).isoformat() + "Z"
            for index, site in enumerate(config.sites):
                if site.id != site_id:
                    continue
                updated = site.model_copy(
                    update={
                        "updated_at": utc_now_iso(),
                        "next_run_at": next_run_at if site.enabled else None,
                    }
                )
                config.sites[index] = updated
                self.save_config(config)
                return updated
        raise KeyError(site_id)

    def list_grants(self) -> list[GrantRecord]:
        with self._lock:
            raw = self._read_json(self.grants_path, [])
            grants = [GrantRecord.model_validate(item) for item in raw]
            return sorted(grants, key=lambda item: item.updated_at, reverse=True)

    def list_products(self) -> list[ProductRecord]:
        with self._lock:
            raw = self._read_json(self.products_path, [])
            products = [ProductRecord.model_validate(item) for item in raw]
            return sorted(products, key=lambda item: item.updated_at, reverse=True)

    def add_product(self, payload: ProductCreate) -> ProductRecord:
        with self._lock:
            now = utc_now_iso()
            product = ProductRecord(
                id=str(uuid.uuid4()),
                name=payload.name.strip(),
                description=payload.description.strip(),
                target_volume=payload.target_volume.strip(),
                components=self._normalize_components(payload.components),
                created_at=now,
                updated_at=now,
            )
            products = self.list_products()
            products.append(product)
            self._write_json(self.products_path, [item.model_dump() for item in products])
            return product

    def update_product(self, product_id: str, payload: ProductUpdate) -> ProductRecord:
        with self._lock:
            products = self.list_products()
            for index, product in enumerate(products):
                if product.id != product_id:
                    continue
                updated = product.model_copy(
                    update={
                        "name": payload.name.strip(),
                        "description": payload.description.strip(),
                        "target_volume": payload.target_volume.strip(),
                        "components": self._normalize_components(payload.components),
                        "updated_at": utc_now_iso(),
                    }
                )
                products[index] = updated
                self._write_json(self.products_path, [item.model_dump() for item in products])
                return updated
        raise KeyError(product_id)

    def delete_product(self, product_id: str) -> None:
        with self._lock:
            products = [product for product in self.list_products() if product.id != product_id]
            self._write_json(self.products_path, [item.model_dump() for item in products])

    def _normalize_components(self, components: list[ProductComponent]) -> list[ProductComponent]:
        normalized: list[ProductComponent] = []
        for component in components:
            name = component.name.strip()
            if not name:
                continue
            normalized.append(
                ProductComponent(
                    id=component.id or str(uuid.uuid4()),
                    name=name,
                    specification=component.specification.strip(),
                    quantity=component.quantity,
                    target_price=component.target_price.strip(),
                    notes=component.notes.strip(),
                )
            )
        return normalized

    def upsert_grant(
        self,
        *,
        site_id: str,
        site_url: str,
        run_id: str,
        title: str,
        institution: str = "",
        amount: str = "",
        funding_type: str = "",
        category: str = "",
        conditions: str = "",
        restrictions: str = "",
        deadline: str = "",
        application_url: str = "",
        site: str = "",
        description: str = "",
        fit_reason: str = "",
        how_to_apply: str = "",
        source: str = "",
    ) -> GrantRecord:
        with self._lock:
            now = utc_now_iso()
            grants = self.list_grants()
            dedupe_key = self._grant_dedupe_key(site_id, source, title)
            existing = next(
                (
                    grant
                    for grant in grants
                    if self._grant_dedupe_key(grant.site_id, grant.source, grant.title) == dedupe_key
                ),
                None,
            )
            if existing is None:
                grant = GrantRecord(
                    id=str(uuid.uuid4()),
                    title=title.strip(),
                    institution=institution.strip(),
                    amount=amount.strip(),
                    funding_type=funding_type.strip(),
                    category=category.strip(),
                    conditions=conditions.strip(),
                    restrictions=restrictions.strip(),
                    deadline=deadline.strip(),
                    application_url=application_url.strip(),
                    status="new",
                    site=site.strip(),
                    site_id=site_id,
                    description=description.strip(),
                    fit_reason=fit_reason.strip(),
                    how_to_apply=how_to_apply.strip(),
                    source=source.strip(),
                    site_url=site_url,
                    discovered_at=now,
                    updated_at=now,
                    last_run_id=run_id,
                    telegram_notified_at=None,
                )
                grants.append(grant)
            else:
                grant = existing.model_copy(
                    update={
                        "title": title.strip(),
                        "institution": institution.strip(),
                        "amount": amount.strip(),
                        "funding_type": funding_type.strip(),
                        "category": category.strip(),
                        "conditions": conditions.strip(),
                        "restrictions": restrictions.strip(),
                        "deadline": deadline.strip(),
                        "application_url": application_url.strip(),
                        "site": site.strip(),
                        "description": description.strip(),
                        "fit_reason": fit_reason.strip(),
                        "how_to_apply": how_to_apply.strip(),
                        "site_id": site_id,
                        "site_url": site_url,
                        "updated_at": now,
                        "last_run_id": run_id,
                    }
                )
                grants = [
                    grant
                    if self._grant_dedupe_key(item.site_id, item.source, item.title) == dedupe_key
                    else item
                    for item in grants
                ]
            self._write_json(self.grants_path, [item.model_dump() for item in grants])
            return grant

    def list_source_candidates(self) -> list[SourceCandidate]:
        with self._lock:
            raw = self._read_json(self.source_candidates_path, [])
            candidates = [SourceCandidate.model_validate(item) for item in raw]
            return sorted(candidates, key=lambda item: item.updated_at, reverse=True)

    def list_active_source_candidates(self) -> list[SourceCandidate]:
        return [candidate for candidate in self.list_source_candidates() if candidate.status == "new"]

    def known_source_url_keys(self) -> set[str]:
        config = self.load_config()
        keys = {self.normalize_url_key(site.url) for site in config.sites}
        keys.update(self.normalize_url_key(candidate.url) for candidate in self.list_source_candidates())
        return keys

    def upsert_source_candidate(
        self,
        *,
        run_id: str,
        label: str,
        url: str,
        reason: str = "",
        evidence: str = "",
    ) -> SourceCandidate:
        with self._lock:
            now = utc_now_iso()
            url_key = self.normalize_url_key(url)
            candidates = self.list_source_candidates()
            existing = next((item for item in candidates if self.normalize_url_key(item.url) == url_key), None)
            if existing is None:
                candidate = SourceCandidate(
                    id=str(uuid.uuid4()),
                    label=label.strip(),
                    url=url.strip(),
                    reason=reason.strip(),
                    evidence=evidence.strip(),
                    status="new",
                    discovered_at=now,
                    updated_at=now,
                    last_run_id=run_id,
                    telegram_notified_at=None,
                )
                candidates.append(candidate)
            else:
                candidate = existing.model_copy(
                    update={
                        "label": label.strip() or existing.label,
                        "reason": reason.strip() or existing.reason,
                        "evidence": evidence.strip() or existing.evidence,
                        "updated_at": now,
                        "last_run_id": run_id,
                    }
                )
                candidates = [candidate if item.id == existing.id else item for item in candidates]
            self._write_json(self.source_candidates_path, [item.model_dump() for item in candidates])
            return candidate

    def update_source_candidate_status(self, candidate_id: str, status: str) -> SourceCandidate:
        with self._lock:
            candidates = self.list_source_candidates()
            for index, candidate in enumerate(candidates):
                if candidate.id != candidate_id:
                    continue
                updated = candidate.model_copy(update={"status": status, "updated_at": utc_now_iso()})
                candidates[index] = updated
                self._write_json(self.source_candidates_path, [item.model_dump() for item in candidates])
                return updated
        raise KeyError(candidate_id)

    def add_site_from_candidate(self, candidate_id: str) -> SiteConfig:
        with self._lock:
            candidates = self.list_source_candidates()
            candidate = next((item for item in candidates if item.id == candidate_id), None)
            if candidate is None:
                raise KeyError(candidate_id)
            site = self.add_site(SiteCreate(label=candidate.label, url=candidate.url, enabled=True))
            self.update_source_candidate_status(candidate.id, "added")
            return site

    def list_unnotified_source_candidates_for_run(self, run_id: str) -> list[SourceCandidate]:
        with self._lock:
            return [
                candidate
                for candidate in self.list_source_candidates()
                if candidate.last_run_id == run_id and candidate.telegram_notified_at is None and candidate.status == "new"
            ]

    def mark_source_candidate_telegram_notified(self, candidate_id: str) -> SourceCandidate:
        with self._lock:
            now = utc_now_iso()
            candidates = self.list_source_candidates()
            for index, candidate in enumerate(candidates):
                if candidate.id != candidate_id:
                    continue
                updated = candidate.model_copy(update={"telegram_notified_at": now})
                candidates[index] = updated
                self._write_json(self.source_candidates_path, [item.model_dump() for item in candidates])
                return updated
        raise KeyError(candidate_id)

    def update_grant_status(self, grant_id: str, status: str) -> GrantRecord:
        with self._lock:
            grants = self.list_grants()
            for index, grant in enumerate(grants):
                if grant.id != grant_id:
                    continue
                updated = grant.model_copy(update={"status": status, "updated_at": utc_now_iso()})
                grants[index] = updated
                self._write_json(self.grants_path, [item.model_dump() for item in grants])
                return updated
        raise KeyError(grant_id)

    def list_unnotified_grants_for_run(self, run_id: str) -> list[GrantRecord]:
        with self._lock:
            return [
                grant
                for grant in self.list_grants()
                if grant.last_run_id == run_id and grant.telegram_notified_at is None
            ]

    def mark_grant_telegram_notified(self, grant_id: str) -> GrantRecord:
        with self._lock:
            now = utc_now_iso()
            grants = self.list_grants()
            for index, grant in enumerate(grants):
                if grant.id != grant_id:
                    continue
                updated = grant.model_copy(update={"telegram_notified_at": now})
                grants[index] = updated
                self._write_json(self.grants_path, [item.model_dump() for item in grants])
                return updated
        raise KeyError(grant_id)

    def create_run(self, site_id: str, site_url: str) -> RunRecord:
        with self._lock:
            run = RunRecord(
                id=str(uuid.uuid4()),
                site_id=site_id,
                site_url=site_url,
                started_at=utc_now_iso(),
                status="queued",
                summary="",
                error=None,
            )
            self._write_json(self.runs_dir / f"{run.id}.json", run.model_dump())
            self._write_json(self.events_dir / f"{run.id}.json", [])
            return run

    def update_run(self, run_id: str, **updates) -> RunRecord:
        with self._lock:
            path = self.runs_dir / f"{run_id}.json"
            run = RunRecord.model_validate(self._read_json(path, {}))
            merged = run.model_copy(update=updates)
            self._write_json(path, merged.model_dump())
            return merged

    def list_runs(self, limit: int = 50) -> list[RunRecord]:
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
        site_id: str,
        site_url: str,
        event_type: str,
        message: str,
        metadata: dict[str, str] | None = None,
    ) -> RunEventRecord:
        with self._lock:
            path = self.events_dir / f"{run_id}.json"
            raw = self._read_json(path, [])
            event = RunEventRecord(
                id=str(uuid.uuid4()),
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
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

    def read_notes(self, site_id: str) -> tuple[str, str | None]:
        return self._read_text(self.notes_dir / f"{site_id}.txt")

    def write_notes(self, site_id: str, content: str) -> str:
        return self._write_text(self.notes_dir / f"{site_id}.txt", content)

    def read_status(self, site_id: str) -> tuple[str, str | None]:
        return self._read_text(self.status_dir / f"{site_id}.txt")

    def write_status(self, site_id: str, content: str) -> str:
        return self._write_text(self.status_dir / f"{site_id}.txt", content)

    def build_state(self):
        return {
            "config": self.load_config(),
            "grants": self.list_grants(),
            "runs": self.list_runs(),
            "source_candidates": self.list_source_candidates(),
            "products": self.list_products(),
        }

    def _read_text(self, path: Path) -> tuple[str, str | None]:
        with self._lock:
            if not path.exists():
                return "", None
            return path.read_text(encoding="utf-8"), self._mtime(path)

    def _write_text(self, path: Path, content: str) -> str:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.strip() + "\n" if content.strip() else "", encoding="utf-8")
            return self._mtime(path) or utc_now_iso()

    def _mtime(self, path: Path) -> str | None:
        if not path.exists():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat() + "Z"

    def _parse_iso(self, value: str):
        return datetime.fromisoformat(value.replace("Z", ""))

    def _delete_if_exists(self, path: Path) -> None:
        if path.exists():
            path.unlink()
