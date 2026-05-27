"""Probe proxyapi.ru from Python using the same SDK the container uses.

Run inside the agent_server container (so it uses the exact same package
versions) like this:

    docker compose -f docker-compose-api.yml exec agent_server \
        python /app/../scripts/probe_python.py

Or just:

    docker compose -f docker-compose-api.yml run --rm agent_server \
        python -c "$(cat scripts/probe_python.py)"

Reads PROXY_API_KEY / PROXY_BASE_URL from env (or .env if python-dotenv is
installed).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    for candidate in (Path(__file__).resolve().parent.parent / ".env",):
        if candidate.is_file():
            load_dotenv(candidate)
            return


_load_dotenv()


API_KEY = os.getenv("PROXY_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
BASE_URL = os.getenv("PROXY_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.proxyapi.ru/openai/v1"


async def test_responses_minimal(model: str, prompt: str) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    print(f"── responses · {model} · input_len={len(prompt)} ──")
    try:
        response = await client.responses.create(model=model, input=prompt)
        text = getattr(response, "output_text", None) or "(no output_text)"
        print(f"OK: {text[:200]}")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
        # If it's an APIError, print the raw response body for clues
        body = getattr(exc, "response", None)
        if body is not None:
            try:
                print(f"  body: {body.text[:500]}")
            except Exception:
                pass
    print()


async def test_chat_minimal(model: str, prompt: str) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    print(f"── chat.completions · {model} · input_len={len(prompt)} ──")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or "(empty)"
        print(f"OK: {text[:200]}")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
    print()


async def test_chat_with_system(model: str, system: str, user: str) -> None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL)
    print(f"── chat+system · {model} ──")
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = response.choices[0].message.content or "(empty)"
        print(f"OK: {text[:200]}")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
    print()


async def test_via_raw_httpx(model: str, prompt: str) -> None:
    import httpx

    print(f"── raw httpx /chat/completions · {model} ──")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{BASE_URL.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            print(f"HTTP {response.status_code}")
            print(f"body: {response.text[:500]}")
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}")
    print()


async def main() -> None:
    print(f"BASE = {BASE_URL}")
    print(f"KEY  = {API_KEY[:10]}...{API_KEY[-4:]}  (len={len(API_KEY)})")
    print(f"OPENAI_API_KEY env = {os.getenv('OPENAI_API_KEY')!r}")
    print(f"PROXY_API_KEY env  = {os.getenv('PROXY_API_KEY')!r}")
    print()

    # Tier 1: minimal call, exact shape from user's working snippet.
    await test_responses_minimal("gpt-4o", "Привет")
    await test_chat_minimal("gpt-4o", "Привет")

    # Tier 2: with system message (which is what our parser does, essentially).
    await test_chat_with_system(
        "gpt-4o",
        "Ты парсер. Возвращай только JSON.",
        "Верни {\"ok\": true}",
    )

    # Tier 3: longer input similar to what our parser sends.
    long_input = (
        "Парсь следующий BOM в JSON структуру.\n\n"
        "Жакет oversize SS26\n"
        "- основная ткань: шерсть 350 г/м², 320 м\n"
        "- подкладка: вискоза 120 г/м², 280 м\n"
        "- пуговицы: рог, 12 шт\n"
    ) * 30  # ~ 3-4 KB
    await test_responses_minimal("gpt-4o", long_input)
    await test_chat_minimal("gpt-4o", long_input)

    # Tier 4: raw httpx bypassing the SDK entirely, from inside the container.
    await test_via_raw_httpx("gpt-4o", "Привет")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
