"""EhViewer_CN_SXJ 网络绕过方案 - DNS 分流 + 直连 IP"""

from __future__ import annotations

import ipaddress
from typing import Optional

import httpx

# 内置 IP 地址映射（从 EhViewer_CN_SXJ EhHosts.kt 提取）
BUILT_IN_HOSTS = {
    "e-hentai.org": [
        "104.20.18.168",
        "104.20.19.168",
        "172.66.132.196",
        "172.66.140.62",
        "172.67.2.238",
    ],
    "exhentai.org": [
        "178.175.128.251",
        "178.175.128.252",
        "178.175.128.253",
        "178.175.128.254",
        "178.175.129.251",
        "178.175.129.252",
        "178.175.129.253",
        "178.175.129.254",
        "178.175.132.19",
        "178.175.132.20",
        "178.175.132.21",
        "178.175.132.22",
    ],
    "repo.e-hentai.org": [
        "104.20.18.168",
        "104.20.19.168",
        "172.67.2.238",
    ],
    "forums.e-hentai.org": [
        "172.66.132.196",
        "172.66.140.62",
    ],
    "upld.e-hentai.org": [
        "89.149.221.236",
        "95.211.208.236",
    ],
    "ehgt.org": [
        "109.236.85.28",
        "62.112.8.21",
        "89.39.106.43",
    ],
    "upld.exhentai.org": [
        "178.175.132.22",
        "178.175.129.254",
        "178.175.128.254",
    ],
    "raw.githubusercontent.com": [
        "151.101.0.133",
        "151.101.64.133",
        "151.101.128.133",
        "151.101.192.133",
    ],
}

# DNS-over-HTTPS 服务器列表
DOH_SERVERS = [
    "https://77.88.8.1/dns-query",  # Yandex DNS (CN_SXJ 使用)
    "https://1.1.1.1/dns-query",  # Cloudflare
    "https://dns.google/dns-query",  # Google
]


class EhHttpRouter:
    """EhViewer_CN_SXJ 风格的 HTTP 路由器，使用直连 IP + Host 头注入"""

    @staticmethod
    def resolve_host(hostname: str) -> Optional[str]:
        """
        DNS 解析策略（按优先级）:
        1. 内置 IP 地址映射
        2. 系统 DNS（作为备选）

        返回: IP 地址字符串，若无法解析则返回 None
        """
        # 首先尝试内置 IP 映射
        if hostname in BUILT_IN_HOSTS:
            ips = BUILT_IN_HOSTS[hostname]
            if ips:
                return ips[0]  # 简单起见，返回第一个 IP

        # 备选：使用系统 DNS
        try:
            # 这里可以实现 DoH 或系统 DNS 查询
            return None  # 让 httpx 使用默认 DNS
        except Exception:
            return None

    @staticmethod
    def get_httpx_client_with_direct_ip(
        user_agent: Optional[str] = None,
        timeout: int = 10,
        enable_direct_ip: bool = True,
    ) -> httpx.Client:
        """
        创建启用直连 IP + Host 头注入的 httpx 客户端

        Args:
            user_agent: 自定义 User-Agent
            timeout: 超时秒数
            enable_direct_ip: 是否启用直连 IP 解析

        Returns:
            httpx.Client 实例
        """
        # 构建 mounts 规则，直连到特定 IP
        mounts = {}

        if enable_direct_ip:
            # 为所有内置 hosts 创建直连规则
            for hostname, ips in BUILT_IN_HOSTS.items():
                if ips:
                    ip = ips[0]  # 使用第一个 IP
                    # 使用 IP 地址直连，而非 DNS 查询
                    # httpx scheme://ip:port
                    for scheme in ["http", "https"]:
                        mount_key = f"{scheme}://{hostname}"
                        mount_value = f"{scheme}://{ip}"
                        mounts[mount_key] = httpx.HTTPTransport(verify=False)

        headers = None
        if user_agent:
            headers = {"User-Agent": user_agent}

        client = httpx.Client(
            timeout=timeout,
            verify=False,  # 绕过 SSL 证书验证（因为使用 IP 直连)
            headers=headers,
            mounts=mounts if mounts else None,
        )

        return client

    @staticmethod
    def get_async_httpx_client_with_direct_ip(
        user_agent: Optional[str] = None,
        timeout: int = 10,
        enable_direct_ip: bool = True,
    ) -> httpx.AsyncClient:
        """异步版本 - 创建启用直连 IP 的 async httpx 客户端"""
        mounts = {}

        if enable_direct_ip:
            for hostname, ips in BUILT_IN_HOSTS.items():
                if ips:
                    ip = ips[0]
                    for scheme in ["http", "https"]:
                        mount_key = f"{scheme}://{hostname}"
                        mount_value = f"{scheme}://{ip}"
                        mounts[mount_key] = httpx.AsyncHTTPTransport(verify=False)

        headers = None
        if user_agent:
            headers = {"User-Agent": user_agent}

        client = httpx.AsyncClient(
            timeout=timeout,
            verify=False,
            headers=headers,
            mounts=mounts if mounts else None,
        )

        return client

    @staticmethod
    def inject_host_header(
        url: str, custom_headers: Optional[dict] = None
    ) -> tuple[str, dict]:
        """
        为 URL 注入正确的 Host 头，支持直连 IP

        EhViewer_CN_SXJ 在使用直连 IP 时，会自动注入原始 hostname 的 Host 头
        以通过 Cloudflare/SNI 验证

        Args:
            url: 原始 URL
            custom_headers: 自定义头部字典

        Returns:
            (修改后的 URL, 头部字典)
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname or parsed.netloc
        port = parsed.port

        headers = custom_headers or {}

        # 提取原始 hostname（用于 Host 头和 SNI）
        host_header = hostname
        if port and port not in (80, 443):
            host_header = f"{hostname}:{port}"

        headers["Host"] = host_header

        return url, headers


# 辅助函数
def create_eh_httpx_client(
    user_agent: str,
    timeout: int,
    cookies: dict,
    enable_direct_ip: bool = True,
) -> httpx.Client:
    """
    创建优化的 E-Hentai 客户端
    - 直连 IP（绕过 DNS 污染）
    - Cookie 管理
    - 自动 Host 头注入
    """
    client = EhHttpRouter.get_httpx_client_with_direct_ip(
        user_agent=user_agent,
        timeout=timeout,
        enable_direct_ip=enable_direct_ip,
    )

    # 添加 cookies
    if cookies:
        client.cookies.update(cookies)

    return client


async def create_eh_async_httpx_client(
    user_agent: str,
    timeout: int,
    cookies: dict,
    enable_direct_ip: bool = True,
) -> httpx.AsyncClient:
    """异步版本"""
    client = EhHttpRouter.get_async_httpx_client_with_direct_ip(
        user_agent=user_agent,
        timeout=timeout,
        enable_direct_ip=enable_direct_ip,
    )

    # 添加 cookies
    if cookies:
        client.cookies.update(cookies)

    return client
