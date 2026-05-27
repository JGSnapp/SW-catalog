from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents import Agent, OpenAIChatCompletionsModel, RunContextWrapper, Runner, function_tool, set_default_openai_client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI

try:
    from .models import (
        AppConfig,
        AppState,
        AuthRequest,
        AuthResponse,
        ProjectCreate,
        ProjectItemDraft,
        ProjectUpdate,
        SourceSiteCreate,
        SourceSiteUpdate,
        SupplierCreate,
        SupplierMonitorUpdate,
        SupplierStatusUpdate,
        SupplierUpdate,
        UploadCreate,
        UserPublic,
        utc_now_iso,
    )
    from .prompts import ITEM_DISCOVERY_PROMPT, PROJECT_DECOMPOSITION_PROMPT, UPLOAD_PARSER_PROMPT
    from .auth import AuthStore
    from .storage import JsonStorage, parse_price_value
    from .tools import find_and_download_image, read_url, search_images, search_web
except ImportError:  # pragma: no cover
    from models import (  # type: ignore
        AppConfig,
        AppState,
        AuthRequest,
        AuthResponse,
        ProjectCreate,
        ProjectItemDraft,
        ProjectUpdate,
        SourceSiteCreate,
        SourceSiteUpdate,
        SupplierCreate,
        SupplierMonitorUpdate,
        SupplierStatusUpdate,
        SupplierUpdate,
        UploadCreate,
        UserPublic,
        utc_now_iso,
    )
    from prompts import ITEM_DISCOVERY_PROMPT, PROJECT_DECOMPOSITION_PROMPT, UPLOAD_PARSER_PROMPT  # type: ignore
    from auth import AuthStore  # type: ignore
    from storage import JsonStorage, parse_price_value  # type: ignore
    from tools import find_and_download_image, read_url, search_images, search_web  # type: ignore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FINALIZATION_TURN_BUFFER = 4


def configure_openai_client() -> AsyncOpenAI | None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("PROXY_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("PROXY_BASE_URL")
    model_name = configured_model_name()
    logger.info(
        "LLM init: model=%r, base_url=%r, api_key_set=%s",
        model_name,
        base_url,
        bool(api_key),
    )
    if not api_key:
        logger.warning("LLM init: no API key set, agent will use heuristic fallback only")
        return None
    client_kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)
    set_default_openai_client(client, use_for_tracing=False)
    return client


def llm_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL") or os.getenv("PROXY_BASE_URL") or ""


def llm_uses_openrouter() -> bool:
    return "/openrouter/" in llm_base_url().lower()


def configured_model_name() -> str:
    return os.getenv("AGENT_MODEL") or os.getenv("PROXY_MODEL") or "gpt-4o-mini"


def configured_fallback_model_name() -> str:
    return os.getenv("AGENT_FALLBACK_MODEL") or os.getenv("PROXY_FALLBACK_MODEL") or ""


async def generate_text(
    *,
    client: AsyncOpenAI | None,
    model_name: str,
    prompt: str,
) -> str:
    if client is None:
        return ""
    fallback_model = configured_fallback_model_name()
    candidates = [model_name]
    if fallback_model and fallback_model != model_name:
        candidates.append(fallback_model)
    last_error: Exception | None = None
    for candidate in candidates:
        try:
            if llm_uses_openrouter():
                response = await client.chat.completions.create(
                    model=candidate,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                )
                return (response.choices[0].message.content or "").strip()
            response = await client.responses.create(model=candidate, input=prompt)
            raw = (getattr(response, "output_text", "") or "").strip()
            if raw:
                return raw
            try:
                return response.output[0].content[0].text.strip()  # type: ignore[attr-defined]
            except Exception:
                return ""
        except Exception as exc:
            last_error = exc
            logger.warning("LLM request failed for model %s: %s", candidate, exc)
    if last_error is not None:
        raise last_error
    return ""


# ---------------------------------------------------------------------------
# Per-item discovery / monitoring agent
# ---------------------------------------------------------------------------

@dataclass
class ItemRunContext:
    storage: JsonStorage
    project_id: str
    item_id: str
    item_name: str
    run_id: str
    run_kind: str


def build_item_agent(model: Any) -> Agent[ItemRunContext]:
    def log(ctx: RunContextWrapper[ItemRunContext], *, event_type: str, message: str, metadata: dict[str, str] | None = None) -> None:
        ctx.context.storage.append_run_event(
            run_id=ctx.context.run_id,
            project_id=ctx.context.project_id,
            item_id=ctx.context.item_id,
            event_type=event_type,
            message=message,
            metadata=metadata,
        )

    @function_tool
    async def search_supplier_web(
        ctx: RunContextWrapper[ItemRunContext],
        query: str,
        docs: int = 8,
        maxpassages: int = 4,
        lr: int | None = None,
    ):
        """Internet search for supplier pages. Pass keywords, not URLs. Returns title/url/passages."""

        log(ctx, event_type="search_start", message=f"Поиск: {query}", metadata={"query": query})
        try:
            result = await search_web(query=query, docs=docs, maxpassages=maxpassages, lr=lr)
            log(
                ctx,
                event_type="search_done",
                message=f"Найдено результатов: {len(result)}",
                metadata={"query": query, "results": str(len(result))},
            )
            return result
        except Exception as exc:
            log(ctx, event_type="search_error", message=f"Ошибка поиска: {exc}", metadata={"query": query, "error": str(exc)})
            return [{"ok": False, "query": query, "error": str(exc)}]

    @function_tool
    async def read_supplier_page(ctx: RunContextWrapper[ItemRunContext], url: str, timeout: int = 60):
        """Read the contents of a supplier or catalog page via reader chain (Jina -> direct -> Playwright)."""

        log(ctx, event_type="read_start", message=f"Читаю: {url}", metadata={"url": url})
        try:
            result = await read_url(url=url, timeout=timeout)
            log(
                ctx,
                event_type="read_done",
                message=f"Страница прочитана: {url}",
                metadata={"url": url, "reader": str(result.get("reader", ""))},
            )
            return result
        except Exception as exc:
            log(ctx, event_type="read_error", message=f"Ошибка чтения: {exc}", metadata={"url": url, "error": str(exc)})
            return {"ok": False, "url": url, "error": str(exc)}

    @function_tool(name_override="find_in_page")
    async def find_in_page(ctx: RunContextWrapper[ItemRunContext], url: str, query: str = "", timeout: int = 60):
        """Alias for read_supplier_page. Use it to inspect a supplier page and find relevant offer details."""

        result = await read_url(url=url, timeout=timeout)
        log(
            ctx,
            event_type="read_done",
            message=f"Страница прочитана: {url}",
            metadata={"url": url, "query": query, "reader": str(result.get("reader", ""))},
        )
        return result

    @function_tool
    def add_supplier(
        ctx: RunContextWrapper[ItemRunContext],
        name: str,
        offer_title: str = "",
        price_text: str = "",
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
    ) -> dict[str, str]:
        """Save or update an alternative supplier for the current item. Use real, verified data only."""

        supplier = ctx.context.storage.upsert_discovered_supplier(
            project_id=ctx.context.project_id,
            item_id=ctx.context.item_id,
            run_id=ctx.context.run_id,
            name=name,
            offer_title=offer_title,
            price_text=price_text,
            currency=currency or "RUB",
            lead_time=lead_time,
            country=country,
            category=category,
            description=description,
            terms=terms,
            restrictions=restrictions,
            url=url,
            source_url=source_url or url,
            contact=contact,
            image_url=image_url,
            ai_notes=ai_notes,
        )
        log(
            ctx,
            event_type="supplier_upsert",
            message=f"Сохранён поставщик: {supplier.name}",
            metadata={"supplier_id": supplier.id, "url": supplier.url},
        )
        return {"supplier_id": supplier.id, "updated_at": supplier.updated_at}

    @function_tool
    def write_item_notes(ctx: RunContextWrapper[ItemRunContext], content: str) -> str:
        """Replace the agent's reusable notes for this item with an updated short memo."""

        notes = content.strip()
        ctx.context.storage.set_item_notes(ctx.context.project_id, ctx.context.item_id, notes)
        ctx.context.storage.write_item_notes_file(ctx.context.item_id, notes)
        log(ctx, event_type="notes_write", message="Заметки по позиции обновлены.")
        return "ok"

    def instructions(ctx: RunContextWrapper[ItemRunContext], _agent: Agent[ItemRunContext]) -> str:
        storage = ctx.context.storage
        config = storage.load_config()
        try:
            project = storage.get_project(ctx.context.project_id)
        except KeyError:
            project = None
        item = None
        if project is not None:
            for candidate in project.items:
                if candidate.id == ctx.context.item_id:
                    item = candidate
                    break
        suppliers = storage.list_suppliers_for_item(ctx.context.item_id)
        suppliers_block = "\n".join(
            f"- {supplier.name} | цена: {supplier.price_text or supplier.price or 'Unknown'} | срок: {supplier.lead_time or 'Unknown'} | url: {supplier.url or supplier.source_url or 'Unknown'}"
            for supplier in suppliers
        ) or "Известных поставщиков пока нет."
        sites_block = "\n".join(f"- {site.label}: {site.url}" for site in config.sites if site.enabled) or "—"
        item_block = (
            f"Название: {item.name if item else ctx.context.item_name}\n"
            f"Спецификация: {item.specification if item and item.specification else 'не указана'}\n"
            f"Количество: {item.quantity if item else '?'} {item.unit if item else ''}\n"
            f"Целевая цена: {item.target_price if item and item.target_price else 'не указана'}\n"
            f"Заметки закупщика: {item.notes if item and item.notes else 'нет'}\n"
            f"Текущие заметки агента: {item.ai_notes if item and item.ai_notes else 'нет'}"
        )
        project_block = (
            f"Проект: {project.name if project else ctx.context.project_id}\n"
            f"Описание: {project.description if project and project.description else 'нет'}\n"
            f"Категория: {project.category if project and project.category else 'нет'}\n"
            f"Бюджет: {project.budget if project and project.budget else 'не указан'} {project.currency if project else ''}"
        )
        return "\n\n".join(
            [
                ITEM_DISCOVERY_PROMPT.strip(),
                f"Профиль закупщика:\n{config.company_profile or 'не задан'}",
                f"Глобальные требования:\n{config.global_prompt or 'нет'}",
                f"Категории мониторинга: {config.monitored_categories or 'не заданы'}",
                f"Предпочтительные регионы: {config.preferred_regions or 'не заданы'}",
                f"Исключённые регионы: {config.excluded_regions or 'нет'}",
                f"Максимальный срок поставки: {config.max_lead_time or 'не задан'}",
                f"Проект:\n{project_block}",
                f"Позиция:\n{item_block}",
                f"Уже известные поставщики:\n{suppliers_block}",
                f"Постоянные источники (площадки):\n{sites_block}",
            ]
        )

    return Agent[ItemRunContext](
        name="Item Procurement Agent",
        instructions=instructions,
        model=model,
        tools=[search_supplier_web, read_supplier_page, find_in_page, add_supplier, write_item_notes],
    )


def compute_max_turns(iterations: int) -> int:
    return max(1, int(iterations)) + FINALIZATION_TURN_BUFFER


# ---------------------------------------------------------------------------
# Upload parsing agent (single LLM call)
# ---------------------------------------------------------------------------

async def decompose_project_with_llm(
    *,
    client: AsyncOpenAI | None,
    model_name: str,
    name: str,
    description: str = "",
    category: str = "",
) -> list[dict[str, Any]]:
    """Ask the model for a typical BOM for the product. Returns list of item dicts.

    Each item dict matches ProjectItemDraft fields. Empty list means nothing was
    generated and the caller should leave the project as-is.
    """

    if client is None:
        return []
    full_input = (
        PROJECT_DECOMPOSITION_PROMPT.strip()
        + "\n\n"
        + f"Название проекта: {name}\n"
        + (f"Категория: {category}\n" if category else "")
        + (f"Описание: {description}\n" if description else "")
        + "\nВерни строго JSON по описанной схеме. Никаких пояснений или markdown-фенсов."
    )
    try:
        raw = await generate_text(client=client, model_name=model_name, prompt=full_input)
    except Exception as exc:
        logger.warning("project decomposition failed: %s", exc)
        return []
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    items = parsed.get("items") or []
    cleaned: list[dict[str, Any]] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        name_val = str(raw_item.get("name") or "").strip()
        if not name_val:
            continue
        try:
            quantity = float(raw_item.get("quantity") or 1)
        except (TypeError, ValueError):
            quantity = 1.0
        cleaned.append(
            {
                "name": name_val,
                "specification": str(raw_item.get("specification") or "").strip(),
                "quantity": quantity,
                "unit": str(raw_item.get("unit") or "шт").strip() or "шт",
                "target_price": str(raw_item.get("target_price") or "").strip(),
                "notes": str(raw_item.get("notes") or "").strip(),
            }
        )
    return cleaned


async def parse_upload_with_llm(
    *,
    client: AsyncOpenAI | None,
    model_name: str,
    text: str,
    name: str,
) -> dict[str, Any]:
    def fallback(reason: str = "") -> dict[str, Any]:
        items = []
        for line in text.splitlines():
            cleaned = line.strip(" \t-•|;,")
            if not cleaned or len(cleaned) < 2:
                continue
            items.append({"name": cleaned[:200], "specification": "", "quantity": 1, "unit": "шт", "suppliers": []})
            if len(items) >= 12:
                break
        summary = "Парсинг без LLM: позиции взяты построчно."
        if reason:
            summary = f"{summary} Причина: {reason}"
        return {
            "projects": [
                {
                    "name": name or "Импорт",
                    "description": "Автоматически распознанный список позиций (fallback без LLM).",
                    "status": "planning",
                    "items": items,
                }
            ],
            "summary": summary,
        }

    if client is None:
        return fallback("LLM-клиент не настроен.")

    # Minimal Responses API call — mirrors the user's working snippet:
    # client.responses.create(model=..., input=...). No extra fields that
    # the proxyapi.ru gateway might reject as "Model not supported".
    full_input = (
        UPLOAD_PARSER_PROMPT.strip()
        + "\n\n"
        + f"Исходное название документа: {name}\n\n"
        + f"Сырое содержимое:\n```\n{text[:12000]}\n```\n\n"
        + "Верни строго JSON по описанной схеме. Никаких пояснений, markdown-фенсов или текста вокруг — только JSON."
    )
    try:
        raw = await generate_text(client=client, model_name=model_name, prompt=full_input)
    except Exception as exc:
        logger.warning("upload parser LLM unavailable, using fallback: %s", exc)
        return fallback(str(exc))
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            logger.warning("upload parser returned non-json response, using fallback")
            return fallback("LLM вернул невалидный JSON.")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("upload parser returned broken json object, using fallback")
            return fallback("LLM вернул поврежденный JSON.")


# ---------------------------------------------------------------------------
# Run manager
# ---------------------------------------------------------------------------

class RunManager:
    def __init__(self, storage: JsonStorage, model_name: str, client: AsyncOpenAI | None):
        self.storage = storage
        self.model_name = model_name
        self.client = client
        self.agent_model: Any = model_name
        self.fallback_agent_model: Any | None = None
        if self.client is not None and llm_uses_openrouter():
            self.agent_model = OpenAIChatCompletionsModel(model=model_name, openai_client=self.client)
            fallback_model = configured_fallback_model_name()
            if fallback_model and fallback_model != model_name:
                self.fallback_agent_model = OpenAIChatCompletionsModel(model=fallback_model, openai_client=self.client)
        self.scheduler = AsyncIOScheduler()
        self._running_items: set[str] = set()
        self._upload_running: set[str] = set()
        self._scheduler_lock = asyncio.Lock()

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.add_job(
                self._scheduled_loop,
                "interval",
                minutes=5,
                id="sw-monitor",
                replace_existing=True,
            )
            self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def is_item_running(self, item_id: str) -> bool:
        return item_id in self._running_items

    # ----- trigger entry points -----

    async def trigger_item_discovery(self, project_id: str, item_id: str) -> str:
        if self.is_item_running(item_id):
            raise ValueError("item is already being processed")
        project = self.storage.get_project(project_id)
        item = next((candidate for candidate in project.items if candidate.id == item_id), None)
        if item is None:
            raise KeyError(item_id)
        run = self.storage.create_run(
            kind="item_discovery",
            label=f"{project.name} · {item.name}",
            project_id=project.id,
            item_id=item.id,
        )
        asyncio.create_task(self._run_item(run_id=run.id, project_id=project.id, item_id=item.id, kind="item_discovery"))
        return run.id

    async def trigger_upload_parse(self, upload_id: str) -> str:
        if upload_id in self._upload_running:
            raise ValueError("upload is already being parsed")
        run = self.storage.create_run(kind="upload_parse", label=f"Парсинг загрузки {upload_id}")
        asyncio.create_task(self._run_upload_parse(run_id=run.id, upload_id=upload_id))
        return run.id

    async def trigger_image_search(self, project_id: str, item_id: str) -> str:
        project = self.storage.get_project(project_id)
        item = next((candidate for candidate in project.items if candidate.id == item_id), None)
        if item is None:
            raise KeyError(item_id)
        run = self.storage.create_run(
            kind="image_search",
            label=f"Изображение: {item.name}",
            project_id=project_id,
            item_id=item_id,
        )
        asyncio.create_task(
            self._run_image_search(run_id=run.id, project_id=project_id, item_id=item_id, item_name=item.name, hint=item.specification)
        )
        return run.id

    # ----- background work -----

    async def _scheduled_loop(self) -> None:
        async with self._scheduler_lock:
            config = self.storage.load_config()
            due = self.storage.items_due_for_monitoring(config.monitor_interval_hours)
            for project, item in due:
                if self.is_item_running(item.id):
                    continue
                run = self.storage.create_run(
                    kind="item_monitor",
                    label=f"Мониторинг · {project.name} · {item.name}",
                    project_id=project.id,
                    item_id=item.id,
                )
                await self._run_item(run_id=run.id, project_id=project.id, item_id=item.id, kind="item_monitor")

    async def _run_item(self, *, run_id: str, project_id: str, item_id: str, kind: str) -> None:
        self._running_items.add(item_id)
        try:
            project = self.storage.get_project(project_id)
            item = next((candidate for candidate in project.items if candidate.id == item_id), None)
            if item is None:
                raise KeyError(item_id)
            self.storage.update_run(run_id, status="running")
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="run_started",
                message=f"Старт {('мониторинга' if kind == 'item_monitor' else 'поиска')} поставщиков: {item.name}",
            )
            config = self.storage.load_config()
            iterations = config.monitor_iterations if kind == "item_monitor" else config.discovery_iterations
            prompt_intro = (
                "Найди альтернативных поставщиков для указанной позиции."
                if kind == "item_discovery"
                else "Проверь актуальность цен и условий у отслеживаемых поставщиков и при необходимости обнови записи."
            )
            prompt = (
                f"{prompt_intro}\n"
                f"Используй до {iterations} ходов на исследование.\n"
                "В конце обязательно вызови write_item_notes(). Все тексты на русском."
            )
            context = ItemRunContext(
                storage=self.storage,
                project_id=project_id,
                item_id=item_id,
                item_name=item.name,
                run_id=run_id,
                run_kind=kind,
            )
            try:
                result = await Runner.run(
                    build_item_agent(self.agent_model),
                    prompt,
                    context=context,
                    max_turns=compute_max_turns(iterations),
                )
            except Exception as exc:
                if self.fallback_agent_model is None:
                    raise
                logger.warning("agent run failed on primary model, retrying fallback: %s", exc)
                self.storage.append_run_event(
                    run_id=run_id,
                    project_id=project_id,
                    item_id=item_id,
                    event_type="model_fallback",
                    message=f"Основная модель недоступна, повторяю на fallback: {configured_fallback_model_name()}",
                    metadata={"error": str(exc), "fallback_model": configured_fallback_model_name()},
                )
                result = await Runner.run(
                    build_item_agent(self.fallback_agent_model),
                    prompt,
                    context=context,
                    max_turns=compute_max_turns(iterations),
                )
            summary = str(result.final_output).strip()
            self.storage.update_run(run_id, status="completed", summary=summary, finished_at=utc_now_iso())
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="run_completed",
                message="Прогон по позиции завершён.",
            )
        except Exception as exc:
            logger.exception("item run failed: %s", item_id)
            error_text = str(exc)
            self.storage.update_run(
                run_id,
                status="failed",
                summary="Прогон завершился ошибкой.",
                error=error_text,
                finished_at=utc_now_iso(),
            )
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="run_failed",
                message=f"Ошибка: {error_text}",
            )
        finally:
            self._running_items.discard(item_id)

    async def _run_upload_parse(self, *, run_id: str, upload_id: str) -> None:
        self._upload_running.add(upload_id)
        try:
            upload = next((item for item in self.storage.list_uploads() if item.id == upload_id), None)
            if upload is None:
                raise KeyError(upload_id)
            self.storage.update_run(run_id, status="running")
            self.storage.update_upload(upload_id, status="parsing")
            self.storage.append_run_event(
                run_id=run_id,
                event_type="upload_started",
                message=f"Парсю загрузку: {upload.name}",
            )
            text = self.storage.read_upload(upload_id)
            parsed = await parse_upload_with_llm(
                client=self.client,
                model_name=self.model_name,
                text=text,
                name=upload.name,
            )
            created_ids: list[str] = []
            for project_payload in parsed.get("projects", []) or []:
                items_payload = project_payload.get("items") or []
                project_items = [
                    ProjectItemDraft(
                        name=str(item.get("name") or "").strip() or "Без названия",
                        specification=str(item.get("specification") or "").strip(),
                        quantity=float(item.get("quantity") or 1),
                        unit=str(item.get("unit") or "шт"),
                        target_price=str(item.get("target_price") or ""),
                        notes=str(item.get("notes") or ""),
                    )
                    for item in items_payload
                    if str(item.get("name") or "").strip()
                ]
                draft = ProjectCreate(
                    name=str(project_payload.get("name") or upload.name)[:200],
                    description=str(project_payload.get("description") or "")[:8000],
                    status=str(project_payload.get("status") or "planning"),  # type: ignore[arg-type]
                    target_volume=str(project_payload.get("target_volume") or ""),
                    budget=str(project_payload.get("budget") or ""),
                    currency=str(project_payload.get("currency") or "RUB"),
                    category=str(project_payload.get("category") or ""),
                    items=project_items,
                )
                created = self.storage.add_project(draft)
                created_ids.append(created.id)
                # If the upload mentioned a product without items, run the
                # decomposition agent and inject typical BOM items.
                if not created.items:
                    decomposed = await decompose_project_with_llm(
                        client=self.client,
                        model_name=self.model_name,
                        name=created.name,
                        description=created.description,
                        category=created.category,
                    )
                    for item_dict in decomposed:
                        try:
                            self.storage.add_item(created.id, ProjectItemDraft(**item_dict))
                        except Exception as exc:
                            logger.warning("upload decomposition: failed to add item: %s", exc)
                    created = self.storage.get_project(created.id)
                # Schedule auto image search for every item now in the project.
                for item in created.items:
                    if not item.image_url:
                        asyncio.create_task(
                            _safe_trigger_image_search(self, created.id, item.id)
                        )
                # Attach mentioned suppliers to created items.
                for raw_item, created_item in zip(items_payload, created.items):
                    for supplier_payload in raw_item.get("suppliers") or []:
                        name = str(supplier_payload.get("name") or "").strip()
                        if not name:
                            continue
                        self.storage.add_supplier(
                            project_id=created.id,
                            item_id=created_item.id,
                            payload=SupplierCreate(
                                name=name,
                                offer_title=str(supplier_payload.get("offer_title") or ""),
                                price=None,
                                price_text=str(supplier_payload.get("price_text") or ""),
                                currency=str(supplier_payload.get("currency") or "RUB"),
                                lead_time=str(supplier_payload.get("lead_time") or ""),
                                country=str(supplier_payload.get("country") or ""),
                                category=str(supplier_payload.get("category") or ""),
                                description=str(supplier_payload.get("description") or ""),
                                terms=str(supplier_payload.get("terms") or ""),
                                restrictions=str(supplier_payload.get("restrictions") or ""),
                                url=str(supplier_payload.get("url") or ""),
                                source_url=str(supplier_payload.get("source_url") or ""),
                                contact=str(supplier_payload.get("contact") or ""),
                                image_url=str(supplier_payload.get("image_url") or ""),
                                status="verified",
                                is_existing=True,
                                monitoring_enabled=bool(supplier_payload.get("monitoring_enabled", False)),
                            ),
                        )
            summary = str(parsed.get("summary") or "").strip() or f"Создано проектов: {len(created_ids)}"
            self.storage.update_upload(
                upload_id,
                status="parsed",
                parsed_at=utc_now_iso(),
                summary=summary,
                created_project_ids=created_ids,
            )
            self.storage.update_run(run_id, status="completed", summary=summary, finished_at=utc_now_iso())
            self.storage.append_run_event(
                run_id=run_id,
                event_type="upload_completed",
                message=f"Создано проектов: {len(created_ids)}",
                metadata={"project_ids": ",".join(created_ids)},
            )
        except Exception as exc:
            logger.exception("upload parse failed: %s", upload_id)
            error_text = str(exc)
            self.storage.update_upload(upload_id, status="failed", error=error_text, parsed_at=utc_now_iso())
            self.storage.update_run(run_id, status="failed", error=error_text, summary="Парсинг загрузки упал.", finished_at=utc_now_iso())
            self.storage.append_run_event(run_id=run_id, event_type="upload_failed", message=f"Ошибка: {error_text}")
        finally:
            self._upload_running.discard(upload_id)

    async def _run_image_search(self, *, run_id: str, project_id: str, item_id: str, item_name: str, hint: str) -> None:
        try:
            self.storage.update_run(run_id, status="running")
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="image_search_start",
                message=f"Ищу изображение: {item_name}",
            )
            query = f"{item_name} {hint}".strip()
            file_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", item_id) or "item"
            destination_dir = self.storage.images_dir
            result = await find_and_download_image(
                query,
                destination_dir=destination_dir,
                file_stem=file_stem,
                limit=6,
            )
            if result is None:
                raise RuntimeError("подходящее изображение не найдено")
            local_path, source_url = result
            relative_path = local_path.name
            prefix = getattr(self.storage, "public_images_prefix", "/static/images").rstrip("/")
            public_url = f"{prefix}/{relative_path}"
            self.storage.set_item_image(project_id, item_id, public_url)
            self.storage.update_run(
                run_id,
                status="completed",
                summary=f"Сохранено: {public_url} (источник: {source_url})",
                finished_at=utc_now_iso(),
            )
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="image_search_done",
                message=f"Изображение сохранено: {public_url}",
                metadata={"source": source_url, "local": str(local_path)},
            )
        except Exception as exc:
            logger.exception("image search failed: %s", item_id)
            error_text = str(exc)
            self.storage.update_run(run_id, status="failed", error=error_text, summary="Поиск изображения упал.", finished_at=utc_now_iso())
            self.storage.append_run_event(
                run_id=run_id,
                project_id=project_id,
                item_id=item_id,
                event_type="image_search_failed",
                message=f"Ошибка: {error_text}",
            )


async def _safe_trigger_image_search(run_manager: "RunManager", project_id: str, item_id: str) -> None:
    """Fire-and-forget wrapper used to schedule image search for a newly created item.

    Swallows errors so a failing image lookup never breaks the caller's request.
    """

    try:
        await run_manager.trigger_image_search(project_id, item_id)
    except Exception as exc:
        logger.warning("auto image search failed for item %s: %s", item_id, exc)


def get_data_dir() -> Path:
    return Path(os.getenv("DATA_DIR", "/data"))


def create_app(data_dir: Path | None = None) -> FastAPI:
    client = configure_openai_client()
    base_data_dir = data_dir or get_data_dir()
    base_storage = JsonStorage(base_data_dir)
    auth_store = AuthStore()
    model_name = configured_model_name()
    runtimes: dict[str, tuple[JsonStorage, RunManager]] = {}

    def runtime_for_user(user: UserPublic) -> tuple[JsonStorage, RunManager]:
        cached = runtimes.get(user.id)
        if cached is not None:
            return cached
        user_dir = base_data_dir / "users" / user.id
        user_storage = JsonStorage(user_dir)
        user_storage.enable_postgres_sync(dsn=auth_store.dsn, user_id=user.id)
        user_storage.public_images_prefix = f"/static/user-images/{user.id}/images"  # type: ignore[attr-defined]
        user_run_manager = RunManager(storage=user_storage, model_name=model_name, client=client)
        user_run_manager.start()
        runtimes[user.id] = (user_storage, user_run_manager)
        return user_storage, user_run_manager

    def token_from_header(authorization: str | None) -> str:
        if not authorization:
            raise HTTPException(status_code=401, detail="Authorization header required")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=401, detail="Bearer token required")
        return token.strip()

    async def current_user(authorization: str | None = Header(default=None)) -> UserPublic:
        token = token_from_header(authorization)
        user = auth_store.user_for_token(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user

    async def current_token(authorization: str | None = Header(default=None)) -> str:
        return token_from_header(authorization)

    async def user_storage(user: UserPublic = Depends(current_user)) -> JsonStorage:
        storage, _run_manager = runtime_for_user(user)
        return storage

    async def user_run_manager(user: UserPublic = Depends(current_user)) -> RunManager:
        _storage, run_manager = runtime_for_user(user)
        return run_manager

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        auth_store.ensure_schema()
        yield
        for _storage, run_manager in runtimes.values():
            run_manager.shutdown()

    app = FastAPI(title="SW-catalog", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.storage = base_storage
    app.state.auth_store = auth_store

    users_static_dir = base_data_dir / "users"
    users_static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/images", StaticFiles(directory=str(base_storage.images_dir)), name="item-images")
    app.mount("/static/user-images", StaticFiles(directory=str(users_static_dir)), name="user-item-images")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    # ---- auth ----

    @app.post("/api/auth/register", response_model=AuthResponse)
    async def register(payload: AuthRequest):
        try:
            session = auth_store.register(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime_for_user(session.user)
        return AuthResponse(user=session.user, token=session.token)

    @app.post("/api/auth/login", response_model=AuthResponse)
    async def login(payload: AuthRequest):
        try:
            session = auth_store.login(payload)
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        runtime_for_user(session.user)
        return AuthResponse(user=session.user, token=session.token)

    @app.get("/api/auth/me", response_model=UserPublic)
    async def me(user: UserPublic = Depends(current_user)):
        return user

    @app.post("/api/auth/logout")
    async def logout(token: str = Depends(current_token)):
        auth_store.revoke(token)
        return {"ok": True}

    # ---- config ----

    @app.get("/api/config", response_model=AppConfig)
    async def get_config(storage: JsonStorage = Depends(user_storage)):
        return storage.load_config()

    @app.put("/api/config", response_model=AppConfig)
    async def put_config(payload: AppConfig, storage: JsonStorage = Depends(user_storage)):
        return storage.replace_config(payload)

    # ---- projects ----

    @app.get("/api/projects")
    async def list_projects(storage: JsonStorage = Depends(user_storage)):
        return storage.list_projects()

    @app.post("/api/projects")
    async def create_project(
        payload: ProjectCreate,
        storage: JsonStorage = Depends(user_storage),
        run_manager: RunManager = Depends(user_run_manager),
    ):
        project = storage.add_project(payload)
        # If user didn't list any items, ask the LLM to decompose the product
        # into a typical BOM. This is the default behaviour, not opt-in.
        if not project.items:
            decomposed = await decompose_project_with_llm(
                client=client,
                model_name=run_manager.model_name,
                name=project.name,
                description=project.description,
                category=project.category,
            )
            for item_dict in decomposed:
                try:
                    storage.add_item(project.id, ProjectItemDraft(**item_dict))
                except Exception as exc:
                    logger.warning("failed to add decomposed item: %s", exc)
            project = storage.get_project(project.id)
        # Fire-and-forget: find an image for every newly created item.
        for item in project.items:
            if not item.image_url:
                asyncio.create_task(
                    _safe_trigger_image_search(run_manager, project.id, item.id)
                )
        return project

    @app.get("/api/projects/{project_id}")
    async def get_project(project_id: str, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.get_project(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.put("/api/projects/{project_id}")
    async def update_project(project_id: str, payload: ProjectUpdate, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.update_project(project_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.delete("/api/projects/{project_id}")
    async def delete_project(project_id: str, storage: JsonStorage = Depends(user_storage)):
        storage.delete_project(project_id)
        return {"ok": True}

    # ---- items ----

    @app.post("/api/projects/{project_id}/items")
    async def add_item(
        project_id: str,
        payload: ProjectItemDraft,
        storage: JsonStorage = Depends(user_storage),
        run_manager: RunManager = Depends(user_run_manager),
    ):
        try:
            item = storage.add_item(project_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not item.image_url:
            asyncio.create_task(_safe_trigger_image_search(run_manager, project_id, item.id))
        return item

    @app.put("/api/projects/{project_id}/items/{item_id}")
    async def update_item(project_id: str, item_id: str, payload: ProjectItemDraft, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.update_item(project_id, item_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Item not found") from exc

    @app.delete("/api/projects/{project_id}/items/{item_id}")
    async def delete_item(project_id: str, item_id: str, storage: JsonStorage = Depends(user_storage)):
        storage.delete_item(project_id, item_id)
        return {"ok": True}

    @app.post("/api/projects/{project_id}/items/{item_id}/discover")
    async def discover_item(project_id: str, item_id: str, run_manager: RunManager = Depends(user_run_manager)):
        try:
            run_id = await run_manager.trigger_item_discovery(project_id, item_id)
            return {"run_id": run_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Item not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/items/{item_id}/image")
    async def search_item_image(project_id: str, item_id: str, run_manager: RunManager = Depends(user_run_manager)):
        try:
            run_id = await run_manager.trigger_image_search(project_id, item_id)
            return {"run_id": run_id}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Item not found") from exc

    @app.get("/api/projects/{project_id}/items/{item_id}/notes")
    async def get_item_notes(project_id: str, item_id: str, storage: JsonStorage = Depends(user_storage)):
        content, updated_at = storage.read_item_notes_file(item_id)
        return {"content": content, "updated_at": updated_at}

    # ---- suppliers ----

    @app.get("/api/suppliers")
    async def list_suppliers(storage: JsonStorage = Depends(user_storage)):
        return storage.list_suppliers()

    @app.get("/api/items/{item_id}/suppliers")
    async def list_item_suppliers(item_id: str, storage: JsonStorage = Depends(user_storage)):
        return storage.list_suppliers_for_item(item_id)

    @app.post("/api/projects/{project_id}/items/{item_id}/suppliers")
    async def add_supplier(project_id: str, item_id: str, payload: SupplierCreate, storage: JsonStorage = Depends(user_storage)):
        return storage.add_supplier(project_id, item_id, payload)

    @app.put("/api/suppliers/{supplier_id}")
    async def update_supplier(supplier_id: str, payload: SupplierUpdate, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.update_supplier(supplier_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Supplier not found") from exc

    @app.put("/api/suppliers/{supplier_id}/monitor")
    async def update_supplier_monitor(supplier_id: str, payload: SupplierMonitorUpdate, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.set_supplier_monitoring(supplier_id, payload.monitoring_enabled)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Supplier not found") from exc

    @app.put("/api/suppliers/{supplier_id}/status")
    async def update_supplier_status(supplier_id: str, payload: SupplierStatusUpdate, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.set_supplier_status(supplier_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Supplier not found") from exc

    @app.delete("/api/suppliers/{supplier_id}")
    async def delete_supplier(supplier_id: str, storage: JsonStorage = Depends(user_storage)):
        storage.delete_supplier(supplier_id)
        return {"ok": True}

    # ---- source sites ----

    @app.post("/api/sites")
    async def add_site(payload: SourceSiteCreate, storage: JsonStorage = Depends(user_storage)):
        return storage.add_site(payload)

    @app.put("/api/sites/{site_id}")
    async def update_site(site_id: str, payload: SourceSiteUpdate, storage: JsonStorage = Depends(user_storage)):
        try:
            return storage.update_site(site_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Site not found") from exc

    @app.delete("/api/sites/{site_id}")
    async def delete_site(site_id: str, storage: JsonStorage = Depends(user_storage)):
        storage.delete_site(site_id)
        return {"ok": True}

    # ---- uploads ----

    @app.get("/api/uploads")
    async def list_uploads(storage: JsonStorage = Depends(user_storage)):
        return storage.list_uploads()

    @app.post("/api/uploads")
    async def add_upload(
        payload: UploadCreate,
        storage: JsonStorage = Depends(user_storage),
        run_manager: RunManager = Depends(user_run_manager),
    ):
        upload = storage.add_upload(payload)
        run_id = await run_manager.trigger_upload_parse(upload.id)
        return {"upload": upload, "run_id": run_id}

    @app.get("/api/uploads/{upload_id}/raw")
    async def upload_raw(upload_id: str, storage: JsonStorage = Depends(user_storage)):
        path = storage.uploads_dir / f"{upload_id}.txt"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Upload not found")
        return FileResponse(path, media_type="text/plain")

    # ---- runs ----

    @app.get("/api/runs")
    async def list_runs(storage: JsonStorage = Depends(user_storage)):
        return storage.list_runs()

    @app.get("/api/runs/{run_id}/events")
    async def list_run_events(run_id: str, storage: JsonStorage = Depends(user_storage)):
        return storage.list_run_events(run_id)

    # ---- changes ----

    @app.get("/api/changes")
    async def list_changes(storage: JsonStorage = Depends(user_storage)):
        return storage.list_changes()

    # ---- aggregated state ----

    @app.get("/api/state", response_model=AppState)
    async def get_state(storage: JsonStorage = Depends(user_storage)):
        state = storage.build_state()
        return AppState.model_validate(state)

    # ---- preview helpers for raw image search (used by manual UI debug) ----

    @app.get("/api/images/search")
    async def image_search_preview(query: str, limit: int = 6, _user: UserPublic = Depends(current_user)):
        try:
            urls = await search_images(query, limit=limit)
            return {"query": query, "results": urls}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
