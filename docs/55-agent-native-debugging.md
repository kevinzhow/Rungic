# Agent原生调试：统一采集与可调用接口的可行性

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-23。用户要求开发方式 agent native：设备上的信息和错误集中采集，交给 Agent 分析；调试接口可由 Agent 直接调用，不依赖在 GUI 中点击。本轮只做可行性分析，没有部署服务，也没有改变设备配置。下面“实测”项均通过 K8-Plus 经无线 adb 以只读方式检查，“候选”项尚未验证。

## 结论

可行，而且大部分能力已经存在，主要问题是分散、不能持久保存，并且只能通过人工 shell 或 GUI 使用。第一阶段不需要写新的常驻采集守护进程：把现有标准机制（journald、logcat、tombstone、perfetto/ftrace、D-Bus、AT-SPI、platform.sock）接到一个主机侧 Agent 接口即可。真正需要新开发的是四项：Linux 容器崩溃现场、容器进入 Android 追踪系统的通道、面向 Agent 的统一工具层，以及工具层的安全分级。

一个关键的实测事实降低了难度：容器与 Android 共用同一内核，因此单调时钟相同。同一时刻 logcat `-v monotonic` 为19319.53s，容器 journald `__MONOTONIC_TIMESTAMP` 为19320.14s（两次命令先后执行）。设备端各来源可以直接合并到一条时间线，不需要51篇那样的主机↔设备校准；只有主机侧发起的动作仍需要校准。

## 现状实测

| 来源 | 实测状态 | 对Agent的缺口 |
|---|---|---|
| 设备连接 | 本机adb位于`~/android-sdk`，设备为无线`10.77.0.16:44995` | `tools/device.py`、`rungic_plasma.py`、`profile_plasma_frames.py`硬编码`~/Android/Sdk`和USB序列号`ZY32MVJS25`，在K8上直接失败。设备选择属于本机配置，应放在`.work/`或环境变量中 |
| Android日志 | logcat可用，支持monotonic；Rust后端用`android_logger`，标签`WinlandNative`；Java有16处`Log.*` | 环形缓冲，不持久；本次dump中没有保留到`WinlandNative`行 |
| Android崩溃 | `/data/tombstones`有记录；crash缓冲区可读 | 没有和桌面事件关联，也没有汇总工具 |
| 内核 | Android root下可用dmesg；容器内`klogctl: Permission denied`；tracefs有`adreno_cmdbatch_{queued,ready,done,fault,recovery}`；`/system/bin/perfetto`、`simpleperf`存在，`persist.traced.enable=1` | KGSL的GPU提交/完成/fault事件已经可追踪，但目前没有使用 |
| 容器系统日志 | systemd 259运行正常，journald占用13.9M | 持续出现systemd-localed/timedated的bpf-firewall挂载失败，属于已知类别的噪声，应在分析层标注，不能每次重新诊断 |
| 桌面会话日志 | KDE用户单元与应用单元已进入系统journal（`_SYSTEMD_USER_UNIT`），只是没有分离的user journal文件；会话入口服务`StandardOutput=append:/var/log/plasma/session.log`（20K），`moto-plasma log`只输出最后70行 | 会话入口输出没有时间戳和优先级（P1已改入journal） |
| 容器崩溃 | 全局`core_pattern=/data/app_dump/%e_%p_%t.core.gz`属于Android；容器内`ulimit -c`为0，且没有`/data/app_dump`；没有systemd-coredump；有gdb、没有perf | **KWin、plasmashell及Linux应用崩溃目前不会留下core**，这是最大的空白 |
| KWin | D-Bus提供`supportInformation`、`/Scripting`（`loadScript`等）和`/FTrace`；`showDebugConsole`只有GUI | FTrace在`compositor.cpp`打点，但容器看不到`/sys/kernel/tracing/trace_marker`，开启后无法写入 |
| Plasma/应用 | `plasmashell.evaluateScript`、`org.kde.KWin.ScreenShot2`、`org.a11y.Bus`（AT-SPI）及门户均在会话总线上 | 自动化脚本现在用固定坐标`adb input tap`；51篇已记录布局变化会使测试轮次失效 |
| Android宿主 | `platform.sock`单行JSON协议（status、display-get、network-get、capture-info等），命令行`device-panel.py --request` | 这是现成的RPC样板；但原生后端的`getWaylandRuntimeStats`/`getBackendSnapshot`/`getLastNativeError`只由MainActivity写进logcat，没有可查询入口 |
| SELinux | KWin、plasmashell为`u:r:magisk:s0`，APK为`untrusted_app`，traced为`u:r:traced:s0`；`/dev/socket/traced_producer`权限为`srw-rw-rw-` | 容器进程能否连接traced取决于策略与挂载，尚未验证 |

## 建议架构

### 1. 采集：统一复用标准数据源，不另造日志格式

- **容器**：以journald为唯一汇集点。会话服务改为`StandardOutput=journal`（可在过渡期同时保留文件），用`SyslogIdentifier`/单元名区分组件。Qt日志类别通过`QT_LOGGING_RULES`或`qtlogging.ini`控制，一般要重启进程才生效；是否支持运行时切换尚未核验。
- **Android**：按UID/标签持续抓取logcat并滚动写入`/data/adb/moto-plasma/diag/`；同时收集tombstone和dmesg。只采本项目UID/标签以及crash/kernel缓冲，避免把整个手机的隐私日志一律上传。
- **容器崩溃**（候选，待验证）：Android拥有全局`core_pattern`，不能修改。已知对于普通文件路径的`core_pattern`，内核会在崩溃进程自身的文件系统根下创建core。因此可以在容器rootfs中建立`/data/app_dump`，并只给会话单元设置`LimitCORE`，这样既不影响Android，也能保存桌面进程的现场。另一种方案是KDE自带的KCrash/DrKonqi，但它只覆盖KDE程序。两者都必须实际触发一次可控崩溃来验收，还要设置大小和份数上限。
- **追踪**：由perfetto在Android侧统一录制sched、SurfaceFlinger帧时间线、`adreno_cmdbatch_*`以及用户态标记。因为共用内核，容器线程（KWin、Turnip提交线程）会与SurfaceFlinger、APK出现在同一份trace中。两条候选通道：
  1. 把`trace_marker`挂载进容器，或通过`KWIN_PERF_FTRACE_FILE`指向它，就能直接使用KWin现有的FTrace打点；
  2. 把`traced_producer`套接字挂入容器，再开启vendor Mesa已有的`-Dperfetto=true`（`src/freedreno/ds`含a6xx/a7xx的PPS数据源），取得GPU阶段和计数器。
  两者都要核验SELinux、Android16上的traced兼容性，以及Mesa perfetto SDK版本。

### 2. 调用：主机侧提供一个MCP服务作为唯一Agent入口

Claude Code原生支持MCP工具，Agent可以直接获得带参数模式的工具，不必每次拼adb/su/lxc-attach的多层引号（本次探测就遇到这个问题）。工具层（实现为`tools/rungic_agent.py`与`tools/rungic_agent_mcp.py`）只通过adb传输，设备端复用现有`moto-plasma`入口和`platform.sock`。建议的工具分组：

| 分组 | 示例 | 实现依据 |
|---|---|---|
| 状态 | `device_status`、`desktop_status`、`gpu_info`、`display_info` | lxc-info、systemctl、KWin supportInformation、platform.sock |
| 日志 | `logs_query(since, until, source, unit, priority, grep)`，返回统一的monotonic时间线 | journalctl `-o json`、logcat `-v monotonic`、dmesg |
| 崩溃 | `crashes_list`、`crash_get(id)`：包含backtrace（gdb批处理）、前后30秒日志 | tombstone、容器core |
| 追踪 | `trace_capture(duration, preset)`（例如`frame`、`gpu`、`input`）→ trace文件和摘要统计 | perfetto配置模板，trace_processor离线分析 |
| 桌面检查 | `windows_list`、`a11y_tree(app)`、`screenshot` | KWin Scripting/D-Bus、AT-SPI、ScreenShot2或`screencap` |
| 桌面操作 | `a11y_activate(app, role, name)`、`gesture(...)`、`launch(app)`、`dbus_call`（白名单） | AT-SPI按控件名称操作，取代坐标；坐标手势只用于性能测试 |
| 宿主 | `host_request(op)`；新增`native_stats`，把`getWaylandRuntimeStats`等接入platform.sock | 现有JSON协议，只追加只读op |
| 基准 | `bench_run(scenario, variant, rounds)` → 51篇格式的原始数据和统计 | 现有`compare_plasma_gpu.py`等，改为使用工具层 |
| 证据包 | `snapshot(label)`：一次调用把状态、日志窗口、崩溃、trace、截图写入`.work/diag/<时间>-<label>/`并生成清单 | 符合本仓库“原始证据+结论分离”的做法 |

每个工具的输出都应先给摘要，并附原始文件路径，避免大段日志直接塞满Agent上下文。证据包目录可以原样进入`benchmarks/`或文档引用。

### 3. 安全分级

- **只读**（默认允许）：状态、日志、崩溃、截图、a11y树、trace。
- **可恢复的变更**：重启会话、切换日志类别、临时KWin覆盖（例如51篇的Zink测试）。这类工具要自带恢复路径，并记录变更前状态。
- **禁止或需人工确认**：刷写、reboot到bootloader、修改全局SELinux/core_pattern/厂商配置。执行Magisk sqlite时，由工具层拒绝可能返回NULL的查询（遵守39篇和AGENTS.md的约定），不能只靠Agent记住规则。
- 设备日志可能含网络、账户、剪贴板等隐私信息；采集只写本地`.work/`，不上传外部服务。

## 分阶段实施与验收

| 阶段 | 内容 | 验收 | 估计 |
|---|---|---|---|
| P0 | 设备配置脱离硬编码（`.work/device.env`或`MOTO_ADB`/`MOTO_SERIAL`）；只读MCP：状态、日志合并查询、tombstone、KWin supportInformation、截图、证据包 | 两台机器上都能用同一套工具；人为制造一条日志后，能在合并时间线中按时间找到 | 小 |
| P1 | 会话日志进入journald；容器core现场；`native_stats`进入platform.sock | 对一个测试进程执行可控`SIGSEGV`，能得到core和backtrace；Android侧零影响 | 中 |
| P2 | perfetto统一trace：先接Android侧sched、SF和adreno事件，再接KWin FTrace标记，最后接Mesa perfetto | 一份trace中能同时看到KWin绘制→glFinish→AHB提交→SF呈现 | 中到大，SELinux和挂载有不确定性 |
| P3 | AT-SPI操作和基准工具迁移；按控件名称替代坐标 | 51篇场景在改变图标布局后仍能正确通过进程/界面检查 | 中 |

## 与KWin + Vulkan问题的关系

51篇只能测到SurfaceFlinger的呈现间隔和进程CPU时间，无法拆出一帧时间花在KWin绘制、`glFinish`等待、跨进程AHB交接还是宿主GLES再呈现上。要量化原生Vulkan的收益，前提就是这种分段数据。P2的统一trace（adreno cmdbatch的queued/done、KWin FTrace的paint区间、SF frametimeline）正好提供这些数据，因此建议先完成P0与P2的Android侧部分，再写独立Vulkan合成原型并测量。这样原型与现有GLES路径能按阶段逐项比较，不只是比较一个总帧间隔。

## P0/P1实施与验收（2026-09-23）

在K8-Plus经无线adb实施。P2/P3在同日完成，量化结论见[56篇](56-kwin-vulkan-quantification.md)。K8不能访问`192.168.5.45:6152`，主机侧MCP SDK直接从PyPI安装；手机侧访问该代理正常（archive.ubuntu.com 200），但经代理访问debuginfod.ubuntu.com超时。

**P0：设备入口与只读Agent工具**

- `tools/rungic_device.py`：按`MOTO_ADB`/`MOTO_SERIAL`/`MOTO_TRANSPORT`或`.work/device.env`定位adb；默认用`ro.serialno=ZY32MVJS25`在所有adb设备中查找，无线调试端口变化不需修改。脚本经stdin送到Android shell、Android root、容器root或桌面用户四个层级，避免多层引号；退出码原样返回。原7个硬编码`~/Android/Sdk`与序列号的工具改为共用此入口。
- `tools/rungic_touch.py`（2026-10-01）：adb `input` 做不了的多点触控。以 root 向触摸屏的 evdev 节点写多点触控协议 B 的事件，一个 shell 里逐帧写入，约 12 ms 一帧，Android 和其后的 Plasma、Qt 都当作真实手指；运行前检查屏幕上没有真实手指。内置点击、拖动、双指张开或捏小，以及“张开后一根手指先抬起”（docs/65）。会在用户屏幕上实际操作。
- 注意：未给adb子进程关闭stdin时，`adb shell`会吞掉调用者的stdin，使MCP stdio握手无响应。已统一使用`stdin=DEVNULL`。
- `tools/rungic_agent.py`（库+命令行）与`tools/rungic_agent_mcp.py`（官方MCP Python SDK 2.2，uv内联依赖），在工作区`.mcp.json`注册为`moto`。工具：`device_status`、`logs`、`session_log`、`crashes`、`crash_detail`、`kwin_info`、`host_request`、`screenshot`、`snapshot`，只读工具带`read_only_hint`。
- 日志以墙钟合并：logcat `-v epoch`、journald `__REALTIME_TIMESTAMP`、`dmesg -r`（保留内核自身级别；用同一脚本采样的CLOCK_REALTIME与`/proc/timer_list`单调时间换算）。`scope=plasma`只取桌面APK UID、本项目标签、crash缓冲及GPU/内存/SELinux相关内核行。已知噪声（bpf-firewall、runuser会话、binder释放、Moto剪贴板审计）只计数不显示，并注明原因。
- 截屏：300ms RTT链路上`exec-out screencap`传1.5MB用36秒，改为设备侧写文件再`adb pull`约5秒；MCP返回540px JPEG预览，原图留在`.work/diag`。
- 验收：Android（`log -t MotoPlasma`）、容器（`logger`）、内核（`/dev/kmsg`）各写一个标记，`logs --grep agent-marker`按实际先后返回全部3个来源，间隔约0.9–1秒（各为一次adb往返）。MCP客户端经stdio列出9个工具，逐一调用成功；`snapshot`在34秒内生成完整证据包，无采集错误。首次查询即发现一条真实错误（本次调查早先误用不存在的用户`moto`调用`systemctl --user -M`）以及`plasma-settings`在20:00:47的SIGSEGV。

**P1：崩溃现场、会话journal、宿主统计**

- 核实：全局`core_pattern=/data/app_dump/%e_%p_%t.core.gz`由内核在崩溃进程自身根目录下解析。在容器内建`/data/app_dump`后，用户进程崩溃的core写在容器里，Android侧该目录不存在、不受影响。文件虽名为`.gz`，实为未压缩core。KWin源码无`PR_SET_DUMPABLE`，KWin进程UID全为1000、无额外能力，`suid_dumpable=0`不影响。
- `system/diagnostics/`：`60-moto-core.conf`给system与user管理器设`DefaultLimitCORE=2G:infinity`；tmpfiles建立spool；`moto-coredump.path`（DirectoryNotEmpty）触发`moto-coredump-collect`：等待core写完→gdb取头信息与全部线程backtrace→Python 3.14自带zstd压缩→写`info.json`→在journal记一条err级`coredump:`。保留最近8个core（总计≤4GiB）和200份报告；无法处理的文件移入`unprocessed`，确保path单元不会循环触发。部署用`tools/deploy_plasma_diagnostics.py`，只在显式`--restart-session`时重启桌面。
- 会话入口服务改为`StandardOutput=journal`、`SyslogIdentifier=moto-plasma-session`；Android侧`moto-plasma log`在容器运行时读journal。
- 验收：`systemd-run --user sleep`与`kalk`（KDE应用，经KCrash）分别收到SIGSEGV，systemd记录`Result: core-dump`，各生成报告与journal条目；kalk的core为268MB，zstd后7.4MB，收集用4.7秒。`crashes`/`crash_detail`已读取报告和`/var/crash`（apport的Python异常报告）。KWin/plasmashell重启后的core上限为2GiB。
- 符号：Ubuntu库没有随系统安装调试符号，帧多为`??`，但Qt/GLib导出符号可读。debuginfod需`-iex 'set debuginfod enabled on'`，直连下载很慢，经代理超时，因此没有放进自动收集流程；按需符号化留待后续。
- 安装中发现：容器根目录`/`、`/usr`、`/usr/share`、`/usr/share/locale`为UID1000（桌面用户）所有且模式775，另有253个zh_CN翻译文件属UID1000。这使桌面用户可以替换`/usr`下的条目，等同取得容器root，也使systemd-tmpfiles以“unsafe path transition”拒绝执行。来源是`restore-chinese-translations.py`生成的暂存树带着构建者UID和目录模式解包到`/`。已改回root所有、755，脚本改为直接输出只含文件、属主为root的归档。
- APK1.9/versionCode10：`platform.sock`增加只读`native-stats`（Wayland宿主窗口/焦点/注入输入计数、已呈现帧、最后原生错误），仅接受UID0/1000。K8没有Rust工具链，原生库经`tools/pull_installed_native_libs.py`从已安装1.8 APK原样取出（`.work`中附SHA256SUMS），`build-apk.sh`改为自动查找SDK并按manifest版本命名。安装后KWin自动重连（21:48:14），截屏确认桌面正常；左上角有一个指针图标覆盖时钟，尚未确认是否本次引入。

## P2：统一追踪

- 容器内挂载tracefs：systemd自带`sys-kernel-tracing.mount`因`ConditionVirtualization=!lxc`在容器中跳过；`system/diagnostics/sys-kernel-tracing.conf`只清空该条件，复用标准单元，开机即挂载。容器进程为真实root（无user namespace），`trace_marker`本身在Android上即全体可写，没有扩大权限面。
- `tools/rungic_trace.py`：Android perfetto v49一次录制sched、cpu/gpu频率、dma_fence、atrace（gfx/view/input）、SurfaceFlinger帧时间线与全体进程名；容器进程以全局PID出现（例如kwin_wayland、plasmashell），与SurfaceFlinger、APK在同一时间线。录制期间经D-Bus打开KWin `/FTrace`，结束恢复原状态。
- 本机为user版，perfetto只接受其白名单ftrace事件，KGSL事件被静默忽略。改为同时建立tracefs实例`moto_gpu`（`trace_clock=boot`与perfetto一致，`record-tgid`），记录`adreno_cmdbatch_queued/submitted/retired`、上下文切换与功率级别。`retired`带GPU常开计数器的start/retire（19.2MHz；实测29295 ticks=1.53ms，与事件时间差1.62ms一致），可得每次提交的GPU执行时间；`queued`的提交线程tgid给出上下文所属进程。
- `tools/rungic_trace_report.py`（trace_processor）：APK `queueBuffer`（主机提交）、SurfaceFlinger显示帧、KWin标记区间、各进程CPU、各进程GPU时间与GPU忙碌率、GPU频率分布。本机SurfaceView为原生EGL，SurfaceFlinger帧时间线没有该层的逐帧条目，因此以`queueBuffer`为主机提交时间。
- 发现KWin上游缺陷：`FTraceLogger`以普通`QIODevice::WriteOnly`打开`trace_marker`，`QTextStream`的`endl`只刷到QFile内部缓冲，标记成批写入内核（一次print事件含数十行），时间戳失效；2026-09-23上游master仍如此。vendor改为`WriteOnly | Unbuffered`，并在Android输出路径的`glFinish`与导入/提交处增加`GpuWait`、`Import`区间（moto6）。
- moto7：moto6的无缓冲QFile在经D-Bus关闭后再开启时，每次写入都失败（journal大量`QFile::at: Cannot set file position 0`；`trace_marker`不支持seek，QIODevice在重新打开后仍试图定位）。首次开启正常，所以只有会话启动后的第一次采集有KWin标记。改为普通fd，每个标记格式化后一次`write()`；写入失败（例如内核tracing关闭时返回EBADF）只丢弃该标记。上游master同样使用QFile。采集工具也改为在perfetto开始录制后才开启KWin标记。
- 注意：容器有独立PID命名空间。perfetto/KGSL记录的是全局PID（例如KWin为22979），容器内`pgrep`看到的是命名空间PID（6606），两者不同不代表进程重启。
- moto6验证：标记不再成批（`batched_print_events=0`）。一份trace中同时得到KWin Paint→GpuWait（`glFinish`）→Import、宿主`queueBuffer`与SurfaceFlinger呈现，满足P2验收。编译负载下的首轮数据：Paint p50 8.5ms，其中GpuWait p50 5.6ms；而KWin自身每次提交的GPU执行时间均值仅1.26ms，说明`glFinish`大部分时间在等GPU上其他进程（plasmashell单次提交约7ms）的工作。正式数值见56篇。
- 初步数据（旧moto5、4次滑动、8秒）：GPU忙碌10.8%；plasmashell单次提交GPU时间p50 7.4ms，KWin均值1.1ms，宿主每帧GLES合成约1.55ms。宿主8秒内实际提交189个缓冲（相邻间隔p50 13.6ms，低于120Hz），`queueBuffer`本身p50 1.4ms、p95 5.3ms。注意：Android把`Surface::queueBuffer`与`BufferQueueProducer::queueBuffer`都记为嵌套的`queueBuffer`，直接计数会翻倍；此前“每帧提交两次”的判断因此不成立，报告已改为只计外层。

## P3：按控件操作

- 选型：AT-SPI是Linux桌面标准无障碍接口，Qt/KDE原生支持；KDE自己的GUI测试方案selenium-webdriver-at-spi也基于它。本机at-spi2-core与GObject绑定已安装，未另装pyatspi/dogtail。`org.a11y.Status IsEnabled`默认关闭；经D-Bus置为true后，运行中的Qt进程约2秒内动态注册（plasmashell、kwin、plasma-keyboard等9个），不需重启。无障碍在每个应用都有开销，工具按需开启，性能测试前关闭并记录状态。
- 设备端`moto-a11y`（随diagnostics安装）：apps、tree、find、act（AT-SPI动作）、text、windows。Wayland客户端不知道全局坐标，`windows`用一次性KWin脚本（D-Bus `/Scripting`）取窗口全局逻辑几何，经journal读回。主机侧`ui_find/ui_press/ui_tap/ui_set_text`；无动作元素（启动器图标、Kalk自绘键盘）按“窗口原点+元素中心”×（物理/逻辑宽度）经Android input点击。
- 验收：打开抽屉后按名称找到Calculator并点击，kalk启动并注册；在Kalk中按名称点C、7、+、8、=，截屏显示15。未使用任何固定坐标。
- 发现Plasma Mobile导航栏三个按钮无无障碍名称（辅助技术与Agent都无法区分）。上游master已给`NavigationPanelAction`增加`accessibleText`并绑定`Accessible.name`；已原样回移到vendor 6.6.5。mobileshell的qmldir使用`prefer :/…`，QML编进插件资源，因此在手机上以CMake单独构建`mobileshellplugin`与`org.kde.plasma.mobile.taskpanel`（`tools/build_on_device.py plasma-mobile targets`），经dpkg-divert替换发行版文件，原件保留为`.distrib`。重启会话后三个按钮名称为Task switcher、Home、Close app，均有Press动作。
- 构建环境：Ubuntu以打包补丁放宽Plasma内部依赖版本，KPipeWire为6.6.4而上游6.6.5要求同版本；未改vendor源码，改用`desktop/build-shims/KPipeWire`版本垫片（仅接受6.6.x）。容器新增只含源码索引的`/etc/apt/sources.list.d/moto-build-src.sources`，以`apt-get build-dep plasma-mobile`安装构建依赖。

## P4：交付、完整性与崩溃链（2026-09-26）

实施细节与验收见[61篇](61-delivery-diagnostics-plan.md)“实施记录”。与本篇相关的变化：

- 诊断组件不再由`tools/deploy_plasma_diagnostics.py`安装，改为`moto-plasma-diagnostics`包（`/usr/bin`、`/usr/libexec`、`/usr/lib/systemd`），随发布部署。
- 新MCP工具：`integrity`（rootfs漂移：包文件、本项目divert、无主文件、systemd mask、本机配置清单、崩溃链前提）、`crash_groups`（按签名归并，含`new_in_release`）、`crash_symbolize`（按build-id装`-dbgsym`并重做回溯）。
- 崩溃报告增加签名、build-id、所属包与发布；同时写入systemd-coredump格式，`coredumpctl list/info/debug`可直接使用。上面“按需符号化”一项由`moto-crash-symbols`完成：Ubuntu库用ddebs，本项目包用发布仓库中的`-dbgsym`（构建改为`-g1`并拆出调试信息）。
- AT-SPI补充：会话刚重启时，抽屉搜索结果不进入AT-SPI树（屏幕上已显示）；快捷设置折叠时，未显示的磁贴仍报告为showing；plasma-keyboard的按键以label暴露，但面板坐标与屏幕有偏移。验收脚本分别用OCR读回、完全展开面板后再点、只检查键盘出现来处理。

## 仍未完成或未验证

- 容器内连接Android `traced_producer`、Mesa `-Dperfetto=true`的GPU阶段数据源：未实施。目前GPU时间来自KGSL tracepoint，已满足56篇量化；逐渲染阶段的GPU计数需要时再做。
- AT-SPI覆盖：plasmashell、Kalk已验证；启动器图标无动作、Kirigami搜索框无EditableText接口，工具已分别以坐标点击和“聚焦+Android输入”兜底。其他应用逐个遇到再记录。
- Qt日志类别的运行时切换未验证。
- `.mcp.json`为项目级MCP配置，Claude Code首次加载时需要用户批准；K8已实际用MCP客户端调用验证，本机（另一台开发机）尚未在其环境中运行。
- 构建：`dpkg-genchanges`因缺少`.dsc`报错，但各`.deb`已生成；plasma-mobile两处插件以dpkg-divert替换，发行版升级plasma-mobile时需重新构建并核对。

## 抽屉搜索的语言无关定位

开发启动工具和验收输入检查共用 ui_launch_check.drawer_search_fields：查询 Plasma shell 的 text 节点，要求 showing、visible、enabled、editable 状态及屏幕内非零范围；不按翻译名称匹配。唯一候选才操作，多候选报歧义。若抽屉已打开，不再滑动；打开后要求节点路径和范围连续两次一致，之后再聚焦、输入并读屏验证。

USB G100 的只读 AT-SPI 记录显示中文字段名为搜索，role=text，状态 showing/visible/enabled/editable，范围 [26,56,301,43]。开发回归重放同一语义、分别使用 Search／搜索名称，覆盖打开、聚焦、文字注入和 OCR 结果边界，以及隐藏、不可用和多候选拒绝。这里 OCR 和设备命令为替身，中文及英文手机真实输入流程仍须下一轮验收，不能把工具回归当成产品输入通过。
