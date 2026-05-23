from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, RunContextWrapper, Runner, function_tool, set_default_openai_client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from openai import AsyncOpenAI

try:
    from .models import (
        AppConfig,
        AppState,
        GrantStatusUpdate,
        ProductCreate,
        ProductUpdate,
        SiteCreate,
        SiteTextResponse,
        SiteUpdate,
        SourceCandidateStatusUpdate,
        utc_now_iso,
    )
    from .prompts import SITE_AGENT_PROMPT, SOURCE_DISCOVERY_PROMPT
    from .storage import JsonStorage
    from .telegram import TelegramNotifier
    from .tools import read_url, search_web
except ImportError:  # pragma: no cover
    from models import (
        AppConfig,
        AppState,
        GrantStatusUpdate,
        ProductCreate,
        ProductUpdate,
        SiteCreate,
        SiteTextResponse,
        SiteUpdate,
        SourceCandidateStatusUpdate,
        utc_now_iso,
    )
    from prompts import SITE_AGENT_PROMPT, SOURCE_DISCOVERY_PROMPT
    from storage import JsonStorage
    from telegram import TelegramNotifier
    from tools import read_url, search_web

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
FINALIZATION_TURN_BUFFER = 4


def configure_openai_client() -> None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("PROXY_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("PROXY_BASE_URL")
    if not api_key:
        return
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    set_default_openai_client(AsyncOpenAI(**client_kwargs), use_for_tracing=False)


@dataclass
class SiteRunContext:
    storage: JsonStorage
    site_id: str
    site_label: str
    site_url: str
    run_id: str


def build_site_agent(model_name: str) -> Agent[SiteRunContext]:
    def log_tool_event(
        ctx: RunContextWrapper[SiteRunContext],
        *,
        event_type: str,
        message: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        ctx.context.storage.append_run_event(
            run_id=ctx.context.run_id,
            site_id=ctx.context.site_id,
            site_url=ctx.context.site_url,
            event_type=event_type,
            message=message,
            metadata=metadata,
        )

    @function_tool
    async def read_site_url(ctx: RunContextWrapper[SiteRunContext], url: str, timeout: int = 60):
        """Read the content of a specific page URL through the configured reader chain."""
        log_tool_event(ctx, event_type="read_url_start", message=f"Открываю страницу: {url}", metadata={"url": url})
        try:
            result = await read_url(url=url, timeout=timeout)
            log_tool_event(
                ctx,
                event_type="read_url_done",
                message=f"Страница прочитана: {url}",
                metadata={
                    "url": url,
                    "status_code": str(result.get("status_code", "")),
                    "reader": str(result.get("reader", "")),
                },
            )
            return result
        except Exception as exc:
            # Tool errors should not crash the run; return a structured blocker for the agent.
            log_tool_event(
                ctx,
                event_type="read_url_error",
                message=f"Ошибка чтения страницы: {url}",
                metadata={"url": url, "error": str(exc)},
            )
            return {"url": url, "ok": False, "error": str(exc)}

    @function_tool
    async def search_site_web(
        ctx: RunContextWrapper[SiteRunContext],
        query: str,
        docs: int = 10,
        maxpassages: int = 5,
        lr: int | None = None,
        groupby: str = "10",
    ):
        """Search the internet through the configured XMLSearch/XMLStock service. This is not a site parser; pass only a search query, not a website endpoint."""
        log_tool_event(ctx, event_type="search_start", message=f"Поисковый запрос: {query}", metadata={"query": query})
        try:
            result = await search_web(
                query=query,
                docs=docs,
                maxpassages=maxpassages,
                lr=lr,
                groupby=groupby,
            )
            log_tool_event(
                ctx,
                event_type="search_done",
                message=f"Получено результатов поиска: {len(result)}",
                metadata={"query": query, "results": str(len(result))},
            )
            return result
        except Exception as exc:
            log_tool_event(
                ctx,
                event_type="search_error",
                message=f"Ошибка поиска: {query}",
                metadata={"query": query, "error": str(exc)},
            )
            return [{"ok": False, "query": query, "error": str(exc)}]

    @function_tool
    def write_notes(ctx: RunContextWrapper[SiteRunContext], content: str) -> str:
        """Replace the reusable notes for this site with a full updated memo."""
        log_tool_event(ctx, event_type="notes_write", message="Обновлены заметки для сайта.")
        return ctx.context.storage.write_notes(ctx.context.site_id, content)

    @function_tool
    def write_status(ctx: RunContextWrapper[SiteRunContext], content: str) -> str:
        """Replace the current site status report with a short full summary of this run."""
        log_tool_event(ctx, event_type="status_write", message="Обновлен статус текущего прогона.")
        return ctx.context.storage.write_status(ctx.context.site_id, content)

    @function_tool
    def add_grant(
        ctx: RunContextWrapper[SiteRunContext],
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
        ) -> dict[str, str]:
        """Save or update a found supplier offer. Include price, terms, component fit, and how to buy/contact."""
        record = ctx.context.storage.upsert_grant(
            site_id=ctx.context.site_id,
            site_url=ctx.context.site_url,
            run_id=ctx.context.run_id,
            title=title,
            institution=institution,
            amount=amount,
            funding_type=funding_type,
            category=category,
            conditions=conditions,
            restrictions=restrictions,
            deadline=deadline,
            application_url=application_url,
            site=site,
            description=description,
            fit_reason=fit_reason,
            how_to_apply=how_to_apply,
            source=source,
        )
        log_tool_event(
            ctx,
            event_type="grant_upsert",
            message=f"Добавлен или обновлен поставщик: {title.strip()}",
            metadata={"source": source.strip(), "grant_id": record.id},
        )
        return {"grant_id": record.id, "updated_at": record.updated_at}

    def instructions(ctx: RunContextWrapper[SiteRunContext], _agent: Agent[SiteRunContext]) -> str:
        config = ctx.context.storage.load_config()
        notes, _ = ctx.context.storage.read_notes(ctx.context.site_id)
        status, _ = ctx.context.storage.read_status(ctx.context.site_id)
        return "\n\n".join(
            [
                SITE_AGENT_PROMPT.strip(),
                f"Product and procurement profile:\n{config.company_profile or 'Not provided.'}",
                "Structured supplier search settings:\n"
                f"- Preferred vendors/platforms: {config.target_institutions or 'Not provided.'}\n"
                f"- Product groups/components: {config.search_directions or 'Not provided.'}\n"
                f"- Minimum price/lot: {config.min_amount or 'Not provided.'}\n"
                f"- Maximum price/budget: {config.max_amount or 'Not provided.'}\n"
                f"- Supplier types: {config.funding_types or 'Not provided.'}\n"
                f"- Regions/delivery geography: {config.regions or 'Not provided.'}\n"
                f"- Delivery window: {config.deadline_window or 'Not provided.'}\n"
                f"- Required supplier terms: {config.eligibility_requirements or 'Not provided.'}\n"
                f"- Excluded terms: {config.excluded_restrictions or 'Not provided.'}\n"
                f"- Keywords, article numbers, specs: {config.keywords or 'Not provided.'}",
                f"Global prompt:\n{config.global_prompt or 'Not provided.'}",
                f"Target site label: {ctx.context.site_label}",
                f"Target site URL: {ctx.context.site_url}",
                f"Previous status:\n{status or 'No previous status.'}",
                f"Reusable notes:\n{notes or 'No notes yet.'}",
            ]
        )

    return Agent[SiteRunContext](
        name="Supplier Site Agent",
        instructions=instructions,
        model=model_name,
        tools=[read_site_url, search_site_web, write_notes, write_status, add_grant],
    )


def build_source_discovery_agent(model_name: str) -> Agent[SiteRunContext]:
    def log_tool_event(
        ctx: RunContextWrapper[SiteRunContext],
        *,
        event_type: str,
        message: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        ctx.context.storage.append_run_event(
            run_id=ctx.context.run_id,
            site_id=ctx.context.site_id,
            site_url=ctx.context.site_url,
            event_type=event_type,
            message=message,
            metadata=metadata,
        )

    @function_tool
    async def read_site_url(ctx: RunContextWrapper[SiteRunContext], url: str, timeout: int = 60):
        """Read the content of a specific page URL through the configured reader chain."""
        log_tool_event(ctx, event_type="read_url_start", message=f"Открываю страницу: {url}", metadata={"url": url})
        try:
            result = await read_url(url=url, timeout=timeout)
            log_tool_event(
                ctx,
                event_type="read_url_done",
                message=f"Страница прочитана: {url}",
                metadata={
                    "url": url,
                    "status_code": str(result.get("status_code", "")),
                    "reader": str(result.get("reader", "")),
                },
            )
            return result
        except Exception as exc:
            log_tool_event(
                ctx,
                event_type="read_url_error",
                message=f"Ошибка чтения страницы: {url}",
                metadata={"url": url, "error": str(exc)},
            )
            return {"url": url, "ok": False, "error": str(exc)}

    @function_tool
    async def search_site_web(
        ctx: RunContextWrapper[SiteRunContext],
        query: str,
        docs: int = 10,
        maxpassages: int = 5,
        lr: int | None = None,
        groupby: str = "10",
    ):
        """Search the internet through the configured XMLSearch/XMLStock service."""
        log_tool_event(ctx, event_type="search_start", message=f"Поисковый запрос: {query}", metadata={"query": query})
        try:
            result = await search_web(query=query, docs=docs, maxpassages=maxpassages, lr=lr, groupby=groupby)
            log_tool_event(
                ctx,
                event_type="search_done",
                message=f"Получено результатов поиска: {len(result)}",
                metadata={"query": query, "results": str(len(result))},
            )
            return result
        except Exception as exc:
            log_tool_event(
                ctx,
                event_type="search_error",
                message=f"Ошибка поиска: {query}",
                metadata={"query": query, "error": str(exc)},
            )
            return [{"ok": False, "query": query, "error": str(exc)}]

    @function_tool
    def add_source_candidate(
        ctx: RunContextWrapper[SiteRunContext],
        label: str,
        url: str,
        reason: str,
        evidence: str = "",
    ) -> dict[str, str]:
        """Save a verified new recurring supplier source candidate if it is not already known."""
        known = ctx.context.storage.known_source_url_keys()
        if ctx.context.storage.normalize_url_key(url) in known:
            log_tool_event(
                ctx,
                event_type="source_candidate_duplicate",
                message=f"Источник уже есть в списке: {url}",
                metadata={"url": url},
            )
            return {"ok": "false", "duplicate": "true", "url": url}
        candidate = ctx.context.storage.upsert_source_candidate(
            run_id=ctx.context.run_id,
            label=label,
            url=url,
            reason=reason,
            evidence=evidence,
        )
        log_tool_event(
            ctx,
            event_type="source_candidate_upsert",
            message=f"Найден новый потенциальный источник: {candidate.label}",
            metadata={"candidate_id": candidate.id, "url": candidate.url},
        )
        return {"candidate_id": candidate.id, "updated_at": candidate.updated_at}

    @function_tool
    def write_discovery_notes(ctx: RunContextWrapper[SiteRunContext], content: str) -> str:
        """Replace reusable notes for source discovery."""
        log_tool_event(ctx, event_type="notes_write", message="Обновлены заметки поиска источников.")
        return ctx.context.storage.write_notes(ctx.context.site_id, content)

    @function_tool
    def write_discovery_status(ctx: RunContextWrapper[SiteRunContext], content: str) -> str:
        """Replace current status for source discovery."""
        log_tool_event(ctx, event_type="status_write", message="Обновлен статус поиска источников.")
        return ctx.context.storage.write_status(ctx.context.site_id, content)

    def instructions(ctx: RunContextWrapper[SiteRunContext], _agent: Agent[SiteRunContext]) -> str:
        config = ctx.context.storage.load_config()
        notes, _ = ctx.context.storage.read_notes(ctx.context.site_id)
        status, _ = ctx.context.storage.read_status(ctx.context.site_id)
        known_sources = "\n".join(f"- {site.label}: {site.url}" for site in config.sites) or "No known sources."
        active_candidates = ctx.context.storage.list_active_source_candidates()
        pending_sources = "\n".join(f"- {item.label}: {item.url}" for item in active_candidates) or "No pending candidates."
        return "\n\n".join(
            [
                SOURCE_DISCOVERY_PROMPT.strip(),
                f"Product and procurement profile:\n{config.company_profile or 'Not provided.'}",
                "Structured supplier search settings:\n"
                f"- Preferred vendors/platforms: {config.target_institutions or 'Not provided.'}\n"
                f"- Product groups/components: {config.search_directions or 'Not provided.'}\n"
                f"- Minimum price/lot: {config.min_amount or 'Not provided.'}\n"
                f"- Maximum price/budget: {config.max_amount or 'Not provided.'}\n"
                f"- Supplier types: {config.funding_types or 'Not provided.'}\n"
                f"- Regions/delivery geography: {config.regions or 'Not provided.'}\n"
                f"- Delivery window: {config.deadline_window or 'Not provided.'}\n"
                f"- Required supplier terms: {config.eligibility_requirements or 'Not provided.'}\n"
                f"- Excluded terms: {config.excluded_restrictions or 'Not provided.'}\n"
                f"- Keywords, article numbers, specs: {config.keywords or 'Not provided.'}",
                f"Global prompt:\n{config.global_prompt or 'Not provided.'}",
                f"Known recurring sources:\n{known_sources}",
                f"Pending source candidates:\n{pending_sources}",
                f"Previous discovery status:\n{status or 'No previous status.'}",
                f"Reusable discovery notes:\n{notes or 'No notes yet.'}",
            ]
        )

    return Agent[SiteRunContext](
        name="Supplier Source Discovery Agent",
        instructions=instructions,
        model=model_name,
        tools=[read_site_url, search_site_web, add_source_candidate, write_discovery_notes, write_discovery_status],
    )


class RunManager:
    def __init__(self, storage: JsonStorage, model_name: str, telegram_notifier: TelegramNotifier | None = None):
        self.storage = storage
        self.model_name = model_name
        self.telegram_notifier = telegram_notifier or TelegramNotifier()
        self.scheduler = AsyncIOScheduler()
        self._running_sites: set[str] = set()
        self._source_discovery_running = False
        self._scheduler_lock = asyncio.Lock()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.add_job(self._scheduled_run_loop, "interval", minutes=1, id="supplier-scheduler", replace_existing=True)
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def is_site_running(self, site_id: str) -> bool:
        return site_id in self._running_sites

    def is_source_discovery_running(self) -> bool:
        return self._source_discovery_running

    async def trigger_site(self, site_id: str) -> str:
        if self.is_site_running(site_id):
            raise ValueError("site is already running")
        site = self.storage.get_site(site_id)
        run = self.storage.create_run(site.id, site.url)
        asyncio.create_task(self._run_site(site_id=site.id, run_id=run.id))
        return run.id

    async def trigger_source_discovery(self) -> str:
        if self.is_source_discovery_running():
            raise ValueError("source discovery is already running")
        run = self.storage.create_run("__source_discovery__", "source-discovery")
        asyncio.create_task(self._run_source_discovery(run.id))
        return run.id

    async def _scheduled_run_loop(self) -> None:
        async with self._scheduler_lock:
            if self.storage.is_source_discovery_due() and not self.is_source_discovery_running():
                run = self.storage.create_run("__source_discovery__", "source-discovery")
                await self._run_source_discovery(run.id)
            for site in self.storage.due_sites():
                if self.is_site_running(site.id):
                    continue
                run = self.storage.create_run(site.id, site.url)
                await self._run_site(site_id=site.id, run_id=run.id)

    async def _run_site(self, site_id: str, run_id: str) -> None:
        if site_id in self._running_sites:
            return
        self._running_sites.add(site_id)
        site_url_for_events = ""
        try:
            site = self.storage.mark_site_run_started(site_id)
            site_url_for_events = site.url
            self.storage.update_run(run_id, status="running")
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site.id,
                site_url=site.url,
                event_type="run_started",
                message=f"Старт проверки площадки: {site.label} ({site.url})",
                metadata={"site_label": site.label},
            )
            summary = await self._run_agent_for_site(site_id=site.id, site_label=site.label, site_url=site.url, run_id=run_id)
            await self._notify_new_grants(run_id=run_id, site_id=site.id, site_url=site.url)
            config = self.storage.load_config()
            self.storage.mark_site_run_finished(site.id, config.interval_hours)
            self.storage.update_run(
                run_id,
                status="completed",
                summary=summary.strip(),
                finished_at=utc_now_iso(),
            )
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site.id,
                site_url=site.url,
                event_type="run_completed",
                message="Прогон завершен успешно.",
            )
        except Exception as exc:  # pragma: no cover - exercised through integration tests
            logger.exception("site run failed: %s", site_id)
            error_text = str(exc)
            config = self.storage.load_config()
            self.storage.mark_site_run_finished(site_id, config.interval_hours)
            if "Max turns" in error_text:
                self.storage.update_run(
                    run_id,
                    status="completed",
                    error=error_text,
                    summary="Прогон ограничен лимитом ходов до финализации. Проверьте notes/status и при необходимости увеличьте iterations.",
                    finished_at=utc_now_iso(),
                )
                self.storage.append_run_event(
                    run_id=run_id,
                    site_id=site_id,
                    site_url=site_url_for_events,
                    event_type="run_limited",
                    message=f"Прогон остановлен лимитом ходов: {error_text}",
                )
            else:
                self.storage.update_run(
                    run_id,
                    status="failed",
                    error=error_text,
                    summary="Run failed",
                    finished_at=utc_now_iso(),
                )
                self.storage.append_run_event(
                    run_id=run_id,
                    site_id=site_id,
                    site_url=site_url_for_events,
                    event_type="run_failed",
                    message=f"Прогон завершился ошибкой: {error_text}",
                )
        finally:
            self._running_sites.discard(site_id)

    async def _run_source_discovery(self, run_id: str) -> None:
        if self._source_discovery_running:
            return
        self._source_discovery_running = True
        site_id = "__source_discovery__"
        site_url = "source-discovery"
        try:
            config = self.storage.mark_source_discovery_started()
            self.storage.update_run(run_id, status="running")
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
                event_type="run_started",
                message="Старт поиска новых площадок поставщиков.",
            )
            summary = await self._run_source_discovery_agent(run_id=run_id)
            await self._notify_new_source_candidates(run_id=run_id, site_id=site_id, site_url=site_url)
            self.storage.mark_source_discovery_finished(config.source_discovery_interval_hours)
            self.storage.update_run(
                run_id,
                status="completed",
                summary=summary.strip(),
                finished_at=utc_now_iso(),
            )
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
                event_type="run_completed",
                message="Поиск новых площадок поставщиков завершен успешно.",
            )
        except Exception as exc:  # pragma: no cover - integration path
            logger.exception("source discovery failed")
            config = self.storage.load_config()
            self.storage.mark_source_discovery_finished(config.source_discovery_interval_hours)
            self.storage.update_run(
                run_id,
                status="failed",
                error=str(exc),
                summary="Source discovery failed",
                finished_at=utc_now_iso(),
            )
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
                event_type="run_failed",
                message=f"Поиск новых площадок завершился ошибкой: {exc}",
            )
        finally:
            self._source_discovery_running = False

    async def _run_agent_for_site(self, site_id: str, site_label: str, site_url: str, run_id: str) -> str:
        model_name = self.model_name
        agent = build_site_agent(model_name)
        config = self.storage.load_config()
        self.storage.append_run_event(
            run_id=run_id,
            site_id=site_id,
            site_url=site_url,
            event_type="agent_started",
            message=f"Агент начал исследование площадки поставщиков: {site_label}",
        )
        prompt = (
            f"Check the site {site_url} for supplier offers, product components, prices, delivery terms, and procurement contacts relevant to the product profile.\n"
            f"Use up to {config.iterations_per_site} turns for research.\n"
            "Leave room for finalization: always update notes, update status, and then finish.\n"
            "All text outputs must be in Russian."
        )
        result = await Runner.run(
            agent,
            prompt,
            context=SiteRunContext(
                storage=self.storage,
                site_id=site_id,
                site_label=site_label,
                site_url=site_url,
                run_id=run_id,
            ),
            max_turns=compute_runner_max_turns(config.iterations_per_site),
        )
        return str(result.final_output)

    async def _run_source_discovery_agent(self, run_id: str) -> str:
        model_name = self.model_name
        agent = build_source_discovery_agent(model_name)
        config = self.storage.load_config()
        self.storage.append_run_event(
            run_id=run_id,
            site_id="__source_discovery__",
            site_url="source-discovery",
            event_type="agent_started",
            message="Агент начал поиск новых площадок поставщиков.",
        )
        prompt = (
            "Find new recurring supplier marketplaces, catalogs, distributors, manufacturers, and procurement platforms relevant to the product profile.\n"
            f"Use up to {config.source_discovery_iterations} turns for research.\n"
            "Do not add sources that are already known. Always update discovery notes and status before finishing.\n"
            "All text outputs must be in Russian."
        )
        result = await Runner.run(
            agent,
            prompt,
            context=SiteRunContext(
                storage=self.storage,
                site_id="__source_discovery__",
                site_label="Поиск источников",
                site_url="source-discovery",
                run_id=run_id,
            ),
            max_turns=compute_runner_max_turns(config.source_discovery_iterations),
        )
        return str(result.final_output)

    async def _notify_new_grants(self, *, run_id: str, site_id: str, site_url: str) -> None:
        grants = self.storage.list_unnotified_grants_for_run(run_id)
        if not grants:
            return
        if not self.telegram_notifier.enabled:
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
                event_type="telegram_skipped",
                message="Telegram не настроен, уведомления о новых поставщиках не отправлены.",
                metadata={"grants": str(len(grants))},
            )
            return
        for grant in grants:
            try:
                sent = await self.telegram_notifier.send_grant(grant)
                if sent:
                    self.storage.mark_grant_telegram_notified(grant.id)
                    self.storage.append_run_event(
                        run_id=run_id,
                        site_id=site_id,
                        site_url=site_url,
                        event_type="telegram_sent",
                        message=f"Telegram-уведомление отправлено: {grant.title}",
                        metadata={"grant_id": grant.id, "source": grant.source},
                    )
            except Exception as exc:
                logger.exception("telegram notification failed for grant: %s", grant.id)
                self.storage.append_run_event(
                    run_id=run_id,
                    site_id=site_id,
                    site_url=site_url,
                    event_type="telegram_error",
                    message=f"Ошибка отправки Telegram-уведомления: {exc}",
                    metadata={"grant_id": grant.id, "source": grant.source},
                )

    async def _notify_new_source_candidates(self, *, run_id: str, site_id: str, site_url: str) -> None:
        candidates = self.storage.list_unnotified_source_candidates_for_run(run_id)
        if not candidates:
            return
        if not self.telegram_notifier.enabled:
            self.storage.append_run_event(
                run_id=run_id,
                site_id=site_id,
                site_url=site_url,
                event_type="telegram_skipped",
                message="Telegram не настроен, уведомления о новых источниках не отправлены.",
                metadata={"candidates": str(len(candidates))},
            )
            return
        for candidate in candidates:
            try:
                sent = await self.telegram_notifier.send_source_candidate(candidate)
                if sent:
                    self.storage.mark_source_candidate_telegram_notified(candidate.id)
                    self.storage.append_run_event(
                        run_id=run_id,
                        site_id=site_id,
                        site_url=site_url,
                        event_type="telegram_sent",
                        message=f"Telegram-уведомление отправлено: {candidate.label}",
                        metadata={"candidate_id": candidate.id, "source": candidate.url},
                    )
            except Exception as exc:
                logger.exception("telegram notification failed for source candidate: %s", candidate.id)
                self.storage.append_run_event(
                    run_id=run_id,
                    site_id=site_id,
                    site_url=site_url,
                    event_type="telegram_error",
                    message=f"Ошибка отправки Telegram-уведомления: {exc}",
                    metadata={"candidate_id": candidate.id, "source": candidate.url},
                )


def compute_runner_max_turns(iterations_per_site: int) -> int:
    return max(1, int(iterations_per_site)) + FINALIZATION_TURN_BUFFER


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "/data"))


def create_app(data_dir: Path | None = None) -> FastAPI:
    configure_openai_client()
    storage = JsonStorage(data_dir or get_data_dir())
    run_manager = RunManager(
        storage=storage,
        model_name=os.getenv("AGENT_MODEL", os.getenv("PROXY_MODEL", "gpt-4.1-mini")),
        telegram_notifier=TelegramNotifier(),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        run_manager.start()
        yield
        run_manager.shutdown()

    app = FastAPI(title="Supplier Scout", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.storage = storage
    app.state.run_manager = run_manager

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config", response_model=AppConfig)
    async def get_config():
        return storage.load_config()

    @app.put("/api/config", response_model=AppConfig)
    async def put_config(config: AppConfig):
        return storage.replace_config(config)

    @app.post("/api/sites")
    async def post_site(payload: SiteCreate):
        return storage.add_site(payload)

    @app.put("/api/sites/{site_id}")
    async def put_site(site_id: str, payload: SiteUpdate):
        try:
            return storage.update_site(site_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Site not found") from exc

    @app.delete("/api/sites/{site_id}")
    async def delete_site(site_id: str):
        storage.delete_site(site_id)
        return {"ok": True}

    @app.post("/api/sites/{site_id}/run")
    async def run_site(site_id: str):
        try:
            run_id = await run_manager.trigger_site(site_id)
            return {"run_id": run_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Site not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/source-discovery/run")
    async def run_source_discovery():
        try:
            run_id = await run_manager.trigger_source_discovery()
            return {"run_id": run_id}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/sites/{site_id}/notes", response_model=SiteTextResponse)
    async def get_notes(site_id: str):
        content, updated_at = storage.read_notes(site_id)
        return SiteTextResponse(content=content, updated_at=updated_at)

    @app.get("/api/sites/{site_id}/status", response_model=SiteTextResponse)
    async def get_status(site_id: str):
        content, updated_at = storage.read_status(site_id)
        return SiteTextResponse(content=content, updated_at=updated_at)

    @app.get("/api/grants")
    async def get_grants():
        return storage.list_grants()

    @app.get("/api/products")
    async def get_products():
        return storage.list_products()

    @app.post("/api/products")
    async def post_product(payload: ProductCreate):
        return storage.add_product(payload)

    @app.put("/api/products/{product_id}")
    async def put_product(product_id: str, payload: ProductUpdate):
        try:
            return storage.update_product(product_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Product not found") from exc

    @app.delete("/api/products/{product_id}")
    async def delete_product(product_id: str):
        storage.delete_product(product_id)
        return {"ok": True}

    @app.get("/api/source-candidates")
    async def get_source_candidates():
        return storage.list_source_candidates()

    @app.post("/api/source-candidates/{candidate_id}/add")
    async def add_source_candidate_to_sites(candidate_id: str):
        try:
            return storage.add_site_from_candidate(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source candidate not found") from exc

    @app.put("/api/source-candidates/{candidate_id}/status")
    async def put_source_candidate_status(candidate_id: str, payload: SourceCandidateStatusUpdate):
        try:
            return storage.update_source_candidate_status(candidate_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Source candidate not found") from exc

    @app.put("/api/grants/{grant_id}/status")
    async def put_grant_status(grant_id: str, payload: GrantStatusUpdate):
        try:
            return storage.update_grant_status(grant_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Grant not found") from exc

    @app.get("/api/runs")
    async def get_runs():
        return storage.list_runs()

    @app.get("/api/runs/{run_id}/events")
    async def get_run_events(run_id: str):
        return storage.list_run_events(run_id)

    @app.get("/api/state", response_model=AppState)
    async def get_state():
        state = storage.build_state()
        return AppState.model_validate(state)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
