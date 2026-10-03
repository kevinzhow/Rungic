# 电脑操作：arc-cua + JEV 的 Linux 后端（moto-cua）

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

> 当前默认（2026-10-03）：由 Codex 使用 Agent 所选模型和当前登录，看截图并逐步执行桌面操作，见 [106 篇](106-codex-desktop-operation.md)。本篇保留 AT-SPI/OCR/JEV 备用方案的历史实现与验收；选择 `rungic-cua plan atspi` 并重启 MCP 后使用。独立 Luna API 方案见 [68 篇](68-luna-computer-use.md)，可在 Agent App「设置 → 桌面操作」选择。

2026-09-24。目标：语音助手的 Agent 能在本机 Plasma 桌面上操作图形应用。执行由 arc-cua 运行时和 TypeSafe 的 JEV 快速决策模型完成，Agent（Codex）只负责规划子任务。

## 选型与来源

| 组件 | 版本/来源 | 许可证 | 说明 |
|---|---|---|---|
| arc-cua | [shhivv/arc-cua](https://github.com/shhivv/arc-cua) `21d979d32bc74c136de9fe53cf4e6c9bc70a9f6b`（0.1.0），导入到 `vendor/arc-cua`，去掉网站目录 | MIT | 运行时、`DesktopBackend`协议、JEV策略（`TypeSafeJevPolicy`）。上游只有macOS后端（AX、Vision OCR） |
| JEV | `https://api.typesafe.ai/v1/systemone`，`jev-latest` | 云服务 | 每步一次请求，只从候选中选择“操作 + 目标”。实测首个请求约1.2–1.8 s，之后约0.4 s |
| `python3-httpx` 0.28.1、`python3-h2` 4.3.0 | Ubuntu 26.04 | BSD-3 / MIT | arc-cua 用 HTTP/2 调用 JEV，经用户代理 |

arc-cua的接口：`observe() -> DesktopSnapshot`、`is_fresh(snapshot, action)`、`execute(snapshot, action)`。模型只能从快照中选择元素id和它提供的动作。文字只能来自规划方给的`inputs`，模型不能自己编写。执行前后端必须重新核对目标，目标已变化就拒绝执行。

## Linux 后端的实现（`plasma/cua/moto_cua/`）

### 观察：AT-SPI（`a11y.py`）
- 以`libatspi`/`pyatspi`的方式逐个属性查询时，每次都是一次D-Bus往返，本机每个节点约50 ms。系统设置的133个节点需要7.4 s。
- Qt没有实现AT-SPI缓存：`Cache.GetItems`返回空列表。Qt只实现了`Properties.Get`，没有实现`GetAll`（GTK两者都实现了）。
- 做法是直接用Gio发D-Bus调用：每个节点发Name、Description、AccessibleId、GetState、GetRoleName、GetInterfaces六个请求，同一层所有节点的请求一次发出。100个节点只需70–100 ms，一次完整观察（61个元素，含动作名和值）约0.5–1 s。
- 元素id为`at_` + sha1(总线名 + 对象路径)，在对象存活期间保持稳定。角色、名称、值和状态进入arc-cua的语义校验（semantic guard）。
- 能力判定：
  - `CLICK`：有press、toggle、activate等动作，或者是按钮、列表项、页签等带屏幕位置的控件。
  - `DOUBLE_CLICK`、`RIGHT_CLICK`：列表项、表格单元等。
  - `TYPE_TEXT`、`SET_VALUE`：EditableText接口，或可编辑的文本角色。
  - `SET_VALUE`：Value接口（滑块等，不含滚动条）。

### 活动窗口与坐标（`kwin.py`）
- KWin的D-Bus接口没有“活动窗口”查询。moto-a11y让KWin脚本把结果print进journal再读回，要几秒。
- 现在由一次性KWin脚本经`callDBus`回调本进程在会话总线上导出的`dev.moto.Cua.Report`，约0.1 s。回调内容为活动窗口的pid、客户区全局几何（`clientGeometry`）、所在输出，以及光标位置。
- 按pid（a11y总线上的`GetConnectionUnixProcessID`）匹配AT-SPI应用。多个窗口时，选有ACTIVE状态、或标题与KWin标题一致的窗口。plasmashell的面板也会报告ACTIVE，所以不能只看状态。
- Wayland客户端不知道自己的全局位置。控件中心 = KWin客户区原点 + AT-SPI窗口坐标（`GetExtents(WINDOW)`）的中心。

### 执行：真实输入优先（`backend.py`、`portal.py`）
用户要求：输入应让应用看起来就是正常的键盘和鼠标操作，而不是自动化。

- **输入来源分析**：
  - 手指：Android触摸 → 宿主APK → Wayland `wl_touch`/`wl_pointer` → KWin → 应用。
  - RemoteDesktop门户：KWin的fake-input → KWin输入管线 → 应用。
  - 容器或Android的uinput/HID：内核设备 → Android InputReader → 宿主APK → KWin → 应用。
  - Linux客户端最终收到的都是KWin发来的普通Wayland键盘/指针事件，协议里没有来源字段。所以对本机Linux应用，HID与门户没有区别；走HID还要多绕一层Android。
  - 真正能被识别的是AT-SPI动作（DoAction、SetTextContents不产生输入事件，文本被直接替换），以及瞬移、零间隔等行为特征。
- **点击**：
  - 用XDG RemoteDesktop门户（标准接口，KDE上由KWin fake-input实现）把光标滑到控件中心：分6步缓动约0.1 s，然后按下、抬起，间隔随机。
  - 控件没有屏幕位置时，才用AT-SPI的Action兜底。
- **文字**：点击聚焦 → Ctrl+A → KWin的`org.kde.kwin.VirtualKeyboard.commitText`（KWin moto15新增），由KWin像输入法一样把整段文字提交给当前焦点的输入框：text-input v1/v2/v3，任何语言都可以；应用不支持text-input时退回按键事件。全程在Linux内完成，Plasma在后台时同样可用。
  - 起初复用了宿主平台桥`text-commit`（Android键盘的路径）来输入中文。那条路绕经Android宿主再回到KWin，而且Plasma退到后台时宿主没有键盘焦点，已废弃。
  - 没有`commitText`的旧KWin上，ASCII改用门户逐键输入，其他文字用AT-SPI的EditableText兜底。
- **滑块**：AT-SPI `Value.CurrentValue`。
- **按键和快捷键**：门户的keysym，MOD映射为Ctrl。
- **滚动**：光标滑到窗口中心后发离散滚轮事件。
- **门户的两个细节**：
  - xdg-desktop-portal 1.21前端（`remote-desktop.c`的`check_position`）只接受落在会话屏幕共享流之内的绝对坐标。只为定位而开屏幕共享，会让KWin持续输出PipeWire画面，因此改用相对移动`NotifyPointerMotion`，起点取KWin的`workspace.cursorPos`。fake-input的相对移动不经过指针加速。
  - 光标跨越手机与电视两个输出时，KWin的屏幕边缘阻挡会吸住约100像素。实测目标(477,52.5)，结果落在(377,53)。滑动后按KWin读回的位置补偿，结果为(477,52)。
- **门户授权**：第一次会在屏幕上弹出“Remote Control — Control input devices”对话框，并默认勾选“Allow restoring on future sessions”。
  - 批准后的restore token存放在`~/.local/state/moto-cua/remote-desktop-token`（600）。之后不再询问。
  - 2026-09-24搭建时由我经AT-SPI点了Approve。撤销方法：删除该文件，或在系统设置的应用权限中撤销。
- **无障碍开关**：`org.a11y.Status IsEnabled`默认关闭，由moto-cua在调用时打开（运行中的Qt应用约2 s内注册）。若是moto-cua打开的，空闲10分钟后自动关闭。

### 接入 Codex（`server.py`、`install.sh`）
- Agent的命令沙箱连不上D-Bus和Wayland，而Codex在沙箱外启动MCP服务。所以桌面操作做成stdio MCP服务`moto-cua mcp`，注册为`~/.codex/config.toml`的`[mcp_servers.moto-desktop]`。
  - 启动脚本`/usr/local/bin/moto-cua`补上会话总线、Wayland和代理环境：Codex给MCP的环境是精简过的。
- **工具**：
  - `desktop_windows`、`desktop_observe`：只读。
  - `desktop_launch`：按桌面文件id或名称启动应用，按WM类或可执行名等待窗口激活。
  - `desktop_activate`。
  - `desktop_window`：经KWin关闭、最小化、最大化、还原窗口，或移到手机/电视。关闭等同于标题栏的关闭按钮，应用仍可能询问是否保存，此时返回`still_open`。
  - `desktop_run`：一个有界子任务，参数为goal、verification、inputs、constraints、shortcuts、max_actions、timeout_s。
- **审批**：Codex 0.156对没有注解的MCP工具默认每次都要审批（`requires_mcp_tool_approval`），语音服务无法应答。
  - 因此只读工具标`readOnlyHint`；launch、activate、run标`destructiveHint: false`、`openWorldHint: false`。
  - 删除、发送、发布、付款、改账户等步骤，由Agent提示词和技能要求先口头确认。
- **语音助手界面**：MCP调用显示为命令卡片（工具名 + 目标或应用），也计入进度播报的“当前步骤”。
- **JEV key**：`~/.config/moto-cua/typesafe-api-key`或`~/.config/moto-voice-agent/typesafe-api-key`（600），不进仓库。

## 实测（2026-09-24，电视上的系统设置）

| 场景 | 结果 |
|---|---|
| arc-cua自带内存后端demo（JEV联网） | SUBTASK_COMPLETE，3步；JEV延迟1230/395/402 ms |
| “打开键盘设置页” | 1步CLICK“键盘”，SUBTASK_COMPLETE，4.3 s；页面标题变为“键盘 — 系统设置” |
| 搜索框输入“WLAN” | 点击聚焦、键盘通道输入，框内为WLAN；中文界面无结果，JEV返回NEEDS_AGENT（合理） |
| 搜索框输入“声音” | 输入法提交，结果列出“声音”“系统声音”“通知”，SUBTASK_COMPLETE，6.2 s |
| KWin `commitText`：搜索框输入“蓝牙 bluetooth” | SUBTASK_COMPLETE，5.1 s，读回值一致；Plasma在后台时输入“显示”同样成功 |
| `desktop_launch 系统设置` | 1.15 s，返回已激活窗口 |
| 语音端到端：“帮我打开系统设置，然后在里面搜索蓝牙” | 回应 → 读技能 → `desktop_launch` → `desktop_windows` → `desktop_run`（输入“蓝牙”）→ 播报“没有找到蓝牙相关项目”（本机未装蓝牙设置模块，结果属实）。约50 s，其中launch匹配超时占8 s，已修正 |

## 关闭窗口反复失败（2026-09-24）

- **现象**：用户让助手关闭电视上的系统设置，Agent试了几次才关上。
  - 它先让JEV“点窗口的关闭按钮”：标题栏是KWin画的窗口装饰，不在应用的无障碍树里，JEV看不到。
  - 接着在shell里`pgrep`：Agent沙箱有自己的进程命名空间，看不到桌面进程。
  - 最后在`shortcuts`里给了Ctrl+Q才关上。arc-cua默认只开放MOD+A/C/V/Z/SHIFT+Z/F。
- **修复**：窗口管理交给窗口管理器。新增`desktop_window`工具（KWin脚本：`closeWindow`、`minimized`、`setMaximize`、`sendClientToScreen`）。技能里写明：窗口的关闭、最小化、最大化、移屏用这个工具，不要走`desktop_run`；沙箱中的`ps`/`pgrep`看不到桌面应用。
- **实测**：`moto-cua window <id> close`一次关闭，窗口列表中不再有该窗口。

## 投屏时默认在电视上操作（2026-09-24）

用户要求：大屏开启时，绝大多数操作应在大屏上进行，不打断手机上的工作（手机上还显示着语音助手）。

- **`desktop_windows`**：返回`casting`和`screens`。
- **`desktop_launch`**：`screen`参数为`auto`（默认，投屏时为电视）、`tv`或`phone`。
  - 应用已经打开时，不再重复启动，而是把它移到目标屏并激活。
  - 新启动时，先加载一个一次性KWin脚本监听`windowAdded`，再执行`kstart`。该应用的第一个普通窗口一出现，就`sendClientToScreen`到目标屏并设为活动窗口，然后回报窗口id。
  - 窗口类名的匹配包括桌面文件完整id（Dolphin的类名就是`org.kde.dolphin`）、id末段、可执行名和StartupWMClass。
- **提示词和技能**：投屏时在电视上工作；手机上已打开的应用，先`desktop_window` `to_tv`再操作；只有用户点名手机时才用手机屏。
- **实测**：
  - Dolphin新开，直接出现在CAST-1并处于活动状态，1.7 s。
  - Dolphin先移回手机再调用`launch`，被移回电视并激活，1.0 s。
  - Firefox新开，出现在CAST-1，3.4 s。

## Firefox仍开在手机上、虚拟键盘顶起窗口（2026-09-24）

- **现象**：让助手打开Firefox，窗口出现在手机上；Agent输入文字时，手机弹出虚拟键盘，把窗口顶了上去。
- **原因**：
  - Codex启动MCP服务时只传十几个环境变量，缺少`MOZ_ENABLE_WAYLAND`、`GDK_BACKEND`、`QT_QPA_PLATFORM`、`XDG_SESSION_TYPE`、`XDG_CURRENT_DESKTOP`等会话变量。`desktop_launch`从这里经`kstart`启动Firefox，Firefox找不到显示、不出窗口；Dolphin是Qt应用，靠`WAYLAND_DISPLAY`仍能启动，所以此前在完整会话里测试时没有发现。
  - `desktop_launch`报告没有窗口后，Agent改在shell里直接执行`firefox`。新窗口落在当前活动输出上，即显示语音助手的手机。
  - 输入框在手机屏时，按移动端设计（`KWIN_IM_SHOW_ALWAYS`，仅对内置输出生效）会弹出虚拟键盘，KWin为给键盘让位把窗口上推。输入框在电视上时不会弹出，复现时`visible`为false。
- **修复**：
  - moto-cua启动时，从systemd用户管理器读入图形会话环境（`busctl --user -j get-property … Manager Environment`），与桌面启动应用时相同。等窗口的时间放宽到25 s。
  - 技能中规定：不要从shell启动图形应用或`xdg-open`；`desktop_launch`报告失败时查一次`desktop_windows`，然后告诉用户，不要换别的方式重试。
  - 在手机屏上输入文字时，聚焦后把`org.kde.kwin.VirtualKeyboard.active`设为false，再由KWin提交文字。用户下次触摸输入框时键盘会照常弹出。
- **应用名匹配**：会话语言是`en_US`（`plasma-localerc`），因此从桌面环境启动的应用是英文界面，与Plasma启动器一致。`desktop_launch`匹配.desktop中所有语言的Name/GenericName，所以用中文名也能找到应用。
- **实测**：
  - 以Codex同样的精简环境（`env -i`）调用`launch Firefox`：3.5 s，出现在CAST-1并处于活动状态。
  - 语音“帮我打开火狐浏览器”：Agent调用`desktop_launch`，Firefox出现在电视上。
  - 手机上的Firefox地址栏输入期间逐秒采样，`visible`始终为false。点击与收起之间约0.1 s，可能短暂闪现。

## 限制与待办
- 只操作活动窗口；弹出菜单等若属于另一个窗口，需要Agent先激活或再观察。
- 没有无障碍树的应用（部分Electron、游戏）只能看截图，arc-cua的OCR后端是macOS Vision实现，Linux暂无对应。
- `DRAG_TO`/`DRAG_BY`未实现（与上游macOS AX后端一致）。
- 快照里的`metadata.id`取AccessibleId末48字符，仍较长，可再压缩以省token。
- 进度播报偶尔在任务刚完成时说出“还在处理”（appendSpeech已排队，无法撤回）。

## 操作微信效率低的原因与修复（2026-09-25）

用户反馈：语音助手操作微信效率很低，好像读不懂界面。同一个子任务（“搜索并打开‘文件传输助手’”）修复前连续失败：

- 第一次BLOCKED：KDE“远程控制会话已开始”的通知盖住了窗口顶部。
- 第二次NEEDS_AGENT：文字被输进了一个群聊的草稿（未发送，已清除，并逐一核对所有会话都没有草稿），随后又点开了另一个群。

原因有三个，都修在共享层：

1. **坐标错位（Qt无障碍bug）**：
   - 微信内置Qt的`GetExtents(WINDOW)`返回的y和高度都是错的，比如搜索框报成`(99, 19, 68, 129)`，实际是22高。`GetExtents(SCREEN)`在同一窗口内是一致的，只是整体偏移到Qt以为的窗口位置（0, 80）。
   - 现在`a11y.extents`用“控件屏幕坐标 − 窗口根节点屏幕坐标”（`a11y.origin`，每次observe时记录）。
   - 对照：KDE Qt6应用新旧算法一致（18/18）；Firefox（GTK）新旧相差(26, 23)，正好是它的阴影边距，旧算法一直偏了这么多；微信由错误变为正确。
2. **窗口按手机尺寸打开**：
   - 微信主窗口按主屏幕（手机，逻辑宽度360）计算大小，在电视上只有420宽，于是切到窄布局，没有会话列表和联系人搜索框。执行器只能找到导航栏的“Search”（其实是“搜一搜”）和以联系人命名的输入框。
   - `convergentwindows`（vendor/plasma-mobile）改为：窗口打开5秒内出现在外接屏上、可调大小、宽度不超过手机宽度+120时，改为工作区的70%×80%居中，和从手机挪过去的窗口规则一致。
   - 顺带修复：新窗口的`desktops`尚为空时，改用当前桌面，否则调整会被跳过。
   - 验收：`size-probe`以400×600在CAST-1上打开，被调整为896×541。
3. **远程控制通知挡住窗口**：每次新建RemoteDesktop门户会话都会弹出通知。`plasma/cua/install.sh`把用户的`xdg-desktop-portal-kde.notifyrc`中`remotedesktopstarted`的Action设为空；门户服务重启后生效。

另外在技能说明里补充了微信界面要点：`Search`文本框在会话列表顶部；导航栏的`Search`是搜一搜；输入框以对话名命名，在里面按回车就会发送；测试一律用`File Transfer`。这些是应用层的界面知识。

修复后，同一子任务`SUBTASK_COMPLETE`，2步，用时7.2 s。

## 按读音找人；把弹出层纳入观察（2026-09-25）

- **问题**：用户说的人名经语音识别后常常写错字（“周凯文”实为“周楷雯”），按字搜索找不到人或找错人。
- **`desktop_find_name`（`names.py`，python3-pypinyin 0.55）**：
  - 按无声调拼音比较：同音得1.0；z/zh、c/ch、s/sh、n/l、f/h、an/ang、en/eng、in/ing这类混淆算接近，得0.9；其余按拼音相似度打分，最高0.8。
  - 返回`search_text`（拼音，如`zhoukaiwen`），以及当前窗口与其弹出层中按读音排序的名字。每个名字附带所在的分区标题。
- **微信支持拼音搜索**：输入`zhoukaiwen`，“Contacts”下即为周楷雯。“Internet search results”下的周凯文、周开文、周开稳也同为1.0，但属于搜索建议，由分区标题区分。
- **弹出层**：
  - 微信的搜索结果是同一应用的另一个无障碍窗口（无名filler），在Wayland上是xdg_popup，不是KWin的活动窗口，原先的观察看不到。
  - `backend.observe`现在把同一应用正在显示、且不是frame或dialog的窗口一并纳入，它们与主窗口共用屏幕坐标原点。
  - 另开的普通窗口（例如单独打开的对话窗口）仍然排除。
- **技能说明**：人名来自语音时，先`desktop_find_name`取得拼音，用拼音搜索，再从“Contacts”中按读音挑选；有两个不同的人都达到0.9时，先问用户。
- **实测**：在微信搜索`zhoukaiwen`后，`moto-cua find-name 周凯文`用时1.2 s，“周楷雯”在`Contacts`下得1.0。
