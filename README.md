# AstrBot E-Hentai 搜索下载插件

这是 `nonebot-plugin-ehentai` 的 AstrBot 版本。

## 功能

- `/search <关键词>` - 搜索 E-Hentai 本子并返回搜索结果
- `/download [-original] <关键词>` - 搜索并下载 E-Hentai 本子到 R2 或本地

## 安装

1. 将此插件复制到 AstrBot 的 `data/plugins/` 目录下
2. 重启 AstrBot，插件会自动被发现
3. 在 AstrBot WebUI 中配置插件选项

## 配置

在 AstrBot WebUI 中的"插件配置"部分配置以下选项：

### 基础配置

- **EHENTAI_SITE**: 站点选择（`e` 或 `ex`）。`ex` 表示 exhentai.org，`e` 表示 e-hentai.org
- **EHENTAI_IPB_MEMBER_ID**: IPB Member ID
- **EHENTAI_IPB_PASS_HASH**: IPB Pass Hash
- **EHENTAI_IGNEOUS**: Igneous Cookie（使用 exhentai 时通常需要）
- **EHENTAI_CF_CLEARANCE**: Cloudflare 验证 Cookie

### 网络配置

- **EHENTAI_TIMEOUT**: 请求超时时间（秒）
- **EHENTAI_PROXY**: 代理地址（可选）

以下参数已固定为默认值，不再提供配置项：`ehentai_cookie`、`ehentai_http_backend`、`ehentai_http3`、`ehentai_impersonate`、`ehentai_curl_cffi_skip_on_error`。

### R2 云存储配置

如果启用 R2 上传，下载的文件可以上传到 Cloudflare R2 存储桶并生成公开链接：

- **EHENTAI_R2_ENABLED**: 启用 R2 上传
- **EHENTAI_R2_ACCESS_KEY_ID**: R2 Access Key ID
- **EHENTAI_R2_SECRET_ACCESS_KEY**: R2 Secret Access Key
- **EHENTAI_R2_BUCKET_NAME**: R2 Bucket 名称
- **EHENTAI_R2_ENDPOINT**: R2 Endpoint URL
- **EHENTAI_R2_PUBLIC_DOMAIN**: R2 公开访问域名

### D1 数据库配置

用于记录下载历史：

- **EHENTAI_D1_ENABLED**: 启用 D1 数据库
- **EHENTAI_D1_ACCOUNT_ID**: Cloudflare Account ID
- **EHENTAI_D1_DATABASE_ID**: D1 Database ID
- **EHENTAI_D1_API_TOKEN**: D1 API Token

## 使用示例

```
# 搜索关键词（默认返回 5 条）
/search 关键词

# 搜索关键词并查看第 2 页结果（序号将从 1-5 重新排列）
/search 关键词 --page 2

# 下载搜索结果中返回的列表第一本（也可以输入 2、3 等序号）
/download 1

# 直接根据关键词下载搜索结果的第一本（Resample 版本）
/download 关键词

# 下载原始版本（文件较大，支持 -original 和 --original）
/download -original 1

# 下面这种写法也会被识别
/download --original 1
```

## 注意事项

- 需要配置有效的 E-Hentai 登录 Cookie 才能下载
- 如果使用 exhentai 站点，通常需要配置 IGNEOUS Cookie
- R2 上传需要有效的 Cloudflare 凭证

## 常见问题

### Q: 搜索或下载失败？

A: 检查 Cookie 配置是否正确，以及网络连接是否正常。

### Q: 如何获取登录 Cookie？

A: 可以使用Cookie Editor获取三要素

### Q: R2 上传失败？

A: 确保 R2 凭证正确，并且 Bucket 名称和 Endpoint 配置无误。

## 版本

当前插件版本：v0.0.3

当前转换版本基于 nonebot-plugin-ehentai v0.0.1

# astrbot_plugin_ehentai
