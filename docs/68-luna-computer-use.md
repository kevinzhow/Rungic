# 方案一：GPT-6 Luna Computer Use（看画面决定点哪里）

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-25。用户要求：电脑操作改用 GPT-6 Luna 的 Computer Use，直接从画面决定操作，不依赖无障碍树、OCR 等；原有的一整套作为“方案二”保留，但不作为默认。测试用例：在微信里给周凯文发一段语音消息。

> 2026-10-03：当前默认改为 Codex 直接看图并调用本地动作，独立 Luna API 方案继续作为显式选择保留；下文记录 2026-09-25 的方案与验收，当前实现见 [106](106-codex-desktop-operation.md)。

## 调研

- **OpenAI Computer Use**（`developers.openai.com`：`guides/tools-computer-use`、`-integration`、`images-vision`，2026-09-25 读取原文）：
  - 工具定义为 `{"type": "computer"}`，没有分辨率参数。
  - 模型返回 `computer_call`，其中 `actions` 可以批量给出多个动作：`click`（`button`：left/right/wheel/back/forward）、`double_click`、`drag`（`path`）、`move`、`scroll`（`x`、`y`、`scroll_x`、`scroll_y`，示例按每 100 折算一格滚轮）、`keypress`（`keys`）、`type`（`text`）、`wait`、`screenshot`。鼠标动作可以带 `keys`，表示按住的修饰键。
  - 截图以 `computer_call_output` 回传，类型为 `computer_screenshot`，指定 `detail: "original"`，通过 `previous_response_id` 续接对话。第一次调用可能只要一张截图。
  - 图片格式支持 PNG、JPEG、WEBP 和非动画 GIF；`original` 细节保留原始尺寸，每张上限 30,000 个 patch。
- **gpt-6-luna** 的模型页在 Responses API 工具列表中标明 Computer use 为 Supported；价格为输入 $0.10、输出 $0.50（每百万 token）。
- **Codex 自带的 Computer use**（Codex 0.156.1 源码 `b412ff32`、官方文档 `codex/computer-use`）：
  - `features.computer_use` 只是管理员层面的准入开关。
  - 真正的能力是 ChatGPT/Codex 桌面应用附带的插件（一个名为 `cua_repl.js` 的 MCP 工具，底下是 JS 运行环境里的 `cua` 对象），只支持 macOS 和 Windows，不在开源的 codex-rs 里。Linux 上不能直接使用。
  - 它说明 Codex 做 Computer Use 的方式就是 MCP 工具返回截图图片，所以 moto-cua 同样可以把截图交给 Codex 的主模型。

## 设计

- **两个方案**（`moto-cua plan [luna|atspi]`，保存在 `~/.config/moto-cua/plan`，默认 `luna`；切换后需要重启语音服务，Codex 才会重新读取工具列表）：

  | | 方案一 `luna`（默认） | 方案二 `atspi` |
  |---|---|---|
  | 决策 | gpt-6-luna 看截图（`computer` 工具） | JEV 读无障碍树 + OCR |
  | `desktop_goal` | `luna.py` 的循环 | moto-clicker（64 篇） |
  | 单步工具 | `desktop_screenshot`（图片）、`desktop_act`（同样的动作格式） | `desktop_observe`、`desktop_run`、`desktop_find_name` |
  | 语音消息 | 模型在画面上找录音和发送控件 | 按控件名（62 篇） |

  窗口管理（`desktop_windows`、`desktop_launch`、`desktop_activate`、`desktop_window`，走 KWin）和底层输入（RemoteDesktop 门户、KWin 文字提交）两个方案共用。方案一只在方案二需要时才打开无障碍总线。
- **两种用法，同一个执行器**：
  - `desktop_goal`：把多步任务交给 Luna，速度快、成本低，主对话只拿回结论。
  - `desktop_screenshot` / `desktop_act`：由 Codex 的主模型自己看、自己点，适合一两步的操作或需要结合对话判断的地方。
- **执行器**（`moto_cua/luna.py`）：
  - 截取任务所在的窗口（见下一节“截图范围”）；截图像素按原点和缩放换算为全局逻辑坐标，交给门户的 `glide`/`click`/`press`/`release` 和滚轮。
  - 按键名（ENTER、CTRL、ARROWUP 等）映射为 keysym；文字走 `commitText`，中文直接可用。
  - 每批动作执行后等待 0.5 s，再截下一张图。
  - 模型结束时以 DONE / ASK / FAILED 开头作答，映射为 `done` / `question` / `failed`。
  - 支持 moto-clicker 的中止文件（用户说停时生效），以及调用方自己的 `stop`（提前结束）和 `gate`（可以先看、先决定，但到放行时才执行动作）。
  - 遇到 `pending_safety_checks` 时不自动确认，作为 `question` 返回给 Agent。
- **语音消息**：
  - 录音开始的判定以音频路由为准：`moto-audio-route` 报告该应用开始从 Linux 麦克风录音才算开始，比看截图可靠。
  - 录音一开始，就让模型去找发送按钮，与语音播放同时进行；发送点击等语音播完才执行（`gate`）。
  - 提示里排除“语音输入/听写”（它也会从麦克风录音，但会把语音转成文字发出去）。微信里真正的录音按钮是输入框右侧的圆形声波图标，麦克风图标是听写。

## 截图范围：由 KWin 直接给出任务所在的窗口

用户指出：打开应用之后，模型只需要这个应用里的内容，不需要整个桌面。

- **原来的方案怎么做**：
  - typesafe-computer-use（`perception.py`）：每步都截整屏，但 OCR 只读前台窗口（外扩 8 pt，并上它上方的菜单栏）；拿不到窗口外框时读整屏；与上一张相比只重读变化了的 256 px 分块。JEV 只接收文字，不看图。
  - arc-cua（方案二的单步执行）：只读激活窗口的无障碍树。
  - Codex 的 Computer use：按应用划定范围（点名 `@App`，逐个应用授权）。它不开源，截图粒度是从文档推断的。
- **做法**（`luna.Screen`）：
  - 目标窗口直接取自流程：`desktop_launch` 返回的窗口 id，或助理屏上当前激活的窗口，不需要模型在整屏里找。
  - 没有弹出层时，用 KWin ScreenShot2 的 `CaptureWindow(id)`：这个窗口被单独渲染（即使被挡住），带标题栏、不带阴影，图片正好对应 `frameGeometry`。
  - 弹出菜单、下拉框、对话框在 Wayland 下是独立窗口（KWin 源码 `takeScreenShot(Window*)` 只渲染 `windowItem()`，不包括它们）。有这类窗口时，用 `CaptureArea` 截取“目标窗口及所有 `transientFor` 链指向它的窗口”的并集（在 KWin 里实测，微信的右键菜单是 `popup`，`transientFor` 指向主窗口，外框伸出主窗口右边界）。`CaptureArea` 按所有屏幕里最高的缩放（手机的 3 倍）出图，所以要缩回助理屏的 1.75 倍，否则 token 会多出将近三倍。
  - 目标窗口关闭或最小化，或者屏幕上换成另一个应用（包括系统对话框）激活时，跟随新的激活窗口；没有窗口时截整屏。
  - 范围一变，就用一句文字告诉模型现在看到的是什么。模型可以调用 `view_whole_screen` 看整屏。注意：续接对话后，接口只接受通过 `computer_call_output` 回传的图片（否则报错 `Computer tool only allows input image without previous response`），所以函数调用只回文字，由模型再请求截图。
  - 坐标：全局坐标 = 截图原点 + 像素 ÷ 缩放。点击范围限制在助理屏内。
  - `moto-screenshot` 新增 `window <id>` 和 `area x y w h` 两种方式；`kwin.py` 新增 `target()` 查询。
- **收益**（同一张微信截图各请求 4 次）：整屏 1920×1080 为 2523 个输入 token、请求耗时中位数 4.9 s；只截窗口时为 760×828、823 个 token（−67%）、3.8 s。两种方式下点击坐标误差都是 1 px。
- **实测**：
  - 右键最后一条语音并读出菜单：截图依次为“只有窗口 735×802 → 窗口加菜单 817×901 → 只有窗口”，菜单选项读对，3 步 15.0 s。
  - 问任务栏上的时间：模型先调用 `view_whole_screen`，再截图看到整屏，回答“下午 3:00”，与设备时间一致，10.2 s。
  - 语音消息回归（文件传输助手）：全程只截微信窗口，6.5 s 的语音成为一条 8" 消息，29.2 s。

## 截图格式（实测，助理屏上的微信聊天截图）

每种格式请求 4 次，问题是读出聊天标题、聊天内容，并给出麦克风图标的坐标。标准答案为：标题“周楷雯”；消息 Name Card、02:56、通话时长 01:19、03:09、通话时长 00:21；麦克风约在 (724, 849)。

| 格式 | 大小 | 编码（手机） | 请求耗时中位数 | 标题读对 | 消息读全 | 坐标误差 |
|---|---|---|---|---|---|---|
| PNG（压缩级别 1） | 1313 KB | 125 ms | 7.0 s | 0/4 | 4/4 | 1 px |
| **JPEG q85，4:4:4** | **193 KB** | **26 ms** | **3.5 s** | 4/4 | 4/4 | 1 px |
| JPEG q92，4:4:4 | 253 KB | 28 ms | 5.0 s | 2/4 | 3/4 | 1 px |
| WebP q85（method 0） | 92 KB | 106 ms | 4.6 s | 2/4 | 4/4 | 1 px |
| WebP q90（method 2） | 87 KB | 123 ms | 5.0 s | 2/4 | 4/4 | 1 px |

- 输入 token 数与格式无关，都是 2510：计费看像素，不看字节。
- 点击坐标在所有格式下误差都是 1 像素。
- “楷/楠”这种细微字形，所有格式都会偶尔读错，连无损 PNG 也是，错误来自模型本身，与压缩无关。这也是联系人要按拼音搜索的原因。
- 采用 **JPEG q85、4:4:4**：编码最快，请求最快，准确性不差于无损格式。GIF 只有 256 色，不在考虑之列。

## 实机验证

- **只读**：问屏幕上有什么窗口，模型直接作答，没有执行动作（5.4 s）。
- **测试用例（用户指定：给周凯文发语音）**：
  - 打开聊天：`desktop_goal`（微信）用 3 步完成（点搜索框、输入 `zhoukaiwen`、点联系人），回报“顶部显示的联系人名字是周楷雯”（同音的真实联系人）。当时还是 PNG，共 41.6 s，其中模型 38.1 s。
  - 发语音：模型先把指针悬停在圆形声波图标上确认，再点击；路由确认开始录音后播放 11.8 s 的语音，再点发送。聊天里出现一条 21" 的语音。结尾多出约 9 s 静音，原因是找发送按钮的两次模型调用排在语音播完之后。
- **修正后复测**（为了不再打扰真实联系人，改在文件传输助手）：
  - 打开聊天 3 步，17.5 s，其中模型 14.8 s（JPEG，平均每次调用约 4.9 s）。
  - 6.7 s 的语音变成 8" 的消息，前后留白合计约 1.3 s；整个工具调用 32 s。
- **Codex 直接看图**：`codex exec` 调用 `desktop_screenshot` 后回答“打开的是微信窗口，顶部显示的标题是 File Transfer”，与画面一致。

## 未验证与后续

- 通过语音助手（Codex app-server）完整跑一次方案一（本轮用 CLI 和 `codex exec` 验证）。
- `desktop_act` 由 Codex 主模型逐步操作的实际效果与耗时。
- 方案二切换后的回归（`moto-cua plan atspi`，工具列表和 JEV 版 `desktop_goal`）。
- 通话代理的拨号和挂断已改用方案一（63 篇），但还没有实际打一通电话验证。
- 按住说话类的录音控件（`hold`）在方案一下不支持：`computer` 工具没有“按住直到外部信号”的动作。
- 推理强度目前为 `low`（`MOTO_CUA_EFFORT`），更低档位对速度和准确性的影响未测。
