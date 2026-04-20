#!/usr/bin/env python3
"""
调试脚本：测试 Cloudflare Worker 返回的 HTML 内容
帮助诊断为什么 Worker 搜索返回空结果
"""

import asyncio
import httpx
import json
from bs4 import BeautifulSoup

async def debug_worker_search(
    worker_url: str,
    keyword: str = "loli",
    base_url: str = "https://e-hentai.org",
    cookies: str = ""
):
    """
    测试 Worker 搜索返回的 HTML
    """
    print(f"[调试] 开始测试 Worker 搜索")
    print(f"  Worker URL: {worker_url}")
    print(f"  关键词: {keyword}")
    print(f"  基础 URL: {base_url}")
    print(f"  Cookie 长度: {len(cookies)} 字符")
    print()
    
    payload = {
        "keyword": keyword,
        "page": 0,
        "baseUrl": base_url,
        "rawHtml": True
    }
    
    if cookies:
        payload["cookies"] = cookies
    
    try:
        async with httpx.AsyncClient(timeout=30, verify=False, follow_redirects=True) as client:
            resp = await client.post(
                worker_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            )
        
        print(f"[调试] 响应状态码: {resp.status_code}")
        
        if resp.status_code != 200:
            print(f"[调试] 错误: 返回非 200 状态码")
            print(f"[调试] 响应内容: {resp.text[:500]}")
            return False
        
        # 解析 JSON 响应
        try:
            data = resp.json()
        except json.JSONDecodeError:
            print(f"[调试] 错误: 返回内容不是 JSON")
            print(f"[调试] 响应内容: {resp.text[:500]}")
            return False
        
        print(f"[调试] JSON 响应键: {list(data.keys())}")
        
        if not data.get("success"):
            print(f"[调试] Worker 返回失败: {data.get('error')}")
            return False
        
        raw_html = data.get("html", "")
        if not raw_html:
            print(f"[调试] 警告: 响应中没有 html 字段")
            return False
        
        print(f"[调试] 获得 HTML 大小: {len(raw_html)} 字节")
        print()
        
        # 检查 HTML 结构
        print("[检查 HTML 结构]")
        if "class=\"itg\"" in raw_html or "class='itg'" in raw_html:
            print("✓ 找到 .itg 容器")
        else:
            print("✗ 未找到 .itg 容器")
        
        if "</html>" in raw_html:
            print("✓ HTML 看起来是完整的")
        else:
            print("⚠ HTML 可能不完整")
        
        if "e-hentai.org" in raw_html or "exhentai.org" in raw_html:
            print("✓ HTML 包含 E-Hentai 域名")
        else:
            print("✗ HTML 不包含 E-Hentai 域名")
        
        if "login" in raw_html.lower() or "password" in raw_html.lower():
            print("✗ 检测到登录页面")
        else:
            print("✓ 不是登录页面")
        
        if "/g/" in raw_html:
            print("✓ HTML 包含画廊链接格式")
        else:
            print("✗ HTML 不包含画廊链接")
        
        print()
        
        # 尝试解析
        soup = BeautifulSoup(raw_html, 'html.parser')
        itg_tables = soup.find_all('table', class_='itg')
        print(f"[解析] 找到 {len(itg_tables)} 个 .itg 表格")
        
        # 查找所有包含画廊链接的标签
        gallery_links = soup.find_all('a', href=lambda x: x and '/g/' in x if x else False)
        print(f"[解析] 找到 {len(gallery_links)} 个画廊链接")
        
        if gallery_links:
            print(f"[样本] 前 3 个链接:")
            for i, link in enumerate(gallery_links[:3]):
                href = link.get('href', '')
                text = link.get_text(strip=True)[:50]
                print(f"  {i+1}. href={href} text={text}")
        
        print()
        
        # HTML 样本
        print("[HTML 样本 (前 1000 字符)]")
        print(raw_html[:1000])
        print("...")
        print()
        
        # 最后 500 字符
        print("[HTML 样本 (最后 500 字符)]")
        print(raw_html[-500:])
        
        return True
        
    except Exception as e:
        print(f"[调试] 异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    import sys
    
    # 支持命令行参数
    worker_url = sys.argv[1] if len(sys.argv) > 1 else "https://eh.shirasuazusa.workers.dev/"
    base_url = sys.argv[2] if len(sys.argv) > 2 else "https://exhentai.org"
    keyword = sys.argv[3] if len(sys.argv) > 3 else "loli"
    cookies = sys.argv[4] if len(sys.argv) > 4 else ""
    
    print()
    success = await debug_worker_search(worker_url, keyword, base_url, cookies)
    print()
    print(f"[结果] {'✓ 调试成功' if success else '✗ 调试失败'}")

if __name__ == '__main__':
    asyncio.run(main())
