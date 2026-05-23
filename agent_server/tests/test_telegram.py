from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from agent_server.models import GrantRecord
    from agent_server.telegram import TelegramNotifier
except ModuleNotFoundError:  # pragma: no cover
    from models import GrantRecord
    from telegram import TelegramNotifier


def make_grant() -> GrantRecord:
    return GrantRecord(
        id="grant-1",
        title="Грант на робототехнику",
        institution="Институт развития",
        amount="10 млн рублей",
        funding_type="грант",
        category="робототехника",
        conditions="Подходит технологическим компаниям",
        restrictions="Софинансирование обязательно",
        deadline="2026-12-31",
        application_url="https://example.org/apply",
        site="Институт развития",
        site_id="site-1",
        description="Финансирование опытно-конструкторских работ",
        source="https://example.org/grant",
        site_url="https://example.org",
        discovered_at="2026-04-24T10:00:00Z",
        updated_at="2026-04-24T10:00:00Z",
        last_run_id="run-1",
    )


def test_notifier_disabled_without_credentials():
    notifier = TelegramNotifier(bot_token="", chat_id="")
    assert notifier.enabled is False


def test_format_grant_message_contains_key_fields():
    message = TelegramNotifier.format_grant_message(make_grant())
    assert "Найден новый грант" in message
    assert "Грант на робототехнику" in message
    assert "10 млн рублей" in message
    assert "робототехника" in message
    assert "2026-12-31" in message
    assert "https://example.org/apply" in message
