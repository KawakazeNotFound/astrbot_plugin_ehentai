"""
AstrBot E-Hentai 搜索下载插件
转换自 nonebot-plugin-ehentai
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import time
from datetime import datetime
from math import ceil
from pathlib import Path
from uuid import uuid4
from typing import Optional
from urllib.parse import unquote, urlparse

import httpx
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger as astrbot_logger, AstrBotConfig
from astrbot.api.message_components import (
    Plain, Image, At, File
)
from astrbot.api.event import MessageChain
from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .utils.config_loader import PluginConfig
from .utils.logger_compat import init_logger, get_logger
from .core.service import EHentaiClient, ImageSearchOptions, SearchOptions, CHROME_DESKTOP_USER_AGENT, GalleryResult
from .logic.search_logic import (
    SearchExecutionError,
    execute_gallery_search,
    execute_gallery_image_search_paged,
    execute_gallery_search_paged,
    format_search_results_message,
    pick_first_result,
)
from .render.search_render import SearchRenderError, render_search_results_image
from .render.gallery_preview import (
    GalleryPreviewError,
    fetch_gallery_info,
    render_gallery_preview_image,
)
from .storage.r2 import init_r2_manager, get_r2_manager
from .storage.d1 import init_d1_manager, get_d1_manager


class EHentaiPlugin(Star):
    """E-Hentai 搜索下载插件"""
    
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        
        # 初始化 logger
        init_logger(astrbot_logger)
        
        # 获取插件配置
        self.plugin_config = PluginConfig(config or {})
        
        # 存储每个会话最近一次返回的搜索结果页面列表，以支持序号下载
        self._last_search_results = {}
        
        # 记录日志
        get_logger().info("[E-Hentai插件] 插件已初始化")
        
        # 立即启动异步初始化任务 (AstrBot 不一定会调用 __aenter__)
        asyncio.create_task(self._init_managers())
        
    async def _init_managers(self):
        """异步初始化系统管理器"""
        get_logger().info("[E-Hentai插件] 开始初始化 R2 和 D1 管理器")
        await init_r2_manager(self.plugin_config)
        await init_d1_manager(self.plugin_config)
        get_logger().info("[E-Hentai插件] R2 和 D1 初始化完成")
        
        # 启动后台自动清理任务
        asyncio.create_task(self._auto_cleanup_task())

    async def _auto_cleanup_task(self):
        """本地缓存自动清理后台任务"""
        logger = get_logger()
        logger.info("[清理任务] 自动清理任务已启动...")
        last_cleaned_date = None

        while True:
            try:
                if self.plugin_config.ehentai_auto_cleanup_local:
                    now = datetime.now()
                    current_date = now.date()
                    target_time_str = self.plugin_config.ehentai_auto_cleanup_time or "03:00"
                    
                    # 当时间匹配且今天尚未清理时执行
                    if now.strftime("%H:%M") == target_time_str and last_cleaned_date != current_date:
                        download_dir = Path(self.plugin_config.ehentai_download_dir)
                        if download_dir.exists():
                            logger.info(f"[清理任务] 达到设定的清理时间 {target_time_str}，开始清理 24 小时前的本地缓存...")
                            now_ts = time.time()
                            deleted_count = 0
                            
                            # 遍历目录及子目录：删除修改时间超 24 小时的文件
                            for file_path in download_dir.rglob('*'):
                                if file_path.is_file():
                                    file_age_hours = (now_ts - file_path.stat().st_mtime) / 3600
                                    if file_age_hours > 24:
                                        try:
                                            file_path.unlink()
                                            deleted_count += 1
                                        except Exception as e:
                                            logger.warning(f"[清理任务] 无法删除 {file_path}: {e}")
                            
                            logger.info(f"[清理任务] 日常自动清理完成，共删除了 {deleted_count} 个存活超过 24 小时的缓存文件。")
                        
                        last_cleaned_date = current_date
            except Exception as e:
                logger.error(f"[清理任务] 自动清理处理异常: {e}")
                
            # 每 30 秒检查一次是否到达设定时间
            await asyncio.sleep(30)
    
    async def __aenter__(self):
        """向后兼容"""
        return self
    
    def build_client(self) -> EHentaiClient:
        """构建 EHentai 客户端"""
        return EHentaiClient(
            site=self.plugin_config.ehentai_site,
            base_url="",  # base_url 由 site 参数自动决定，不再从配置读取
            cookie="",
            ipb_member_id=self.plugin_config.ehentai_ipb_member_id,
            ipb_pass_hash=self.plugin_config.ehentai_ipb_pass_hash,
            igneous=self.plugin_config.ehentai_igneous,
            cf_clearance=self.plugin_config.ehentai_cf_clearance,
            user_agent=CHROME_DESKTOP_USER_AGENT,
            timeout=self.plugin_config.ehentai_timeout,
            proxy=self.plugin_config.ehentai_proxy,
            backend="httpx",
            http3=True,
            desktop_site=self.plugin_config.ehentai_desktop_site,
            impersonate="chrome124",
            enable_direct_ip=self.plugin_config.ehentai_enable_direct_ip,
            curl_cffi_skip_on_error=True,
            cloudflare_worker_url=self.plugin_config.ehentai_cloudflare_worker_url,
        )
    
    def build_search_options(self) -> SearchOptions:
        """构建搜索选项"""
        return SearchOptions(
            f_cats=self.plugin_config.ehentai_search_f_cats,
            advsearch=self.plugin_config.ehentai_search_advsearch,
            f_sh=False,
            f_sto=False,
            f_sfl=False,
            f_sfu=False,
            f_sft=False,
            f_srdd=0,
            f_spf=0,
            f_spt=0,
        )

    @staticmethod
    def _build_session_key(event: AstrMessageEvent) -> str:
        sender_id = getattr(event.message_obj.sender, "user_id", "unknown")
        group_id = getattr(event.message_obj, "group_id", "private")
        return f"{group_id}_{sender_id}"

    @staticmethod
    def _extract_image_reference_from_event(event: AstrMessageEvent) -> str:
        message_chain = getattr(event.message_obj, "message", None) or []
        for component in message_chain:
            value = EHentaiPlugin._extract_image_reference_from_component(component)
            if value:
                return value

        return ""

    @staticmethod
    def _extract_image_reference_from_component(component) -> str:
        comp_type = str(getattr(component, "type", "")).lower()
        if not comp_type and isinstance(component, dict):
            comp_type = str(component.get("type", "") or "").lower()

        class_name = component.__class__.__name__.lower()
        is_image = "image" in comp_type or class_name == "image"
        if is_image:
            for attr in ("file", "url", "path"):
                value = ""
                if isinstance(component, dict):
                    value = str(component.get(attr, "") or "").strip()
                else:
                    value = str(getattr(component, attr, "") or "").strip()
                if value:
                    return value

        # 兼容引用消息：Reply 组件中可能携带 chain，递归查找其中的图片段
        is_reply = "reply" in comp_type or class_name == "reply"
        if is_reply:
            nested_chain = []
            if isinstance(component, dict):
                nested_chain = component.get("chain") or []
            else:
                nested_chain = getattr(component, "chain", None) or []
            for nested in nested_chain:
                nested_value = EHentaiPlugin._extract_image_reference_from_component(nested)
                if nested_value:
                    return nested_value

        return ""

    @staticmethod
    def _looks_like_image_source(text: str) -> bool:
        raw = (text or "").strip()
        if not raw:
            return False
        if raw.startswith(("http://", "https://", "file://", "data:image/", "base64://")):
            return True
        candidate = Path(raw).expanduser()
        return candidate.exists() and candidate.is_file()

    async def _materialize_image_source(self, source: str, output_dir: Path) -> tuple[Path, bool]:
        """将图片来源（URL/本地路径/base64）转为可上传文件。返回 (path, should_cleanup)。"""
        raw = (source or "").strip()
        if not raw:
            raise RuntimeError("未提供图片来源")

        output_dir.mkdir(parents=True, exist_ok=True)

        if raw.startswith("file://"):
            file_path = Path(unquote(urlparse(raw).path))
            if not file_path.exists() or not file_path.is_file():
                raise RuntimeError(f"图片文件不存在: {file_path}")
            return file_path, False

        local_candidate = Path(raw).expanduser()
        if local_candidate.exists() and local_candidate.is_file():
            return local_candidate, False

        if raw.startswith("data:image/"):
            header, sep, payload = raw.partition(",")
            if not sep or not payload:
                raise RuntimeError("data:image 格式不正确")

            ext_match = re.search(r"data:image/([a-zA-Z0-9.+-]+)", header)
            ext = ext_match.group(1).lower() if ext_match else "jpg"
            if ext == "jpeg":
                ext = "jpg"

            temp_path = output_dir / f"imgsearch_{uuid4().hex}.{ext}"
            temp_path.write_bytes(base64.b64decode(payload, validate=False))
            return temp_path, True

        if raw.startswith("base64://"):
            payload = raw[len("base64://"):]
            temp_path = output_dir / f"imgsearch_{uuid4().hex}.jpg"
            temp_path.write_bytes(base64.b64decode(payload, validate=False))
            return temp_path, True

        if raw.startswith("http://") or raw.startswith("https://"):
            async with httpx.AsyncClient(
                timeout=self.plugin_config.ehentai_timeout,
                verify=False,
                follow_redirects=True,
                proxy=self.plugin_config.ehentai_proxy or None,
            ) as client:
                response = await client.get(
                    raw,
                    headers={"User-Agent": CHROME_DESKTOP_USER_AGENT},
                )
                response.raise_for_status()

            suffix = Path(urlparse(raw).path).suffix.lower()
            if not suffix or len(suffix) > 8:
                content_type = (response.headers.get("Content-Type", "").split(";", 1)[0]).strip().lower()
                suffix_map = {
                    "image/jpeg": ".jpg",
                    "image/jpg": ".jpg",
                    "image/png": ".png",
                    "image/webp": ".webp",
                    "image/gif": ".gif",
                    "image/bmp": ".bmp",
                }
                suffix = suffix_map.get(content_type, ".jpg")

            temp_path = output_dir / f"imgsearch_{uuid4().hex}{suffix}"
            temp_path.write_bytes(response.content)
            return temp_path, True

        raise RuntimeError("无法识别图片来源，请发送图片或提供 http/https 图片链接")
    
    @filter.command("search")
    async def handle_search(self, event: AstrMessageEvent):
        """搜索 E-Hentai 本子
        
        用法: /search <关键词> [--page N]
        """
        logger = get_logger()
        
        raw = event.message_str.strip()
        # 移除命令前缀 "search "
        if raw.startswith("search "):
            raw = raw[7:]  # len("search ") = 7
        logger.info(f"[搜索处理] 开始处理搜索请求: raw='{raw}'")
        
        # 解析 --page N 参数
        configured_results_per_page = self.plugin_config.ehentai_max_results
        _RESULTS_PER_PAGE = configured_results_per_page if configured_results_per_page > 0 else 5
        _MAX_EH_PAGES = 3
        
        page_match = re.search(r'--page\s+(\d+)', raw)
        bot_page = int(page_match.group(1)) if page_match else 1
        if bot_page < 1:
            bot_page = 1
        keyword = re.sub(r'--page\s+\d+', '', raw).strip()
        
        if not keyword:
            yield event.plain_result("用法: /search <关键词> [--page N]")
            return
        
        logger.info(f"[搜索处理] keyword='{keyword}', bot_page={bot_page}")
        
        client = self.build_client()
        options = self.build_search_options()
        
        try:
            page_results, total_fetched = await execute_gallery_search_paged(
                client,
                keyword,
                bot_page,
                _RESULTS_PER_PAGE,
                _MAX_EH_PAGES,
                options,
            )
            # 记录搜索结果到当前会话（用于序号下载）
            session_key = self._build_session_key(event)
            self._last_search_results[session_key] = page_results
        except SearchExecutionError as error:
            yield event.plain_result(f"搜索失败: {error}")
            return
        
        logger.info(f"[搜索处理] 分页搜索成功，当前页 {len(page_results)} 条，共抓取 {total_fetched} 条")
        
        if not page_results:
            if bot_page > 1:
                yield event.plain_result(f"第 {bot_page} 页没有更多结果了")
            else:
                yield event.plain_result("没有找到结果，或当前 Cookie 权限不足")
            return
        
        # 尝试渲染为图片；失败时回退为文本
        render_dir = Path(self.plugin_config.ehentai_download_dir) / "search_render"
        try:
            image_path = await render_search_results_image(
                keyword=keyword,
                results=page_results,
                display_limit=_RESULTS_PER_PAGE,
                bot_page=bot_page,
                total_fetched=total_fetched,
                output_dir=render_dir,
            )
            yield event.image_result(str(image_path))
        except SearchRenderError as error:
            logger.warning(f"[搜索处理] 渲染搜索图失败，回退文本: {error}")
            message_text = format_search_results_message(
                keyword, page_results, _RESULTS_PER_PAGE, bot_page=bot_page, total_fetched=total_fetched
            )
            yield event.plain_result(message_text)
        except Exception as error:
            logger.warning(f"[搜索处理] 搜索渲染图发送失败，回退文本: {error}")
            message_text = format_search_results_message(
                keyword, page_results, _RESULTS_PER_PAGE, bot_page=bot_page, total_fetched=total_fetched
            )
            yield event.plain_result(message_text)

    @filter.command("imgsearch")
    async def handle_image_search(self, event: AstrMessageEvent):
        """上传图片进行搜索。

        用法:
        /imgsearch [图片URL] [--page N] [--similar] [--covers] [--expunged]
        或发送 /imgsearch 并附带一张图片。
        """
        logger = get_logger()

        raw = event.message_str.strip()
        if raw.startswith("imgsearch "):
            raw = raw[10:]
        elif raw == "imgsearch":
            raw = ""

        page_match = re.search(r"--page\s+(\d+)", raw)
        bot_page = int(page_match.group(1)) if page_match else 1
        if bot_page < 1:
            bot_page = 1

        use_similarity_scan = bool(re.search(r"--(?:similar|uss)\b", raw))
        only_search_covers = bool(re.search(r"--covers\b", raw))
        show_expunged = bool(re.search(r"--(?:exp|expunged)\b", raw))

        cleaned = re.sub(r"--page\s+\d+", "", raw)
        cleaned = re.sub(r"--(?:similar|uss|covers|exp|expunged)\b", "", cleaned)
        source_arg = cleaned.strip()

        image_source = source_arg or self._extract_image_reference_from_event(event)
        if not image_source:
            yield event.plain_result("请在 60 秒内发送一张图片，或发送图片 URL。")

            @session_waiter(timeout=60, record_history_chains=False)
            async def wait_for_image(controller: SessionController, wait_event: AstrMessageEvent):
                nonlocal image_source

                waited_source = self._extract_image_reference_from_event(wait_event)
                if not waited_source:
                    text_source = wait_event.message_str.strip()
                    if self._looks_like_image_source(text_source):
                        waited_source = text_source

                if waited_source:
                    image_source = waited_source
                    controller.stop()
                    return

                await wait_event.send(wait_event.plain_result("未检测到图片或 URL，请继续发送。"))
                controller.keep(timeout=60, reset_timeout=True)

            try:
                await wait_for_image(event)
            except TimeoutError:
                yield event.plain_result("等待超时：未收到图片或图片 URL。")
                event.stop_event()
                return
            except Exception as error:
                logger.warning(f"[图片搜索处理] 等待图片输入失败: {error}")
                yield event.plain_result(f"等待图片输入失败: {error}")
                event.stop_event()
                return
            finally:
                event.stop_event()

        if not image_source:
            yield event.plain_result(
                "用法: /imgsearch [图片URL] [--page N] [--similar] [--covers] [--expunged]\n"
                "也可以直接发送: /imgsearch + 一张图片"
            )
            return

        image_input_dir = Path(self.plugin_config.ehentai_download_dir) / "image_search_input"
        image_path: Optional[Path] = None
        should_cleanup = False

        try:
            image_path, should_cleanup = await self._materialize_image_source(image_source, image_input_dir)
        except Exception as error:
            logger.warning(f"[图片搜索处理] 读取图片失败: {error}")
            yield event.plain_result(f"读取图片失败: {error}")
            return

        configured_results_per_page = self.plugin_config.ehentai_max_results
        _RESULTS_PER_PAGE = configured_results_per_page if configured_results_per_page > 0 else 5
        _MAX_EH_PAGES = 3

        client = self.build_client()
        image_options = ImageSearchOptions(
            use_similarity_scan=use_similarity_scan,
            only_search_covers=only_search_covers,
            show_expunged=show_expunged,
        )

        try:
            page_results, total_fetched = await execute_gallery_image_search_paged(
                client=client,
                image_path=image_path,
                bot_page=bot_page,
                results_per_page=_RESULTS_PER_PAGE,
                max_eh_pages=_MAX_EH_PAGES,
                options=image_options,
            )
            session_key = self._build_session_key(event)
            self._last_search_results[session_key] = page_results
        except SearchExecutionError as error:
            yield event.plain_result(f"图片搜索失败: {error}")
            if should_cleanup and image_path is not None:
                image_path.unlink(missing_ok=True)
            return

        if should_cleanup and image_path is not None:
            image_path.unlink(missing_ok=True)

        logger.info(
            f"[图片搜索处理] 搜索成功，当前页 {len(page_results)} 条，共抓取 {total_fetched} 条"
        )

        if not page_results:
            if bot_page > 1:
                yield event.plain_result(f"第 {bot_page} 页没有更多结果了")
            else:
                yield event.plain_result("没有找到结果，或当前 Cookie 权限不足")
            return

        render_keyword = "图片搜索"
        render_dir = Path(self.plugin_config.ehentai_download_dir) / "search_render"
        try:
            image_result_path = await render_search_results_image(
                keyword=render_keyword,
                results=page_results,
                display_limit=_RESULTS_PER_PAGE,
                bot_page=bot_page,
                total_fetched=total_fetched,
                output_dir=render_dir,
            )
            yield event.image_result(str(image_result_path))
        except SearchRenderError as error:
            logger.warning(f"[图片搜索处理] 渲染搜索图失败，回退文本: {error}")
            message_text = format_search_results_message(
                render_keyword,
                page_results,
                _RESULTS_PER_PAGE,
                bot_page=bot_page,
                total_fetched=total_fetched,
            )
            yield event.plain_result(message_text)
        except Exception as error:
            logger.warning(f"[图片搜索处理] 搜索渲染图发送失败，回退文本: {error}")
            message_text = format_search_results_message(
                render_keyword,
                page_results,
                _RESULTS_PER_PAGE,
                bot_page=bot_page,
                total_fetched=total_fetched,
            )
            yield event.plain_result(message_text)
    
    @filter.command("download")
    async def handle_download(self, event: AstrMessageEvent):
        """下载 E-Hentai 本子
        
        用法: /download [-original] <关键词>
        """
        logger = get_logger()
        
        raw_input = event.message_str.strip()
        
        # 移除命令前缀 "download "
        if raw_input.startswith("download "):
            raw_input = raw_input[9:]
        
        # 解析 -original 标志
        use_original = "-original" in raw_input
        keyword = raw_input.replace("-original", "").strip()
        
        logger.info(f"[下载处理] 开始处理下载请求: keyword='{keyword}', use_original={use_original}")
        
        if not keyword:
            yield event.plain_result("用法: /download [-original] <关键词>")
            return
        
        client = self.build_client()
        options = self.build_search_options()
        quality = "original" if use_original else "resample"
        
        yield event.plain_result(f"正在搜索并准备下载（{quality}版本），请稍候...")
        logger.info(f"[下载处理] 创建 EHentai 客户端，质量={quality}")
        
        if not client.has_login_cookies():
            yield event.plain_result(
                "下载需要登录 Cookie。请在配置中设置 EHENTAI_IPB_MEMBER_ID 和 EHENTAI_IPB_PASS_HASH"
            )
            return
        
        if self.plugin_config.ehentai_site.lower() == "ex" and not client.has_ex_cookie():
            yield event.plain_result(
                "当前站点为 exhentai，需要 EHENTAI_IGNEOUS Cookie"
            )
            return
        
        try:
            # 如果输入的是纯数字，则尝试从历史搜索记录中获取对应的序号结果
            if keyword.isdigit():
                idx = int(keyword)
                sender_id = getattr(event.message_obj.sender, "user_id", "unknown")
                group_id = getattr(event.message_obj, "group_id", "private")
                session_key = f"{group_id}_{sender_id}"
                
                last_results = self._last_search_results.get(session_key, [])
                if 1 <= idx <= len(last_results):
                    gallery = last_results[idx - 1]
                    logger.info(f"[下载处理] 命中最近搜索结果，序号 {idx} -> {gallery.title}")
                else:
                    yield event.plain_result(f"序号 {idx} 不正确或您最近没有搜索过。请确认后重试。")
                    return
            else:
                # 常规关键词搜索
                results = await execute_gallery_search(client, keyword, 1, options)
                if not results:
                    yield event.plain_result("没有找到可下载的本子")
                    return
                gallery = pick_first_result(results)
                if gallery is None:
                    yield event.plain_result("没有找到可下载的本子")
                    return
        except SearchExecutionError as error:
            yield event.plain_result(f"搜索失败: {error}")
            return
        
        logger.info(f"[下载处理] 找到目标: gid={gallery.gid}, title={gallery.title[:50]}")
        
        try:
            logger.info(f"[下载处理] 解析存档下载链接")
            archive_url = await client.resolve_archive_url(gallery.url, prefer_original=use_original)
        except Exception as error:
            logger.error(f"[下载处理] 解析存档失败: {error}")
            yield event.plain_result(f"解析下载链接失败: {error}")
            return
        
        if not archive_url:
            yield event.plain_result("未能获取压缩包下载链接，可能需要有效的权限")
            return
        
        download_dir = Path(self.plugin_config.ehentai_download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{gallery.gid}_{gallery.token}.zip"
        file_path = download_dir / file_name
        
        logger.info(f"[下载处理] 开始下载存档文件")
        
        try:
            await client.download_file(archive_url, file_path)
            logger.info(f"[下载处理] 下载文件成功")
        except Exception as error:
            logger.error(f"[下载处理] 下载文件失败: {error}")
            yield event.plain_result(f"下载失败: {error}")
            return
        
        # 获取 R2 管理器
        file_size_mb = file_path.stat().st_size / 1024 / 1024
        r2_manager = get_r2_manager()
        if r2_manager is None:
            r2_manager = await init_r2_manager(self.plugin_config)
        
        # 尝试上传到 R2
        if r2_manager and r2_manager.is_available:
            logger.info(f"[下载处理] 尝试 R2 上传...")
            try:
                r2_url = await r2_manager.upload_file(str(file_path), file_path.name)
                if r2_url:
                    logger.info(f"[下载处理] R2 上传成功: {r2_url}")
                    
                    # 准备消息
                    safe_title = gallery.title.encode("utf-8", errors="ignore").decode("utf-8")
                    text_info = (
                        f"你请求的资源：\n{safe_title}\n\n"
                        f"下载链接：\n{r2_url}\n\n"
                        f"链接有效期：{r2_manager.retention_hours} 小时\n"
                        f"大小：{file_size_mb:.2f} MB"
                    )
                    
                    # 记录 D1
                    d1_manager = get_d1_manager()
                    if d1_manager is None:
                        d1_manager = await init_d1_manager(self.plugin_config)
                    if d1_manager:
                        try:
                            sender_id = event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
                            await d1_manager.record_download(
                                gid=str(gallery.gid),
                                title=gallery.title,
                                size_mb=file_size_mb,
                                user_id=sender_id,
                                r2_url=r2_url,
                                retention_hours=r2_manager.retention_hours
                            )
                        except Exception as e:
                            logger.warning(f"[下载处理] 记录 D1 失败: {e}")
                    
                    yield event.plain_result(text_info)
                    return
            except Exception as error:
                logger.warning(f"[下载处理] R2 上传失败: {error}")
        
        # R2 不可用或失败，返回文件信息
        msg = (
            f"✓ 下载完成！\n"
            f"但上传失败（R2 不可用）\n\n"
            f"文件信息：\n"
            f"- 文件名: {file_path.name}\n"
            f"- 大小: {file_size_mb:.2f} MB\n"
            f"- 路径: {file_path}\n\n"
            f"请稍候后手动下载，或联系管理员。"
        )
        yield event.plain_result(msg)
    
    @filter.regex(r"https?://(?:exhentai\.org|e-hentai\.org(?:/lofi)?)/(?:g|mpv)/\d+/[0-9a-f]{10}")
    async def handle_gallery_link(self, event: AstrMessageEvent):
        """处理 E-Hentai 画廊链接
        
        当用户发送 exhentai.org 或 e-hentai.org 的链接时：
        1. 提取 gid 和 token
        2. 下载整个画廊（压缩包）
        3. 上传到 R2
        4. 返回可下载的链接
        """
        logger = get_logger()
        
        raw = event.message_str.strip()
        logger.info(f"[链接处理] 捕捉到画廊链接: {raw[:80]}")
        
        # 提取链接
        match = re.search(r"(https?://(?:exhentai\.org|e-hentai\.org(?:/lofi)?)/(?:g|mpv)/\d+/[0-9a-f]{10})", raw)
        if not match:
            logger.warning("[链接处理] 无法从消息中提取链接")
            return
        
        gallery_url = match.group(1)
        
        client = self.build_client()
        
        # 从URL提取gid和token
        gid_token = client._extract_gid_token(gallery_url)
        if not gid_token:
            yield event.plain_result("❌ 无法解析链接，请检查链接是否正确")
            return
        
        gid, token = gid_token
        logger.info(f"[链接处理] 已提取 gid={gid}, token={token}")
        
        # 检查登录状态
        if not client.has_login_cookies():
            yield event.plain_result("❌ 下载需要登录 Cookie。请在配置中设置 EHENTAI_IPB_MEMBER_ID 和 EHENTAI_IPB_PASS_HASH")
            return
        
        if client.site.lower() == "ex" and not client.has_ex_cookie():
            yield event.plain_result("❌ 当前站点为 exhentai，需要 EHENTAI_IGNEOUS Cookie")
            return
        
        yield event.plain_result("🔄 正在获取画廊信息...")
        
        # 从gid和token构造gallery对象（最小信息）
        gallery = GalleryResult(
            gid=gid,
            token=token,
            title="",  # 将在下载时填充
            url=gallery_url,
            category="",
            posted="",
            uploader="",
            rating=-1.0,
            pages=0,
            cover_url="",
            thumb_width=0,
            thumb_height=0,
            tags=[],
            disowned=False,
            favorited=-1,
        )

        # 获取画廊信息并发送预览图（失败时不阻断后续下载流程）
        try:
            gallery_domain = urlparse(gallery_url).netloc
            logger.info(f"[链接处理] 获取画廊详细信息并渲染预览图: domain={gallery_domain}")
            detailed_gallery = await fetch_gallery_info(
                client,
                gid,
                token,
                gallery_domain=gallery_domain,
            )
            if detailed_gallery:
                gallery = detailed_gallery
                preview_dir = Path(self.plugin_config.ehentai_download_dir) / "gallery_preview"
                try:
                    preview_image_path = await render_gallery_preview_image(gallery, preview_dir)
                    yield event.image_result(str(preview_image_path))
                    logger.info(f"[链接处理] 画廊预览图发送成功: {preview_image_path}")
                except GalleryPreviewError as error:
                    logger.warning(f"[链接处理] 画廊预览图渲染失败，继续下载流程: {error}")
                except Exception as error:
                    logger.warning(f"[链接处理] 画廊预览图发送失败，继续下载流程: {error}")
            else:
                logger.warning("[链接处理] 未获取到画廊详情，跳过预览图生成")
        except Exception as error:
            logger.warning(f"[链接处理] 预览图流程失败，继续下载流程: {error}")
        
        download_dir = Path(self.plugin_config.ehentai_download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{gallery.gid}_{gallery.token}.zip"
        file_path = download_dir / file_name

        max_attempts = 3
        last_error: Optional[Exception] = None
        download_success = False

        yield event.plain_result("⬇️ 正在下载画廊文件...")
        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"[链接处理] 第{attempt}次解析存档下载链接")
                archive_url = await client.resolve_archive_url(gallery.url, prefer_original=False)
            except Exception as error:
                last_error = error
                logger.error(f"[链接处理] 第{attempt}次解析存档失败: {error}", exc_info=True)
                continue

            if not archive_url:
                logger.warning(f"[链接处理] 第{attempt}次未获取到存档链接")
                continue

            logger.info(f"[链接处理] 开始下载存档文件 (attempt={attempt}/{max_attempts})")
            try:
                await client.download_file(archive_url, file_path)
                logger.info(f"[链接处理] 下载文件成功 (attempt={attempt}/{max_attempts})")
                download_success = True
                break
            except Exception as error:
                last_error = error
                logger.error(
                    f"[链接处理] 下载文件失败 (attempt={attempt}/{max_attempts}): {error}",
                    exc_info=True,
                )

                if attempt < max_attempts:
                    yield event.plain_result(f"🔁 下载失败，正在重试（第 {attempt + 1}/{max_attempts} 次）...")

        if not download_success:
            if last_error is not None:
                yield event.plain_result(f"❌ 下载失败（已重试 {max_attempts} 次）: {last_error}")
            else:
                error_msg = (
                    "❌ 无法获取压缩包下载链接\n\n"
                    "可能的原因：\n"
                    "  1. 链接或 Token 已过期\n"
                    f"  2. GID {gid} 不存在或无权限访问\n"
                    "  3. 该画廊不提供存档下载\n"
                    "  4. 服务器暂时不可用\n\n"
                    "💡 建议：检查链接是否正确，或稍后重试"
                )
                yield event.plain_result(error_msg)
            return
        
        # 获取 R2 管理器
        file_size_mb = file_path.stat().st_size / 1024 / 1024
        r2_manager = get_r2_manager()
        if r2_manager is None:
            r2_manager = await init_r2_manager(self.plugin_config)
        
        # 尝试上传到 R2
        if r2_manager and r2_manager.is_available:
            yield event.plain_result("⬆️ 正在上传到 R2...")
            logger.info("[链接处理] 尝试 R2 上传...")
            try:
                r2_url = await r2_manager.upload_file(str(file_path), file_path.name)
                if r2_url:
                    logger.info(f"[链接处理] R2 上传成功: {r2_url}")
                    
                    # 准备消息
                    text_info = (
                        f"✅ 画廊下载完成！\n\n"
                        f"🔗 下载链接：\n{r2_url}\n\n"
                        f"📦 文件信息：\n"
                        f"  • 大小：{file_size_mb:.2f} MB\n"
                        f"  • 格式：ZIP 压缩包\n\n"
                        f"⏰ 链接有效期：{r2_manager.retention_hours} 小时\n\n"
                        f"💾 GID: {gid}"
                    )
                    
                    # 记录 D1
                    d1_manager = get_d1_manager()
                    if d1_manager is None:
                        d1_manager = await init_d1_manager(self.plugin_config)
                    if d1_manager:
                        try:
                            sender_id = event.message_obj.sender.user_id if event.message_obj.sender else "unknown"
                            db_title = (gallery.title or "未知标题").strip()
                            await d1_manager.record_download(
                                gid=str(gallery.gid),
                                title=db_title,
                                size_mb=file_size_mb,
                                user_id=sender_id,
                                r2_url=r2_url,
                                retention_hours=r2_manager.retention_hours
                            )
                        except Exception as e:
                            logger.warning(f"[链接处理] 记录 D1 失败: {e}")
                    
                    yield event.plain_result(text_info)
                    return
            except Exception as error:
                logger.warning(f"[链接处理] R2 上传失败: {error}")
        
        # R2 不可用或失败，返回文件信息
        msg = (
            f"✓ 下载完成！\n"
            f"但上传 R2 失败（R2 不可用）\n\n"
            f"📦 文件信息：\n"
            f"  • 文件名: {file_path.name}\n"
            f"  • 大小: {file_size_mb:.2f} MB\n"
            f"  • 路径: {file_path}\n\n"
            f"请稍候后手动下载，或联系管理员。"
        )
        yield event.plain_result(msg)
    
    async def terminate(self):
        """插件卸载时的清理"""
        logger = get_logger()
        logger.info("[E-Hentai插件] 插件正在卸载，执行清理...")
        # 这里可以添加清理逻辑，如关闭连接等
