import asyncio
import os
import sys

from astrbot_plugin_ehentai.service import EHentaiClient, SearchOptions

async def test():
    # 从你的环境中读取 worker 变量，如果不设就用默认的
    worker_url = os.environ.get("EHENTAI_CLOUDFLARE_WORKER_URL", "你自己的Cloudflare Worker URL")
    cookie = os.environ.get("EHENTAI_COOKIE", "")
    
    if worker_url == "你自己的Cloudflare Worker URL":
        print("请在脚本中填入实际的 worker_url！")
        # 或者直接在这里写死
        # worker_url = "https://your-worker.workers.dev"
        return
        
    print(f"[*] 初始化 Client，使用 Worker: {worker_url}")
    client = EHentaiClient(
        site="e-hentai",
        cloudflare_worker_url=worker_url,
        cookie=cookie
    )
    
    # 找一个随便的本子 URL 测试 fetch /archiver.php
    # 这是你在上一次搜索里截图或看到的一个本子 gid 和 token
    # 例如：https://e-hentai.org/g/3093259/fbed914562/
    test_gallery_url = "https://e-hentai.org/g/3093259/fbed914562/"
    
    print(f"[*] 开始测试获取拱档页面，目标: {test_gallery_url}")
    try:
        archive_url_direct = await client.resolve_archive_url(test_gallery_url, prefer_original=False)
        print(f"\n[+] 成功解析到下载链接: {archive_url_direct}")
    except Exception as e:
        print(f"\n[-] 发生错误: {e}")

if __name__ == "__main__":
    asyncio.run(test())
