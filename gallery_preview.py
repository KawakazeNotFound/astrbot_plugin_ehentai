"""单个画廊预览功能 - 处理exhentai.org链接并生成预览图"""

from __future__ import annotations

import asyncio
import base64
import html
import importlib
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .service import EHentaiClient, GalleryResult
from .logger_compat import get_logger

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

ITEM_BLOCK_RE = re.compile(r"<!-- \{\{#items\}\} -->(.*?)<!-- \{\{/items\}\} -->", re.S)
PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


class GalleryPreviewError(RuntimeError):
    """Raised when gallery preview rendering or screenshot fails."""


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


def _render_single_item_template(template_text: str, item: dict[str, Any]) -> str:
    """渲染单个结果卡片模板"""
    # 提取单个结果卡片的模板块
    item_match = ITEM_BLOCK_RE.search(template_text)
    if item_match is None:
        raise GalleryPreviewError("模板中未找到 items 循环块")

    item_block = item_match.group(1)
    rendered_item = _replace_placeholders(item_block, item)
    
    # 只返回单个结果卡片，不需要summary部分
    # 直接从搜索结果框获取 ef-panel 和 ef-result-card 部分
    return rendered_item


def _get_preview_template_path() -> Path:
    """获取预览模板路径"""
    return Path(__file__).resolve().parent / "search_template.html"


def _build_gallery_preview_html(
    gallery: GalleryResult,
    cover_data_uri: str = "",
) -> str:
    """构建单个画廊的预览 HTML"""
    
    # 准备项目数据
    item_data = {
        "index": 1,
        "gid": gallery.gid,
        "cover_url": cover_data_uri if cover_data_uri else gallery.cover_url,
        "title": gallery.title,
        "title_jpn": gallery.title_jpn,
        "rating": f"{gallery.rating:.1f}" if gallery.rating >= 0 else "N/A",
        "tags": " / ".join(gallery.tags) if gallery.tags else "(no tags)",
        "language": "Unknown",  # 从标签或其他地方推断
        "pages": str(gallery.pages),
        "posted": gallery.posted,
    }
    
    # 从标签中尝试提取语言
    if gallery.tags:
        for tag in gallery.tags:
            tag_lower = tag.lower()
            if "english" in tag_lower:
                item_data["language"] = "English"
                break
            elif "chinese" in tag_lower or "中文" in tag:
                item_data["language"] = "Chinese"
                break
            elif "japanese" in tag_lower or "日本語" in tag:
                item_data["language"] = "Japanese"
                break
    
    # 获取模板
    template_path = _get_preview_template_path()
    if not template_path.exists():
        raise GalleryPreviewError(f"模板不存在: {template_path}")
    
    template_text = template_path.read_text(encoding="utf-8")
    
    # 只渲染单个结果卡片部分，不包含summary
    item_match = ITEM_BLOCK_RE.search(template_text)
    if item_match is None:
        raise GalleryPreviewError("模板中未找到 items 循环块")
    
    item_block = item_match.group(1)
    rendered_item = _replace_placeholders(item_block, item_data)
    
    # 构建完整的HTML，包含样式和单个结果卡片
    base_html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gallery Preview</title>
    <style>
        :root {
            --ef-yellow: #fff100;
            --ef-dark: #222222;
            --ef-gray-light: #efefef;
            --ef-gray-mid: #d0d0d0;
            --ef-gray-text: #a0a0a0;
            --ef-gray-deep: #6f6f6f;
            --ef-white: #ffffff;
            --font-mono: 'Courier New', Courier, monospace;
            --font-sans: 'Helvetica Neue', Helvetica, Arial, 'Microsoft YaHei', sans-serif;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            min-height: 100vh;
            background-color: #111;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 36px 16px 48px;
            font-family: var(--font-sans);
        }

        .ef-shell {
            width: min(1200px, 100%);
        }

        .ef-panel {
            position: relative;
            width: 100%;
            background-color: var(--ef-gray-light);
            overflow: hidden;
            box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22);
            background-image:
                linear-gradient(to right, rgba(0, 0, 0, 0.03) 1px, transparent 1px),
                linear-gradient(to bottom, rgba(0, 0, 0, 0.03) 1px, transparent 1px);
            background-size: 40px 40px;
        }

        .ef-result-card {
            padding: 28px 36px 28px 48px;
        }

        .ef-left-bar {
            position: absolute;
            inset: 0 auto 0 0;
            width: 16px;
            background-color: var(--ef-yellow);
            z-index: 10;
        }

        .ef-bg-watermark {
            position: absolute;
            top: 18px;
            right: 28px;
            font-size: clamp(88px, 10vw, 160px);
            font-weight: 900;
            color: transparent;
            letter-spacing: -3px;
            user-select: none;
            z-index: 1;
            background: linear-gradient(180deg, var(--ef-gray-mid) 0%, var(--ef-gray-mid) 42%, transparent 42%, transparent 50%, var(--ef-gray-mid) 50%, var(--ef-gray-mid) 100%);
            background-clip: text;
            -webkit-background-clip: text;
            -webkit-text-stroke: 2px var(--ef-gray-mid);
            opacity: 0.72;
        }

        .ef-bg-watermark-result {
            top: 22px;
            font-size: clamp(72px, 8vw, 128px);
        }

        .ef-top-nav {
            position: relative;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            z-index: 5;
            margin-bottom: 22px;
        }

        .ef-tiny-square {
            width: 6px;
            height: 12px;
            background-color: var(--ef-yellow);
        }

        .ef-top-nav span {
            font-size: 14px;
            font-weight: 700;
            color: var(--ef-gray-text);
            letter-spacing: 1px;
        }

        .ef-result-content {
            position: relative;
            z-index: 5;
            display: flex;
            gap: 24px;
            align-items: stretch;
        }

        .ef-card-left {
            flex: 0 0 240px;
            display: flex;
            flex-direction: column;
        }

        .ef-cover-img {
            width: 100%;
            height: 100%;
            min-height: 320px;
            object-fit: cover;
            border: 2px solid var(--ef-dark);
            box-shadow: 4px 4px 0 rgba(0, 0, 0, 0.1);
            background-color: var(--ef-white);
        }

        .ef-card-right {
            flex: 1;
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        .ef-title-text {
            font-size: 26px;
            color: var(--ef-dark);
            font-weight: 900;
            line-height: 1.2;
            margin-bottom: 8px;
            word-break: break-word;
            background: var(--ef-white);
            padding: 8px 12px;
            border: 1px solid var(--ef-gray-mid);
        }

        .ef-subtitle-text {
            font-size: 15px;
            font-weight: 600;
            color: var(--ef-gray-deep);
            margin-bottom: 16px;
            padding: 0 4px;
        }

        .ef-rating-box {
            align-self: flex-start;
            background: var(--ef-dark);
            color: var(--ef-white);
            padding: 6px 14px;
            font-weight: 700;
            font-size: 16px;
            letter-spacing: 1px;
            margin-bottom: 24px;
            box-shadow: 2px 2px 0 var(--ef-yellow);
        }

        .ef-card-bottom {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            margin-top: auto;
            gap: 20px;
        }

        .ef-tags-box {
            flex: 1;
            background: rgba(255, 255, 255, 0.7);
            border: 1px solid var(--ef-gray-mid);
            padding: 12px 16px;
            font-size: 14px;
            color: var(--ef-gray-deep);
            line-height: 1.5;
            min-height: 80px;
        }

        .ef-tags-label {
            font-weight: 900;
            color: var(--ef-dark);
            display: block;
            margin-bottom: 4px;
        }

        .ef-meta-stack {
            display: flex;
            flex-direction: column;
            gap: 8px;
            align-items: flex-end;
            min-width: 140px;
        }

        .ef-meta-pill {
            background: var(--ef-white);
            border: 2px solid var(--ef-dark);
            padding: 4px 10px;
            font-family: var(--font-mono);
            font-size: 13px;
            font-weight: 700;
            color: var(--ef-dark);
            text-align: right;
            box-shadow: 2px 2px 0 rgba(0, 0, 0, 0.1);
        }

        @media (max-width: 980px) {
            .ef-bg-watermark {
                right: 12px;
            }
        }

        @media (max-width: 720px) {
            body {
                padding: 20px 10px 32px;
            }
            .ef-result-card {
                padding: 20px 18px 18px 30px;
            }
            .ef-result-content {
                flex-direction: column;
            }
            .ef-card-left {
                flex: none;
                width: 100%;
                max-width: 300px;
                margin: 0 auto;
            }
            .ef-card-bottom {
                flex-direction: column;
                align-items: stretch;
            }
            .ef-meta-stack {
                align-items: stretch;
            }
            .ef-meta-pill {
                text-align: center;
            }
        }
    </style>
</head>
<body>
    <main class="ef-shell">
        <article class="ef-panel ef-result-card">
            <div class="ef-left-bar"></div>
            <div class="ef-bg-watermark ef-bg-watermark-result">PREVIEW</div>

            <div class="ef-top-nav">
                <div class="ef-tiny-square"></div>
                <span>GALLERY PREVIEW // GID {{gid}}</span>
            </div>

            {item_html}
        </article>
    </main>
</body>
</html>"""
    
    return base_html.replace("{item_html}", rendered_item)


async def render_gallery_preview_image(
    gallery: GalleryResult,
    output_dir: Path,
) -> Path:
    """渲染单个画廊为预览图"""
    
    logger = get_logger()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    html_path = output_dir / f"gallery_{gallery.gid}.render.html"
    image_path = output_dir / f"gallery_{gallery.gid}.render.jpg"
    
    # 下载封面图作为 data URI
    cover_data_uri = ""
    if gallery.cover_url:
        sem = asyncio.Semaphore(_COVER_FETCH_CONCURRENCY)
        async with httpx.AsyncClient(
            headers=_COVER_HEADERS,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            cover_data_uri = await _fetch_cover_as_data_uri(client, sem, gallery.cover_url)
    
    # 构建 HTML
    html_text = _build_gallery_preview_html(gallery, cover_data_uri)
    html_path.write_text(html_text, encoding="utf-8")
    
    # 使用 Playwright 截图
    try:
        async_playwright = importlib.import_module("playwright.async_api").async_playwright
    except Exception as error:
        raise GalleryPreviewError(
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
        raise GalleryPreviewError(f"HTML 截图失败: {error}") from error
    
    logger.info(f"[画廊预览] 预览图生成完成: {image_path}")
    return image_path


async def fetch_gallery_info(
    client: EHentaiClient,
    gid: str,
    token: str,
) -> Optional[GalleryResult]:
    """从 exhentai/e-hentai 获取单个画廊的详细信息"""
    
    logger = get_logger()
    
    try:
        # 构建画廊 URL
        gallery_url = f"{client.base_url}/g/{gid}/{token}/"
        logger.info(f"[画廊获取] 正在获取画廊信息: {gallery_url}")
        
        # 发送请求
        headers = client._headers_for_url(gallery_url)
        cookies_header = client._cookie_pairs_for_url(gallery_url)
        
        async with client._client() as http_client:
            resp = await http_client.get(
                gallery_url,
                headers=headers,
                cookies=dict(cookies_header) if cookies_header else None,
                timeout=client.timeout,
            )
            resp.raise_for_status()
            
        body = resp.text
        soup = BeautifulSoup(body, "html.parser")
        
        # 解析画廊信息
        # 获取标题
        title_elem = soup.select_one("#gn")
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"
        
        # 获取日文标题
        title_jpn_elem = soup.select_one("#gj")
        title_jpn = title_jpn_elem.get_text(strip=True) if title_jpn_elem else ""
        
        # 获取评分
        rating = -1.0
        rating_elem = soup.select_one(".rating")
        if rating_elem:
            try:
                rating_text = rating_elem.get_text(strip=True)
                # 评分通常是 "4.5" 的格式
                rating = float(rating_text)
            except (ValueError, AttributeError):
                pass
        
        # 获取页数和其他元数据
        pages = 0
        posted = ""
        
        # 查找 gd2 div 内的元数据行
        gd2 = soup.select_one(".gd2")
        if gd2:
            # gd2 包含的是页数、上传时间等信息
            rows = gd2.find_all("div", recursive=False)
            for row in rows:
                text = row.get_text(strip=True)
                # 查找页数行
                if "pages" in text.lower():
                    try:
                        # 提取数字，格式可能是 "250 pages" 或类似的
                        parts = text.lower().split()
                        if parts:
                            pages = int(parts[0])
                    except (ValueError, IndexError):
                        pass
                # 查找上传日期
                if "posted" in text.lower() or any(month in text for month in 
                    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
                    posted = text
        
        # 获取标签
        tags = []
        
        # 尝试从 .gd3 或 .gtl 获取标签
        tag_container = soup.select_one(".gd3")
        if not tag_container:
            tag_container = soup.select_one(".gtl")
        
        if tag_container:
            tag_rows = tag_container.find_all("div", class_=["gt1", "gt2"], recursive=False)
            for tag_row in tag_rows:
                tag_links = tag_row.find_all("a")
                for tag_link in tag_links:
                    tag_text = tag_link.get_text(strip=True)
                    if tag_text:
                        tags.append(tag_text)
        
        # 如果还是没有找到标签，尝试其他选择器
        if not tags:
            tag_elems = soup.select("div.gtl a")
            for tag_elem in tag_elems:
                tag_text = tag_elem.get_text(strip=True)
                if tag_text:
                    tags.append(tag_text)
        
        # 获取封面 URL
        cover_url = ""
        cover_elem = soup.select_one("div.thumb img")
        if cover_elem:
            src = cover_elem.get("src", "")
            if src:
                cover_url = src if src.startswith("http") else f"{client.base_url}{src}"
        
        # 创建 GalleryResult 对象
        gallery = GalleryResult(
            gid=gid,
            token=token,
            title=title,
            url=gallery_url,
            title_jpn=title_jpn,
            rating=rating,
            pages=pages,
            tags=tags,
            posted=posted,
            cover_url=cover_url,
        )
        
        logger.info(f"[画廊获取] 成功获取画廊信息: {title}")
        return gallery
        
    except Exception as error:
        logger.error(f"[画廊获取] 获取画廊信息失败: {type(error).__name__}: {error}")
        return None
