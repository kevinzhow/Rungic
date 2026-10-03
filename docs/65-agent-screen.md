# 助理屏：按需开启、浮窗与投屏无缝互转的第二输出

> 概念调整（2026-09-29，用户定义，见 [research/91](research/91-agent-workspaces.md)）：本篇的“助理屏”，即用户桌面的第二输出，现在叫**桌面模式**（`rungic-desktop-mode`，控制中心的“桌面模式”）。“助理屏”只指 Agent 自己的工作区屏幕，由 Agent 需要时自己显示，控制中心里没有它的开关。投屏时，电视显示的是桌面模式。下文按时间记录，保留当时的名称。

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-25。用户要求：
- 后续开发不再以投屏为主，Agent 在手机上的一块虚拟屏上工作。
- 这块屏在 Linux 桌面里以浮窗显示，可以收到屏幕边缘。
- 浮窗与投屏之间要能无缝互转，两个方向都要。
- 不常驻：可以在控制中心开启，Agent 执行任务时也会自动开启。

界面要求：
- 工具栏在画面下方、留间距，拖动或点击时出现，几秒后自动隐藏，按钮用图标。
- 小窗不能直接操作画面内容；双指捏合缩放，最大到屏幕宽。
- 全屏为横屏铺满，底部上滑调出工具栏。
- 拖动要平滑。

## 为什么不用门户的虚拟输出

xdg-desktop-portal-kde 6.6 的 RemoteDesktop 可以创建 KWin 虚拟输出（`Virtual-…`，1920×1080）。但它只存在于 KWin 内部，电视只能显示 Android 宿主提供的输出（CAST-n）。所以虚拟屏和投屏必然是两块不同的输出，互转就得把窗口在两块输出之间搬家，做不到无缝。第一个原型就是这样写的，启动即失败，没有继续。

## 设计：一块固定的第二输出，只换显示去处

```
                 ┌ 电视接上：CastDesktop 的 Surface（固定 1920×1080，缩放显示到电视）
KWin CAST-1 ─────┤
（宿主第二输出）  └ 没有电视：帧确认后丢弃；Linux 浮窗用 zkde_screencast 录这块输出来显示
```

- **原生宿主**（`native/plasma`）：新增 `JniCommand::AgentScreen` 和 `NativeBridge.setAgentScreen`。
  - 开启后第二输出始终存在，与电视无关。
  - 电视接上时只绑定显示端：输出已存在就不重建。
  - 电视断开时，只要助理屏开着，就只释放显示端、保留输出。没有显示端时帧照常确认并释放。
- **APK 1.38**：
  - 平台桥 `{"op":"agent-screen","enabled":bool}` 返回 `enabled/width/height/tv`，开关持久化，宿主重启后重新应用。助理屏只影响 Linux 自己的输出，所以这个请求不要求宿主在前台。
  - 助理屏开着时，CastDesktop 固定用 1920×1080 绑定电视。
  - `orientation` 请求不带 mode 时返回当前设置。
- **Linux**（`plasma/agent-screen/`）：
  - `moto-agent-screen on|off|toggle|status|ensure`（Python CLI）。`on` 开启输出、启动浮窗，并等待 KWin 出现 CAST-n；`ensure` 会重新拉起开着的屏但已退出的浮窗，控制中心磁贴每 4 秒调用一次。
  - `moto-agent-screen-window`（Qt6/QML）：用 KWin 受限协议 `zkde_screencast_unstable_v1`（`stream_output`）录制 CAST-n，用 `org_kde_kwin_fake_input`（`pointer_motion_absolute`）把触摸转成对这块输出的指针输入。两个协议通过 `dev.moto.AgentScreen.desktop` 的 `X-KDE-Wayland-Interfaces` 授权，按可执行文件路径匹配，不会授权给解释器。
  - 每 1.5 秒查一次平台桥：电视接管时浮窗隐藏、停止录制；电视断开时恢复；屏关闭时浮窗退出。
  - 控制中心磁贴“助理屏”（`dev.moto.quicksetting.agentscreen`，放在“投屏”之后）。
  - “投屏”磁贴改为按 `moto-cast status` 的 `active` 判断是否在投，不再用“屏幕数大于 1”判断，否则助理屏会被误认成电视。
- **Agent**：
  - `desktop_goal` 执行前运行 `moto-agent-screen on`，并把应用开到助理屏（`desktop_launch` 新增 `"screen":"agent"`；有 CAST-n 时 auto 模式也选它）。
  - 技能和提示词说明：助理屏是 Agent 自己的屏，手机主屏属于用户。

## 浮窗的交互与实现

- **表面**：一个透明的 layer-shell 表面覆盖整个手机屏（top 层，控制中心、锁屏等 overlay 层会盖住它）。画面、工具栏和贴边标签都在表面内部由 QML 移动；Wayland 输入区域（`QWindow::setMask`）只包含可见部分，其余触摸照常落到手机应用上。
  - 早先的做法是用 margin 移动整个 layer surface。拖动时手指坐标是相对于窗口实际位置计算的，而实际位置总比请求的晚一帧，形成反馈，窗口“到处乱窜”。
- **小窗**：
  - 只显示画面，不能操作内容。单指拖动时窗口跟手，松手后平滑归位（OutCubic，240 ms）。
  - 拖出侧边四分之一，或向侧边快速甩动，就收成贴边标签；点标签展开，竖直拖动标签可以沿边移动。
  - 双指捏合调整宽度，在屏幕宽度的 50% 到 100% 之间，以中心为基准缩放。
  - 点击、拖动或捏合时，工具栏出现在画面下方 10 px 处（靠近屏幕底部时改到上方），3 秒后淡出。
  - 工具栏按钮：全屏、投到电视、收到边缘、关闭。
- **全屏**（APK 1.39，`AgentFullscreen`）：由 Android 宿主直接呈现，与电视走同一条显示路径。
  - 最初的做法是 Linux 浮窗录制 CAST-1，用 Qt 画进一个全屏图层，再由 KWin 合成进手机输出。实测只有约 29 fps（帧间隔稳定在 26–29 ms）；小窗同一条流约 52 fps。
  - 现在的做法：浮窗的全屏按钮向平台桥发 `{"op":"agent-screen","fullscreen":true}`。APK 在 Plasma 画面之上加一个全屏 SurfaceView，并以 `bindPresenter("fullscreen", …, rotation 90)` 绑定宿主的第二输出显示端。原生显示端用 `ASurfaceTransaction_setGeometry`，由 SurfaceFlinger 完成顺时针 90° 旋转、按比例缩放和居中留黑边，帧仍是零拷贝。浮窗在全屏期间隐藏并停止录制，处理方式和电视接管时相同。
  - 显示端归属：电视（`"tv"`，CastDesktop）和全屏（`"fullscreen"`）共用一个显示端，一方只在自己是当前绑定者时才释放，所以不会误断另一方。电视接上时优先，全屏自动退出；电视在显示时拒绝进入全屏。
  - 触摸：由宿主处理，不经 Linux。透明触摸层和工具栏放在一个单独的子窗口里（`TYPE_APPLICATION_PANEL`），因为宿主的零拷贝图层会盖住同一个 Activity 窗口里画的所有东西，最初的工具栏只露出一条边。这个层同样旋转 90°，使用横屏坐标。
    - 手势与移动算法见 66 篇（APK 1.40 统一重构：直接触摸与触控板共用 `PointerOutput`、`PointerTransfer`、`GestureRules`）。
    - 指针通过新增的 `castPointer` op 5（绝对位置，单位为助理屏像素）直接落到 KWin 的 CAST 窗口上。
    - 点击时抬起延后 40 ms：按下和抬起在同一时刻发出时，面板的应用启动器不响应。
    - 进入全屏 300 ms 后先把指针移到画面中央：KWin 要等看到新的指针能力后才绑定指针，第一次点击的按下原本会丢。
  - 工具栏：在横屏视角底部 56 dp 的条带内上滑，工具栏出现在底部中间（离开全屏、触控板模式、投到电视、关闭），3 秒后隐藏。在这条带内单击和长按照常传给画面，因为助理屏的任务栏就在这里。
  - 两种触摸方式，用工具栏的开关切换，选择会记住：
    - 直接触摸：手指所在的位置就是指针的位置（见上）。
    - 触控板：和手机当电视触控板时的手势相同（`TouchpadGestures`，从 CastControls 中提出，两处共用）。单指相对移动指针（带加速），轻点单击，轻点后再按住拖动，双指滚动，双指轻点右键，三指轻点中键。在底部条带内上滑仍然调出工具栏。实测单指滑动后，KWin 光标按横屏方向相对移动。
  - 手机本身保持竖屏。让系统转成横屏时，手机上所有应用都要重新布局，手机输出（800 宽）还与 CAST-1（x=360）重叠，KWin 在重叠处把手机桌面画进了助理屏。浮窗仍保留重叠检测，检测到后用 kscreen-doctor 把 CAST-n 移到手机右侧，但移动后 plasmashell 会留下未重绘的区域。
  - 帧率：助理屏上播放 60 fps 的运动测试画面时，全屏呈现约 49–54 fps，所有新帧都被呈现（`cast(new, frames)` 同步增长）。剩下的差距在 KWin 渲染 CAST-1 的频率上，不在显示端。
- **材质**：深色半透明胶囊加细边框。真正模糊背后内容需要 KWin 的 blur 效果，而 Plasma Mobile 默认没有加载它（`loadedEffects` 中没有 blur，但 `isEffectSupported(blur)` 为 true）。全局开启会给所有窗口增加 GPU 开销，尚未评估，所以没有开。
- **指针**：录制时如果请求“指针嵌入画面”，KWin 会显示指针，而指针当时停在手机输出的 (0,0)，宿主把光标表面画成了黑底方块。现在改为录制不含指针，浮窗启动时把指针移到助理屏中央。

## 实机验收（2026-09-25）

- `moto-agent-screen on`：在没有电视的情况下出现 `CAST-1`（1920×1080，KScreen 沿用 1.5 倍缩放，逻辑尺寸 1280×720）；关闭后输出消失，浮窗退出。磁贴的开关和状态显示都正确；“投屏”磁贴显示“未连接”，没有误判。
- 浮窗：
  - 在浮窗里点击，会在助理屏上打开应用菜单（此项在旧交互下验证过，现在小窗改为不可操作）。
  - 以下都已截图验证：点击显示工具栏、拖动跟手、3 秒自动隐藏、甩到边缘收起、点标签展开、全屏（旋转、铺满）、侧向上滑调出工具栏、退出全屏。
  - 双指捏合当时无法用 adb 模拟，未实测；2026-10-01 起可用 `tools/rungic_touch.py` 直接向触摸屏写多点触控事件（见下文）。
- 宿主全屏（APK 1.39）：全屏呈现约 49–54 fps（之前的 Linux 渲染路径约 29 fps）。画面旋转后铺满，工具栏显示在画面之上；在全屏中单击任务栏启动器，菜单能打开和关闭，进入全屏后的第一次点击也有效；退出后回到浮窗；`moto-agent-screen status` 显示 `phone fullscreen`。
- `desktop_goal`（“打开下载文件夹，告诉我里面有哪些文件”，app 为 Dolphin）在助理屏上完成，回答正确。本次 OCR 用了 6.9 s，因为 APK 升级后 GPU 程序要重新编译一次。
- **未验收**：浮窗与电视之间的实际互转。电视当时不可用（`available:false`）。代码路径已就绪：电视绑定时不重建输出，断开时保留输出，浮窗随状态隐藏和恢复。

## 没人看的输出降速（2026-09-28，APK 2.7、KWin +rungic4、plasma-mobile +rungic3）

用户要求：助理屏全屏时，被遮住的手机画面要降低刷新；浮窗收到边缘时，助理屏也要降低刷新；先评估降速能省多少，省得不多再考虑别的方案。

### 评估

- **测法**：`.work/diag/fullscreen-pause/`（`sample.py`、`run.py`、`results.md`）。每组采样 10 s，统计各进程的单核 CPU 占用、`gpu_busy_percentage` 0.5 s 采样的平均值和宿主帧计数。用 gst `videotestsrc pattern=ball` 模拟不同帧率的应用：
  - GPU 出图的 `glimagesink` 由帧回调驱动，接近普通 Qt/GTK/Firefox 应用；
  - 共享内存的 `waylandsink` 按自己的时钟出帧，而且让 KWin 每帧都在 CPU 上传纹理，会夸大 KWin 的开销。
- **降速的收益**（浮窗收边，GPU 客户端）：

  | 帧率 | KWin | GPU |
  |---|---|---|
  | 60 | 55% | 51% |
  | 5 | 7% | 5.5% |
  | 1 | 2% | 4.5% |
  | 静止 | 0.7% | 3.5% |

  降到 5 fps 已去掉 KWin 和 GPU 多出开销的约 90–95%，降到 1 fps 约 98%。所以采用降速：和停止相比省不了多少，还能让 Agent 在自己的屏上照常截图、操作。
- **更大的浪费：两块屏互相拖着重画。**
  1. **宿主**：主循环只看全局提交计数，任何一块屏的提交都会触发 `render_all`，把整张手机画面重新送一遍给 SurfaceFlinger。收边时助理屏 60 fps，宿主每秒多呈现约 105 次手机画面；这时 SurfaceFlinger 加 APK 约占 70% 单核，全是这个原因。
  2. **KWin**：见下文“KWin：缩放补边越界”。

### 实现

- **宿主按屏渲染**（`native/plasma`）：
  - 提交按来源分开计数：助理屏或电视窗口的提交记为 cast，其余记为手机（`engine_timing::note_commit`）。
  - `render` 分成两部分：`render_cast` 在 cast 窗口有新提交时取帧、发帧回调；`render_phone` 只在手机侧有提交、会影响手机的命令或强制刷新时才重新呈现手机画面。
  - 查询、`CastPointer` 这类命令不算手机活动。只有手机侧的活动才让主循环跟随手机的 vsync；cast 帧在到达时处理，与后台投屏时相同。
- **按可见性限速**（`OutputPacing`，`engine_timing`）：

  | 状态 | 输出 | 最小帧间隔 |
  |---|---|---|
  | 助理屏全屏（`NativeBridge.setPhoneCovered`，APK 绑定 `fullscreen` 显示端时设置） | 手机 | 1000 ms |
  | 没有电视或全屏显示端，且浮窗收边（`setAgentScreenWatched(false)`）或手机画面不在（后台、熄屏） | 助理屏 | 200 ms |

  - 帧回调和呈现反馈都按这个间隔放行。被推迟的输出记下到期时间，驻留的主循环到时醒来补发。全屏解除时强制重画一次手机。
- **被顶替帧的反馈要攒着**（smithay `PresentationFeedbackCachedState.hold_superseded`）：
  - KWin 的 Wayland 输出最多允许 2 帧待呈现（VRR 时 1 帧），靠呈现反馈腾出名额；一帧收到 `discarded` 也算不再待呈现（`OutputFrame` 析构调用 `notifyFrameDropped`）。
  - smithay 按协议，在新提交顶替旧提交时立即回 `discarded`。所以宿主只推迟反馈时，KWin 的 CAST-1 仍按 60 Hz 出帧，每一帧都被丢弃。手机输出开着 VRR，只允许 1 帧待呈现，不会被顶替，所以原型里手机能停下来。
  - 现在限速期间，被顶替的反馈先攒起来，到这块屏的下一轮再回 `discarded`；协议没有要求立即回。结果 KWin 每轮最多出 2 帧。
- **KWin：缩放补边越界**（`packages/kwin/.../clip-scaled-damage-to-surface.patch`）：
  - 手机 WL-0（0,0 360×800，缩放 3）和 CAST-1（从 x=360 开始，缩放 1.75）共用一条边。
  - 表面缓冲区的缩放和输出不同时，`SurfaceItem::addDamage` 会把损坏区域向四周各扩 1 个逻辑像素，照顾缩放采样的边缘，但扩出的部分没有裁回表面自身。
  - 结果：贴在助理屏左边缘的窗口，每出一帧都会安排手机输出重画；实测助理屏上 60 fps 的窗口让 KWin 每秒重画约 70 次静止的手机输出。反过来，手机上的动画也让 KWin 重画助理屏。
  - 定位过程：
    - 两块屏之间留 1 像素间隙，这种耦合就消失；换成整数缩放 2 仍然存在。
    - 调试版 KWin 在 `OutputLayer::scheduleRepaint` 上记录调用栈，泄漏来自 `SurfaceItem::addDamage → Item::scheduleRepaintInternal`。
    - 最初猜是 `paintedDeviceArea` 的取整问题，改完实测无效，那个补丁已放弃。
  - 修复：补边后的损坏区域裁回表面自身的矩形；表面外本来就没有它的像素。电视输出同样受益。
- **每轮循环结束时 flush**：`pump()` 只在渲染前 flush。一轮放行产生的帧回调和呈现反馈，原本要等下一轮循环才真正发出；循环在驻留时最多要等 1 秒，所以收边时实际只有约 1 fps。现在每轮循环结束、进入等待之前再 flush 一次。
- **浮窗**（`plasma/agent-screen`）：`mode` 变化时经平台桥发 `{"op":"agent-screen","watched":bool}`。每次轮询时如果宿主记录的状态不一致（APK 重启过，或助理屏重新开启），就重新上报。收边时录制流不停，展开时不用重连。

### 顺带修复：宿主重启后 plasmashell 变成桌面版 shell

- **现象**：2026-09-28 为测试重装 APK 后，手机出现桌面版任务栏，移动版的状态栏和导航栏都没了。
- **原因**：
  - `rungic-plasma-session.service` 重启时，systemd 只结束了它的主进程；上一个会话的 `startplasma-wayland` 仍在 logind 的 scope 里，过约 1.5 s 才退出。
  - 它退出时调用 `cleanupPlasmaEnvironment`，用 `UnsetAndSetEnvironment` 把 systemd 用户环境恢复成那次会话开始前的样子。
  - 这次恢复发生在新会话导入 `PLASMA_DEFAULT_SHELL` 之后，把这个变量清掉了，plasmashell 就按默认的桌面版 shell 启动。
- **修复**：`plasma/session` 在启动新会话前，先等上一个会话的 `startplasma-wayland` 退出（最多 10 s，超时就 `KILL`）。

### 实机验收（2026-09-28，APK 2.7、KWin +rungic4、plasma-mobile +rungic3）

- **版本说明**：实测所用 APK 的宿主代码与 2.7 相同，但在合并远端首次启动改动（同样编为 2.6 的另一版）之前构建，当时编号为 2.6；2.7 只做了构建检查。
- **测试应用**：GTK4 帧时钟动画（`.work/diag/fullscreen-pause/anim.py`，每个 tick 移动一个方块，自己统计每秒绘制的帧数），放在助理屏上。另用 gst 共享内存动画模拟手机上的动画。CAST-1 与手机相邻（x=360）。
- **结果**（单核 CPU）：

  | 状态 | 动画 fps | KWin | SF | APK | GPU | 手机重画/s |
  |---|---|---|---|---|---|---|
  | 有人看（浮窗展开） | 72–87 | 47% | 3–4% | 13% | 25% | 0 |
  | 收边 | 10 | 7% | 3% | 4% | 6% | 0 |
  | 展开恢复 | 72–80 | 47% | 3% | 13% | 27% | 0 |
  | 全屏，手机上另有 60 fps 动画 | 57–76（助理屏呈现 72 fps） | 42% | 21% | 20% | 16% | 1.0 |
  | 退出全屏 | — | 98% | 27% | 33% | 24% | 61 |

  - 改动前，收边状态下助理屏 60 fps：KWin 37–55%，SurfaceFlinger 27–33%，APK 34–37%，GPU 41–51%，手机每秒被多呈现约 100 次。
  - 不开助理屏时，手机上 60 fps 的动画每秒渲染 60.2 次，和内容帧率一致；空闲时为 0。
- **浮窗上报**（rungic-agent-screen 0.283）：用滑动把浮窗甩到边缘后，平台桥显示 `watched:false`，宿主自动切到 `cast=200ms`；点标签展开后变回 `watched:true`，立即恢复全速，画面正常。
- **会话修复**（rungic-plasma-session 0.283）：连续两次重装 APK 让宿主重启，plasmashell 都是移动版。日志顺序为旧会话的 `startplasma-wayland: Shutting down` 在前，新会话的 `plasma-mobile-envmanager` 在后；修复前正好相反。其间多次重启会话，助理屏始终只有一个桌面面板（plasma-mobile +rungic3 的防重复修复）。
- **说明**：没有显示端但有人看的时候，助理屏现在约 80 fps，超过名义上的 60 Hz（以前约 42 fps，因为反馈要等到下一轮才发出）。宿主在没有显示端时收到帧就立即回“已呈现”，KWin 于是尽快画下一帧。浮窗以手机 120 Hz 显示，画面更流畅，但比以前多耗电；是否把这种状态限到 60 Hz，待定。

## 双指放大时浮窗被收成标签（2026-10-01，已修，实机验证）

- **用户反馈**：双指放大浮窗，某一边超出屏幕时，浮窗直接被收成边缘的小标签。
- **真正的原因**（实机：G100 S，Qt 6.10.2，用 `qt.quick.handler.*` 调试日志记录）：
  - 浮窗上同时有单指的 `DragHandler` 和 `PinchHandler`；
  - 张开到超出屏幕边缘后，一根手指先抬起。捏合随即结束并放开另一根手指，`DragHandler` 立刻拿到它：这根手指离按下的地方已经很远，早就超过了拖动距离；
  - 接管后的第一帧位移是 (340, 402)：拖动按最初按下的位置计算偏移，浮窗一下子跳到屏幕外；手指抬起时被当作“拖过边缘四分之一”，于是收起；
  - 离线（PySide6 6.11 和 6.10.2，`QTest` 注入触摸）重现不了这条路径：经 Android → 宿主 → KWin → Wayland 送到 Qt 的触摸，和测试工具直接注入的不一样。
  - 离线还能重现另一条路径：第二根手指落下时，正在快速外移的第一根手指让拖动结束，随即按“甩向边缘”收起。这条在实机上没有触发。
- **修复**（`agent/screen/qml/Main.qml`，开发覆盖 `rungic-agent-screen`）：
  - `PinchHandler` 抓取手指时，按“编号 + 按下时间”记下这些手指（Android 会重复使用编号）；`DragHandler` 接管的若是其中之一，既不移动也不收起，抬起后只归位。日志确认拖动是先抓取、后激活的，所以在激活前就能判断；
  - 只有手指真的抬起才算松手：抓取交出时那根手指的状态为 `Released`，判断推迟到 `Qt.callLater`。这一条针对离线那条路径；
  - 甩动收起改成按惯性推算：速度超过 900 px/s 时，假设浮窗按这个速度再走 0.15 s，看会不会越过边缘的四分之一；
  - 走过的弯路：先试过拿掉 `DragHandler` 的 `CanTakeOverFromHandlersOfDifferentType`，结果无效。日志显示那根手指被接管时已经没有任何处理器占着，不存在“抢”的问题。这一改动已撤掉。
- **实机测试**（`tools/rungic_touch.py` 向 `/dev/input/event8` 写多点触控事件，约 12 ms 一帧）：

  | 手势 | 修复前（发布版 0.510） | 修复后 |
  |---|---|---|
  | 贴右边缘，张开超出边缘，左手指先抬起，右手指继续外移后抬起 | 收成右下角标签（重现用户问题） | 仍是浮窗，满屏宽 |
  | 同上的镜像（贴左边缘，左手指最后抬起） | — | 仍是浮窗 |
  | 外侧手指先落下并以约 3000 px/s 外移，第二根随后落下，双指张开 | 仍是浮窗（实机不触发这条路径） | 仍是浮窗 |
  | 慢速张开、慢速捏小 | — | 正常放大、缩小 |
  | 捏合之后立即单指慢拖 | — | 跟手移动 200 px，与手指位移一致 |
  | 单指快速右甩 / 拖过左边缘四分之一 | — | 分别收成右侧、左侧标签 |
  | 点标签 | — | 展开 |

- **离线回归测试**：`tools/tests/test_agent_screen_gestures.py`，从真实的 `Main.qml` 抽出手势代码运行，Qt 6.11（`.work/venv`）与 6.10.2 都通过。6.10.2 与手机上的 Qt 版本相同，用 `.work/venv-qt610`（`python3 -m venv .work/venv-qt610 && .work/venv-qt610/bin/pip install PySide6==6.10.2 pytest`）运行。修复前的代码在“外侧手指先动”那项上失败。实机的那条路径离线重现不了，靠上面的实机测试覆盖。
- **测试注意**：
  - 每次测试前确认没有真实手指按在屏幕上（工具会检查 `dumpsys input`）；
  - 测试会在用户屏幕上实际移动浮窗；测试结束后浮窗重启，回到默认位置（左侧、屏宽 72%）。

## 待办
- 电视互转实测；输出重叠后 plasmashell 的重绘问题；是否启用 KWin blur 做真正的毛玻璃。
- 小窗约 52 fps（录制 → Qt）；是否经 dmabuf 尚未确认。
- 写手（gpt-6-luna）生成回答和文字约 7–8 s（64 篇）。

## Agent 对话应用冷启动闪黑窗调查（2026-10-03）

用户反馈：在 Plasma 里冷启动 Agent 对话应用，偶尔闪过黑色悬浮屏，疑似助理屏。调查分支 `fix/agent-app-cold-start-screen` 从 main `331bbdea` 创建。以下区分已复现的助理屏缺陷与尚未确认的用户触发路径。

### 已复现：关闭的助理屏仍显示黑窗

- `AgentScreen` 构造函数启动 1500 ms 轮询并立即调用 `poll()`。平台桥回答助理屏 `enabled:false` 时，旧实现直接调用 `QCoreApplication::quit()`。
- 此时 `main.cpp` 尚未进入 `app.exec()`，退出请求不起作用。程序继续加载 QML、附着 layer surface 并设 `ready:true`；状态仍是 `starting`，`nodeId` 为 0，于是映射黑色浮窗。下一次轮询进入已运行的事件循环后才真正退出。
- [Qt 的 QCoreApplication::quit 文档](https://doc.qt.io/qt-6/qcoreapplication.html#quit)说明事件循环启动之前的直接调用无效，建议排队执行退出。采用 Qt 自有事件循环机制，不添加固定等待或新的窗口管理层。适用当前 Qt 6；未修改 Qt 上游源码。
- 修复：关闭状态立即置为 `off`，通过 `QTimer::singleShot(0, qApp, &QCoreApplication::quit)` 延后退出；QML 在 `off` 状态保持隐藏。同样处理桌面模式 0 号工作空间 socket 不存在的退出路径。
- 原生回归 `tools/system/tests/desktop_mode_window.py::disabled_assistant`：真实 C++/QML 浮窗程序、独立 KWin、平台桥契约替身回答关闭。旧 main 失败，确认映射了浮窗；修复后通过，并补验 0 号桌面关闭时的残留启动。它验证关闭状态下的残留启动，不声称复现 App 发出启动请求。
- 新的 QML 检查验证关闭状态从首个布局起浮窗和全屏窗口都不显示；与原生回归共同覆盖 `desktop-mode.floating-window/E7`。

### App 启动路径与实机观察

对话应用入口 `agent/assistant/app/main.cpp`、`Main.qml` 与 `ChatPage.qml` 在空白页冷启动时连接 VoiceAgent、设置观看状态并请求 PhoneSnapshot；本轮追踪未找到这条路径直接调用 `rungic-agent-screen on/ensure/start_window` 的证据。常驻语音悬浮层 `--overlay` 是另一个窗口，不能根据名字判断它就是助理屏。

G100（USB `ZY32M9MRVP`）当前开发覆盖为 `20260930.19+dev20261003t085900`，相关语音服务包为 `0.510+dev20261003t085900.333b931`。本次没有部署浮窗修复、没有重启常驻服务。启动前确认没有对话 App、任务或电话会话，只退出本轮自己创建的 App，结束后恢复 Plasma 首页。

- KWin 普通窗口清单会过滤 layer surface，因此先前只读普通窗口的结果不足以排除短暂浮窗。补充用持续 KWin `windowAdded/windowRemoved` 信号记录全部窗口的类、PID、层级和几何。
- 连续三次直接运行 `rungic-voice-assistant`：各出现一个 `com.rungic.VoiceAssistant` 对话窗口，没有捕捉到助理屏窗口。退出后恢复原有三个 Plasma 表面。
- 按 Plasma 的公开 D-Bus `openAppLaunchAnimationWithPosition` 入口先请求启动动画，再运行同一 App：额外出现 `org.kde.plasmashell`、PID 为现有 plasmashell 的临时窗口，覆盖应用区域 `360×726`；约 0.54 秒后移除。这个受控重放验证动画窗口身份，**没有实际点击桌面图标，也没有确认它在视觉上就是用户反馈的黑窗**。
- 固定上游 plasma-mobile `6.6.5` 的 `AppDelegate.qml::launchApp()` 在应用尚未运行时请求此动画；`StartupFeedbackWindows.qml` 创建最大化、无边框的独立 Window，`StartupFeedbackModel::onWindowOpened()` 在实际应用激活后移除它。来源为配方 `packages/plasma-mobile/recipe.json` 的固定源码（KDE，相关 QML LGPL-2.1-only / LGPL-3.0-only / KDE-Accepted-LGPL，C++ GPL-2.0-or-later）。保留成熟启动反馈机制，本轮没有因相似外观修改或禁用全局启动动画。

**边界**：已修复的是已关闭助理屏的残留启动闪窗。尚未把用户的 App 冷启动现象关联到它；也未证实 Plasma 动画存在黑色绘制缺陷。待确认发生设备、黑窗是小矩形还是覆盖应用区域，并在相同入口捕捉窗口身份和帧画面后再决定是否需要 Plasma 补丁。

### 验证记录

- 修复前：`.work/system-tests/20261003-191003/results.json`，关闭助理屏回归失败。
- 修复后：`.work/system-tests/20261003-191134/results.json`，`desktop_mode_window`、`assistant_app` 均通过；包含原有浮窗、全屏和电视切换路径。
- `.work/system-tests/20261003-192351/results.json` 的最终原生回归也通过，含助理屏和桌面模式关闭的两条路径。
- 本机 QML 30 项通过；日志：`.work/verify/20261003-agent-cold-start/qml-tests.log`。
- G100 窗口事件：同目录 `trace-surfaces.log`、`trace-shell-startup.log`；初次仅普通窗口的 `trace-app.log` 不作为排除浮窗的证据。
