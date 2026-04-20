from __future__ import annotations

import asyncio
import re
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
from bs4 import BeautifulSoup

from .network import BUILT_IN_HOSTS
from .logger_compat import get_logger


def _get_logger():
    """获取logger，解决循环导入问题"""
    return get_logger()


# 这是一个代理对象，提供与 nonebot logger 相同的接口
class LoggerProxy:
    def __getattr__(self, name):
        return getattr(_get_logger(), name)


logger = LoggerProxy()

try:
    from curl_cffi import requests as curl_requests
    from curl_cffi.const import CurlHttpVersion
except ImportError:
    curl_requests = None
    CurlHttpVersion = None


GALLERY_URL_RE = re.compile(
    r"https?://(?:exhentai\.org|e-hentai\.org(?:/lofi)?)/(?:g|mpv)/(\d+)/([0-9a-f]{10})"
)
INSUFFICIENT_FUNDS_MSG = "You do not have enough funds to download this archive."
NEED_HATH_CLIENT_MSG = "You must have a H@H client assigned to your account to use this feature."
CHROME_DESKTOP_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CHROME_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)
CHROME_ACCEPT_LANGUAGE = "en-US,en;q=0.9"


@dataclass
class GalleryResult:
    """对标 EhViewer 官方的 BaseGalleryInfo"""
    # 核心字段
    gid: str
    token: str
    title: str
    url: str
    
    # 元数据字段
    category: str = ""  # 分类：Manga, Doujinshi, Cosplay 等
    posted: str = ""  # 发布日期
    uploader: str = ""  # 上传者
    rating: float = -1.0  # 评分 0-5，-1 表示未评分
    pages: int = 0  # 页数
    
    # 缩略图信息（对标官方的 thumbKey）
    cover_url: str = ""  # 缩略图 URL
    thumb_width: int = 0
    thumb_height: int = 0

    # 标题补全信息（来自 gdata API）
    title_jpn: str = ""  # 日文原文标题
    has_japanese_title: int = 0  # 1=有日文原文，0=无
    
    # 标签列表
    tags: list[str] = None  # 标签列表
    
    # 其他元数据
    disowned: bool = False  # 是否被遗弃（显示为灰色）
    favorited: int = -1  # 收藏槽位选择：-1 未收藏
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class ArchiveOption:
    res: str
    name: str
    size: str
    cost: str
    is_hath: bool


def _safe_error_text(error: Exception) -> str:
    try:
        return str(error)
    except Exception:
        return repr(error)


@dataclass
class SearchOptions:
    f_cats: int = 0
    advsearch: bool = False
    f_sh: bool = False
    f_sto: bool = False
    f_sfl: bool = False
    f_sfu: bool = False
    f_sft: bool = False
    f_srdd: int = 0
    f_spf: int = 0
    f_spt: int = 0


class EHentaiClient:
    def __init__(
        self,
        site: str,
        base_url: str,
        cookie: str,
        ipb_member_id: str,
        ipb_pass_hash: str,
        igneous: str,
        cf_clearance: str,
        user_agent: str,
        timeout: int,
        proxy: str = "",
        backend: str = "httpx",
        http3: bool = False,
        desktop_site: bool = False,
        impersonate: str = "chrome124",
        enable_direct_ip: bool = True,
        curl_cffi_skip_on_error: bool = True,
        min_cache_file_size_kb: int = 100,
        cloudflare_worker_url: str = "",
    ) -> None:
        self.site = site.lower()
        self.base_url = self._resolve_base_url(site, base_url)
        effective_user_agent = CHROME_DESKTOP_USER_AGENT if desktop_site else user_agent
        self.headers = {
            "User-Agent": effective_user_agent,
            "Accept": CHROME_ACCEPT,
            "Accept-Language": CHROME_ACCEPT_LANGUAGE,
        }
        self.raw_cookie = cookie.strip()
        self.ipb_member_id = ipb_member_id.strip()
        self.ipb_pass_hash = ipb_pass_hash.strip()
        self.igneous = igneous.strip()
        self.cf_clearance = cf_clearance.strip()
        self.timeout = timeout
        self.proxy = proxy
        self.backend = backend.lower()
        self.http3 = http3
        self.desktop_site = desktop_site
        self.cloudflare_worker_url = cloudflare_worker_url.strip()
        self.impersonate = impersonate
        self.enable_direct_ip = enable_direct_ip
        self.curl_cffi_skip_on_error = curl_cffi_skip_on_error
        self.min_cache_file_size_bytes = min_cache_file_size_kb * 1024

    @staticmethod
    def _resolve_base_url(site: str, base_url: str) -> str:
        normalized_site = site.lower()
        if normalized_site == "ex":
            return "https://exhentai.org"
        if normalized_site == "e":
            return "https://e-hentai.org"
        return base_url.rstrip("/")

    def has_identity_cookies(self) -> bool:
        return bool(self.ipb_member_id and self.ipb_pass_hash)

    def has_login_cookies(self) -> bool:
        return bool(self.raw_cookie or self.has_identity_cookies())

    def has_ex_cookie(self) -> bool:
        return bool(self.raw_cookie or (self.has_identity_cookies() and self.igneous))

    def _cookie_pairs_for_url(self, url: str) -> list[tuple[str, str]]:
        host = url.lower()
        cookie_pairs: list[tuple[str, str]] = []

        if self.raw_cookie:
            return []

        if self.ipb_member_id:
            cookie_pairs.append(("ipb_member_id", self.ipb_member_id))
        if self.ipb_pass_hash:
            cookie_pairs.append(("ipb_pass_hash", self.ipb_pass_hash))
        if self.cf_clearance:
            cookie_pairs.append(("cf_clearance", self.cf_clearance))
        if "e-hentai.org" in host:
            cookie_pairs.append(("nw", "1"))
        if "exhentai.org" in host and self.igneous:
            cookie_pairs.append(("igneous", self.igneous))

        return cookie_pairs

    def _build_cookie_header(self, url: str) -> str:
        if self.raw_cookie:
            return self.raw_cookie
        cookie_pairs = self._cookie_pairs_for_url(url)
        return "; ".join(f"{key}={value}" for key, value in cookie_pairs if value)

    def _headers_for_url(self, url: str) -> dict[str, str]:
        headers = dict(self.headers)
        cookie_header = self._build_cookie_header(url)
        if cookie_header:
            headers["Cookie"] = cookie_header

        # 对于直连 IP，注入原始 Host 头以通过 Cloudflare 验证
        if self.enable_direct_ip:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            hostname = parsed.hostname
            if hostname and hostname in BUILT_IN_HOSTS:
                port = parsed.port
                if port and port not in (80, 443):
                    headers["Host"] = f"{hostname}:{port}"
                else:
                    headers["Host"] = hostname

        return headers

    def _client(self) -> httpx.AsyncClient:
        # 使用直连 IP 的 httpx 客户端（EhViewer_CN_SXJ 方案）
        if self.enable_direct_ip:
            kwargs: dict = {
                "timeout": self.timeout,
                "verify": False,
                "follow_redirects": True,
            }
            if self.proxy:
                kwargs["proxy"] = self.proxy

            # 构建内置 IP 直连规则
            mounts = {}
            for hostname, ips in BUILT_IN_HOSTS.items():
                if ips:
                    ip = ips[0]
                    # 为每个主机名创建直连规则
                    for scheme in ["http", "https"]:
                        mount_key = f"{scheme}://{hostname}"
                        mounts[mount_key] = httpx.AsyncHTTPTransport(verify=False)

            if mounts:
                kwargs["mounts"] = mounts

            kwargs["headers"] = self.headers.copy()
            if self.raw_cookie or self.has_identity_cookies():
                # 添加 HTTP2 支持用于 IP 直连
                pass

            client = httpx.AsyncClient(**kwargs)
            return client

        # 降级到默认 httpx 客户端（禁用直连 IP）
        kwargs: dict = {
            "timeout": self.timeout,
            "follow_redirects": True,
        }
        if self.proxy:
            kwargs["proxy"] = self.proxy
        return httpx.AsyncClient(**kwargs)

    def _curl_request_kwargs(self) -> dict:
        return self._curl_request_kwargs_with_http3(self.http3)

    def _curl_request_kwargs_with_http3(self, http3: bool) -> dict:
        kwargs: dict = {
            "timeout": self.timeout,
            "allow_redirects": True,
            "impersonate": self.impersonate,
        }
        if self.proxy:
            kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        if http3 and CurlHttpVersion is not None:
            kwargs["http_version"] = CurlHttpVersion.V3
        return kwargs

    def _curl_session(self, http3: Optional[bool] = None):
        if curl_requests is None:
            raise RuntimeError("未安装 curl_cffi，无法使用浏览器仿真后端")
        if http3 is None:
            return curl_requests.Session(**self._curl_request_kwargs())
        return curl_requests.Session(**self._curl_request_kwargs_with_http3(http3))

    @staticmethod
    def _is_quic_tls_error(error: Exception) -> bool:
        return "quic needs at least tls version 1.3" in str(error).lower()

    @staticmethod
    def _should_fallback_to_httpx(error: Exception) -> bool:
        message = str(error).lower()
        if "connection reset by peer" in message:
            return True
        if "curl: (35)" in message:
            return True
        if "ssl" in message and "curl:" in message:
            return True
        return False

    @staticmethod
    def _is_login_required_page(body: str) -> bool:
        lowered = body.lower()
        return "this page requires you to log on." in lowered or "e-hentai.org login" in lowered

    @staticmethod
    def _raise_for_response(resp) -> None:
        if getattr(resp, "status_code", None) == 451:
            raise RuntimeError(
                "当前网络环境访问 E-Hentai 被 451 拦截。请配置 EHENTAI_PROXY，或在可访问该站点的网络环境下运行。"
            )
        resp.raise_for_status()

    def _normalize_gallery_url(self, href: str) -> str:
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return f"{self.base_url}{href if href.startswith('/') else '/' + href}"

    def _get_request_url_for_direct_ip(self, original_url: str) -> str:
        """
        对于直连 IP 模式，将 URL 中的主机名转换为预设的 IP 地址

        例：
        - https://e-hentai.org/path → https://104.20.18.168/path
        - https://exhentai.org/path → https://178.175.128.251/path
        """
        if not self.enable_direct_ip:
            return original_url

        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(original_url)
        hostname = parsed.hostname

        # 检查是否是已知的 e-hentai 相关主机
        if hostname and hostname in BUILT_IN_HOSTS:
            ips = BUILT_IN_HOSTS.get(hostname, [])
            if ips:
                # 强制使用 HTTPS 和 IP 地址
                new_netloc = ips[0]
                if parsed.port and parsed.port not in (80, 443):
                    new_netloc = f"{ips[0]}:{parsed.port}"

                new_parsed = (
                    "https",  # 总是使用 HTTPS
                    new_netloc,
                    parsed.path,
                    parsed.params,
                    parsed.query,
                    parsed.fragment,
                )
                return urlunparse(new_parsed)

        return original_url

    def _extract_gid_token(self, gallery_url: str) -> Optional[tuple[str, str]]:
        normalized = self._normalize_gallery_url(gallery_url)
        match = GALLERY_URL_RE.search(normalized)
        if not match:
            return None
        return match.group(1), match.group(2)

    def _build_search_url(self, keyword: str, options: Optional[SearchOptions], eh_page: int = 0) -> str:
        params: dict[str, str] = {"f_search": keyword}
        if eh_page > 0:
            params["page"] = str(eh_page)
        if options is None:
            return f"{self.base_url}/?{urlencode(params)}"

        if options.f_cats > 0:
            params["f_cats"] = str(options.f_cats)

        adv_enabled = options.advsearch or any(
            [
                options.f_sh,
                options.f_sto,
                options.f_sfl,
                options.f_sfu,
                options.f_sft,
                options.f_srdd > 0,
                options.f_spf > 0,
                options.f_spt > 0,
            ]
        )

        if adv_enabled:
            params["advsearch"] = "1"
            if options.f_sh:
                params["f_sh"] = "on"
            if options.f_sto:
                params["f_sto"] = "on"
            if options.f_sfl:
                params["f_sfl"] = "on"
            if options.f_sfu:
                params["f_sfu"] = "on"
            if options.f_sft:
                params["f_sft"] = "on"
            if options.f_srdd > 0:
                params["f_srdd"] = str(options.f_srdd)
            if options.f_spf > 0:
                params["f_spf"] = str(options.f_spf)
            if options.f_spt > 0:
                params["f_spt"] = str(options.f_spt)

        return f"{self.base_url}/?{urlencode(params)}"

    def _resolve_gmetadata_api_url(self) -> str:
        host = (urlparse(self.base_url).hostname or "").lower()
        if "exhentai.org" in host:
            return "https://s.exhentai.org/api.php"
        return "https://api.e-hentai.org/api.php"

    async def _enrich_japanese_titles(self, results: list[GalleryResult]) -> None:
        if not results:
            return

        gidlist: list[list[object]] = []
        for item in results:
            try:
                gidlist.append([int(item.gid), item.token])
            except Exception:
                continue

        if not gidlist:
            return

        api_url = self._resolve_gmetadata_api_url()
        request_url = self._get_request_url_for_direct_ip(api_url) if self.enable_direct_ip else api_url
        payload = {
            "method": "gdata",
            "gidlist": gidlist,
            "namespace": 1,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=False,
                follow_redirects=True,
                proxy=self.proxy or None,
            ) as client:
                resp = await client.post(
                    request_url,
                    json=payload,
                    headers=self._headers_for_url(api_url),
                )
        except Exception as error:
            logger.warning(
                f"[搜索补全] 拉取日文原文失败: {type(error).__name__}: {_safe_error_text(error)}"
            )
            return

        if resp.status_code != 200:
            logger.warning(f"[搜索补全] gdata API 返回状态码异常: {resp.status_code}")
            return

        try:
            data = resp.json()
        except Exception as error:
            logger.warning(
                f"[搜索补全] gdata API JSON 解析失败: {type(error).__name__}: {_safe_error_text(error)}"
            )
            return

        gmetadata = data.get("gmetadata", []) if isinstance(data, dict) else []
        if not isinstance(gmetadata, list):
            return

        jpn_map: dict[tuple[str, str], str] = {}
        for metadata in gmetadata:
            if not isinstance(metadata, dict):
                continue
            gid = str(metadata.get("gid", "")).strip()
            token = str(metadata.get("token", "")).strip()
            title_jpn_raw = str(metadata.get("title_jpn", "")).strip()
            if gid and token:
                jpn_map[(gid, token)] = self._clean_title(title_jpn_raw)

        for item in results:
            title_jpn = jpn_map.get((item.gid, item.token), "")
            item.title_jpn = title_jpn
            item.has_japanese_title = 1 if title_jpn else 0

    @staticmethod
    def _clean_title(title: str) -> str:
        """清理标题中的 f: 标签
        
        对标官方：html.unescape() + 移除搜索过滤标签
        """
        import html
        # Step 1: 解码 HTML 实体（与官方 unescape 一致）
        title = html.unescape(title)
        # Step 2: 移除所有 f:xxx 搜索过滤标签
        import re
        cleaned = re.sub(r'\s*f:\S+', '', title)
        # Step 3: 移除多个连续空格
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()

    @staticmethod
    def _parse_category(row) -> str:
        """提取分类标签（对标官方）
        
        查找 .cn 或 .cs 元素，映射到分类名称
        """
        category_elem = row.select_one(".cn") or row.select_one(".cs")
        if category_elem:
            return category_elem.get_text(strip=True)
        return ""

    @staticmethod
    def _parse_rating(row) -> float:
        """解析评分（对标官方 parse_rating）
        
        从 .ir 元素的 CSS background-position 计算评分
        """
        ir_elem = row.select_one(".ir")
        if not ir_elem:
            return -1.0
        
        style = ir_elem.get("style", "")
        # 匹配像素值：background-position: 0px -16px; 或 background: 0px -21px;
        import re
        matches = re.findall(r'(\d+)px', style)
        if len(matches) < 2:
            return -1.0
        
        try:
            num1, num2 = int(matches[0]), int(matches[1])
            # 官方逻辑：5 - (num1 // 16) 是简单的评星计算
            # num1 的每 16px 代表一半星（官方精度 0.5 星）
            rate = 5 - num1 // 16
            
            # 如果 num2 是 21（vs 0），表示半颗星
            if num2 == 21:
                return (rate - 1) + 0.5
            else:
                return float(rate)
        except (ValueError, ZeroDivisionError):
            return -1.0

    @staticmethod
    def _parse_posted(row) -> str:
        """提取发布日期（对标官方）
        
        从 #posted_{GID} 元素获取日期
        """
        # 先尝试标准日期格式
        date_elem = row.select_one("div[id^='posted_']")
        if date_elem:
            return date_elem.get_text(strip=True)
        
        # 备用：查找 gl3e 容器中的日期
        gl3e = row.select_one(".gl3e")
        if gl3e:
            divs = gl3e.find_all("div", recursive=False)
            if len(divs) >= 2:
                return divs[1].get_text(strip=True)
        
        return ""

    @staticmethod
    def _parse_uploader(row) -> str:
        """提取上传者名称（对标官方）
        
        从 <a href="...uploader/..."> 获取文本
        """
        uploader_link = row.select_one("a[href*='/uploader/']")
        if uploader_link:
            return uploader_link.get_text(strip=True)
        
        # 备用：从 gl3e 中的所有链接查找
        gl3e = row.select_one(".gl3e")
        if gl3e:
            for link in gl3e.find_all("a"):
                href = link.get("href", "")
                if "uploader" in href:
                    return link.get_text(strip=True)
        
        return ""

    @staticmethod
    def _parse_pages(row) -> int:
        """提取页数（对标官方）
        
        查找包含 "pages" 的文本
        """
        import re
        # 最常见：在 gl3e 最后一个 div 中
        gl3e = row.select_one(".gl3e")
        if gl3e:
            text = gl3e.get_text()
            match = re.search(r'(\d+)\s*pages?', text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        
        # 备用：整行查找
        row_text = row.get_text()
        match = re.search(r'(\d+)\s*pages?', row_text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        
        return 0

    @staticmethod
    def _parse_tags(row) -> list[str]:
        """提取标签列表（对标官方）
        
        从 .gt 和 .gtl 元素的 title 属性提取
        """
        tags = []
        for tag_elem in row.select(".gt, .gtl"):
            if tag_elem.get("title"):
                tags.append(tag_elem["title"])
        return tags

    @staticmethod
    def _parse_thumb_resolution(row) -> tuple[int, int]:
        """提取缩略图尺寸（对标官方 parse_thumb_resolution）
        
        从图片 style 属性的 height/width 计算
        """
        img = row.select_one("img")
        if not img:
            return 0, 0
        
        style = img.get("style", "")
        import re
        
        # 查找 height 和 width
        height_match = re.search(r'height\s*:\s*(\d+)px', style)
        width_match = re.search(r'width\s*:\s*(\d+)px', style)
        
        height = int(height_match.group(1)) if height_match else 0
        width = int(width_match.group(1)) if width_match else 0
        
        return width, height

    @staticmethod
    def _parse_disowned(row) -> bool:
        """检测是否被遗弃（显示为灰色）
        
        检查 opacity 或其他灰显标记
        """
        row_style = row.get("style", "")
        # 官方标记：opacity:0.5
        return "opacity:0.5" in row_style or "opacity: 0.5" in row_style

    def _parse_search_results(self, body: str, limit: int) -> list[GalleryResult]:
        """完全对标 EhViewer 官方搜索结果解析
        
        流程：
        1. 检查搜索警告页面
        2. 定位容器（.itg 或 .gld）
        3. 遍历行/块元素
        4. 按官方顺序提取所有字段
        5. 去重并限制数量
        
        支持三种布局：
        - 旧版 table 布局：.itg table，.gdt 作为行
        - 中版 div 布局：.itg div，.gl1t 元素作为结果项
        - 新版 grid 布局：.gld 网格，.gl1t 元素作为结果项（无 .itg）
        """
        soup = BeautifulSoup(body, "html.parser")
        
        # Step 1: 检查搜索警告页面（对标官方）
        if soup.select_one(".searchwarn") is not None:
            logger.warning(f"[搜索解析] 搜索页面返回警告信息 (可能无权限访问或搜索限制)")
            return []

        # Step 2: 获取容器（优先 .itg，其次 .gld）
        itg = soup.select_one(".itg")
        gld = soup.select_one(".gld")
        
        if itg is None and gld is None:
            logger.warning("[搜索解析] 未找到 .itg 或 .gld 容器，尝试直接查找画廊链接")
            # 最后的降级方案：直接查找所有画廊链接
            nodes = soup.find_all("a", href=lambda x: x and "/g/" in x if x else False)
            if not nodes:
                logger.warning("[搜索解析] 也找不到任何画廊链接")
                return []
            logger.debug(f"[搜索解析] 使用降级方案，找到 {len(nodes)} 个链接")
        else:
            # Step 3: 遍历行/块，支持多种布局
            nodes = []
            
            if itg is not None:
                # 使用 .itg 容器（旧版或中版）
                if itg.name and itg.name.lower() == "table":
                    # table 布局：跳过表头
                    nodes = itg.select("tr")
                    nodes = nodes[1:] if len(nodes) > 1 else []
                    logger.debug(f"[搜索解析] 容器类型=table, 候选行数={len(nodes)}")
                else:
                    # div 布局：先尝试旧的 .gdt + 新的 .gl1t 格式
                    gdt_nodes = itg.select(".gdt")  # 旧版单行布局
                    gl1t_nodes = itg.select(".gl1t")  # 新版网格布局
                    
                    if gdt_nodes:
                        nodes = gdt_nodes
                        logger.debug(f"[搜索解析] 使用 .itg > .gdt 布局，候选行数={len(nodes)}")
                    elif gl1t_nodes:
                        nodes = gl1t_nodes
                        logger.debug(f"[搜索解析] 使用 .itg > .gl1t 网格布局，候选项数={len(nodes)}")
                    else:
                        # 如果都没找到，尝试取第一级子元素
                        nodes = [child for child in itg.find_all(recursive=False) if getattr(child, "name", None)]
                        logger.debug(f"[搜索解析] 使用 .itg 第一级子元素，候选块数={len(nodes)}")
            
            elif gld is not None:
                # 使用 .gld 容器（新版网格布局，无 .itg）
                gl1t_nodes = gld.select(".gl1t")
                if gl1t_nodes:
                    nodes = gl1t_nodes
                    logger.debug(f"[搜索解析] 使用 .gld > .gl1t 新版网格布局，候选项数={len(nodes)}")
                else:
                    # 降级：直接取 gld 的所有子元素
                    nodes = [child for child in gld.find_all(recursive=False) if getattr(child, "name", None)]
                    logger.debug(f"[搜索解析] 使用 .gld 第一级子元素，候选块数={len(nodes)}")

        results: list[GalleryResult] = []
        seen: set[str] = set()
        
        # Step 4: 循环解析每一行/块（对标官方 parse_gallery_info）
        for idx, row in enumerate(nodes, 1):
            # 4.1: 获取标题链接（对标官方的三层备选方案）
            # 兼容多种情况：
            # - 如果 row 本身就是 <a> 链接，直接使用
            # - 如果 row 是容器，在其中查找链接
            
            title_anchor = None
            
            # 情况 1：row 本身是链接
            if hasattr(row, 'name') and row.name and row.name.lower() == 'a':
                if "/g/" in row.get("href", "") or "/mpv/" in row.get("href", ""):
                    title_anchor = row
            
            # 情况 2：row 是容器，查找内部链接
            if not title_anchor:
                # 先尝试新版布局：直接查找 <a href...> 包含 /g/
                title_anchor = row.select_one("a[href*='/g/'], a[href*='/mpv/']") if hasattr(row, 'select_one') else None
            
            if not title_anchor:
                # 尝试旧版布局：在 .glname 内查找 <a>
                if hasattr(row, 'select_one'):
                    glname = row.select_one(".glname")
                    if glname:
                        title_anchor = glname.select_one("a[href]")

            if not title_anchor:
                # 最后的尝试：遍历所有链接
                if hasattr(row, 'find_all'):
                    for link in row.find_all("a", href=True):
                        href = link.get("href", "").strip()
                        if href and re.search(r"/(?:g|mpv)/\d+/[0-9a-f]{10}", href):
                            title_anchor = link
                            break

            if not title_anchor:
                logger.debug(f"[搜索解析] 第 {idx} 项: 未找到标题链接")
                continue
            
            # 4.2: 提取标题和链接
            href_raw = title_anchor.get("href", "").strip()
            # 标题来自链接元素本身的文本（新版）或其子元素（旧版）
            title = title_anchor.get_text(" ", strip=True)
            
            if not href_raw or not title:
                logger.debug(f"[搜索解析] 第 {idx} 项: 标题或链接为空")
                continue

            # 重要：清理标题中的 HTML 实体和 f: 标签（对标官方 unescape）
            title = self._clean_title(title)
            
            # 4.3: 提取 GID/Token
            href = self._normalize_gallery_url(href_raw)
            gid_token = self._extract_gid_token(href)
            if not gid_token:
                logger.debug(f"[搜索解析] 第 {idx} 行: 无法提取 gid/token")
                continue

            gid, token = gid_token
            
            # 4.4: 去重（对标官方）
            dedupe_key = f"{gid}:{token}"
            if dedupe_key in seen:
                logger.debug(f"[搜索解析] 第 {idx} 行: 重复的 {dedupe_key}")
                continue
            seen.add(dedupe_key)
            
            # 4.5: 获取缩略图 URL（对标官方：data-src 优先，过滤 base64）
            img = None
            cover_url = ""
            # 只有在 row 是容器时才尝试查找图片
            if hasattr(row, 'select_one') and row.name and row.name.lower() != 'a':
                img = row.select_one("img[data-src], img[src]")
                if img:
                    cover_url = (img.get("data-src") or img.get("src") or "").strip()
                    if cover_url.startswith("data:image"):
                        cover_url = ""
            
            # 4.6: 提取所有元数据字段（对标官方）
            # 只有在 row 是容器时才尝试提取这些字段
            if hasattr(row, 'select_one') and row.name and row.name.lower() != 'a':
                category = self._parse_category(row)
                rating = self._parse_rating(row)
                posted = self._parse_posted(row)
                uploader = self._parse_uploader(row)
                pages = self._parse_pages(row)
                tags = self._parse_tags(row)
                thumb_width, thumb_height = self._parse_thumb_resolution(row)
                disowned = self._parse_disowned(row)
            else:
                # 如果 row 是直接链接，使用默认值
                category = ""
                rating = -1
                posted = ""
                uploader = ""
                pages = -1
                tags = []
                thumb_width = 0
                thumb_height = 0
                disowned = False
            
            # 5. 组装完整结果（对标官方 BaseGalleryInfo）
            result = GalleryResult(
                gid=gid,
                token=token,
                title=title,
                url=href,
                category=category,
                posted=posted,
                uploader=uploader,
                rating=rating,
                pages=pages,
                cover_url=cover_url,
                thumb_width=thumb_width,
                thumb_height=thumb_height,
                tags=tags,
                disowned=disowned,
                favorited=-1,  # 初始未收藏
            )
            results.append(result)
            logger.debug(f"[搜索解析] 第 {idx} 行: ✓ 解析成功 - {title[:30]}")
            
            # 5. 限制数量（对标官方 parse_info_list 的 limit 参数）
            if len(results) >= limit:
                break

        logger.info(f"[搜索解析] 共解析 {len(results)} 个结果/最多 {limit} 个")
        return results

    def _search_from_response(self, resp, limit: int) -> list[GalleryResult]:
        body = resp.text
        status_code = getattr(resp, "status_code", None)
        
        if status_code not in (200, 451):
            logger.error(f"[搜索响应] 非预期状态码: {status_code}")
            self._raise_for_response(resp)

        logger.debug(f"[搜索响应] 响应状态码: {status_code}, 响应体大小: {len(body)} 字节")
        
        # 兼容 table/div 结构，只检查是否存在 .itg 容器
        if "class=\"itg" not in body and "class='itg" not in body:
            logger.warning(f"[搜索响应] 响应体中未找到 .itg 容器")
        
        results = self._parse_search_results(body, limit)
        
        if results:
            logger.info(f"[搜索响应] 根据响应解析出 {len(results)} 个结果")
            return results

        if status_code == 451:
            logger.error(f"[搜索响应] HTTP 451 且无结果，权限不足或 IP 被限制")
            raise RuntimeError(
                "搜索页返回 451，且正文中未解析出图集列表。请配置 EHENTAI_PROXY，或切换到可访问 E-Hentai 的网络环境。"
            )

        logger.warning(f"[搜索响应] 未解析出任何搜索结果")
        return results

    def _search_sync(
        self,
        keyword: str,
        limit: int,
        options: Optional[SearchOptions],
        http3: Optional[bool] = None,
        eh_page: int = 0,
    ) -> list[GalleryResult]:
        search_url = self._build_search_url(keyword, options, eh_page)
        with self._curl_session(http3=http3) as session:
            resp = session.get(search_url, headers=self._headers_for_url(search_url))
            return self._search_from_response(resp, limit)

    @staticmethod
    def _is_connect_error(error: Exception) -> bool:
        """检测是否是连接错误（应该 fallback 到标准 DNS）"""
        from httpx import ConnectError as HttpxConnectError
        if isinstance(error, (HttpxConnectError, ssl.SSLError, TimeoutError)):
            return True
        if isinstance(error, OSError) and "Connection" in str(error):
            return True
        return False

    class _FakeResponse:
        def __init__(self, text: str, status_code: int):
            self.text = text
            self.status_code = status_code

    async def _fetch_url_via_worker(self, url: str, method: str = "GET", data: Optional[dict] = None):
        """通用代理方法：通过 Cloudflare Worker 获取 e-hentai URL（处理 archiver 等绕墙需求）"""
        logger.debug(f"[Worker代理] 准备通过 Worker 获取 URL: {url} ({method})")
        payload = {
            "action": "fetch",
            "fetchUrl": url,
            "fetchMethod": method,
        }
        if data:
            payload["fetchData"] = data
            
        cookie_header = self._build_cookie_header(self.base_url)
        if cookie_header:
            payload["cookies"] = cookie_header
            
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.headers.get("User-Agent", "Mozilla/5.0"),
        }
        
        async with httpx.AsyncClient(timeout=self.timeout, verify=False, follow_redirects=True) as client:
            resp = await client.post(self.cloudflare_worker_url, json=payload, headers=headers)
            resp.raise_for_status()
            
            result = resp.json()
            if "error" in result:
                raise RuntimeError(f"Worker Error: {result.get('error')}")
                
            return self._FakeResponse(result.get("html", ""), result.get("status", 200))

    async def _search_via_worker(
        self, keyword: str, limit: int, options: Optional[SearchOptions], eh_page: int
    ) -> list[GalleryResult]:
        """使用 Cloudflare Worker 搜索（将 Worker 仅作为网络代理，本地解析 HTML）"""
        logger.debug(f"[Worker搜索] 准备请求 Worker: {self.cloudflare_worker_url}")
        
        # 构造请求体，申请获取 raw HTML
        payload = {
            "keyword": keyword,
            "page": eh_page,
            "baseUrl": self.base_url,
            "rawHtml": True  # 请求 Worker 返回原始 HTML 以便利用本地完整的解析器（评分、标签、缩略图等）
        }
        
        # 如果有 cookies，也传给 Worker
        # 使用 _build_cookie_header() 获取完整的 Cookie 字符串
        cookie_header = self._build_cookie_header(self.base_url)
        if cookie_header:
            payload["cookies"] = cookie_header
            logger.debug(f"[Worker搜索] 使用自定义 Cookie (长度: {len(cookie_header)} 字符)")
        
        # 构造请求头
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.headers.get("User-Agent", "Mozilla/5.0"),
        }
        
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=False,
                follow_redirects=True,
            ) as client:
                logger.debug(f"[Worker搜索] 发送 POST 请求到 Worker，基础 URL: {self.base_url}")
                resp = await client.post(
                    self.cloudflare_worker_url,
                    json=payload,
                    headers=headers,
                )
            
            logger.debug(f"[Worker搜索] 收到响应，状态码: {resp.status_code}")
            
            if resp.status_code != 200:
                logger.error(f"[Worker搜索] Worker 返回非 200 状态码: {resp.status_code}")
                raise RuntimeError(f"Worker 返回状态码: {resp.status_code}")
            
            # 解析 JSON 响应
            data = resp.json()
            
            if not data.get("success"):
                logger.error(f"[Worker搜索] Worker 返回失败响应: {data.get('error', '未知错误')}")
                raise RuntimeError("Worker 搜索失败")
                
            # 获取 HTML 文本并使用自带的本地解析器
            raw_html = data.get("html", "")
            if not raw_html:
                # 兼容旧版 Worker，不包含原始 html
                logger.warning("[Worker搜索] 未获得 raw HTML，尝试兼容模式（可能会丢失如标签、上传者等字段）")
                results: list[GalleryResult] = []
                worker_results = data.get("results", [])
                for idx, item in enumerate(worker_results[:limit]):
                    try:
                        gid = str(item.get("gid", ""))
                        token = str(item.get("token", ""))
                        title = str(item.get("title", ""))
                        url = str(item.get("url", ""))
                        category = str(item.get("category", ""))
                        rating = item.get("rating", -1)
                        cover_url = str(item.get("cover_url", ""))
                        if not gid or not token: continue
                        result = GalleryResult(
                            gid=gid, token=token, title=title, url=url,
                            category=category, rating=rating, cover_url=cover_url
                        )
                        results.append(result)
                    except Exception: pass
                return results

            # 检查 HTML 是否包含搜索结果标签
            if "class=\"itg" not in raw_html and "class='itg" not in raw_html:
                # 调试模式：打印部分 HTML 以诊断问题
                logger.warning(f"[Worker搜索] HTML 中未找到 .itg 容器，可能是登录页或错误页")
                logger.debug(f"[Worker搜索] HTML 样本 (前 500 字符):\n{raw_html[:500]}")
                
                # 检查是否是登录页面
                if "login" in raw_html.lower() or "password" in raw_html.lower():
                    logger.error("[Worker搜索] 检测到登录页面，Cookie 可能已过期或无效")
                    raise RuntimeError("E-Hentai 返回登录页面，Cookie 可能已过期")
                
                # 检查是否是 IP 被限制页面
                if "blocked" in raw_html.lower() or "restricted" in raw_html.lower():
                    logger.error("[Worker搜索] 检测到 IP 被限制")
                    raise RuntimeError("E-Hentai IP 可能被限制，请检查网络或使用代理")
                
                # 其他情况，记录警告但继续尝试解析
                logger.warning("[Worker搜索] 无法确定返回的是什么页面，继续尝试本地解析...")

            logger.debug(f"[Worker搜索] 成功获取 HTML (大小: {len(raw_html)} 字节)，开始本地解析详尽元数据...")
            
            # 本地解析，能获取到完整的 缩略图、评分、发布时间、上传者、标签等
            results = self._parse_search_results(raw_html, limit)
            
            # 如果 Worker 返回空结果，尝试检测是否是网络问题
            if not results:
                logger.warning("[Worker搜索] 本地解析得到 0 条结果，尝试检测 HTML 类型...")
                
                # 输出 HTML 样本用于诊断
                logger.debug(f"[Worker搜索] HTML 前 800 字符:\n{raw_html[:800]}")
                
                # 检查是否是登录页面
                if "login" in raw_html.lower() or "password" in raw_html.lower() or "cookie" in raw_html.lower():
                    logger.error("[Worker搜索] 检测到登录页面，Cookie 可能已过期或无效")
                    raise RuntimeError("E-Hentai 返回登录页面，Cookie 可能已过期")
                
                # 检查是否是 IP 被限制页面
                if "blocked" in raw_html.lower() or "restricted" in raw_html.lower() or "limit" in raw_html.lower():
                    logger.error("[Worker搜索] 检测到 IP 被限制或访问受限")
                    raise RuntimeError("E-Hentai 检测到受限访问，请检查网络或稍后重试")
                
                # 检查是否是错误页面（404 等）
                if "404" in raw_html or "not found" in raw_html.lower() or "error" in raw_html.lower():
                    logger.warning("[Worker搜索] 检测到错误页面响应，降级到直连")
                    raise RuntimeError("Worker 返回错误页面")
                
                # 检查是否包含任何画廊链接
                if "/g/" not in raw_html:
                    logger.warning("[Worker搜索] HTML 中不包含任何 /g/ 链接，可能是错误的响应")
                    raise RuntimeError("Worker 返回的 HTML 不包含画廊链接")
                
                # 如果都不是，可能是 E-Hentai 搜索就是无结果，或者 HTML 格式与预期不符
                logger.warning("[Worker搜索] 无法确定返回的是什么页面，将降级到直连模式尝试直接搜索")
                raise RuntimeError("Worker 搜索无结果，可能需要更新解析器或 Worker 实现")
            
            logger.info(f"[Worker搜索] 成功从 HTML 解析出 {len(results)} 条完整结果")
            return results
            
        except httpx.RequestError as e:
            logger.error(f"[Worker搜索] 网络请求错误: {type(e).__name__}: {e}")
            raise RuntimeError(f"Worker 请求失败: {e}")
        except Exception as e:
            logger.error(f"[Worker搜索] 未知错误: {type(e).__name__}: {e}")
            raise RuntimeError(f"Worker 搜索出错: {e}")

    async def search(
        self, keyword: str, limit: int = 5, options: Optional[SearchOptions] = None, eh_page: int = 0
    ) -> list[GalleryResult]:
        logger.info(f"[搜索] 开始搜索: keyword='{keyword}', limit={limit}, site={self.site}, backend={self.backend}")
        
        # 如果配置了 Cloudflare Worker，使用 Worker 搜索
        if self.cloudflare_worker_url:
            logger.info(f"[搜索] 使用 Cloudflare Worker 搜索: {self.cloudflare_worker_url}")
            try:
                results = await self._search_via_worker(keyword, limit, options, eh_page)
                logger.info(f"[搜索] Worker 搜索成功，获得 {len(results)} 条结果")
                
                # 如果 Worker 搜索返回空结果，尝试切换站点重新搜索
                if not results:
                    logger.warning(f"[搜索] Worker 搜索在站点 {self.site} 返回 0 结果，尝试切换到备选站点")
                    original_site = self.site
                    original_base_url = self.base_url
                    
                    try:
                        # 切换站点
                        if self.site == "ex":
                            self.site = "e"
                            self.base_url = "https://e-hentai.org"
                            logger.info("[搜索] 已切换到 e-hentai.org，重新搜索...")
                        else:
                            self.site = "ex"
                            self.base_url = "https://exhentai.org"
                            logger.info("[搜索] 已切换到 exhentai.org，重新搜索...")
                        
                        # 重新搜索
                        results = await self._search_via_worker(keyword, limit, options, eh_page)
                        logger.info(f"[搜索] 备选站点搜索完成，获得 {len(results)} 条结果")
                        
                    finally:
                        # 恢复原始站点设置（这样不会影响后续的其他搜索）
                        # 但如果新站点有结果，可以保持使用该站点
                        if not results:
                            self.site = original_site
                            self.base_url = original_base_url
                            logger.info(f"[搜索] 两个站点都无结果，恢复原始站点设置")
                
                await self._enrich_japanese_titles(results)
                return results
            except Exception as error:
                logger.warning(f"[搜索] Worker 搜索出错: {type(error).__name__}: {error}，将降级到直连模式")
                # 继续使用原有的直连逻辑
        
        # 如果配置了搜索失败立即降级，或者后端是 curl_cffi，尝试 curl_cffi
        # 但如果失败则立即转向 httpx
        if self.backend == "curl_cffi":
            try:
                logger.debug(f"[搜索] 使用 curl_cffi 后端搜索")
                results = await asyncio.to_thread(self._search_sync, keyword, limit, options, None, eh_page)
                await self._enrich_japanese_titles(results)
                return results
            except Exception as error:
                logger.warning(f"[搜索] curl_cffi 搜索出错: {type(error).__name__}: {error}")
                if self.curl_cffi_skip_on_error:
                    logger.info("[搜索] 配置为 curl_cffi 失败即降级，切换 httpx 继续")
                else:
                    if self.http3 and self._is_quic_tls_error(error):
                        logger.info(f"[搜索] 检测到 QUIC/TLS 错误，自动降级到 HTTP/1.1")
                        results = await asyncio.to_thread(
                            self._search_sync, keyword, limit, options, False, eh_page
                        )
                        await self._enrich_japanese_titles(results)
                        return results
                    if not self._should_fallback_to_httpx(error):
                        logger.error(f"[搜索] 错误不可恢复，抛出异常")
                        raise
                    logger.info(f"[搜索] 将使用 httpx 后端作为备选")

        search_url = self._build_search_url(keyword, options, eh_page)
        logger.debug(f"[搜索] 构建的搜索 URL: {search_url}")

        # 先尝试直连 IP 模式
        if self.enable_direct_ip:
            try:
                request_url = self._get_request_url_for_direct_ip(search_url)
                logger.debug(f"[搜索] 使用直连 IP 模式: {request_url}")
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    verify=False,
                    follow_redirects=True,
                ) as client:
                    logger.debug(f"[搜索] 发送搜索请求 (直连 IP)")
                    resp = await client.get(request_url, headers=self._headers_for_url(search_url))
                logger.info(f"[搜索] 直连 IP 搜索成功，状态码: {resp.status_code}")
                results = self._search_from_response(resp, limit)
                await self._enrich_japanese_titles(results)
                return results
            except Exception as error:
                if self._is_connect_error(error):
                    logger.warning(
                        f"[搜索] 直连 IP 连接失败，自动降级到标准 DNS: {type(error).__name__}: {error}",
                    )
                    # 临时禁用直连 IP
                    self.enable_direct_ip = False
                    try:
                        # 重试一次
                        logger.info(f"[搜索] 使用标准 DNS 重试搜索")
                        async with httpx.AsyncClient(
                            timeout=self.timeout,
                            verify=False,
                            follow_redirects=True,
                        ) as client:
                            logger.debug(f"[搜索] 发送搜索请求 (标准 DNS)")
                            resp = await client.get(search_url, headers=self._headers_for_url(search_url))
                        logger.info(f"[搜索] 标准 DNS 搜索成功，状态码: {resp.status_code}")
                        results = self._search_from_response(resp, limit)
                        await self._enrich_japanese_titles(results)
                        return results
                    finally:
                        self.enable_direct_ip = True
                else:
                    logger.error(f"[搜索] 直连 IP 发生非连接错误: {type(error).__name__}: {error}")
                    raise

        # 使用标准客户端
        logger.debug(f"[搜索] 使用标准 DNS 模式")
        async with httpx.AsyncClient(
            timeout=self.timeout,
            verify=False,
            follow_redirects=True,
        ) as client:
            logger.debug(f"[搜索] 发送搜索请求 (标准 DNS)")
            resp = await client.get(search_url, headers=self._headers_for_url(search_url))
        logger.info(f"[搜索] 搜索成功，状态码: {resp.status_code}")
        results = self._search_from_response(resp, limit)
        
        # 如果搜索返回空结果，尝试切换站点重新搜索
        if not results:
            logger.warning(f"[搜索] 直连搜索在站点 {self.site} 返回 0 结果，尝试切换到备选站点")
            original_site = self.site
            original_base_url = self.base_url
            
            try:
                # 切换站点
                if self.site == "ex":
                    self.site = "e"
                    self.base_url = "https://e-hentai.org"
                    logger.info("[搜索] 已切换到 e-hentai.org，重新搜索...")
                else:
                    self.site = "ex"
                    self.base_url = "https://exhentai.org"
                    logger.info("[搜索] 已切换到 exhentai.org，重新搜索...")
                
                # 重新构建搜索 URL 并搜索
                search_url = self._build_search_url(keyword, options, eh_page)
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    verify=False,
                    follow_redirects=True,
                ) as client:
                    logger.debug(f"[搜索] 使用备选站点发送搜索请求")
                    resp = await client.get(search_url, headers=self._headers_for_url(search_url))
                logger.info(f"[搜索] 备选站点搜索完成，状态码: {resp.status_code}")
                results = self._search_from_response(resp, limit)
                
            except Exception as e:
                logger.error(f"[搜索] 备选站点搜索失败: {type(e).__name__}: {e}")
                # 备选站点搜索失败，恢复原始设置
                self.site = original_site
                self.base_url = original_base_url
                # 返回空结果而不是抛出异常
            finally:
                # 如果新站点没有结果，恢复原始站点设置
                if not results:
                    self.site = original_site
                    self.base_url = original_base_url
                    logger.info(f"[搜索] 两个站点都无结果，恢复原始站点设置")
        
        await self._enrich_japanese_titles(results)
        return results

    async def search_paged(
        self,
        keyword: str,
        bot_page: int = 1,
        results_per_page: int = 5,
        max_eh_pages: int = 3,
        options: Optional[SearchOptions] = None,
    ) -> tuple[list[GalleryResult], int]:
        """分页搜索。bot_page 从 1 开始，每页显示 results_per_page 条。
        
        Returns: (当前页结果列表, 本次共抓取的条数)
        最多抓取 max_eh_pages 个 e-hentai 搜索页（每页约 25 条）。
        """
        EH_PAGE_SIZE = 25
        start = (bot_page - 1) * results_per_page
        end = bot_page * results_per_page

        # 第 1 页快速路径：只抓取需要的数量
        if bot_page == 1:
            results = await self.search(keyword, results_per_page, options, eh_page=0)
            return results[:results_per_page], len(results)

        # 计算需要几个 e-hentai 页面
        from math import ceil as _ceil
        eh_pages_needed = min(max_eh_pages, _ceil(end / EH_PAGE_SIZE))

        all_results: list[GalleryResult] = []
        seen: set[str] = set()

        for eh_page_idx in range(eh_pages_needed):
            if len(all_results) >= end:
                break
            page_results = await self.search(keyword, EH_PAGE_SIZE, options, eh_page=eh_page_idx)
            for r in page_results:
                if r.gid not in seen:
                    seen.add(r.gid)
                    all_results.append(r)
            if len(page_results) < EH_PAGE_SIZE:
                break  # e-hentai 已无更多结果

        return all_results[start:end], len(all_results)

    async def _get_archive_page(self, client: httpx.AsyncClient, gid: str, token: str, gallery_domain: Optional[str] = None) -> str:
        # 如果提供了gallery_domain，使用用户原始域名；否则使用self.base_url
        base_url = gallery_domain if gallery_domain else self.base_url
        archive_page_url = f"{base_url}/archiver.php?gid={gid}&token={token}"
        logger.debug(f"[存档] 获取存档页面: {archive_page_url}")
        
        if self.cloudflare_worker_url:
            logger.debug(f"[存档] 使用 Worker 获取存档页面")
            try:
                resp = await self._fetch_url_via_worker(archive_page_url, method="GET")
            except Exception as error:
                logger.error(f"[存档] Worker 代理请求失败: {error}")
                raise
        else:
            # 对于直连 IP，将主机名转换为 IP
            request_url = self._get_request_url_for_direct_ip(archive_page_url) if self.enable_direct_ip else archive_page_url
            logger.debug(f"[存档] 请求 URL: {request_url}, 直连模式: {self.enable_direct_ip}")
            resp = await client.get(
                request_url, headers=self._headers_for_url(archive_page_url)
            )
        
        logger.debug(f"[存档] 获取存档页面响应: 状态码={resp.status_code}")
        if not self.cloudflare_worker_url:
            self._raise_for_response(resp)
            
        if self._is_login_required_page(resp.text):
            logger.error(f"[存档] 需要登录 Cookie 才能访问")
            raise RuntimeError("下载归档需要已登录的 E-Hentai/ExHentai Cookie")
        return resp.text

    def _select_archive_option(self, options: list[ArchiveOption], prefer_original: bool = False) -> ArchiveOption:
        """
        选择最优的存档选项
        prefer_original=False: 优先选择 resample（小文件）
        prefer_original=True: 优先选择 original（高质量）
        """

        def is_original(item: ArchiveOption) -> bool:
            text = f"{item.res} {item.name}".lower()
            return any(key in text for key in ("org", "original", "source"))

        def is_resample(item: ArchiveOption) -> bool:
            text = f"{item.res} {item.name}".lower()
            return any(key in text for key in ("resample", "resampled", "res"))

        normal_options = [item for item in options if not item.is_hath]
        if not normal_options:
            return options[0]

        if prefer_original:
            preferred = next((item for item in normal_options if is_original(item)), None)
            if preferred is None:
                preferred = normal_options[0]
        else:
            preferred = next((item for item in normal_options if is_resample(item)), None)
            if preferred is None:
                preferred = next((item for item in normal_options if not is_original(item)), None)
            if preferred is None:
                preferred = normal_options[0]

        logger.debug(
            f"[存档] 存档选项选择: prefer_original={prefer_original}, chosen=(res={preferred.res}, name={preferred.name}, size={preferred.size})"
        )
        return preferred

    def _parse_archive_options(self, body: str) -> list[ArchiveOption]:
        soup = BeautifulSoup(body, "html.parser")

        options: list[ArchiveOption] = []
        archive_blocks = soup.select("#db > div > div")
        for block in archive_blocks:
            style = (block.get("style") or "").replace(" ", "").lower()
            if "color:#cccccc" in style:
                continue

            input_tag = block.select_one("form input[value]")
            name_tag = block.select_one("form div input[value]")
            size_tag = block.select_one("p strong")
            cost_tag = block.select_one("div strong")
            if not input_tag or not size_tag or not cost_tag:
                continue

            res = input_tag.get("value", "").strip()
            if not res:
                continue

            name_text = ""
            if name_tag is not None:
                name_text = name_tag.get("value", "").strip()
            if not name_text:
                name_text = block.get_text(" ", strip=True)

            options.append(
                ArchiveOption(
                    res=res,
                    name=name_text,
                    size=size_tag.get_text(" ", strip=True),
                    cost=cost_tag.get_text(" ", strip=True).replace(",", ""),
                    is_hath=False,
                )
            )

        hath_pattern = re.compile(
            r"do_hathdl\('([0-9]+|org)'\)\">([^<]+)</a></p>\s*"
            r"<p>([\w. ]+)</p>\s*<p>([\w. ]+)</p>",
            flags=re.IGNORECASE,
        )
        for match in hath_pattern.finditer(body):
            options.append(
                ArchiveOption(
                    res=match.group(1),
                    name=match.group(2),
                    size=match.group(3),
                    cost=match.group(4),
                    is_hath=True,
                )
            )

        return options

    async def _request_archive_download_url(
        self,
        client: httpx.AsyncClient,
        gid: str,
        token: str,
        archive_option: ArchiveOption,
    ) -> Optional[str]:
        archive_url = f"{self.base_url}/archiver.php?gid={gid}&token={token}"
        # 对于直连 IP，将主机名转换为 IP
        request_url = self._get_request_url_for_direct_ip(archive_url)
        payload: dict[str, str] = {}

        if archive_option.is_hath:
            payload["hathdl_xres"] = archive_option.res
        else:
            payload["dltype"] = archive_option.res
            if archive_option.res == "org":
                payload["dlcheck"] = "Download Original Archive"
            else:
                payload["dlcheck"] = "Download Resample Archive"

        async def do_post() -> Optional[str]:
            if self.cloudflare_worker_url:
                logger.debug(f"[存档] 使用 Worker 发送 POST")
                resp = await self._fetch_url_via_worker(archive_url, method="POST", data=payload)
            else:
                resp = await client.post(
                    request_url,
                    data=payload,
                    headers=self._headers_for_url(archive_url),
                )
                self._raise_for_response(resp)

            body = resp.text
            if NEED_HATH_CLIENT_MSG in body:
                raise RuntimeError("当前账号未分配 H@H 客户端，无法使用该下载方式")
            if INSUFFICIENT_FUNDS_MSG in body:
                raise RuntimeError("当前账号 GP/Credits 不足，无法下载该归档")

            soup = BeautifulSoup(body, "html.parser")
            continue_link = soup.select_one("#continue a[href]")
            if continue_link is None:
                return None

            href = continue_link.get("href", "").strip()
            if not href:
                return None

            full = self._normalize_gallery_url(href)
            return f"{full}?start=1"

        result = await do_post()
        if result is None and not archive_option.is_hath:
            await asyncio.sleep(1)
            result = await do_post()
        return result

    async def resolve_archive_url(self, gallery_url: str, prefer_original: bool = False) -> Optional[str]:
        logger.info(f"[存档] 开始解析存档下载链接: {gallery_url}, prefer_original={prefer_original}")
        logger.debug(f"[存档] 下载链路固定使用 httpx")

        gid_token = self._extract_gid_token(gallery_url)
        if not gid_token:
            logger.warning(f"[存档] 无法从 URL 提取 gid/token")
            return None

        gid, token = gid_token
        logger.debug(f"[存档] 提取到 gid={gid}, token={token}")
        
        # 提取用户提供的域名，保持原始站点
        parsed_url = urlparse(gallery_url)
        gallery_domain = f"{parsed_url.scheme}://{parsed_url.netloc}"
        logger.debug(f"[存档] 使用用户提供的域名: {gallery_domain}")

        # 先尝试直连 IP 模式
        if self.enable_direct_ip:
            try:
                logger.debug(f"[存档] 使用直连 IP 模式")
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    verify=False,
                    follow_redirects=True,
                ) as client:
                    logger.debug(f"[存档] 获取存档页面 (直连 IP)")
                    archive_page = await self._get_archive_page(client, gid, token, gallery_domain)
                    if NEED_HATH_CLIENT_MSG in archive_page:
                        logger.warning(f"[存档] 需要 H@H 客户端")
                        return None

                    logger.debug(f"[存档] 解析存档选项")
                    options = self._parse_archive_options(archive_page)
                    if not options:
                        logger.warning(f"[存档] 未找到可用的存档选项")
                        return None

                    preferred = self._select_archive_option(options, prefer_original)
                    logger.debug(f"[存档] 选择存档: {preferred.res} (prefer_original={prefer_original})")
                    url = await self._request_archive_download_url(client, gid, token, preferred)
                    logger.info(f"[存档] 成功获取下载链接 (直连 IP)")
                    return url
            except Exception as error:
                if self._is_connect_error(error):
                    logger.warning(
                        f"[存档] 直连 IP 连接失败，自动降级到标准 DNS: {type(error).__name__}: {error}",
                    )
                    # 临时禁用直连 IP，重试
                    self.enable_direct_ip = False
                    try:
                        logger.info(f"[存档] 使用标准 DNS 重试")
                        async with httpx.AsyncClient(
                            timeout=self.timeout,
                            verify=False,
                            follow_redirects=True,
                        ) as client:
                            logger.debug(f"[存档] 获取存档页面 (标准 DNS)")
                            archive_page = await self._get_archive_page(client, gid, token, gallery_domain)
                            if NEED_HATH_CLIENT_MSG in archive_page:
                                logger.warning(f"[存档] 需要 H@H 客户端")
                                return None

                            logger.debug(f"[存档] 解析存档选项")
                            options = self._parse_archive_options(archive_page)
                            if not options:
                                logger.warning(f"[存档] 未找到可用的存档选项")
                                return None

                            preferred = self._select_archive_option(options, prefer_original)
                            logger.debug(f"[存档] 选择存档: {preferred.res} (prefer_original={prefer_original})")
                            url = await self._request_archive_download_url(client, gid, token, preferred)
                            logger.info(f"[存档] 成功获取下载链接 (标准 DNS)")
                            return url
                    finally:
                        self.enable_direct_ip = True
                else:
                    logger.error(f"[存档] 直连 IP 发生非连接错误: {type(error).__name__}: {error}")
                    raise

        # 使用标准客户端
        logger.debug(f"[存档] 使用标准 DNS 模式")
        async with httpx.AsyncClient(
            timeout=self.timeout,
            verify=False,
            follow_redirects=True,
        ) as client:
            logger.debug(f"[存档] 获取存档页面 (标准 DNS)")
            archive_page = await self._get_archive_page(client, gid, token, gallery_domain)
            if NEED_HATH_CLIENT_MSG in archive_page:
                logger.warning(f"[存档] 需要 H@H 客户端")
                return None

            logger.debug(f"[存档] 解析存档选项")
            options = self._parse_archive_options(archive_page)
            if not options:
                logger.warning(f"[存档] 未找到可用的存档选项")
                return None

            preferred = self._select_archive_option(options, prefer_original)
            logger.debug(f"[存档] 选择存档: {preferred.res} (prefer_original={prefer_original})")
            url = await self._request_archive_download_url(client, gid, token, preferred)
            logger.info(f"[存档] 成功获取下载链接 (标准 DNS)")
            return url

    async def download_file(self, url: str, save_path: Path) -> Path:
        """下载文件到本地
        
        处理逻辑：
        1. 如果文件已存在且大小合理，直接返回（缓存优化）
        2. 如果文件部分下载（残留），先删除
        3. 创建下载目录
        4. 下载文件到临时位置，完成后再移动
        """
        logger.info(f"[下载] 开始下载文件: {url}")
        logger.debug(f"[下载] 保存路径: {save_path}")
        
        # 关键改进：检查文件是否已存在
        if save_path.exists():
            file_size = save_path.stat().st_size
            logger.info(f"[下载] 文件已存在: {save_path.name} ({file_size / 1024 / 1024:.2f} MB)")
            
            # 如果文件大小超过最小阈值，认为是有效缓存，直接返回
            if file_size >= self.min_cache_file_size_bytes:
                logger.info(f"[下载] 使用缓存文件（已存在）")
                return save_path
            else:
                # 文件太小，可能是残留的不完整文件，删除重新下载
                logger.warning(f"[下载] 文件过小（{file_size} 字节），可能是残留，删除后重新下载")
                try:
                    save_path.unlink()
                    logger.debug(f"[下载] 删除不完整文件成功")
                except Exception as e:
                    logger.error(f"[下载] 删除文件失败: {e}")
                    # 继续尝试覆盖
        
        logger.debug(f"[下载] 下载链路固定使用 httpx")

        save_path.parent.mkdir(parents=True, exist_ok=True)
        logger.debug(f"[下载] 创建下载目录成功")

        # 先尝试直连 IP 模式
        if self.enable_direct_ip:
            try:
                request_url = self._get_request_url_for_direct_ip(url)
                logger.debug(f"[下载] 使用直连 IP 模式: {request_url}")
                async with self._client() as client:
                    logger.debug(f"[下载] 开始下载流 (直连 IP)")
                    async with client.stream("GET", request_url, headers=self._headers_for_url(url)) as resp:
                        self._raise_for_response(resp)
                        logger.debug(f"[下载] 响应状态码: {resp.status_code}")
                        downloaded = 0
                        with save_path.open("wb") as file:
                            async for chunk in resp.aiter_bytes(chunk_size=1024 * 128):
                                file.write(chunk)
                                downloaded += len(chunk)
                        logger.info(f"[下载] 直连 IP 下载成功，大小: {downloaded / 1024 / 1024:.2f} MB")
                return save_path
            except Exception as error:
                # 直连 IP 失败（ConnectError、SSLError 等），自动 fallback
                from httpx import ConnectError as HttpxConnectError
                if isinstance(error, (HttpxConnectError, ssl.SSLError, TimeoutError)):
                    logger.warning(
                        f"[下载] 直连 IP 连接失败，自动降级到标准 DNS: {type(error).__name__}: {error}",
                        exc_info=True
                    )
                    # 临时禁用直连 IP
                    self.enable_direct_ip = False
                    try:
                        # 重试一次
                        logger.info(f"[下载] 使用标准 DNS 重试下载")
                        async with httpx.AsyncClient(
                            timeout=self.timeout,
                            verify=False,
                            follow_redirects=True,
                        ) as client:
                            logger.debug(f"[下载] 开始下载流 (标准 DNS)")
                            async with client.stream("GET", url, headers=self._headers_for_url(url)) as resp:
                                self._raise_for_response(resp)
                                logger.debug(f"[下载] 响应状态码: {resp.status_code}")
                                downloaded = 0
                                with save_path.open("wb") as file:
                                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 128):
                                        file.write(chunk)
                                        downloaded += len(chunk)
                            logger.info(f"[下载] 标准 DNS 下载成功，大小: {downloaded / 1024 / 1024:.2f} MB")
                        return save_path
                    finally:
                        # 恢复设置
                        self.enable_direct_ip = True
                else:
                    # 其他错误直接抛出
                    logger.error(f"[下载] 直连 IP 发生非连接错误: {type(error).__name__}: {error}")
                    raise

        # 降级到标准 httpx 客户端
        logger.debug(f"[下载] 使用标准 DNS 模式")
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout,
                verify=False,
                follow_redirects=True,
            ) as client:
                logger.debug(f"[下载] 开始下载流 (标准 DNS)")
                async with client.stream("GET", url, headers=self._headers_for_url(url)) as resp:
                    self._raise_for_response(resp)
                    logger.debug(f"[下载] 响应状态码: {resp.status_code}")
                    downloaded = 0
                    with save_path.open("wb") as file:
                        async for chunk in resp.aiter_bytes(chunk_size=1024 * 128):
                            file.write(chunk)
                            downloaded += len(chunk)
                logger.info(f"[下载] 下载成功，大小: {downloaded / 1024 / 1024:.2f} MB")

            return save_path
        except Exception as error:
            # 下载失败，清理部分下载的文件（防止第二次重试时出错）
            logger.error(f"[下载] 标准 DNS 下载失败: {type(error).__name__}: {error}")
            if save_path.exists():
                try:
                    save_path.unlink()
                    logger.debug(f"[下载] 清理失败的部分下载文件")
                except Exception as cleanup_error:
                    logger.warning(f"[下载] 清理文件失败: {cleanup_error}")
            raise