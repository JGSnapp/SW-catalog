from __future__ import annotations

import os
from urllib.parse import quote

import httpx
import trafilatura


def env_enabled(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_reader_url(target_url: str) -> str:
    reader_base = (os.getenv("JINA_READER_BASE_URL") or "https://r.jina.ai/").rstrip("/") + "/"
    safe_chars = ":/?&=#%@+;,!~*'()[]"
    return f"{reader_base}{quote(target_url, safe=safe_chars)}"


def extract_text(html: str, url: str) -> str:
    extracted = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=True,
        output_format="txt",
    )
    return (extracted or "").strip()


def should_fallback(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return True
    return exc.response.status_code >= 400


async def read_with_jina(url: str, timeout: int = 60) -> dict[str, object]:
    request_url = build_reader_url(url)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(request_url)
        response.raise_for_status()
    return {
        "url": url,
        "reader": "jina",
        "request_url": request_url,
        "status_code": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "text": response.text,
    }


async def read_with_direct_httpx(url: str, timeout: int = 60) -> dict[str, object]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    text = extract_text(response.text, url)
    if not text:
        raise RuntimeError("direct_httpx reader extracted empty text")
    return {
        "url": url,
        "reader": "direct_httpx",
        "request_url": str(response.url),
        "status_code": response.status_code,
        "content_type": content_type,
        "text": text,
    }


async def read_with_playwright(url: str, timeout: int = 60) -> dict[str, object]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("playwright reader is not installed") from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                locale="ru-RU",
            )
            response = await page.goto(url, wait_until="networkidle", timeout=max(1, int(timeout)) * 1000)
            html = await page.content()
            text = extract_text(html, url)
            if not text:
                raise RuntimeError("playwright reader extracted empty text")
            return {
                "url": url,
                "reader": "playwright",
                "request_url": page.url,
                "status_code": response.status if response else 0,
                "content_type": response.headers.get("content-type", "") if response else "",
                "text": text,
            }
        finally:
            await browser.close()


async def read_url(url: str, timeout: int = 60) -> dict[str, object]:
    errors: list[str] = []
    try:
        return await read_with_jina(url=url, timeout=timeout)
    except Exception as exc:
        errors.append(f"jina: {exc}")
        if not should_fallback(exc):
            raise

    if env_enabled("DIRECT_READER_ENABLED", True):
        try:
            result = await read_with_direct_httpx(url=url, timeout=timeout)
            result["fallback_errors"] = errors
            return result
        except Exception as exc:
            errors.append(f"direct_httpx: {exc}")

    if env_enabled("PLAYWRIGHT_READER_ENABLED", True):
        try:
            result = await read_with_playwright(url=url, timeout=timeout)
            result["fallback_errors"] = errors
            return result
        except Exception as exc:
            errors.append(f"playwright: {exc}")

    raise RuntimeError("; ".join(errors))
