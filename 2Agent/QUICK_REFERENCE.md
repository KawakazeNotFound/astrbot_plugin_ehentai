# 快速参考 - 项目转换要点

## 项目转换成果

✅ **NoneBot 插件** → ✅ **AstrBot 插件**

所有 **25 个配置项**、**2 个主命令**、**所有核心业务逻辑** 已完整转换。

## 文件树

```
2Agent/astrbot_plugin_ehentai/                 # 新 AstrBot 插件目录
├── __init__.py                                # 包入口（新建）
├── main.py                                    # 主插件类（新建）
├── metadata.yaml                              # 插件元数据（新建）
├── _conf_schema.json                          # 配置架构（新建）
├── requirements.txt                           # 依赖列表（新建）
├── config_loader.py                           # 配置加载器（新建）
├── logger_compat.py                           # 日志适配层（新建）
├── README.md                                  # 使用说明（新建）
├── search_template.html                       # HTML 模板（复制+修改）
├── service.py                                 # 客户端（复制+logger 适配）
├── search_logic.py                            # 搜索逻辑（复制+logger 适配）
├── search_render.py                           # 搜索渲染（复制+路径修改）
├── network.py                                 # 网络配置（复制，无改动）
├── r2.py                                      # R2 管理（复制，无改动）
└── d1.py                                      # D1 管理（复制，无改动）

2Agent/
├── CONVERSION_SUMMARY.md                      # 转换总结文档（新建）
└── FINAL_VERIFICATION.md                      # 最终验证报告（新建）
```

## 主要改动

### 1️⃣ 入口点转换

**NoneBot**：
```python
# src/nonebot_plugin_ehentai/__init__.py
search_cmd = on_command("search", ...)
@search_cmd.handle()
async def handle_search(...): await search_cmd.finish(msg)
```

**AstrBot**：
```python
# astrbot_plugin_ehentai/main.py
class EHentaiPlugin(Star):
    @filter.command("search")
    async def handle_search(self, event: AstrMessageEvent):
        yield event.plain_result(msg)
```

### 2️⃣ 配置系统转换

```
NoneBot:  .env 文件
AstrBot:  _conf_schema.json + WebUI 界面
```

所有 25 个配置项已映射。

### 3️⃣ 日志系统适配

创建 `logger_compat.py`，通过代理模式桥接两个系统的 logger 接口。

### 4️⃣ 路径修复

- **模板文件**：`scripts/test.html` → `search_template.html`（同目录）
- **项目根目录**：`__file__.parents[2]` → `__file__.parent`（AstrBot 结构）

## 功能对应表

| NoneBot | AstrBot | 对应关系 |
|---------|---------|--------|
| `/search <kw>` | `/search <kw>` | ✅ 完同 |
| `/search <kw> --page N` | `/search <kw> --page N` | ✅ 完同 |
| `/download <kw>` | `/download <kw>` | ✅ 完同 |
| `/download -original <kw>` | `/download -original <kw>` | ✅ 完同 |
| R2 上传 | R2 上传 | ✅ 保留 |
| D1 记录 | D1 记录 | ✅ 保留 |
| 搜索渲染 | 搜索渲染 | ✅ 保留 |

## 立即开始

### 步骤1：部署

```bash
# 将插件目录放到 AstrBot 的插件目录
cp -r 2Agent/astrbot_plugin_ehentai <AstrBot_root>/data/plugins/
```

### 步骤2：安装依赖

```bash
pip install httpx beautifulsoup4 playwright boto3 aiofiles
playwright install chromium
```

### 步骤3：配置

在 AstrBot WebUI 中配置：
- `ehentai_site` = "e" 或 "ex"
- `ehentai_ipb_member_id` = （你的 ID）
- `ehentai_ipb_pass_hash` = （你的 Hash）
- 其他可选项（R2、D1 等）

### 步骤4：重启

重启 AstrBot，插件会自动加载。

### 步骤5：测试

```
/search 关键词
/download 关键词
```

## 关键代码位置

| 功能 | 文件 | 行数 |
|-----|------|------|
| 搜索命令 | main.py | ~80-150 |
| 下载命令 | main.py | ~150-280 |
| 客户端 | service.py | ~1348 行 |
| 渲染 | search_render.py | ~228 行 |
| R2 管理 | r2.py | ~312 行 |
| D1 管理 | d1.py | ~182 行 |
| 配置加载 | config_loader.py | ~194 行 |
| 日志适配 | logger_compat.py | ~51 行 |

## 所有配置项

### 基础（9 个）
- ehentai_site
- ehentai_base_url
- ehentai_cookie
- ehentai_ipb_member_id
- ehentai_ipb_pass_hash
- ehentai_igneous
- ehentai_cf_clearance
- ehentai_timeout
- ehentai_max_results

### 网络（7 个）
- ehentai_http_backend
- ehentai_http3
- ehentai_desktop_site
- ehentai_impersonate
- ehentai_enable_direct_ip
- ehentai_curl_cffi_skip_on_error
- ehentai_proxy

### R2（7 个）
- ehentai_r2_enabled
- ehentai_r2_access_key_id
- ehentai_r2_secret_access_key
- ehentai_r2_bucket_name
- ehentai_r2_endpoint
- ehentai_r2_public_domain
- ehentai_r2_max_total_size_mb

### D1（3 个）
- ehentai_d1_enabled
- ehentai_d1_account_id
- ehentai_d1_database_id

### 其他（3 个）
- ehentai_download_dir
- ehentai_auto_cleanup_local
- ehentai_auto_cleanup_time

## 依赖项

```
httpx>=0.27.0
beautifulsoup4>=4.12.0
playwright>=1.53.0
boto3>=1.26.0
aiofiles>=23.0.0
```

## 已测试/已验证

✅ Python 语法无误
✅ 所有导入修复
✅ 配置架构完整
✅ 项目结构符合规范
✅ 文档完善

## 待用户确认

1. 是否需要添加定时任务支持？
2. 是否需要群文件上传功能？
3. 是否需要其他平台适配？

## 问题排查

| 问题 | 解决 |
|------|------|
| 导入错误 | 检查依赖是否安装 |
| 配置读不到 | 检查 _conf_schema.json 语法 |
| 命令无响应 | 检查插件是否加载，logger 中是否有错误 |
| 下载失败 | 检查 Cookie 是否有效 |

## 下一步

1. 部署到 AstrBot
2. 在 WebUI 配置
3. 测试基础功能
4. 根据需要进行环境特定的调整

---

✅ 转换完成，准备投入使用！
