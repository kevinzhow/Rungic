# Miracast投屏桌面与手机触控板：可行性分析

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-24（含root条件下的补充），XT2537-4 / Android16 / KWin 6.6.6 / Plasma Mobile 6.6.5。前半部分是调研与方案；第1–3步原型已实现并实机验证（见各节），“已核实”指本机或源码中确认过的事实，其余为推断。

## 目标

Linux桌面通过Miracast显示到电视/显示器，外屏进入Plasma Mobile的桌面（Docked）模式；投屏期间手机屏幕作为触控板和键盘。

## 已核实的事实

| 环节 | 结果 | 依据 |
|---|---|---|
| Android Miracast源端 | 可用：`WifiDisplayAdapter featureState=3`（开启），`wifi_display_on=1`，Wi-Fi P2P可用；装有`com.qualcomm.wfd.service`与Moto WFD overlay | `dumpsys display`、`pm list packages` |
| 用户发起连接 | `android.settings.CAST_SETTINGS`解析到AOSP `WifiDisplaySettingsActivity`（系统自带无线显示设置页），普通应用可直接打开 | `cmd package resolve-activity` |
| 应用自行发起连接 | 不行：`CONFIGURE_WIFI_DISPLAY`为`signature|knownSigner`。root进程（uid 0）通过权限检查，可用`app_process`调用DisplayManager隐藏接口（scan/connect/disconnect），属待验证推断 | `dumpsys package permission` |
| Moto Ready For | 已预装（`com.motorola.mobiledesktop` 08.0.3，含`wfd.hce.SourceHceService`），是Moto自家的Android桌面模式；它接管外屏时外屏跑的是Moto桌面，不是给第三方的演示屏 | `dumpsys package` |
| Plasma Mobile外屏行为 | 6.6.5已内置：`KScreenOSDProvider`在输出数>1时自动打开`convergenceModeEnabled`，拔出后恢复；`convergentwindows`脚本在该模式下给窗口加边框、不强制最大化；快捷设置有Docked Mode开关 | `vendor/plasma-mobile` |
| KWin运行时增加输出 | Wayland嵌套后端已有`createVirtualOutput/removeVirtualOutput`（KDE虚拟显示器投屏使用），每个输出在宿主上是一个独立的xdg_toplevel | `vendor/kwin/src/backends/wayland/wayland_backend.cpp` |
| 宿主输入 | 已有Touch/Trackpad/Mouse三种模式、相对位移与点击注入、Android按键与文本提交通道 | `native/plasma/src/compositor.rs`、`input_router.rs` |

## Root条件下的目标架构（2026-09-24补充）

原则：Linux拥有桌面、设备语义和用户交互；Android只当“显示/无线/编码驱动”。外屏在Linux里应像插上一台显示器，手机应像接上一块触控板和键盘，接收端在Plasma里选择。

补充核实（root）：

| 项 | 结果 |
|---|---|
| 网络 | 容器`lxc.net.0.type = none`，与Android共用网络命名空间，能直接使用P2P组网卡上的连接 |
| Wi-Fi P2P | Android wpa_supplicant控制socket在`/data/vendor/wifi/wpa/sockets/wlan0`，`p2p0`网卡存在 |
| 触摸屏 | `chipone-tddi`（event8，TDDI液晶，触摸依赖面板供电）；`/dev/uinput`在Android侧可用 |
| 容器设备 | 容器内暂无`/dev/input`、`/dev/uinput`，未运行udevd；需在LXC配置直通并处理设备属性 |
| KWin输入 | `InputRedirection`支持同时挂多个输入后端；libinput后端目前只在DRM后端创建，只依赖Session，可在嵌套模式下额外挂载 |
| Linux Miracast发送端 | GNOME Network Displays 0.99.0（2026-01）：WFD P2P（经NetworkManager+wpa_supplicant）、MICE（Miracast over Infrastructure，无需P2P）、Chromecast；无界面守护进程与D-Bus管理接口；画面来自portal ScreenCast，GStreamer编码 |
| 发起WFD连接 | root进程通过`CONFIGURE_WIFI_DISPLAY`检查（AOSP对root/system uid直接放行），可用`app_process`调用DisplayManager隐藏接口（scrcpy同类做法），待实测 |

### 输出：外屏作为KWin的热插拔输出

- 触发：宿主检测到外屏后，经平台桥调用KWin（本地补丁，D-Bus）`createVirtualOutput`；断开时`removeVirtualOutput`。KScreen、每输出缩放（电视宜1.5–2）、Plasma Docked模式全部按标准路径生效。
- 传输引擎A（默认）：Android高通WFD栈。宿主在WFD显示上建`Presentation`，第二个toplevel经零拷贝（UBWC）子层提交；SurfaceFlinger合成进WFD编码面（一次GPU/HWC合成），硬件编码、音频、HDCP由厂商栈负责，接收端兼容性最好。
- 传输引擎B（进阶）：Linux侧自有发送端。基于GNOME Network Displays的RTSP/WFD协商与封装，P2P改由Android wpa_supplicant（root下经控制socket设置WFD IE与发现/连接，或经宿主`WifiP2pManager`）提供；另可直接走MICE（无P2P，保留现有Wi-Fi与VPN）和Chromecast。画面从KWin输出取：最短路径是让KWin直接渲染进MediaCodec输入Surface的缓冲（宿主出租这些AHB，零拷贝编码），可设低延迟编码参数（无B帧、帧内刷新、60fps）。
- 两者共用同一个Linux侧D-Bus服务（接收端列表、连接、断开、状态），Plasma快捷设置与设置模块只面向该服务；引擎A的扫描/连接由root辅助进程调用隐藏接口实现。

### 输入：手机作为真实的Linux触控板与键盘

- 极致路线：投屏期间root守护进程`EVIOCGRAB`独占触摸屏（Android不再响应），把多点触控原始事件写入一个带`INPUT_PROP_POINTER/BUTTONPAD`与物理分辨率的uinput触控板；该设备直通进容器，KWin在嵌套模式下额外挂libinput后端读取它。由此得到libinput级的加速曲线、防误触、轻点/拖拽、双指滚动与KWin三/四指手势，系统设置里的触控板模块也直接可用。需要处理：Android InputReader也会看到新uinput设备（root下经隐藏接口禁用该设备）、容器内设备节点与udev属性（`ID_INPUT_TOUCHPAD`）。
- 稳妥路线：宿主把Android MotionEvent处理成wl_pointer相对位移、带finger来源的滚动和`zwp_pointer_gestures_v1`手势发给KWin（嵌套后端已绑定宿主的手势协议）。无内核改动，但加速与轻点逻辑要自己实现或移植libinput算法。
- 键盘：APK内自带键盘视图，发送evdev键码（同样可走uinput键盘或wl_keyboard），由Linux侧Rime组字，Ctrl/Alt/Meta快捷键完整；蓝牙实体键盘照常可用。
- 手机屏幕：TDDI触摸依赖面板供电，不能关屏；投屏时用root把背光降到0并显示纯黑，省电且触摸仍有效（待实测）。

### Root带来的系统保障

- 投屏是长时间高负载场景：宿主启动后由root把其`oom_score_adj`设为-1000，避免重演57篇Elisa事故中前台宿主被lmkd杀死；另加容器GPU内存看门狗。
- P2P与STA同信道（root下设置P2P操作信道等于当前Wi-Fi信道），避免多信道切换带来的带宽与延迟损失。

### 分阶段验证（每步都是小原型）

1. root辅助进程扫描/连接接收端；宿主在外屏显示Presentation测试图；记录显示标志、分辨率、端到端延迟（需要一台Miracast接收端）。
2. KWin热插拔第二输出+宿主多窗口绑定，外屏出现Plasma并自动进入Docked模式。
3. 输入：先做宿主级触控板+手势；并行验证uinput触控板+嵌套libinput后端。
4. 声音、断开重连、宿主重启恢复、功耗温度；然后评估引擎B（MICE/Chromecast/自有编码）。

### 现有实现调研与自研边界（2026-09-24）

- KDE：本轮在invent.kde.org按“miracast”“wifi display”检索未找到Miracast发送端项目（只能记为本轮未找到）。相邻组件：KDE Connect的`virtualmonitor`（把另一台装有KDE Connect的设备当扩展屏，走RDP/VNC，不面向电视）、`mousepad`/`remotekeyboard`（手机当触控板/键盘，需切到KDE Connect应用）、krfb/KRdp（VNC/RDP服务端）。
- GNOME Network Displays：GPL-3.0，本轮查到的唯一仍在维护的Linux Miracast发送端（0.99.0，2026-09-22仍有提交）；不绑定GNOME，经portal ScreenCast取画面，可配合xdg-desktop-portal-kde。
- 更底层的库：MiracleCast（albfan维护，2026-03有提交，以接收端为主，发送端不完整）；Intel WDS（RTSP状态机库，LGPL-2.1，2022年已归档）；gst-rtsp-server（GND在其上实现WFD）。AOSP早期的libstagefright WFD发送端已移除，现由厂商（本机为高通）闭源实现。
- 协议本身：Wi-Fi Direct（带WFD信息元素）→ RTSP能力协商（M1–M7，保活M16）→ RTP承载MPEG-TS（H.264，LPCM/AAC）；HDCP 2.x与UIBC可选。自研最小发送端的工作量主要在接收端兼容性、P2P建链可靠性与音画同步，协议状态机与封装本身不大。
- 自研的合理位置是宿主原生层：编码器（MediaCodec）与Wi-Fi P2P都在Android侧，且只有宿主能让KWin直接渲染进编码器输入缓冲实现零拷贝。结论：先用高通WFD栈（引擎A）打通完整体验并量化延迟，确有必要再在宿主Rust里自研引擎B，并以引擎A为基准对比。

### 用户需求（2026-09-24确认）

- 有一台支持的电视。
- 投屏时手机继续显示Plasma移动界面（两个输出同时渲染）。
- 手机屏幕上有可拖动的悬浮控制按钮，在“手机正常触控 / 触控板（控制电视指针）/ 键盘（向电视上的窗口输入）”之间切换。按钮做在宿主Activity内（Android视图层），不依赖KWin状态、响应即时；触控板模式是覆盖在手机界面上的半透明层，键盘模式在下方显示键盘、上方保留触控板区域。

### 测试与观测方法（手机与开发机K8不在同一地点）

Miracast需要Wi-Fi Direct近距离直连；手机经VPN连到K8，K8（Intel AX200，支持P2P-client/GO/device）不能当接收端。改为真实电视接收、agent在手机侧观测：

- 画面：WFD显示是Android的独立显示，root下`screencap -d <显示ID>`取编码前画面（若为安全显示被拒，用下一项）。
- 码流：高通`wfdconfig.xml`的`RTPDumpEnable`保存发出的RTP流，拉回K8解码，核对分辨率、码率、关键帧间隔与画面内容。
- 协商：logcat中的RTSP协商与会话状态，用于定位兼容性失败发生在哪一步。
- 延迟：手机侧从出帧到发包自动测；电视解码显示部分需一次人工观测（手机与电视同时显示毫秒时钟并拍照）。
- 自研发送端（引擎B）的协议调试不受距离限制：P2P只负责建链，RTSP协商与RTP传输是普通IP流量，可经VPN连K8上的软件接收端调试。

## 实测：系统投屏卡在“正在准备”（已修复）

2026-09-24，接收端TCL 85Q6H（广播R1与R2两个入口），另试“客厅电视”与DIRECT-9c4hVFLO，结果相同。

- 现象：六次尝试都能完成Wi-Fi Direct建组（手机为组主，GO intent 15，`p2p0` 192.168.49.1/24，2.4GHz 2417MHz），但高通`ExtendedRemoteDisplay`一直停在`ESTABLISHING`，约10秒后以“Why on earth is surface null??”拆除，电视停在“正在准备”。
- 排除：SwiftWire VPN只接管`10.77.0.0/24`与`198.18.0.1`（分流），其路由表没有IPv4默认路由，P2P流量不经隧道；用户关闭VPN后同样失败。`wfdservice64`按会话由`vendor.wfdservice64`属性启停，属正常。
- 定位：高通WFD进程读不到调试属性，几乎不打日志。用户授权后临时`setenforce 0`，投屏立即成功；宽容模式下记录的WFD相关拒绝为：`vendor_wifidisplayhalservice_qti`的`capability net_raw`（每次会话失败前约0.7秒出现，推断用于把socket绑定到P2P网卡）与`vendor_media_data_file`目录search、`vendor_wfdservice`查找`vendor.perfservice`、`vendor_wfdservice/vendor_wfd_app`读取`vendor_wfd_sys_debug_prop`。
- 修复：`shared/android/wfd.sepolicy.rule`只放行上述四组；`shared/android/moto-wfd-sepolicy.sh`安装为`/data/adb/service.d/moto-wfd-sepolicy.sh`，每次开机用`magiskpolicy --live --apply`加载`/data/adb/moto-wfd/wfd.sepolicy.rule`（与Docker规则同一方式）。SELinux恢复Enforcing后再次投屏成功，之后没有新的WFD拒绝；临时打开的`persist.vendor.debug.wfd*`已用`resetprop -p --delete`删除。
- 连接后的状态：WFD显示`TCL 85Q6H-9E92[R1]`，1920×1080@60，`activeDisplayState=2`；Moto启动器在该显示上启动`SecondaryDisplayLauncher`（外屏是可运行Activity的副屏，而非只能镜像），这对后续由宿主接管外屏有利。
- 未验证：`net_raw`以外的三组规则是否必要（为减少用户往返一并放行）；重启后规则自动加载需在下次重启时确认。

## 第1步原型：root投屏控制与接管电视画面（2026-09-24）

### root投屏控制：`moto-cast`

`shared/android/moto-cast/`：Java源码经javac+d8编为`moto-cast.jar`，装在`/data/adb/moto-wfd/`，由root经`app_process`运行，反射调用`DisplayManagerGlobal`隐藏接口（root通过`CONFIGURE_WIFI_DISPLAY`检查），输出JSON：`status`、`scan [秒]`、`connect <地址|名称> [秒]`、`disconnect`、`decor <显示ID> [on|off]`。实测按名称约3–6秒连上。

实现中踩到的两处框架行为：

- Android 16的`DisplayManagerGlobal.startWifiDisplayScan`不再隐式注册显示回调，调用方必须先`registerDisplayListener`，否则抛`IllegalStateException`（未捕获时app_process以SIGKILL结束，表现为“Killed”）。
- `WifiDisplayController`只连接当前发现列表中的对端，扫描开始或停止都会清空该列表，而`WifiDisplayStatus`里可能仍显示上一轮的“可用”。因此连接请求须在扫描进行中发出，并在状态仍为未连接且对端重新出现时每3秒重发。修复前的若干次“停用Ready For后连不上”实验结论因此作废并已重做。

### 接管电视画面：`CastTest`（APK1.26）

平台桥`cast-test`（`{"enabled":true|false}`或仅查询）：在Presentation类显示上放测试图案（彩条、毫秒时钟、帧计数、移动色块），手机中央叠加同源时钟用于拍照测延迟。

- WFD显示：`FLAG_PRESENTATION | FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS | FLAG_TRUSTED | FLAG_OWN_DISPLAY_GROUP`，1920×1080@60，160dpi，type WIFI，无`FLAG_SECURE`；每次连接逻辑显示ID都会变。`screencap -d <SurfaceFlinger虚拟显示ID>`（`dumpsys SurfaceFlinger --display-id`）可截取合成后的电视画面。
- 遮挡：Moto Ready For在首次连接时弹出模式选择`MotoDesktopSplash`（窗口类型2938），并在该显示上放任务栏`MotoTaskBar`（`com.motorola.systemui.desk`，导航栏类窗口）与副屏启动器。Presentation位于二者之下；普通窗口无法盖过导航栏层级；`setShouldShowSystemDecors(false)`被显示自身的`FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS`覆盖，无效。
- Ready For不能整体停用：`com.motorola.mobiledesktop.core`参与P2P发现，`com.motorola.mobiledesktop`参与发起连接（Moto改过的框架把连接交给它）。只停用任务栏包`com.motorola.systemui.desk`后连接正常，任务栏消失。当前设备上该包处于`disabled-user`状态，恢复命令：`pm enable --user 0 com.motorola.systemui.desk`。
- 窗口：改用`TYPE_APPLICATION_OVERLAY`（清单声明`SYSTEM_ALERT_WINDOW`，root下`appops set dev.moto.plasma SYSTEM_ALERT_WINDOW allow`），`FLAG_NOT_FOCUSABLE`使输入焦点始终留在手机；不能加`FLAG_NOT_TOUCHABLE`，否则Android 12+对不可触摸的悬浮窗限制最高0.8不透明度（画面透出底层）。结果：电视上为不透明、整屏1920×1080的测试图案。
- 焦点：向电视显示注入点击会把`mTopFocusedDisplayId`切到外屏，手机上的宿主不再是顶层resumed（平台桥随即拒绝请求）；正式方案中电视上的输入必须走宿主自己的路由，不能让Android焦点切过去。
- 帧节拍：测试图案在主线程Choreographer上重绘，跟随手机120Hz（焦点在外屏时为60Hz），而WFD显示为60Hz；正式实现须按外屏的60Hz出帧。
- 延迟：待拍照测量。

## 第2步：电视成为KWin的第二个输出（2026-09-24）

结果：手机继续显示Plasma移动界面，电视上是同一会话的第二个输出`CAST-1`（1920×1080，缩放1.5，逻辑1280×720，位于手机输出右侧）；Plasma Mobile自动进入docked（convergence）模式，窗口可移到电视上并带标题栏。断开/重连多次，KWin、plasmashell保持运行，docked模式随之开关。延迟未测（按用户要求暂缓）。

```
电视 ◀─WFD─ Android WFD显示 ◀─ 宿主悬浮窗PlasmaCastDesktop（SurfaceView，Presenter零拷贝）
                                              ▲ dmabuf/AHB
宿主Smithay：wl_output "Moto Cast" ◀─bind─ KWin(moto11)：输出CAST-1的xdg_toplevel
                                              set_fullscreen(该wl_output) → 宿主认领为投屏窗口
手机屏幕：宿主原有窗口/Presenter ◀── KWin输出WL-0（不变）
```

链路：

1. 宿主平台桥`cast-desktop`（`{"enabled":true|false}`）：`CastDesktop`在Presentation类显示上加`TYPE_APPLICATION_OVERLAY`悬浮窗（与第1步相同的不透明、不可聚焦参数），内含固定为显示模式尺寸的SurfaceView；`surfaceChanged`经JNI`bindCastSurface(surface,w,h,刷新mHz)`交给原生层，`surfaceDestroyed`调用`releaseCastSurface`。显示移除（断开投屏）时悬浮窗随之移除。
2. 原生宿主（`native/plasma/src/android/backend/wayland/cast.rs`）：绑定后新增wl_output全局（make“Moto”、model“Cast”、1000×563mm、缩放1），释放时撤回该全局。KWin对它的xdg_toplevel发出`set_fullscreen(该输出)`时，`fullscreen_request`把它认领为投屏窗口：配置为电视尺寸、映射在空间坐标(100000,0)、不参与手机的渲染列表、触摸命中和焦点选择（新窗口抢到的键盘焦点还给手机窗口）。每帧`render_all`把它的dmabuf留给`present_cast`，经独立的`Presenter`送到电视SurfaceView，完成后同样调用`finish_presentation`回帧回调；帧时钟停摆判断把未结清的投屏Presenter视为忙。
3. KWin嵌套后端（vendor/kwin，`6.6.6-0ubuntu0.1+moto11`）：`WaylandDisplay`以KWayland `Output`绑定宿主的wl_output并跟踪增删；`WaylandBackend`对make/model为Moto/Cast的宿主输出创建`CAST-n`输出（热插拔，发`outputAdded`与`outputsQueried`），其xdg_toplevel以该宿主wl_output请求全屏；制造商、型号、物理尺寸、刷新率取自宿主输出。`MOTO_KWIN_FLAT_OUTPUT`的Android主屏模式与`internal`标记只作用于手机输出，投屏输出按外接显示器处理。
4. Plasma Mobile（vendor/plasma-mobile）：`KScreenOSDProvider`在输出数>1时打开convergence模式，但上游`KScreenOSDUtil`构造时从未调用`retrieveKScreen()`，输出数恒为0，插入外屏也不会切换（上游master同样未修复）。本地在构造函数中调用它；插件在手机上编译后以dpkg-divert覆盖`/usr/lib/aarch64-linux-gnu/qt6/qml/org/kde/plasma/quicksetting/kscreenosd/libkscreenosdplugin.so`。

问题与处理：

- **断开时KWin被宿主断开**：KWin最初以`wl_output` v2绑定，KWayland `Output`销毁时发送`release`（v3起才有），宿主判定协议错误`invalid method 0 (since 2 < 3), object wl_output#44`并断开KWin，会话里的plasmashell、portal等随之以255退出重启。改为绑定v3（KWayland不处理v4的name/description事件）后断开/重连正常。
- **docked模式持久值**：`KScreenOSDProvider`把“插入前的模式”记在持久设置本身，会话在docked状态下重启会把`true`当成初始值，之后拔出外屏也不再退出docked。上面的崩溃期间出现过一次，已手动写回`false`（`kwriteconfig6 --file plasmamobilerc --group General --key convergenceModeEnabled --notify false`）。只要会话不在投屏中重启就不会触发；未改上游逻辑。
- **缩放**：KWin的`chooseScale`对高度>500mm的外接屏按电视目标30.5dpi计算，1000×563mm下给1.35；首次出现时曾以其他模式记下缩放1。现已用`kscreen-doctor output.CAST-1.scale.1.5`设为1.5，写入`kwinoutputconfig.json`并按输出UUID保留；用户可在显示设置中改。
- **Android焦点不能切到电视**：用`input -d <电视显示> tap`调试会把Android顶层焦点移到外屏，之后`am start`会把宿主Activity以自由窗口启动到电视上。宿主`MainActivity`在非默认显示上创建时改为在显示0上重新启动自身并结束（APK1.27）。电视上的指针输入由第3步经宿主路由，不依赖Android的外屏触摸。
- **帧节拍**：投屏窗口目前跟随手机输出的帧时钟出帧，未按电视60Hz单独节拍；Presenter统计计数由两个Presenter共用。

验收方法：`moto-cast connect <电视>`连上后调用`cast-desktop`；`kscreen-doctor -o`应列出WL-0与CAST-1；`screencap -d <电视的SurfaceFlinger虚拟显示ID>`截取电视画面；用KWin脚本（`workspace.sendClientToScreen`）把窗口移到CAST-1确认应用显示在电视上；关闭再打开`cast-desktop`，确认`pgrep kwin_wayland/plasmashell`不变、输出与`convergenceModeEnabled`随之变化。

## 第3步：手机作为电视的触控板与键盘（2026-09-24）

结果：投屏时手机右侧出现可上下拖动的悬浮控制条。手机模式下只有一个“投屏控制”按钮，点它进入触控板；展开后有“手机 / 触控板 / 键盘”三段。

- **手机**：手机照常操作Plasma移动界面。
- **触控板**：手机屏幕盖一层半透明触控面板（Plasma界面仍隐约可见），在电视上移动KWin光标、单击、拖动、滚动。
- **键盘**：触控板加上Android输入法；按键和文字送到电视上获得焦点的Linux窗口。输入法被返回键收起后自动回到触控板。

实机验证：

- 触控板把电视上的Kalk按出7、8。
- 键盘模式下，按键输入的“abc1+2”和经输入法提交路径的“你好，世界”“中文输入”都正确进入电视上的KRunner，电视上不再弹出Plasma屏幕键盘。
- 多次切换模式、重启会话，KWin与plasmashell不崩溃。

```
手机：CastControls（控制条 + TouchpadView，Java手势）
   │ NativeBridge.castPointer(op,x,y)：0开关 1相对移动 2按键 3滚动 4滚动结束
   ▼
宿主 cast.rs：cast_pointer → wl_pointer 进入投屏窗口（KWin输出CAST-n的宿主窗口），位置限制在电视像素内
   ▼
KWin(moto12) 嵌套后端：Pointer::motion → 输出CAST-n上的绝对位置；投屏输出没有光标层，光标由KWin画进电视画面
键盘：Android输入法 → DisplayView InputConnection → 已有按键/文字通道 → 宿主wl_keyboard / text-input-v3 → KWin → 焦点窗口
```

实现要点：

- **触控板手势在Java侧识别**（`CastControls.TouchpadView`）：
  - 单指移动为相对位移，增益约1.3到4倍，随速度增大。
  - 轻点在抬起时按下左键，180ms后松开；这期间再次按下即为拖动（与libinput的tap-and-drag一致），不移动再轻点一次即为双击。
  - 双指移动为自然方向滚动，结束时发送axis stop。
  - 双指轻点为右键，三指轻点为中键。
- **宿主**：投屏指针与手机原有的Touch/Trackpad/Mouse模式分开。`cast_pointer`开启时，座位额外提供`wl_pointer`；手机侧仍是`wl_touch`，控制条之外的触摸被触控面板截获，不会进入手机界面。指针事件只发给投屏窗口，焦点原点取该窗口在空间中的位置。
- **enter时序**：Smithay只在焦点变化时发送`wl_pointer.enter`/`zwp_text_input_v3.enter`，而KWin看到新能力或创建对象后才绑定。因此：
  - 开启触控板时不立即移动指针，由第一次手指移动进入投屏窗口。
  - KWin创建text-input后先主动enable一次。
- **KWin光标**：
  - 嵌套后端原本把光标交给宿主的`wl_pointer.set_cursor`。宿主只把光标画在手机画面上，电视路径是单缓冲零拷贝。
  - 现在投屏输出不创建CursorOnly层，KWin的`assignOverlays`找不到光标层，就把光标画进主层。
  - 手机输出保持原有的光标层。
- **键盘**：沿用`DisplayView`的InputConnection。Latin字符和按键按键码注入。中文、表情等无法用键码表示的文字经text-input-v3提交，见下文。
- **平台桥**：
  - `cast-controls`：查询或设置`{"mode":"phone|touchpad|keyboard"}`。
  - `text-commit`：`{"text":…}`，与输入法`commitText`走同一调用，用于测试和工具。

排查中修复的共享层问题：

1. **KWayland `Output`导致KWin崩溃**：
   - 上游`Output::Private::addMode`（KWayland master仍如此）先把新模式插到QList末尾并记下`currentIt`，然后遍历时`erase`重复模式。`erase`移动元素后`currentIt`失效，循环越过末尾，导致SIGSEGV。
   - 宿主每次改变刷新率（DisplayPacer在60/120Hz间切换）都会重发wl_output mode。KWin从第2步开始绑定宿主wl_output，因此在切换触控板/键盘时崩溃。核心转储显示崩在KWayland监听回调内。
   - KWin改用自有的`HostOutput`直接监听原始wl_output事件，只保留制造商、型号、物理尺寸和当前模式；`set_fullscreen`直接调用`xdg_toplevel_set_fullscreen`。
   - KWin退出时先释放HostOutput再断开连接，否则`wl_output.release`会发到已关闭的连接上，退出时崩溃并打乱会话重启。
2. **Android键码表错位**：
   - `keymap.rs`中所有标点都错了一行，例如逗号被映射为句点、等号被映射为`[`。Home、End、Insert、SysRq以及小键盘除号、乘号、小数点、回车也不对。
   - 已按`linux/input-event-codes.h`改正，并补上Caps Lock、`+`（小键盘加号）、`*`。
   - 影响所有经Android按键事件进入的输入，包括蓝牙键盘和输入法`sendKeyEvent`。
3. **电视每次重连缩放回到1倍**：KWin按输出名保存缩放等设置（这些输出没有EDID）。投屏输出原先每次连接都递增为CAST-2、CAST-3……，于是每次都成了新输出。现在取最小可用编号，电视始终是CAST-1，沿用已保存的1.5倍。
4. **电视上弹出Plasma屏幕键盘**：
   - `KWIN_IM_SHOW_ALWAYS=1`是为手机触屏准备的，现在只对内部输出生效。判断依据是正在输入的窗口所在输出，取不到时用活动输出。
   - 外接输出按KWin原规则处理：最后输入来自触摸或数位板时才弹出。
   - 输入面板的“允许显示”（`InputPanelV1Window::allow`）一经授予就不再收回，因此文本框的自动显示请求（text-input v1/v2/v3）改走`showForTextInput`：不满足上述条件时收起面板。用户经D-Bus手动调出不受影响。
5. **中文经Android输入法提交**：
   - 宿主原有的text-input-v3提交路径（`arabic_input.rs`）从未生效。Smithay在没有input-method客户端时丢弃全部text-input请求，而且KWin本身也不绑定宿主的text-input。
   - 现在Smithay（`native/plasma/lib/smithay`本地修改）在没有input-method时也跟踪启用状态。text-input对象销毁时清除激活记录，焦点离开时总是发送leave。原先KWin重启后，旧连接留下的失效激活记录会让新KWin的enable一直被当成“已有激活实例”而丢弃。KWin嵌套后端绑定宿主的`zwp_text_input_manager_v3`并保持启用，收到的`commit_string`交给KWin输入法的`commitHostText` → `commitString`：焦点客户端有text-input时直接提交，没有时用临时键位注入。
   - 手机模式下的“Android 键盘”菜单也经过这条路径。

仍待处理：

- 延迟未测。
- 手势的增益与轻点阈值只在实机上初调过。
- 手机的Android焦点在键盘模式时仍在宿主Activity，需确认各类输入法的行为（本机为搜狗）。
- KWin在已有投屏触控板时崩溃重启，手机左上角会短暂出现KWin画的光标（已知的启动时光标问题）。

验收方法：

1. `cast-desktop`连接后调用`cast-controls {"mode":"touchpad"}`。
2. 用`input swipe/tap`在手机上操作；`native-stats`的`dispatch=cast_motion x=… y=…`给出电视像素位置，KWin脚本`workspace.cursorPos`应与之一致（逻辑坐标×1.5）。
3. 截取电视画面，确认光标位置与点击效果。
4. 键盘模式下用`input text`和`text-commit`分别验证按键与中文提交。
5. 每一步后`pgrep kwin_wayland`，确认进程ID不变。

## 第4步：自动接管、断开重连、宿主重启、声音与功耗（2026-09-24）

- **自动接管**：
  - `cast-desktop`默认开启，选择记在宿主偏好里。电视一连上（Presentation类显示出现）就显示Linux桌面，无需调用平台桥。
  - 宿主启动或重启时，若电视已经连着，会在合成器就绪后立即接管（`MainActivity`在`resumeRendering`后调用`CastDesktop.refresh`）。
- **焦点**：
  - 连接时Android会在电视上启动副屏主屏（`com.motorola.launcher3/...SecondaryDisplayLauncher`，`startHomeActivity: displayAdded`），并把输入焦点移到电视，导致宿主失去焦点，平台桥拒绝请求（“请先返回 Plasma Mobile”）。
  - 现在宿主在投屏中失去顶层焦点、而自身仍可见时，会在手机显示上把自己重新排到前台，30秒内最多3次，避免与其他程序反复争抢。回到手机桌面等宿主不可见的情况不处理。
  - 修正（APK 1.32）：上滑回桌面时，手势刚开始宿主就失去顶层焦点，但此时仍然可见（Recents动画中的pausing task）。400 ms后上述逻辑把宿主拉回前台，打断了手势（日志：`taking input focus back from the cast display`之后`RecentsController.merge`）。副屏主屏只在投屏显示接入时抢焦点，因此现在只在投屏画面绑定后15 s内抢回焦点。实测投屏中连续上滑可以停在Android桌面；宿主重启后投屏重新绑定时仍会取回焦点。
- **docked模式随插拔正确切换**：
  - 上游`KScreenOSDProvider`把“插入前的模式”从`convergenceModeEnabled`本身读回。宿主重启会让plasmashell在docked状态下重启，于是拔出后仍停在docked。
  - 现在“是否由本开关进入docked”和“进入前的模式”存在独立的`KScreenOSDAutoDock`设置里（QtCore `Settings`）。输出数为0（KScreen配置未到）时不动作。
  - 重新构建`mobileshellplugin`，并以dpkg-divert覆盖。
- **实测**：
  - 投屏中重装APK（宿主重启），约2秒内电视恢复为CAST-1、1.5倍、docked。
  - `moto-cast disconnect`后电视输出移除、控制条消失、docked关闭。
  - 重连后电视恢复Linux桌面、docked打开，焦点回到手机（`mTopFocusedDisplayId=0`）。
  - 电视断开后需要更久才重新可连：40秒超时一次，60秒成功。
- **声音**：
  - Linux经PulseAudio隧道进入Termux的pulseaudio（uid 10348），其AudioTrack由AudioFlinger放在`AUDIO_DEVICE_OUT_PROXY`（“WFD proxy device”）输出线程上，随WFD送到电视，无需改动。
  - 投屏期间媒体音量按proxy设备单独记忆（本机为5级），手机音量键调节的就是它。
  - 本机无法当场听到，以路由证据为准。
  - 另有固定在手机本机播放的`android_phone`输出，供需要在手机上出声的场景使用（语音助手从手机发起时），见[59篇](59-voice-agent.md)“回复从发起的一端播放”。
- **功耗**：
  - 投屏中手机屏幕保持常亮，因为手机熄屏会使宿主暂停渲染、电视画面冻结。触控板/键盘模式下窗口亮度降到0.08，回到手机模式后恢复。
  - `FLAG_KEEP_SCREEN_ON`原先还被Linux侧`keep-awake`操作（视频播放等）单独开关，12秒后被清除，会顶掉投屏常亮。现改为由`MainActivity.setKeepAwake`合并两个来源。
- **功耗实测**：
  - 手机接电源、充电暂停，以USB输入功率计整机。
  - 投屏+手机模式静止：约1.48W。主要占用为`wifidisplayhalservice`约22%，宿主、plasmashell、kwin各约3–4%。
  - 触控板模式：约2.16W，但期间有Firefox页面在后台占用约24%，不能单独归因。
  - 温度：CPU 36–46°C，`quiet-therm`约36–39°C。
  - 未锁频、未改温控。
- **外接屏的电脑版桌面**：
  - **调研**：上游Plasma Mobile（6.5、6.6.5、2026-09-24的master `7606a3ef`）在外接屏上只放第二份Folio移动主屏，没有状态栏和任务栏。
    - `layout.js`只在第0屏建两块移动面板；`ShellCorona::addOutput`给新屏用shell默认容器（folio）。
    - docked模式只改KWin窗口行为（标题栏、不强制最大化、放置方式）。
    - “外接屏换成桌面面板”只见于未合并的草案plasma-mobile!548（2024-07起，会同时替换手机的导航栏，且撤销docked后面板设置丢失）；plasma-workspace!4802（面板`screen`可由脚本设置）为它而做，已合并。
  - **实现**：手机保持移动shell，第1屏及以后改成Plasma Desktop式外观。
    - **布局脚本**：`vendor/plasma-mobile/shell/contents/external-desktop.js`（幂等），经`org.kde.PlasmaShell.evaluateScript`执行。
      - 桌面容器：把Folio换成`org.kde.plasma.folder`。脚本不能直接创建桌面容器，但把Folio挪到一个不存在的屏号时，`ShellCorona::setScreenForContainment`会在原屏新建folder容器并与其交换，随后删除换下的Folio。
      - 桌面文件夹：本机XDG桌面目录被设为`$HOME`，改为显示`~/Desktop`。
      - 底部面板`org.kde.panel`：kickoff、icontasks（只显示本屏任务）、系统托盘、时钟、显示桌面。
      - 结果保存在shell配置中，拔出后保留（screen=-1），插回自动恢复。
      - 判断屏上是否已有面板时，不能只看`screen`（2026-09-28修复）。面板视图要等该屏桌面就绪、再过250 ms定时器才建立（plasma-workspace 6.6.6 `createWaitingPanels`），在此之前`screen`为-1。实测接屏后约0.4–0.45 s内都是这样，桌面加载慢时会超过脚本运行的1 s，于是每次接屏都可能多建一个面板。助理屏上曾累积到4个，底部出现叠影。现在`screen`为-1时，改读面板配置里的`lastScreen`。这台手机上多出的3个面板已手动删除；脚本不做去重，其他设备上已累积的面板需要另行清理。
    - **自动触发**：`KScreenOSDUtil`在输出数大于1或新增屏幕1秒后，异步调用plasmashell自身的`evaluateScript`执行该脚本。已验证：删除面板后插拔一次屏幕，面板自动重建。
    - **小部件弹窗**：移动shell的`CompactApplet.qml`把弹窗做成全屏遮罩窗口，新窗口默认出现在主屏（手机）上，开始菜单因此弹到手机上并占满手机屏。
      - 现在按容器分流：桌面面板和folder桌面里的小部件用Plasma Desktop的`CompactApplet`（`AppletPopup`贴面板弹出，复制自plasma-desktop 6.6.6）；手机上的移动面板保留原实现。
      - `AppletPopup`按所在屏的95%限定尺寸，新窗口先落在手机屏，到电视后只放宽上限不放大。桌面版在`screenChanged`后按内容撑开。
    - **依赖**：新增`plasma-desktop`（kickoff、icontasks等，12个包），已列入`plasma/ubuntu-packages.txt`。
    - **部署**：`libkscreenosdplugin.so`、`libmobileshellplugin.so`与`CompactApplet.qml`以dpkg-divert覆盖，`CompactAppletDesktop.qml`、`CompactAppletMobile.qml`、`external-desktop.js`直接装入shell包目录。
  - **实测**：
    - 电视显示Plasma Desktop式桌面。
    - 开始菜单在电视上贴任务栏弹出且大小完整；从中打开Dolphin，窗口带标题栏出现在电视上，并在电视任务栏高亮。
    - 手机仍是移动界面。
- **窗口规则按屏幕区分**：
  - **问题**：docked模式是全局开关。envmanager把KWin整体切成桌面行为：最大化窗口带边框、居中放置、可拖动、TabletMode关闭。`convergentwindows`脚本也只看全局开关，让所有窗口带边框且不再最大化。于是投屏时手机上的应用出现标题栏，状态栏和导航条不再沉浸（它们随“本屏有最大化窗口”变为与应用一体）。
  - **修复**：
    - KWin（moto13）的脚本输出对象增加只读属性`internal`（即`LogicalOutput::isInternal`）。
    - `convergentwindows`按窗口所在输出选规则：内置屏用移动规则（无边框、最大化），外接屏用桌面规则（有边框）。没有`internal`属性的KWin保持上游行为。
    - 窗口换屏时（`outputChanged`，拖动中则等松手）重新套用规则。由手机最大化过的窗口到电视后取消最大化，改为可用区域70%×80%居中。
    - KWin全局选项仍取docked值，电视上的最大化窗口保留标题栏；手机窗口由脚本单独去边框。
  - **实测**：
    - 投屏时手机上打开的Discover无边框、最大化，状态栏与导航条恢复沉浸样式。
    - 电视上的窗口有边框。Discover送到电视后得到896×541居中窗口，并自动切成宽布局；Firefox送回手机后无边框、最大化。
    - 断开电视后所有窗口回到手机，均为移动样式。
  - **仍为全局、未区分屏幕**：KWin的TabletMode，以及应用自身的移动/桌面形态（会话环境变量，每进程一份）。
- **后台与熄屏时继续投屏（APK 1.33、KWin moto14）**：用户要求Plasma退到后台后电视照常可用。
  - **原先**：Activity进入后台（`onStop`/`surfaceDestroyed`）会挂起渲染（`rendering_active=false`），`render_all`直接返回，电视那一帧也不再收取，电视画面冻结。熄屏同理。
  - **宿主改动**：
    - 挂起期间`render_all`只处理投屏窗口（`render_cast_only`）：取它的新缓冲区交给电视Presenter，只给它发帧回调；手机上的窗口收不到帧回调，KWin随之停画手机输出。
    - 这时合成循环不再跟随手机的Choreographer（后台和熄屏时都没有），而是在Wayland fd与kick上等待，KWin每提交一帧就立即送给电视。电视的节奏由KWin按电视60 Hz自行控制。
    - 投屏窗口的presentation feedback改为按投屏输出和它的刷新率上报（`finish_cast_presentation`）。原先一律用手机的刷新率，KWin会跟着把CAST-1切成120 Hz，后台时又切成30 Hz。每个投屏提交都立即回报，即使没有带来新缓冲区。
    - 同一缓冲区不再重复送往电视。原先手机每渲染一次，都会把电视当前帧重新提交一次；重新绑定Presenter或重新认领窗口后，会补送一次当前帧。
    - 尺寸与缩放不变时，`update_output_mode`不再重新配置窗口。
    - 运行统计（`native-stats`）新增`cast(frames new skipped …)`，其中`new`是电视实际收到的新帧数。
  - **KWin（moto14）**：嵌套后端收到尺寸不变的configure时，只ack，不再`outputsQueried`。
    - 回到前台时宿主会重发这类configure，KWin随之连续多次重建所有输出的scene view。
    - 调试版KWin记录到：此后电视输出不再合成，电视上的客户端收不到帧回调，电视冻结，而且一直不恢复。
    - 这是时序竞争：修复前大多数前台→后台→前台的循环会卡住，偶尔不会。
  - **测试方法**：
    - 电视上放一个30 fps的`gst-launch-1.0 videotestsrc pattern=ball ! waylandsink`窗口（由KWin脚本`workspace.sendClientToScreen`送到CAST-1），读`native-stats`里的`new`计数。
    - 不能用SurfaceFlinger图层帧数：修复前电视帧会随手机渲染重复提交，图层帧数不代表电视有新内容。
    - KWin脚本中的`workspace.screens`不能用for-of遍历，`Qt.rect`也不可用。
  - **实测**（投屏中）：
    - 前台约40帧/秒（测试画面30 fps，另有电视面板等内容）；HOME回Android桌面后约38–41帧/秒。
    - 5轮前台→后台→前台全部恢复，修复前多数卡住。
    - 电源键熄屏（手机`mScreenState=OFF`，手机所在电源组Dozing，WFD显示组保持唤醒）期间约38帧/秒，15秒后仍在；唤醒后正常。
  - **限制**：后台时手机上没有触控板和键盘，电视只能看不能操作，除非另接蓝牙键鼠（未测）。Android 16对后台应用的音频加固只记录了“would be muted”，没有实际静音；未做长时间测试。
- **仍待处理**：
  - 触控板功耗需排除后台负载后重测。
  - 端到端延迟未测。

## 第5步：从Linux发起投屏、自动重连、不再显示摩托副屏桌面（2026-09-24）

用户要求：
- 电视上不要再闪出摩托的投屏桌面（选方案A：停用该组件）。
- 从Linux一侧发起投屏：快捷开关、语音助手、电视端断开后自动重连，三种入口都要。
- 用户自己断开的不要重连。

### 调研

- **AOSP**：`WifiDisplayController`只在连接过程中失败时重试（`mConnectionRetriesLeft`）。本机日志中，电视关闭RTSP后立即`Disconnecting`，没有重试。
- **Android与Moto设置**：本轮未找到“断线自动重连”的系统设置或公开接口。
- **Plasma Mobile上游**：只有KScreen的“显示配置”快捷设置，没有投屏入口。
- **GNOME Network Displays**：它是Linux自己的Miracast发送端，要求Linux直接掌握Wi-Fi P2P。本机的Wi-Fi归Android所有，因此不适用。
- **结论**：继续用Android自带的WFD栈（`moto-cast`），只在其上补入口和重连。

### 电视上不再出现摩托副屏桌面

- **原因**：WFD显示带`FLAG_SHOULD_SHOW_SYSTEM_DECORATIONS`，Android在它上面启动`SECONDARY_HOME`。本机只有`com.motorola.launcher3/com.android.launcher3.secondarydisplay.SecondaryDisplayLauncher`能响应。在宿主悬浮窗盖上之前，它会显示一会儿；每次重连都会闪。
- **处理**：`pm disable --user 0`停用该组件。
  - 之后Android记录`No home screen found … SECONDARY_HOME`（以`am_wtf`记入日志，无其他影响），电视上不再启动任何主屏。
  - 主手机桌面是同包的其他组件，不受影响。
  - 恢复命令：`pm enable --user 0 <上述组件>`。
- **开机检查**：`shared/android/moto-cast-watch.sh`（`/data/adb/service.d`）开机时检查。仍能解析到该组件时才停用，避免每次开机都杀一次启动器进程。
- **实测**：
  - 重连后`PlasmaCastDesktop`在`Connected`之后约0.6秒绑定，其间没有`SecondaryDisplayLauncher`的`wm_create_activity`。
  - `mTopFocusedDisplayId=0`，焦点仍在手机上。
  - 第4步“投屏绑定后15秒内抢回焦点”的逻辑保留，作为其他应用抢焦点时的保护。

### 区分用户断开与电视端断开

`WifiDisplayController`日志（Slog，`system`缓冲区）中两种断开的顺序不同：

| 情况 | 顺序 |
|---|---|
| 手机端请求断开（`moto-cast disconnect`、快捷开关、Android投屏控制） | `Stopped listening for RTSP connection` → `Closed RTSP connection …` → `Disconnecting` |
| 电视端结束会话（2026-09-24 22:40实例） | `Closed RTSP connection …` → `Stopped listening …` → `Disconnecting` |

手机端的请求先把`mDesiredDevice`清空，随后停止监听，之后会话才关闭。电视端结束时，RTSP先关闭，才触发断开。

### 自动重连：`moto-cast-watch`

`shared/android/moto-cast/moto-cast-watch`由root运行，开机时由`/data/adb/service.d/moto-cast-watch.sh`启动：

- 用`logcat -b system -v raw -s WifiDisplayController:I`等待日志行，不轮询。启动时用`moto-cast status`取得初始状态。用PID文件保证只运行一份：mksh会在子进程中关闭`exec 9>`打开的描述符，无法用`flock`。
- 在“已连接”状态下先看到`Closed RTSP connection`，才判定为电视端断开。此时以`reconnect`模式启动自身：
  - 3秒后开始，每次`moto-cast connect last 40`，最长180秒。第4步实测，电视断开后可能要一分钟才能重新连接。
  - `moto-cast status`在此期间报告`"reconnecting":true`。
  - 自动重连成功后120秒内，电视又一次结束会话，就不再重连，按用户在电视上主动退出处理。
- 用户发出的`moto-cast connect/disconnect`都会写入`run/cancel-reconnect`，并结束正在进行的连接进程。重连循环每次尝试前后都检查该标记。
  - 自动重连调用时带`MOTO_CAST_AUTO=1`，不写入取消标记。
  - 较新的请求优先：连接进程的PID记在`run/connect.pid`。
- 日志：`/data/adb/moto-wfd/run/watch.log`。

### `moto-cast`的补充

- `connect`省略目标（或写`last`）：
  - 连上次成功连接的电视。连接成功时，地址和名称写入`/data/adb/moto-wfd/last-sink`。
  - 没有记录时，若Android只记住一台电视，就连这一台；有多台时报错，要求给出名称。
  - 连接成功的判断改为：已连接设备的地址或名称与目标一致。
- `status`增加`reconnecting`。

### Linux入口

```
Plasma快捷开关“投屏” ─┐
语音助手（Codex执行）  ─┼→ /usr/local/bin/moto-cast（Linux，plasma/cast）
                        │     └ 平台桥 {"op":"cast","args":[…]}（APK 1.35，独立线程，最长60秒）
                        │          └ su -c /data/adb/moto-wfd/moto-cast …（root，app_process）
moto-cast-watch（root） ┘ ← 电视端断开时自动重连
```

- **平台桥`cast`**：
  - 只允许`status`、`scan`、`connect [电视]`、`disconnect`；电视名按shell单引号转义。
  - 在单独线程里应答：连接要几秒到一分钟，不能阻塞剪贴板、亮度等请求的单线程循环。
  - 不要求宿主有焦点，后台时也能断开或连接。
- **Linux命令**：`moto-cast status|scan|connect [电视]|disconnect`，输出工具的JSON，失败时退出码为1。查询约0.6秒。
- **快捷开关**：`plasma/cast/quicksetting`，纯QML，Id为`dev.moto.quicksetting.cast`，由`plasma/cast/install.sh`安装，并排在蓝牙之后。
  - 是否在投屏以`Qt.application.screens.length > 1`判断（电视即会话的CAST-1）。
  - 用Plasma5Support的`executable`引擎调用`moto-cast`。
  - 状态文字：未连接／正在连接…／电视名／正在断开…／电视断开，正在重连…（此时点一下即取消）。2026-09-29起点按改为打开选设备面板，见下文“选设备与投屏控制”。
  - 电视消失2秒后查询一次`status`；重连期间每8秒查询一次。
  - 长按打开KScreen显示设置。
- **语音助手**：技能说明新增`moto-cast`一节，Agent提示词也写明用它连接和断开；实时模型把“投屏”“断开投屏”交给Agent执行。见[59篇](59-voice-agent.md)。

### 实测（2026-09-24，TCL 85Q6H）

| 场景 | 结果 |
|---|---|
| `moto-cast disconnect`后等待20秒 | 保持断开，`reconnecting:false`，watch.log没有重连记录 |
| `moto-cast connect`（不指定电视） | 按唯一记住的电视连接，6.7秒连上，并写入`last-sink` |
| 模拟电视端断开：`iptables -I OUTPUT -o p2p0 -p tcp --sport 7236 -j REJECT --reject-with tcp-reset`，出现`Closed RTSP`后删除规则 | 36秒后RTSP关闭，日志顺序与22:40的真实断开相同。watch.log记录“TV ended the session”，11秒后“reconnected”，电视恢复Linux桌面 |
| 快捷开关断开与连接 | 断开后保持断开。连接时显示“正在连接…”，连上后显示“TCL 85Q6H-9E92”；`kscreen-doctor`中有CAST-1（缩放1.5），电视截图为Linux桌面 |
| 语音“把投屏断开。” | Agent执行`moto-cast disconnect`，从说完到口头确认约6秒 |
| 语音“投到电视上。” | Agent执行`moto-cast connect`并用`status`确认，约26秒后口头确认已连上 |

### 点应用图标时，窗口回到点击的那块屏幕

用户要求：应用已被挪到电视上时，在手机上再点它的图标，应把窗口挪回手机。

- **原因**：Plasma Mobile的主屏（Folio、Halcyon）和媒体控件长按，都经`MobileShell.AppLaunch.launchOrActivateApp`打开应用。应用已在运行时，它调用`WindowUtil::activateWindowByStorageId`，后者只发出`PlasmaWindow::requestActivate`。KWin在窗口所在的电视上激活它，手机上看不到任何变化。
- **修改**（vendor/plasma-mobile，所有从Plasma Mobile shell点开应用的入口共用）：
  - `launchOrActivateApp(storageId, screenName)`增加点击所在屏幕的名称。Folio的`AppDelegate`和媒体控件传入`Screen.name`；Halcyon不传时保持原行为。
  - `WindowUtil`经registry绑定各`wl_output`（v4带名称）。窗口中心不在该屏幕的逻辑几何内时，先用`PlasmaWindow::sendToOutput`（`org_kde_plasma_window.send_to_output`，KWin由`Window::sendToOutput`处理），再激活。
  - 移回手机后，`convergentwindows`脚本按输出重新套用手机规则（无边框、最大化）。
- **部署**：在`/root/moto-build/plasma-mobile/build`用ninja构建`libmobileshellplugin.so`、`libwindowplugin.so`和`org.kde.plasma.mobile.homescreen.folio.so`，再由`plasma/install-mobile-plugins.sh`以dpkg-divert覆盖（strip后安装，原文件保留为`.distrib`）。kscreenosd插件也列在该脚本中。之后重启plasmashell。
- **实测**：语音助手窗口在CAST-1上时，在手机主屏点它的图标，窗口移到WL-0并被激活，呈手机样式；电视上的其他窗口不受影响。
- **未覆盖**：
  - 在电视的开始菜单（kickoff）里点一个正在手机上运行的应用：kickoff不走这条路径。单实例应用会自己在原处激活窗口，多实例应用会另开一个窗口。
  - 手机上的任务切换器（KWin效果）。

### 限制

- 用户在电视遥控器上退出投屏时，电视同样先关闭RTSP，与电视端故障无法区分，因此会被自动重连一次；两分钟内再次退出就不再重连。要断开投屏，请用快捷开关、语音助手或Android投屏控制。
- Wi-Fi P2P链路直接丢失（而不是RTSP先关闭）时，日志顺序可能与手机端断开相同，这种情况不会重连。本轮无法模拟，未验证。
- 连接时Moto“超级互联显示”仍会在手机上弹出“已连接至…”横幅（`com.motorola.mobiledesktop`）。第1步确认它参与连接，不能停用。
- 快捷开关不提供选择电视的界面：它连上次的电视。连其他电视用`moto-cast connect "<名称>"`，或对语音助手说出电视名。

## 电视R2入口“失败”：厂商WFD配置超出本机编码器（2026-09-28）

G100 S（XT2537-4，SM6435 `_parrot_v3`），接收端TCL 85Q6H。电视这次以`TCL 85Q6H-9E92[R2]`出现，投屏直接失败，界面没有错误信息。

### 现象与定位（实机日志）

- P2P建组、RTSP能力协商都成功，会话进入PLAY后约0.4秒拆除：`WFDV4L2ENC: Failed to set level` → `Failed to set profile and level` → `VIDEO_RUNTIME_ERROR`。两次失败的协商结果与成功时相同（`Dumping Negotiated Capability bitmaps`为`2 128 0 0`，1920×1080）；该位图不含level，不能据此判断level。
- `/vendor/etc/wfdconfig.xml`是平台共用文件，H.264六个条目均为Level 7（文件注释：5.2）、4096×2160@60，H.265为Level 4（5.1）。
- 编码器实际能力：`media_codecs_parrot_v3.xml`中`c2.qti.avc.encoder`最大2560×1440、491520宏块/秒；`MediaCodecList`报告AVC最高Level 5、HEVC最高High Tier 5，1080p60可用、1440p仅30fps、4K不可用；直接对`/dev/video32`、`/dev/video33`做`VIDIOC_QUERYCTRL`，`V4L2_CID_MPEG_VIDEO_H264_LEVEL`最大为14（5.0）。
- 结论：WFD栈把协商得到的level直接交给V4L2编码器，而厂商配置声明的上限高于本机编码器，R2接收端接受了高level后编码器拒绝。09-24经R1入口成功，推断R1路径（日志有`Invalid R1 level 64`）没有用到高level，未单独验证。

### 对照验证（同一台电视、同一R2入口）

| 配置 | 时间 | 结果 |
|---|---|---|
| 原厂（5.2） | 17:52、18:24 | 编码器`Failed to set level`，会话拆除 |
| 原厂 | 18:21 | 协商后电视解散P2P组（`reason=3`、-81dBm），未到编码器，不计 |
| 手工降级（H.264 4.2、H.265 4.1、1920×1080） | 17:57、17:59、18:03、18:07 | 编码器正常出帧，用户确认画面正常 |
| 自动生成（与手工降级逐字节相同） | 18:30 | 进入PLAYING，编码器无报错 |

### 修复：按本机编码器生成WFD配置

- `rungic-cast wfd-config <厂商文件> <输出>`（`WfdConfig.java`）：对每个`<VideoCodecN>`，取该类型硬件编码器（非别名、最大帧面积）经`MediaCodecList`报告的能力，把分辨率降到编码器在该条目帧率下支持的最大标准尺寸（4096×2160、3840×2160、2560×1440、1920×1080、1280×720），level降到覆盖该尺寸的最低级别（H.264表A-1、H.265表A.8），且不超过编码器报告的最高level；只降不升。使用Android公开接口，不维护芯片表。
- `rungic-cast-watch.sh`在`sys.boot_completed`后生成`/data/adb/rungic-wfd/wfdconfig.xml`，有改动时标为`vendor_configs_file`，在init挂载命名空间bind到`/vendor/etc/wfdconfig.xml`；WFD栈每次会话都重新读取，无需重启服务。结果写入`/data/adb/rungic-wfd/wfd-config.log`。撤销：`nsenter -t 1 -m umount /vendor/etc/wfdconfig.xml`。
- 本机输出：H.264 5.2→4.2、H.265 5.1→4.1，均为1920×1080@60。

### 验收边界与遗留

- 已验证：手动运行开机脚本（含已有挂载时先卸载再生成）；自动生成配置下连接电视进入PLAYING。
- 未验证：真实重启后的开机时序（ADB经无线调试，本轮未重启）。
- 整包：原先首启种子只含`rungic-lxc`、`rungic-plasma`，投屏组件只由`tools/rungic_cutover.py`安装，清数据刷入的机器投屏会失败。现`build_host_seed.py`（新参数`--cast-jar`）把`rungic-cast`、jar、watch、SELinux规则和两个service.d脚本放入种子的`rungic-wfd/`；`rungic-firstboot.sh`在`/data/adb/rungic-wfd/rungic-cast`不存在时安装（保留已有`last-sink`等状态），把脚本放入`/data/adb/service.d`并在本次开机启动（`setsid`并关闭安装锁描述符）。投屏按75篇属可选能力：安装失败只记日志，不阻止桌面安装；SELinux规则加载失败（其他厂商策略可能没有这些Qualcomm域）不影响其余组件。已通过`tools/ci/test_firstboot_cast.py`沙箱测试（清数据、半安装保留状态、种子不完整、已安装不覆盖）及手机mksh语法检查；尚未构建新整包，也未做清数据刷入验收。G100（SM7435，同为parrot平台）的WFD组件、SELinux域与编码器能力未实机核对。
- 悬浮窗权限：`CastDesktop`用`TYPE_APPLICATION_OVERLAY`把桌面放上电视，需要`SYSTEM_ALERT_WINDOW`应用操作；开发机上是手工`appops set`，整包的`default-permissions`管不到它，新刷的机器投屏时电视不会出现Linux桌面（缺权限时原先静默跳过）。现首启脚本在`finish`阶段先以root、失败再以`magisk su 2000`（shell身份）执行`appops set`，结果写入首启日志且不阻止安装（G100首启曾拒绝root上下文的appops，见79篇）；APK 2.10起，电视显示出现而未获授权时，应用先用自身root授予，仍不行再打开系统授权页并在一分钟内等待用户允许。实测（G100 S，APK 2.10，先把该权限置回default再连接TV）：20:08:19连接后应用以root授予并在同一秒把桌面放上电视。首启脚本这一步只做了语法检查，未做清数据刷入验收。
- 约45秒断开：两次会话在PLAYING约45秒后因P2P链路丢失结束（电视为组主，5240MHz，手机侧`disconnect rssi=-87`，`locally_generated=1`）；用户调整手机位置后会话稳定。18:30的会话在PLAYING后约48秒同样出现`disconnect rssi=-87`，但约3秒后重新关联，会话未中断。断开反复出现在约45–48秒，不像单纯的信号偶然波动；当时家庭Wi-Fi在5GHz另一信道，多信道并发、电视侧节能等原因均未排除。
- 编码器失败后`rungic-cast-watch`按“电视端断开”反复重连且错误为空；快捷开关只显示“没有连上电视”，真实原因只在`console.warn`。两者待改。

### 扫描发现不了电视（2026-09-28）

- 现象：连续数分钟（18:54–19:02）扫描不到电视，日志反复出现`P2P: Reject scan trigger since one is already pending`。
- 该行不是原因：打开`cmd wifi set-verbose-logging enabled`后，wpa_supplicant的P2P查找一直正常循环（社交信道2412/2437/2462与全5GHz扫描交替，约每秒一轮，其间监听），框架每10秒重复下发的`discoverPeers`在上一轮未结束时被拒。
- 原因：电视进入屏保后，Miracast等待界面停止P2P监听，期间既不回应探测也不发组信标；用户19:02让电视退出屏保，手机立即发现电视，之后每2–10秒都能再次发现。电视监听信道为6（2437MHz），`group_capab=0x0`（等待时不是组主）。
- 屏保期间电视仍在家庭局域网（192.168.5.21，Android 13），mDNS广播`_airplay`、`_raop`、`_leboremote`（乐播SDK），SSDP有DLNA `MediaRenderer`；无`_googlecast`与MICE的`_display._tcp`。
- Ready For对照（同日）：停用`com.motorola.mobiledesktop`与`.core`后18:44连接成功（进入PLAYING，Linux桌面接管电视，仅多出`Failed to connect to hce service`）；之后两次失败分别为电视30秒未回应邀请和60秒未发现电视，推断与电视停止监听有关。19:37再次停用后连接成功，用户确认电视画面正常且稳定；19:38–19:40断开15秒后重连4轮，4/4成功，每轮6–7秒进入PLAYING，无编码器或运行时错误，其间Ready For进程数为0。
- Moto框架的钩子（`services.jar`反汇编）：连接后`WifiDisplayController`把接收端的`hce ip/port`经`com.motorola.mobiledesktop.wfd.hce`交给Ready For，高通`WfdSession`也尝试绑定该服务；Ready For不在时两者都只记日志，会话照常。
- 用户决定（2026-09-28）：Moto机型保留Ready For，不为它做专门处理；投屏只要求经Android无线显示框架＋高通WFD组件正常连接，Ready For启用或停用都可以。高通组件可以使用，不要求自研发送端（84篇原型暂缓）。

## 选设备与投屏控制（2026-09-29）

用户要求：能投屏的设备不止一台时要先选设备；投屏控制里要显示当前投到哪台设备，并能更换设备。之后又要求补全按下状态，并让投屏控制胶囊可拖动、贴左右边缘吸附、空闲后收起。设计稿在Claude Design画布“投屏选设备与投屏控制”（私有），外观沿用Plasma Mobile/Breeze，不用设计系统。

### 后端：`rungic-cast`的补充（`shared/android/rungic-cast`）

- **按电视合并**：`status`和`scan`新增`receivers`。同一台电视的多个WFD入口（TCL广播`NAME[R1]`与`NAME[R2]`）去掉`[..]`后缀合并为一项。每项含`name`、`address`（优先上次用过的入口，其次会话空闲的入口）、`active`、`available`、`can_connect`、`remembered`、`last`、`seen_ms_ago`。
- **会话占用**：`can_connect`取`WifiDisplay.canConnect()`，即对方WFD信息里的会话可用位（`WifiP2pWfdInfo.isSessionAvailable`）；可见但不可连接的记为“忙碌中”。
- **最近发现**：投屏期间Android不扫描（`WifiDisplayController.updateScanState`要求`mDesiredDevice == null`）。因此每次`scan`把发现的入口写进`run/seen`，`receivers`带上十分钟内见过的设备，供投屏中更换设备使用。
- **切换**：`connect <另一台>`在已连接时先断开当前电视，等状态回到0，再按原流程扫描并连接。电视画面会中断几秒。`connect`可用接收端名（不带后缀）匹配，按地址找不到时改找同一台电视的另一个入口。
- **不扫描时的状态不可靠**：扫描停止后，`status`里的`available`可能仍是上次扫描的结果，所以选设备只采信`scan`返回的可见状态。
- **平台桥**：`scan`可带秒数（1–2位数字），APK 2.13。

### Plasma：快捷开关打开的“投屏到”面板

- **入口**：点“投屏”磁贴打开面板（`CastPicker.qml`），不再直接连接上次的电视。面板是plasmashell内的LayerShell覆盖层窗口，用法与音量OSD相同，打开时先关闭快捷设置。长按磁贴仍进入Android投屏设置。
- **未投屏时**：
  - 每轮`scan 6`，最多5轮，结果逐轮合并。
  - 分“上次使用”和“附近的设备”两组；忙碌中的设备不可点。
  - 底部提示电视须停在等待画面、屏保时搜不到，并有“重新搜索”和“Android 投屏设置”。
  - 没有找到电视时，列出三条检查项。
- **连接中**：选中行转圈，显示“正在连接…电视如弹出确认，请选择允许”，其他行不可点，有“取消连接”（即`disconnect`）。连上后面板关闭；超时则留在面板里提示。
- **投屏中**：
  - “正在使用”只给当前电视的图标徽章上强调色，整行不高亮，以保留按下态。
  - “最近发现的设备”可点，点了即切换。
  - 另有“断开投屏”，重连期间改为“停止重连”。
- **磁贴状态文字**：点按选择电视／正在连接…／电视名／电视断开，正在重连…。
- **界面细节**：面板放在名字不以`CAST-`开头的屏幕上（手机屏）。填充色按背景向文字色插值取得，浅色和深色配色都可见。

### APK：投屏控制（`CastControls.java`）

- **胶囊**：显示电视名（取Android无线显示的显示名，去掉`[..]`）和一行状态（投屏控制／手机用作触控板／正在切换…／电视断开，正在重连…）。点按打开面板。
- **面板**：
  - 顶部是“正在投屏”、电视名和分辨率。
  - 中间是“手机用作：手机／触控板／键盘”，改成先展开再选，原来的三段式胶囊不再使用。
  - 底部是“更换设备”和“断开”；切换中为“取消”，重连中为“停止重连”。
- **更换投屏设备**：底部表单，列出当前电视（标“当前”）和`receivers`里的上次使用与最近发现。点另一台即执行`connect`，失败时提示。
- **胶囊状态**：

  | 状态 | 外观 | 何时 |
  |---|---|---|
  | 展开胶囊 | 图标＋电视名＋状态行，离边缘12dp | 出现时、会话状态变化时、触摸后、关闭面板后 |
  | 贴边小胶囊 | 只有图标，贴边一侧为直角，另一侧为半圆，高48dp | 展开胶囊空闲4秒后 |
  | 拖动中 | 放大到1.06倍并抬高阴影，可拖到屏幕任意位置 | 手指移动超过触摸阈值 |
  | 吸附 | 松手后220ms动画移到较近的左边或右边；纵向限制在状态栏以下、底部手势区以上；边和高度比例记入偏好 | 松手时 |
  | 待定 | 图标底色改为橙棕，展开时带转圈 | 切换中、重连中 |
  | 隐藏 | 面板或设备表单打开时隐藏，没有会话时移除 | — |

- **边缘手势**：胶囊贴边时位于Android返回手势的起点区域，所以用`setSystemGestureExclusionRects`把它自己的范围排除在返回手势之外，排除区域在`dumpsys window`中可见。
- **按下状态**：胶囊、按钮、分段、关闭钮和设备行都用裁剪到自身形状的`RippleDrawable`；透明底的元素按下时也有反馈。
- **重连与切换**：
  - 电视消失且不是本机断开或切换时，每2.5秒查一次`status`，共3次；`reconnecting`为真时胶囊保持显示“正在重连”，每3秒复查一次，直到重连结束。
  - 从Plasma面板断开时，APK看不到用户操作，会查3次`status`。重连监视器已被这次请求取消，所以胶囊随之移除。

### 实测（2026-09-29，G100 S，TCL 85Q6H）

以下均为实机验证：
- **Plasma面板找设备**：`rungic-cast scan 6`合并出一项`TCL 85Q6H-9E92`，本次只有R2入口可见；面板第一轮扫描即显示上次使用的电视，第二次打开时另发现`DIRECT-9c4hJ2sB`（来源未确认，没有连接）。
- **连接与断开**：
  - 在面板里点电视，约3秒连上并自动关闭，胶囊显示电视名。
  - 在Plasma面板点“断开投屏”，断开后胶囊移除，之后没有自动重连。
- **投屏中两个入口**：
  - 快捷设置面板显示“正在使用”和“断开投屏”。
  - APK里的“更换投屏设备”列出当前电视和最近发现的设备。
- **胶囊与面板**：
  - 点胶囊展开面板，可切到触控板模式。
  - 胶囊空闲4秒后收成贴边小胶囊。
  - 用`input motionevent`分步拖动和`input swipe`连续拖动，都能左右移动并吸附到较近一边；位置在APK重启后保留。
- **按下状态**：按住“更换设备”时可见高亮，滑出后取消，不触发操作。
- **一次异常**：第一次从右边缘连续拖动时（尚未加手势排除），拖动失败并打开了面板，Linux时钟小部件进入了编辑模式；加上排除区域后，两边各重试成功。

未验证：
- 切换到第二台电视：第二台不确认归属，没有连接。
- 重连期间胶囊的“正在重连”状态：本轮没有让电视掉线。
- 切换失败时的提示。

限制：
- Android层的胶囊始终画在Linux画面之上，Plasma面板打开时小胶囊仍会压在面板边缘。
- 胶囊收起后，原来展开区域的点击会落到桌面。

### 更换设备列表里没有其他电视（2026-10-01）

- **现象**（用户报告）：投屏中打开 APK 胶囊的“更换投屏设备”，搜不到电视选项。当时电视没在屏保。
- **实机状态**（G100 S，只读查询）：
  - 正在投 `UGREEN-52BCCF3C`，`active_state` 为 2。
  - `rungic-cast status` 的 `receivers` 里有 `TCL 85Q6H-9E92`：`available: true`、`remembered: true`，但 `seen_ms_ago: null`。
  - `run/seen` 里 TCL 的最后记录约在 40 小时前，早已超过十分钟的有效期。
- **原因**：
  - 表单打开时只读一次 `status`，不扫描。
  - 表单只保留三类：当前电视、上次使用的电视、十分钟内见过的电视（`DeviceSheet.show`）。
  - 投屏中 Android 不扫描，`run/seen` 不会更新。投屏一久，Android 已保存的电视就全被过滤掉了。
  - 这不是代码回退：该过滤逻辑自加入以来没有变过。
- **修复**：
  - 表单同时列出 Android 已保存的电视（`remembered`），副标题写“已保存”，与“上次使用”“最近发现”区分。
  - 表单的说明和空列表提示也改成“已保存和最近发现”。
  - 切换路径不变：root 工具的 `connect` 先断开当前电视，再边扫描边连接目标。已保存的电视如果不在线，会像其他连接失败一样提示。
- **root 组件过旧（已修）**：
  - 手机上的 `/data/adb/rungic-wfd/rungic-cast.jar` 还是 09-29 的版本（SHA-256 `1936b97d…`），`status` 里没有 `protocol_version`。
  - 09-30 发布的容器侧 `rungic-cast 0.510` 要求这个字段。实测容器里 `rungic-cast status` 返回 `backend-incompatible`，所以 Plasma 快捷设置的投屏面板列不出电视；APK 胶囊不受影响。
  - 用当前源码重建 jar（SHA-256 `2e528d0e4695…`），经 `deploy_cast.py` 备份并升级。证据和备份在 `.work/cast/g100s-root-update-20261001/`。
  - 升级后，容器里的 `rungic-cast status` 带 `protocol_version: 1`，返回码为 0。
- **安装**：APK 2.28 开发版（只改了 Java 和字符串，原生库取自手机上已装的 2.28），SHA-256 `70114e37…`，用 `adb install -r` 安装。装完后 KWin 和 plasmashell 均为 active。
- **待验收**：投屏中打开“更换投屏设备”，确认 TCL 以“已保存”出现，并实际切换一次。

## 断开电视后通知在手机上重复弹出（2026-09-29）

- 现象（用户报告，实机复现）：连过电视再断开后，每条通知在手机上出现两个弹窗。一个是手机壳层的居中卡片，另一个是桌面样式的弹窗，在左上角，带倒计时条和✕。复现方法：电视已断开时在容器中执行`notify-send -t 20000 …`。
- 两套弹窗来源：
  - 手机：Plasma Mobile的`NotificationPopupProvider`（壳层单例，`PopupProviderLoader.load()`）。
  - 电视：`external-desktop.js`给外屏建的桌面面板带系统托盘，托盘里有桌面通知小程序`org.kde.plasma.notifications`，它的弹窗由plasma-workspace的`applets/notifications/global/Globals.qml`管理。
- 原因：
  - 屏幕移除后面板容器仍保留并加载。`evaluateScript`列出面板25（`org.kde.panel`）为`screen=-1 last=1`，其中的`systemtray`（29）仍在。
  - `Globals.qml`（v6.6.6）在`ratePlasmoids()`里只按位置、是否展开、是否在屏幕0打分，不排除没有屏幕的小程序。
  - 所以断开电视后，桌面通知小程序照常为每条通知建弹窗，这些弹窗落到仅剩的手机屏上，与手机壳层的弹窗叠在一起。
- 电视连着时，手机和电视各弹一次，属于各屏各自显示，不是本问题。
- 考虑过的修复：给plasma-workspace打补丁，让`Globals.qml`跳过没有屏幕的小程序。这需要把plasma-workspace纳入补丁队列并重新构建。用户选了更简单的做法：只停用电视面板里的通知，手机自己的通知照常。
- 修复（plasma-mobile `+rungic5`，`external-screen-desktop-shell.patch`）：`external-desktop.js`对每个桌面面板（`org.kde.panel`）的系统托盘，从`General/extraItems`里去掉`org.kde.plasma.notifications`，并保证它在`knownItems`中，免得托盘把它加回来。新建和已有的面板都处理。电视上从此不弹桌面样式的通知。
- 触发条件（实测）：通知小程序的界面只有在电视面板真正显示过之后才会加载。plasmashell启动时如果电视不在，即使配置里有它也不弹窗（测试5只有一个弹窗）。只有本次会话里接过电视、再断开，才会重复。
- 生效时机：
  - 小程序一旦加载，它的弹窗组件在plasmashell里会一直工作，运行中移除也照样弹窗。
  - 接上电视时，面板和小程序先加载，脚本约1秒后才移除它。所以升级后第一次接电视的那次会话里，断开后仍会重复一次；从plasmashell下次启动起不再出现。
- 实测（G100 S，plasma-mobile `+rungic5`，TCL 85Q6H，均为实机验证）：
  1. 把通知小程序加回电视面板托盘（模拟升级前的配置），重启plasmashell。电视不在时发通知，只有手机的一个弹窗。
  2. 接上电视：`screenCount`为2，脚本已从配置中移除该小程序。断开后发通知仍是两个弹窗，左上角是桌面样式的那个，因为本次会话里小程序已加载过。
  3. 重启plasmashell，再接上、断开电视后发通知，只剩手机的弹窗。

## 初版方案：Android Presentation承载外屏输出（无root假设）

```
电视 ◀─Miracast(Android WFD编码)─ Android WFD显示(Presentation类)
                                        ▲ Presentation窗口(SurfaceView，零拷贝子层)
宿主APK ── 第二个xdg_toplevel ◀── KWin输出WL-1(createVirtualOutput)
手机屏幕：宿主Activity切换为触控板+键盘界面 → Trackpad模式 → wl_pointer/wl_keyboard → KWin外屏输出
```

1. 连接：用户在Plasma里点“无线投屏”，宿主打开系统无线显示设置页选电视（无需root）；进阶可由root辅助进程在Plasma内直接列出和连接接收端。
2. 外屏出现：AOSP把WFD显示注册为演示类显示（推断：`FLAG_PRESENTATION`，需实测标志）。宿主`DisplayManager`监听到后，在该显示上创建`Presentation`，内含SurfaceView；有Presentation时外屏显示它而不是镜像手机。
3. 输出：宿主经平台桥通知容器，KWin（本地补丁，D-Bus入口）调用`createVirtualOutput`按外屏尺寸建输出WL-1；宿主把第二个toplevel绑定到Presentation的窗口，配置尺寸、独立的零拷贝图层与帧节拍。Plasma Mobile随即进入Docked模式。
4. 手机端：主Activity切换为触控板界面（单指移动、轻点点击、双指滚动、三指等手势沿用Trackpad模式），键盘用Android输入法（按键与文本提交已有通道，中文可走Android输入法提交或Linux侧Rime）。指针事件发往外屏输出的toplevel。
5. 断开：Presentation消失→`removeVirtualOutput`→退出Docked模式→手机恢复桌面。
6. 声音：Android WFD随会话传输设备音频，容器声音经现有PulseAudio→AudioTrack桥进入Android，推断会一同送到电视，需实测。

### 需要做的工作

| 部分 | 内容 | 规模 |
|---|---|---|
| 宿主Java | 显示监听、Presentation、触控板/键盘界面、打开投屏设置 | 中 |
| 宿主Rust | 多窗口：toplevel与Android窗口的绑定、每个输出独立的表面尺寸、零拷贝Presenter、GLES回退与帧节拍；输入按目标输出路由（目前全部按单一表面设计） | 大 |
| KWin补丁 | D-Bus增删宿主输出、输出名/尺寸/缩放；确认嵌套后端对两个输出的指针进出与光标 | 中 |
| 容器桥 | 平台桥事件→KWin；Plasma快捷设置“无线投屏” | 小 |
| 可选root辅助 | `app_process`调用隐藏接口扫描/连接接收端 | 小到中，待验证 |

## 备选方案：借Moto Ready For

把现有Activity直接放到Ready For接管的外屏上全屏运行，Ready For自带手机触控板与键盘，宿主只需处理显示尺寸与密度变化（与旋转同类）和鼠标事件。改动最小，但依赖Moto私有功能、外屏只有Linux一个窗口且受Ready For窗口管理约束，也无法在Linux侧选择和连接接收端。适合作快速原型，不作为通用方案。

## 风险与待验证

- WFD显示的实际标志、分辨率（通常1080p60或720p）与Presentation是否可用，需接真实Miracast接收端验证；部分电视的Miracast兼容性差。
- 端到端延迟：Miracast编码、Wi-Fi与电视解码通常在百毫秒量级（推断），光标会有可感知的滞后；电视游戏模式可改善。
- Wi-Fi P2P与现有Wi-Fi/VPN（SwiftWire）并发时可能换信道或降速。
- Ready For与系统投屏入口可能争用同一WFD会话；需确认普通“无线显示”连接不会被Ready For接管。
- 功耗与发热：KWin多渲染一个1080p输出，外加Android硬件编码。
- Plasma Mobile的Docked模式偏基础（窗口边框、非最大化），外屏上的面板/任务栏体验需实机评估，可能需要桌面化调整。
- 手机屏幕若继续显示Plasma移动界面则需要双输出同时渲染；推荐投屏时手机端只显示触控板，内置输出保持但不参与交互。

## 原型验收顺序

1. 用系统无线显示页连电视；宿主在外屏显示Presentation测试图，记录显示标志、尺寸与延迟。
2. KWin建第二输出并在外屏显示Plasma，确认Docked模式自动开启。
3. 手机触控板移动/点击/滚动与键盘（含中文）在外屏应用中可用。
4. 断开、重连、旋转手机、宿主重启后的恢复；声音路由；功耗与温度。

## 2026-09-29：精确视频分辨率与帧率

投屏胶囊展开面板通过 `rungic-cast modes` 和 `resolution ADDRESS/WIDTHxHEIGHT@FPS` 调整视频模式（用户要求此入口只放在胶囊里；Plasma 投屏设备面板继续负责选择、连接和断开），按接收端真实 RTSP 回复、手机编码器和安装接口取交集；支持自动、按接收端记忆、执行读回与失败恢复。连接默认等待120秒，界面可取消。当前后端同尺寸帧率在线切换，分辨率变化重连以同步Android外屏与Linux桌面大小。具体协议来源、Qualcomm接口探测、G100/UGREEN四种常用模式实测及高分辨率限制见 [视频模式记录](research/miracast-video-modes.md)。此实现不替代其他机型的实机验收。

## 2026-10-01：导播台，以及电视显示什么

**背景**：有了多个助理屏以后，原来的投屏按钮会直接连上次那台电视，也没法选择电视上显示什么。用户先后提出两版要求：
- **第一版**：没连电视时先问投到哪台；已连电视时点一下切到下一块屏；在投屏控制里加一个“导播台”。
- **修订**：导播台不只用于投屏，而是“多个助理屏”这件事本身。屏幕上有多个助理屏时，它们自动合成一个导播台控件，标出当前焦点；投屏时，有导播台就默认投导播台。要解决的是：怎么切换焦点、怎么把焦点放大；你的电脑模式和导播台之间的切换放在投屏控件里。第一版里“点一下切到下一块屏”的循环由此取消。

**模型**：
- **屏幕分两类**：你的屏幕（手机本身，电脑模式即桌面模式）和助理屏（Agent 工作区 1..4）。导播台只包含助理屏。
- **导播台状态**：只有一份，保存在 APK（`Director.java`），手机上的导播台窗口和电视共用：
  - 成员：正在运行的工作区，即宿主 `liveSources()` 里有窗口的那些；
  - 焦点；
  - 级别：0 标准（缩略图约占 1/5 高）、1 放大（缩略图压成细条，约 1/10 高）、2 独占（只显示焦点）。
- **接口**：平台桥 `{"op":"director"}` 用 `focus`、`step`、`level` 修改状态，回复里带 `version`，每次变化都会递增；命令行用 `rungic-cast director [N|next|prev|level 0|1|2]`。
- **电视显示什么**：在投屏控制里二选一，导播台（有助理屏时才能选）或电脑模式（你的桌面）。
  - 新连上电视时，有助理屏就投导播台；
  - 投导播台时，触控板操作的是焦点屏；
  - 最后一个助理屏关掉后，电视自动回到电脑模式；
  - 接口是 `{"op":"tv","content":"director"|"desktop"}`，命令行 `rungic-cast tv director|desktop`。

**投屏按钮**：浮窗、导播台窗口、全屏和快捷开关的投屏按钮都按同一规则：

| 状态 | 点一下 |
|---|---|
| 没连电视 | 浮窗、导播台窗口和全屏的按钮打开 APK 的选电视列表：已保存的电视先列出来，同时按 6 秒一轮搜索附近的电视，最多 3 轮。选中后投导播台，点按钮的那块屏成为焦点；没有助理屏时投电脑模式。快捷开关仍打开 Plasma 自己的选择器。 |
| 已连电视 | 打开 APK 的投屏控制面板：选择投导播台还是电脑模式，选焦点和级别，换电视，断开。 |

接口是 `{"op":"tv","button":true,"source":N}`。另外修复了一个 bug：浮窗的电视按钮原来用全局的 `assistantWorkspace`，在 3 号浮窗上点可能投出 1 号。

**2026-10-02 修正：每个窗口的投屏按钮投它自己的画面**（用户发现）：
- **问题**：在桌面模式浮窗上点投屏，电视投出的是导播台或助手屏，投屏面板上亮的也是“导播台”。
- **原因**：
  - 按钮只发 `source:0`，安卓把 0 当作“不改内容”。
  - 新连上的电视在 `Director.bound()` 里被无条件设成导播台。
  - 无头工作区默认存在，`members` 几乎总不为空，所以总是投导播台。
- **修法**：
  - **窗口按钮带上内容**：浮窗和导播台窗口的按钮同时发 `content`：桌面模式发 `desktop`，助手屏发 `director`（并把该屏设为焦点）。已连电视时，先切到这个内容，再打开控制面板。
  - **快捷开关不变**：快捷开关的 `tv button 0` 不带 `content`，仍然只打开面板。
  - **电视连上时不覆盖选择**：`Director.bound()` 不再覆盖已选的内容，改为在电视断开时恢复默认导播台。所以新电视在没有特别指定时，仍投导播台。
  - **APK 全屏页**：投屏按钮按全屏显示的内容设定（导播台或电脑模式）。
  - **命令行**：`rungic-desktop-mode tv` 改为把电脑模式投到电视上，没有电视时先连接。

**手机上的导播台窗口**（`rungic-agent-screen-window --director`）：
- **何时出现**：第二个工作区的屏要显示时，`rungic-agent-screen` 关掉各工作区自己的窗口，改起一个导播台窗口；所有成员都由它显示，成员少到一个时它就像普通浮窗。
- **结构**：`agentscreen.cpp` 的 `Director` 跟随 APK 的状态，每个成员一个 `AgentScreen`，各自有一路画面流和动态说明。
- **布局**：上方是焦点画面，下方是其他成员的缩略图（标准、放大两级），独占时下方只有圆点。点缩略图或圆点就把它设为焦点；工具栏多一个放大按钮，在三级之间循环；✕ 关闭的是焦点那块屏。
- **画面流**：缩略图原计划每秒截一张图，实际采用实时画面流。各窗口各自一路流时，每个窗口约 113 MB、每路流约 59 MB；合成一个窗口后只剩窗口本身加每路流，3 块屏约 290 MB，原来是约 516 MB。
- **配合**：keeper 看到导播台窗口在运行，就把所有成员都算作正在显示，不会冻结，但只接上焦点那块的声音。电视上同理，`tvShown` 里的都算显示，只有 `tvHeard` 那块有声音。

**电视上的导播台**：android-host 补丁 `21-tv-director.patch`，以及覆盖文件 `cast.rs`、`surface_control.rs`。
- **图层**：投屏窗口里每个入格的画面源有一个自己的子 SurfaceControl，用 `setGeometry` 放到自己的格子里，仍是零拷贝；
- **帧率**：焦点屏按投屏窗口的节奏出帧，缩略图每 66 ms 一帧（约 15 fps）；
- **标签**：APK 在电视上另开一个透明窗口（`PlasmaCastLabels`），画每个格子的名字和焦点边框，切换时短暂显示所投屏幕的名字；
- **布局**：电视按 16:9 计算。标准级别下缩略图不超过 1/5 高，放大级别下为 1/10 高；焦点在其上方尽量放大，居中；独占级别或只有一个成员时为单屏。

**再次修订（用户 2026-10-01 晚）**：
- **浮窗**：只显示焦点，下面一排编号按钮用于切换，不再挤一排缩略图（浮窗本身已经很小）。
- **全屏**：导播台浮窗的全屏按钮进入“导播台全屏”，即 APK 的 `AgentFullscreen.show(true)`，用的布局和电视一样，所以手机全屏就是电视画面的预演。
  - 在焦点格子里触摸，照常操作那块屏，手势按焦点格子换算坐标；点其他格子把它设为焦点；工具栏的放大镜按钮在三级之间循环。
  - 宿主在 rotation 90 下也能放格子：格子坐标是转成横向后整个窗口的比例，转一刀后放到竖着的窗口里。
- **排列**：其他屏改为竖排在焦点的右侧，用户提议。布局按实际窗口宽高比计算：手机横过来是 20:9，焦点几乎占满高度，右侧一列正好用上多出来的宽度；独占时焦点按 16:9 居中，不拉伸。
- **动画**：切换焦点或级别时，各格子用约 0.28 秒从原位置移动、缩放到新位置；新出现的格子从自身中心长出来。浮窗里切换时，新画面淡入并略微放大。
- **修复**：宿主丢掉一个格子的图层时，原来只释放引用，旧画面会留在屏幕上。现在先隐藏并从父层摘下，再释放。

**实机**（G100 S，三块助理屏：1 号空着，2 号开计算器，3 号开时钟）：
- 导播台浮窗和编号按钮正常；
- 全屏的标准、放大、独占三级布局和名字标签正常，独占时不再残留旧图层；
- 触摸操作、点格子切焦点、动画观感还没有人工试过。

**第三次修订（用户反馈）**：
- **浮窗**：也改成右侧一列实时小窗，不用编号按钮；焦点约占浮窗宽度的 3/4。
- **切换动画**：原来的格子移动、缩放动画“太夸张，还有点卡”。卡的原因是动画每一帧都调用 `setDirector`，宿主每一帧都要把所有画面重新提交一遍。
  - 现在布局一次换到位，新焦点从约 45% 的暗度在 0.18 秒内亮起来（`Director.settle()`）；
  - 这层暗度只画在全屏和电视的叠加层上，不经过宿主；
  - 浮窗里只有焦点画面从 35% 不透明度淡入，同样 0.18 秒，不再放大。

**第四次修订（用户反馈）**：只是变亮“像刷新了一下、闪了一下”。用户更喜欢第一版“一收一放”的呼吸感，并希望有弹簧的感觉。
- **全屏和电视**：新焦点从 93% 大小，绕自身中心弹回原大。
  - 参数：阻尼弹簧，频率 2.4 Hz，阻尼比 0.5，过冲约 1.6%，约 0.5 秒停稳。
  - 实现：每帧只调用 `NativeBridge.placeTile` 移动焦点那一层。宿主补丁新增 `PlaceTile`，`Presenter::place` 只做一次 `setGeometry` 事务，不提交新画面；`set_tile_rect` 让期间到来的新帧也落在当前位置。
  - 第一版卡顿，是因为每帧都重新布局、重新提交所有画面，这次避开了。
- **浮窗**：焦点画面先设成 0.93 倍，再用 Qt 的 `SpringAnimation`（spring 6，damping 0.32）回到原大，同时在 0.14 秒内从 50% 不透明度淡入。

**第五次修订（用户反馈）**：
- **浮窗闪黑**：浮窗里的大画面原来只有一个视频项，切焦点要把它的 `nodeId` 换成另一路 PipeWire 流，接通前那几帧是黑底；全屏的每块屏一直各有自己的图层，所以顺滑。
  - 改法：浮窗里每块屏固定一个视频项，一直连着，切焦点只改它们的位置和大小，焦点那一块铺在大画面的位置；
  - 圆角改为每个视频项自己做遮罩；
  - 实机：切到 3 号后一秒内的截图已是时钟画面。
- **不要弹簧**：用户觉得弹簧会分散注意力。全屏、电视和浮窗统一改为普通的呼吸：0.95 倍到原大，0.26 秒，缓出，不过冲（全屏和电视用 `DecelerateInterpolator(1.5)`，浮窗用 `Easing.OutCubic`）。

**电视实机（2026-10-01 23:58，TCL 85Q6H）**：
- **连接**：用 `rungic-cast connect "TCL 85Q6H-9E92"` 连上，协商出 1920×1080、30 fps，这台电视只给出这一种模式。
- **默认内容**：新连上的电视直接投导播台。`tv` 状态为 `content=director`，`shown=[3,1,2]`，`heard=3`。
- **手机**：导播台浮窗自动收起，因为各成员 `AgentScreen` 都处在 `tv` 状态，画面流停了。
- **用户确认**：电视上布局和标签都正确，焦点在左边，其余在右侧一列，没有拉伸。
- **数据**：
  - 宿主 10 秒内送出 71 帧。画面静止，只有时钟每秒在变；宿主只在画面有提交时才出帧，所以这不代表帧率上限；
  - 容器可用内存约 2.1 GB，APK 的 PSS 约 139 MB。

**电视实机（续，2026-10-02 00:00–00:03）**：
- **声音**：
  - 方法：三个工作区各循环播放一段可分辨的声音（`ffmpeg aevalsrc` 生成，`paplay` 在各自工作区环境里播放）。1 号是 220 Hz 慢“咚”，2 号是 660 Hz 快“嘀嘀嘀”，3 号是 1320 Hz 衰减的“叮”；
  - 声道：三路分别进 `rungic_ws1`..`3`，只有焦点那一路经 loopback 接到 Android 输出；
  - 结果：用户在投屏控制里切焦点，确认声音跟着焦点切换。声音比画面晚一两秒，因为 keeper 是轮询状态的。
- **关闭一块屏**：`rungic-cua close-workspace 2` 后，成员从 [1,2,3] 变成 [1,3]，电视上的格子随之减少。
- **断开重连**：`rungic-cast disconnect` 后，状态为未连接；重新连上 TCL，自动回到导播台（shown [1,3]，heard 1）。手机上导播台浮窗保持收起，投屏胶囊在。

**声音切换的延迟（2026-10-02）**：
- **问题**：声音比画面晚一两秒，因为 keeper 每 3 秒才检查一次。
- **改法**：
  - `HostEvents` 新增主题 `screens`。导播台每次换版本、TV 或全屏绑定和释放呈现器、`agent-screen` 有修改时，都会 bump 这个主题；
  - 每个 keeper 用平台桥的 `watch` 一直挂着等这个主题，一变化就立刻 `check()`；
  - 原来 3 秒一次的轮询保留，作为兜底。
- **实机**：从发出 `rungic-cast director N` 到新焦点的 loopback 建好，6 次分别为 130–142 ms（原来最长约 3 s），每次都只剩一路 loopback。

**声音跟着画面（2026-10-02，用户要求）**：投屏时，用户在手机上玩的游戏声音却从电视出来，因为 Android 会把所有普通媒体声音都送到无线显示。
- **做法**：共享层服务 `rungic-audio-follow`（`shared/media/audio-follow.py`，用户单元 `rungic-plasma-audio-follow`，属于 rungic-plasma-bridges 包）。投屏期间，它把用户会话的每条播放流在 `android`（跟随 Android 路由，即电视）和 `android_phone`（始终在手机本机，docs/59）之间挪动：

  | 声音 | 去向 |
  |---|---|
  | 窗口在手机屏上的应用 | 手机 |
  | 窗口在 `CAST-n` 上的应用 | 电视 |
  | 工作区的声音 | 在电视的 `shown` 里就去电视，否则去手机 |
  | 没有窗口的声音 | 电视在投电脑模式就去电视，否则去手机 |

  应用自己选定的去处不动，比如语音助手的 `android_phone`、通话用的 Linux 扬声器、工作区自己的 sink。断开电视后，挪过的声音都回到 `android`。
- **事件来源**：
  - 窗口：常驻的 KWin 脚本在窗口新增、关闭或换输出时，通过 `callDBus` 报告 {pid: [输出]}。找窗口时会沿父进程往上查，浏览器的声音来自子进程；
  - 投屏：平台桥的 `screens` 事件；
  - 声音流：`pactl subscribe`。
  - 全程不轮询。
- **实机**（TCL 连接中，电视投导播台）：
  - 无窗口的 `paplay` 去了 `android_phone`；
  - 1 号工作区在电视上，它的 loopback 留在 `android`；
  - 用户桌面上打开的 Sky Hop（Godot，pid 25680）的声音流被挪到了 `android_phone`；
  - 是否真的从手机出声，以及延迟大小，还要用户实听确认。手机本机这条路比默认路径多约 0.15–0.2 s（docs/59 的 FIFO 和 AudioTrack 缓冲）。

**手机本机输出卡顿与延迟（2026-10-02）**：用户反馈游戏声音从手机出来了，但“一卡一卡的，还有点延迟”。
- **测量**：
  - Android 端这一路（APK 的 AudioTrack）没有欠载，FrmRdy 一直是满的 7200 帧（150 ms），AudioFlinger 报告延迟 274–313 ms；
  - PulseAudio 这边，sink 延迟约 66 ms，游戏流的缓冲约 45 ms。
- **原因**：module-pipe-sink 原来“由读取速度计时”。读取的一方是 Python 线程，后面接一个阻塞写入的 150 ms AudioTrack，sink 因此是一阵一阵地写，游戏流在 PulseAudio 一侧接不上；各级缓冲加起来约半秒。
- **改法**：
  - pipe-sink 加上 `use_system_clock_for_timing=yes`，由 PulseAudio 按自己的时钟匀速写；
  - FIFO 和套接字缓冲都降到 8 KiB，每次读 10 ms；
  - AudioTrack 缓冲改为 `max(min, 40 ms)`，用 `WRITE_NON_BLOCKING` 写，写不下的直接丢掉，让延迟有上限。
- **一次失败**：试过 `PERFORMANCE_MODE_LOW_LATENCY`，结果这一路不再理会 `setPreferredDevice`，投屏时被送到了 PROXY，声音跑到电视上，用户发现后已撤回。
- **结果**：
  - 这一路回到 `AUDIO_DEVICE_OUT_SPEAKER`，FrmRdy 约 940 帧（约 20 ms），没有欠载；
  - AudioFlinger 报告延迟约 185 ms，PulseAudio sink 约 3 ms；
  - 用户确认声音从手机出来、不卡了，“还有点延迟，可以暂时不管”。剩下的延迟主要在 Android 扬声器输出线程。以后可以试 `USAGE_GAME`，但要先核对它不会被送到 PROXY。

**事故（2026-10-01 23:07）**：用户在导播台全屏里点了电视按钮，APK 崩溃，回来后黑屏。
- **原因**：`castButton` 要求主窗口有焦点（防止经平台桥在后台弹出选择列表），但全屏时焦点在全屏层的面板窗口上，于是抛出 `IllegalStateException`，在按钮回调里没人接住，整个应用退出。
- **修复**：只有经平台桥来的请求才检查焦点；应用内的按钮回调出错时只弹 Toast。
- **连带问题，未修**：APK 被拉起后会话重启了，KWin 和 plasmashell 都是 active，但 plasmashell 一个窗口都没有，KWin 截图是空白，屏幕全黑。手动 `systemctl --user restart plasma-plasmashell` 后恢复。这和 docs/96 的 B 不同：那次 plasmashell 停在 failed 或 inactive，这次是 active 却没有窗口，自动恢复还没覆盖。
- 同一时刻还有几个核心转储：`rungic-workspace-stream` 是 SIGABRT，`rungic-workspace-desktop` 是 SIGSEGV（在 `QWaylandShmBuffer` 中）。都是宿主随 APK 退出、Wayland 连接断开时的连带结果。

**状态**：手机全屏部分已实现。电视部分复用同一套布局，实机验收要电视在场。


### 2026-10-07 启动条件修复（task #99）

第二轮 G100 的三次开机都记录了 audio-follow 在 `org.kde.KWin` 名称注册前调用 `loadScript`，以 `ServiceUnknown` 退出；五秒后的自动重启恢复，不算首次启动通过。实际发行版单元的 `BusName=org.kde.KWinWrapper` 使 systemd 采用 `Type=dbus`，等待的是 Wrapper 名称；`After=plasma-kwin_wayland.service` 因此不能代表调用目标 `org.kde.KWin` 就绪。

`rungic-plasma-audio-follow.service` 现在先通过 `rungic-wait-dbus org.kde.KWin` 检查名称实际有属主，再启动原脚本。这个入口由语音浮层已有的名称等待抽出，浮层也使用同一实现。复用发行版 `gdbus wait --session --timeout=120` 的名称变化订阅与当前属主查询，等待上限 120 秒；依赖一直不就绪或会话总线断开仍报告启动错误，保留原本的失败恢复策略。名称出现之后的实际脚本调用仍可能失败，不把名称检查扩大成运行成功。

依据是 [D-Bus 的 NameHasOwner 契约](https://dbus.freedesktop.org/doc/dbus-specification.html#bus-messages-name-has-owner)：它检查当前名称所有权，不启动服务。工作空间 portal 使用的 Wayland 往返则是显示就绪条件，不能替代这里的总线名称条件。[GLib 的名称监听](https://docs.gtk.org/gio/func.bus_watch_name_on_connection.html)先订阅变化，再检查当前属主。私人总线的延迟注册、缺失超时和语音浮层回归由离线测试检查；实际 KWin 脚本的系统测试在无手机的原生 ARM64 环境运行。第二轮原始失败保留；此修复的 G100 开机验证留给第三轮新候选。

原生 ARM64 的三次隔离 systemd 用户会话里，原 audio-follow 每次首次启动均保持 active 且 `NRestarts=0`。名称缺失的反例每次都返回启动失败，`ExecStartPre` 退出码为 1，主程序未运行；在原五秒自动重试前读回并停止反例，避免把后续恢复误报成首次通过。另一次真实 KWin 工作空间检查确认脚本收到窗口回报，KDE／GTK 后端各三次激活通过。

运行中 KWin 崩溃重启后，已有脚本丢失不会自动重新加载，这是独立的 task #101；本次只处理首次启动依赖就绪。
