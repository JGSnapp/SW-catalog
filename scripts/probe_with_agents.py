"""Same exact request as parse_upload_with_llm, but also calls
set_default_openai_client like the real app does. This isolates whether the
Agents SDK affects subsequent direct client calls."""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "/app")

from openai import AsyncOpenAI

from agents import set_default_openai_client

from prompts import UPLOAD_PARSER_PROMPT

API_KEY = os.getenv("PROXY_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
BASE_URL = os.getenv("PROXY_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.proxyapi.ru/openai/v1"
MODEL = os.getenv("PROXY_MODEL") or os.getenv("AGENT_MODEL") or "gpt-4o"

SAMPLE = """Жакет oversize SS26
- основная ткань: шерсть 350 г/м², 320 м, цель 18 €/м, поставщик TextilePro Italy 22 €/м
- подкладка: вискоза 120 г/м², 280 м
- пуговицы: рог, 12 шт, поставщик Fornituris 0.40 €/шт"""


async def main() -> None:
    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    # The line that the real app calls during configure_openai_client():
    set_default_openai_client(client, use_for_tracing=False)
    print("set_default_openai_client called")

    full_input = (
        UPLOAD_PARSER_PROMPT.strip()
        + "\n\n"
        + "Исходное название документа: Импорт закупочной выгрузки\n\n"
        + f"Сырое содержимое:\n```\n{SAMPLE[:12000]}\n```\n\n"
        + "Верни строго JSON по описанной схеме. Никаких пояснений, markdown-фенсов или текста вокруг — только JSON."
    )
    print(f"calling client.responses.create(model={MODEL!r}, input=<{len(full_input)} chars>)")

    try:
        response = await client.responses.create(model=MODEL, input=full_input)
        print("OK:", (getattr(response, "output_text", "") or "(empty)")[:300])
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
