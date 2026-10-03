# Codex 登录与 OpenAI API Key：两套凭据分开讲清楚（2026-10-01）

- **问题**：用户指出设置里 Codex 的部分含糊不清：用 ChatGPT 账户登录时，“登录方式”点进去却是 API Key 页，容易让人以为所有请求都走 API Key。
- **文案错误**：API Key 页写着“Codex uses this key to call OpenAI”，这个说法也不对。这把 key 实际用在 Codex 登录覆盖不到的地方。
- **要求**：把两者分开讲清楚。ChatGPT 登录分不出是 device code 还是浏览器方式，直接按套餐称呼。

标注：“实测”指在 G100 S 上查看过；“源码”指核对过 openai/codex `rust-v0.159.2`。

## 当前功能（2026-10-03）

默认 Codex 桌面操作使用 Agent 所选模型和已有登录；Luna API 是独立执行方式，在「设置 → 桌面操作」选择。「登录方式」只负责 Codex 的认证，两项独立，切换执行器不切换账户。默认模式的普通桌面操作不需要额外的 OpenAI API key；语音、转写、TTS 和显式 Luna API 仍需要它。

通话的拨号、挂断及画面判断也已接入 Codex 默认路径，通话音频对话与调度仍走独立 API。开发 G100 的默认桌面和 API 只读验收已通过，完整电话、语音消息未在本轮验收，详见 [106 篇](106-codex-desktop-operation.md)。本轮 G100 保持 API key 登录；下文 G100 S 的登录验收是 2026-10-01 的独立记录。

## 实际上是两套凭据

- **Codex 的登录**（`account/read`：`chatgpt`、`apiKey` 或 `amazonBedrock`）：
  - 用于 Agent 执行任务、默认桌面操作、系统建议整理、读取模型列表；
  - ChatGPT 登录计入套餐（`planType`，比如 team），API Key 登录按 API 用量计费。
- **我们单独保存的 OpenAI API Key**（`~/.config/rungic-voice-agent/openai-api-key`）：
  - **实时语音**：走 Codex 的 `thread/realtime/start`，但 Codex 用 WebSocket 连接实时语音时只接受 API Key（源码：`core/src/realtime_conversation.rs` 的 `realtime_api_key()`）。ChatGPT 登录时，它退回读取环境变量 `OPENAI_API_KEY`，也就是服务传给 app-server 的这把 key。源码里有 TODO 说明这是暂时的；WebRTC 方式不带 API Key，是否能用 ChatGPT 登录还没有验证；
  - **按住说话后转文字**、**通话音频对话与调度**、**语音合成**：由本地适配器直接调用 API；
  - **Luna 桌面执行及通话画面判断**：仅在显式 API 模式直接调用 API；默认 Codex 模式沿用 Codex 的登录。
- **device code 分辨不出来**（实测）：`account/read` 只返回类型、套餐和邮箱；`~/.codex/auth.json` 只记 `auth_mode` 和凭据，不记登录方式。两种方式登录的是同一个账户，计费也一样，所以界面按套餐称呼。

## 实测中发现的问题

- **用户的 Codex 已被切到 API Key 登录**：2026-10-01 00:31:46 进入 API Key 页，00:32:39 触发登录请求，00:32:41 `auth.json` 被改写成 `apikey`。
  - 旧 API Key 页上的“让 Codex 改用 API Key 登录”只在 ChatGPT 登录时出现，点一下就立即切换，没有确认，也不提示计费会变。
  - 之后 `account/read` 返回 `apiKey`，用量面板显示 API Key、没有套餐额度。用户将在新的登录页里自己登录回 ChatGPT。
- **切换登录会让服务整个退出**：`restart_server` 停掉旧的 app-server 后，读取它输出的线程执行 `os._exit(1)`，服务随之退出，靠 systemd 重新拉起。这次连续发生了两次（NRestarts=2）。

## 修改

- **设置首页**：
  - Codex 组：Codex、模型、**登录方式**（值为“ChatGPT 套餐（Team）”、“API Key”或“未登录”）、Agent 用量；
  - 语音组：新增 **OpenAI API Key** 一行，副标题“语音对话、转文字和代打电话”。
- **新的 Codex 登录页 `AccountPage.qml`**：
  - 状态写成显式的几种：加载中、未登录、chatgpt、apiKey，以及“正在登录”（显示验证码）和“确认改用 API Key”；
  - “当前”一栏显示登录方式和邮箱，下方用一句话说明计费归属；
  - 选择登录方式：ChatGPT 账户（device code 登录）或 API Key。选 API Key 要先确认，写明“Agent 的任务将按 OpenAI API 用量计费，不再计入 ChatGPT 套餐”；没设置 key 时这一项不可选；
  - 底部有一行链接到 OpenAI API Key 页。
- **API Key 页 `KeyPage.qml`**：
  - 说明改为：用于 Codex 登录覆盖不到的地方，即语音对话（Codex 连接实时语音时只接受 API Key）、转文字和代打电话；按 OpenAI API 用量计费，与 ChatGPT 套餐分开；
  - 删掉原来切换 Codex 登录的两行和验证码区域，改为一行“Codex 登录方式”，链接到新的登录页。
- **Codex 页**：“登录方式”一行显示套餐名和计费说明，点进去是新的登录页。
- **`account.js`**：三处页面共用的说法，包括套餐名、登录方式和计费归属。
- **服务**：`restart_server` 在停掉旧 app-server 之前，先把它标记为 `retired`。被主动替换的 app-server 退出时，读取线程只记日志；只有意外死掉时，服务才退出，交给 systemd 恢复。
- **翻译**：删掉 9 条不再使用的词条，新增 23 条。

## device code 登录显示“Login was not completed”后再也拿不到验证码（2026-10-01）

- **用户反馈**：点“ChatGPT 账户”后验证码出现了一下；没输入，过了一会儿页面显示 “Login was not completed”；之后怎么点都不再出现验证码。
- **原因**（源码：`app-server/src/request_processors/account_processor.rs`）：
  - 每次 `account/login/start`（chatgptDeviceCode）都会顶替进行中的登录；被顶替的那次以 `Login was not completed` 结束（`cancel.cancelled()` 那一支）。超时报的是别的错误，所以这条消息只意味着“被新的登录请求顶替了，或被取消了”；
  - 页面不检查 `account/login/completed` 属于哪一次登录：新的验证码先回来、显示出来，随后上一次登录的“未完成”通知到达，直接覆盖了它。所以之后每点一次，都是新码一闪，随即被旧的作废消息盖掉；
  - 第一次被顶替，最可能是那一行被连点，或触摸被识别成了两次。服务当时不记录登录请求，没法从日志确认。
- **修复**：
  - **服务**：记住进行中的 device code 登录（`loginId`、验证码、开始时间）。15 分钟有效期内（`login/src/device_code_auth.rs`，留出 1 分钟余量）再点，返回同一个验证码，不再让 Codex 顶替；
  - `login_completed()` 核对 `loginId`，已被顶替的那次登录结束时只记日志；
  - 新增 `CancelCodexLogin`（`account/login/cancel`）；
  - `Setup` 带上进行中的登录；
  - 登录的开始、复用、结束和被顶替都记日志。
  - **界面**：登录页按 `loginId` 核对通知，“取消”真正调用取消，重新打开页面时恢复进行中的验证码。
- **离线核对**：`tools/tests/test_codex_login.py` 覆盖了：15 分钟内连点返回同一个码且只向 Codex 申请一次；过期后重新申请；被顶替的结束不发给界面；当前这次的成功或失败会发出，成功后重启 app-server；取消会调用 Codex；改用 API Key 时丢弃进行中的 device code。

## 离线核对

- `tools/tests/test_app_server.py`：主动替换时服务不退出，意外退出时服务退出，`restart_server` 先标记再停进程。拿掉修复时，3 个用例中有 2 个失败。
- 本机离线渲染了登录页的 ChatGPT、API Key、未登录、确认改用 API Key、正在登录这几个状态，以及新的 API Key 页。

## 实机（G100 S，2026-10-01）

- **部署**：用开发覆盖装上 `rungic-voice-agent 0.514+dev20260930t164335.b5ed464.dirty`，基线是发布 20260930.10。apt 核对通过，服务重启后为 active；装上的 `/usr/bin/rungic-voice-agent` 与工作区的哈希一致。
- **当前状态**：`Setup` 报告 Codex 0.159.2，登录方式为 `apiKey`，API Key 已设置。
- **用户重新登录**（01:20:32 开始，01:21:15 成功）：`account/read` 返回 chatgpt/team，用量面板显示套餐的 2 个额度窗口，Agent 的模型为 GPT-6.1-Sol。登录成功后服务替换 app-server，日志为 “codex app-server exited (replaced)”，NRestarts 为 0：切换登录时服务不再退出。
- **device code 流程**（D-Bus 实测）：两次 `CodexLogin chatgpt` 返回同一个 `loginId` 和验证码（“still valid, shown again”）；`Setup` 带着进行中的登录；`CancelCodexLogin` 之后 `login` 为空。
- **测试中又发现一处**：被 `CancelCodexLogin` 取消的登录，结束时的 “Login was not completed” 仍会作为失败发给界面；如果登录页开着（包括刚登录成功的页面），就会多出一条红色错误。修复：服务记下被取消的 `loginId`，这些结束只记日志；页面上没有进行中的登录时，不显示失败。`test_codex_login.py` 增加了对应用例，随后再次用开发覆盖部署。
- **原先等用户确认的**（除上面已确认的以外）：
  - 在新的登录页里用 ChatGPT 登录回去（device code），之后应显示“ChatGPT 套餐（Team）”；
  - 设置首页、Codex 页、登录页和 API Key 页在手机上的实际显示：只做了离线渲染；
  - 切换登录时服务不再退出：要等用户这次切换时，从 NRestarts 和日志里确认。
