from __future__ import annotations

import os

import httpx

try:
    from .models import GrantRecord, SourceCandidate
except ImportError:  # pragma: no cover
    from models import GrantRecord, SourceCandidate


class TelegramNotifier:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        chat_id: str | None = None,
        base_url: str | None = None,
    ):
        self.bot_token = bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self.base_url = (base_url or os.getenv("TELEGRAM_API_BASE_URL") or "https://api.telegram.org").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send_grant(self, grant: GrantRecord) -> bool:
        if not self.enabled:
            return False
        text = self.format_grant_message(grant)
        url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": False,
                },
            )
            response.raise_for_status()
        return True

    async def send_source_candidate(self, candidate: SourceCandidate) -> bool:
        if not self.enabled:
            return False
        text = self.format_source_candidate_message(candidate)
        url = f"{self.base_url}/bot{self.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": False,
                },
            )
            response.raise_for_status()
        return True

    @staticmethod
    def format_grant_message(grant: GrantRecord) -> str:
        parts = [
            "Найден новый поставщик",
            f"Предложение: {grant.title or 'Без названия'}",
            f"Поставщик: {grant.institution or grant.site or 'Unknown'}",
            f"Цена: {grant.amount or 'Unknown'}",
            f"Тип площадки: {grant.funding_type or 'Unknown'}",
            f"Компонент: {grant.category or 'Unknown'}",
            f"Срок/доставка: {grant.deadline or 'Unknown'}",
            f"Условия: {grant.conditions or 'Unknown'}",
        ]
        if grant.restrictions:
            parts.append(f"Ограничения: {grant.restrictions}")
        if grant.description:
            parts.append(f"Описание: {grant.description}")
        if grant.fit_reason:
            parts.append(f"Почему подходит: {grant.fit_reason}")
        if grant.how_to_apply:
            parts.append(f"Как заказать: {grant.how_to_apply}")
        link = grant.application_url or grant.source
        if link:
            parts.append(f"Ссылка: {link}")
        return "\n\n".join(parts)

    @staticmethod
    def format_source_candidate_message(candidate: SourceCandidate) -> str:
        parts = [
            "Найдена потенциальная площадка поставщиков",
            f"Название: {candidate.label or 'Без названия'}",
            f"URL: {candidate.url}",
            f"Почему стоит рассмотреть: {candidate.reason or 'Unknown'}",
        ]
        if candidate.evidence:
            parts.append(f"Что проверено: {candidate.evidence}")
        return "\n\n".join(parts)
