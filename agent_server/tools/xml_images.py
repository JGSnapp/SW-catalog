"""Async image search via XMLStock Yandex Live.

Ported from C:/Users/JGSnapp/Desktop/Stroki/XMLSearch/xml_images.py and adapted
for async httpx usage inside the FastAPI process. The base URL is taken from
the environment variable XMLSTOCK_YANDEXLIVE_URL.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx


IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|gif|bmp|tiff)(\?.*)?$", re.IGNORECASE)


def _env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 300) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def _env_float(name: str, default: float, *, min_value: float = 0.1, max_value: float = 60.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def _build_url(base_url: str, extra_params: dict[str, object]) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in extra_params.items() if value is not None})
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query, doseq=True), parsed.fragment)
    )


def _looks_like_image_url(value: str) -> bool:
    if not value:
        return False
    if not (value.startswith("http://") or value.startswith("https://")):
        return False
    lower = value.lower()
    return bool(IMG_EXT_RE.search(value)) or "/images" in lower or "img" in lower


def _extract_image_urls(root: ET.Element) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for element in root.iter():
        if not element.text:
            continue
        text = element.text.strip()
        if not text or not (text.startswith("http://") or text.startswith("https://")):
            continue
        if text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return [value for value in ordered if _looks_like_image_url(value)]


def _get_error(root: ET.Element) -> tuple[int | None, str]:
    err = root.find(".//error")
    if err is None:
        return None, ""
    raw = err.get("code") or ""
    code = int(raw) if raw.lstrip("-").isdigit() else None
    return code, (err.text or "").strip()


async def search_images(
    query: str,
    *,
    limit: int = 10,
    lr: int | None = None,
    page: int = 0,
    device: str | None = None,
    domain: str | None = None,
    base_url: str | None = None,
    retries: int | None = None,
    wait_seconds: float | None = None,
) -> list[str]:
    base = base_url or os.getenv("XMLSTOCK_YANDEXLIVE_URL")
    if not base:
        raise RuntimeError("XMLSTOCK_YANDEXLIVE_URL is not set")
    url = _build_url(
        base,
        {
            "tbm": "images",
            "query": query,
            "lr": lr,
            "page": page,
            "device": device,
            "domain": domain,
        },
    )
    last_error: str | None = None
    retries = retries or _env_int("IMAGE_SEARCH_RETRIES", 3, min_value=1, max_value=8)
    wait_seconds = wait_seconds or _env_float("IMAGE_SEARCH_WAIT_SECONDS", 1.5, min_value=0.2, max_value=20.0)
    timeout = _env_int("IMAGE_SEARCH_TIMEOUT_SECONDS", 15, min_value=5, max_value=120)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _attempt in range(max(1, retries)):
            response = await client.get(url)
            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                await asyncio.sleep(wait_seconds)
                continue
            try:
                root = ET.fromstring(response.content)
            except ET.ParseError as exc:
                last_error = f"parse: {exc}"
                await asyncio.sleep(wait_seconds)
                continue
            code, message = _get_error(root)
            if code in (20, 21, 22, 23, 24, 25, 110):
                last_error = f"code={code}: {message}"
                await asyncio.sleep(wait_seconds if code not in (20, 110) else max(wait_seconds, 6.0))
                continue
            if code is not None:
                raise RuntimeError(f"XMLStock error code={code}: {message}")
            return _extract_image_urls(root)[: max(0, int(limit))]
    raise RuntimeError(f"image search did not finish after retries; last error: {last_error}")


async def download_image(url: str, destination: Path, *, timeout: int | None = None) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    timeout = timeout or _env_int("IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 15, min_value=5, max_value=120)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "SW-catalog/1.0"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        suffix = destination.suffix
        if not suffix:
            if "jpeg" in content_type or "jpg" in content_type:
                suffix = ".jpg"
            elif "png" in content_type:
                suffix = ".png"
            elif "webp" in content_type:
                suffix = ".webp"
            else:
                suffix = ".img"
            destination = destination.with_suffix(suffix)
        destination.write_bytes(response.content)
    return destination


async def find_and_download_image(
    query: str,
    *,
    destination_dir: Path,
    file_stem: str,
    limit: int = 5,
    base_url: str | None = None,
) -> tuple[Path, str] | None:
    """Returns (local_path, source_url) for the first downloadable image, or None."""

    candidates = await search_images(query, limit=limit, base_url=base_url)
    for source_url in candidates:
        try:
            local_path = await download_image(source_url, destination_dir / file_stem)
            return local_path, source_url
        except Exception:
            continue
    return None


# Small jitter so the agent does not hammer the search service in tight loops.
async def jitter_sleep(min_seconds: float = 0.2, max_seconds: float = 0.8) -> None:
    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
