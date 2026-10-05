# 语音Agent：GPT Realtime 驱动 Codex

> 2026-10-03 功能更新：默认桌面操作由当前认证 Codex 使用 Agent 所选模型逐步看图、点击和核验，进展留在来源对话；Luna API 为显式备选。执行方式在「设置 → 桌面操作」选择，登录方式单独设置，语音与 TTS 仍使用 API key。开发 G100 的桌面与 API 只读验收见 [106 篇](106-codex-desktop-operation.md)，完整电话和语音消息未在本轮验收。下文保留各阶段实现记录。

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-24。目标：用户点按钮后说话，由 Agent 在本机 Linux 桌面上完成工作。第1步原型已在实机跑通。

## 选型与来源

| 组件 | 版本/来源 | 许可证 | 说明 |
|---|---|---|---|
| Codex CLI 完整包 | `codex-package-aarch64-unknown-linux-musl.tar.gz`，[rust-v0.156.1](https://github.com/openai/codex/releases/tag/rust-v0.156.1)（2026-09-23），SHA256 `fdd47ed6aade0360796fd3f6f95a45096f327c15e19e8c7339f9dc5633041786`（与GitHub发布摘要一致） | Apache-2.0 | 含`bin/codex`、`bin/codex-code-mode-host`（执行命令必需；单独的CLI二进制缺它时Agent无法执行命令）、rg、bwrap、语音运行时 |
| 实时语音 | OpenAI Realtime（Codex默认`gpt-realtime-1.5`，协议v2），输入转写`gpt-4o-mini-transcribe` | 云服务 | 经Codex app-server使用 |

调研时核对的是Codex源码`29f056c`（2026-09-24），调研范围也包括其他语音识别与语音合成方案。

## Codex 如何用实时语音调用 Agent（源码核实）

- 实时会话只给语音模型两个工具：`background_agent`与`remain_silent`（`codex-api/src/endpoint/realtime_websocket/methods_v2.rs`）。
- 语音模型调用`background_agent(用户原话)`时，Codex在同一线程开始一轮Agent任务。若Agent正忙，这段话会作为补充指示并入当前任务（steer）。
- Agent的进度和结果作为该函数调用的返回值送回语音模型，由它播报；不值得播报时调用`remain_silent`。
- 语音模型使用服务端VAD：检测到用户说完就开始回应，用户开口则打断当前播报。
- 这一切都在`codex app-server`（JSON-RPC，实验性接口需在初始化时声明`experimentalApi`）中实现：
  - `thread/realtime/start`、`appendAudio`（base64 PCM）、`appendText`（只追加对话项，不触发回答）、`stop`；
  - 通知`thread/realtime/outputAudio/delta`、`transcript/done`等；
  - Agent执行命令、改文件时的审批以服务端请求形式发给客户端。

## 认证与代理

- 用户用ChatGPT账号经设备码登录（`codex login --device-auth`），Agent任务使用该账号。
- Codex 0.156的WebSocket实时会话必须使用API Key（`realtime_api_key`：ChatGPT登录时临时从`OPENAI_API_KEY`读取）。WebRTC传输可以用ChatGPT账号，但媒体走UDP，用户的HTTP代理无法转发，因此不用WebRTC。
- 用户提供的API Key只保存在容器内`~/.config/moto-voice-agent/openai-api-key`（目录700、文件600），仅在启动`codex app-server`子进程时传入，不进仓库、文档或日志。
- **代理**：
  - 所有Codex流量必须经用户指定的`http://192.168.5.45:6152`。`/usr/local/bin/codex`是包装脚本（`plasma/codex/codex-wrapper`），每次先加载`/etc/profile.d/proxy.sh`。
  - 源码核实：reqwest与实时WebSocket拨号器（`websocket-client/src/dialer.rs`，tokio-tungstenite proxy特性）都遵循`HTTPS_PROXY`/`ALL_PROXY`/`NO_PROXY`。
  - 实测：登录（在清空所有代理变量的情况下启动）、`codex exec`、实时会话期间，Codex进程的TCP连接只有`192.168.5.45:6152`。

## 安装位置

- `/usr/local/lib/codex/0.156.1/`：完整包原样解开。
- `/usr/local/lib/codex/current -> 0.156.1`。
- `/usr/local/bin/codex`：包装脚本。
- `/usr/local/bin/moto-voice-agent`：原型（`plasma/voice-agent/moto_voice_agent.py`）。

升级时解开新版本，再切换`current`链接。

## 第1步原型与实测

`moto-voice-agent`的工作流程：

1. 启动`codex app-server`，线程工作目录为家目录、沙箱为`read-only`、审批策略为`on-request`（原型阶段一律拒绝提权）。
2. 以WebSocket开启实时会话。
3. GStreamer从PulseAudio `android_microphone`取24 kHz单声道S16LE，每100 ms送一次。
4. 回复音频送往扬声器；播放期间及其后0.6 s暂停上传麦克风，作为最简单的防回声措施。

参数：`--audio-file`把录音按实时速度当作麦克风输入，用于自动测试；`--text`只追加文字。

实测（用`gpt-4o-mini-tts`合成的“帮我看一下磁盘还剩多少空间”作为输入）：

1. 转写得到“幫我看一下磁盤還剩多少空間”。
2. 约5 s后语音模型移交任务，Agent在bwrap只读沙箱中运行`df -h`。
3. Agent回答“还剩193 GB……”。
4. 语音模型播报“磁盘总容量大概222GB，用了30GB，还剩193GB……”。

从说完到开始播报约11 s。给线程加`developerInstructions`要求使用简体中文后，Agent与播报均为简体。（当时也传了`realtimeStartInstructions`，后来核实它只进入Agent上下文，见第2步。）

## 第2步：对话列表与按住说话（2026-09-24）

- **后台服务**：`moto-voice-agent --service`，systemd用户单元`moto-voice-agent.service`，D-Bus按需启动，服务名`dev.moto.VoiceAgent`。
  - 接口：`ListConversations`、`OpenConversation(id)`（空id为新对话）、`CloseConversation`、`DeleteConversation`（同时归档Codex线程）、`StartTalking`/`StopTalking`、`Interrupt`、`Approve(id, allow|allow-session|deny)`、`State`；信号`Event`（JSON）。
  - 每个对话就是一个Codex线程：工作目录为家目录，沙箱`workspace-write`，审批策略`on-request`。
  - 界面历史按对话存为`~/.local/share/moto-voice-agent/conversations/<id>.jsonl`，内容包括双方话语、Agent消息、命令及输出、文件改动、审批。说出第一句话后才进入列表，并以这句话作标题。
  - 打开对话即开启实时会话，空闲10分钟后关闭。
- **按住说话**：
  - 按下时停止正在播放的回答（打断），并开始采集；只在按住期间打开麦克风，自然避免外放回声。松开后补0.9 s静音，让服务端VAD判定一句话结束。
  - 采集数据拼成100 ms一块，由单一线程按顺序上传。起初每块各开一个线程上传，服务器收到的音频乱序，无法识别。
- **系统提示词**（`plasma/voice-agent/prompts/`）：
  - `realtime.md`作为语音模型指令，经`thread/realtime/start`的`prompt`传入。它会整体替换Codex默认指令，因此保留Codex默认内容（Apache-2.0，注明出处），后面追加本机环境、位置、能力和语言要求。
  - 源码核实：`realtimeStartInstructions`注入的是Agent上下文，不是语音模型指令。改用正确的`prompt`之前，语音模型仍回答“无法查看你的手机”；改后直接把请求交给Agent。
  - `agent.md`作为Agent的`developerInstructions`：手机硬件、Android宿主、LXC中的Ubuntu、Plasma Mobile、共享存储、投屏、代理、权限和回答方式。
- **技能**：`skills/moto-phone-desktop/SKILL.md`，安装到`/usr/local/share/moto-voice-agent/skills`，并链接到`~/.codex/skills/`。内容包括平台桥全部操作、电量/内存/存储、窗口与截图（新装`kde-spectacle`）、启动应用、通知（新装`libnotify-bin`）、AT-SPI操作（`moto-a11y`）和录屏。
  - 实测Agent在处理“手机还剩多少存储”时自己先读取了该技能，再执行`df`。
- **界面**：Kirigami应用`moto-voice-assistant`，桌面入口“语音助手”（`dev.moto.VoiceAssistant.desktop`）。
  - 列表页：新建、打开、删除对话。
  - 聊天页：你的话、助手的话（均含实时转写）、Agent消息（Markdown）、命令卡片（运行中/成功/失败，可展开输出）、文件改动卡片、审批卡片（允许/本次对话都允许/拒绝）、状态行，底部按住说话按钮。
- **实测**：
  - 直接注入音频：从说完到Agent执行`df`再到播报，事件全部到达界面。
  - 界面长按3 s后，服务收到开始与结束，约2.7 s音频。
  - 用手机扬声器外放合成语音再由手机麦克风录入的自测不成立：Android采集启用回声消除，会抵消本机播放的声音，识别结果成了乱码。真人说话已在第1步实测中识别准确。

## 回复从发起的一端播放（2026-09-24）

用户实测按住说话后听不到回复。原因不在语音：服务收到并播放了回复音频（一轮约5.3 s），但投屏连接时Android把所有媒体声音（包括容器经Termux PulseAudio的音轨）放在`AUDIO_DEVICE_OUT_PROXY`上送往电视。此外，手机扬声器的媒体音量当时为1/15。

用户要求：**从哪一端发起语音交互，回复就从哪一端播放。**

- **共享层**：新增始终在手机本机播放的PulseAudio输出`android_phone`（“手机本机”），任何应用都可以选用。默认sink仍是跟随Android路由的`android`。
  - 调研：Termux PulseAudio 17.0-4的`module-aaudio-sink`与`module-sles-sink`都不能指定输出设备（参数只有sink名、格式、延迟、性能模式等）；OpenSL ES也只能选流类型。因此没有改Termux模块。
  - 做法与麦克风对称：容器内`module-pipe-sink`（PulseAudio 17，LGPL-2.1+）→ `media-bridge`在sink未挂起时读FIFO → 私有`capture.sock`的`phone-output` → APK的AudioTrack（`USAGE_MEDIA`）。`setPreferredDevice`优先选有线/USB/蓝牙耳机，没有时用扬声器；设备增减时重新选择。
  - 延迟：pipe-sink由读取速度计时，按FIFO内未读数据上报延迟。FIFO缩到16 KiB，socket收发缓冲各16 KiB（各约85 ms）。sink空闲3 s挂起后停止读取，并丢弃FIFO残留。
  - 播放不要求Plasma在前台（与Termux输出一致），采集仍要求。例外：和 Agent 的通话在锁屏、后台时继续收音（2026-10-05，docs/101「锁屏继续通话」）。
  - PulseAudio 17的`pactl -f json`遇到UTF-8描述（“手机本机”的monitor source）会报错。media-bridge改用`pactl list short`查麦克风source，否则主循环会一直走异常分支，麦克风也会停用。
- **语音助手**：`StartTalking(s screen)`由按钮传入所在屏幕名。KWin把投屏输出命名为`CAST-n`，此时回复用默认sink（跟随Android路由，在电视上）；否则用`android_phone`，该sink不存在时退回默认。
- **实测**（投屏连接中，用合成语音）：
  - 从手机屏`WL-0`发起：回复8.9 s，APK音轨（uid 10352）在`AudioOut_15`（SPEAKER）上。
  - 从电视屏`CAST-1`发起：回复6.25 s，Termux音轨在`AudioOut_D`（PROXY）上。
  - 两路并存互不影响；麦克风回归：`parec`取到3.8 s有效信号。
  - 以上以Android路由证据为准，尚待用户实听确认。
- **音量**：投屏时手机音量键和设置里的媒体音量调的是电视那一路（PROXY）。扬声器的媒体音量要在未投屏时调节。本机当时是1/15，需要调大才听得清。Android没有给普通应用按设备设置音量的公开接口。

## 等待时先回应并播报进度（2026-09-24）

用户要求：提出请求后不要让人干等；先回应要去做什么，处理中告知进度。

- **源码核实**（Codex rust-v0.156.1，`core/src/realtime_conversation.rs`的`handle_handoff_output`）：
  - 协议v2下，Agent执行中的消息（`HandoffUpdate`/`HandoffAppend`）只作为`[BACKEND]`用户消息加入对话，不发`response.create`，所以语音模型不会开口。只有任务完成（`CompletedHandoff`）时才触发回答。
  - `thread/realtime/appendSpeech`（`StandaloneSpeech`）会加入一条`[BACKEND]`消息并请求`response.create`。语音模型正在回答时，Codex会把请求排到当前回答结束后。
- **先回应**：`realtime.md`新增“Responsiveness”一节，作为用户的持续偏好覆盖默认提示词中“不要宣布计划”的要求。移交任务前先说一句很短的确认，再在同一回答中调用`background_agent`。
- **播报进度**：服务在Agent工作期间每秒检查一次，满足条件时通过`appendSpeech`发“进度（已用时N秒，任务仍在进行）……”，并要求语音模型用一句话说明当前在做什么、不要说成结果。
  - 工作不足8 s的任务不播进度。
  - 有新的过程说明（`agentMessage`且`phase`不是`final_answer`）、且距离上次出声至少6 s时，播报这条说明。说明超过8 s未播就视为过时并丢弃，避免播报已经完成的步骤。
  - 长时间没有新说明时，说“还在处理”和当前命令：先等20 s，此后间隔依次为30 s、45 s，最长60 s。
  - 按住说话期间不插话；回复音频和上一次进度请求都算作“出声”。
- **等用户审批时**：出现审批卡片立即播报，请用户在屏幕上点“允许”或“拒绝”。等待期间不再说“还在处理”。
- **实测**（合成语音，手机端发起）：
  - “找共享存储里最大的五个文件”：转写完成后0.3 s回应“好，我来帮你找一下。”；第8 s播报“正在扫描文件并按大小排序”；完成后播报结果。修正过时说明之前，还多播了一条已完成的步骤。
  - “截屏并告诉我屏幕上有什么”：先回应，第8 s播报进度，第19 s出现审批，2 s后播报“请在屏幕上的提示卡里点一下‘允许’或‘拒绝’”。
- **发现的问题**：Agent的`workspace-write`沙箱禁止连接Unix socket（D-Bus、Wayland、平台桥都返回EPERM），因此截图、AT-SPI、`moto-platform`等桌面操作每次都要审批提权。可以用“本次对话都允许”，根本解决办法是第3步把桌面工具放在沙箱外作为MCP提供（已完成，60篇）。

## 回复播放断续（2026-09-24）

用户反映回复播放时会突然快进、突然卡顿。

- **原因**：播放管线用`appsrc is-live=true do-timestamp=true`，按到达时间给音频打时间戳。实时API的音频是一阵一阵到的，到达时间与音频时长对不上，pulsesink（audiobasesink）便判定不连续并重新同步。
  - 一段回复的`GST_DEBUG=audiobasesink:5`日志中，有3次“Unexpected discontinuity in audio timestamps”，其中−1.08 s即丢掉约1秒语音，另有多次数千到五万多样本的对齐调整。
- **修复**：按顺序播放样本，不再打到达时间戳（`do-timestamp=false`，pulsesink `sync=false`，`buffer-time` 300 ms），由PulseAudio按采样率连续播放。复测同一类回复，没有任何不连续或重新同步记录。
- **手机本机输出**：APK 1.34把AudioTrack缓冲从约40 ms加到约150 ms。语音与媒体播放不需要低延迟，而Linux侧由Python线程转发，手机繁忙时会被延迟。每次播放结束时，在`MotoAudio`日志中记录欠载次数：一段约35 s的回复只有1次，是开始播放时缓冲为空的那一次。

## Agent模型、权限与停止按钮（2026-09-24）

- **模型**：
  - Agent的模型与推理强度由`thread/start`与`thread/resume`的`model`和`config.model_reasoning_effort`指定，已有对话恢复时同样切换。先改为`gpt-6-luna` medium以求更快，用户认为其推理太弱，改回`gpt-6-sol` medium（`AGENT_MODEL`/`AGENT_EFFORT`）。
  - 账号可用模型（`model/list`）：gpt-6-sol（默认）、gpt-6-astra、gpt-6-luna（“快速、便宜，适合简单任务”），以及5.6系列。
  - 实时语音起初为Codex默认的`gpt-realtime-1.5`，2026-09-25改为`gpt-realtime-2.1-mini`（见下文）。输入转写`gpt-4o-mini-transcribe`由Codex固定，只用于显示和交给Agent的上下文；交给Agent的任务文字由实时模型自己写入`background_agent`的参数。
- **权限**：用户要求去掉所有授权，改为`approvalPolicy: never`、`sandbox: danger-full-access`，即Codex的YOLO模式。
  - Agent以桌面用户身份不受限制地执行命令，也能直接访问D-Bus和Wayland。
  - 破坏性操作由提示词约束：删除、覆盖、卸载、发送、发布、付款、改账户或系统设置，都需要用户明确要求并先确认。实时模型的提示词也去掉了“授权卡片”的说法。
- **停止按钮**：
  - 界面在Agent工作或正在播报时，于说话按钮右侧显示“停止”。它调用D-Bus `StopTask`：服务先停止播放，再对当前turn（`turn/started`里的id）调用`turn/interrupt`，并发`task-stopped`事件（界面显示“已停止”）。
  - 按下时正在播报的那条回复，余下的音频会继续到达，因此一直丢弃到该回复结束（下一条助手`transcript/done`）；按住说话时也会解除。
  - 实测：截屏任务开始3 s后按停止，Codex记录`turn_aborted interrupted`，界面出现“已停止”，之后没有再播出残句。
  - 用语音说“停”仍会作为补充指示交给正在进行的任务（steer），不是强制停止。
- **测试参数**：`--stop-after N`在N秒后模拟按下停止。

## 主动解决问题（2026-09-24）

用户反映助手总让用户自己去做事，不够主动。提示词改为：
- Agent（`agent.md`）：
  - 由它负责把事做成：出错时自己查日志和状态、找原因、能修就修。
  - 能用工具完成的事不让用户做；普通步骤不征求许可。
  - 需要用户决定时，给出2–3个选项并把推荐放在第一个。
  - 只有密码（让用户在对话框里输入，不要说出来）、删除、卸载、发送、付款、改账户这些才交给用户。
  - 只报告核实过的结果。
- 实时模型（`realtime.md`）：不指挥用户动手，问题转交执行端处理；有选项时念出推荐项；不声称执行端没有确认过的结果。
- **环境说明**：用户在Linux桌面里工作，“安装、打开、文件、应用”默认都指Linux一侧。用户要装的软件直接安装（`.deb`用`pkcon install-local`，Flatpak用`flatpak install`）；系统要密码时，由Agent触发授权框并说明它在哪块屏幕上。
  - 起因：一次测试中，Agent查清了微信包是arm64的`.deb`，却按安卓理解，结论成了“手机装不了，请下载安卓版”。

## 语音投屏（2026-09-24）

- **用法**：用户说“投屏”“投到电视”“断开投屏”时，实时模型交给Agent执行。Agent按技能说明运行`moto-cast connect`或`moto-cast disconnect`，可用`moto-cast status`确认。链路与自动重连见[58篇](58-miracast-desktop-feasibility.md)第5步。
- **实测**（合成语音，从手机发起）：
  - “把投屏断开。”：先口头回应，Agent执行`moto-cast disconnect`，约6秒后确认。
  - “投到电视上。”：Agent读技能说明、执行`moto-cast connect`、再用`status`确认，约26秒后说“已经投到那台TCL 85Q6H电视上了”。
- **耗时**：主要花在Agent的推理轮次（读技能说明、多一次`status`），连接本身约7秒。

## 一次按住只算一句话；每轮工作折叠显示（2026-09-24）

用户反馈：话还没说完，消息就被拆成好几段发了出去；聊天里一轮工作冒出很多气泡。要求像ChatGPT/Codex那样，把一轮的工作折叠起来，点开可以看到做了什么。

- **原因**：
  - Codex 0.156把实时会话的轮次检测固定为服务端VAD：静音500 ms即结束，`create_response`为true（`codex-api/src/endpoint/realtime_websocket/methods_v2.rs`）。app-server没有提供修改它或手动提交音频的接口。
  - 于是按住期间一停顿，服务端就提交半句并开始回答。
  - 实例：23:17:12，半句话被识别成“Ni cena tema.”，助手随即回复“好的，我来看看电量”并开始处理。
- **发送端**（`PauseGate`）：
  - 按住期间照常实时上传，只把停顿中间的静音留在本地：停顿开始的200 ms照常发送；说话恢复前，先补发最后的140 ms，保留轻起音。因此服务端看到的静音不超过约340 ms。
  - 停顿判定按20 ms一帧：比本次按住的底噪（电平第20百分位）高不到10 dB，且比本次最响的语音低至少12 dB。
  - 松开后发送余下音频和900 ms静音，这时服务端才结束这一轮。
  - 日志记录每次按住省略的停顿时长。
- **接收端**（`ChatPage.qml`）：
  - 用户转写带`press`（本次按住的起始时间），同一次按住的各段合并进一个气泡。
  - Agent一轮（`agent-started`到`agent-finished`）是一张折叠卡片，按顺序记下：处理中口头播报的进度、Agent的说明（`final`表示最终报告）、命令与MCP操作（点开看输出）、文件修改。
  - 交出任务前那句“好的，我来…”仍是气泡，留在卡片上方。
    - 起初把它挪进卡片，但它显示时还不知道后面会不会开始工作；Agent一开始，已显示的气泡又消失了，用户看到的是先露出来再被删掉。
    - 现在的规则是：显示过的条目不再移除（与Codex桌面版一致，先是一句回应，再是折叠的处理过程，最后是答复）。
  - 开始新的请求时，之前展开的卡片自动收起。
  - 卡片标题：运行中显示“正在处理 · N秒”和最新一步；完成后显示“已处理 · N步 · 用时N秒”；停止后显示“已停止”。点标题展开或收起。
  - Agent完成后的口头答复仍是气泡。
  - 历史记录按同样规则重放。保存时未结束的一轮，按最后一个事件计时。
- **实测**：
  - 合成语音“帮我看一下”，停顿1.6 s（约-60 dBFS噪声），再说“手机现在还剩多少电”，在同一次按住内发出。
    - `--raw`（不处理停顿）：前半句单独提交，助手立即说“好的，我来看看”并开始处理，后半句成为第二条消息。与用户遇到的一致。
    - 默认：省略1520 ms停顿，服务端只收到一条“帮我看一下，手机现在还剩多少电？”，只回应一次。
  - 界面：在电视上打开用户的真实对话，两轮工作分别折叠为“已处理 · 32步 · 用时126秒”和“已处理 · 19步”。展开后依次是说过的话、Agent说明和命令。
- **未验证**：真人说话、环境嘈杂（例如电视正在出声）时的停顿判定。如果服务端仍把静音判定成停顿而切开，气泡会合并，但助手可能会先回应前半句。

## 流式气泡按转写段配对；实时会话只启动一次（2026-09-25）

用户反馈：新对话里第一次按下说话后，常多出一个类似流式输出的残缺气泡。

- **原因一（界面）**：
  - 用户的转写常在助手开始回答之后才完成。
  - 实测新对话按下：用户片段 5.41–5.50 s，助手片段 5.57 s 开始，用户完成 5.73 s，之后助手片段继续。会话就绪后也会交错：用户段 9.08 s 开始，助手段 9.34 s 开始，用户段 9.52 s 完成。
  - 旧逻辑按“最后一条是不是流式气泡”来拼接：
    - 用户完成时删掉用户流式气泡，把正式消息追加到末尾。
    - 助手后续片段看到最后一条已是用户消息，就另起一个流式气泡。
    - 助手完成时只替换后一个，前一个“好的，”一直残留，还排在用户消息之前。
- **原因二（服务端）**：
  - 打开对话时后台启动实时会话；按下时 `realtime` 还没变成 True，`start_talking` 又启动一次。
  - 复现（打开后 0.5 s 按下）：`thread/realtime/start` 调用两次，`started` 收到两次（2.10 s、3.71 s）。第二次重建了会话，送进第一个会话的音频有丢失的风险。
  - 两次启动还让音频集中送达，交错更明显，所以第一次按下几乎必现。
- **修复**：
  - 服务端改用 Codex 带条目 id 的转写通知：`thread/realtime/item/started`、`item/completed`（`transcriptSegment`）和 `item/transcript/delta`（`itemId`）。
    - `delta` 和 `message` 带上 `id`。
    - 用户段的 `press` 取该段开始时的那次按下。
    - `message` 带上 `started`（该段开始的时间）。
  - 实时会话启动加 `realtime_starting` 标志：已在启动时，按下只等待，不再重复启动；收到 `started`/`closed`、启动失败或 20 s 未就绪时清除。
  - `ChatPage.qml`：
    - 片段按 `id` 找到自己的气泡。
    - 完成时原地转为正式消息，不再删掉后追加。
    - 同一次按下的后续段并入该次的消息。
    - 助手在 Agent 开始前已经以气泡流式显示的话，完成后仍是气泡；Agent 工作中说的话照旧进入卡片。
    - 历史回放没有流式片段，按 `started` 早于工作开始，把这句放在卡片上方，与实时显示一致。
- **验证**：
  - 服务端：打开后 0.3 s、0.5 s 按下，均只调用一次 `thread/realtime/start`，只收到一次 `started`。
  - 界面：离屏加载真实的 `ChatPage.qml`/`ChatItem.qml`，用假的 `AgentClient` 单例回放事件。
    - 实录的交错事件流（新对话，打开后 0.5 s 按下）：旧版本多出残缺的“好的，”气泡，排在用户消息之前；新版本只有用户消息、确认语、工作卡片和答复。
    - 构造的时序（新版本全部正确）：
      - 一次按下被切成两段，中间插入助手片段：合并为一条用户消息。
      - Agent 开始时确认语还在流式输出：确认语保持为气泡，旧版本会把已显示的气泡收进卡片。
      - 工作中播报进度：进入卡片。
      - Agent 先于用户转写开始：用户消息插在卡片前。
      - 历史回放（含没有 id 的旧记录）：显示正确。
  - 测试中新建的对话已删除。
- **未验证**：真人按住说话时的界面表现（本轮用合成语音和事件回放验证）。

## 实时语音模型换成gpt-realtime-2.1-mini；语气自动控制（2026-09-25）

- **模型**：`thread/realtime/start`传入`model: gpt-realtime-2.1-mini`（常量`REALTIME_MODEL`），替换Codex默认的`gpt-realtime-1.5`。
  - Codex 0.156.1的app-server接受该参数（`generate-json-schema --experimental`的`ThreadRealtimeStartParams`中有`model`）。
  - 用独立探针开启`RUST_LOG=debug`启动实时会话：日志里出现的是`gpt-realtime-2.1-mini`，会话以v2启动。
  - 官方模型页：只支持`v1/realtime`，支持工具调用和推理。发布公告称该模型支持可配置的推理强度，但Codex不发送这一项。
- **情绪（emotion）**：
  - OpenAI模型页、发布公告和Realtime参考中都没有叫emotion的会话参数（本轮检索范围内）。语气、语速和情绪靠指令控制。
  - 做法：`realtime.md`新增“Voice and emotion”规则，由模型按每次回答的内容和用户的状态自选语气：
    - 好消息：轻快、温暖。坏消息或失败：平静、诚恳，略带歉意。不可撤销操作或警告：严肃、放慢。
    - 进度：平稳；久等后简短致歉。
    - 用户烦躁：平静、简短、不开玩笑。用户放松：可以轻松一些。用户着急：更快更干脆。
    - 情绪只影响声音，不增加话语。
  - 我们主动让它播报进度时（`appendSpeech`），也附上语气提示：前45秒“平稳、让人安心”，之后“平和，简短为久等致歉”。
- **实测**：用合成的烦躁语音“怎么还没好啊，烦死了，电量到底还剩多少？”测试。
  - 首版规则下，最终答复13.7 s，还加了没有依据的推测。
  - 补上“情绪不增加话语、一句给结果、不安抚不猜测”后，答复变为5.75 s，但仍带一句安抚，mini模型对这条规则执行得不够严格。
  - 语气本身未经人耳评估（本机无法听到）。

## 长按Home呼出（2026-09-25）

长按Home呼出的悬浮层、固定的助理对话、预热和免提模式见[67篇](67-home-assistant.md)。本篇相关的改动：
- 服务随会话启动（`plasma-workspace.target`），启动时恢复助理对话但不连接实时语音，并提前建好麦克风管线。
- `CloseConversation`带上对话id，只关闭仍是当前的那个；助理对话不因应用离开页面而关闭。打开已经是当前的对话时直接返回，不重连实时语音。
- 聊天页的事件处理抽成`ChatModel.qml`，聊天页和悬浮层共用；按上一节的回放测试，重构前后结果一致。
- 应用改为单实例：再次启动（例如悬浮层“在应用中查看”）把要打开的对话交给正在运行的实例。

## App 重新设计（2026-09-25）

用户要求：用 Claude Design 重新设计语音助手 App，设计稿在画布“语音助手 App”（6 张手机稿加一张大屏稿），确认后用 QML 实现。

- **视觉**：沿用 Home 浮层（67 篇）的语言。App 固定为深色，底部带一点冷色光；光丸就是说话控件。颜色和常用函数集中在单例 `Style.qml` 里。
- **对话列表**（`ConversationsPage.qml`）：
  - 置顶显示长按 Home 用的助理对话，以及它最近的一句话；
  - 其余对话按日期分组，每行是标题、最后一句话和时间；
  - 左滑出现“删除”；
  - 按住底部光带，就新建对话并立即开始听，松手后再进入这个对话。服务端先建好对话再打开麦克风，在这之前就松手也会被正确处理。
  - 后端配合：`ListConversations` 的每个条目新增 `preview`（从对话文件末尾最多 64 KB 里找最后一条消息，或“通话结束”）和 `assistant`（是否为助理对话）。
- **对话页**（`ChatPage.qml`、`ChatItem.qml`）：
  - 内容限宽 680 px 居中，内容少时贴着底部的说话栏；
  - 用户的话是右侧的玻璃气泡，助手的话是正文，Agent 工作之后的答复用大字；
  - 工作卡片运行中描金边并显示转圈，展开后每一步是图标加小标题；命令和输出合在一个等宽代码块里，超过 8 行时折叠，点击展开；
  - 通话卡片：状态和计时、目的、双方的话；“问你”单独用金色框出；按钮为旁听、我来接、挂断，挂断是红色。
- **说话栏**（`TalkDock.qml`，取代 `TalkButton.qml`）：
  - 按住说话，轻点进入免提；手指滑离光丸 64 px 以外再松开，就丢弃这句话；
  - 运行中或回答时右侧出现“停止”；
  - 按住时光从底部升起（与浮层共用 `Bloom`），光由 60 Hz 时钟驱动，空闲时静止。
- **宽屏**：Kirigami 的 `pageStack` 分栏，列表列宽 20 格，右侧是对话，宽屏上不显示返回键。
- **顺带修复**：历史记录里没有结束事件的通话，加载后原来一直显示为“进行中”，还带着挂断按钮；现在标为已结束。如果随后的状态事件表明通话确实还在进行，再恢复（`ChatModel.staleCallAt`）。
- **实机验证**（手机 WL-0）：
  - 列表页、对话页、展开的工作卡片、已结束的通话卡片，截屏与设计一致；
  - 页面背景必须是带 `color` 的 `Rectangle`，否则 Kirigami 的 `PageRow` 会报错。
  - 按住列表页的光带：服务端依次记录“新对话 → 开始说话 → 收到 6 秒音频后停止”，松手后进入新对话页。这次没有答复，因为播放合成语音时手机媒体音量处于静音。
- **未验证**：
  - 在 App 里完整说一轮（上面那次没有答复）；
  - 大屏分栏；
  - 左滑删除的手势手感。

## 面板跟随应用配色；滚动不再跳回底部（2026-09-25）

用户反馈：App 里的状态栏和导航栏不沉浸；对话往上滚后会突然跳回底部，问是不是组件选错了。

### 状态栏和导航栏

- **原因**：前台有最大化的应用时，Plasma Mobile 把两条面板画成不透明，颜色取系统主题（现在是浅色 Breeze 的 Header 和 Window 配色），与应用本身无关。深色的 App 于是上下各夹着一条浅色的系统栏。这是共享层的问题，每个自带配色的应用都会遇到。
- **调研**：
  - 上游 plasma-mobile 的合并请求中，面板颜色相关的有 #136（顶栏改用 Header 配色）、#820、#827、#838、#863（启动反馈时的颜色），都没有让面板跟随应用配色；以“status bar color”“panel color”“colorscheme”检索，本轮未找到这类工作。
  - Android 的做法是由应用设置系统栏颜色，或者绘制到系统栏下面（edge-to-edge）。Plasma Mobile 的最大化窗口不会延伸到面板下面。
  - 现成的标准通路：KDE 应用通过 `org_kde_kwin_server_decoration_palette` 协议上报自己的配色方案，KWin 保存在 `Window::colorScheme`，原本用来给窗口标题栏上色。plasma-integration（Plasma/6.6 分支 `kwaylandintegration.cpp`）在窗口创建和应用调色板变化时，把 `qApp` 的 `KDE_COLOR_SCHEME_PATH` 属性发给 KWin；KColorSchemeManager 切换配色时设置的也是这个属性。
- **做法**（应用 → 标准接口 → 共享层）：
  - 应用：`MotoVoiceAssistant.colors` 由 Breeze Dark 派生（LGPL-2.0-or-later），Window、Header、View、Complementary 的背景都是 App 的底色 #0A0B10，文字 #F4F1EA，强调色为金色。`main.cpp` 在创建窗口之前设置 `KDE_COLOR_SCHEME_PATH` 和对应的调色板；App 背景改为纯色，与导航栏无缝衔接。
  - KWin：convergentwindows 包里新增 `WindowColors.qml`。普通窗口被激活、窗口配色变化或换屏时，用 `DBusCall` 把 `(输出名, colorScheme)` 发给 plasmashell。之所以放进这个已经启用的脚本，是因为 Plasma Mobile 的 kwinrc 插件列表由 envmanager 写入且不可改，新增脚本需要改 envmanager。
  - plasmashell：`ShellDBusObject` 新增 `setActiveWindowColorScheme(屏幕, 配色)` 和 `windowBackground(屏幕, 配色组)`。配色是绝对路径或 `color-schemes/` 下的名字，用 KConfig 直接读 `BackgroundNormal`（plasma-mobile 没有链接 KColorScheme）。`kdeglobals` 表示应用没有自己的配色，返回空字符串，面板保持原样。
  - 面板：状态栏（`StatusBarWrapper.qml`，Header 组）和导航栏（`NavigationPanelComponent.qml`，Window 组）在不透明时使用应用的背景色；背景偏深时前景改用 Complementary 配色组，也就是浅色图标和文字。
- **部署**：
  - `build_on_device.py plasma-mobile targets` 构建 `mobileshellstateplugin`、`org.kde.plasma.mobile.taskpanel` 和 `org.kde.plasma.mobile.panel`，由 `plasma/install-mobile-plugins.sh` 安装（新增后两项），`plasma/install-convergent-fix.sh` 一并安装 `WindowColors.qml`；然后重启 plasmashell。
  - 注意：KWin 的声明式脚本共用一个 QML 引擎，卸载再加载 convergentwindows 时仍使用缓存的旧 `main.qml`，新的 `WindowColors` 要等 KWin 下次启动才会生效。本次为了当场验证，另外把 `WindowColors.qml` 作为临时脚本加载了一份（只在本次会话有效）。
- **验证**（手机 WL-0）：
  - 语音助手 App：`dbus-monitor` 看到 `setActiveWindowColorScheme("WL-0", ".../MotoVoiceAssistant.colors")`，状态栏和导航栏变为 #0A0B10，图标和文字为浅色，与 App 连成一体（截屏）。
  - 系统设置 `plasma-settings`：上报 `kdeglobals`，面板保持浅色主题（截屏），说明没有声明配色的应用不受影响。
  - 尚未验证：其他声明了自定义配色的 KDE 应用；电视屏上的面板；KWin 重启后由 `main.qml` 加载 `WindowColors` 的路径。

### 滚动跳回底部

- **原因**：组件没有选错，问题在自动滚动的写法。`contentHeight` 每次变化都调用 `positionViewAtEnd()`；而往上滚时，ListView 会创建新的条目并重新估算总高度，每次估算变化都把视图拉回底部。浮层上拉后的对话列表也有同样的问题。
- **做法**：
  - 采用常见的“跟随到底”规则：只有视图本来就在底部时，新内容才让它滚到最新；用户开始拖动就停止跟随，停下时如果在底部，再恢复跟随。
  - 在 App 里按下说话、打开对话时，回到最新。
  - 离开底部后，说话栏上方出现“回到最新”按钮。
- **验证**：在一段很长的对话里向上拖动两次，等待 4 秒后，视图停在拖到的位置，“回到最新”按钮已出现（截屏）。

## 打开对话不再等 Codex；加载时有状态（2026-09-25）

用户反馈：点一个会话要加载很久，也没有加载动画。

- **实测原因**（手机）：
  - `OpenConversation` 要等 Codex `thread/resume` 完成才返回历史记录。Codex 恢复时要读完整的 rollout，而电脑操作的截图都存在里面：Blender 那段对话的 rollout 有 8.2 MB，打开用了 2775 ms；一段 144 KB 的通话对话只要 132 ms。
  - 可 App 显示的是我们自己存的记录，只有 60 KB、90 条事件。
  - 离开页面时，服务在持有锁的情况下切回语音助手对话（rollout 1.9 MB），`CloseConversation` 要 1.6 s，这期间点开的下一个对话只能排队。
  - App 调服务是异步的，界面线程没有被卡住。但等待期间页面显示的是空对话的“有什么可以帮你？”，标题是“新对话”，内容到了以后整块出现。
- **做法**：
  - 服务：`open_conversation` 从自己的记录读出历史后立即返回，由 `resume_thread` 在后台恢复 Codex 线程，不持有锁。只有实时语音需要这个线程：`start_realtime` 会先等恢复完成，说话的音频本来就在本地排队。
  - 服务：每次打开对话都有编号；已经切到别的对话后才完成的恢复，结果直接丢弃。语音助手对话恢复失败（从没说过话的线程在重启后不存在）时，在后台新建一个线程，并发出 `assistant-reset`。
  - App：从列表把标题带到对话页，标题立刻就对；加载超过 150 ms 才显示“正在打开…”和转圈；内容到了以后停在最新的位置，220 ms 淡入；空对话的界面只在加载完、确实没有内容时出现。
- **验证**：
  - 打开 Blender 对话：2775 ms 降到 73 ms；另一段对话：132 ms 降到 59 ms；关闭：1629 ms 降到 51 ms。
  - 日志中，对话在后台恢复完成（“resumed … in 0.8 s”）；已经切走后才完成的恢复记为 “left alone”。
- **注意**：Codex app-server 看起来是逐个处理请求的。快速连点多个对话时，恢复会排队，实测最后一个用了 5.5 s，但这不再挡住界面。

## 滚动时的闪动：ScrollBar 换成 ScrollIndicator；越界改为拉伸（2026-09-25）

用户反馈：在对话底部慢慢往上拖，会拉出很多空白；这时即使没有松手，列表位置也会突然闪一下。

- **定位过程**（手机，adb 慢速拖动，`screenrecord` 逐帧计算位移）：
  - 录屏中，拉出空白的状态在一帧之内跳回边界（约 110 逻辑像素），前后都是每帧 1–2 px 的平滑移动。
  - 给 ListView 加带时间戳的探针后发现：拖动过程中 `contentY`、`contentHeight`、`originY` 都是连续的；抬手约 25 ms 后 `movementEnded` 就触发了，同一毫秒内 `contentY` 从 3246 跳到边界 3104，本该持续数百毫秒的回弹没有播放。
  - 去掉“跟随到底”的逻辑后照旧；去掉挂在列表上的 QQC2 `ScrollBar` 后，回弹变成 549 ms、47 帧的动画。原因是：`ScrollBar` 是可交互控件，会把自己限制在 0 到 1 之间的位置写回列表，越界时就把内容一下拉回边界。滚动条移到屏幕边缘以后，手指在边缘附近拖动也可能碰到它，所以没松手时也可能闪。
  - 起初怀疑是 ListView 估算可变高度条目的总高度引起的跳变（NeoChat 为此采用 `BottomToTop` 布局）。探针显示本例中这些值没有变化，所以没有采用这个方案。
- **做法**：
  - 对话页换成 `ScrollIndicator`：只显示位置、不接收输入，Qt 为触屏设计的就是它。
  - 对话页和对话列表都设 `boundsMovement: Flickable.StopAtBounds`：内容不再越界，不会拉出空白；越界量 `verticalOvershoot` 用来在对应的一端做最多 8% 的纵向拉伸，松手后随回弹恢复，类似 Android 12 以后的效果。
- **验证**：同样的慢速拖动加松手再录屏，没有单帧跳变；松手后是连续 9 帧的平滑回弹，内容始终停在边界，底部没有空白。
- 测试期间用户正在使用手机，一次录屏拍到了用户自己的操作。这些画面已从 `.work` 和手机上删除，不作为证据。

## 掉帧：静止时也在按 120 Hz 重画（2026-09-25）

用户反馈：QML App 掉帧，感觉卡。

- **渲染方式**：`QSG_INFO` 显示 App 用 GPU 渲染（freedreno FD710，OpenGL 4.6，Mesa 26.3），线程化渲染循环，按 8.33 ms（120 Hz）出帧，不是软件渲染。
- **App 自身的问题**：在一段长对话里，页面完全静止时 3 秒内也画了 390 帧。原因是 `Spinner.qml` 的 `RotationAnimator` 写成了 `running: parent.visible`，这里的 `parent` 指的是外层容器，而不是转圈本身。每张已完成的工作卡片里都有一个看不见但一直在转的转圈，让整个窗口按 120 Hz 不停重画。改为 `running: spinner.visible` 后，静止 3 秒为 0 帧。
- **滚动时的帧耗时**（Qt 渲染循环日志；一次快速滑动加一次慢速拖动）：
  - 界面线程几乎不花时间：布局 0 ms，动画中位数 0 ms。
  - GPU 提交 `render` 中位数 1 ms、p90 3 ms。
  - 时间在 `swap`（交出这一帧并等待合成器）：中位数 6 ms，p90 12–13 ms，最长 28 ms。
  - 结果是约 30% 的帧间隔超过 11 ms。
- **对照**：同样测系统设置（`plasma-settings`），31% 的帧间隔超过 11 ms，节奏与本 App 相同。所以剩下的顿挫属于共享的合成通路，就是 57 篇留下的“显示节拍受宿主帧回调与呈现反馈影响”（SurfaceFlinger 帧间隔 p95 16.7 ms），不是本 App 的问题。
- **Android 侧现状**：空闲时屏幕实际是 30 Hz（SurfaceFlinger 当前模式），宿主在触摸后才申请 120 Hz；系统默认优先级还有一条最高 90 Hz 的投票。滑动开头的几帧因此可能落在较低的刷新率上。这一点尚未量化。

## 深浅两套主题与设计系统（2026-09-26）

用户要求：长按 Home 的浮层和语音助手 App 都要有深色、浅色两套主题，并且注意可读性；用 Claude Design 做浅色设计，并抽象出设计系统。

- **跟随系统**：`SystemTheme`（C++ 单例）读取 `kdeglobals` 的窗口背景色判断深浅，用 KConfigWatcher 监听变化，Qt 的 colorScheme 变化时也会重新读取。App 自己设置了调色板，所以不能靠调色板判断。App 同时切换配色方案文件（深色 `MotoVoiceAssistant.colors`、浅色 `MotoVoiceAssistantLight.colors`），面板跟着变（`KDE_COLOR_SCHEME_PATH`，见上文“面板跟随应用配色”）。实测已打开的 App 会当场跟随 `plasma-apply-colorscheme` 切换。
- **配色**：`Style.qml` 里写了两套值，所有写死的颜色都换成了 token（53 处）。
  - 按 WCAG 计算了每一对文字和底色：浅色最低 4.65:1（`faint` 在 `glass` 上），深色最低 5.57:1。
  - 浮层遮罩加深到浅色 0.78 / 0.84 / 0.90、深色 0.74 / 0.80 / 0.88，背后是纯白或纯黑时 `dim` 仍有 5.2:1。原来的 0.58 在背后是白色时只有 3.24:1。浮层里的次要文字一律用 `dim`。
  - Markdown 链接用的是调色板里的链接色：浅色配色把 ForegroundLink 改为 #1F5AAA（6.2:1），原来的 #2980B9 只有 3.9:1。
- **光**：三个着色器加了 `light` 参数。浅色底上相加的光会消失在白里，所以改成把同样的颜色加深，再按强度覆盖上去（核心用金色）。光丸外圈的光晕以强度作为 alpha；发光文字用深金、墨色和深蓝，不加光晕。
- **设计系统**：Claude Design 的“语音助手设计系统”，从上述代码整理而来：
  - 33 个颜色，浅色、深色两套主题，每个都注明用法和对比度；
  - 15 个字体样式（行高按 QML lineHeight × Noto Sans CJK 自然行距 1.448 换算），以及间距、圆角、尺寸、浮层遮罩不透明度；
  - 13 个组件的说明和静态预览，按 QML 源码还原：LightPill、GlowLabel、TalkDock、Bloom、Spinner、Message、WorkCard、CallCard、PillButton、GlassButton、ConversationRow、PinnedCard、OverlayPanel；
  - 15 个 Breeze symbolic 图标（LGPL-3.0-or-later，颜色固定为 #232629，只作查看用）。
  - 设计系统以 `Style.qml` 为准，改 token 时两边一起改。
- **浅色设计稿**：两块画布各加“深色 / 浅色”两页，并安装了设计系统。浮层的浅色页有聆听、处理中、回答、展开对话；App 的浅色页有对话列表、对话、聆听、处理中、代打电话。画稿中的色值全部取自设计系统的浅色主题。
- **实机验证**：浅色下，列表、对话、浮层截屏都可读，面板颜色与 App 一致；临时切到深色，已打开的 App 当场跟着变，验证后恢复为 Breeze Light。
- **深色设计稿按设计系统重做**（2026-09-26）：两块画布的深色页原来是手写色值，与现行实现有出入（例如 `faint` 0.46、浮层遮罩 0.58 / 0.74 / 0.86、正文 #F2EEE6），现在改用与浅色页同一个生成脚本、取设计系统的深色主题重新生成。
  - 浮层：聆听、免提、处理中、回答、展开对话；光效分解稿保持原样。
  - App：对话列表、新对话、对话、聆听、处理中、代打电话、电视 / 大屏。
  - 大屏稿改为实现里的实际布局：左侧 360 px 列表列（Kirigami 宽模式，没有分隔线），右侧是 680 px 阅读栏的对话，不显示返回键。
  - 生成脚本的浅色输出与已发布的浅色页逐字节相同。
  - 代码无需改动：`Style.qml` 的深色值本来就等于设计系统的深色 token，手机上的构建即 291d2346。临时切到 Breeze Dark 截屏核对，列表、对话、浮层与设计稿一致（深色链接色为配色方案的 #1D99F3），核对后恢复为 Breeze Light。
- **空闲光丸不再发灰**（2026-09-26）：用户在画布上评论浅色列表底部的空闲光丸“没有激活的时候太丑太抑郁了”。
  - 原因：`pill.frag` 空闲时把颜色乘 0.75。深色底上这只是变暗，浅色底上奶油色核心和金色变成土黄、蓝紫发闷。用 numpy 移植着色器复现了实机的样子。
  - 设计系统同期已改为“静候的光”：空闲不减亮度，光晕偏暖，色轮 24 s 一圈；浅色光丸底为暖白 #FFFAF2，外面有一圈淡蓝细边。
  - 实现：
    - 浅色空闲时不再变暗，只向暖白淡一点（`bright` 0.84，浅色时混入 8% 暖白）；深色空闲为原亮度的 92%。
    - 新增 `warm` 参数：空闲时光晕由近到远为金、粉、蓝。
    - 浅色时外缘加 0.12 的淡蓝细环。
    - 空闲光晕强度 0.45 → 0.6。
  - 慢转：共用单例 `IdleClock`，20 Hz 推进，速度为工作时的 1/3.4；只有空闲光丸在显示、窗口可见时才走。实测 App 静置时 SurfaceFlinger 上是稳定的 20 fps。之前空闲是 0 fps，不能回到早先的 120 Hz 空转。这是为动效付出的功耗代价；若要省电，可以把 `IdleClock` 停掉，光丸会静止但仍然明亮。
  - 实机截屏：浅色、深色下列表、对话、浮层的空闲光丸都清亮，没有发灰；几张截屏的色轮角度不同，说明确实在转。
## 第三版：聊天式界面与设计系统库（2026-09-29）

界面按“Agent · App 设计”画布重做，光效设计（Bloom、光丸、发光字）已删除。设计系统抽成独立的 QML 模块 `com.rungic.design`（包 `rungic-design`），控件按状态实现。另外新增打字、附件、转文字、设置页，按住说话改为先存本地再上传。详见[87篇](87-agent-app-redesign.md)。

## 待办

- **输入转写为繁体**：app-server不能设置转写语言，只影响显示；显示时做简繁转换。
- **真人说话实测**：用“语音助手”应用按住说话，验收识别、Agent执行、审批和打断。
- 快捷设置和悬浮控制条上的入口；识别文字的简繁转换；对话标题可改。
- 投屏时调节手机扬声器音量的入口。
- **第3步（已完成，见[60篇](60-computer-use.md)）**：桌面操作经MCP服务`moto-desktop`挂给Codex，界面操作由arc-cua + JEV执行。
- **费用**：实时语音按API用量计费（`gpt-realtime`音频输入$32、输出$64/百万token；mini版$10/$20）。

## 指令归用户所有，改动即时生效（2026-09-29）

- **问题**：
  - 提示词（`agent.md`、`realtime.md`）和技能 `rungic-phone-desktop` 原先是 `/usr/share` 下归 root 的系统文件，用户不能改（用户指出）。
  - 装上新版本后，一个进行中的对话仍按旧指令做事。实例：投屏仍然先投桌面再切助理屏。原因有三：Codex 把开发者指令固定在对话加载的那一刻；在同一个 app-server 进程里，再次恢复只是重新加入对话（实测 `thread/unsubscribe` 之后对话也没有卸载）；包升级只 reload systemd，不重启用户服务。
- **用户可编辑的副本**：
  - `~/.config/rungic-voice-agent/prompts/`（`agent.md`、`realtime.md`）和 `~/.codex/skills/rungic-phone-desktop/`（真实目录，原先是指向 `/usr/share` 的链接）。包里的文件只作默认值。
  - 服务启动时同步：用户没改过的副本跟随新的默认值；改过的保留用户的，新默认值另存为 `NAME.default`。记录在 `~/.local/share/rungic-voice-agent/instructions-seeded.json`。
- **即时生效**：
  - 每个对话在索引里记下它拿到的指令指纹（`agent.md` 和技能文件的内容哈希）。
  - 恢复对话后，以及空闲检查时（每 30 秒，Agent 不忙、没在说话，且 10 秒内没有动静），若指纹不同，就用 `thread/inject_items` 注入一条 developer 消息：`agent.md` 变了就附上新的全文；技能变了就提醒 Agent 重读。
  - `realtime.md` 变了，就在空闲时重启实时会话。
- **实测**：
  - 用户正在用的对话在恢复时收到了当前指令。
  - 测试对话里编辑用户的 `agent.md`（追加标记）后约 30 秒，rollout 中出现带标记的第 4 条 developer 消息，下一轮已经用上。

## 能力清单单一来源；技能拆分；主动提议（2026-10-05）

- **问题**（用户反映 Agent 不知道什么时候该用手机的工具，也不主动）：
  - 电话模式的提示词 `phone.md` 只有任务分派规则，没有能力清单，语音模型无从提议。
  - 按住说话的 `realtime.md` 有一份简短清单，缺短信、手机卡通话、团队，也没分清电视、助理屏和桌面模式。
  - `agent.md` 的清单偏重“怎么做”，说通话只在聊天应用里，和技能里的手机卡通话矛盾，也没提短信。
  - 技能 `rungic-phone-desktop` 包了十几个领域。Codex 打开技能前只看到三行描述，短信和通话的细节常常没读到。
  - 实时语音模型读不了文件和技能，执行端能做什么必须写在它的提示词里。
- **能力清单单一来源**：`agent/assistant/prompts/capabilities.yaml` 每项写名称、能做什么、什么情况下用（附中英文说法）、细节在哪个技能，以及有对外影响的部分（发送、拨打）。改完运行 `python3 tools/agent_capabilities.py render --write`，它把清单写进三份提示词标记之间的段落：`agent.md` 写成“什么时候用、看哪个技能”，`phone.md` 和 `realtime.md` 写成“执行端能做什么、什么时候开任务或提议”。`tools/tests/test_agent_capabilities.py` 在清单过期时失败。这个 yaml 不进安装包，打包仍只装 `prompts/*.md`，运行时照旧读这三份文件。标记是两行 HTML 注释，原样进提示词，约几十个 token。
- **技能拆分**（原有内容只搬不删）：
  - `rungic-phone-desktop`：桌面应用操作（`desktop_*` 工具、窗口、截图、选屏、Blender、团队和方案二入口）。名字不变：用户目录的副本、迁移脚本和改名工具都指向它。
  - `rungic-messages-calls`：短信、手机卡通话、微信消息、语音代发和通话代理，`calls.md` 搬到这里。
  - `rungic-screens`：投屏、助理屏、桌面模式。
  - `rungic-phone-settings`：安卓功能（`rungic-platform`）、电量和存储、通知、录屏。
  - 每个技能的描述写明触发情境，正文先有“什么时候用”一节（照 `rungic-agent-team` 的写法）。包里删掉的 `rungic-phone-desktop/calls.md`，用户没改过的副本由同步一并删掉；新技能目录照旧逐个复制到 `~/.codex/skills/`。技能变动时注入的提醒列出全部技能名。
- **主动**：三份提示词各加一节。明显隐含、可撤销、只在手机上的步骤直接做；用户多半想要的下一步只提议一句，最多一个；发送、拨打、发布、购买、付款、删除只在用户明确要求或答应后做，不自作主张。原有的 read_only/exclusive 分派、停止任务等规则不变。
- **通话矛盾**：`agent.md` 的清单写明短信、手机卡通话和微信通话都可以做，对外的部分要用户点头。
- 新写和搬动时改写的英文按项目的 asd-ste100 技能（简化技术英语）书写。

## Krita 像素画技能（2026-10-05）

**用户要求**：把“用 Krita 画像素画”的做法（参考 pixelglade.net.au 的 Krita 像素画设置一文）包装成技能。

**技能** `rungic-krita-pixel-art`（`agent/assistant/skills/rungic-krita-pixel-art/`）：
- `SKILL.md`：什么时候用（像素画、像素风、精灵图、地块，以及没有指定应用的像素画）和步骤：
  1. 先定尺寸和调色板（6～16 色），写成计划；
  2. 在 Krita 的 Scripter 里载入 `pixel.py`，从后往前分图层画；
  3. 小东西用 `rows()` 逐像素画，过渡用 `dither()`，必要时用 1 像素笔刷手动补；
  4. 用 `save()` 存 .kra、原尺寸 .png 和放大 8 倍的 .png。
  技能里也收了那篇文章里各工具的设置（填充、选区、形状、变换、文字、网格、缩放，都关掉抗锯齿、用最近邻），供手动画时用。
- `pixel.py`：在 Krita 窗口里打开的文档上精确写像素（Python API 的 `setPixelData`，BGRA）。提供 `canvas`（透明画布，放大显示，打开网格）、`palette`、`rect`、`rows`、`line`（Bresenham）、`ellipse`、`dither`、`layer`、`brush`（“u) Pixel Art”预设、1 像素）、`save`（最近邻放大，不产生新颜色）。每一步都会刷新画布，用户看得到画面一步步出来。
- `setup.py`：在 Krita 启动前，把 kritarc 里的 `[python] enable_scripter=true` 打开；Krita 正在运行时不改。
- 能力清单加了“像素画”一项，三份提示词都已重新生成。打包时技能目录里的 `*.py` 装到包的路径下，不复制给用户编辑。

**验证**：
- 在 G100 S 上用 `kritarunner` 无界面运行：Krita 6.0.1 的 Python API 可用；画布、图层、逐像素写入、导出 .kra 和 .png 都正常；最近邻放大 4 倍后颜色数不变（5 色）。像素预设是 “u) Pixel Art”“u) Pixel Art Dithering”“u) Pixel Art Fill”。
- `test_krita_pixel_art`：用模拟的 Krita 检查逐像素写入、裁剪、存盘和最近邻放大；`setup.py` 只加这一行、保留其余设置，Krita 运行时不改。

