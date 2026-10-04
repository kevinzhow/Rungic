# KDE 显示设置与 Android 原生分辨率

2026-09-23，正在实施；未完成下方实机验收前，不把界面选项等同于生效。

## 本机事实与目标

Moto XT2537-4的Android DisplayManager及dumpsys display报告1080×2400，实际模式为30/60/90/120Hz；hasArrSupport=false，不宣称支持连续VRR。旧宿主以720×1600生成Surface，放大至物理屏幕，模糊有明确的缩放来源。用户要求原生清晰度、60/90/120及自动刷新率，并将分辨率/刷新率/缩放放入KDE设置。保留真实30Hz选项。

默认目标1080×2400渲染、KDE 300%缩放，使逻辑工作区保持360×800；720×1600保留为低负载选择。渲染像素数提高2.25倍，需在最终版本重测帧时间，不能沿用720p测试数据保证性能。

## 调研与共同接口

- KDE官方[KScreen 6.6.5源包](https://download.kde.org/stable/plasma/6.6.5/kscreen-6.6.5.tar.xz)及[OutputPanel](https://github.com/KDE/kscreen/blob/Plasma/6.6/kcm/ui/OutputPanel.qml)：已有分辨率、缩放、固定刷新率、应用/还原机制，插件元数据支持handset。Ubuntu kscreen原先未安装，现已从签名APT安装4:6.6.5-0ubuntu0.1及其依赖。
- 现有KWin6.6.6 Wayland嵌套后端只发布当前模式；applyChanges故意忽略scale，因为普通嵌套窗口会跟随上层fractional-scale。本设备平面输出已把显示控制交给Android，需在MOTO_KWIN_FLAT_OUTPUT条件内提供模式枚举及真实设置。非Android后端保留上游行为。
- Android[帧率接口](https://developer.android.com/media/optimize/performance/frame-rate)允许应用提出刷新率偏好，最终受系统调度/温控限制。自动模式是Android策略，不冒充KScreen Adaptive Sync / VRR。

路径：KDE显示KCM / kscreen-doctor → libkscreen → KWin输出管理协议 → Android显示适配 → 宿主Window/Surface公开接口。应用窗口缩放由KWin/Wayland处理，不逐个修改App。

## 实现入口

- APK1.8：DisplayPacer按实际模式枚举固定档位与自动；MainActivity支持原生/720短边渲染，更新Surface尺寸、输入换算和挖孔数据；platform.sock增加display-get/display-set；android-display.json提供只读能力/实际状态。Android设置和Linux请求使用同一套持久化偏好。
- `plasma/android-display-client.h`：KWin和KCM共用客户端，认证沿用平台socket的UID检查，请求限定500ms总期限；渲染帧路径不做同步IPC。
- `plasma/kwin-display-settings.patch`：标准模式枚举、用户模式设置转发、真实缩放；系统重放配置不能覆盖Android实际刷新率与旋转。
- `plasma/kscreen-android-policy.patch`：扩展原版KCM的刷新率策略，显示物理屏幕/渲染分辨率/当前刷新率，复用标准分辨率、缩放、应用和还原。

## 验收清单

KDE主设置可进入显示页；固定30/60/90/120与自动实际生效；原生与720渲染切换、150–300%缩放及还原；重开会话持久化；前后台释放；横竖屏触摸和挖孔；原生分辨率的列表/开启动画帧时间。证据目录refs/plasma-display-settings-20260923/。

构建补充：KScreen源码来自KDE官方HTTPS源包，SHA256保存在证据目录。Ubuntu已安装libkscreen/LayerShellQt6.6.4，KScreen6.6.5原包也依赖这套库；仅将源码的构建最低版本检查对齐6.6.4，实际头文件/链接编译通过。未替换Qt/KF6/图形库。脚本 `build-display-settings.sh`、`build-kwin.sh`、`package-display.py` 保留构建/打包入口。

旧KWin配置的scale=1与实际scale=2不一致，迁移时先保存原文件；新默认原生分辨率和scale=3写入一次性迁移标记，后续不覆盖用户选择。内置屏幕的物理毫米尺寸采用Android报告的xdpi/ydpi换算，供KWin自动缩放识别。

## 19:03实机进展

APK1.8/versionCode9、KWin+moto5、KScreen+moto1已部署。实际渲染1080×2400，KScreen和Qt均为scale3、逻辑360×800。KDE显示页已显示物理/渲染分辨率、缩放和自动/30/60/90/120Hz；四个固定档位经kscreen-doctor设置后，Android物理mode分别为4/3/2/1，实际刷新率吻合。GUI应用“自动”后policy=0，触摸升至120Hz，随后交还Android调度，观察到60/90Hz。证据为rate-validation.json、display-*.txt及auto-validation.json。

用户随后要求量化Vulkan并评估桌面合成，当前转入51篇。缩放/分辨率的完整GUI应用与倒计时还原、方向切换仍需继续验收，不能因此把本篇所有验收项标记完成。固定120Hz/原生尺寸在后续KWin重启测试中持续复核。

## 后续回归记录

KScreen+moto2拆分刷新率选项、已选策略和实时显示信息的变更信号，避免每秒更新实际刷新率时重建选项模型。退出旧设置进程、重新启动后，GUI选择自动并应用，实际refreshPolicy=0；证据为moto2-gui-auto.json和moto2-auto-dialog-later.png。此时的固定120Hz切回检查遇到前台被安卓相机/文件选择器替换，已停止自动点击；moto2-gui-fixed120.json仍为0，不能视为切回成功。完整应用/还原及缩放/方向回归仍待完成。

六个当前显示组件安装包已收集至refs/plasma-display-settings-20260923/packages/，附SHA256SUMS；包含KWin+moto5五包及KScreen+moto2，未重新封装整套ROM。安装后dpkg --audit无输出，apt-get check通过。

## 2026-09-28 X70 Air Pro 的 300% 上限核查

用户反馈 300% 仍偏小。本轮只读核查，未修改用户缩放或重启桌面。精确设备 ZY22MHZKFT（ADB 5038）的 KScreen 实际输出为 WL-0、1264×2780、scale=3，逻辑工作区 422×927；Android display.ini 同样报告 1264×2780。证据：`.work/ci/runs/vantage-20260928-onboarding/device/display-scale-investigation.log`。

上限有两层：KDE KScreen Plasma/6.6 的 [OutputPanel.qml](https://github.com/KDE/kscreen/blob/Plasma/6.6/kcm/ui/OutputPanel.qml) 中 Slider `to: 300`、SpinBox `to: 3.0 * factor`（GPL-2.0-or-later）；项目当前 `packages/kwin/debian/patches/rungic/android-backend.patch` 的 `AndroidBackend::applyOutputChanges` 也拒绝用户请求 `scale > 3.0`。旧的一次性迁移脚本仍使用 `SCALE = 3.0`。本篇原来的默认值依据是 1080 像素短边配 360 逻辑像素，并非 X70 Air Pro 的硬件上限。

按本机原生尺寸计算，350% 约为 361×794 逻辑像素，400% 为 316×695；这只是布局尺寸计算，尚未验收这些档位。提高可选范围须同时核对设置界面和 KWin 后端，沿用标准输出缩放与应用/还原机制；不能仅改 UI 或只放大字体便宣称解决。后续实现前核对固定上游版本和协议校验，验证缩放读回、触摸、旋转、挖孔、键盘及设置还原，默认策略须区分机型且保留用户已选值。

### 密度与手机显示大小策略的进一步核查

本机 `wm density` 报告基础逻辑密度 480（无 override），`dumpsys display` 报告 xdpi=445.9111、ydpi=449.75797，即面板报告的物理尺寸约 72×157 mm；这些是设备报告值，未用尺实测。证据：同目录 `display-density-investigation.txt`。Android 的逻辑密度并非面板 PPI，480/160=3 是 Android dp 到像素的倍率，不能据此保证 Plasma 控件与 Android 控件等大；两者的字体、布局和触摸区域设计不同。参考 [Android 密度说明](https://developer.android.com/training/multiscreen/screendensities)。

进一步查 KDE Plasma/6.6 [OutputConfigurationStore::chooseScale](https://github.com/KDE/kwin/blob/Plasma/6.6/src/outputconfigurationstore.cpp)：手机内屏使用目标逻辑密度 150、最小逻辑尺寸 360，自动缩放另有限幅 3.0，并以 5% 取整。因此前述沿用旧机默认值不是 300% 的唯一来源；上游自动策略按本机报告值计算也接近 3。项目启动包装器已有短边/360 的初值，但最终输出还受 KWin 保存配置、自动生成和迁移影响，不能仅据启动参数认定默认值实际生效。

后续策略建议（尚未实现）：以实际可读性和触摸尺寸校准默认值，使用 `逻辑尺寸=渲染像素尺寸/倍率`，并以 `控件毫米数=逻辑尺寸×倍率/渲染像素密度×25.4` 核验。以 48 逻辑像素的假设点击区域作计算示例，本机原生模式 100%/300%/350% 分别约为 2.7/8.2/9.6 mm；这不是声称所有 Plasma 按钮均为 48。约 360 的手机逻辑短边可作为本机候选，1264/360≈3.51，取 350% 后需验证布局。不能将所有尺寸的平板、折叠屏、外屏统一为 360。

普通用户设置宜提供相对本机推荐值的显示大小档位，必要时在高级设置展示真实倍率，避免将像素一比一的 100% 当作手机推荐值。可选边界按文字/触摸下限及最小可用布局确定，不按固定百分比截断；400% 的逻辑宽仅 316，需验证窄屏布局。切换到 720 短边渲染时，为维持约 360 逻辑短边，倍率应同步约为 2；旋转不改变用户显示大小，外屏使用独立策略。这些为研究建议，未修改当前设备。


2026-09-28：手机显示大小公共策略已重构，KWin 默认/保存/渲染分辨率补偿和 KScreen 五档 UI 共用 `shared/display-policy/`，移除固定 300% 上限与旧迁移。X70 Air Pro 默认 350%，已有设置保留；最新版本、GUI 恢复与实机边界见 [85 篇](85-phone-display-size-policy.md)。

## 2026-10-01 旋转：由 Android 转动，Linux 只跟随

**现象**（用户报告，G100 S）：
- 在“设置 → 显示”里把手机屏转成横屏后，画面只占上面约 45%，下面全黑。
- 用户随后强制停止了 APK，会话重启后仍然错位。
- 恢复竖屏后，主屏的搜索框、图标和小部件都超出右边。
- 横屏时，Android 的状态栏和手势导航条叠在 Plasma 的面板上，左侧还留了一条挖孔安全区的黑边。

**原因**（源码核实和实机日志）：

1. **KWin 内部旋转**：设置里的旋转是 KWin 的标准输出变换。Android 后端照单全收（`WaylandOutput::applyChanges` 设置 `next.transform`），同时把 2400×1080 的模式通过 `display-set` 交给宿主。宿主于是分配了横向缓冲区（`AHB allocated 2400x1080`），而 Android 窗口仍是竖屏，KWin 把旋转后的画面画进了这块缓冲区。这个变换还被写进了 `kwinoutputconfig.json`，会话重启后又被重放。
2. **主屏边距**（plasma-mobile 6.6.5，上游 master 也一样）：
   - `HomeScreen.qml` 在 `Component.onCompleted` 时计算边距，这时根项还是 0×0，右、下边距算成 −360、−800，页面成了屏幕的两倍宽。
   - 之后只在容器的 `availableScreenRectChanged` 时重算，但 libplasma 在容器 `screen()` 为 −1 时会丢掉这个信号（`containment.cpp:75-77`），边距就一直不更新。
   - 实机只读查询：Folio 容器的 `screen` 为 −1，可用区域正确。
3. **APK**：横屏时把挖孔安全区设成了窗口的左右内边距；旋转不会触发窗口焦点变化，所以 `immersive()` 没有被再次调用，系统栏留在屏幕上。

**修复**：

- **KWin**（`packages/kwin/.../phone-turns-with-android.patch`）：
  - 手机屏收到 90°/270° 的变换，或者方向与当前相反的模式时，通过平台桥 `{"op":"orientation"}` 请 Android 转动，和设备面板用的是同一个请求；
  - 转动按相对方向理解：竖屏转横屏，横屏转竖屏；
  - 手机屏在 KWin 内部不再接受任何变换，用户设置的和启动时重放的都一样。
- **plasma-mobile**（`homescreen-margins-follow-geometry.patch`）：边距改为监听 `PlasmoidItem` 的信号，并在尺寸变化时按事件循环合并重算；0 尺寸时不计算，边距一律不小于 0。
- **APK**：
  - 横竖屏都铺到屏幕边缘，不再保留 Android 的挖孔安全区（用户要求）；
  - `onConfigurationChanged` 时重新隐藏系统栏。
  - `desktop/display.py`：只有挖孔落在状态栏那一行时，才用侧边安全区作为状态栏的左右内边距，其余情况用默认的 24。
- **现场恢复**：
  - 手机屏改回 1080×2400，不旋转；
  - 恢复时指定 `@120` 让宿主变成了固定 120Hz，已经切回“自动”（`refreshPolicy 0`）；
  - 出错时的配置保存在手机的 `/tmp/kwinoutputconfig.broken-192028.json`。

**实机验证**（开发覆盖：KWin `+dev20261001t103229`，plasma-mobile 和 rungic-plasma-session `+dev20261001t104608`，APK 2.28 开发版；重启会话后）：
- 用 `kscreen-doctor output.WL-0.rotation.right`（和设置界面走同一个接口）：Android 转成横屏，KWin 为 762×360、不旋转，平台桥报告 `landscape`，画面铺满。
- 新 APK 装好后，横屏宽度为 800（挖孔不再留边），用户确认挖孔部分正常。
- 主屏边距修复后，横屏的小部件和收藏栏都在屏幕之内。

**还没验证**：
- 从设置界面直接点击旋转，以及转回竖屏；
- 多次往返；
- 键盘、触摸、应用窗口；
- 桌面模式时第二输出的重叠；
- 会话重启后不再错位；
- Halcyon 主屏。

### 横屏时小组件的布局（2026-10-01，用户选了方案 1）

**调研**（资料和源码）：
- **Folio 为什么转置**：MR !372（2023-10）引入了“横屏时行列互换”，好处是横竖屏的位置一一对应；后来加入的小组件也沿用了这套做法。上游 README 的待办里有 “option to turn off row/column swap”，master（3764c4364，2026-09-29）还没有做。
- **Android Launcher3**：横屏时网格的行列不变，格子变扁（`CellLayout` 用的是 `inv.numColumns/numRows`），侧边快捷栏布局下隐藏图标文字。小组件会收到竖屏和横屏两种像素尺寸。
- **iPad、三星 One UI**：小组件的尺寸和比例都保持不变。
- **结论**：只有 Folio 会把小组件的宽高对调。

**实现**：
- **plasma-mobile `folio-landscape-keeps-widget-shape.patch`**：
  - 新增设置项 `swapRowsColumnsInLandscape`，默认关闭，在主屏设置里，附中文翻译；
  - 关闭时横屏保持 `RegularPosition`，网格和小组件的形状都不变，保存的布局也一样；
  - 横屏时主屏上的图标隐藏名称（格子太矮，放不下）。
- **小组件**（`agent/suggestions`）：
  - 建议卡片高度小于 200 时为“矮”布局：标题单行，边距收紧；
  - 用量小组件高度小于 90 时为单行布局：名称后面直接跟各项用量的百分比和进度条，不显示重置时间；
  - 组件画廊加了横屏尺寸的样例。

**实机验证**（G100 S，开发覆盖 `+dev20261001t112011`）：
- 画廊在手机上以 offscreen 方式渲染，退出码 0，没有 QML 警告；横屏卡片的样例排版正确。
- 主屏用 `kscreen-doctor` 往返旋转：
  - 横屏时建议卡片横向、标题单行、按钮完整；用量小组件单行显示两项用量；收藏栏在右侧；没有黑边，没有 Android 系统栏；
  - 回到竖屏，布局复原。
- 横屏时的应用抽屉：搜索框居中，8 列图标完整，导航栏不再压住文字。

**发现的问题（还没修）**：在横屏下打开抽屉，搜索框会拿到焦点；关掉抽屉、转回竖屏后，键盘自己弹了出来，收起键盘后又出现了 “Paste” 气泡，点一下主屏空白处才消失。


### 2026-10-05：只由设备面板转手机

**现象**（G100 S，用户没有动手机）：桌面自己在竖屏和横屏之间来回转。dev 发布 20261005.1 两次部署，都在冒烟验收途中转成横屏，`input.text` 打不开抽屉，于是自动回滚。

**原因**：
- `kwinoutputconfig.json` 里手机屏 WL-0 还保存着 `transform: Rotated270`，是 10 月 1 日那次旋转留下的。
- 客户端提交配置时会带上整个输出状态，只要其中带着这个 270°，上面的补丁就当成「请 Android 转」，而且按相对方向转：竖转横、横转竖。所以会被反复翻转。
- 受控复现：每执行一次 `kscreen-doctor output.WL-0.rotation.right`，平台桥报告的方向就翻转一次；`rotation.normal` 不会转回来。
- 安卓本身没有自动旋转：Rungic 的窗口锁着竖屏，物理显示一直是 1080×2400。

**修复**（用户要求「只有用户在设置里选了方向才转」，`phone-orientation-from-device-panel.patch`）：
- 手机方向只认设备面板里的「屏幕方向」（竖屏 / 横屏 / 跟随系统，默认竖屏），由 Android 转窗口，宿主给出转后的尺寸，KWin 按普通的尺寸变化接收。
- 任何配置都不再让 KWin 请 Android 转：带 90°/270° 变换或方向相反的模式时，这部分连同随它一起的尺寸、刷新率请求都忽略，缩放等其余部分照常生效。
- 手机屏的变换始终为 `Normal`。KDE 显示设置里的旋转对手机屏不再生效。
- 现场：手机上的 `kwinoutputconfig.json` 已备份为 `kwinoutputconfig.json.before-rotation-fix-20261005`。受控复现最后一步「转回正常」已把 WL-0 存回 `Normal`。

**实机验证**（G100 S，KWin 开发覆盖 `+rungic10+dev20261004t171719`，会话重启后）：
- 刷新率 60 → 90 → 120 Hz：不转。
- 连续两次提交带 `rotation.right` 的配置：不转，安卓仍是竖屏。
- 设备面板选横屏：安卓横过来，KWin 跟着变成 800×360、变换 Normal，画面铺满。横屏时切换刷新率也不转。
- 设备面板选竖屏：转回 360×800。
- 保存的 WL-0 变换始终是 `Normal`。
- `display.geometry`、`input.text` 验收通过。
