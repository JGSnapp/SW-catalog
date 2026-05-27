"""Replicate the exact request our parse_upload_with_llm sends."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import sys
sys.path.insert(0, "/app")

from openai import AsyncOpenAI

from prompts import UPLOAD_PARSER_PROMPT

API_KEY = os.getenv("PROXY_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
BASE_URL = os.getenv("PROXY_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.proxyapi.ru/openai/v1"
MODEL = os.getenv("PROXY_MODEL") or os.getenv("AGENT_MODEL") or "gpt-4o"

SAMPLE = """Жакет oversize SS26
- основная ткань: шерсть 350 г/м², 320 м, цель 18 €/м, поставщик TextilePro Italy 22 €/м
- подкладка: вискоза 120 г/м², 280 м
- пуговицы: рог, 12 шт, поставщик Fornituris 0.40 €/шт"""


async def main() -> None:
    print(f"MODEL={MODEL}  len(prompt)={len(UPLOAD_PARSER_PROMPT)}")
    full_input = (
        UPLOAD_PARSER_PROMPT.strip()
        + "\n\n"
        + "Исходное название документа: Импорт закупочной выгрузки\n\n"
        + f"Сырое содержимое:\n```\n{SAMPLE[:12000]}\n```\n\n"
        + "Верни строго JSON по описанной схеме. Никаких пояснений, markdown-фенсов или текста вокруг — только JSON."
    )
    print(f"full_input len = {len(full_input)}")
    print(f"first 200 chars: {full_input[:200]!r}")
    print()

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    try:
        response = await client.responses.create(model=MODEL, input=full_input)
        text = getattr(response, "output_text", None) or "(no output_text)"
        print("OK")
        print(text[:600])
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        body = getattr(exc, "response", None)
        if body is not None:
            try:
                print(f"body: {body.text[:1000]}")
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
