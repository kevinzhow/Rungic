# 默认由 Codex 操作桌面，保留 API 执行器

2026-10-03。用户指定：开发手机部署当前改动，computer use 默认使用 Codex，API 方式保留。

## 当前功能与验收范围

| 功能 | 当前行为 | 本轮验收 |
|---|---|---|
| 默认桌面操作 | 当前 Codex 登录与所选模型逐步看图和执行动作，计划、进展和结果在来源对话显示 | 最新 G100 覆盖由常驻 Agent 点击 147 × 31、再次截图确认 4,557；无头 KWin 的 MCP 点击链路通过 |
| API 备选 | 显式 Luna API 执行器，保留旧 API 及 AT-SPI/OCR 选择 | Luna 只读识别同一计算器结果、恢复 Codex 通过；未验证 API 模式的完整多步任务 |
| Agent App 设置 | 桌面操作方式、Codex 登录、OpenAI API key 分开；任务或通话进行中拒绝切换 | QML 点击和错误保留通过；G100 任务、SIM 通话、空闲电话模式均拒绝切换；未验完整页面触控流程 |
| 语音消息 | Codex 决定录音和发送动作，本地辅助程序管理路由和一次播放，TTS 使用 API key | ARM64 三项原生生命周期及真实 PulseAudio 录音、路由恢复通过；G100 无已登录微信，实际发送待验 |
| 电话界面 | 默认 Codex 执行应用内拨号/挂断和画面判断；SIM 由 Android Telecom 处理，通话音频与调度保留 API | 10000 实际接通、挂断与模式保护通过；不代表微信界面的 Codex 拨号/挂断或双向音质验收 |

最新开发覆盖为 `20260930.19+dev20261003t085900`，不是正式发布或所有机型的验收。G100 S 日常机未更新。下文保存实现、部署和逐次失败/修正证据。

## 执行与认证是两个选择

- 默认 `codex`：Agent 当前的 Codex 线程使用所选模型，看 `desktop_screenshot`、调用 `desktop_act`，逐步判断和核验。MCP 只提供本地截图、输入、窗口与音频能力，不为桌面判断另发 Responses 请求。模型、登录、进展和实际观察到的 Codex token 用量沿用 Agent。
- 显式 `luna`（CLI 别名 `api`）：保留现有 `desktop_goal` 和语音消息执行器，独立通过 OpenAI API key 调用 Luna。原来明确保存的 `luna`、`atspi` 选择不被覆盖；没有配置或配置无效时默认 Codex。
- Codex 可以登录 ChatGPT，也可以使用 API key。选择 Codex 执行不等于切换为 ChatGPT 计费；登录页负责登录，桌面操作页负责执行方式。语音、转文字和 TTS 继续使用 API key。

这复用的是 Linux 的截图、RemoteDesktop portal、KWin 和 Codex MCP 接口，不依赖 Codex macOS/Windows 桌面插件。原始 API 方案、截图范围和上游调查见 [68](68-luna-computer-use.md)；登录与计费入口见 [101](101-codex-sign-in-and-api-key.md)。

## 实现

`rungic_cua/mode.py` 管理持久化选择；`screen.py` 共享截图和输入执行，`luna.py` 保留 API 决策循环。Codex 模式隐藏且拒绝 `desktop_goal`、旧语音执行器和 AT-SPI/OCR 的 API 工具，避免任务无提示地回退为独立 API 决策。

Agent App 设置新增“桌面操作”，显示 Codex（默认）和 Luna/OpenAI API，并说明登录与费用归属。`SetDesktopMode` 在任务、后台线程或通话仍在进行时拒绝切换；空闲时保存并替换 app-server，使 MCP 工具列表更新，不切换认证。OpenAI API key 单独成组，不再藏在语音设置里。

普通桌面任务留在原来的 Codex 对话。代打电话的拨号/挂断属于服务生命周期，由现有认证 app-server 创建短期 Codex 线程，继承模型并绑定应用桌面；只启用桌面 MCP。复用 native `rungic-task-tools` 的进程组和租约，停止时先撤销租约再 interrupt，完成后 unsubscribe；进展送回通话来源对话。连接判定仍看实际画面，接受拨号请求不代表接通。

Codex 模式的语音消息用 `desktop_voice_recording` 管理音频，Codex 自己点击录音和发送。C++ `rungic-voice-recording` 等真实 Linux 麦克风路由后才播放，播放只尝试一次；失败、EOF 或超时释放路由。TTS 使用 API key。它不点击界面，不替用户发送。按住说话类流程尚未验收。

## 离线与构建核验

- Python：配置默认值、API 选择保留、模式工具边界、忙时切换拒绝、电话辅助线程认证/模型复用及租约清理；并回归模型、app-server、phone session、后台简报、工作空间和登录。
- ARM64 native helper：无录音时拒绝播放、一次播放及 EOF 清理、失败后不重复播放。用假音频进程，不访问硬件或发消息。
- 设置页离线渲染 Codex、API、无 key、忙时错误；截图在 `.work/verify/20261003-codex-desktop/ui/`。
- ARM64 包在 Mac mini 的 `rungic-build` 构建，不向手机安装编译依赖。

## 开发部署与实机

验收记录继续写在本节。设备为 G100 / portov_cn / ZY32M9MRVP，Android 16、Ubuntu ARM64；G100 S 日常机未部署。基线发布 20260930.19；使用 `rungic_dev.py` 开发覆盖，不生成正式发布或提交 rootfs 快照。

首轮构建成功，安装前的直传检查失败：开发 G100 没有 buildhost SSH 密钥。已在手机生成密钥，Mac mini 只授权其公钥，限定来源地址和 `rungic-transfer` 强制命令。两条直连路径（10.77.0.20、192.168.5.45）读取同一构建机文件得到相同 SHA-256；私钥留在手机，产物从 Mac mini 直接到手机。

证据目录 `.work/verify/20261003-codex-desktop/`；失败部署记录 `.work/dev-deploy/20261003-135335-deploy/`，后续部署记录 `.work/dev-deploy/20261003-140208-deploy/`。


首轮六包覆盖 `20260930.19+dev20261003t060208` 安装和重启通过，apt Installed = Candidate。完整性摘要与部署前一致：changed_files=0、release_mismatch=0，已有 308 个缺失语言文件和 unowned_usr=5 / unowned_etc=11；未新增漂移。SSH socket 继续启用。

首个常驻 Agent 验收正确调用了启动和截图工具，任务计划/进展回到原对话，但截图连续返回 Cancelled；Agent 停止而未假称完成。调查 KWin 6.6.6 实际源码和 supportInformation：截图需要 EGL 后端，手机工作区实际为 QPainter。此次安装的新工作区脚本默认 virtual，而设备仍是旧 `kwin +rungic8`，缺少已有 `virtual-render-device.patch`。这不是 Codex 登录或模型问题。补齐当前 KWin 补丁队列的五个配套包，再验收；不改应用或添加另一条截图通道。原方案调查与此前验收见 [research/97 §10](research/97-headless-agent-work.md#10-方案-c-第一步无头工作区实机实验2026-10-02-13551410)。


补齐 KWin 开发覆盖 `4:6.6.6-0ubuntu0.1+rungic9+dev20261003t060922.b8d3251`，整体覆盖变为 `20260930.19+dev20261003t060922`。增量构建 29 秒，直接同步五个二进制包。主手机会话已经重启，独立工作区按当前设计不会跟它一起退出，所以另重启验收工作区 1 以加载新 KWin，截图随即恢复。

部署工具的 session-ready 步骤出现已知误报：它比较 `pidof kwin_wayland` 的全部 PID，其中独立工作区的旧 PID 持续存在，被判成“旧桌面尚未退出”。主手机 KWin/plasmashell 都是 active；同一问题已记在 [research/97 §20.1](research/97-headless-agent-work.md)。本轮记录该限制，不把工具最终 result=ok 当作完整 UI 验收。


Codex 的常驻 Agent 验收已通过：新对话 `01a10066-ad2e-73c3-b380-36ce35558741` 由当前模型 gpt-6-luna 通过桌面 MCP 启动 Kalk、读取截图、点按 137 × 29，并重复截图确认显示 3,973。原对话显示计划、逐步进展、结果和 token 事件增长；这是本机观察值，不是账单。截图 `.work/verify/20261003-codex-desktop/kalk-final.png` 已人工核对。进行中调用 SetDesktopMode 返回忙时错误，未改变模式。第二轮失败是工具实际路由到尚未加载新 KWin 的工作区 0；确认该区没有应用窗口后重启它，0/1 都报告 OpenGL ES + FD710，随后同一测试通过。保留两个失败日志，避免把一次 CLI 截图成功当作完整路由验收。

API 备选的首次实机只读验收发现本轮重构遗漏：模型工具常量 TOOLS 被移到共享 screen 模块，luna 循环没有引用。已把 API 工具定义放回 luna 模块，增加真实 ComputerUse 类的隔离请求/截图续接测试，并重新通过 106 项 Python 回归。随后仅重建和部署 rungic-cua。所有 API 实机验证结束都恢复 Codex，未改变现有登录或 key。


最终 API 备选验收通过：`SetDesktopMode api` 返回 luna，独立 Responses 循环 3.2 秒只读识别同一 Kalk 显示 3,973，并指出当前没有显示算式；与截图一致。`SetDesktopMode codex` 恢复成功，Setup 保持 account.type=apiKey。最终开发覆盖 `20260930.19+dev20261003t061714`，cua 版本 `0.358+dev20261003t061714.b8d3251.dirty`，其他覆盖沿用上述记录。最终 apt 核对通过，完整性摘要仍与部署前相同。证据 `api-mode-final.log`、`dev-final.json`、`final-state.log`；安装的 voice-agent、server、mode、luna、screen 与工作区 SHA 一致。

本轮实机范围是默认配置、常驻 Codex 看图/输入/结果验证、来源对话进展和用量事件、忙时切换拒绝、API 只读循环和恢复 Codex。设计系统 ChoiceRow/ListRow/RadioMark 在手机离屏出图通过；完整新设置页为本机四状态渲染。没有向真实联系人发语音或拨号，没有验证完整电话或按住录音；原生音频生命周期的假进程测试不能代替这些验收。G100 S 日常机未改，rootfs 快照未提交。代码在 `feat/codex-desktop-default` 分支，工作区改动未提交，部署可经开发覆盖 reset 撤销。


## 同步最新 main 后的继续开发（2026-10-03）

本机 mibook / x86_64 经 SwiftWire SOCKS5h 拉取 `origin/main`，将 `feat/codex-desktop-default` 快进到 `331bbdea`。同步前的 33 个改动文件保存在 `.work/verify/20261003-codex-desktop-continue/before-pull.tar.gz`，Git stash 也保留。恢复后合并文档索引、CUA 包依赖和工作区测试三处冲突；保留上游补充的 python3-pypinyin 依赖、独立桌面行为和测试。

本轮继续完成：

- 接入 main 新增的质量治理：声明 mode、screen、设置页、录音辅助程序和测试的功能归属，分类本文，更新默认 Codex / 显式 API 的体验，重新生成功能总览。严格检查无新增结构性警告，原有 44 条 device-only 欠账不变。
- 上游电脑操作测试改为显式选择 API 备选，截图检查使用共享 screen 模块；配置文件在 mode 模块内替换为测试私有文件，避免改写用户选择。拨号测试使用新的决策执行入口。
- 增加设置页真实 QML 点击检查：没有独立 API key 时不能选择 Luna，等待切换结果时不能连点，忙时拒绝保持原选择并显示错误，成功才更新选择。
- 修复电话模式空闲或启动中允许切换执行方式的问题。`restart_server` 会调用 BackendReset，故没有运行任务也会打断电话模式；现在 sessionId 存在或 phone_starting 时拒绝切换，保留原模式和连接。新回归检查覆盖这两种状态。
- CLI 的无效模式仍以清楚的错误退出，不输出 Python traceback。

验证证据在 `.work/verify/20261003-codex-desktop-continue/`：

- `targeted-final.log`：桌面执行方式、API 循环、共享截图、拨号入口和 Agent App 的 57 项测试、7 项子测试通过。
- `ui-interaction.log`：设置页交互和历史滚动两项单独检查通过。历史滚动测试此前没有计入 ListView 的 originY，估计高度使 originY=-2 时误判未到末尾；修正测试的内容坐标计算，未改动聊天页实现。
- `offline-final.log`：完整 runner 的 Python 部分为 864 passed、15 failed、6 skipped、506 subtests passed；补丁队列、Java 和 shell 检查通过。pytest 输出汇总后，在本机 Python 3.15 / GI / Qt 混合进程的退出阶段发生段错误，原因尚未定位，整套检查不算通过。Qt6Core 开发文件本机未安装，原生录音 helper 的三项测试本轮未重跑；其源码未变，不能将旧 ARM64 假进程测试称为本轮实机验收。
- `baseline.log`、`baseline-import-order.log`：在干净 `331bbdea` 工作树逐项对照，全部 15 个失败复现。其中缺少 dpkg-parsechangelog / apt-get、Debian 依赖检查、libx264；另有 director 替身请求、linux-vdso 符号化、输入探针，以及完整模块导入顺序下 ThreadPoolExecutor 为 lazy_import 的六个失败。本轮未扩大范围修复这些上游或宿主环境问题。

本轮只同步、整合和离线检查。没有重新部署手机、拨打电话或发送语音，没有更换账户和 API key。新的电话模式切换保护仍需后续开发部署验收；此前 G100 的计算器和 API 只读验收仍为上一节记录的版本。

## PR 前补充验证（2026-10-03）

用户要求补齐上一节明确未完成的验证并提交 PR。生产代码基于 `333b931e` / main `331bbdea`；本次新增测试归属与 `covers` 仍按 quality 清单维护。证据目录 `.work/verify/20261003-codex-desktop-pr/`。

### 无头 Linux 与原生音频

Mac mini 现场为 Darwin ARM64，Surge HTTP/HTTPS 6152、SOCKS 6153；运行中的构建容器为 `rungic-arm64-host:5dce1a9f1dbc`。系统测试在独立 Ubuntu ARM64 容器 `rungic-system:e14d334e6523` 运行，不使用手机。

- `cua_desktop`：默认 Codex 的 `desktop_screenshot` → `desktop_act` 实际经 KWin 向 GTK 应用点击，使用窗口图片坐标，返回下一张图片；未截图时拒绝动作，Codex 模式拒绝独立 API goal。既有启动、不重复实例、双击、拖动、滚动、键盘、弹出菜单、窗口管理回归通过。GPU-less KWin 的截图像素使用明确的替身，输入和窗口没有替换；真实截图仍由下述 G100 验证。
- `voice_recording`：现场构建真实 Qt C++ helper，三项原生生命周期检查全部运行、零跳过。另运行真实 PulseAudio、生产 `rungic-audio-route`、pacat 及录音进程，录到完整一秒正弦音（非硬件麦克风），关闭 helper stdin 后实际录音流回到原 source。三项负面/一次播放测试使用假音频进程；这项集成使用私有 PulseAudio 的 null sink，不是微信或人工听感验收。
- `assistant_app`：当前树的 Qt 应用在双输出 KWin 与 D-Bus 接口替身中构建通过，Home 浮层和单实例回归通过；其旧系统用例不检查新设置页，新设置页的真实点击另由 QML 单元检查覆盖。
- `phone_session_units`：电话会话的 C++ 核心及状态测试现场构建、执行通过。

四项系统测试原始 JSON：`.work/system-tests/20261003-165741/results.json`。补充真实 PulseAudio 后的最终录音记录：`.work/system-tests/20261003-170837/results.json`，3.7 秒通过；强化 Codex 点击误差约一像素的断言后，桌面测试最终记录 `.work/system-tests/20261003-171323/results.json`，7.9 秒通过。单元回归 `test_desktop_mode`、`test_cua_tools`、`test_voice_message`、`test_assistant_app` 为 **47 passed、5 subtests passed**（`unit.log`）。上一节完整 runner 的 15 个已复现 main 失败和退出崩溃仍保留，没有用这轮局部通过覆盖它们。

### G100 开发覆盖与实际桌面

目标明确固定为 G100 `ZY32M9MRVP`（portov_cn）；G100 S 日常机未操作。现场核对其容器 ARM64、192.168.5.69、手机代理 `192.168.5.45:6152`，SSH socket 仍 enabled。仅以 `rungic_dev.py deploy rungic-voice-agent rungic-cua --host macmini` 更新两个相关包；构建产物从 Mac mini 直传手机。未做正式发布、APK 更新或 rootfs 快照提交。

覆盖版本 `20260930.19+dev20261003t085900`：voice-agent `0.510+dev20261003t085900.333b931`，cua `0.358+dev20261003t085900.333b931`。重启 voice-agent、voice-overlay 和助理应用，保留主桌面与独立工作区。部署记录 `.work/dev-deploy/20261003-165900-deploy/`：result=ok，apt Installed=Candidate，changed_files=0、release_mismatch=0；已有 missing_files=308、unowned_usr=5、unowned_etc=11，与部署前一致，完整性摘要仍是 drift，不称整机无漂移。已安装服务脚本与 MCP server SHA-256 和当前源码一致。

常驻 Codex 使用 gpt-6-luna，在专用验收对话中实际启动 Kalk、看截图、点按 **147 × 31**，独立截图显示 **4,557**，计划/进展/结果回到来源对话。`resident-codex.log` 与人工核对的 `kalk-workspace.png` 保存证据。验收时拒绝 SetDesktopMode(luna)，没有改写 Codex 模式。

### 10000 通话及空闲电话模式保护

用户明确指定 **10000**（DM `47652ce7`）。只使用 SIM 通道做短暂通话，不按业务菜单、不转人工、不查询或办理业务。Android Telecom 接受请求后，实际 call state 依次为 dialing → ringing → connected；接通约 2.6 秒后发送 hang-up，随后收到 call-ended（hung up），额外核对 phoneState=0、calls=[]。接受拨号请求本身没有被计为接通。

通话中 SetDesktopMode(luna) 返回拒绝错误，结束后 mode 仍 codex、callPhase 为空。日志 `call-test.log`。这是 SIM 控制和恢复验收，**不是应用内 Codex 拨号界面或双向音频/音质验收**。系统此前记录的“保持安静仍可能自动开场”限制仍可观察到：本次有自动开场转写，不能宣称全程静默；没有操作任何业务菜单。

另外实际启动一个临时电话模式会话后设为静音，等待 connected；在没有运行任务的状态调用 SetDesktopMode(luna) 被拒绝。一秒后原 sessionId、connected 和 muted=true 都保持，最后停止该测试会话。`idle-phone.log`；它验证了本次修复的空闲电话模式不会因修改执行方式被 BackendReset 打断，不代表人工对话验收。

### 尚未完成与恢复

G100 当前 `/opt`、应用包与 Flatpak 中没有微信，也没有已登录微信窗口。**真实微信文件传输助手的录音按钮选择、播放后发送、消息时长/音质，以及应用内电话的 Codex 拨号/挂断未验**；没有给真实联系人发送消息，没有将原生假进程或 PulseAudio 正弦音测试算作发消息成功。用户随后明确“微信就暂时不验收了”（DM `bfe17756`），本轮将这些实机项暂缓，并在待审 PR 保留该边界。

开发覆盖可经 `rungic_dev.py reset rungic-voice-agent rungic-cua` 回到基线发布对应包；这会重启相关服务，本次未执行。其他既有覆盖、SSH 和 rootfs 快照未动。测试结束无运行任务或通话，桌面方式保持 Codex。验收对话已按接口尝试回到先前助理对话；Kalk 保留在 Agent 工作区，未操作用户主桌面窗口。
