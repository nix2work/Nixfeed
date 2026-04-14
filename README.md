# Nixfeed 🤖

每天自动抓取 AI 和 UX 领域资讯，生成中文摘要，推送到飞书群。支持通过飞书回复打分，自动筛选优质作者。

---

## 功能

- 每天北京时间 09:00 自动推送 8 条资讯（AI:4, UX:4）
- Gemini 优先生成中文摘要，失败自动切换 Claude 备用
- 自动去重，避免重复推送
- 支持飞书回复打分（格式：`1:5 2:3`），自动管理优质作者
- 支持飞书命令 `订阅 @username` 手动订阅作者

---

## 文件结构

```
Nixfeed/
├── bot/
│   ├── __init__.py
│   ├── ai_helper.py       # AI 摘要生成（Gemini + Claude 双备用）
│   ├── author_manager.py  # 作者评分与管理
│   ├── dedupe.py          # 去重逻辑
│   ├── feishu.py          # 飞书推送
│   ├── feishu_reader.py   # 飞书消息读取（打分/订阅）
│   ├── fetcher.py         # RSS 抓取与排序
│   ├── run.py             # 主程序入口
│   └── sources.py         # RSS 源配置
├── state/
│   ├── seen.json              # 已推送文章记录
│   ├── pending_articles.json  # 当天待打分文章
│   ├── author_scores.json     # 作者累计评分
│   └── curated_authors.json   # 优质作者列表
├── .github/workflows/
│   ├── digest.yml    # 每天推送任务
│   └── poll.yml      # 每小时读取飞书打分
├── requirements.txt
└── README.md
```

---

## 部署步骤

### 第一步：在 GitHub 上传文件

把所有文件按上方结构逐个创建到仓库中。

> ⚠️ `.github/workflows/` 路径要完整创建，否则 Actions 不会触发

### 第二步：配置 GitHub Secrets

进入仓库 → **Settings → Secrets and variables → Actions → New repository secret**，添加以下 Secrets：

| Secret 名称 | 说明 |
|---|---|
| `FEISHU_WEBHOOK_URL` | 飞书群机器人的 Webhook 地址 |
| `FEISHU_SECRET` | 飞书机器人签名密钥（开启加签时必填） |
| `FEISHU_APP_ID` | 飞书自建应用 App ID（打分功能用） |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_USER_OPEN_ID` | 你自己的飞书 Open ID |
| `GEMINI_API_KEY` | Gemini API Key（主力 AI） |
| `CLAUDE_API_KEY` | Claude API Key（备用 AI） |
| `CLAUDE_BASE_URL` | Claude 接口地址，填第三方平台地址 |

> 如果暂时不需要打分功能，`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_USER_OPEN_ID` 可以先不填，只填 Webhook 和 API Key 也能正常推送。

### 第三步：测试运行

1. 进入仓库 **Actions** 标签
2. 点击左侧 **AIxUX Digest to Feishu**
3. 点击右侧 **Run workflow**
4. 等待约 1-2 分钟，查看飞书群是否收到消息

---

## Secrets 说明

### 飞书 Webhook（推送用）

在飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 开启加签，拿到：
- Webhook URL → `FEISHU_WEBHOOK_URL`
- 签名密钥 → `FEISHU_SECRET`

### 飞书自建应用（打分读取用）

在 [open.feishu.cn](https://open.feishu.cn) 创建企业自建应用，开通 `im:message` 和 `im:message:readonly` 权限，拿到：
- App ID → `FEISHU_APP_ID`
- App Secret → `FEISHU_APP_SECRET`
- 自己的 Open ID → `FEISHU_USER_OPEN_ID`（开放平台 → 开发工具 → 用户身份查询）

### AI API

| 变量 | 说明 |
|---|---|
| `GEMINI_API_KEY` | 从 [Google AI Studio](https://aistudio.google.com) 获取 |
| `CLAUDE_API_KEY` | Claude API Key |
| `CLAUDE_BASE_URL` | 第三方平台地址，末尾不要加 `/` |

---

## 打分功能

当天推送消息后，在飞书群直接回复：

```
1:5 2:3 3:4
```

表示第 1 篇 5 分、第 2 篇 3 分、第 3 篇 4 分。bot 每小时轮询一次，自动更新作者评分。

- 平均分 ≥ 4.0 → 自动加入优质作者，后续优先展示
- 平均分 ≤ 2.0 → 自动屏蔽该作者

---

## 推送时间

默认北京时间 09:00（UTC 01:00），在 `.github/workflows/digest.yml` 修改：

```yaml
- cron: '0 1 * * *'   # UTC 01:00 = 北京 09:00
```

---

## 故障排查

**推送失败 / 没收到消息** → 检查 `FEISHU_WEBHOOK_URL` 是否正确，查看 Actions 日志

**AI 摘要是英文 / 为空** → 检查 `GEMINI_API_KEY` 和 `CLAUDE_API_KEY`，确认 `CLAUDE_BASE_URL` 末尾没有多余的 `/`

**推送条数不足 8 条** → 正常现象，近期新内容不够时 bot 会自动扩展时间范围

**打分不生效** → 检查飞书自建应用相关的三个 Secrets 是否配置，确认 `Poll Feishu Commands` 这个 Action 在运行
