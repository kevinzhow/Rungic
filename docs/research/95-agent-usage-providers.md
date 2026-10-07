# Agent 用量的通用接口与 Claude Code 数据来源（2026-09-30）

状态：**接口与 Codex 迁移已实现并通过离线测试和构建机集成测试，未部署、未在手机实机验收**；Claude Code 部分只实现可离线核验的读取器，其数据来源的调研结论单独列出。标注：“文档”指 Anthropic 官方文档或 changelog 中的说明；“未文档化”指只在第三方工具源码或本机文件中观察到；“源码”指核对过相应源码；“已验证”指本仓库测试或构建机实际运行过。

## 目标与现状

2026-09-30 的用量链（[主动建议记录](proactive-system-care.md)）只服务 Codex：`Care::Usage` 保存 Codex 快照，`view()` 直接输出 Codex 的 `authMode/rateLimits/accountUsage` 等字段，QML 直接读取这些字段。每增加一个 Agent 都要同时修改服务和组件。本次把接口改为“提供方”模型：服务只理解统一的提供方对象，每个 Agent 通过描述文件声明数据来源，服务和组件不需要为新 Agent 改代码。

## 数据契约：`AgentUsage()` schema 2（已实现）

```json
{"schema": 2, "primary": "codex", "updatedAt": 1790743308,
 "providers": [
  {"id": "codex", "name": "Codex", "vendor": "OpenAI",
   "icon": {"light": "/usr/share/rungic/agent-usage/icons/codex-light.svg",
            "dark": "/usr/share/rungic/agent-usage/icons/codex-dark.svg"},
   "status": "working|ready|offline|connecting|not-installed|signed-out|error",
   "installed": true,
   "account": {"kind": "subscription|api-key|none", "label": "ChatGPT", "plan": "plus"},
   "model": "gpt-…",
   "tokens": {"device": 700, "today": 700, "account": null},
   "limits": [{"id": "codex.primary", "label": "codex", "windowMinutes": 300, "usedPercent": 3.0,
               "resetsAt": 1790750000, "expired": false}],
   "updatedAt": 1790743308, "stale": false, "error": ""}]}
```

- `id/name/vendor/icon` 来自描述文件，适配器返回的同名字段会被忽略。`icon` 是该 Agent 自己的标志文件路径（只传路径，不传内容）：`light` 必需、`dark` 可选；文件须为绝对路径、存在、可读、不超过 1 MiB 的 SVG 或 PNG，否则整个 `icon` 为 `null`（只有 `dark` 无效时只去掉 `dark`），Agent 本身照常显示，组件退回字母图块。`updatedAt/stale/expired` 和本机账本由服务计算。
- `installed`：独立安装检查的布尔值；无法确认时为 `null`。Codex 缺少用户目录中的独立二进制时是 `false`，状态为 `not-installed`；系统启动脚本存在不代表 Codex 已安装。用量传输失败时清为 `null`，不能根据连接失败猜测未安装或未登录。其他提供方未报告此字段时输出 `null`。
- `tokens.device/today`：本机为该提供方**当前账户**记录的累计值与当日值。账本以“提供方 + 不可逆账户指纹”分区，沿用原高水位逻辑：会话累计值只计增长，首次见到的恢复会话只计最后一次请求（`last`）。自带账本的适配器（Claude Code 读取器）可以直接给出这两个值，服务原样使用。账户未经本次会话确认前显示 `null`。
- `tokens.account`：提供方报告的账户总量（Codex `account/usage/read` 的 `summary.lifetimeTokens`），未知为 `null`。
- `primary`：正在 `working` 的提供方；否则最近活跃的（本机账本最后记账时间、最近一次 `working` 或适配器的 `lastActive` 提示）；否则排序第一个；没有提供方时为空字符串。
- 排序：描述文件的 `order`（默认 100），再按 `id`。
- 不在契约里的字段一律丢弃（包括 `accountKey`、邮箱、未知状态值、额度窗口里的附加键）；字符串有长度上限，额度最多 16 个窗口。
- 部分读取（适配器返回 `error` 且缺少 `limits` 或 `tokens.account`）保留上次完整读取的值，并标记 `stale`。
- 客户端 `UsageClient` 增加 `providers` 与 `primary` 两个属性，`data` 仍是完整回复。服务断开时保留上次的提供方及用量，并全部标记 `stale`，将状态改为 `offline`、安装状态清为 `null`，另设 `data.error`。旧的“未安装”结果不能当作本次确认。

### 适配器可额外提供的输入字段（不会出现在输出里）

- `accountKey`：不可逆账户指纹，用于账本分区和账户切换检测。
- `tokenEvents`：`[{accountKey, session, turn, total, last}]`，服务重启后补回适配器缓存的累计事件。
- `lastActive`：epoch 秒，用于 `primary` 选择。
- `available: false`：该 Agent 在本机未使用，隐藏这个提供方。

## 扩展机制：描述文件（已实现）

位置：`XDG_DATA_DIRS` 下的 `rungic/agent-usage/providers/<id>.json`（系统 `/usr/share/…`，由拥有该适配器的包安装），以及用户的 `~/.local/share/rungic/agent-usage/providers/`。低优先级目录先读，用户目录最后读；同一 `id` 以后读到的为准。服务每 30 秒最多重新扫描一次，安装或删除适配器不需要重启服务。无效文件跳过，并在内容变化时写一次警告日志。测试可用 `RUNGIC_AGENT_USAGE_PROVIDERS=目录1:目录2` 覆盖。

```json
{"schema": 1, "id": "codex", "name": "Codex", "vendor": "OpenAI", "order": 10,
 "dbus": {"service": "com.rungic.VoiceAgent", "path": "/com/rungic/VoiceAgent",
          "interface": "com.rungic.VoiceAgent", "method": "Usage"},
 "timeoutSeconds": 35}
```

```json
{"schema": 1, "id": "claude-code", "name": "Claude Code", "vendor": "Anthropic", "order": 20,
 "optional": true, "command": ["/usr/libexec/rungic-agent-usage-claude-code", "--json"],
 "timeoutSeconds": 20}
```

规则：`schema` 为 1；`id` 匹配 `[a-z0-9][a-z0-9._-]{0,63}` 且文件名必须是 `<id>.json`；`name` 1–64 字符；来源**二选一**：
- `dbus`：服务调用该方法（无参数，返回 JSON 字符串），**不自动激活**对方服务：对方不在总线上时显示 `offline`，不会为此启动一个 Agent。默认超时 35 秒。
- `command`：绝对路径开头的参数数组，不经 shell 执行，标准输入为空，标准错误丢弃，输出上限 1 MiB，默认超时 10 秒，超时即结束进程。

可选：`timeoutSeconds`（1–60）、`order`（0–1000）、`optional`（适配器报告可用之前不显示）、`icon`（`{"light": "<SVG/PNG 绝对路径>", "dark": "<可选>"}`，在每次加载描述文件时核对文件是否存在）。未知键忽略，以便以后扩展。`rungic-suggestions --validate-usage-providers 文件…` 用于构建时检查，`rungic-suggestions` 包的 build.sh 已检查自带描述文件。

读取节奏：只有在最近 3 分钟内有客户端调用 `AgentUsage`（组件或用量页可见）时才读取来源，每个提供方最多 30 秒一次；提供方通过 `ProviderChanged` 表示状态已变化时，若有人在看则在 2 秒节流后立即重读，否则标记待读，下次有人查看时优先读取。D-Bus 来源的所有者从总线上消失时立即标为 `offline`，重新出现时重读。

## D-Bus 接口 `com.rungic.Suggestions`（已实现）

| 成员 | 说明 |
|---|---|
| `AgentUsage() → s` | schema 2 JSON；同时触发按上述节奏的读取 |
| `RefreshAgentUsage()` | 只触发读取 |
| `RecordTokens(s provider, s json)` | 推送会话累计 token：`{"accountKey","session","turn","total","last"}`；未声明的提供方忽略；只增长时发 `UsageChanged` |
| `ProviderChanged(s provider)` | 适配器状态、账户或额度变化，请求重读 |
| `UsageChanged` 信号 | 数据变化，客户端重新调用 `AgentUsage` |

命令行：`rungic-suggestions usage` 打印当前 `AgentUsage`。

安全边界：同一会话总线上的进程都可以调用 `RecordTokens`，与原先监听 VoiceAgent 信号的信任范围一致（同一用户）；服务只接受已声明的提供方，计数单调，不接受负数或非整数。

## Codex 的接法（已实现）

- 描述文件 `plasma/voice-agent/agent-usage/codex.json` 由 `rungic-voice-agent` 安装到 `/usr/share/rungic/agent-usage/providers/`。
- VoiceAgent 的 `Usage` 方法直接返回提供方对象：`chatgpt` → `account.kind: subscription`、`label: ChatGPT`；`apiKey` → `api-key`、`API Key`；未登录 → `none` 且 `status: signed-out`；独立二进制缺失 → `installed: false` 且 `not-installed`，不读取账户或模型；二进制存在但未连接 → `installed: true` 且 `offline`；安装检查不可读 → `installed: null`；`agent_busy` → `working`。额度只对订阅账户读取，`rateLimitsByLimitId`（或单一 `rateLimits`）的 primary/secondary 窗口映射为 `limits`（`id = <limitId>.<primary|secondary>`，`label = limitName`），`windowDurationMins → windowMinutes`。读取失败时返回已本地化的 `error`（原 gettext 条目“Account usage has not updated yet”），不伪造额度。
- Token：`thread/tokenUsage/updated` 转换为 `{accountKey, session: threadId, turn: turnId, total, last}`，通过 `RecordTokens("codex", …)` 推送，并缓存在 `tokenEvents` 中供服务重启后补回。账户仍以回合开始时的账户为准。
- `turn/started`、`turn/completed`、`account/updated`、`account/rateLimits/updated`、`account/login/completed` 调用 `ProviderChanged("codex")`。
- 兼容：服务仍把旧 VoiceAgent 的 `token-usage`、`usage-changed`、`agent-started/finished/restarted`、`account` 事件转换为 `codex` 的记账或重读，**保留一个发布周期**（代码注释写明 2026-10 后删除）。新 VoiceAgent 一旦通过 D-Bus 调用过 `RecordTokens`/`ProviderChanged`，服务就不再转换它的旧事件，避免每个回合重复读取；即使重复送达，计数是高水位，也不会多记。旧 VoiceAgent 的 `Usage` 回复格式不再解析：`rungic-voice-agent` 的依赖提高到 `rungic-suggestions (>= 0.473)`，新 VoiceAgent 只和新服务搭配；旧 VoiceAgent 搭配新服务时只保留记账，状态显示不完整，升级两包即可恢复。
- 账本文件仍为 `~/.local/share/rungic-suggestions/agent-usage.json`（0600），格式升为 schema 2 `{"providers": {"codex": {"accounts": …}}}`；schema 1 读入时迁移到 `codex`，每个会话记录的键不变，所以迁移后不重复计数（测试覆盖）。

## 各 Agent 的标志（已实现）

用户要求每个 Agent 显示自己的标志，而不是共用 Rungic 图像。来源与校验记录在 `provenance/agent-usage-icons-20260930/sources.json`，测试逐一核对哈希：

- Claude Code：Anthropic 新闻资料包中的 Claude Spark（Clay 色）`Anthropic media resources/Anthropic logos/Claude logos/3 Claude Spark/SVG/Claude Spark - Clay.svg`，2026-09-30 从 <https://www.anthropic.com/press-kit>（实际为 `www-cdn.anthropic.com/ae59ca4c….zip`）下载，sha256 `6d53db4b…e219`，**未修改**。这是 Anthropic 的商标，不属于本仓库的许可证，只用于标明所显示的是 Claude Code 的用量（指称性使用）。随 `rungic-suggestions` 安装到 `/usr/share/rungic/agent-usage/icons/claude-code.svg`；只有一个颜色，暗色主题同样使用它。交接时附带的哈希（`0b272d36…`）与资料包内文件不符，以资料包文件为准。
- Codex：按用户要求不使用 OpenAI 的品牌文件（openai.com 对脚本下载返回 403，也不从第三方图标站获取），改用本项目原创的 10×10 像素标志（圆角方块上的终端提示符 `>_`，GPL-2.0-or-later），亮/暗两色，随 `rungic-voice-agent` 安装为 `codex-light.svg`、`codex-dark.svg`。

## Claude Code 可用的用量来源（调研，2026-09-30）

调研在 K8-Plus（x86_64，直连，无系统代理）进行。来源：官方文档 `code.claude.com/docs/en/*`、全文包 `code.claude.com/docs/llms-full.txt`、`platform.claude.com/docs/en/*`；Claude Code changelog（`anthropics/claude-code` main，首条 2.1.285，条目不带日期）；第三方源码用 `gh api` 读取固定提交。

### 1. 会话记录 JSONL

- 文档：`~/.claude/projects/<项目>/<session-id>.jsonl`，`<项目>` 是工作目录中非字母数字字符换成 `-`；子代理记录在 `<项目>/<session>/subagents/`；被替换的副本命名为 `*.orphaned-<ts>-<suffix>.jsonl`、`*.jsonl.superseded-<ts>`（[sessions](https://code.claude.com/docs/en/sessions)、[claude-directory](https://code.claude.com/docs/en/claude-directory)）。`CLAUDE_CONFIG_DIR` 是替换 `~/.claude` 的**单个**目录（[env-vars](https://code.claude.com/docs/en/env-vars)）。保留期 `cleanupPeriodDays` 默认 30 天（[settings-reference](https://code.claude.com/docs/en/settings-reference)）。
- **文档明确说明记录格式是内部格式、随版本变化，直接解析的脚本可能在任何版本失效**，推荐 `/export`、`claude -p --output-format json`、hooks/statusline 提供的 `transcript_path` 或 Agent SDK。监控文档只把 `message.uuid`、`requestId`、`tool_use_id` 列为关联键。
- 未文档化（本机 2.1.261+ 记录的键名）：assistant 行有 `type/timestamp`（ISO-8601 UTC，含毫秒）`/sessionId/requestId/uuid/isSidechain/isApiErrorMessage/version/agentId/message`；`message.id`（`msg_…`）、`message.model`（可为 `<synthetic>`）、`message.usage.{input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, cache_creation.{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}, server_tool_use, service_tier, …}`。当前记录已不含 `costUSD`。
- ccusage（MIT，仓库已迁到 `ccusage/ccusage`，Rust 重写，v20.0.26 / 2026-09-27，读取提交 `a427446b6e`）：路径 `claude_paths()` 在设置 `CLAUDE_CONFIG_DIR` 时按逗号拆分（其自有约定），否则同时读 `$XDG_CONFIG_HOME/claude` 与 `~/.claude`；只解析含 `"usage":{` 的行；去重键 `usage_dedupe_hash()` = `message.id` + `requestId`（缺 `requestId` 时加 `sessionId` 与时间）；总量 = input + output + 缓存写入 + 缓存读取（有 `cache_creation` 对象时缓存写入取 5m + 1h）；日期按本地或 `--timezone` 时区，不是 UTC。
- Claude-Code-Usage-Monitor（MIT，v4.0.0 / 2026-06-27，读取提交 `c59a83bf94`）：只读 `~/.claude/projects`，`message_id:request_id` 两者都存在时才去重。

### 2. statusline 输入（文档）

[statusline](https://code.claude.com/docs/en/statusline#available-data)：changelog **2.1.80** 加入 `rate_limits`：`rate_limits.five_hour` / `seven_day` 的 `used_percentage`（0–100）与 `resets_at`（Unix epoch 秒）。只对 claude.ai Pro/Max 订阅（或设置了花费上限的 Claude apps gateway）出现，且在会话的第一次 API 响应之后才出现；两个窗口可分别缺失，窗口的 `resets_at` 过后 Claude Code 会去掉它。2.1.243 修正了重置前的旧百分比；已关闭的 [claude-code#52326](https://github.com/anthropics/claude-code/issues/52326) 曾把重置时间写进 `used_percentage`。其他字段：`session_id`、`transcript_path`、`version`、`model.id/display_name`、`cost.total_cost_usd`（客户端按标价估算，2.1.211 起 `/clear` 后清零）、`context_window.*` 等。

### 3. `/usage` 与额度来源

- 文档：`/usage` 显示会话花费、套餐额度和活动统计，2.1.118 起 `/cost`、`/stats` 为其别名；changelog 只写“plan-usage endpoint”，未公布地址。
- **未文档化**：`GET https://api.anthropic.com/api/oauth/usage`，需要 OAuth 访问令牌和 `anthropic-beta: oauth-2025-04-20`。官方全文包无此地址。第三方使用者：Claude-Code-Usage-Monitor `output/api_usage.py`（自述“undocumented”，需手动开启，令牌取自 `CLAUDE_CODE_OAUTH_TOKEN` 或 `~/.claude/.credentials.json`）、ccstatusline `src/utils/usage-fetch.ts`、CodexBar `ClaudeOAuthUsageFetcher.swift`（v0.69.0）。观察到的返回形态：`five_hour/seven_day/seven_day_sonnet/seven_day_opus` 的 `{utilization, resets_at(ISO)}`、较新的 `limits[]`、`extra_usage`；有 429 限流；`utilization` 的单位第三方工具两种都兼容。
- 响应头 `anthropic-ratelimit-unified-*` 只在 [Claude apps gateway 花费上限](https://code.claude.com/docs/en/claude-apps-gateway-spend-limits) 页被提及，具体名称（`-5h-utilization` 等）只见于第三方。公开 API 的[速率限制页](https://platform.claude.com/docs/en/api/rate-limits)只列 `anthropic-ratelimit-{requests,tokens,…}-{limit,remaining,reset}`。

### 4. OpenTelemetry（文档）

[monitoring-usage](https://code.claude.com/docs/en/monitoring-usage)：`CLAUDE_CODE_ENABLE_TELEMETRY=1`，指标 `claude_code.token.usage`（`type` = input/output/cacheRead/cacheCreation，带 `model`、`session.id`、`user.account_uuid` 等属性）、`claude_code.cost.usage`；事件 `claude_code.api_request` 含逐请求 token 与 `request_id`。导出器 otlp/prometheus/console，默认每 60 秒。**没有额度或配额指标**。

### 5. API Key 的组织用量（文档）

[Usage & Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)：`GET /v1/organizations/usage_report/messages`（`bucket_width` 1m/1h/1d）与 `/v1/organizations/cost_report`（仅按天），需 Admin Key（`sk-ant-admin01-…`），**个人账户不可用**，约 5 分钟延迟。[Claude Code Analytics API](https://platform.claude.com/docs/en/manage-claude/claude-code-analytics-api)：`/v1/organizations/usage_report/claude_code`，按 UTC 日、最多 1 小时延迟。

### 6. 本地账户身份

文档只说明 `~/.claude/.credentials.json`（0600，Linux）保存凭据、`~/.claude.json` 保存应用状态。`oauthAccount` 的键（`accountUuid`、`organizationType`、`billingType` 等）和凭据中的 `subscriptionType/rateLimitTier` **未文档化**；Agent SDK 的 `accountInfo()` 文档化了 `subscriptionType`，但需要运行 SDK。

### 选型

| 来源 | 性质 | 采用 |
|---|---|---|
| statusline `rate_limits` | 文档，2.1.80+，推送式，需要用户配置 statusLine | **采用**，作为唯一额度来源 |
| 会话记录 JSONL 的 `message.usage` | 位置有文档，**行格式明示为内部格式** | **采用 token 计数**，按 ccusage 的去重与计算方式，读取宽松、失败不计，自有账本 |
| OAuth `/api/oauth/usage` | 未文档化，需要读取凭据中的访问令牌 | **不采用**；列为下一步候选（需用户明确同意读取凭据，并接受接口随时变化） |
| `~/.claude.json` / `.credentials.json` 账户字段 | 未文档化，后者含密钥 | 不读取 |
| OpenTelemetry | 文档，但需要用户开启并运行收集端 | 暂不采用；若以后需要逐请求数据可作为推送式适配器 |
| Admin Usage/Cost API | 文档，需组织 Admin Key，个人账户不可用 | 不采用 |

不直接复用 ccusage 或 Claude-Code-Usage-Monitor：前者是 Rust/Node CLI，后者是 Python 终端界面，都不适合常驻桌面按需调用；只借鉴其去重键和计数方式（MIT，未复制代码）。

## Claude Code 读取器（已实现，离线验证）

`rungic-agent-usage-claude-code`（C++，随 `rungic-suggestions` 安装到 `/usr/libexec/`，描述文件 `claude-code.json`，`optional: true`）：

- `--json`：
  - 读取 `CLAUDE_CONFIG_DIR`（按 ccusage 约定也接受逗号分隔），否则 `~/.claude` 与 `~/.config/claude` 下 `projects/**/*.jsonl`（含 `subagents/`），规范路径去重。
  - 每个文件记住已读偏移，只读新增的完整行；文件变短视为重写，从头读（消息去重保证不重复计数）。只解析含 `"usage"` 的行；无效行、`<synthetic>`（token 为 0）跳过。
  - 按 `message.id|requestId` 指纹去重（缺失时退回到会话、时间和 uuid），同一消息取较大的 token 数；总量 = input + output + 缓存写入 + 缓存读取；日期按本地时区归日。
  - 账本 `~/.local/share/rungic/agent-usage/claude-code-ledger.json`（0600）保存偏移和每条消息的指纹、日期、token，所以 Claude Code 按保留期清理记录后，本机累计不减少。每次最多读 8 秒，未读完时返回“仍在读取历史”的本地化错误，下次继续。
  - 输出 `tokens.device/today`、最新消息的 `model`、`lastActive`；最新响应在 90 秒内时 `status: working`（推断，长时间工具调用期间可能显示 ready）。
  - 本机没有 Claude Code 的目录也没有 statusline 记录时输出 `{"available": false}`，服务不显示该提供方。
- `--statusline`：作为（或在用户自己的）statusLine 命令中调用，从标准输入读取文档化的 JSON，只保留 `version`、`model.id/display_name`、`rate_limits` 的两个窗口（`used_percentage` 超出 0–100 的丢弃），写入 `claude-code-statusline.json`（0600），输出一行“`Opus · 5h 24% · 7d 41%`”。不保存 `transcript_path`、`cwd`、花费等。新会话在第一次响应前没有 `rate_limits` 时保留上次的窗口；Claude Code 去掉的窗口（已重置）同步去掉。`--json` 只使用 1 小时内的额度读数，有读数时 `account.kind: subscription`、`label: Claude`（文档：`rate_limits` 只对 Pro/Max 出现）。
- 用户配置示例（`~/.claude/settings.json`）：`{"statusLine": {"type": "command", "command": "/usr/libexec/rungic-agent-usage-claude-code --statusline"}}`；已有自己的 statusline 脚本时，在脚本里把同一份标准输入再交给该命令并丢弃其输出。
- 已知缺口：没有 statusline 读数时无法区分订阅和 API Key，`account.kind` 为 `none`（现有 QML 会显示“未登录”，需组件重写时处理或在契约中增加 `unknown`）；token 只涵盖本机记录，不含其他设备和 claude.ai；记录格式变化时计数可能停止增长，但不会伪造数据。

## 测试与构建（已验证）

- Python：`PYTHONPYCACHEPREFIX=.work/cache/python .work/venv/bin/python3 -m pytest -q tools/tests plasma/voice-agent`（等同于 `source tools/work-env.sh` 后运行）→ **51 passed、18 subtests passed**。`test_agent_usage.py` 覆盖 API Key 不请求订阅额度、订阅窗口映射、读取失败不伪造、signed-out/offline/working、账户切换丢弃旧回复、后台线程 token 推送、迟到事件保持回合开始时的账户、描述文件与 D-Bus 接口一致、描述文件的标志就是各包安装的文件且哈希与来源记录一致。
- C++（Mac mini 构建容器，Ubuntu 26.04 aarch64，Qt 6.10.2）：`python3 tools/rungic_package.py build rungic-suggestions rungic-voice-agent`，最终提交两个包均以 `0.475` 构建成功（包内含读取器、两个描述文件和三个标志文件，已用 `dpkg-deb -c` 核对）；`care-tests` 另在构建容器的独立临时目录重新编译运行；`care-tests` **34 passed**（含 QtTest 的初始化/清理；改写原 2 项用量测试并新增 10 项：标志路径校验、描述文件的系统/用户目录与无效文件、多提供方视图与排序、primary 选择、按提供方的账户隔离、按提供方的 stale/error/offline、丢弃未知字段、schema 1 迁移与旧事件转换、适配器自带 token、Claude Code 记录去重/增量/半行/清理后保留/时间预算续读、statusline 额度与过期）。
- 集成（同一构建容器，`dbus-run-session` 私有总线，未碰手机）：真实 `rungic-suggestions --service` 加载 5 个描述文件（1 个无效被跳过），第一次 `usage` 全部为 `connecting`；数秒后 Codex 因 VoiceAgent 不在总线显示 `offline`，Claude Code 读取器从测试记录给出 `device 1168 / today 1115`，命令型替身的邮箱等字段被丢弃并成为 `working` 的 primary，`RecordTokens` 记账 900，2 秒超时的读取器显示本地化错误。加入标志后在独立临时目录重跑：Claude Code 的 `icon.light` 指向存在的 SVG，Codex 描述文件引用的图标在该容器中不存在，`icon` 为 `null`，提供方照常显示。测试服务均已结束，临时目录已删除。
- 未验证：手机上的部署、真实 Codex 订阅账户、真实 Claude Code 使用（手机未安装 Claude Code，也不安装）；zh_CN 新增条目只经构建时 `ki18n_install` 编译。

## 实机验收办法（待做）

1. 安装两个新包后 `rungic-suggestions usage`：Codex 为 `ready/working`，账户类型与原先一致，本机 token 与升级前相同（schema 1 迁移不重复计数）。
2. 发起一次 Codex 回合：组件状态变为 working，结束后 `tokens.device` 增加且重启建议服务后不重复。
3. 停止 VoiceAgent：Codex 变为 `offline`，恢复后自动重读。
4. 放入一个用户描述文件（例如命令型替身），30 秒内出现在列表中，删除后消失。
5. 未安装 Claude Code 时列表中不出现 Claude Code。

## 剩余工作

- 桌面组件和用量页的多提供方界面由组件重写完成；本次只把现有 QML 最小改为读取 `primary`。
- 删除旧 VoiceAgent 事件的兼容转换（下一发布）。
- Claude Code 额度的非 statusline 来源（OAuth usage 端点）需另行决定；Agent SDK `accountInfo()` 可作为文档化的订阅类型来源，但需要常驻 SDK 进程。
