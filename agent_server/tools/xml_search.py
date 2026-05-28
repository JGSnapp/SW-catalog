from __future__ import annotations

import os
import random
import asyncio
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
import xml.etree.ElementTree as ET

import httpx


def env_int(name: str, default: int, *, min_value: int = 1, max_value: int = 300) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def env_float(name: str, default: float, *, min_value: float = 0.1, max_value: float = 60.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(min_value, min(max_value, parsed))


def build_url(base_url: str, extra_params: dict[str, object]) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({key: str(value) for key, value in extra_params.items() if value is not None})
    new_query = urlencode(query, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def parse_xml(xml_bytes: bytes) -> ET.Element:
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        preview = xml_bytes[:300].decode("utf-8", errors="replace").replace("\n", " ").strip()
        raise RuntimeError(f"Invalid XMLSearch response: {exc}. Response preview: {preview}") from exc


def get_error(root: ET.Element):
    err = root.find(".//error")
    if err is None:
        return None, None
    code_raw = err.get("code")
    code = int(code_raw) if code_raw and code_raw.lstrip("-").isdigit() else None
    return code, (err.text or "").strip()


def extract_doc_text(node: ET.Element) -> str:
    return ET.tostring(node, encoding="unicode", method="text").strip()


def extract_results(root: ET.Element, limit_docs: int, limit_passages: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for doc in root.findall(".//doc"):
        url = (doc.findtext("url") or "").strip()
        if not url:
            continue
        passages: list[str] = []
        for passage in doc.findall(".//passages/passage"):
            text = extract_doc_text(passage)
            if text:
                passages.append(text)
            if len(passages) >= limit_passages:
                break
        results.append(
            {
                "title": (doc.findtext("title") or "").strip(),
                "url": url,
                "passages": passages,
            }
        )
        if len(results) >= limit_docs:
            break
    return results


async def fetch_with_retries(
    url: str,
    retries: int | None = None,
    min_wait: float | None = None,
    max_wait: float | None = None,
) -> ET.Element:
    retries = retries or env_int("XML_SEARCH_RETRIES", 3, min_value=1, max_value=8)
    min_wait = min_wait or env_float("XML_SEARCH_MIN_WAIT_SECONDS", 2.0, min_value=0.2, max_value=30.0)
    max_wait = max_wait or env_float("XML_SEARCH_MAX_WAIT_SECONDS", 4.0, min_value=min_wait, max_value=60.0)
    timeout = env_int("XML_SEARCH_TIMEOUT_SECONDS", 15, min_value=5, max_value=120)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for _attempt in range(1, retries + 1):
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            stripped = response.content.lstrip()
            if not stripped.startswith(b"<"):
                preview = response.text[:300].replace("\n", " ").strip()
                raise RuntimeError(
                    f"XMLSearch returned non-XML response. "
                    f"status={response.status_code}, content_type={content_type}, preview={preview}"
                )
            root = parse_xml(response.content)
            code, _text = get_error(root)
            if code in (210, 202, 110):
                await asyncio.sleep(random.uniform(min_wait, max_wait))
                continue
            return root
    raise RuntimeError("XML search did not finish after retries")


async def search_web(
    query: str,
    docs: int = 10,
    maxpassages: int = 5,
    lr: int | None = None,
    groupby: str = "10",
) -> list[dict[str, object]]:
    base = os.getenv("XMLSTOCK_GOOGLE_URL")
    if not base:
        raise RuntimeError("XMLSTOCK_GOOGLE_URL is not set")
    maxpassages = max(1, min(5, int(maxpassages)))
    url = build_url(
        base,
        {
            "query": query,
            "groupby": groupby,
            "maxpassages": maxpassages,
            "lr": lr,
        },
    )
    root = await fetch_with_retries(url)
    code, text = get_error(root)
    if code is not None:
        raise RuntimeError(f"XMLStock error code={code}: {text}")
    return extract_results(root, limit_docs=int(docs), limit_passages=maxpassages)
