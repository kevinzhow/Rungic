# Agent Ready 系统接口索引

本页按当前源码整理，供接入其他 Agent 时查找接口；README 提供[能力总览](../README.md#interfaces-an-agent-can-use)。系统能力不绑定 Codex。内置助理是参考集成，其语音、会话、建议执行和用量链路仍需替换 Agent 提供适配。

这是现有入口的索引，不承诺全部接口已冻结，也不把源码中存在的方法视为跨机型验收。参数以链接的源码 schema、CLI 和 D-Bus introspection 为准；实机范围见各功能文档。本次整理只核对源码和文档，没有启动 MCP、连接手机或进行新一轮实机测试。

## 1. 两个 MCP 服务

两者均使用 stdio，作用域不同。开发机上的诊断服务不能替代手机桌面会话里的操作服务；手机助理也不会因此自动得到开发机的 ADB/root 权限。

### 手机桌面：`rungic-desktop`

在已安装桌面组件的手机 Linux 容器中，以桌面用户启动 `rungic-cua mcp`。客户端通用配置示意如下，配置文件位置由所用 Agent 决定：

```json
{
  "mcpServers": {
    "rungic-desktop": {
      "command": "rungic-cua",
      "args": ["mcp"]
    }
  }
}
```

服务需要正确的 `XDG_RUNTIME_DIR`、`WAYLAND_DISPLAY` 和 `DBUS_SESSION_BUS_ADDRESS`。上面的最小配置使用当前桌面会话，不自动创建独立工作区。要启用双桌面路由，先启动 `rungic-workspace@N.service`，再通过 `rungic-workspace-env N rungic-cua mcp` 启动；该包装器传入工作区会话和 `RUNGIC_USER_*` 用户会话。须在目标设备核验工作区支持和图形环境。内置助理由自己的会话管理代码完成这些准备。

| 工具 | 用途与可用条件 |
|---|---|
| `desktop_windows` | 列出目标会话的窗口与活动窗口 |
| `desktop_launch` | 按应用 ID/名称启动、传入文件或参数；单实例应用迁移可能返回 `needs_confirmation` |
| `desktop_activate`, `desktop_window` | 激活、关闭、最小化、最大化、恢复及移动窗口 |
| `desktop_screenshot`, `desktop_act` | 默认 `codex` 及显式 `luna` 模式：看截图并注入鼠标、滚动、键盘、文本等动作；连接的 Agent 自行推理 |
| `desktop_goal` | 显式 API 模式的多步执行器；Luna 后端或备用 AT-SPI/OCR 路径。默认 Codex 模式不提供此工具，Agent 直接看截图并调用动作；见 [106](106-codex-desktop-operation.md) |
| `desktop_voice_recording` | 默认 Codex 模式的音频 prepare/status/play/close；Agent 自行截图并点击录音和发送，TTS 使用 API key |
| `desktop_voice_message` | 显式 API 模式：使用虚拟麦克风和语音合成操作当前聊天；参数随执行模式变化，发送前需用户授权 |
| `desktop_observe`, `desktop_run`, `desktop_find_name` | 仅 `atspi` 备用模式：控件树、子任务执行、按读音匹配名称 |
| `desktop_where` | 仅工作区路由启用时：查询或选择 `auto` / `desktop` / `workspace` |

`rungic-cua plan` 查看模式，`rungic-cua plan codex` / `rungic-cua plan api`（luna）/ `rungic-cua plan atspi` 切换配置；重启 MCP 后重新获取 `tools/list`。不要将不同模式的工具和参数合并成一个固定清单。`desktop_where` 的选择保存在该路由进程中，另一 Agent 要自行管理与会话的对应关系。

源码：[工具 schema 与 CLI](../agent/computer-use/rungic_cua/server.py)、[路由](../agent/computer-use/rungic_cua/router.py)、[工作区环境](../agent/workspace/rungic-workspace-env)。流程与验收：[60](60-computer-use.md)、[68](68-luna-computer-use.md)、[工作区](research/91-agent-workspaces.md)。

### 开发机诊断：`rungic`

仓库根目录的 [`.mcp.json`](../.mcp.json) 已声明启动命令：

```sh
uv run --quiet --script tools/rungic_agent_mcp.py
```

客户端工作目录须为仓库根目录，或将脚本参数改为绝对路径。依赖由脚本的内联声明管理。先按照 [设备工具](../tools/rungic_device.py) 配置 `.work/device.env` 或 `RUNGIC_ADB`、`RUNGIC_SERIAL`、`RUNGIC_TRANSPORT`，现场核对 ADB server、设备序列号和 root/容器状态，不能沿用工具的历史默认设备。服务启动不等于设备连接成功。

| 工具 | 能力及副作用 |
|---|---|
| `device_status`, `kwin_info`, `host_request` | Android/容器/桌面状态、渲染器及宿主只读查询；`host_request` 只允许指定的读取操作 |
| `logs`, `session_log` | 合并 Android、Linux、内核日志及桌面日志 |
| `crashes`, `crash_groups`, `crash_detail` | 崩溃列表、按签名聚合和详细回溯 |
| `crash_symbolize` | 安装需要的调试符号包并重新生成回溯，会修改设备的软件与报告 |
| `integrity` | 软件包、文件、发布版本及配置漂移检查 |
| `screenshot`, `snapshot` | 手机截屏、开发机本地证据包；会写入 `.work/diag/` |
| `ui_apps`, `ui_find` | 通过 AT-SPI 查看应用和控件 |
| `ui_press`, `ui_tap`, `ui_set_text`, `ui_accessibility` | 操作控件或切换无障碍注册，会改变桌面状态 |
| `trace`, `trace_report` | 采集/分析 Perfetto 和 GPU 追踪；采集会临时启用标记，指定 `swipes` 时还会执行滑动 |
| `build_status` | 查询已有设备端构建的状态，不启动构建或部署 |

上述为 [MCP 声明](../tools/rungic_agent_mcp.py) 中的全部 21 个工具。底层另有 [诊断 CLI/库](../tools/rungic_agent.py)、[追踪工具](../tools/rungic_trace.py) 和构建/发布工具，不应把研究文档的候选 API 当成已发布 MCP 工具。实施与历史验收见 [55](55-agent-native-debugging.md)。

## 2. 手机、工作区与投屏 CLI

以下命令在手机 Linux 容器中运行。图形相关命令需要对应桌面会话和可访问的 Android 宿主；Android 权限、前台状态及设备能力仍适用。优先使用包装命令，底层 `platform.sock` 是本地实现契约，不是远程 HTTP API。

| 入口 | 当前用途 | 参数与实现 |
|---|---|---|
| `rungic-platform --request '<json>'` | `status`、`network-get`、`display-get`、亮度、剪贴板、方向、振动、系统设置面板及投屏控制 | [手机桌面 Skill 的请求表](../agent/assistant/skills/rungic-phone-desktop/SKILL.md) |
| `rungic-cast` | `capabilities`、`status`、`scan`、`connect`、`disconnect`、`settings`、`modes`、`resolution` | [CLI 源码](../desktop/cast/rungic-cast)；不同固件的可用后端不同 |
| `rungic-agent-screen`, `rungic-desktop-mode` | 独立助理屏/用户桌面模式的显示、隐藏、状态与投屏 | [共用实现](../agent/screen/rungic-agent-screen) |
| `rungic-workspace-env N COMMAND...`, `rungic-user COMMAND...` | 选择工作区/用户桌面的图形和 D-Bus 会话 | [工作区环境](../agent/workspace/rungic-workspace-env)、[用户会话](../agent/workspace/rungic-user) |
| `rungic-cua` CLI | 窗口、启动、截图、动作、任务及模式选择；与 MCP 共用实现 | [命令分派](../agent/computer-use/rungic_cua/server.py) |
| `rungic-voice-agent --call-capabilities`, `--start-call`, `--call-command` | 查询通话前提、启动/控制内置通话代理 | [通话契约](../agent/assistant/skills/rungic-phone-desktop/calls.md)、[63](63-call-proxy.md)；通话代理仍在测试，接收拨号请求不代表已接通 |

只读查询示例：

```sh
rungic-platform --request '{"op":"status"}'
rungic-cast capabilities
rungic-cua windows
rungic-suggestions list
```

Skill 是操作说明，可被其他 Agent 复用；不是 MCP 服务或协议本身。系统还有没有 Agent CLI 的交互，例如现有录屏按钮，不能据 GUI 功能推断存在命令接口。

## 3. 主动式智能与助理 D-Bus

以下均为 session bus 服务，需连接实际拥有服务的用户会话。独立工作区有自己的 bus；从工作区访问用户侧建议服务时通过 `rungic-user` 等方式选择正确会话。

### `com.rungic.Suggestions`

总线名/接口名均为 `com.rungic.Suggestions`，对象路径 `/com/rungic/Suggestions`。方法返回的结构化内容使用 JSON 字符串。

| 方法/信号 | 用途 |
|---|---|
| `List`, `Get`, `Knowledge` | 建议列表、单项证据/状态、兼容知识 |
| `Act`, `Update` | 提醒、延后、忽略、恢复、调查/修复/停止，以及结果和计划字段更新；合法动作及校验见实现 |
| `Feedback` | 生成本地反馈事实材料，不上传、不提交 PR |
| `Refresh`, `Changed` | 刷新和变更通知 |
| `SetVisible`, `Presented` | UI 可见性、已展示证据版本与打开记录，供推送调度使用 |
| `AgentUsage`, `RefreshAgentUsage`, `UsageChanged` | Agent 用量读取、刷新及变更通知 |
| `AgentEvent` | 当前助理任务与用量事件的接入点；事件格式需按现有适配器映射 |

CLI 提供 `rungic-suggestions list|get ID|act ID ACTION [JSON]|update ID JSON|feedback ID|knowledge|refresh`。另有 `--collect` 采集入口和 `--validate-knowledge` 校验入口；CLI 不覆盖所有 D-Bus 方法。

源码：[接口声明](../agent/suggestions/service.h)、[服务与任务衔接](../agent/suggestions/service.cpp)、[状态机](../agent/suggestions/model.cpp)、[CLI](../agent/suggestions/main.cpp)。知识库：[格式与状态规则](../compatibility/README.md)。调查结果、证据版本、修复授权和任务完成是不同状态；更新结果不等于确认故障解决。

目前 `investigate` / `apply` / `stop` 和任务恢复会调用 `com.rungic.VoiceAgent`，打开卡片会转到内置助理。用量采集同样对接内置助理和 Codex 数据。替换 Agent 要提供兼容桥接或修改这些连接；仅能调用 `List` 不代表完整建议处理链路已接入。具体范围见 [主动式智能实施与验收](research/proactive-system-care.md)。

### `com.rungic.VoiceAgent`

总线名/接口名均为 `com.rungic.VoiceAgent`，对象路径 `/com/rungic/VoiceAgent`。它是内置助理的集成契约，内部对接 Codex `app-server`，不是通用 Agent 协议。

| 契约分组 | 代表方法/信号 |
|---|---|
| 会话、执行与状态 | `ListConversations`, `OpenConversation`, `CloseConversation`, `DeleteConversation`, `Use`, `SendText`, `State`, `StopTask`, `Approve`, `Event` |
| 语音与助理交互 | `OpenAssistant`, `AssistantTalk`, `StartListening`, `StartTalking`, `StopTalking`, `ReleaseTalking`, `CancelTalking`, `Interrupt`, `TalkToText`, `ReadAloud`, `SetWatching` |
| 主动建议任务 | `InvestigateSuggestion`, `ApplySuggestion`, `SuggestionTask`, `StopSuggestion` |
| 通话 | `CallCapabilities`, `StartCall`, `CallCommand` |
| 账户、偏好与用量 | `Usage`, `Setup`, `SetPreferences`, `SetApiKey`, `TestApiKey`, `RemoveApiKey`, `CodexLogin`, `InstallCodex`, `CancelInstall` |

精确参数与事件定义见 [服务源码的 `INTERFACE`](../agent/assistant/rungic_voice_agent.py)，UI 对接见 [客户端](../agent/assistant/app/agentclient.cpp)。助理应用另以 `com.rungic.VoiceAssistantApp` 的 `/App` 对象提供页面跳转，见 [应用入口](../agent/assistant/app/main.cpp)。这些账户安装方法含 Codex 专用行为，不能直接宣称任意 Agent 即插即用。

## 4. 共享 Linux 能力与交付入口

| 机制 | Agent 可利用的能力与边界 | 参考 |
|---|---|---|
| Shell、文件与共享目录 | 编译、脚本、成果文件、`~/Shared` 中的 Android 共享存储；共享存储不具备完整 Linux 文件语义 | [文件系统能力](69-filesystem-capabilities.md) |
| PackageKit / `pkgcli`、polkit、APT | 软件安装、系统认证与版本管理；桌面密码对话框、行为上的确认、root 权限是不同机制 | [应用商店与授权](45-plasma-app-store.md) |
| Wayland、KWin、XDG Desktop Portal、AT-SPI | 窗口、截屏、输入、屏幕共享与无障碍；需正确会话、权限及应用支持 | [桌面操作](60-computer-use.md)、[后端接口映射](research/31-backend-integration.md) |
| PipeWire / PulseAudio、libcamera、GStreamer / FFmpeg | 摄像头、麦克风、音频路由、编解码；普通 Linux 软件和 Agent 共用硬件后端 | [媒体链路](48-plasma-media-pipelines.md)、[虚拟音频](62-linux-virtual-audio.md) |
| Android-backed NetworkManager、BlueZ、ModemManager D-Bus | 已实现的网络、蓝牙、蜂窝状态及部分操作；是兼容子集，不保证覆盖上游全部方法 | [共享层源码](../shared/platform/)、[上游接口复用](73-reduce-upstream-changes.md) |
| systemd / journald、崩溃收集与诊断命令 | 服务状态、日志、持久化证据；手机上的查询不自动具备开发机诊断工具的全部权限 | [诊断实现](../system/diagnostics/)、[55](55-agent-native-debugging.md) |
| 构建 Skill、补丁队列、版本化包与发布工具 | Agent 可按工程流程构建、部署、验收并回退软件包；不是 MCP 一键刷机接口 | [三段式 Skill](../.agents/skills/rungic-three-stage-image/SKILL.md)、[独立构建与安装边界](75-image-build-separation.md) |

## 5. 替换 Agent 时的验收范围

先验证 MCP 握手与实际 `tools/list`、目标桌面选择、截图/输入、应用启动和成果文件；再验证任务进度/停止、建议调查和过期修复授权拒绝，最后接入语音、页面跳转和真实账户用量。API key 没有提供订阅额度时应显示不可用，不能推算重置时间。

桌面工作区只隔离显示和输入，仍共享 Linux 账户与文件。MCP annotations 是工具提示，不是权限执行器；内置 Agent 的确认策略、polkit 授权和建议服务的版本校验分别承担不同职责。替换集成的验收要求见 [接入说明](README.md#integrating-another-agent)，不能沿用内置 Codex 的成功记录作为另一 Agent 的验收结果。
