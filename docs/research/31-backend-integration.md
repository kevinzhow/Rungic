# Phosh 与 Android 后端的连接：架构、研究过程和维护方法

2026-09-30 构建与部署身份：新的独立包 manifest schema 2 绑定组件输入指纹、依赖关系及 APK/rootfs/host seed/稀疏写入器摘要；pack 核对当前期望配方，verify 核对随包记录与文件。G100 已用该路径部署 OS `20260930.17` 并通过保留账户及重启检查，设备保留 `/usr/share/rungic/build/payload-build-manifest.json` 供追溯。旧上游二进制基线仍作为显式依赖，不能被追认为源码构建证明；实现、适用入口和验收边界见 [94 篇](../94-build-fingerprints.md)。

2026-09-30 X70 投屏搜索修复：旧镜像 JAR 返回 `displays`，新版桌面需要 `receivers`，实际发现被显示为空。配套升级 root helper 与桌面包后，手机实际列表可见 TCL/UGREEN；增加协议检查和构建来源校验，并修复空态居中。此轮仅验证发现与 UI，没有重建 `.7` 镜像或重验电视画面/声音，证据与边界见 [86 篇](../86-x70-miracast-assessment.md)。

2026-09-30 X70 新镜像复验：OS `20260930.15`、独立载荷 `.7`、APK 2.27。修复 APK umask 0077 导致 cgroup 0700，以及 Android 音频持久化 PID 被其他应用复用的两个独立冷启动故障；全新账户 UI、连续三轮整机重启首次打开、9 项 smoke 全部通过。此前 `.5` 第三轮失败仍保留证据；用户选择最终空白账户，由其自行配置。固定产物、日志与验收边界见 [93 篇](../93-x70-independent-image-revalidation.md)。

2026-09-30 X70 重刷补验：原厂 Android 分区、GKI 和 Magisk 引导实际刷入并清数据，再从无 Rungic/Termux 的底座独立安装；账户、桌面/触摸、SSH 和首轮重启已走通；末轮首次打开失败、检查后重试恢复，稳定性仍待定位。首次会话的五个迁移程序崩溃定位到桌面前误用 Wayland，`desktop/session` 对迁移命令局部使用 offscreen；另修正 CI2 的旧 APT 版本锁遗留。原载荷与后续修补包分别记录，Magisk 仍需安装完整管理器和修复环境，详见 [92 篇](../92-x70-android-base-end-to-end.md)。

2026-09-30 X70 独立安装补充：`standalone.py` 绑定可信载荷和实际底座；root 控制器读取 `/data/adb/rungic-install/active.env`，APK 读取私有来源声明与 release 状态。安装/挂载完成后才准备账户。旧 product 引导经 Magisk 模块转发，已做重启核验；账户、桌面、SSH 与测试边界见 [91 篇](../91-x70-independent-install.md)。

2026-09-29 MacBook直连研究：连接层（本地热点/AWDL候选）与桌面串流分离，采集和编码优先复用KWin/PipeWire及共享MediaCodec桥。G100现有无线模块monitor的标准发送回调缺失，原版filin不能直接使用；只读能力与在用模块Build ID/重定位证据见[接口与选型记录](mac-offline-desktop-link.md)。本轮未实现或部署，外部网络独立的发现/信令、远程观看降速与硬编拥塞控制仍待验。

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](../70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

> 历史研究记录：Phosh 专属实现已于2026-09-23移除。本篇保留共享硬件接口与研究结论；当前代码见 `shared/`、`android/host/` 与 `packages/android-host/`、`desktop/`、`system/`，现行集成见 [40篇](../40-plasma-mobile-integration.md)。旧Phosh路径和已删除的原始日志不再作为可执行入口。

记录日期：2026-09-23。对应 moto g100s / XT2537-4，Android 16，Phosh/Phoc/Stevia 0.57.0，Linux 桌面 APK 2.3，Alpine edge 与统一 Mesa/KGSL 基线。本文依据本地实际源码、配置与上一轮实机证据整理；本次编写文档没有新增硬件验收。

**这套系统的连接方式是：Linux 应用继续使用 Wayland、PulseAudio、D-Bus/portal 等桌面接口；Android 继续管理屏幕和手机硬件；在两侧之间接入必要的显示、声音和设备接口。** Phosh 提供桌面界面，Phoc 管理 Linux 窗口，APK 提供 Android 上的显示宿主和部分硬件能力。LXC 提供 Linux 用户空间，复用手机内核。

**后续更新：原生网络桥已部署，见[32：Android 网络接入](32-network-integration.md)。下文网络规划描述的是补桥前的问题；第32篇记录最终对象、接口、GNOME补丁和实机验收。**

**采集更新：APK2.5接入AudioRecord/Camera2，Linux提供PulseAudio麦克风与PipeWire摄像头，见[33：麦克风、拍照、录像](33-capture-integration.md)。其中同时记录输出故障自动恢复、共享目录FUSE ABI修复和采集生命周期。**

**编解码更新：APK2.6的普通应用域运行MediaCodec；GStreamer插件和Firefox私有FFmpeg通过`/mnt/android-wayland/codec.sock`、独立控制FD及32MiB共享内存传帧。Firefox在沙箱前预连受限broker，fork子进程各持有独立连接；没有关闭内容/RDD沙箱。应用目录中的四个私有FFmpeg运行库用于统一Firefox各进程的后端，系统FFmpeg和Mesa保持原样。协议、安装路径、维护/回退与实机范围见[35篇](35-hardware-codec-integration.md)。**

同轮桌面调整：底部home栏恢复15逻辑像素，保留Android沉浸手势和顶部safe area；`phosh/session`禁用无可用密码的独立Linux锁屏，Phosh各锁屏入口遵从此设置。完整源归档与增量补丁位置见35篇。

## 阅读入口

2026-09-28 当前 Plasma 首次账户入口补充：APK → Magisk `--mount-master` → `rungic-plasma account-setup` → 公共 `start_container()` → LXC 内账户助手。容器入口绑定的宿主音频目录必须在所有启动路径上提前准备；`android-audio prepare` 复用目录创建和权限逻辑，普通桌面启动再以 `start` 启动音频服务。G100 全新安装曾在真正校验账户之前因目录缺失失败，修复及验收范围见 [79 篇](../79-g100-ci-execution.md)。

| 要回答的问题 | 文档 |
|---|---|
| 当时有哪些功能缺失、怎样测出来 | [28：能力审计](28-capability-audit.md) |
| 调查了哪些上游和类似项目，为什么选这条路线 | [29：复用研究](29-reuse-research.md) |
| 现在能用哪些功能，实测到了什么程度 | [30：功能适配记录](30-feature-adaptation.md) |
| 各层怎样连接、如何继续开发和排查 | 本文 |
| 源码、补丁、二进制与原始验收证据在哪里 | 本轮材料索引（历史材料已移除） |

第25、26、27篇保留原生Wayland、GPU、版本升级的历史过程。当前Linux图形库位于统一的 `/usr`；26篇早期 `/opt/moto-mesa` 的运行配置已被27篇替代。第30篇之后的新变化应继续更新状态记录，不能把本文的版本和验收结果视为永远有效。

## 1. 先分清各层的职责

| 层 | 实际组件 | 负责什么 | 不应据此推断什么 |
|---|---|---|---|
| Linux应用与外壳 | GNOME Apps、Firefox、Phosh、Stevia | 应用窗口、桌面、面板、输入法 | 装了设置App并不等于有Android硬件接口 |
| Linux合成器 | 自定义Phoc 0.57 + 静态嵌入wlroots 0.20.2 | 内层Wayland服务、窗口合成、输入焦点、layer-shell、屏幕采集 | 嵌套输出WL-1不等于物理屏幕控制器 |
| Linux桌面服务 | PulseAudio、D-Bus、PipeWire/WirePlumber、portal、GVfs、Keyring | 标准声音、共享屏幕、文件和凭据接口 | 服务进程存在还需验证实际后端与数据流 |
| Android显示宿主 | `dev.moto.phosh` APK，Java + JNI/Rust/Smithay | 外层Wayland服务、SurfaceView、厂商EGL、AHardwareBuffer、触摸、vsync | APK本身不包含整个Linux系统 |
| Android设备接口 | APK的 `PlatformBridge`；独立Termux PulseAudio实例 | Android窗口亮度、设备信息、系统设置入口、剪贴板；AAudio声音输出 | NetworkManager（含Wi‑Fi操作与调制解调器设备）、ModemManager、BlueZ子集模拟服务已接入（docs/73第四阶段）；GeoClue兼容服务未完成 |
| Android采集接口 | APK `CaptureBridge/CaptureService`；Linux `moto-media-bridge`、`moto-camera-source` | 普通Android权限、AudioRecord PCM→PulseAudio source；Camera2 YUV→PipeWire Video/Source | 可见且有消费者才采集；不代表硬编码、后台录像或完整Camera HAL直通 |
| 容器和控制入口 | Magisk、`moto-phosh`、`moto-phosh-enter`、LXC | 启停、挂载可见性、Linux文件系统、设备白名单 | 这不是具有独立内核的虚拟机 |

Android继续拥有物理显示模式、温控、网络连接、蓝牙栈、系统电源策略等。Linux应用尽量复用共同的标准服务，避免每个App单独配置一个Android适配方法。

### 1.1 显示和设备控制是两条连接

```mermaid
flowchart TB
  subgraph Linux[Alpine LXC 用户空间]
    apps[GNOME App / Firefox / Phosh / Stevia]
    phoc[Phoc + wlroots 内层 Wayland]
    mesa[Mesa Freedreno / KGSL]
    bridge[亮度后端 / Android 设备页 / 剪贴板同步]
    pulse[Linux PulseAudio]
    apps -->|Wayland 窗口与输入协议| phoc
    phoc -->|GLES2 绘制| mesa
    apps -->|Linux 桌面接口| bridge
    apps -->|libpulse| pulse
  end
  subgraph Android[Android 宿主]
    native[APK JNI / Rust / Smithay 外层 Wayland]
    surface[厂商 EGL / SurfaceView]
    sf[SurfaceFlinger / 物理屏幕]
    platform[PlatformBridge / Android API]
    audio[Termux PulseAudio / AAudio]
    native --> surface --> sf
  end
  phoc <-->|外层 Wayland socket 与缓冲 FD| native
  mesa -->|绘制到 APK 分配的共享 AHardwareBuffer| native
  bridge <-->|私有 Unix socket / JSON| platform
  pulse -->|私有 Unix socket / PulseAudio 协议| audio
```

图中的JSON接口传设备状态和控制请求。桌面像素走Wayland/GPU缓冲，声音走PulseAudio。HTTP代理 `192.168.5.45:6152` 用于访问互联网，不参与这些本地IPC连接。

2.5另有 `/mnt/android-wayland/capture.sock`：麦克风PCM与相机YUV经私有socket进入Linux标准音视频服务；平台JSON的 `capture-info` 仅用于状态和规格。完整协议、按需启停和文件归属见33篇。

## 2. APK与桌面怎样接起来：两层Wayland

Phoc同时扮演两个角色：对于GNOME App，它是Wayland服务器；对于Android APK中的Smithay，它是一个Wayland客户端。APK显示的是Phoc合成出来的Linux桌面。

```text
GNOME App、Phosh、Stevia
  → 内层Wayland socket（由Phoc创建，位于 /run/user/1000）
  → Phoc / wlroots 的 Wayland backend
  → /mnt/android-wayland/wayland-0（APK创建的外层socket）
  → APK原生Wayland服务器 → Android SurfaceView → SurfaceFlinger
```

这两个socket可能具有相同的文件名，但目录、所有者和角色不同。**应用应连接Phoc的内层socket；Phoc的嵌套backend连接APK的外层socket。** 不能把 `/mnt/android-wayland/wayland-0` 当成所有GNOME应用的显示地址，否则会绕开Phosh窗口管理和内层协议服务。

session（历史材料已移除） 启动Phoc前设置外层 `WAYLAND_DISPLAY`。Phoc启动 `-E /usr/local/bin/moto-phosh-apps` 时提供内层显示环境；session-apps（历史材料已移除） 保存该环境到 `/run/user/1000/moto-session.env`，再启动Phosh、Stevia和辅助进程。user-exec（历史材料已移除） 先降为Linux用户，再导入这个文件，因此从电脑运行的图形程序可以加入正在使用的桌面。

会话还通过 `dbus-update-activation-environment` 把内层Wayland、XDG桌面身份、GTK渲染和图形驱动环境传给D-Bus。否则直接启动的App可能正常，而通过D-Bus激活的App/portal找不到同一显示或选错后端。

当前 phoc.ini（历史材料已移除） 设置 `xwayland=false`，常用竖屏输出为720×1600、scale=2。图像最终由Android映射到物理显示区域；旋转会调整实际输出尺寸。当前链路不经过X11或VNC。

### 2.1 GPU为什么需要单独的缓冲桥

Linux Mesa使用KGSL，Android APK使用厂商EGL。两套实现位于不同进程，各自服务对应用户空间；Linux内部必须保持同一套Mesa。

本机厂商EGL没有提供所需的 `EGL_EXT_image_dma_buf_import`。如果只让Linux分配普通DMA-BUF再交给APK，无法据此保证GPU直接导入。采用的方案是由Android分配双方都能使用的缓冲：

1. Phoc的定制wlroots allocator连接 `/mnt/android-wayland/moto-gpu-alloc`，请求宽、高和像素格式。
2. APK分配AHardwareBuffer，通过Unix socket的 `SCM_RIGHTS` 发送缓冲FD、stride和格式。
3. Phoc通过Mesa/KGSL导入FD，GPU直接画入该缓冲。
4. Phoc经外层Wayland提交缓冲。APK按FD的device/inode和尺寸/格式找到仍持有的AHardwareBuffer，以 `EGL_ANDROID_image_native_buffer` 建立EGLImage后显示。
5. 每个缓冲保留一个socket租约；Linux销毁缓冲时关闭租约，Android释放所有权。APK采样阶段另持有引用，避免过早释放。

相关源码：Android allocator（历史材料已移除）、[Linux allocator客户端](../../shared/graphics/gpu-allocator-client.h)、Phoc/wlroots补丁（历史材料已移除）、APK GPU补丁（历史材料已移除）。

协议使用小端整数。v1：请求为magic `0x4d475055`、width、height、fourcc共16字节；回复magic、字节stride、fourcc共12字节并携带一个FD，缓冲为LINEAR。v2（APK1.23起，KWin moto9）：magic `0x4d475056`，请求再附8字节期望修饰符，回复再附8字节实际修饰符；期望`DRM_FORMAT_MOD_QCOM_COMPRESSED`时宿主以高通私有usage位分配UBWC缓冲，并按dma-buf大小核对确为UBWC布局后才回报该修饰符，否则回报LINEAR（57篇）。服务限制尺寸和最多32个活动租约。该接口依赖APK私有父目录和受控挂载，**没有复制PlatformBridge的peer UID校验**，不能把两个服务的检查条件混写。

KWin输出现以sync_file显式同步（`zwp_linux_explicit_synchronization_v1`），宿主把该AHB直接交给SurfaceFlinger（零拷贝，窗口尺寸变化后先走GLES帧），KWin输出默认为UBWC；细节、开关与实测见57篇。此共享缓冲路径用于不透明桌面输出，内部透明缩略图、Phosh概览和屏幕共享还有GBM、读回或SHM路径。因此不把整个桌面称为全程零拷贝。

### 2.2 保持Linux图形库一致

gpu-env（历史材料已移除） 选中统一的 `/usr/lib/dri`、KGSL、Turnip ICD，开启 `FD_KGSL_ENABLE_DMABUF=1`，Phoc使用gles2，GTK4默认使用GL。GPU启用时清除私有 `LD_LIBRARY_PATH` 和 `LIBGL_ALWAYS_SOFTWARE`。

之前混用旧系统libGL和新私有Mesa导致符号/驱动不一致；现已将六个Mesa子包作为一套签名Alpine包部署并锁定内容哈希。Phoc静态嵌入wlroots，修改wlroots后必须重编Phoc，单换共享库不会改变它。实际renderer、像素读回、进程加载路径的验证记录见27篇（旧Phosh专属记录已删除），不能只查包版本或环境变量。

## 3. 启动顺序、挂载和生命周期

### 3.1 一次正常启动经过什么

```mermaid
sequenceDiagram
  participant APK as Android APK
  participant Host as APK 原生显示与平台服务
  participant Manager as Magisk / moto-phosh
  participant LXC as LXC init
  participant Session as Linux 用户会话
  APK->>Host: Surface创建，启动Wayland、PlatformBridge、GPU allocator
  APK->>Host: 设置尺寸，发布挖孔信息，启动vsync
  APK->>Manager: su --mount-master，start 或 restart-session
  Manager->>Manager: 启动专用Termux音频实例、准备cookie
  Manager->>LXC: 私有mount namespace中启动phosh容器
  LXC->>LXC: system D-Bus、共享目录、FUSE、只读系统设置服务
  LXC->>Session: su linux，dbus-run-session
  Session->>Host: Phoc连接外层Wayland
  Session->>Session: 导出内层显示环境，启动辅助服务与Phosh
  Manager-->>APK: 检测会话环境文件、Phoc、Phosh已就绪
```

对应入口：

| 环节 | 本地源码 | 部署位置/行为 |
|---|---|---|
| Activity、Surface、JNI、后台恢复 | MainActivity.java（历史材料已移除） | APK `dev.moto.phosh` |
| root控制器 | moto-phosh（历史材料已移除） | `/data/adb/moto-phosh/moto-phosh`；flock避免并发启停 |
| 挂载/进入LXC运行环境 | moto_phosh_enter.c（历史材料已移除） | `/data/adb/moto-phosh/moto-phosh-enter` |
| 容器定义 | phosh.config（历史材料已移除） | runtime内 `/var/lib/lxc/phosh/config` |
| 初始化与会话监督 | inittab（历史材料已移除）、init.sh（历史材料已移除）、session-supervisor（历史材料已移除） | `/etc/inittab`、`/usr/local/sbin/moto-phosh-init`、`moto-phosh-supervisor` |
| Phoc和桌面会话 | session（历史材料已移除）、session-apps（历史材料已移除） | `/usr/local/bin/moto-phosh-session`、`moto-phosh-apps` |
| 电脑管理入口 | moto_phosh.py（历史材料已移除） | 固定选择设备，调用上述控制器 |

首次创建原生服务器时APK调用 `restart-session`，使已运行的Phoc重新连接新的外层服务器；重新绑定仍存活的Surface时可调用 `start`。Surface销毁会暂停渲染，恢复时重新绑定；这与主动停止容器是不同操作。菜单“返回Android”把任务移到后台，“停止Linux桌面”才调用stop并释放显示连接。

`restart-session`清理旧环境文件并结束Phoc，由init的监督链重建用户会话；它会中断当前图形应用，不应作为无影响的刷新操作。`stop/start`还会重建容器的 `/run`、`/tmp`、FUSE和挂载。本轮分别验证了会话重启和完整容器重启，未把它等同于整机冷启动。

2026-09-23后续整机重启发现KGSL设备号会变化：外层控制器现于启动容器前stat读取 `/dev/kgsl-3d0` 与 `/dev/dma_heap/system`，通过 `lxc-start -s` 分别添加当前设备的rw/r权限，配置不再写死动态major/minor。直接绕过控制器运行lxc-start不会获得这两项启动参数；日常与APK均应使用moto-phosh入口。详情及恢复验收见[39篇](../39-magisk-daemon-crash.md)。

### 3.2 Android路径怎样进入Linux

Android上的LXC runtime是 `/data/adb/moto-lxc/runtime`；Phosh rootfs在其下 `var/lib/lxc/phosh/rootfs`。enter先创建私有mount namespace，将所需路径绑定到runtime，再 `pivot_root`，最后执行LXC工具。早期仅chroot时，pidfd attach可能把根目录恢复到Android；这就是这里使用pivot_root的原因。

| Android实际资源 | enter运行环境中的路径 | Phosh容器中看到的路径 | 用途 |
|---|---|---|---|
| `/data/user/0/dev.moto.phosh/files/tmp` | `/mnt/android-wayland` | `/mnt/android-wayland` | 外层Wayland、GPU allocator、平台socket、显示元数据 |
| `/data/data/com.termux/files/usr/tmp/moto-phosh-audio` | `/mnt/android-audio` | `/mnt/android-audio`，只读bind | 专用PulseAudio socket及认证材料 |
| `/storage/emulated/0/Linux` | `/mnt/phosh-shared` | `/mnt/android-shared` | Android公用Linux目录 |
| 上一行容器目录 | 容器内bindfs再映射 | `/home/linux/Shared` | 映射Linux用户读写身份 |
| `/dev/kgsl-3d0`、`/dev/dma_heap/system` | runtime的设备视图 | 容器内同名节点 | GPU命令、内存与共享缓冲 |

APK应用数据隔离namespace一度看不到Termux目录；root控制器使用Magisk `--mount-master` 后再创建自己的私有挂载空间，解决可见性问题。enter的新增挂载不留在Android原始mount namespace。

不要通过Android rootfs路径往正在运行的 `rootfs/tmp` 或 `rootfs/run` 上传会话文件：容器里的这两个目录被tmpfs覆盖。应使用 `moto_phosh.py exec/user-exec` 写入运行中的目录，或者先上传非覆盖路径再从容器内复制。

### 3.3 身份与设备边界

Linux桌面进程使用 `linux` / UID1000，APK是Android分配的普通应用UID，音频进程使用Termux UID。root只在容器控制/挂载/部署路径使用；浏览器和日常GNOME应用不以root运行。

该部署是rootful LXC，沿用已有Magisk宽松域；Android全局仍Enforcing，APK仍是普通应用域。Phosh容器共享Android网络namespace，丢弃NET_ADMIN/NET_RAW等能力。GPU节点按当前内核major/minor列入cgroup白名单；没有把物理显示控制器交给Linux。共享显示内存按APK的MCS类别重新标记容器私有 `/dev/shm`。

这是为同一用户桌面建立的本地连接；UID1000与私有目录检查不构成针对不可信Linux应用的完整权限隔离。摄像头、麦克风等未来能力仍需要各自的授权、生命周期与应用接口设计，不能因为root可调用就默认开放。

## 4. Android私有设备接口的契约

服务端是 PlatformBridge.java（历史材料已移除），socket为APK私有 `files/tmp/platform.sock`，容器地址为 `/mnt/android-wayland/platform.sock`。客户端包括 Android设备页（历史材料已移除）、亮度后端（历史材料已移除）、[剪贴板进程](../../shared/platform/clipboard.py)。

每次连接发送一行UTF-8 JSON，以换行结束；服务端返回一行JSON后关闭连接。正常操作返回结果对象，控制操作通常为 `{"ok":true}`；操作错误通常为 `{"error":...}`，连接/解析失败也可能直接关闭连接，客户端必须同时处理I/O错误。协议不是shell命令通道。

| `op` | 请求字段 | 结果/实际语义 |
|---|---|---|
| `status` | 无 | `version:1`，型号、Android版本、时区、窗口焦点、方向、窗口亮度、网络、电池 |
| `brightness-get` | 无 | `level`整数2–100、`followAndroid`；跟随时读取Android亮度设定值 |
| `brightness` | `value`：0.02–1，或-1 | 只修改当前Activity窗口亮度；-1恢复跟随Android |
| `orientation` | `mode`：`system` / `portrait` / `landscape` | 改Activity方向并保存偏好；system使用FULL_USER，尊重Android用户旋转策略 |
| `watch` | `topics`：network / telephony / bluetooth / capture / clipboard 的数组；`epoch`、`seen`：上次返回的值；`timeout`：毫秒，最长60000 | 等到某个主题的版本号变化（由Android回调递增，`HostEvents`）、epoch 不同（APK重启过），或超时后返回 `{epoch,versions,changed}`；在独立线程上等待。容器侧用共享模块 `rungic_host_watch`：收到变化再取完整状态，取代定时轮询（49篇） |
| `settings` | `target`：`network` / `bluetooth` / `display` / `sound` / `datetime` / `location` | 打开对应Android设置Activity或系统面板 |
| `vibrate` | 无 | Android一次35ms震动；当前仅为测试入口，不是feedbackd后端 |
| `clipboard-get` | 无 | 转发独立宿主后端；`available`和`text`，null表示空；锁定/其他 Android 用户/非文本/敏感标记/超长时不可用，无窗口焦点要求 |
| `clipboard-set` | `text`：字符串或null | 转发宿主后端；设置纯文本，null清空；相同正文不重复设置；锁定/其他 Android 用户时拒绝 |

`status`、`brightness-get`可在没有窗口焦点时查询。剪贴板在 APK 2.14 后转发独立后台服务，不经过 UI 线程或 `Activity.hasWindowFocus()`；其他涉及界面的控制操作仍按各自焦点条件处理。敏感、锁定和 Android 用户隔离由独立剪贴板后端检查。

服务端检查peer UID为0或1000，socket权限0666依赖其私有父目录和受控挂载限制可达性；设置3秒socket读取超时和3秒UI线程任务等待，请求上限524288字节。客户端各有更小上限：设备页响应64KiB，亮度后端4KiB/2秒，剪贴板响应512KiB、文本UTF-8输入262144字节。Java侧文本长度上限65536按UTF-16单元计，Python侧另按字符串长度限制；不是任意长度内容转发。

只读查询示例，在项目目录运行：

```bash
python3 tools/moto_phosh.py user-exec /usr/local/bin/moto-platform \
  --request '{"op":"status"}'

python3 tools/moto_phosh.py user-exec /usr/local/bin/moto-platform \
  --request '{"op":"brightness-get"}'
```

这套JSON是我们自己的宿主接口。标准GNOME应用不会自动理解它：需要把它接进应用已经使用的后端，或者使用明确调用它的设备页。下面三种实现展示了不同的连接方式。

## 5. 实例一：把原生Phosh亮度滑条接到Android

目标是用户直接使用Phosh原有滑条。研究发现0.57已采用内部 `PhoshBacklight` 后端基类，旧版 `org.gnome.SettingsDaemon.Power.Screen` 思路不能直接驱动当前滑条；因此在Phosh内部新增一个小后端，而不是另造整套GNOME电源服务。

```mermaid
sequenceDiagram
  participant UI as Phosh 亮度滑条
  participant Base as BrightnessManager / PhoshBacklight
  participant Adapter as PhoshBacklightAndroid
  participant APK as PlatformBridge
  participant Android as Android 窗口
  UI->>Base: 修改亮度目标
  Base->>Adapter: set_level，合并尚未完成的写请求
  Adapter->>APK: 工作线程发送 brightness / value
  APK->>Android: UI线程设置 screenBrightness
  Android-->>APK: 更新窗口属性
  APK-->>Adapter: ok
  Adapter-->>Base: 异步操作完成
  loop 每2秒，写入期间跳过
    Adapter->>APK: brightness-get
    APK-->>Adapter: level / followAndroid
    Adapter-->>Base: backend_update_level / notify
    Base-->>UI: 更新滑条位置
  end
```

实现分三处：

1. backlight-android.c/.h（历史材料已移除） 实现异步set/finish和状态读取，GTask中执行socket I/O，避免卡住Phosh UI。generation检查避免旧轮询覆盖刚刚拖动的值。
2. Phosh集成补丁（历史材料已移除） 在monitor创建backlight时检测宿主socket，存在则用Android后端，否则保留原sysfs逻辑；新增json-glib构建依赖。
3. PlatformBridge（历史材料已移除） 在Android UI线程更新 `WindowManager.LayoutParams.screenBrightness`，只作用于Linux桌面窗口。

上游BrightnessManager继续负责滑条与亮度状态。验收先查 `org.gnome.Shell.Brightness.HasBrightnessControl=true`，再真正拖动滑条，读取Android `windowBrightness=0.53`，最后恢复-1并观察回传。这比“D-Bus能返回true”多验证了硬件侧操作路径。

跟随Android时回传的是 `Settings.System.SCREEN_BRIGHTNESS` 对应用户设定，不是实时自动亮度、实际发光量或温控后的面板输出。原生滑条拖动会进入窗口覆盖模式；“Android设备”页可恢复跟随。当前不提供第二套Linux自动亮度算法。

## 6. 实例二：让普通Linux应用共用Android声音输出

声音采用成熟的PulseAudio协议，因此播放器和浏览器无需认识我们的JSON接口：

```text
Firefox / Showtime / GStreamer / 系统提示音
  → Linux libpulse
  → 容器内PulseAudio，默认sink=android
  → module-tunnel-sink-new
  → unix:/mnt/android-audio/native
  → Termux UID下的专用PulseAudio 17.0-4
  → module-aaudio-sink，sink=android_output
  → Android AAudio / 音频系统 / 当前输出设备

需要在手机本机出声的流（语音助手从手机发起时，或用户在音量设置里选择）
  → 容器内PulseAudio，sink=android_phone（module-pipe-sink，FIFO 16 KiB）
  → media-bridge（按sink状态启停）
  → 私有capture.sock，op=phone-output，48 kHz双声道S16LE
  → APK CaptureBridge的AudioTrack，setPreferredDevice：有线/USB/蓝牙耳机优先，否则扬声器
```

| 配置 | 所在位置与职责 |
|---|---|
| android-audio（历史材料已移除） | 宿主 `/data/adb/moto-phosh/android-audio`；创建专用runtime/state、以Termux UID启停进程、复制认证cookie |
| android-audio.pa（历史材料已移除） | 宿主AAudio sink、Unix socket和空闲释放；独立于用户其他PulseAudio实例 |
| android-tunnel.pa（历史材料已移除） | 容器 `/etc/pulse/default.pa.d/50-android.pa`；48kHz双声道、cookie认证、2秒重连 |
| `audio-cookie` | 容器 `/etc/phosh/audio-cookie`，1000:1000/0600；部署时创建，不进通用归档 |

研究比较了Termux/Droidspaces与已有Winland Oboe桥。前者已经提供PulseAudio服务器、混音和AAudio后端，适合保持普通Linux应用接口；后者虽有音频JNI，但还需核对自定义传输、权限、缓冲和生命周期。本轮选取前者的音频部分，没有整体安装其X11/VirGL图形方案。

验收覆盖默认sink、真实AudioFlinger轨道、播放、宿主重连与容器重启。空闲时sink的 `SUSPENDED` 是释放音频设备的正常状态，不能单凭它判断无声。第33篇已增加反向AudioRecord输入流和Android录音授权；输出sink的monitor不是麦克风。`android-audio watch`现在探测专用宿主PA的响应，连续两次失败后只重启经UID/路径/配置校验的专用实例，约30秒故障恢复已实测。

## 7. 实例三：让Firefox通过标准portal共享桌面

```text
Firefox getDisplayMedia
  → session D-Bus：org.freedesktop.portal.Desktop / ScreenCast
  → xdg-desktop-portal 按 phosh-portals.conf 选择wlr后端
  → 自定义portal-wlr + 用户确认框
  → Phoc 的 screencopy / image-copy 协议采集Linux桌面
  → PipeWire流，交还node与remote FD
  → Firefox取得视频帧
```

这里复用的是Linux桌面采集协议，不需要把整个Android屏幕再录制一遍。PulseAudio负责音频，PipeWire/WirePlumber负责图像流。第33篇新增的摄像头走Camera2/PipeWire Video/Source；屏幕共享的portal-wlr路线保持原样。

三个必须同时满足的条件：

1. **服务发现正确。** session-apps（历史材料已移除） 导入XDG桌面身份和内层显示环境；portals.conf（历史材料已移除） 显式选择ScreenCast/Screenshot的wlr后端，不能仅依赖“包已装”。
2. **缓冲协商适用于KGSL。** portal-wlr0.8.4在没有GBM设备时仍查询DMABUF格式并崩溃。GDB定位到NULL gbm调用；补丁（历史材料已移除） 跳过不成立的DMABUF分支，使用上游已有SHM路线。桌面正常显示路径仍为GPU。
3. **用户能看到确认框。** 普通GTK窗口曾被Firefox遮住；share-chooser.py（历史材料已移除） 使用gtk4-layer-shell覆盖层和键盘焦点，允许取消或明确共享。返回上游期待的原始输出选择字符串。配置（历史材料已移除） 上限30fps。

先用 test-portal.py（历史材料已移除） 验证CreateSession/SelectSources/Start/OpenPipeWireRemote到GStreamer采集，再验证真实Firefox getDisplayMedia、live状态和canvas像素，最后检查重启后仍可采集。测试完成关闭session/tracks，避免把“浏览器弹出了选择框”误当成已经有视频流。

## 8. 其他通路如何复用上述结构

| 能力 | 完整连接 | 关键语义与源码 |
|---|---|---|
| 触摸 | Android MotionEvent → JNI/Rust → 外层 `wl_touch` → Phoc → 有焦点的应用或Phosh | 保留触点ID和down/move/up，按Surface大小换算缓冲坐标；MainActivity（历史材料已移除）、触摸补丁（历史材料已移除） |
| 中文输入 | GTK等客户端text-input → Phoc → Stevia input-method → UIM候选 → Phoc提交文字 | 本轮已验证路线在Linux内部；Stevia补丁（历史材料已移除）。Android IME另有可选入口，未当作通用完整中文方案验收 |
| 收起键盘 | Android返回键 → 当前Android IME，或root控制器的hide-keyboard → 用户session D-Bus `sm.puri.OSK0.SetVisible(false)` | 先查Visible，已隐藏时才打开APK菜单；无需杀死Stevia |
| 剪贴板 | 内层Phoc data-control ↔ wl-paste/wl-copy ↔ moto-clipboard ↔ PlatformBridge ↔ ClipboardManager | 绕开不必要的外层selection中转；前台同步、去重、限长、跳过敏感标记；[clipboard.py](../../shared/platform/clipboard.py) |
| 网络/设备页 | GTK4/libadwaita设备页 → JSON status/settings → ConnectivityManager等真实API/Android设置 | 数据采集与控制分别处理；platform.py（历史材料已移除）。这个页面不能自动替代GNOME的NetworkManager接口 |
| 挖孔 | WindowInsets/DisplayCutout/RoundedCorner → 原子INI → Phosh文件监视 → 顶栏/exclusive zone/应用可用高度 | android-display.c（历史材料已移除），按当前mode和scale换算；不是为整个画面再叠一条永久黑边 |
| 刷新 | Choreographer → JNI frameTick → Rust条件变量 → 合成和frame callback；EGL成功提交累计。静止400ms后按需停发vsync：合成线程改为poll Wayland fd与kick eventfd，恢复时经eventfd通知Java主Looper（57篇）。手机与cast（电视、助理屏）按各自的提交分开渲染；没人看的输出限速：全屏遮住的手机1 s一帧，收边或无人显示的助理屏200 ms一帧；限速期间被顶替帧的presentation feedback攒到下一轮再回discarded，否则KWin照常出帧（65篇） | DisplayPacer（历史材料已移除）、frame clock（历史材料已移除）、Rust补丁（历史材料已移除） |
| 横竖屏 | 设备页orientation → Android Activity方向 → Surface尺寸/触摸换算/INI → Phoc输出与Phosh布局 | 同步更新坐标、模式、安全区域；旋转修复（历史材料已移除） |
| 文档共享 | GNOME/portal → 容器私有FUSE → 文档导出；公共文件另经bindfs访问 | init.sh（历史材料已移除）。容器节点10:229/0666，Android原 `/dev/fuse` 仍0600 |
| 相机/麦克风 | Android普通授权 → 私有capture.sock → 标准PA source / PW Video/Source → GNOME/Firefox | [media-bridge.py](../../shared/media/media-bridge.py)、[camera-source.cpp](../../shared/media/camera-source.cpp)。session-apps自动启动，flock防重复；后台释放设备 |
| 手机本机输出 | 应用选`android_phone`（“手机本机”）sink → module-pipe-sink FIFO → media-bridge → capture.sock `phone-output` → APK AudioTrack（指定本机设备） | [media-bridge.py](../../shared/media/media-bridge.py)、`CaptureBridge.phoneOutput`。默认sink仍为跟随Android路由的`android`；投屏时两路并存（实测APK音轨在SPEAKER输出线程、Termux音轨在PROXY）。见59篇 |
| 长按Home呼出语音助手 | Plasma Mobile导航栏Home按钮长按400 ms → 任务栏C++ `assistantHold` → D-Bus `dev.moto.VoiceAssistant /Assistant Hold` → 常驻悬浮层（layer-shell，按需加载KWin blur/contrast）→ 语音服务`AssistantTalk`/`ReleaseTalking` → 固定的助理对话；实时语音在按下时才连接，麦克风音频先在本地排队 | [67篇](../67-home-assistant.md)。vendor plasma-mobile的导航按钮新增`holdable`；单击Home发`Hide` |
| 状态栏、导航栏跟随应用配色 | 应用声明配色方案（`qApp` 属性 `KDE_COLOR_SCHEME_PATH`，KColorSchemeManager 也是这样做的）→ plasma-integration 平台主题 → Wayland `org_kde_kwin_server_decoration_palette` → KWin `Window::colorScheme` → convergentwindows 脚本的 `WindowColors.qml`（窗口激活或配色变化时）→ D-Bus `org.kde.plasmashell /Mobile setActiveWindowColorScheme(屏幕, 配色)` → `ShellDBusObject::windowBackground()` 读取 .colors 文件 → 状态栏（Header 组）和导航栏（Window 组）用应用的颜色；深色背景时前景换成 Complementary | [59篇](../59-voice-agent.md)“面板跟随应用配色”。标准协议，所有声明了配色方案的 KDE/Kirigami 应用都适用；没有声明的应用（KWin 记为 `kdeglobals`，例如微信、系统设置）保持主题配色 |
| 电脑操作（Codex 默认；Luna API 显式备选） | 当前认证 Codex / 所选模型 → MCP `rungic-desktop` → `desktop_screenshot` / `desktop_act` → KWin ScreenShot2（JPEG q85）、XDG RemoteDesktop 门户及 KWin `commitText`。API 模式才提供 `desktop_goal`：Luna Responses `computer` 循环，共用截图/输入 | [105篇](../106-codex-desktop-operation.md)，原始 API 方案见 [68篇](../68-luna-computer-use.md)。G100 常驻 Agent 计算器看图/点按/结果核对通过；完整语音消息和电话未在本轮验收 |
| 电脑操作（方案二） | Codex Agent → MCP `moto-desktop`（`moto-cua`，沙箱外）→ arc-cua运行时 + JEV → 观察：AT-SPI（流水线D-Bus）+ KWin一次性脚本`callDBus`；执行：XDG RemoteDesktop门户（KWin fake-input，相对移动）与宿主`text-commit` | [60篇](../60-computer-use.md)。应用收到的是普通Wayland键鼠事件；AT-SPI动作仅作兜底 |
| 网络文件 | Nautilus/GIO → GVfs对应协议后端 → WebDAV/SMB/NFS服务器 | WebDAV中文读写已实测；SMB/NFS仅安装后端，未验用户网络盘 |
| 共享存储（XDG用户目录） | `~/Shared` bindfs → Android MediaProvider FUSE（`/storage/emulated/0/Plasma`） | [69篇](../69-filesystem-capabilities.md)：已去掉`--direct-io`，恢复共享映射和SQLite WAL；符号链接、大小写、执行等是Android FUSE本身的限制；`moto-fs-audit`用于检查 |
| 外接显示（Miracast） | Android WFD显示 ← 宿主悬浮窗SurfaceView/Presenter ← 宿主额外wl_output“Moto Cast” ← KWin(moto11)输出CAST-n的全屏toplevel；Plasma经KScreen进入docked模式 | [58篇](../58-miracast-desktop-feasibility.md)第2步；宿主`cast.rs`、KWin `wayland_display/backend/output`、kscreenosd补丁。连接由root工具`moto-cast`；Linux入口（快捷开关“投屏”、`/usr/local/bin/moto-cast`、语音助手）经平台桥`cast`调用它，电视端断开由`moto-cast-watch`自动重连（第5步） |
| 投屏触控板/键盘 | 手机`CastControls`触控面板 → `NativeBridge.castPointer` → 宿主`cast_pointer`（wl_pointer只进投屏窗口）→ KWin输出CAST-n（软件光标）；Android输入法 → 已有按键/文字通道 → 宿主wl_keyboard或text-input-v3 → KWin `commitHostText` → 焦点应用 | 58篇第3步。text-input-v3路径同时让手机模式下的Android输入法能提交中文；键码表已按input-event-codes改正。双指滚动经KWin嵌套后端转给应用时保留手指来源与axis_stop，并按输出缩放换算（KWin moto17，66篇） |
| 凭据 | GNOME应用/libsecret → session D-Bus Secret Service → GNOME Keyring；Seahorse管理 | 使用上游服务；首次持久钥匙串由用户设密码，容器没有可用于PAM自动解锁的真实登录口令 |

显示元数据有两个文件：`/mnt/android-wayland/android-display.ini` 保存安全区域，坐标单位是Wayland帧缓冲像素；`android-refresh.ini` 保存Android报告Hz、支持范围、请求Hz与APK成功提交fps。两者都通过AtomicFile发布，避免读到半写入文件。

本机竖屏安全顶部115物理像素先转成约77缓冲像素，再按scale=2和留白得到43逻辑像素的Phosh顶栏。旋转曾使代码使用“历史最大输出宽度”，回竖屏顶栏错误变大；修正为当前mode宽度后连续两轮恢复正常。必须同时核验坐标单位和当前模式，单改一个padding数值不能解决这类错误。

刷新策略是触摸后请求最高120Hz并保持2秒；持续画面提交达到阈值时请求至少60Hz，静止后释放请求。Android显示策略和温控仍可覆盖它。实测交互约89fps，静止约2fps空闲提交；GNOME嵌套输出的refresh=0仍表示未知，不能填一个虚构120Hz模式来“补齐”设置页。

## 9. 我们是怎样研究、验证和逐步修正的

### 9.1 每个功能沿用同一个研究顺序

这套顺序来源于用户明确要求，已记录到 [AGENTS.md](../../AGENTS.md)。

1. **先定义用户动作与完成标准。** 例如“播放视频能听见声音”，要包含应用、Linux sink和Android输出轨道；“共享屏幕”要获得真实视频帧；“设置有亮度滑条”要实际改变窗口并能回传。
2. **定位缺失层。** 依次查应用接口、服务注册、进程环境、IPC、缓冲/权限、Android API或内核能力。环境变量、服务缺失、ABI混用和硬件后端缺失是不同故障。
3. **广泛比较已有工作。** 查上游、类似Android容器、手机Linux项目和实际源码。记录版本/提交、依赖、许可证与本机差异；区分“源码有实现”“包已安装”“本机已通过”。
4. **先做一条最小真实链路。** 图形读回像素、声音经过真实sink、中文候选提交、共享取得PipeWire帧。此时先不加大量UI或全局兼容层。
5. **把证明可行的链路接进共同入口。** 选择标准服务、Phosh内部后端或私有设备页；补启动顺序、后台/重连、错误处理和状态回传，避免每个App重复配置。
6. **在普通用户和真实应用里验收。** 既测协议，也测Firefox/GTK等调用者。调试profile和测试文件独立，避免编辑用户未保存草稿或覆盖个人设置。
7. **重启与清理后再确认。** 检查单实例、挂载、环境、cookie、库版本；确认Docker和已有Alpine容器仍正常；移除临时编译包、监听端口、转发和输入探针。
8. **同时留下结论与可复查材料。** 保存补丁、固定源码、构建参数、包锁、产物哈希、测试结果和未完成边界。测试未覆盖的部分继续列为待验收。

### 9.2 实际遇到的问题如何改变了方案

| 当时的现象或候选 | 找到的关键证据 | 最终处理 | 为什么不能只用表面现象判断 |
|---|---|---|---|
| 想直接显示Wayland，比较原型与现成项目 | Winland/Smithay已有可与Phoc连接的Wayland/JNI基础；Termux:X11属于另一显示路线 | 保留原生Wayland基础，补触摸、GPU、调度和宿主接口 | 项目README支持Wayland，不等于本机GPU和输入已打通 |
| Linux缓冲传到APK仍可能走CPU | 厂商EGL缺少所需DMA-BUF导入扩展 | Android先分配AHardwareBuffer，Linux导入绘制 | 看见GPU renderer名称不能证明跨进程显示没有回读 |
| GTK/设置出现图形库混用 | 进程maps和依赖显示不同版本libGL/EGL/Gallium | 六个Mesa子包统一到/usr，D-Bus环境一致 | 改LD_LIBRARY_PATH只能覆盖部分启动路径 |
| 设置App因空显示mode列表崩溃 | Phosh忽略refresh=0的有效嵌套输出，GNOME触发断言 | 保留有效宽高模式，0继续表示未知刷新率 | 给UI伪造60/120Hz会掩盖输出后端语义 |
| 音频只有Dummy Output | libpulse查询只有auto_null，实际无宿主出口 | 复用Termux AAudio后端和PulseAudio tunnel | ALSA卡名可读或GStreamer运行并不证明扬声器收到声音 |
| 浏览器宽度不适合手机、中文未生效 | 桌面UI裁切；firefox-intl包为空；分发扩展未自动加载 | mobile-config-firefox移动布局，标准policy安装官方中文XPI | 包名含intl或偏好写了zh-CN都不是中文UI证据 |
| Firefox报告软件合成 | KGSL无DRM节点触发分类问题；实际Mesa FD710可绘制 | 可修改的WebRender默认值，验证WebGL像素/视频和sandbox | 图形兼容修复没有要求关闭网页沙箱 |
| Stevia拼音每键被取消 | GTK preedit布局反馈触发OTHER，但周围文字/选区没变 | 仅在真实文本/选区等变化时重置completer | 已有UIM引擎无需先替换整套输入法 |
| portal有确认框却没有流 | GDB定位无GBM时的格式查询崩溃；一般GTK确认框又会被Firefox盖住 | 条件化DMABUF协商，使用SHM；确认框改layer-shell | 服务名存在、窗口弹出都不代表采集链已经完成 |
| 文档portal无法挂载 | root-only宿主/dev/fuse节点绑定进容器 | 容器私有FUSE节点，保留宿主权限 | 不需要为一个Linux服务修改Android全局设备权限 |
| 原生亮度滑条不可用 | 0.57实际使用PhoshBacklight，WL-1没有物理sysfs connector | 小型Android backlight后端 | 旧GNOME接口教程不一定适用于新Phosh架构 |
| 横屏回来顶栏变大 | safe area使用历史最大mode宽度 | 使用当前mode，验证两轮切换和缩放 | 单次竖屏截图无法覆盖旋转后的状态 |

早期对比的Droidian/libhybris、gst-droid/droidmedia、Waydroid、bluebinder等仍保留在29篇：有些运行方向不同，有些假设自己拥有Android HAL/蓝牙控制器。它们可以提供源码与协议参考，本轮没有将它们整体装进保留完整Android的LXC。未采用不表示项目不能用；只是当前设备和已验证范围不支持直接作此结论。

### 9.3 证据应该能回答哪一层已经通过

| 要确认的结论 | 已保存的本地证据 |
|---|---|
| 查过哪些来源，而非凭旧教程猜测 | fetches.json（历史材料已移除）、[29篇](29-reuse-research.md)、亮度/凭据补充核验（历史材料已移除） |
| 浏览器真实GPU/沙箱/中文状态 | 前后诊断（历史材料已移除）、WebGL与视频（历史材料已移除） |
| 原生亮度滑条确实到达Android | 窗口亮度结果（历史材料已移除） |
| 浏览器真的拿到了共享帧 | Firefox共享结果（历史材料已移除）、portal重启后采集日志（历史材料已移除） |
| 中文问题与修复有可重复输入 | 独立输入探针（历史材料已移除）、连续输入画面（历史材料已移除）、Stevia补丁（历史材料已移除） |
| 跨系统复制含中文/换行/emoji且能恢复 | 普通剪贴板测试（历史材料已移除）、敏感标记测试（历史材料已移除） |
| 并非依赖一次性的手工启动 | 完整容器重启记录（历史材料已移除）、清理后的最终检查（历史材料已移除） |
| 设备文件与保存的产物一致 | 逐文件SHA256/权限（历史材料已移除）、归档校验（历史材料已移除） |

相应测试脚本保留在电脑refs中；手机端临时探针已清理。需要复跑时先部署对应脚本到适当路径，不能照抄旧调试路径并假定仍存在。浏览器诊断脚本要求独立测试profile和临时Marionette环境；不应直接连接用户日常profile。

## 10. 排查时按连接顺序走

下面是在 `/home/kevinzhow/moto` 下运行的检查命令，使用现有工具固定选择目标设备。D-Bus查询可能按需激活相应服务，但不修改用户配置。

```bash
# 容器及会话日志
python3 tools/moto_phosh.py status
python3 tools/moto_phosh.py log

# 内层会话环境、外层宿主socket与显示元数据
python3 tools/moto_phosh.py user-exec sh -c \
  'printf "Wayland=%s Runtime=%s\n" "$WAYLAND_DISPLAY" "$XDG_RUNTIME_DIR"; ls -l /mnt/android-wayland; cat /mnt/android-wayland/android-refresh.ini'

# 已部署图形库/进程加载路径审计
python3 tools/moto_phosh.py exec /usr/local/bin/moto-graphics-audit

# Linux声音出口；空闲SUSPENDED本身不是失败
python3 tools/moto_phosh.py user-exec pactl list short sinks

# 原生Phosh亮度能力
python3 tools/moto_phosh.py user-exec gdbus call --session \
  --dest org.gnome.Shell.Brightness --object-path /org/gnome/Shell/Brightness \
  --method org.freedesktop.DBus.Properties.Get \
  org.gnome.Shell.Brightness HasBrightnessControl

# 文档portal是否能建立导出目录
python3 tools/moto_phosh.py user-exec gdbus call --session \
  --dest org.freedesktop.portal.Documents --object-path /org/freedesktop/portal/documents \
  --method org.freedesktop.portal.Documents.GetMountPoint
```

| 症状 | 先看哪里 | 如何判断下一步 |
|---|---|---|
| APK有外框但无桌面 | Android的MotoWayland日志、外层socket、Phoc与session.log | 没socket查APK初始化；有socket而Phoc失败查backend/环境/挂载；Phoc正常再查Phosh |
| 终端启动正常，点击图标失败 | `moto-session.env` 与D-Bus激活环境、App日志、maps | 优先确认内层Wayland和统一Mesa环境，不先换渲染库 |
| 有画面但滑动卡顿 | android-refresh.ini、Android显示/温控信息、提交帧计数 | 区分请求Hz、Android报告Hz、物理mode、成功提交fps和内容帧率 |
| 显示按钮却操作无效 | Linux后端请求、PlatformBridge返回、Activity焦点 | `ok`后再验证宿主状态；无焦点错误不等于缺root权限 |
| 原生Wi-Fi页显示未连接但浏览器联网 | 设备页status与NetworkManager服务是否存在 | 当前可能是标准GNOME接口缺失，而非实际断网；不要启动NM接管Android网卡来“修UI” |
| 无声 | sink是否android、tunnel、cookie权限、宿主专用Pulse日志、AudioFlinger | 按两端逐段查；不输出cookie正文 |
| 共享确认后失败 | session.log、pipewire.log、wireplumber.log、portal后端与输出格式 | 区分D-Bus选择、确认交互、格式协商、PipeWire与浏览器消费 |
| 键盘不收起/中文被取消 | OSK Visible、输入焦点、Stevia keyboard.log | 先查状态与text-input事务；协议日志只使用专用测试文字 |
| 旋转后布局不对 | Android视图尺寸、INI、Phoc当前mode/scale、Phosh safe-area日志 | 核对物理/缓冲/逻辑三种坐标，避免只改最终像素常量 |

需要主动重启验证时，先保护当前图形应用中的工作，再使用 `restart-session` 或 `stop`、`start`。诊断命令成功只证明它检查的那一层，不能替代真实应用验收。

## 11. 后续功能怎样选择接入点

按“桌面原本向谁请求这个能力”决定接哪一层：

| 后续目标 | 已有基础 | 尚需研究/实现的连接 |
|---|---|---|
| 麦克风/会议音频 | 第33篇已提供标准source、录音/视频/浏览器采集与可见状态控制 | 后续需验证音频焦点、耳机/蓝牙路由、长会议和精确音画同步 |
| Plasma网络、Wi‑Fi与蜂窝设置页 | NetworkManager模拟服务：接入点、已保存网络、连接/断开/忘记；ModemManager模拟服务：SIM、运营商、信号；移动数据开关对应Android `svc data`（docs/73） | 保留Android连接所有权；SIM PIN、选网、APN、数据漫游与VPN管理仍在Android设置 |
| 蓝牙 | BlueZ子集模拟服务（`org.bluez`，无bluetoothd、无HCI）：bluedevil/bluetoothctl的开关、搜索、配对、连接由Android蓝牙API完成（docs/73） | 配对在Android弹窗确认；音频路由仍由Android决定；OBEX文件传输未接入 |
| 摄像头/定位/传感器 | 第33篇已接Camera2→PipeWire→GNOME/GStreamer/Firefox | 摄像头长时间/后台与硬编码仍缺失；定位/传感器需另行比较Location→GeoClue、Sensor→iio-sensor-proxy |
| 应用震动反馈 | vibrate测试请求已到Android | 研究feedbackd后端与事件/强度/取消语义；不是把测试按钮当成应用通用支持 |
| 视频硬解/硬编码 | GPU绘制与软件编解码已验收；第34篇验证Android高通AVC/HEVC硬编→硬解，原厂V4L2节点可枚举 | Linux后端未接通；先验证现成V4L2帧流兼容性，再比较MediaCodec桥与gst-droid/droidmedia维护成本，Firefox需独立适配，见[34篇](34-hardware-codec-audit.md) |
| 内容索引 | LocalSearch应用存在 | 核对并补Landlock内核能力、ABI和原厂模块兼容；保留提取器隔离 |
| 图形应用更新 | Alpine包管理与固定Mesa清单 | 更新事务、版本约束、失败恢复与KGSL运行时一致性；不是直接套用未验证的Flatpak图形栈 |
| Flatpak 应用 GPU | 运行时 GL 扩展 `org.freedesktop.Platform.GL.rungic`（rungic-flatpak-gl，本项目 Mesa 按 Freedesktop 25.08 构建），`FLATPAK_GL_DRIVERS=rungic`；Telegram 为 FD710（2026-09-28） | 其他运行时分支（24.08 等）尚无扩展；`--device=dri` 应用拿不到 `/dev/kgsl-3d0`，只能用 softpipe，见 49 篇 |

新功能的本地记录至少应包含：用户动作/验收目标、当前缺失层、候选与固定源码、许可证、本机差异、选用与放弃原因、两端接口契约、权限和数据所有权、启动/停止/重连、补丁/构建/部署位置、真实应用证据、回退方法与未验收边界。实现前继续广泛核对，而不是把29篇的一次检索当成所有后续功能已经完成研究。

## 12. 维护与复现时以哪些文件为准

2026-10-03 按当前目录重写；Phosh 时期的对照表（`phosh/`、`native/phosh/`、refs 中的源码镜像）见 git 历史。每个文件归哪个功能，用 `python3 tools/feature_inventory.py owner 路径` 查（quality/README.md）。

| 修改类型 | 改哪里 | 构建与验证 |
|---|---|---|
| Android 界面、权限、平台 op、旋转/Insets | `android/app/` | `android/build-apk.sh`，沿用开发签名；平台 op 的回复格式见 `quality/contracts/platform-bridge.json`，Linux 一侧同时更新 |
| 外层 Wayland、GPU allocator、frame clock | 上游部分在 `packages/android-host/`（补丁队列），自有模块在 `android/host/` | `tools/prepare_android_host.py` 组装源码，`android/build-native-core.sh` 交叉编译，再打 APK |
| 内层 KWin（Android 后端、全屏、录屏） | `packages/kwin/`（补丁队列，`tools/pq.py`） | `tools/build_on_device.py` 或 `rungic_dev.py deploy kwin`；系统层面可先用 `tools/system_test.py` 在无头 KWin 上测 |
| Plasma Mobile 外壳、设置、键盘、Portal 等上游组件 | `packages/<组件>/` | 同上，按组件部署 |
| 共享硬件桥（音频、摄像头、编解码、剪贴板、网络、蓝牙、电话） | `shared/`、`system/` | 所属软件包（`packaging/*/package.json` 的 `paths`）经开发覆盖或发布部署；接口见 `quality/interfaces.yaml` |
| 会话、设备面板、桌面工具 | `desktop/` | 同上 |
| 挂载与容器初始化 | `system/plasma.config`、`system/init`、`system/rungic-plasma`；enter 程序 `tools/rungic_*_enter.c` | Android 侧文件随发布部署；挂载配置变化需完整容器重启验证 |

功能状态与未完成事项统一维护在[30篇](30-feature-adaptation.md)，接口和连接方式维护在本文，原始来源与实测证据保存在refs。这样版本升级时可以分别判断：需要重新研究的接口、需要迁移的补丁，以及需要重新运行的验收。

2026-09-23桌面启动补修：发现Firefox发行版desktop三个Exec直接调用原程序，绕过编解码包装入口；之前只测命令行漏检了此路径。新增`phosh/install-firefox-launcher.py`生成同ID用户desktop覆盖，普通启动/新窗口/隐私窗口均走包装脚本。恢复步骤、原始问题证据与桌面启动复测见修复记录（历史材料已移除）。

2026-09-23输入验证流程修正：Marionette仅连接显式独立测试profile，禁止用日常profile保存自动化偏好。真实屏幕键盘需独立验收：焦点/ASCII提交、中文preedit与候选、正常退出确认与新进程启动。此前残留focusmanager.testmode触发Firefox154空指针，已清理，见[36篇](../36-firefox-input-fix.md)。迁移glibc发行版时保留Android接口契约、重编Linux侧二进制并验证会话服务，详见[37篇](../37-linux-distribution-evaluation.md)。

## 调试与性能观测入口（2026-09-24）

Agent可直接调用的设备诊断、崩溃现场、统一追踪（perfetto + KGSL + KWin FTrace）与按控件操作见[55篇](../55-agent-native-debugging.md)；入口为工作区`.mcp.json`中的`moto` MCP服务及`tools/rungic_agent.py`等命令行。桌面输出链的分段耗时与GLES/Vulkan对照见[56篇](../56-kwin-vulkan-quantification.md)。

## 名称对照（Rungic改名，B阶段2026-09-26，C阶段2026-09-27）

容器内的接口在B阶段改名，Android侧在C阶段随APK改名（切换工具`tools/rungic_cutover.py`，docs/70）；本文上面的描述保留原名。

| 位置 | 改名前 | 改名后 |
|---|---|---|
| 包 | `moto-plasma-*`、`moto-codec`、`moto-cua`等；发布元包`moto-plasma-release` | `rungic-plasma-*`、`rungic-codec`、`rungic-cua`等；`rungic-release` |
| 会话与桥接单元 | `moto-plasma-session`、`moto-plasma-{display,brightness,media,clipboard,network,shared}`、`moto-voice-{agent,overlay}`、`moto-coredump.path` | 同名`rungic-*` |
| D-Bus | `dev.moto.VoiceAssistant`、`dev.moto.VoiceAgent`（`/dev/moto/VoiceAgent`）、`dev.moto.Cua`（`/dev/moto/Cua`）、`dev.moto.Android.{Power,Network}` | `com.rungic.*`（对象路径`/com/rungic/*`） |
| 编解码 | GStreamer `motoh264enc`/`motoh26{4,5}dec`/`motovp9dec`，FFmpeg `h264_moto`等，`libmotocodec.so` | `rungich264enc`等，`h264_rungic`等，`librungiccodec.so` |
| 相机 | PipeWire节点`moto.camera.N`、`device.api=moto-android`，libcamera相机`moto-pipewire-{back,front}` | `rungic.camera.N`、`rungic-android`、`rungic-pipewire-{back,front}` |
| KWin与显示设置 | `MotoDisplay`命名空间（`android-display-client.h`） | `RungicDisplay` |
| APK | `dev.moto.plasma`（Java包同名，日志标签`Moto*`，标签“Plasma Mobile”） | `com.rungic.plasma`（`Rungic*`，“Rungic”） |
| Android侧目录与程序 | `/data/adb/moto-{plasma,lxc,wfd}`，`moto-plasma`、`moto-plasma-enter`、`moto-lxc`、`moto-cast`；镜像`…/lxc/plasma/images/` | `/data/adb/rungic-{plasma,lxc,wfd}`，`rungic-plasma`、`rungic-plasma-enter`、`rungic-lxc`、`rungic-cast`；镜像`/data/adb/rungic-lxc/images/` |
| 镜像设备与SELinux | dm `moto-plasma-root`/`moto-plasma-before`，`moto_plasma_image` | `rungic-root`/`rungic-before`，`rungic_image` |
| 容器绑定挂载点 | `/var/lib/moto-{host,cores,apt}` | `/var/lib/rungic-{host,cores,apt}`（旧名同时挂载到D阶段） |
| APK与KWin之间 | `/mnt/android-wayland/moto-gpu-alloc`，投屏输出厂商“Moto” | `rungic-gpu-alloc`（APK保留`moto-gpu-alloc`链接），厂商“Moto”保留到D阶段（KWin两者都认） |
| 其他 | `debug.moto.*`，Termux音频目录`moto-plasma-audio`，LXC主机名`moto-plasma` | `debug.rungic.*`，`rungic-plasma-audio`，`rungic` |
| Docker | `/data/adb/moto-docker`、`moto-docker`，SELinux `moto_docker`、`moto_docker_file`、`moto_docker_image`，链`MOTO_DOCKER_*` | `/data/adb/rungic-docker`、`rungic-docker`，`rungic_docker*`，`RUNGIC_DOCKER_*`（`rungic_cutover.py docker-up`） |
| 保持不变 | ROM中的Magisk引导脚本随下次刷机 | — |

2026-09-28 首次镜像安装增加独立就绪契约（79 篇）：Magisk 首启脚本 → APK 私有原子状态文件（release/state/phase）→ 原生 loading → root `account-prepare` 的容器/账户前置检查 → 账户表单 → Plasma 启动。只有状态匹配当前 product 且所有准备完成才放行；root 控制器另查完成标记。不能用普通重启或已初始化数据替代空白刷机首启验证。

2026-09-28 UX 第一批重构（[82 篇](../82-first-run-ux-refactor.md)）：安装原子状态升级 schema 2（兼容 1）；账户助手持有事务锁并提供 `--status`/`pending`，rootfs 标记 `account-protocol=2`，组包与安装验收检查该契约。APK 2.8 与 native JNI 一起更新，通过渲染线程请求屏障和当前手机输出的真实完成反馈撤除 loading；旧帧、投屏帧、合成超时反馈不能满足门槛。显示确认不等于桌面全部服务就绪。现阶段实机证据是已有账户启动进入 Plasma 欢迎页，新组件完整清数据首启另验。

2026-09-28 X70 全新账户目录链补齐：Koko/Qt 与 GLib 应用 → XDG user-dirs → `system/user-dirs` → `~/Pictures` 等到 `~/Shared` 的链接 → bindfs → Android `Plasma/` 共享目录。会话在共享挂载真实就绪后幂等创建标准目录并调用 xdg-user-dirs-update，不依赖历史用户目录；Documents/Desktop 继续本地化。实现、上游 0.19 核验与测试见 docs/69，X70 实机证据见 docs/83。


2026-09-28 显示大小公共接口：`KScreen → libkscreen → Wayland 输出管理 → KWin AndroidOutput → Android 宿主`。共享策略在 `shared/display-policy/`，Android 内屏以标准 manufacturer/model=Rungic/Handset 识别，KWin 保存可选 logicalDpi 并补偿 mode/scale；外屏独立。状态栏也以 Qt 同一身份匹配内屏，不使用输出顺序。默认/用户配置/宿主实际模式与刷新策略的职责、失败补偿及验收边界见 [85 篇](../85-phone-display-size-policy.md)。


2026-09-28 显示策略第二轮：APK 2.10 在原子 android-display.json 中新增成对 densityDpi/densityWidthPixels/densityHeightPixels；Android 14+ 来自同一最大窗口指标快照。共享策略只读这些字段，KWin 与 KScreen 用同一算法，保存的用户偏好不随 Android density 改变。 具体接口、回退、测试及边界见 [85 篇](../85-phone-display-size-policy.md)。

2026-09-28 Miracast 通用化评估：Plasma UI → 平台桥/root 控制端 → Android WFD/P2P/厂商编码；KWin 外屏 → 宿主 CastDesktop Surface → 电视，音频继续共享 Android 后端。用户要求支持更多机型：公共层按能力探测并提供稳定状态/错误契约，Qualcomm XML、实际 codec 映射、SELinux 和具体副屏冲突放在 firmware/spec 适配器；无原生 WFD 时明确报告边界。这是后续设计，未宣称已实现；X70 只读证据、版本区分及验收见 [86 篇](../86-x70-miracast-assessment.md)。

2026-09-28 Miracast 实现：公共 WFD 控制增加显式能力/错误、主动启用与系统选择页；固件数据选择 UI 租约及旧设备例外。动态 codec 生成仍为公共检查，仅 changed 时挂载且不覆盖外来挂载。独立部署器与 host seed 共用载荷/摘要，保留账户、接收端和租约。X70 的窗口层级证据、运行恢复与限制见 [86 篇](../86-x70-miracast-assessment.md)。

2026-09-28 收尾补测：容器快捷开关已更新至 rungic-cast 0.331。直接关闭系统 WFD 后 watcher 恢复 UI 包/释放租约通过，但随后的两次重连超时，目标接收端最终报告 unavailable；此异常恢复边界尚未解决，详见 86 篇，不能将前三轮正常重连推广到此场景。锁屏测试由用户手动解锁，自动恢复未验收。

2026-09-29 历史层补齐：Android ClipboardManager ↔ PlatformBridge ↔ rungic-clipboard/wl-clipboard ↔ KWin 标准剪贴板 → Klipper 历史。Mobile taskpanel 与桌面 clipboard 托盘通过 KlipperInterface 持有同一 plasmashell 进程内单例；桥保持只同步当前文本、不过滤规则外扩、不单独存历史的职责。G100 rungic6 已部署，首装/多输出、焦点及手机弹窗的验收边界见 [剪贴板历史记录](clipboard-history.md)。

2026-09-29 后台剪贴板：`ClipboardDaemon`（APK 内代码，由 `system/android-clipboard` 经 Magisk 以 Shell UID 2000 启动）持有 Android framework ClipboardManager 与变化监听；生命周期由 Android 宿主 `rungic-plasma start/stop` 管理，独立于 Activity。Linux `rungic-clipboard` 直连抽象 Unix socket `com.rungic.clipboard.v1`，使用 `clipboard-get` / `clipboard-set` / `watch`，后者返回 epoch 与 versions.clipboard。服务只接受 UID 0、1000 和当前 Rungic APK UID；客户端核验服务 UID 2000。当前 LXC 共享 Android 网络命名空间，未使用网络端口。`platform.sock` 的旧剪贴板操作继续转发，旧 HostEvents 的单/多主题 watch 由独立后端事件驱动。历史仍只在 Klipper，桥不存正文日志/历史。来源、升级配套与边界见 [后台剪贴板](clipboard-background.md)。

## 2026-09-29：SIM 电话 Agent 候选（双向验收未完成）

见[研究、部署和 10000 实验](cellular-call-agent.md)。G100 已安装 APK 2.18 / Agent 0.365 候选：共享 Realtime 对话 → `cellular_call` / `cellular_audio` → root CallDaemon / non-UI InCallService → Android Telecom 与系统通话 PCM → SIM。已验证真实拨号/接通/挂断、客服下行转写、Linux 微缩通话条和文字指示。客服仍表示未听清，远端上行、物理麦克风隔离、接管与故障恢复未通过完整验收，不能作为跨机型可用能力。私下语音指令和独立旁听开关未开放。

后续执行环境限制 ADB 和远程构建，新状态修正、音频诊断与计时排版仅在工作树中，尚未部署。最新实机证据与各阶段边界以链接记录为准；底座仍为 20260929.2。

2026-09-29 后续本地接口（未部署）：`CallCapabilities` / `--call-capabilities` 返回当前前提，明确接口可用不等于端到端通过；`StartCall` 显式接受 `backend=cellular + number` 或 `backend=app + app`，既有显式 app 调用兼容。选择依据用户要求，能力检测不切换通道。共享事件以 `callId` / `conversation` 关联原卡片与存储，`State.callInfo` 提供恢复信息；卡片 `CallCommand` JSON 带 callId，拒绝跨通话误操作。25 项离线回归通过，实际设备契约仍须部署后验收，细节见上述研究文档。


2026-09-29 最新增量部署：G100 / W1VT36H.1-51-8 已安装 **APK 2.20 / Agent 0.367**（底座仍为 20260929.2）。通用通道选择、原对话卡片、转写和技能已实机部署；通过共享 CallDaemon 的 VOICE_COMMUNICATION + 运行时显式 TELEPHONY 输出，让 Android AudioPolicy 选择通话上行。10000 第四次 Realtime 实验已获得远端识别“湖南电信”并进入业务菜单的证据；接管释放 Agent 音频、恢复静音且保留原电话，随后挂断回到空闲。前述 2.18 / 0.365 与“未部署”是历史阶段；最新路由研究、版本哈希、日志、回退和边界见[通话实验记录](cellular-call-agent.md)。不写死设备端口、不修改 HAL/SELinux。物理麦克风隔离专项、故障恢复、真人/其他设备和完整自主决策仍未验收；私下语音及独立旁听保持关闭，SSH 自动开启保持不变。


2026-09-29 G100 / UGREEN 投屏：已将厂商 UI 冲突从机型/固件名单重构为运行时窗口规则。确认同一 WFD 外屏中 Linux 窗口上方的已知 UI、核验所属包/UID/类型后才申请恢复租约；状态独立报告 ui_policy。修正工厂预装包未带 SYSTEM 标志的漏判后，android-native 默认路径下自动识别两类 Moto 窗口、断开恢复和重新连接已实测；初始可能短暂露出 Moto。当前外屏布局空隙也通过标准 KScreen 接口修正并在重连后保留；GUI 缩放操作、声音/输入/其他机型验收另做。证据、首个候选失败、最终 jar 和剩余边界见 [G100 UGREEN 记录](g100-ugreen-miracast.md)。

2026-09-29 Miracast 视频模式：分辨率/帧率选择只放在 APK 投屏胶囊的展开面板，底层共用 root 模式控制；选项来自真实 RTSP 接收端回复、硬件编码与已安装接口的交集，视频帧率不再拿 Android 显示刷新率代替。连接预算 120 秒，支持取消；同尺寸帧率在线切换、尺寸变化重新协商并核对外屏大小。G100/UGREEN 的720p30/60和1080p30/60均通过模式读回与KScreen尺寸核验；1440/1600p声明存在但当前后端未能应用，不作为可用模式宣传。实际协议、适配边界、版本、验证与恢复见 [视频模式记录](miracast-video-modes.md)。

2026-09-29 模式列表修正：声明兼容的候选不再直接作为可用选项。胶囊只显示当前一致的模式和在相同发送端/接收端能力下完成准确切换检查的模式；失败项排除、软件或能力变化使旧记录失效，无未验证/试用入口。此前将编码器/API参数支持等同投屏实际输出，导致1920×1200等无效项仍可选，已修正。见 [视频模式记录](miracast-video-modes.md)。

2026-09-29 胶囊交互统一：APK 2.22/70 用胶囊内的共享 BottomSheet 替换模式选择的 Android AlertDialog，设备选择复用同一容器。选中后点击应用才切换，取消/返回/遮罩/下拉回到胶囊；返回优先关闭浮层。G100/UGREEN 实测交互及1080p30→60应用通过，模式过滤与 root 后端保持不变。证据、实现边界与回退 APK 见 [视频模式记录](miracast-video-modes.md)。


## 2026-09-30：主动建议与原生桌面小组件

新增 C++ `rungic-suggestions`，通过会话 D-Bus `com.rungic.Suggestions` 共享账本。Agent 建议页与原生 Plasma Applet `com.rungic.suggestions` 读取同一记录；小组件位于 Folio 上方网格，独立卡片之间及组件下方留出壁纸空间，保留原底部固定图标、原应用抽屉和桌面编辑。此前替换整个主屏的 0015 补丁已撤回。升级只在没有收藏配置时补默认图标，保留已有布局与用户删除组件的选择。

采集链：dpkg/存储/systemd/既有 coredump 摘要 → root 定时采集 JSON → 用户服务去重/调度/持久化 → QML 卡片 → 用户委托 VoiceAgent 原对话。根采集输出 `/var/lib/rungic-suggestions/observations.json`，用户账本 `~/.local/share/rungic-suggestions/state.json`；60 秒更新，过期数据不触发预约提醒。知识库 `/usr/share/rungic/compatibility/` 随包安装，严格匹配版本。D-Bus `List/Get/Act/Update/Feedback/Knowledge/Presented/SetVisible/Refresh` 与 `Changed`。schema 2 分开保存问题观测、任务与提醒状态；问题不再出现不会覆盖运行任务或丢弃结果。VoiceAgent 的 `InvestigateSuggestion/ApplySuggestion/StopSuggestion` 同时接收事项 ID 与任务 ID，`SuggestionTask` 从当前任务或已保存的对话终态恢复。应用须有明确 ready 方案，确认绑定方案和证据修订，Agent 使用持久化的不可变批准快照。`Presented` 按卡片与进展版本记录视口停留/详情打开，不能用整个窗口可见代替；任务结果须明确归档。未接入可靠应用实例追踪前，移除“应用关闭后提醒”，保留定时和待处理选择。上游材料保存在本机，上游合入不等于本机解决。

USB G100 已部署 `20260930.12`：suggestions `0.452`、voice-agent `0.453`、design `0.393`、Plasma Mobile `6.6.5-0ubuntu0.1+rungic8`。`.11` 的 8 项 smoke 首遍通过、无 flaky，camera 按范围跳过；`.12` 仅更新账户归属桥接并定向复验真实用量。既有 313 个翻译文件 integrity drift 仍在，release 版本 mismatch 为空。此前独立卡片实机点按分别进入对应详情，后台同一 Agent 实例可被激活，标题数量入口打开列表，预约通知可从计算器前台跳入对应详情。隔离测试账本的 14 项列表在组件内上滑实际移动、未触发抽屉；壁纸区域上滑仍打开原抽屉。临时数据已清除，真实账本保留。重构后实机滚动另验：12 张测试卡仅实际进入视口并停留的 1 张获得展示回执。方案变更弹窗明确提示并禁用旧方案应用，服务拒绝旧修订，物理取消有效。迟到回执不再创建空记录。C++ 24 项、Python 33 项与 40 事项 / 39 组、五种 QML 预览通过。

开发环境按授权保持 GPT-6 Luna + API Key，用户服务配置持久化，Codex device-code 入口保留。真实 Luna 调查期间重启建议服务，原任务和对话仍能恢复并回写完成结果；缺少历史崩溃回溯，根因仍未确认，方案保持待调查；未应用系统修复、逐应用证明硬件加速或向上游发 PR。Android 提醒及远端 PR 自动同步尚未覆盖。来源、迁移边界、测试中的失败与修正、回退快照和实机证据见 [主动建议记录](proactive-system-care.md)。

2026-09-30 卡片与 Agent 组件更新：同包同版本的崩溃按展示组堆叠，底层签名、任务、提醒和原对话保持独立，桌面正面显示 `conclusion/nextStep/confidence` 或原报告首段；组内隐藏记录不获得展示回执。真实两条历史调查补写摘要，仍明确根因未确认，未实施修复。新增独立 Plasma Applet `com.rungic.agent`，空闲网格放置像素 Agent、用量与账户状态，两个入口分别跳转对话与用量页。底部收藏和原布局保留。

用量链：固定 Codex 0.156.1 的只读账户/额度 RPC 与 token 事件 → 现有 VoiceAgent 桥 → C++ `Care::Usage` 账户分区、累计高水位与持久化 → D-Bus `AgentUsage/UsageChanged` → 独立 widget 与 APP 专页。API Key 只展示本机收到的实际 Codex token，订阅额度和重置由服务端提供；不纳入 Realtime、不等同账单，不把重置倒计时结束当作已用额度清零。真实 Luna 请求、累计保存和服务重启不重复计数通过；订阅展示使用固定协议与隔离替身验收，未切换实机登录。`.11` 完成 8 项 smoke 首遍通过；`.12` 只补桥接账户归属边界并重启 Agent，按范围复验真实用量。完整证据与各阶段限制见 [主动建议记录](proactive-system-care.md)。

2026-09-30 用量接口通用化（未部署）：Agent → 描述文件 `rungic/agent-usage/providers/<id>.json`（D-Bus 方法或读取命令）→ C++ `Care::Usage` 按提供方与账户分区 → `AgentUsage` schema 2（`providers[]` 与 `primary`）/`RecordTokens`/`ProviderChanged`/`UsageChanged` → widget 与用量页。Codex 通过 VoiceAgent `Usage` 与 `RecordTokens` 接入；Claude Code 读取器只从会话记录计 token、从文档化 statusline 取额度。新增 Agent 只需安装描述文件与适配器，服务和组件不改代码。离线与构建机集成测试通过，实机未验收；详见 [95 篇](95-agent-usage-providers.md)。

2026-09-30 堆叠手势更新：USB G100 `20260930.13` / suggestions **0.461**，其他组件保持 `.12` 基线。桌面堆叠内上滑下一张、下滑上一张，提供页码、跟手动画、短拖动/首尾回弹；标题与间隙承担外层组列表滚动，手势归属在按下时固定。当前成员按 ID 保持，刷新/动画期间不跳卡，移除当前项回到剩余第一项。点正面进入当前记录，顶部数量进入组列表；只对停稳可见的成员回写展示回执。部署后明确重新加载建议服务、桌面和助理 UI。24 项 C++、40 项事项的指针/QML 集成，以及真实两张、隔离三张/多组、刷新/移除、正确跳转与回执、快速连续滑动、原生长按编辑和壁纸抽屉实测通过。临时数据与环境覆盖已清除，原结果/计划/提醒保留；完整证据和默认 smoke 范围说明见 [主动建议记录](proactive-system-care.md)。

2026-09-30 简报层更新（离线实现，未部署）：账本 → C++ `Care::Briefing`（实质变化触发、防抖/间隔/每日上限、严格校验、确定性回退）→ VoiceAgent `Curate`（ephemeral 只读 Codex 回合，`outputSchema` 严格 JSON）→ D-Bus `Briefing/Curate/OpenCard/DismissCard/CardPresented` → QML `SuggestionsClient.cards/briefing`。卡片通知替代逐记录通知；打开卡片经 VoiceAgent `OpenBriefingCard` 开新对话。设计、协议核验与隐私边界见 [96 篇](96-agent-curated-briefing.md)。

## 2026-10-02：共享 communication profile 与 Agent 电话模式

标准链路为 `Agent/GStreamer 或 pacat/parecord → PulseAudio android_communication / android_communication_microphone → C++ rungic-communication-audio → 私有 capture.sock → Android VOICE_COMMUNICATION AudioTrack/AudioRecord`。控制通过用户私有 `rungic-communication.sock`，单 owner、session/epoch、flush 和 playback-head cursor；物理采集使用 Android AEC/NS，缺硬件 AEC 时复用共享 WebRTC DSP。普通 android_microphone 的消费者复用同一采集，media-bridge 根据 communication.active 避免重复开 AudioRecord。静音关闭物理采集并保留播放，Plasma 隐藏关闭 capture sockets。

G100 ZY32M9MRVP / Android 16 / APK 2.29 开发版通过标准 PA 客户端检查：静音播放、连续 PCM、恢复与关闭、第二个 owner 拒绝、epoch 更新；flush 控制确认约 108–109 ms。实机采集测试 PCM 全零，不能据此证明真人识别或回声消除效果；声学延迟、软件 AEC 回退、其他机型和蓝牙仍待验。首次原生 Agent 再取消静音暴露 DSP 两个 appsink 等待彼此 preroll，修正为独立 async=false、sync=false 并先协商 echo reference；最终原生客户端已通过无播放时恢复采集、真实 Realtime 回应播放、停止播报 epoch 更新、静音释放麦克风和挂断后清理。真人声音、回声效果和声学 P95 仍未验收。完整包版本、独立云端/本地/实机证据和回退见 [101 篇](../101-full-duplex-phone-mode.md)。

任务执行走常驻 C++ 协调器 → 现有 VoiceAgent 的认证 Codex app-server → 按任务绑定的 thread/turn。桌面 MCP 的任务 worker 有独立进程组与租约，不结束用户 GUI；后台简报禁用 MCP。真实挂断后任务继续、明确取消等待实际结束、Android Settings 遮挡后停话音而保留任务并要求显式恢复均通过。部署保留 SSH 自动启用；用户要求的 10% 音量由现有 phone sink 继承到新 communication sink。

## 2026-10-03：系统监视器传感器（GPU、磁盘）

链路：系统监视器 / System Monitor 小组件 → 会话 D-Bus `org.kde.ksystemstats1`（手机会话与工作区 0 各有一个按需激活的 ksystemstats）→ GPU 插件 `LinuxKgslGpu` → `/sys/class/kgsl/kgsl-3d0/{gpu_model,gpu_clock_stats,gpubusy,temp,devfreq/*}` → KGSL → Adreno 710；磁盘插件（Solid 无卷时）→ `/proc/self/mountinfo` + `statvfs` + `/proc/diskstats` → `rungic-root`（userdata 上 loop 的 ext4 镜像）与 `userdata`（f2fs）。只在传感器被订阅时读 sysfs；不读 `devfreq/gpu_load`（读后重置调频器统计）。没有按进程的 GPU 使用率（KGSL 无 DRM fdinfo），不提供显存与功耗。实现与验收见 [104 篇](../104-system-monitor-gpu-disks.md)。

## 2026-10-03：授权框跟随请求方的桌面（polkit 代理界面）

链路：0 号（独立桌面）里的应用或 KAuth 辅助程序 → 系统总线 polkitd（只认会话 c126 里唯一注册的代理）→ 手机会话的 polkit-kde-agent（路由，`packages/polkit-kde-agent-1`）→ 读请求进程 `polkit.subject-pid` 的 `DBUS_SESSION_BUS_ADDRESS` → 0 号私有总线上按需激活的 `polkit-kde-authentication-agent-1 --delegate` → 0 号 KWin 里桌面外观的对话框 → polkit-agent-helper-1 凭 cookie 校验密码。代理界面的 `Prompting` 经 `rungic-workspace-stream` 的 `prompting 1|0` 传给浮窗，用来显示提示条。手机会话和无头助理屏工作区的请求不走转发，仍弹在手机上。全屏改为标准全屏窗口后，手机上的授权框、键盘、通知、OSD 都在全屏之上。研究、实现与验收见 [97 篇 §21](97-headless-agent-work.md)。

2026-10-03，开发 G100 的 Codex 默认桌面执行与 API 备选见 [106](../106-codex-desktop-operation.md)。部署工作区脚本须配套当前 KWin 的 virtual-render-device 补丁；旧 +rungic8 会退回 QPainter，截图被取消。主会话重启不重启独立工作区，需在可安全重启时让它加载新版。


## 独立 Linux 服务与 root 硬件后台（2026-10-05）

候选实现把网络、蓝牙、SIM 和短信从显示 APK 移到独立 root DeviceDaemon，Linux 直连硬件端点，Agent 用户服务也不再随手机图形会话停止。实现、发行版归属、Android 16 探针及未验范围见 [113 篇](../113-independent-linux-services.md)。短信库和合成结果回调已核对，未把旧 APK 的无线电测试当作新后台验收。
