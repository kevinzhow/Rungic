# Agent 电话模式（2026-10-02）

本次按用户要求继续使用 `gpt-realtime-2.1-mini` 和 Codex。电话模式持续采集与播放；用户开口先停止播报，已有任务继续。只有明确的停止任务请求或任务卡片的停止按钮取消执行。挂断、Plasma 隐藏、音频或网络连接失败会关闭话音连接，任务调度器继续运行；恢复必须再次点击电话模式。

## 选型依据与边界

本地核对 Codex 0.156.1、0.159.3 的协议与固定源码 rust-v0.159.2：app-server 的 Realtime 封装将任务入口固定为 background_agent/remain_silent，VAD 参数也由内部设置，截断依据收到的 PCM，而不是设备播放位置。`clientManagedHandoffs` 不能阻止原始请求先被后台执行器接走。因此只为电话模式直接连接同一 OpenAI Realtime 模型；原来的按住说话仍使用现有封装。Codex 登录、模型偏好、开发者指令、用量与聊天历史继续复用 resident service。

官方资料：

- [Realtime VAD](https://developers.openai.com/api/docs/guides/realtime-vad)：semantic VAD、create_response 与 interrupt_response。
- [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations)：取消与 conversation.item.truncate。
- [当前模型](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini)。
- [Codex app-server](https://learn.chatgpt.com/docs/app-server)：thread/turn、expectedTurnId、interrupt。
- [GStreamer webrtcdsp](https://gstreamer.freedesktop.org/documentation/webrtcdsp/webrtcdsp.html)：AEC 与本地 voice-activity；核对 1.28.2 实际源码。voice-detection 在 HAVE_WEBRTC1 构建中有效；不能只根据属性存在推断 VAD 可用，也不能依赖已经无效的 frame-size/likelihood 调节。
- [Android AcousticEchoCanceler](https://developer.android.com/reference/android/media/audiofx/AcousticEchoCanceler)：可用性和实际 enable 状态单独核验。

以上服务沿用 OpenAI API 条款。新增 C++ 源码和协议适配采用 GPL-2.0-or-later；Qt、GStreamer、WebRTC DSP 复用系统软件包，未复制上游源码。研究缓存、固定源码哈希、协议和云端探针保存在 `.work/verify/20261001-realtime-feasibility/`；本次构建和验收保存在 `.work/verify/20261002-phone-mode/`。

此前云端探针覆盖 10 个中文场景，延长静默后的独立场景路由均通过；这不是大样本准确率验收。实测 semantic VAD 可延迟较久，故采用应用主动创建回应：等待本地静默和完整最终 ASR，2 秒静默仍未提交时手动 commit。未收到完整转写则不开始执行。模型 original_words 只作说明，执行器始终得到最终转写原文。默认由 ASR 检测实际说话语言，不把桌面界面语言强制当作识别语言。

## 实现

`agent/assistant/session/` 是 Qt/GStreamer 常驻 C++ 协调器。`phone_session.py` 只传递本地 JSON、Codex RPC、历史和前台状态，不承载语音或调度逻辑。D-Bus 提供 StartPhoneMode、StopPhoneMode、SetPhoneMuted、StopSpeaking、FocusTask、StopTaskById、AnswerTask 和 PhoneSnapshot。会话归属显式绑定 conversationId，事件按任务的来源入库，切换界面不迁移事件。

任务有独立 taskId/threadId/turnId。只读任务最多两个并行，并使用 Codex read-only sandbox，禁用配置中的所有 MCP；有修改、桌面或设备操作的任务独占，先到先执行。更正使用 turn/steer 并绑定 expectedTurnId。取消经历 stopping，直到 Codex turn 终止且任务工具不再活动，才显示 stopped。同一完整话语重复调用 start_task 不会重复执行。本版一次话语只更正或取消一个已有任务；第二个不同目标被拒绝，同一目标的重复更正也不会重复发送。需要控制多个已有任务时先询问目标，任务卡片仍可分别操作。已有只读任务执行期间，可以继续开始另一个独立只读任务。

独占任务的桌面 MCP 经过 `rungic-task-tools`。它用私有进程组和进程身份绑定的租约管理 worker；撤销租约或 MCP cancelled 通知只结束这个 worker 的进程组。worker 意外退出或先于后代退出时，仍清理私有进程组；超过退出期限会终止忽略 SIGTERM 的后代。已经独立运行的用户应用不在此组内。旧全局 CUA abort 文件不作用于这些任务。其他自定义 MCP 暂不开放给电话模式的独占任务。

request_user_input 通过任务卡片和 answer_task 处理，绑定任务及服务端请求，不自动选择默认答案；秘密答案只能在卡片输入。重启后不重放 journal 中的任务，查询实际后端状态后恢复显示。服务认证变化会中止原连接的任务状态，要求显式重新发起。

共享媒体链路为：

`Agent/GStreamer → PulseAudio android_communication / android_communication_microphone → rungic-communication-audio → Android CaptureBridge → AudioTrack/AudioRecord`。

Android 使用 MODE_IN_COMMUNICATION、VOICE_COMMUNICATION、AEC/NS 和有线/USB 优先的路由；没有硬件 AEC 时在共享后端使用 WebRTC echo probe/dsp。共享 DSP 的两个 appsink 使用 async=false、sync=false，并先用静音数据协商 echo reference，避免麦克风等待尚未播放的分支完成 preroll。其他标准 microphone 客户端复用同一次采集，不再各开 AudioRecord。静音关闭物理采集，保持播放。后台隐藏会关闭 Android capture sockets。蓝牙不作为本版验收范围。

播放控制具有独立 session/epoch；flush 后旧帧不再进入 AudioTrack。截断采用 Android playback-head cursor，并限制在实际提交的模型音频长度内。当前 PA 管道起点与 Android 100 ms 游标更新仍有边界误差，必须经声学实测，不能把 cursor 读取等同于已证明“听到了什么”。

## 构建、回退与验收

开发机现场核验为 mibook/x86_64；Linux ARM64 包在 macmini 的 rungic-build 容器构建，使用其系统 Surge 代理。G100 为 ZY32M9MRVP，Android 16、portov_cn。G100 S 不用于首轮部署。

Linux 包通过 `rungic_dev.py deploy rungic-voice-agent rungic-plasma-bridges rungic-cua --host macmini` 开发覆盖；回退同一设备执行 `rungic_dev.py reset`。这不是正式发布，不提交或接受 rootfs 快照。开发前基线为 20260930.19，已有 integrity drift（missing_files=308、unowned_usr=5、unowned_etc=9），与本次新增问题区分。

本轮最终开发覆盖为 `20260930.19+dev20261001t190954`：voice-agent `0.510+dev20261001t190954.d24326e.dirty`，bridges `0.358+dev20261001t184851.d24326e.dirty`，CUA `0.358+dev20261001t181635.d24326e.dirty`，Codex 入口 `0.278+dev20261001t181635.d24326e`，design `0.393+dev20261001t181635.d24326e`。设备原来的内置 Codex 0.156.1 不符合当前独立安装入口，按 99 篇安装官方 standalone 0.159.3，保留认证和 `gpt-6-luna` 任务模型偏好；语音仍是 `gpt-realtime-2.1-mini`。补齐 design 是因为当前 ChatEntry 引用了旧安装包没有的 Picture 类型，运行日志已核验修复。

已记录的失败及修正：Ubuntu APT 索引和 ffmpeg 缺失；transient APT unit 未继承代理；通信 FIFO 提前创建使 PulseAudio 拒绝加载模块；批量转发 PA 数据导致队列溢出；问答清空 QJsonObject 后仍持有 QJsonValueRef 导致崩溃。分别补索引/依赖、在部署工具显式传入手机代理、由 PA 创建 FIFO 后收紧权限、按 20 ms 节奏转发、在清空前复制 QJsonValue。后台简报沿用只读模式并禁用配置中的 MCP，避免绕过电话任务的工具租约。

APK 2.29/77 单独构建安装。Java-only 构建复用 G100 已安装 APK 的三个 ARM64 JNI 库和原有 OCR 资产，哈希保存在 native-libs/SHA256SUMS；不重新编译或更换 JNI。原 APK 备份在 `.work/verify/20261002-phone-mode/before.apk`，回退用明确 G100 序列号的 `adb install -r -d`，不清数据。

离线验证：Python 协议适配和现有回归；CMake session-core/session-state 测试；真实原生 MCP worker 的 cancelled/租约撤销/无租约拒绝测试。最终 worker 探针覆盖 cancelled、租约撤销、两者各自的顽固后代、worker 意外退出及无租约拒绝；控制通知和租约测试实测约 2.1–7.8 ms，独立 sleep 进程保持运行。这些结果不代表声学打断延迟。

本轮最终合并 Python 回归 85 项、18 个子测试通过，另有 1 项因宿主缺少 Debian 工具而排除。宿主缺少 dpkg-parsechangelog 的那一项在 Macmini 使用真实 Debian 工具另验通过。原生两个测试目标通过，覆盖任务限额/公平调度、完整转写、重复调用、过期回复、连接前半句、问答身份、停止后的迟到问答、挂断继续执行和协议故障关闭。最终开发部署 APT Installed/Candidate 一致，三个用户服务 active，SSH socket 仍 enabled。

源码同步补充：拉取 `d0b15620` 的 21 个上游提交后，合并保留音频跟随服务和 communication 服务；提交前 94 项 Python 回归、18 个子测试通过，1 项 Debian changelog 检查沿用此前构建机通过的证据。包清单、脚本语法、差异和 Git 源码范围检查通过。同步后的源码未重新部署，以上开发覆盖版本和下述实机证据仍对应同步前的候选。

实机分层证据：

| 检查 | 结果与验收边界 |
| --- | --- |
| Realtime + 生产端点判定/路由 | 同一会话输入 10 个中文 TTS 样本。最终轮讨论、附和、停止播报、否定停止和指定取消均无错误开始/取消；文件统计与天气以两个只读任务并行，0.8 秒句中停顿未提前执行。找今天照片的样本仍追问位置，没有自动开始；严格预期路由为 9/10，不能宣称达到 95% 大样本目标。 |
| ASR 保真与首音 | 实际执行文本取最终 ASR，保留“不修改/先别移动”和最后的纠正。修正探针后样本的云端首音约 2.2–5.7 秒，部分超过目标；旧探针曾把前一回复计入，最终轮已限定当前实际接收的音频。六场景最终轮为 2.240–5.073 秒。云端 delta 不是实际扬声器首音，目标仍未通过。 |
| 连续六场景与执行去重 | 最终云端轮讨论不执行，统计与天气正确并行；模型给天气重复调用 start_task 两次，实际只新增一个天气任务。更正只发送到统计线程，取消统计后天气继续，句中停顿后的“先别移动”完整保留；全部 RPC 均发生在输入结束后。此前轮次仍出现多目标更正或不必要的优先级追问，不能用最终小样本代替大样本质量验收。 |
| 长连接 | 合成 PCM 和静音输入的 Realtime 连接保持 1800.004 秒，configured/connected 均为 true；是传输稳定性探针，不是 30 分钟真人连续双工对话。 |
| 原生 Agent 与 Android 音频 | 最终共享 DSP 修正后，真实 Realtime 回应进入 AudioTrack，playedFrames=960、writtenFrames=6720；停止播报使 epoch 递增且游标归零，无播放时恢复原生 GStreamer 采集成功。静音释放物理麦克风，挂断后 communication 与 microphoneActive 均为 false。真人音质和回声效果尚未验收。 |
| PulseAudio 标准接口 | pacat/parecord 检查单 owner、静音开始仍播放、恢复采集、epoch 递增与关闭清理。1.5 秒收到 72,000 帧；该轮 PCM 全零，证明传输连续性，不证明真人声音可识别。flush 约 108/109 ms 为控制确认，不能当作声学 P95。静音后 microphoneActive=false；停止客户端并关闭会话后模块为零、通信和物理采集均 inactive。 |
| 真实 Codex 执行 | 只读终端等待 20 秒并计算 137×29。挂断时仍 running，之后 completed，结果 3973。另一个等待任务从 stopping 到后端确认 stopped；不是仅验证发送 interrupt 成功。 |
| 前后台 | 用 Android Settings 实际遮住 Plasma，话音关闭而任务仍 running；返回后没有自动恢复，任务可明确停止。G100 的 Home 键返回同一个入口，不能用它当作真正隐藏测试。 |
| 界面 | 真实聊天窗口显示电话入口、任务结果与状态；共享设计状态图库离屏渲染通过，截图保存在本轮 .work。 |

用户随后要求低音量：G100 的 `android` 和 `android_phone` sink 为 10%。新 communication sink 在创建时继承 android_phone 音量，不因开始通话恢复为 100%；Android 系统音量命令未观察到实际改变，因此这里记录的是已核验的 Rungic/PulseAudio 输出设置。

证据目录中的 final-dev.json 核对最终 Installed/Candidate；native-audio-final.log、integration-final.log、foreground3.log、task-tools-verified.log、cloud-capacity.log 和 cloud.log 分别对应原生音频、真实执行、前后台、工具清理、小样本云端路由与长连接。仍须验收真人双工音质/回声、声学打断和首音、真实跨对话使用与长期交互稳定性。目标仍为：有效语音到实际静音 P95≤200 ms、完整话语到首音正常 P95≤2.5 s/兜底≤4 s；意图大样本≥95%，关键错误开始/取消为零，以及连续 30 分钟会话。这些指标不能用当前少量云端或离线样本代替。

## 故障：打开 App 提示 “Phone session service stopped; reopen the app”（2026-10-03）

**现象（用户报告）**：一打开 Agent App 就提示这句话。服务日志从 11:09 起，每次打开对话时调用 `PhoneSnapshot` 都失败；手机上 `rungic-voice-agent.service` 一直在运行（从 03:30 起），但它的子进程 `rungic-agent-session` 已经不在了。

**原因（日志、源码，并用协调器实测确认）**：
- 03:34:47 打开新对话，服务创建了 `PhoneSession`。03:35:04 Agent 回合开始（`turn/started`），`rungic_voice_agent.py` 直接调用 `self.phone._write({"type": "command", "method": "ExternalBusy", ...})`，没有带 `id`。回合结束时（`turn/completed`）也有同样的一处。这两处是 `c40326e`（2026-10-02，Agent 忙时保持唤醒）加的。
- 协调器对每条指令都回复 `{"type": "reply", "id": o["id"], ...}`。请求里没有 `id` 时，Qt 会把值为 undefined 的键省掉，回复就成了 `{"result":{"ok":true},"type":"reply"}`。在手机上用临时 `HOME` 单独运行 `rungic-agent-session` 已复现：不带 `id` 的 `ExternalBusy` 得到不带 `id` 的回复，带 `id` 的原样带回。
- `phone_session.py` 的读取线程执行 `self.pending.get(message['id'])`，抛出 `KeyError: 'id'` 后退出；退出时的 `finally` 主动 `terminate()` 了协调器。
- `PhoneSession` 在服务里只创建一次，没有重建。从那以后每条指令都报 “stopped; reopen the app”，而重开 App 并不能恢复，因为会话属于常驻服务。
- 所以只要 `PhoneSession` 已经存在，Agent 的下一个回合就会把它弄坏，每次都能复现。

**修法**：
- `PhoneSession.post()`：只发送、不等回复，但同样分配 `id`。`ExternalBusy` 改用它；协调器已停止时，这个提示直接丢弃，不把异常抛进 Agent 的回合处理。
- 读取线程逐行处理：解析不了或处理出错的一行记进日志（`phone session: dropped a message …`，带这一行的开头）后跳过，不再结束会话、不再杀协调器。
- `phone_session()` 发现协调器已退出时，记日志后重建，并带上原来任务的 Codex 线程对应关系；提示文字改为 “try again”。
- 测试：`tools/tests/test_phone_session.py` 新增 4 项（无 `id` 的回复和非 JSON 行被跳过，之后的回复和事件照常处理；`post()` 编号；协调器停止时 `post()` 不抛异常；Agent 代码里不再直接调用 `phone._write(`），共 9 项通过。

**部署**：待用户同意。部署 `rungic-voice-agent` 会重启 `rungic-voice-agent.service`、`rungic-voice-overlay.service`，并关闭正在运行的 Agent App。

## 故障：通话中 Agent 说话一卡一卡（2026-10-04）

**现象**：用户反映，和 Agent 通话的整个过程中，Agent 说话都一卡一卡。

**证据（G100 S，只读）**：AudioFlinger 的轨道记录里，Rungic APK 的通话播放轨道（单声道 48 kHz，`USAGE_VOICE_COMMUNICATION`）欠载很多：

| 时间（手机） | 播放时长 | 欠载 |
|---|---|---|
| 10-03 13:19 | 11.3 s | 1.17 s（10.3%） |
| 10-04 10:47 | 5.3 s | 1.12 s（21.3%） |

同一时间的 logcat 有 `BUFFER TIMEOUT: remove track … due to underrun` 和 `AudioTrack … disabled due to previous underrun, restarting`。作为对比，平时语音回复用的媒体轨道（`phone-output`）是 7.8 s 里欠载 0.16 s（2.1%）。

**原因**：两层都在给播放定节奏，而且都会落后。
- `shared/media/communication-audio.cpp` 的 `readOutput()`（bd35b7a，2026-10-02）：
  - 每次从 `android_communication` 的管道最多读 1920 字节（20 ms），然后固定停 20 ms 再读。
  - PulseAudio 的 pipe sink 往管道里写多快，完全取决于这边读多快，所以每一轮实际是 20 ms 加上事件循环的延迟。
  - 结果是送出的数据一直比播放慢。
- 会话的 `Session::tick()` 每 20 ms 只推 20 ms 的回复，而且只在 Android 缓冲不到 100 ms（`written − played < 4800` 帧）时才推。定时器晚了也不补。
- Android 的通话轨道只有 100 ms 缓冲，很快被吃空。

**第一次修复失败（同日）**：只改了服务端的节奏（按时钟读，始终让 Android 领先 80 ms），以开发覆盖装到 G100 S 后，通话只剩开头一声，之后一直静音。
- 原因：Android 一直保持着 80 ms 的余量，再加上播放位置 100 ms 一跳，会话看到的“缓冲”常常超过 100 ms，于是停止推送。
- 会话一停，PulseAudio 就往管道里填静音，服务照样按时把静音送给 Android，Android 一直显得满，会话就再也不推了。
- AudioFlinger 的记录是：播了 7.26 s，欠载为 0，内容却是静音。
- 处理：回滚到旧版本，用户确认回滚后有声音但仍卡。
- 当时的系统测试没发现：它直接用 PulseAudio 播放，绕过了会话的门槛；替身也没有按实时播放、回报位置。

**修法**（两处一起部署，缺一不可）：
- **服务**（`rungic-plasma-bridges`）：按时钟读管道。允许送出的量 = 从开始到现在应播放的量 + 80 ms − 已送出的量，一次最多 20 ms。管道空了以后，放弃欠下的部分；flush 后重新计时。
- **会话**（`rungic-voice-agent`）：不再看 Android 的缓冲（里面有 PulseAudio 的静音），改看“这段回复已推出的量 − Android 已播放的量”（`Session::chunksDue`）。领先不到 300 ms 就补足差额，一次 tick 最多推 8 块，晚了的 tick 下一次补上。打断时这些都会被 flush 清掉，打断的速度和“听到多少”的计算不受影响。

**验证**：在 Mac mini 的无头系统测试 `communication_audio` 里，用真实的 `Session`（`tools/system/call_playback.cpp`）、真的 PulseAudio、通信音频服务，以及按实时播放、缓冲 100 ms、位置 100 ms 一跳的 Android 替身，播放 3 s 的回复：

| 组合 | 结果 |
|---|---|
| 旧服务 + 旧会话（原状态） | Android 等数据共 900 ms（卡顿） |
| 新服务 + 旧会话（第一次修复） | 每 100 ms 的音量为 `[0, 3569, 0, 0, …]`，即开头一声后静音，回复剩 101760 字节没播 |
| 新服务 + 新会话 | 3 次运行，3.0–3.1 s 全部听到，Android 等数据 17–20 ms（含开头和结尾） |

另有两项：持续写满管道时，送达始终领先播放 29–63 ms，没有漂移；不静音时（经过回声消除参照），2 s 正弦波逐字节到达。`phone_session_units` 里有 `chunksDue` 的单元断言。手机上的复核见下文“部署”。
