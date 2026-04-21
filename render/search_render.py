from __future__ import annotations

import asyncio
import base64
import html
import importlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from ..utils.logger_compat import get_logger
from ..logic.search_logic import build_search_render_payload

_COVER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://e-hentai.org/",
}

_COVER_BASE_URL = "https://e-hentai.org/"
_COVER_FETCH_CONCURRENCY = 2
_COVER_FETCH_RETRY = 3


def _normalize_cover_url(url: str) -> str:
    if not url:
        return ""
    value = str(url).strip()
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return urljoin(_COVER_BASE_URL, value)


async def _fetch_cover_as_data_uri(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url: str,
    timeout: float = 15.0,
) -> str:
    """下载封面图并返回 base64 data URI；失败时返回空字符串。"""
    normalized_url = _normalize_cover_url(url)
    if not normalized_url:
        return ""

    async with sem:
        for attempt in range(1, _COVER_FETCH_RETRY + 1):
            try:
                resp = await client.get(normalized_url, timeout=timeout)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if not content_type.startswith("image/"):
                    return ""
                b64 = base64.b64encode(resp.content).decode("ascii")
                return f"data:{content_type};base64,{b64}"
            except Exception:
                if attempt >= _COVER_FETCH_RETRY:
                    return ""
                await asyncio.sleep(0.25 * attempt)
    return ""

ITEM_BLOCK_RE = re.compile(r"<!-- \{\{#items\}\} -->(.*?)<!-- \{\{/items\}\} -->", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


class SearchRenderError(RuntimeError):
    """Raised when HTML template rendering or screenshot fails."""


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _escape_text(value: Any, fallback: str = "-") -> str:
    normalized = _normalize_text(value)
    if not normalized:
        normalized = fallback
    return html.escape(normalized, quote=True)


def _replace_placeholders(template: str, values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        return _escape_text(values.get(key, ""))

    return PLACEHOLDER_RE.sub(repl, template)


def _render_template(template_text: str, payload: dict[str, Any]) -> str:
    item_match = ITEM_BLOCK_RE.search(template_text)
    if item_match is None:
        raise SearchRenderError("模板中未找到 items 循环块")

    item_block = item_match.group(1)
    rendered_items = "\n".join(
        _replace_placeholders(item_block, item_values)
        for item_values in payload.get("items", [])
    )

    result = ITEM_BLOCK_RE.sub(rendered_items, template_text)
    top_level_values = {key: value for key, value in payload.items() if key != "items"}
    return _replace_placeholders(result, top_level_values)


def _project_root() -> Path:
    """获取插件根目录（AstrBot 版本）"""
    return Path(__file__).resolve().parent.parent


def _template_path() -> Path:
    """获取搜索结果模板文件路径"""
    return Path(__file__).resolve().parent / "templates" / "search_template.html"


def _build_template_payload(
    keyword: str,
    results,
    display_limit: int,
    bot_page: int,
    total_fetched: int,
) -> dict[str, Any]:
    payload = build_search_render_payload(keyword, results, display_limit)

    # 用分页抓取总数覆盖 total_count，让总览更符合 /search 分页语义
    if total_fetched > 0:
        payload["total_count"] = total_fetched

    payload["schema_version"] = f"search-render-v1 | page {bot_page}"

    rendered_items: list[dict[str, Any]] = []
    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []

    for item in items:
        new_item = dict(item)
        tags = new_item.get("tags", [])
        if isinstance(tags, list):
            new_item["tags"] = " / ".join(tags) if tags else "(no tags)"
        rendered_items.append(new_item)

    payload["items"] = rendered_items
    return payload


async def render_search_results_image(
    keyword: str,
    results,
    display_limit: int,
    bot_page: int,
    total_fetched: int,
    output_dir: Path,
) -> Path:
    template_path = _template_path()
    if not template_path.exists():
        raise SearchRenderError(f"模板不存在: {template_path}")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / f"search_p{bot_page}.render.html"
    image_path = output_dir / f"search_p{bot_page}.render.jpg"

    payload = _build_template_payload(keyword, results, display_limit, bot_page, total_fetched)

    # 并发下载封面图 → base64 data URI，避免 file:// 上下文无法加载外部 HTTPS 图片
    items = payload.get("items", [])
    if items:
        cover_urls = [item.get("cover_url", "") for item in items]
        sem = asyncio.Semaphore(_COVER_FETCH_CONCURRENCY)
        async with httpx.AsyncClient(
            headers=_COVER_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            data_uris = await asyncio.gather(
                *(_fetch_cover_as_data_uri(client, sem, u) for u in cover_urls)
            )

        success_count = 0
        for item, data_uri in zip(items, data_uris):
            if data_uri:
                item["cover_url"] = data_uri
                success_count += 1

        if success_count < len(items):
            get_logger().warning(
                "[搜索渲染] 封面内嵌完成 %d/%d，部分封面下载失败",
                success_count,
                len(items),
            )

    template_text = template_path.read_text(encoding="utf-8")
    html_text = _render_template(template_text, payload)
    html_path.write_text(html_text, encoding="utf-8")

    try:
        async_playwright = importlib.import_module("playwright.async_api").async_playwright
    except Exception as error:
        raise SearchRenderError(
            "未安装 playwright，无法将 HTML 渲染为图片。"
            "请安装: pip install playwright && playwright install chromium"
        ) from error

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(html_path.as_uri(), wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(1200)
            await page.screenshot(
                path=str(image_path),
                full_page=True,
                type="jpeg",
                quality=80,
            )
            await browser.close()
    except Exception as error:
        raise SearchRenderError(f"HTML 截图失败: {error}") from error

    return image_path
