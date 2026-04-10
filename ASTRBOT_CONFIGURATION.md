# AstrBot E-Hentai/ExHentai 插件配置检查清单

## ✅ 必须配置的项目

### 对于 E-Hentai（免费搜索）

```
ehentai_site = "e"
ehentai_base_url = "https://e-hentai.org"
ehentai_cloudflare_worker_url = "https://eh.shirasuazusa.workers.dev/"
```

运行：`/search english`

---

### 对于 ExHentai（需要 Cookie）

```
ehentai_site = "ex"
ehentai_base_url = "https://exhentai.org"
ehentai_cloudflare_worker_url = "https://eh.shirasuazusa.workers.dev/"
ehentai_cookie = "igneous=XXX; ipb_member_id=XXX; ipb_pass_hash=XXX; sk=XXX; hath_perks=XXX"
```

运行：`/search 鈍色玄`

---

## 🔍 诊断方法

### 1. 检查 Worker URL 是否配置

如果看到日志：
```
[搜索] 使用标准 DNS 模式
```

说明 **Worker URL 未配置**。需要在 AstrBot WebUI 中填入：
```
ehentai_cloudflare_worker_url
```

### 2. 检查 Worker URL 是否正确

正确配置后，日志应该显示：
```
[搜索] 使用 Cloudflare Worker 搜索: https://eh.shirasuazusa.workers.dev/
[搜索] Worker 搜索成功，获得 X 条结果
```

### 3. ExHentai Cookie 检查

Cookie 应该包含以下必要字段：
- `ipb_member_id` - 用户 ID
- `ipb_pass_hash` - 密码哈希
- `igneous` - ExHentai 访问令牌
- `sk` - 可选，会话密钥

---

## 📋 AstrBot WebUI 配置步骤

1. 打开 AstrBot 控制台 → 插件管理
2. 找到 "E-Hentai 搜索"
3. 编辑配置，填入以下内容：

```
基础配置:
  - ehentai_site: "e" 或 "ex"
  - ehentai_base_url: "https://e-hentai.org" 或 "https://exhentai.org"

Worker 配置 ⭐ 必填：
  - ehentai_cloudflare_worker_url: "https://eh.shirasuazusa.workers.dev/"

Cookie 配置（ExHentai 必填）:
  - ehentai_cookie: "[从浏览器复制的完整 Cookie]"

其他设置:
  - 根据需要调整其他参数
```

4. 点击 "保存" 后重启插件

5. 测试搜索：
   ```
   /search english
   ```

---

## ✨ 验证成功标志

当 plugin_worker 配置正确时：

- ✅ 日志显示 `[搜索] 使用 Cloudflare Worker 搜索`
- ✅ E-Hentai 搜索返回 25+ 结果
- ✅ ExHentai 搜索返回结果
- ✅ 标题显示正常（无 HTML 实体）
- ✅ 搜索速度快（通过 Cloudflare Edge）

---

## ❌ 常见问题

### 问题 1: 看到 "使用标准 DNS 模式"
**原因**: Worker URL 未配置  
**解决**: 在 AstrBot WebUI → 插件配置 中填入 `ehentai_cloudflare_worker_url`

### 问题 2: Worker 返回错误
**原因**: ExHentai Cookie 过期或无效  
**解决**: 
1. 访问 https://exhentai.org/
2. 登录账户
3. F12 → Network → 复制 Cookie 头
4. 更新 AstrBot 配置中的 `ehentai_cookie`

### 问题 3: ExHentai 搜索无结果
**原因**: Cookie 可能缺少必要字段  
**解决**: 确保 Cookie 包含 `igneous`, `ipb_member_id`, `ipb_pass_hash`

---

## 🚀 推荐配置

### 最小化配置（仅用 E-Hentai）
```
ehentai_site = "e"
ehentai_cloudflare_worker_url = "https://eh.shirasuazusa.workers.dev/"
```

### 完整配置（E-Hentai + ExHentai）
```
# E-Hentai 配置
ehentai_site = "e"
ehentai_cloudflare_worker_url = "https://eh.shirasuazusa.workers.dev/"

# ExHentai 配置（备用）
ehentai_cookie = "[您的 ExHentai Cookie]"
```

然后切换站点：
- E-Hentai: `/search english`
- ExHentai: 暂时配置 `ehentai_site = "ex"` 再搜索

---

## 📞 需要帮助？

检查这些事项：
1. ✓ Worker URL 已填写
2. ✓ 配置已保存
3. ✓ 插件已重启
4. ✓ 日志显示正确的 Worker URL

如果仍有问题，检查 AstrBot 日志中的错误信息。
