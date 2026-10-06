# 97. 锁屏、息屏下的 Agent 工作（无头模式）调研

2026-10-02。用户要求：Agent（单个任务和团队）必须能在手机锁屏、息屏时继续工作。用户可能远程发起任务，只通过 RDP 等方式查看，不能强依赖 Android 解锁或 Rungic APK 在前台。远程观看用自有项目 RemoteSurface（`~/github/RemoteSurface`），不用 KRdp。

**标注**：
- 【实测】：本机实测；
- 【源码】：读过源码或反编译；
- 【文档】：其他文档或论坛的说法；
- 【推断】：未验证。

本轮调研没有改设备设置，只有两处例外：一是第 1 节的息屏实验；二是临时把 `com.rungic.plasma` 加进了 deviceidle 白名单，至今仍在，见第 6 节。

## 1. 现象（实测，G100 S / XT2537-4，中国版 ROM，Android 16）

触发点是 docs/research/91 §14 的第一次团队验收：art 成员报“受阻”。随后用 `KEYCODE_SLEEP` 息屏复现，手机平放：

| 时刻 | 屏幕 | deviceidle | APK 进程 | `platform.sock` |
|---|---|---|---|---|
| 亮屏 | Awake | ACTIVE | procState 2（顶层），未冻结 | 正常 |
| 息屏 20 s | Dozing | INACTIVE | procState 4（前台服务），未冻结 | 正常 |
| 息屏 90 s | Dozing | IDLE（深度） | procState 4，**`isFrozen=true`** | 无响应 |
| 唤醒后 | Awake | ACTIVE | procState 2，解冻 | 正常 |

- 用 root 执行 `dumpsys deviceidle whitelist +com.rungic.plasma` 后再测：90 s 和 180 s 时仍是冻结状态。
- 冻结期间容器照常运行：成员的 Codex 继续联网、发言。停下来的只是需要 APK 的部分。
- 本机属性：`ro.config.smart_freezer_enabled=true`、`ro.config.use_freezer=true`、`freezer_cutoff_adj=900`；待机分桶 10；RUN_ANY_IN_BACKGROUND 为默认允许。

## 2. 谁冻结了 APK：Motorola SmartFreezer（源码）

**来源**：
- AOSP `frameworks/base`：android-16.0.0_r1（99b01a65）和 r4（45034f06）；
- Motorola 固件公开 dump：`4mede/motorola_mumba_dump`，分支 `user-16-WWAAS36V.48-12-ST12.1-88187-release-keys`（b00538ad），用 jadx 1.5.6 反编译 `services.jar` 和 `framework.jar`。
- 注意：这个 dump 不是本机 `mumba_cn` 的固件，要用本机的 `/system/framework/services.jar` 复核（见第 7 节）。

**AOSP 本身不会冻结前台服务**：
- r1 和 r4 都只冻结 adj ≥ 900 的进程；
- 有前台服务的进程拿到 CPU_TIME 能力，不冻结；
- 冻结逻辑没有和 Doze 挂钩；
- `isFreezeExempt` 只来自 `freezer_exempt_inst_pkg` 加 `INSTALL_PACKAGES`，或 adj < 0 的持久化进程。

**冻结来自 Motorola**：
- `ro.config.smart_freezer_enabled` 是 Motorola 的属性（init.mmi.product.rc 注释为 `# moto_freezer`）。
- 实现在 `com.motorola.server.perf.proactive.freezer.SmartFreezer`、`UidStateController` 和 `com.android.server.am.MotoAMS`。
- 开关只在类初始化时读一次：先读 `persist.sys.smart_freezer_enabled`，没有再读 `ro.config.*`。
- SmartFreezer 开启后，AOSP 的 `CachedAppOptimizer.freezeAppAsyncInternalLSP()` 直接返回，所有冻结都由 Moto 执行（日志带 `reason = moto_freezer`）。`am freeze` 在这台机上不起作用。

**中国版 ROM 的判定规则**（`UidStateController.updateUidPerceptibleState()`，以 `ro.product.is_prc` 区分）：

| 条件 | 判定为“可感知”（不冻结）的条件 |
|---|---|
| 中国版，息屏或快速启动模式 | 仅 adj < 101 |
| 中国版，亮屏 | adj < 251 |
| 非中国版 | importance < 400 或 adj < 600 |

- Rungic 前台服务的 adj 是 200：息屏后被判为不可感知，可以冻结。国际版 ROM 上则不会冻结。
- 冻结延迟：中国版且 adj ≤ 250 时取 max(10 s, `smart_freezer_delay`)。失败后重试，重试间隔从 10 s 起翻倍，最长 600 s。
- Unix socket 的流量不会让被冻结的进程解冻（只有 binder 会），所以连接 `platform.sock` 先排队，排满后报 `EAGAIN`。

**SmartFreezer 放过一个 uid 的条件**：
- 进程 `isFreezeExempt`，或 adj < 0；
- 包名在 `EXEMPTED_APPS`，或在 `POWER_EXEMPTED_APPS` 里（= deviceidle 全量白名单 + Moto 中国版的 `getMotoFreezeExemptList()`）；
- uid 持有未被禁用的 PowerManager wakelock：推迟冻结；
- uid 拥有 VirtualDisplay：推迟冻结。

**刚才加白名单为什么无效**：`POWER_EXEMPTED_APPS` 只在开机完成时，以及“设置”或 `com.motorola.deviceguard` 离开前台时刷新。shell 执行 `dumpsys deviceidle whitelist +` 不会触发刷新。

## 3. 网络、CPU 与其他系统（源码）

- **Doze 下的网络**：netd 的 `bpf_owner_match()` 对 `uid % 100000 < 10000` 的系统 uid 直接放行。
  - 容器里的 root（0）和用户（1000，特权容器、无 idmap）不受 Doze 限制，与实测相符；
  - 容器里的 `nobody`（65534），以及 rootless Docker 里 uid ≥ 10000 的部分，会被 Doze 阻断。
- **CPU 挂起**：本项目没有任何地方持有 wakelock（power-policy 明确不建，APK 里没有 `PARTIAL_WAKE_LOCK`）。
  - root 写 `/sys/power/wake_lock` 可以阻止整机自动挂起（GKI android15-6.6 开了 `CONFIG_PM_WAKELOCKS`）；
  - 但它没有 uid 归属，挡不住 Moto 冻结 APK。
- **cgroup 冻结**：root 直接写 `cgroup.freeze=0`，或把进程移到别的 cgroup，会让 AMS 的状态和 binder 冻结状态不一致，不采用。
- **同类项目**：
  - Termux：前台服务，加 `termux-wake-lock`（`PARTIAL_WAKE_LOCK` 加 WifiLock），并申请电池优化豁免；
  - Termux:X11：X server 不在 app 进程里，用 `app_process` 单独起，app 只负责显示；
  - Linux Deploy：提供“息屏保持 CPU”选项（持有 wakelock）；
  - AOSP 的 Linux Terminal（AVF）：只有 specialUse 前台服务，在 Pixel 上不被冻结，只是因为 AOSP 不冻结前台服务。

## 4. 本仓库的 Agent 路径对 APK 的依赖（源码）

APK 进程提供：`platform.sock`、`capture.sock`、`codec.sock`、`wayland-0`、`ws-N` 和 `rungic-gpu-alloc`。冻结早期，连接先排进 backlog（最多 50），客户端各自等到超时；排满后，带超时的客户端立即收到 `EAGAIN`，不带超时的阻塞式 `connect()` 永久阻塞。power-policy（每 2 s）、keeper、`host_watch` 都在轮询，几分钟内就会排满。

**会卡住 Agent 的**：
1. **在冻结期间启动工作区**：`kwin_wayland --android-host` 在宿主的第一次 Wayland 往返上无超时阻塞（60 s 的连接超时只覆盖 socket 不存在的情况）。之后在该工作区启动的应用也卡在第一次往返，KWin 的 D-Bus 名字不出现，桌面工具全部失败。团队验收中 art 的“受阻”就是这个原因。
2. **宿主被杀（不只是冻结）**：工作区 KWin 以 133 退出，unit 重启；应用的 scope 因为 `BindsTo` 一起被停掉，Agent 的应用和未保存的工作丢失。
3. **`codec-client.c`**：阻塞式 `connect` 没有超时，backlog 排满后永久阻塞。
4. **`rungic-cast`**：每条命令最多等 350 s。

**出错但 Agent 能继续的**：
- 每次桌面工具调用最多多等 3 s（`router.bridge`）；
- OCR 先等 20 s，再降级到 CPU；
- `user_watching()` 在连不上时返回 `{}`，`.get('foreground', True)` 会把用户当成“在看”，从而**压住团队的通知**（第 2 步代码的缺陷，要修）；
- 团队和 Agent 的系统通知只画在 Plasma 里，APK 冻结时用户看不到，目前没有 Linux 到 Android 的通知通道；
- NetworkManager 的模拟层会发布“未知”连接状态，个别应用可能以为自己断网。

**不受影响的**：已经在运行的工作区 KWin 里，桌面工具照常工作：ScreenShot2 是离屏渲染，fake input 与后端无关，KWin 的 D-Bus 也不等宿主。只是画面和客户端的帧回调停住。

## 5. 无头工作区与远程观看（源码与文档，未上机）

### 5.1 工作区实际从宿主拿什么

工作区 KWin 从宿主只拿三样：帧时钟（`wp_presentation` 反馈）、缓冲分配（`rungic-gpu-alloc` 租借 AHB，录屏缓冲也走这条路）、可选的零拷贝呈现。前两样在容器里可以自己解决。

### 5.2 KWin virtual 后端（kwin v6.6.5 `b04d59c0`）

- `--virtual` 用 Noop session，每个输出自带软件 vsync 定时器，不依赖宿主。
- 支持 `zkde_screencast`、fake input、EIS、ScreenShot2 和 Xwayland，运行中可以用 custom modes 改尺寸。
- 本项目的计算机操作工具（截图、fake input、KWin 脚本）都与后端无关。
- **原样不能用**：
  - GPU 节点只靠 `drmGetDevices2()` 枚举 `/dev/dri`，而容器里只有 `/dev/kgsl-3d0`；
  - Mesa 没有 llvmpipe，softpipe 太慢。
- **需要的补丁**（约 50–100 行，大多复用 docs/72 的钩子）：
  1. 指定渲染节点为 `/dev/kgsl-3d0`；
  2. EGL 使用该节点；
  3. linux-dmabuf 只开放 v3（与 Xwayland glamor 补丁一致）；
  4. 录屏缓冲交给 PipeWire 前的同步（KGSL 没有隐式 fence），这一项要实测。
- GBM、EGL 在 KGSL 上已有探针实测通过（docs/research/93）。
- **后端不能热切换**：只在启动时选定。重启 KWin 会结束 Xwayland 及其应用，所以 Agent 工作区只能启动时就定为无头。

### 5.3 一律无头之后的代价

| 去处 | 现在 | 改为无头之后 |
|---|---|---|
| 手机浮窗、手机导播台 | 本来就是录 PipeWire（约 52 fps） | 不变 |
| 手机全屏、电视的焦点格 | 宿主零拷贝（49–54 fps） | 失去零拷贝，多一次录屏渲染和一次合成 |
| 电视导播台的缩略格 | 宿主零拷贝 | 改为 PipeWire 后由 Linux 合成 |

**第二阶段：把零拷贝找回来**（设计，2026-10-02 与用户讨论，未实现）

- **约束**（docs/57）：今天的零拷贝要求 KWin 画进从宿主租借的 AHB（`rungic-gpu-alloc`），宿主再把它直接设到自己的子图层上。Android 的 SurfaceControl 只接受 AHB，app 不能把任意 dma-buf 包成 AHB，所以**只有宿主分配的缓冲能零拷贝呈现**。
- **做法**：给 virtual 后端加一个“可脱离的呈现端”。

  | | 没人看，或 APK 冻结 | 手机全屏或电视在看 |
  |---|---|---|
  | 时钟 | KWin 自己的软件 vsync | 仍是 KWin 自己的；宿主的反馈只作参考 |
  | 缓冲 | KWin 用 GBM 在 KGSL 上自己分配 | 向宿主租借 AHB（沿用今天的路径） |
  | 画完一帧之后 | 不做别的 | 把缓冲交给宿主呈现，不阻塞 |
  | 效果 | 工作区照常运行 | 和今天一样零拷贝 |

  - **挂上和摘下**：有人看时连接宿主，下一帧起把交换链换成租借的缓冲（KWin 改分辨率时本来就会重建交换链）；没人看或宿主不响应时，换回自有缓冲。
  - **宿主冻结不能卡住 KWin**：
    - 与宿主的连接放在独立线程，非阻塞写，KWin 主线程不等它；
    - 宿主扣着缓冲不还时，KWin 换别的空闲缓冲；全被扣住，就立即摘下呈现端；
    - 用 release 和 feedback 超时（约 200 ms）判断宿主停住了，并且在再次租借之前就判断，避免卡在租借的 3 s 超时上。
  - **已租的缓冲在冻结后仍可用**：AHB 背后是 dma-buf，KWin 持有 fd，内核就不会释放【推断，待实测】。
- **复用**：呈现部分复用今天 Android 后端的代码（租借分配器、`set_acquire_fence` 显式同步、AHB 的 release 跟踪）。宿主收到的仍是租借的 AHB，几乎不用改；电视导播台的格子图层不变。
- **退而求其次的方案**：工作区的录屏流改用宿主租借的缓冲，宿主直接呈现这些缓冲。代价是每帧多一次 GPU 拷贝（从输出拷进录屏缓冲），还要另做一条通知，告诉宿主哪块缓冲是最新帧。
- **先验证**：
  1. APK 冻结或被杀后，已租的 AHB 是否仍然有效；
  2. KWin `EglSwapchain` 在宿主扣住缓冲时能否按需加缓冲（读源码核对）；
  3. 时钟解耦后的零拷贝帧率是否保持今天的 49–54 fps。

### 5.4 RemoteSurface Host（Swift 服务、FreeRDP 3.31、`zkde_screencast` 加 fake input）

**能否接到工作区**：
- 它读 `WAYLAND_DISPLAY` 和会话总线，所以给它工作区的环境变量即可（与 `rungic-workspace-stream` 相同）。
- 每个实例服务一个显示器、一个客户端；用 `--config`/`--socket` 可以多开。
- 授权靠桌面文件里的 `X-KDE-Wayland-Interfaces`，要在工作区的 ksycoca 里登记。

**能否在手机上运行**：
- swift.org 有 Swift 6.4.0 的 `ubuntu2604-aarch64` 包；
- FreeRDP 需要自己构建（Ubuntu 的包关了 H.264）；
- `host/build.sh` 写死了 x86_64，要改。

**编码器**：
- 本机没有 DRM 渲染节点，自动选择会落到“MemFd 采集，加 libx264”。
- VA-API 不可能；V4L2 M2M 只支持 DMABUF 队列，FFmpeg 原样用不了；`codec.sock` 的 MediaCodec 随 MainActivity 生灭，不能用于无头场景。
- **第一阶段**：先用软件 x264，720p，损伤驱动。
- **超出预算时**：仿照 `ClipboardDaemon`，用 app_process 以 shell uid 起一个独立的 MediaCodec 编码守护进程。

**导播台**：现在的状态在 APK 的 `Director.java` 和用户会话里。无头导播台需要：
- 一个“导播台工作区”（virtual KWin），里面全屏跑 `--director` 窗口；
- 导播台状态在 Linux 侧维护一份；
- RemoteSurface 接到这个导播台工作区。

**声音**：Host 从 PipeWire 取声音，而容器用的是 PulseAudio（工作区进 null sink），第一阶段先不做声音。

## 6. 方案比较与建议

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A. 让 APK 不被冻结** | 电池优化豁免（经系统对话框，离开“设置”或重启后生效）；Agent 工作期间 APK 持有 `PARTIAL_WAKE_LOCK`（同时阻止 CPU 挂起）；根控制器补发 `cmd activity unfreeze --sticky` 兜底 | 成本最低，保留零拷贝和全部宿主功能 | 依赖 Moto 的启发式规则，每个机型和 ROM 都要重验；APK 不冻结时，息屏后宿主是否还给工作区帧回调待实测；远程观看仍要经 APK 的画面 |
| **B. 宿主中与显示无关的功能移出 APK** | 用 `app_process`（root 或 shell uid）运行平台请求、通知、编码守护进程 | 脱离 APK 生命周期 | 改动面大，只能逐项迁移 |
| **C. Agent 工作区一律无头** | KWin virtual 后端加 KGSL 补丁；手机和电视观看走 PipeWire；远程走 RemoteSurface | 最稳，完全不依赖 APK，最符合“息屏加远程”的要求 | 失去电视和全屏的零拷贝（第二阶段找回）；KWin 新增补丁维护点；内存和功耗要实测 |

**建议**：
- **立即修（与方案无关）**：
  - `user_watching()` 在连不上时按“不在看”处理；
  - `codec-client.c` 的 `connect` 加超时；
  - `rungic-cast` 改为分阶段的短超时；
  - 工作区启动的第一次往返加上限，宿主无响应时明确报错，不要无限挂住。
- **近期：方案 A**，让现有架构先能在息屏时跑完团队任务，同时为方案 C 争取时间。
- **主线：方案 C**，配合 RemoteSurface 实现远程观看；方案 B 按需补齐（OCR、通知、编码）。
- **CPU 不挂起**：Agent 忙时需要持有 wakelock（方案 A 由 APK 持有；方案 C 由 root 侧 `/sys/power/wake_lock` 加 keeper 和 busy 标记控制，空闲即释放）。
- **远程入口**：如果走 VPN 应用，那个应用本身也要豁免冻结和 Doze。

**当前设备状态**：本轮实验把 `com.rungic.plasma` 加进了 deviceidle 白名单（root），至今仍在，正是方案 A 需要的。如果不采用方案 A，用 `dumpsys deviceidle whitelist -com.rungic.plasma` 撤销。

## 7. 上机验证顺序（都还没做；涉及息屏的步骤先征得用户同意）

1. **只读核对**：
   - `getprop ro.product.is_prc`、`persist.sys.smart_freezer_enabled`；
   - 从本机拉 `services.jar`，复核第 2 节的结论；
   - 息屏后 logcat 过滤 `SmartFreezer|UidStateController|moto_freezer`；
   - 用 `/sys/kernel/wakeup_sources` 区分“APK 被冻结”和“整机挂起”。
2. **方案 A**（息屏实验）：
   - 白名单生效（离开“设置”或重启）后，重测 90 s 和 180 s；
   - 只持有 wakelock 时的效果；
   - `unfreeze --sticky` 的效果；
   - APK 不冻结时，宿主的帧回调和画面是否继续。
3. **方案 C**：
   - 在 Mac mini 构建带 KGSL 补丁的 KWin，在不显示的测试槽位以 `--virtual` 启动；
   - 核对 GLES 和 FD710、dmabuf v3、glamor；
   - 启动 Kalk、Krita、Blender；
   - 跑一遍 `rungic_cua` 的截图、输入和脚本；
   - 测 `rungic-workspace-stream` 的帧率和同步。
4. **息屏下跑真实团队任务**：息屏 5–30 分钟，记录 CPU、GPU、电流和温度。
5. **RemoteSurface Host**：
   - 构建 ARM64 版，在测试工作区监听回环地址，从电脑接入；
   - 测 720p 和 1080p 时 x264 的 CPU 和延迟；
   - 测断开重连；
   - 在 Dozing 状态下远程接入。
6. 对比电视和全屏走 PipeWire 与走零拷贝，决定是否做第二阶段。

## 8. 风险

- **内存**：
  - 一个工作区的 KWin 约 146–185 MB，加上 Xwayland 和私有服务；
  - 每个 RDP Host 估计再加 50–100 MB；
  - 容器 memcg 上限 4 GiB。
  - 所以 RDP 按需启动，同时只开一个。
- **功耗和发热**：息屏后 GPU 继续合成，软件 x264 会占大核。靠损伤驱动出帧、没人看时降刷新率、空闲冻结（keeper）来控制。
- **维护**：
  - 方案 A 要按机型和 ROM 重验；
  - 方案 C 新增 KWin 后端补丁，要随 KWin 6.7 升级。
- **未解决**：
  - KGSL 下录屏缓冲的同步待实测；
  - 声音路径；
  - 锁屏时的通知通道。

## 9. 已修：冻结宿主时的卡死（2026-10-02）

先修与方案无关的四处卡死，方案 C 另行推进：

1. **工作区启动**（`agent/workspace/rungic-workspace`）：启动 KWin 之前，先用 3 s 超时向 `platform.sock` 发一次 `status`。宿主不答就不启动 KWin，并把原因写进 `$XDG_RUNTIME_DIR/rungic-workspace-N.failed`。
   - `workspace.ensure` 看到这个文件立即返回失败，不再等满 10 s；
   - 路由把原因放进错误信息（“workspace N did not start: the Android host is not responding …”），Agent 能如实报告；
   - 以前 KWin 会在第一次往返上无限挂住。
2. **`codec-client.c`**：`connect` 前设 2 s 的 `SO_SNDTIMEO`。Unix socket 的 `connect` 按发送超时等待，backlog 满时最多等 2 s，连上后恢复原设置。
   - 私有 FFmpeg（`packages/ffmpeg`）以 overlay 引入同一文件，下次构建时带上。
3. **`rungic-cast`**：先用 3 s 做一次健康检查，宿主不答就立即返回 `host-unreachable`，不再一条命令等满 350 s。
4. **语音助手的 `user_watching()`**：`platform.sock` 不答时按“没人在看”处理，团队的提问、受阻、完成照常发通知。以前被当成“在看”，通知被压住。

**实机验证**（2026-10-02 12:52）：用 root 对 APK 发 `SIGSTOP` 模拟冻结，几秒后 `SIGCONT`，期间不息屏。
- `rungic-cast status` 3.1 s 返回 `host-unreachable`；
- `workspace.ensure(4)` 3.2 s 返回失败，原因为“the Android host is not responding …”；
- Plasma 会话随后正常。
- 中途还修了 `ensure` 的一处问题：单元之前失败过、自动重启次数用完后，新的 `start` 会被 systemd 拒绝，脚本根本不运行，`ensure` 因此等满超时。现在先 `reset-failed` 再启动，单元一进入 failed 状态就停止等待。
- **没有在实机上验证的**：`codec-client.c` 的超时（编译通过，没有构造 backlog 排满的场景）；`user_watching()` 的通知路径（还要在锁屏下实测团队通知）。

## 10. 方案 C 第一步：无头工作区实机实验（2026-10-02 13:55–14:10）

**实现**：
- **KWin 补丁** `packages/kwin/debian/patches/rungic/virtual-render-device.patch`：设置 `RUNGIC_KWIN_RENDER_DEVICE` 后，virtual 后端打开指定的 GPU 节点，EGL 显示用这个节点，客户端最多拿到 dmabuf v3；不设置时行为不变。
- **工作区脚本**：`RUNGIC_WORKSPACE_BACKEND=virtual` 时，以 `kwin_wayland --virtual --width 1920 --height 1080` 启动，`RUNGIC_KWIN_RENDER_DEVICE=/dev/kgsl-3d0`，不检查、不探测宿主。默认仍是 Android 后端。
- **实验方式**：用 systemd drop-in 只给测试槽位 3 和 4 打开，测完已删除。
- 测试脚本在 `.work/diag/headless/`。

**结果**（实测）：

| 项目 | 结果 |
|---|---|
| 启动 | 1.4 s 就绪 |
| KWin 渲染 | OpenGL ES 3.2，渲染器 FD710（GPU，不是软件渲染） |
| Xwayland | GLX 直接渲染，FD710，OpenGL 4.6 core |
| 桌面工具（经 `rungic-cua mcp`，与 Agent 相同的路径） | `desktop_launch` 启动 Kalk 6.7 s；`desktop_windows` 看到它在 `Virtual-0` 上；`desktop_screenshot` 0.3 s |
| 录屏（`rungic-workspace-stream` 的 PipeWire 节点） | 手动 `pw-link` 接到 GStreamer：指针每 30 ms 动一次时，2.05 s 收到 60 帧（约 29 fps）；存下的帧画面正确：壁纸、Kalk 窗口、指针，没有撕裂或黑块 |
| 内存 | 无头 KWin RSS 约 152 MB；同时运行的 Android 后端工作区 KWin 约 142 MB |
| **APK 冻结时** | 实验中途手机进入 Dozing，APK 冻结（平台桥 `EAGAIN`）。无头工作区照样启动、截图、录屏，正是方案 C 要的效果 |

**注意**：
- 本机 WirePlumber 0.5.13 加 PipeWire 1.6.2 不让 GStreamer 的 `pipewiresrc` 按 id、serial 或名字连到 KWin 的录屏节点（报 “target not found”）。Android 后端工作区也一样，与无头无关。生产中的浮窗经 KPipeWire 消费，不受影响。测试时用 `autoconnect=false` 加 `pw-link`。
- 浮窗程序（`rungic-agent-screen-window`）先问 APK 平台桥，桥不通时不会去取画面。

**还没做**：
1. **导播台、浮窗、电视要知道无头工作区**：现在成员列表来自宿主的 `liveSources`，无头工作区不在里面。成员状态和画面要改由 Linux 侧提供，APK 只是其中一个呈现端。
2. **默认启用无头**：先解决第 1 条，并确认浮窗和电视的观看体验，再把 Agent 工作区默认改为无头。
3. 第二阶段的零拷贝（§5.3）；RemoteSurface Host（§5.4）；Agent 忙时持有的 wakelock（§6）。
4. 没有核对 dmabuf v3：手机上没装 `wayland-info`。

## 11. 导播台识别无头工作区（2026-10-02 14:00–14:15，实机）

**做法**：
- **keeper 心跳**（`agent/workspace/keeper.cpp` 的 `announce()`）：无头工作区（`RUNGIC_WORKSPACE_BACKEND=virtual`）的 keeper 每次 `check()`（约 3 s）都向 APK 发 `{"op":"director","alive":N}`，连接超时 0.5 s，APK 冻结时不会拖住 keeper。
  - 用心跳而不是一次性通告：APK 重启或解冻后会自动重新得知。
- **APK `Director`**：
  - 记录无头工作区最近一次心跳；
  - 成员 = 宿主有表面的工作区 ∪ 10 s 内有心跳的无头工作区；
  - `picture()` 一律取宿主真正有画面的成员。焦点是看板、无头工作区或还没打开的成员时，电视和全屏改呈现旁边有画面的那块；
  - 无头工作区的格子显示占位“在后台运行”（`tile_headless`），等第二阶段零拷贝（§5.3）补上画面。

**实测**：
- 无头工作区 4 启动后 1–2 s 进入成员列表（`[1, 4]`）；停止后约 10 s 心跳过期，回到 `[1]`。
- **Linux 导播台窗口**：焦点格是工作区 4 的实时画面（Kalk 窗口加壁纸），由 Linux 侧经 PipeWire 录取；右侧是工作区 1 的缩略格。
- **APK 全屏导播台**（与电视同一套画法）：工作区 4 是“在后台运行”占位，工作区 1 的缩略格正常。

**过程中发现的问题**：
1. **工作区随用户会话一起停止**：工作区单元有 `PartOf=graphical-session.target`，装 APK 等原因重启 Plasma 会话时，无头工作区也一起停掉。无头的 Agent 工作不该因为手机界面重启而中断，要把无头工作区和用户图形会话的生命周期分开（待做）。
2. **APK 文件名**：另一个会话把版本号提到了 2.29，构建输出变成 `Rungic-2.29.apk`。第一次装成了旧的 2.28，所以心跳没被处理。安装前要核对输出文件名和时间。
3. **调用顺序**：已有工作区 1 的浮窗时，给工作区 1 再调 `rungic-agent-screen on` 不会切换到导播台。要给新工作区调（`RUNGIC_WORKSPACE=4`），这与 Agent 工具的实际调用一致。

## 12. 工作区独立于用户会话；Agent 忙时保持唤醒（2026-10-02，实机）

**工作区与用户会话分开**：`rungic-workspace@.service` 去掉了 `PartOf=graphical-session.target`。
- 以前装 APK 或 Plasma 崩溃导致用户会话重启时，Agent 工作区会一起停掉（§11 中实际发生过）。
- 现在工作区只由 Agent 工具和 keeper（空闲冻结、长时间空闲后关闭）结束。
- Android 后端的工作区在宿主断开时自己退出（133），再由 `Restart=on-failure` 重连。

**息屏后系统其实没挂起，只是碰巧**：
- 深度空闲 4 分钟内 `suspend_stats/success` 一次没增加。
- 原因是 `audioserver` 一直持有 `AudioMix` 部分 wakelock，归属 uid 10348，推测是 Termux PulseAudio 常开的音频流。
- 所以此前息屏时 Agent 还能继续，只是碰巧。这条常开音频流本身也耗电，另立问题处理。

**`rungic-agent-wakelock`**（容器里的 root 系统服务，`agent/workspace/`）：
- 每 5 s 检查忙碌标记：`/run/user/*/rungic-workspace-*.busy`（工作区认领）和 `rungic-agent.busy`（语音助手在跑 turn 或后台 turn 时写入，结束时删除），只认持有者进程还活着的标记。
- 有忙碌就写内核 wakelock `rungic_agent`，带 60 s 超时，每 5 s 续一次；服务自己挂了，锁最多 60 s 后自动失效。没有忙碌就立即 `wake_unlock`。
- 容器的 `/sys` 是只读的，所以单元用 `unshare --mount` 在服务自己的挂载命名空间里把 `/sys` 重新挂成可写。`ReadWritePaths=` 做不到：只读挂载下的路径它不会改成可写，实测报 EROFS。
- **实测**：写入一个活进程的忙碌标记后，Android 侧 `/sys/power/wake_lock` 出现 `rungic_agent`；删掉标记约 5 s 后释放。
- 有单元测试（`tools/tests/test_agent_wakelock.py`）。

## 13. 第二阶段：无头工作区在电视和全屏上的画面（呈现器，2026-10-02，实机）

**选型**（设计调研见本日对话，结论如下）：
- **方案 B：每个被宿主显示的工作区一个独立的呈现器进程**。宿主不用改，宿主冻结只会卡住呈现器，工作区 KWin 不受影响。
- 方案 A（呈现器做在 KWin 里）约 900–1400 行 KWin 补丁，线程和阻塞路径都在 KWin 里，风险最高。
- 方案 C（让 KWin 的录屏缓冲直接用租借的 AHB）还能省一次拷贝，作为以后在方案 B 基础上的优化。
- **同类项目**：最接近的是 wl-mirror（把另一个输出的 PipeWire 录屏画进自己的窗口）。但它画进的是自己的 EGL 窗口表面，我们要画进宿主租借的缓冲；而且它是 GPL-3.0。所以自己写一个小程序，复用 KPipeWire（LGPL，浮窗已在用）和我们 Android 后端的租借代码。

**宿主对 ws-N 客户端的要求**（`android/host`，`packages/android-host`）：
- 连接 `/mnt/android-wayland/ws-N`；
- 在制造商为 “Rungic” 的那个工作区输出上，开一个全屏 `xdg_toplevel`；
- 只提交从 `rungic-gpu-alloc` 租借的 XBGR8888 缓冲（宿主按 inode 认出，才走零拷贝），用 `zwp_linux_dmabuf_v1` v3 包装；
- 每帧通过 `set_acquire_fence` 交 GPU 栅栏；按宿主的 frame callback 控制节奏。
- 宿主的 `liveSources` 会自动把这个工作区算作有画面。

**实现**：
- **`agent/workspace/present.cpp` → `/usr/libexec/rungic-workspace-present N`**：
  - 子进程 `rungic-workspace-stream` 录取工作区输出（指针画在画面里）并给出节点号；
  - KPipeWire `PipeWireSourceStream` 接收 DMA-BUF 帧；
  - GLES（Qt 的 EGL 显示）画一个外部纹理四边形，画进 3 块租借的 AHB；导出 EGL 原生栅栏后提交给宿主；
  - 宿主要了帧、又有空闲缓冲时才画，否则丢弃该帧；
  - 宿主的指针事件转成子进程的 `pointer`/`button`/`axis` 输入；
  - 宿主前几次往返有 3 s 上限；宿主断开、子进程退出或 stdin 关闭时，呈现器结束。
- **keeper**：无头工作区被电视（`tvShown`）、导播台全屏或单屏全屏显示时，启动呈现器，否则停止。导播台全屏的判断放在“助理屏是否打开”之前，因为导播台与浮窗开关无关。呈现器很快结束时，重启等待从 30 s 起翻倍，最长 5 min。
- **APK `Director.poll`**：`live` 变了也重排，以便无头成员有了画面就换掉占位。
- **KWin 补丁 `screencast-finish-without-implicit-sync.patch`**：GPU 节点不是 `/dev/dri` 下的 DRM 设备时（KGSL），录屏帧交给 PipeWire 前先 `glFinish`（做法同 Nvidia 和 llvmpipe）。否则消费方可能读到 GPU 没画完的帧；这也影响 Android 后端工作区的浮窗。

**实测**（G100 S，测试槽 4，无头）：
- **导播台全屏**：焦点格是工作区 4 的实时画面（Kalk、壁纸、指针），占位已消失。
- **帧率**：工作区里放 30 fps 的测试动画，宿主呈现约 27.6 fps（10 s 内 276 帧），约 2.4 fps 因宿主没要帧或缓冲占满而丢弃。
- **冻结**：对 APK 发 `SIGSTOP`：
  - 工作区照常跑动画，桌面截图也成功；
  - 呈现器只是暂停；`SIGCONT` 后同一个进程自动恢复，30 fps（10 s 内 300 帧）。
- **退出全屏**：keeper 立即停止呈现器。
- **第一版的坑**：
  - Qt 在 Mesa 上默认建的是桌面 OpenGL 上下文，外部纹理扩展不可用，着色器编译失败，随后在 libgallium 里 SIGSEGV；keeper 每 3 s 重启一次，每次留下约 140 MB 的 core。
  - 已改为显式要求 GLES，着色器链接失败就退出，keeper 加重启退避；相关 core 已清理。
- **冻结期间 Agent 工具变慢**：上面那次截图在冻结期间用了 19 s。原因是路由和 `rungic-agent-screen` 每次请求平台桥都要等满超时（3 s、5 s）。
  - 已改：一次请求超时或被拒后，写入 `$XDG_RUNTIME_DIR/rungic-host-unreachable`，之后 15 s 内的请求立即失败；成功一次就清除。

## 14. Agent 工作区默认无头（2026-10-02）

- `rungic-workspace` 默认 `RUNGIC_WORKSPACE_BACKEND=virtual`，并把实际选用的后端导出给子进程，keeper 据此决定发心跳、开呈现器。`RUNGIC_WORKSPACE_BACKEND=android` 可退回旧方式。
- 观看方式：
  - 浮窗和导播台窗口：PipeWire（与以前相同）；
  - 电视和全屏：呈现器；
  - 远程：RemoteSurface（下一步）。

**默认无头的实机验收**（2026-10-02 15:20–15:35）：
- 去掉测试 drop-in 后，工作区 1 和 4 都按默认以 `kwin_wayland --virtual` 启动，成员为 `[1, 4]`。
- 导播台全屏：焦点格是工作区 1（KClock），缩略格是工作区 4（Kalk），两路画面都来自呈现器。
- **APK 冻结时 Agent 工具的耗时**：
  - 修改前每次截图要 16–19 s，原因：每次工具调用都同步等 `show_workspace` 的两个 `rungic-agent-screen` 子进程，而平台桥连接已排进 backlog、却永远等不到回复，每个请求都等满超时；
  - 已改：`show_workspace` 放到后台线程，宿主不可达时直接跳过；不可达标记的有效期由 15 s 延长到 60 s，因为 Agent 两次调用之间常常超过 15 s；
  - 修改后：冻结期间第一次截图 4.3 s（一次超时），之后每次约 1.0 s（含启动 MCP 进程）。
- 测完退出全屏，呈现器随即停止；屏幕超时恢复为 60000。

## 15. 远程观看：RemoteSurface Host（2026-10-02）

**用户要求**：整台设备只有一个 RDP Host，权限在工作区之上，不为每个工作区各开一个；连接时客户端知道设备上有哪些屏幕（手机桌面、各 Agent 工作区，以后还有导播台），观看端可以直接切换。

**ARM64 构建**（Mac mini，独立容器 `remotesurface-build`，镜像与 `rungic-build` 相同，没有动 `rungic-build`）：
- 工具链：Swift 6.4.0 `ubuntu2604-aarch64`，GPG 签名有效。
- FreeRDP：另编了共享库形式的 FreeRDP 3.31.0（只开 server）供 Host 链接。原因：`native/build.sh` 只产出静态客户端库，而 Ubuntu 26.04 的 freerdp3-dev 已是 3.32.0，与 Host 要求的版本不符。
- 测试全部通过。产物在 `.work/remotesurface/dist-arm64/`（90 MB，自带 Swift 运行库和 FreeRDP）；手机容器已有它需要的其余运行库（FFmpeg 8.0.1、libx264、ICU、PipeWire 等）。
- 需要提给 RemoteSurface 的问题：`host/build.sh:28`/`:178` 写死 x86_64（只影响 ASan 和 Weston）；`host/native/CMakeLists.txt` 写死 FreeRDP 3.31.0；`native/build.sh` 在装有 libopus-dev 时客户端构建失败；`pipewire_capture` 测试在 aarch64 上不稳定（14 次失败 8 次）。

**单工作区冒烟测试**（实测）：
- 在工作区 1（无头）的环境里运行 Host，监听回环 3391，经 `adb forward` 映射到 K8 的 13391，用 K8 上的 RemoteSurface 客户端连接。
- 结果：TLS 加凭据认证通过；1920×1080，libx264 软件编码（没有 DRM 渲染节点，自动退回软件），持续约 27 fps，几十秒内确认 823 帧。
- 测完已断开，并关闭 K8 上的客户端和手机上的测试 Host。

**设计**（调研结论，未实现）：
- **会话内切换屏幕，不重连**：
  - Plasma 后端改为按端点（Wayland socket、会话总线）创建，连接带超时；
  - 切换时先接通新屏幕，等到它的第一个 keyframe 再提交，然后停掉旧屏幕；约 4 s 内没有 keyframe 就回复失败，保留原屏幕，所以冻结的手机桌面不会造成黑屏；
  - 切换时重置帧准入、按住的键、光标序号和码率等设置。
- **屏幕列表和切换的通道**：复用现有 `RemoteSurface::Session` 通道，加 `screens` 功能（hello 里给出列表；请求 `{"command":"screen","id":…}`；列表变化时 Host 主动推送）。客户端在会话面板加 Screen 组，并加 Agent 命令和 `rsctl screens`/`rsctl screen`。
- **屏幕从哪来**：Rungic 维护注册目录 `$XDG_RUNTIME_DIR/remote-surface/screens.d/<id>.json`，Host 每秒读一次；格式由 RemoteSurface 定义，Host 里不写 Rungic 专有逻辑。
  - 工作区启动时写条目、停止时删除；
  - 手机桌面的条目由 Rungic 根据“宿主不可达”标记，写明当前是否可用。
- **授权**：KWin 按可执行文件的规范路径匹配桌面文件，所以 `~/.local/share/applications` 下一个桌面文件就覆盖所有 KWin。
- **部署**：一个 `rungic-remote.service`，运行在用户会话里，单端口。
- **预估规模**：Host 约 1000–1200 行，协议和客户端约 500–700 行，Rungic 侧约 150–250 行。
- **之前写的 `rungic-remote@N`**：每个工作区一个 Host，方向不对，已作废，没有提交。

## 16. 设计：手机浮窗的画面也由宿主图层显示（2026-10-02，已放弃，见 §17）

**目标**：手机上的显示路径（浮窗、导播台窗口）和电视、全屏统一走“呈现器 → 宿主图层”，PipeWire 只留给远程观看。浮窗因此少一次拷贝，也不必每个成员各开一路录屏。

**难点是同帧**：浮窗在用户的 KWin 里逐帧移动（拖动、捏合、收边、呼吸动画、圆角、字幕胶囊和编号压在画面上）。如果宿主图层按单独的消息摆放，拖动时画面会落后于窗口。

**方案**：用 KWin 现成的叠加层机制，并且只用“底层加挖洞”（underlay）一种方式。
- KWin 6.6 已有叠加层的分配、挖洞（含圆角）、设备坐标换算和失败回退。让 Android 后端把宿主的子 SurfaceControl 当作 KWin 的叠加层（新增 `HostSourceLayer`），层级在桌面层之下。
- 浮窗为每块画面放一个占位子表面（在窗口表面之下，单像素黑缓冲，用 `wp_viewport` 定大小），通过新协议 `rungic_host_picture_v1.set_source(slot)` 和 `set_corner_radius` 声明“这里显示宿主画面槽 N”。KWin 把它作为 underlay，在桌面帧上挖一个带圆角的透明洞。
- KWin 把子表面位置、viewport 和槽号，随同一次桌面 commit 交给宿主（同步子表面，加上宿主协议 `rungic_host_source_v1`）。宿主在**同一个 `ASurfaceTransaction`** 里提交桌面缓冲（有洞时改为半透明）和各画面图层的位置、alpha、显隐。所以拖动不会差帧。
- 画面图层的内容直接来自现有的呈现器（ws-N），工作区出帧时，用户的 KWin 和浮窗都不必醒来。
- 字幕胶囊、编号、工具栏和 KWin 画在上层的东西，都靠挖洞自然叠在画面上，不必把客户端界面拆成多层。
- **NDK 限制**：Android 16 的 NDK 没有图层圆角接口，所以圆角只能靠 KWin 挖洞时的圆角 alpha。
- **同类做法**：Android SurfaceView（窗口挖洞、内容在下层）、Chromium 的 SurfaceControl underlay、KWin DRM 后端的 overlay/underlay，都是同一个模型。

**回退**：KWin 不提供协议、呈现器没连上、或零拷贝关闭时，浮窗照旧用 PipeWire。宿主冻结时手机桌面本身也停了，没有可回退的；解冻后自动恢复。

**改动**（估计约 1.5–1.7 千行）：
| 部分 | 内容 | 估计 |
|---|---|---|
| KWin | 新补丁 `android-host-pictures.patch`：协议和服务端；放宽叠加层候选条件；`importHostSource`；洞的 alpha；`HostSourceLayer`；`hostScale` 位置修正 | 约 500 行 |
| 宿主 Rust | `render_phone` 沿子表面树收集宿主画面；多层共用一个事务；背景色层；只换缓冲的提交；画面路由和节奏 | 约 600 行 |
| APK | `agent-screen` 的回复增加 `live`、`phone` 字段，并触发 SCREENS 事件 | 约 30 行 |
| 浮窗 | `HostPicture` QML 类型，在 Qt 渲染线程里驱动占位子表面；宿主模式下画面区域透明；回退判断 | 约 400 行 |
| keeper | 手机显示时也启动呈现器 | 约 30 行 |

**风险**（大多待实测）：
- 缩略图从 1920 缩到约 190 设备像素，可能超出显示硬件的缩放上限，SurfaceFlinger 会退回 GPU 合成。对策：缩略图由呈现器租小缓冲。
- 硬件图层数可能不够。
- KWin 的叠加层分配全有或全无，失败时画面区域会短暂变黑。
- KWin 自己的截图或录屏里，画面区域是黑的。
- 必须按 docs/57 的教训，用 GPU 合成路径复验一次。

**实机测试**：会显示在用户屏幕上，要先约时间：
- 同帧：拖动、捏合、收边时录屏逐帧比较；
- 合成方式：`dumpsys SurfaceFlinger`；
- 强制 GPU 合成；
- 回退：杀呈现器、对 APK SIGSTOP、重装 APK；
- 效率：和 PipeWire 路径对比 CPU、GPU 和带宽。


## 17. 方向调整：浮窗、全屏和电视导播台全部放在 Linux 里（2026-10-02，用户决定）

**用户要求**：浮窗、放大的浮窗和它们的全屏模式，都在 Linux 系统里完成。将来 Linux 发行版运行在 PC 上时，这些功能可以直接用。

**结论**：§16 的“宿主图层”方案只适用于 Android，已停止。KWin 侧和宿主侧的两个实现子代理已经叫停，未完成的改动全部撤销，没有进入仓库。

**新的分工**：
- **Linux 负责**：
  - 浮窗，以及导播台窗口的浮窗、放大和全屏三种形态，都由同一个 QML 窗口完成；
  - 团队看板（`TeamBoard.qml`）、格子布局和焦点切换都在这个窗口里；
  - 全屏时的触摸转发，沿用浮窗现有的输入方式（fake input）。
- **电视**：是 KWin 的第二个输出（桌面模式的 CAST 输出；在 PC 上就是外接显示器）。
  - 电视显示导播台 = 导播台窗口全屏放在这个输出上；
  - 电脑模式 = 不放。
  - 投屏控件里“导播台 / 电脑模式”的切换改为控制这件事。
- **Android（Rungic 应用）只负责**：把 KWin 的输出帧零拷贝放到手机屏幕和电视上（已有）。导播台的状态、布局和绘制（`Director.java`、`DirectorArt`、`AgentFullscreen` 的导播台部分、宿主的电视格子图层 21-tv-director、呈现器）逐步退役。
- **代价**：
  - 工作区画面要经用户的 KWin 合成一次，多一次 GPU 拷贝；以后可用 KWin 标准的直接扫描、叠加层把这次拷贝省掉（在 PC 上本来就有）。
  - 横屏全屏要由 KWin 旋转输出，或请求 Android 旋转，不能由窗口自己强制。
  - 声音跟随焦点要改由 Linux 侧的导播台提供焦点。

**顺序**：
1. 导播台窗口在手机上全屏（Linux）；
2. 电视走 CAST 输出加导播台窗口；
3. 声音跟随和 keeper 的显示判断改用 Linux 侧状态；
4. 退役 Android 侧的导播台、全屏和呈现器；
5. 视情况做叠加层优化。

远程观看的硬件编码，等 RemoteSurface 多屏合并后再做。§16 第 2 项“录屏直接进借来的内存”在新方向下不再需要，取消。

### 17.1 第 1 步：浮窗的全屏在 Linux 里（2026-10-02，实机验证）

**做法**（`agent/screen/qml/Main.qml`）：
- 浮窗新增第三种形态 `fullscreen`，与 window、tab 并列。全屏按钮不再调用 APK 的 `fullscreen`（AgentFullscreen）。
- 进入全屏时，画面、触摸层和工具栏（`stage`）整体移进同一进程的一个**普通全屏窗口**（xdg_toplevel），退出时移回浮窗的图层窗口。
  - 实测：图层窗口即使改到 overlay 层，也压不住 Plasma Mobile 的状态栏和导航栏；对普通全屏窗口，桌面会自动收起面板。PC 上的 Plasma 同理。
  - PipeWire 画面项在两个窗口之间移动后照常出画。
- **不转手机，转窗口内容**：全屏窗口比宽更高（手机竖屏）时，`stage` 绕中心顺时针转 90 度，等于手机向左横放时的横屏画面。
  - 手机本身、桌面和其他应用不转，没有整机转屏动画，也不需要任何恢复步骤。
  - 触摸坐标由 Qt 换算到转后的方向，转发代码不用改。
  - 横屏显示器（PC）上不转。
  - 曾先试过经平台桥临时横屏（`orientation` 加 temporary/restore）；用户指出只转浮窗即可，已改回，APK 的这部分改动也已撤销。
- 背景用壁纸的模糊图，即 `rungic-agent-screen background` 另写的 `$XDG_RUNTIME_DIR/rungic-agent-screen/director-background.jpg`。
- 画面按 16:9 放到最大。导播台的其他屏幕排在右侧一列；缩放按钮沿用导播台的三级 `level`：标准、放大（列宽 15%）、单独（不显示列）。
- 触摸规则与 APK 的 DirectGestures（docs/66）一致：
  - 点按 = 左键；长按 = 右键；按住移动 = 从按下处开始拖动；
  - 双指点按 = 右键，三指点按 = 中键；双指移动 = 自然滚动（按工作区 1080 像素换算）。
  - 点其他格子 = 切换焦点；点画面旁边，或从底边上滑（转后即手机左边缘）= 显示工具栏（退出、缩放、电视、关闭）。底边的点按仍是点击。
- 离开全屏的方式：
  - 工具栏的退出按钮；
  - PC 上按 Esc；
  - 仅限助手屏：全屏窗口失去焦点 0.4 秒以上（切到别的应用、主页手势）。
    - 桌面模式不用这条规则：它的第二块屏幕属于用户自己的 KWin，在全屏里点一下就会激活那块屏幕上的窗口。
    - 用户实测：沿用这条规则时，桌面模式全屏随便一点就退回了浮窗。
- 浮窗和全屏同属一个程序，助手屏（`--workspace N`）和桌面模式（`--desktop`）共用。桌面模式的全屏是用户的第二块屏幕，应当铺满（用户要求）：
  - 不留边距、不要圆角，不显示壁纸和阴影，16:9 以外的部分是黑边；
  - 点黑边呼出工具栏。
- 浮窗画面下方加了柔和阴影（Qt 6.9 起的 `RectangularShadow`）。
- 浮窗位置和尺寸取整：落在小数像素时，画面边上露出过一条底下的黑底（用户发现）。黑底现在也只在还没有画面时显示。
  - 安卓的返回键由 Rungic 应用自己处理（打开它的菜单），菜单会让全屏窗口失去焦点，因此同样退出全屏。
- 修了一个旧问题：屏幕尺寸变化时，`onAreaChanged` 先于派生值 `minWidth` 更新执行，横屏转回竖屏会把浮窗撑到横屏宽度的一半。`settle()` 改为直接按 `area` 计算。

**实机验收**（G100 S，工作区 1，无头 KWin，Dolphin 在工作区内）：
- 进入全屏：面板收起、画面铺满、手机方向保持竖屏（`mCurrentRotation=ROTATION_0`）。
- 转后的长按：右键菜单出现在手指所在的文件上。
- 横屏版本中的点按和拖动（指针落点准确，拖动框选了 3 项）：在转手机的那一版上测的；转内容之后只复测了长按。
- 工具栏退出：回到浮窗，浮窗保持原来的位置和大小。

**未验证**：
- 双指滚动和多指点按（adb 只能模拟单指）；
- 导播台（多个屏幕）的全屏布局和缩放；
- PC 上的 Esc。

**暂未移植**：按重力感应选择向左或向右横放。触控板模式和惯性滚动见 §17.3。

### 17.2 进出全屏无缝过渡（2026-10-02，用户要求）

**用户反馈的问题**：
- 进入全屏时，画面从左下角斜着滑进来；
- 状态栏和导航栏先显示，再消失。

**原因**：
- 当时的全屏是一个普通全屏窗口。Plasma Mobile 的两个面板在 overlay 层，它们发现“当前应用全屏”后才自己滑走（`containments/panel`、`taskpanel` 的 `fullscreen` 状态）。
- 新窗口刚出现时还不是全屏尺寸，画面先按错误尺寸排了一次，又用位置动画滑过去。
- 浮窗原来的位置放进转过的坐标系后，对应的是屏幕上另一个地方，所以看起来是从角落斜着进来。

**过程中试过、但放弃的做法**：另开一个 overlay 层窗口来显示全屏。
- 实测（WAYLAND_DEBUG）：新窗口空的第一帧约 49 ms 画出；画面移进去之后，第一次连同画面一起画却又用了约 112 ms（要第一次建立画面流纹理、特效和着色器）。
- 这段时间里画面两边都不在，录屏可见浮窗消失了十几帧。

**最终做法：同一个窗口完成全部过渡**（`Floater::setFullscreen`）：
- **进入**：
  - 浮窗切到 overlay 层，并声明接收键盘（OnDemand）。
  - 按 KWin 6.6.6 源码：图层窗口开始接收键盘时会被激活（`handleAcceptsFocusChanged` → `activateWindow`），激活会无条件把它提到同层最上面（`raiseWindow`），因此盖在面板上面。
  - 只改图层不接收键盘时，KWin 会把它放到活动窗口下面，仍在面板之下，这就是之前那次尝试失败的原因。
- **面板不动**：面板看不到“全屏应用”，不会滑走，只是被盖住；背景淡入时它们随之被遮住。
- **变形动画**：布局立即换成全屏；画面通过变换（平移、缩放、旋转），从浮窗的位置、大小和角度连续变到全屏，耗时 0.32 秒，OutCubic 缓动。背景同时淡入。
- **退出**：动画反着播放，结束时画面正好回到浮窗的位置，再切回 top 层、不再接收键盘。
- **细节**：变形开始时工具栏立即隐藏（否则会跟着画面一起转）；桌面模式全屏没有阴影，阴影改为淡出。
- **电脑上**：Esc 退出全屏。

**取舍**：
- 全屏期间 Plasma 的下拉和主页手势不可用，因为面板被盖住了；
- 退出全屏后，键盘焦点不会自动交还给之前的应用（KWin 没有这样做），要点一下应用才恢复。

### 17.3 与 APK 全屏逐项对齐（2026-10-02，用户指出漏了触控板切换）

**教训**：做 Linux 全屏时只移植了直接触摸，把 APK 全屏已有的触控板模式记成了“暂未移植”。用户发现工具栏上的切换没了。替换旧机制前，必须先把旧实现的功能逐项列出来再对照，见下表。

| APK AgentFullscreen 的功能 | Linux（`qml/FullTouch.qml`、`Main.qml`） |
|---|---|
| 工具栏：退出、缩放（导播台）、触控板开关、电视、关闭 | 相同；触控板开关选中时高亮 |
| 触控板模式（TouchpadGestures）：libinput 的轻点状态机——轻点单击、点两下双击、点后按住拖动（按过轻点时限也算拖动）、双指或三指轻点右键或中键、双指滚动 | 移植，状态和时限相同（轻点 180 ms，拖动等待 160 ms） |
| 指针加速（PointerTransfer）：libinput 触控板曲线（低于 7 mm/s 降到 1/3，7–130 mm/s 为 1:1，最高约 5.3 倍）；速度取最近 60 ms 的最小二乘直线，停顿超过 40 ms 截断；按辛普森法求平均 | 移植；单位由 `Screen.pixelDensity` 换成毫米 |
| 模式记在 `agent_fullscreen_touchpad` | 记在 `~/.config/rungic-agent-screenrc` 的 `[Fullscreen] touchpad` |
| 移动阈值 1.3 mm，按每根手指相对自己的按下点计算 | 相同（`TouchPoint.startX/startY`） |
| 长按时限等于 `ViewConfiguration.getLongPressTimeout()` | 400 ms（Android 12 起的默认值） |
| 点击按下后 40 ms 再抬起（面板的应用启动器认不出同一瞬间的按下和抬起） | 相同 |
| 双指滚动松手后惯性滚动（宿主的 scrollStop） | 在客户端实现：取最近 80 ms 的速度，每 16 ms 衰减到 94% |
| 从底边上滑呼出工具栏，3 秒后隐藏；底边的点按仍是点击 | 相同 |
| 导播台：点其他屏幕切焦点；看板在焦点时不转发触摸 | 相同 |
| 导播台：各屏幕的名字按电视样式画出（DirectorArt） | 浮窗格子自带的标签（非焦点格子的编号或角色、状态点、焦点的说明条） |

**新增**：
- 直接触摸模式下，点画面旁边可以显示或隐藏工具栏；
- 进入全屏后工具栏先显示 1 秒，再淡出并向底边滑出（用户要求，用来告诉用户工具栏在哪里）。

## 18. 边缘手势与安卓系统手势的冲突（2026-10-02，调研，尚未实机验证）

**用户要求**：边缘第一次滑动只交给 Linux（全屏工具栏、Plasma 的状态栏下拉、底部面板），安卓什么都不显示；1~2 秒内再滑一次，才交给安卓（返回、通知栏、回到主页）。

**源码调研结论**（AOSP android15/16-release、Launcher3 android16-release；Moto 的 SystemUI 和桌面是闭源的，都需要实机核对）：
- **系统怎么判断边缘滑动**：Rungic 用的是 `hide(systemBars())` 加 `BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE`。系统的 `SystemGesturesPointerEventListener` 只观察、不拦截：从边缘 24 dp 内开始、移动 24 dp 以上、500 ms 以内算一次边缘滑动。对这种设置的应用，系统只把系统栏临时显示出来，触摸并不交给状态栏。
- **第一次滑动**：
  - 底边：Launcher 判定“导航栏已隐藏且不允许忽略”，交给不拦截触摸的 `ResetGestureInputConsumer`，整段触摸都到应用；
  - 左右边：导航栏隐藏时返回手势被禁用（`isBackGestureDisabled`）；
  - 顶边：触摸也全部到应用。
  - 但系统栏会半透明地显示约 2.25 秒（`AutoHideControllerImpl`）；用户点了栏外区域则 350 ms 后隐藏。
- **第二次滑动（系统栏还在显示时）**：底边执行回主页手势；左右边执行返回（此时会无视应用设的排除区）；顶边拉出通知栏。
- **结论**：原生安卓本来就是“第一次给应用、第二次给系统”，只是第一次会把系统栏显示出来。
- **用户说的 (a)**：全屏里从“底部”，也就是手机左边缘，上滑就触发返回。按原生代码，系统栏隐藏时第一次不可能触发返回。可能的原因有三：那其实是第二次滑动；Moto 改了这部分；或者当时系统栏并没有隐藏（例如输入法或对话框让系统栏重新显示了）。
- **手势排除区**（`setSystemGestureExclusionRects`）：
  - 只能用于左右边；
  - 导航栏处于这种临时隐藏状态时，不受每条边 200 dp 的限制；
  - 系统栏显示期间排除区会被忽略；
  - 增删大约在下一帧生效。
  - 顶边和底边，应用无法排除。
- **有 root 时可选的手段**：
  - `IStatusBarService.disable/disable2`（root 可调用）或 `cmd statusbar send-disable-flag`：
    - 可以关掉通知栏下拉（`statusbar-expansion`），以及回主页加最近任务（必须 `home` 和 `recents` 都关）；
    - 还可以把系统栏内容清空（`system-icons`、`clock`、`notification-icons`）；
    - 关不掉系统栏的显示本身，也关不掉返回手势。
  - 用 `send-disable-flag` 设的状态挂在 system_server 的固定 token 上：应用崩溃后不会自动恢复，而且对全机生效。`disable` 则可以用我们应用的 Binder 作为 token，应用一死就自动解除。
- **排除掉的手段**：屏幕固定或锁定任务（太重，又不拦主页手势）；`policy_control`（Android 12 起已删除）；切换导航模式（全局生效，要几秒）；调试属性。
- **同类项目**：Termux:X11、Moonlight、bVNC 都只靠原生的沉浸模式，没有自己做“两段式”。

**拟定方案**（待用户确认、实机验证）：
- **不需要 root 的部分**：
  - 应用自己按系统同样的阈值识别边缘滑动。第一次交给 Linux，并打开约 1.5 秒的“交给安卓”窗口；窗口内的第二次交给安卓。
  - 左右边：沉浸状态下整条边都设为排除区，第二次滑动时由应用自己执行返回时的动作（左边打开菜单，右边发 Alt+Left）。
  - 剩下的问题：第一次滑动时半透明系统栏仍会闪现。
- **需要 root 的部分（可选，实现“第一次安卓什么都不显示”）**：
  - Rungic 在前台时，由常驻 root 助手用应用的 Binder 作为 token 调用 `disable`：清空系统栏内容，关掉回主页、最近任务和通知栏下拉；
  - 第一次滑动后立即解除，让第二次滑动生效；
  - 应用暂停、失去焦点或退出时一律恢复；应用一死，token 失效，自动解除。

**需要实机核对的项目**：
- Moto 上的侧边配置、各项阈值和自动隐藏时长（看 `dumpsys activity service com.android.systemui` 和桌面的 TouchInteractionService）；
- 每条边第一次滑动时，触摸实际交给了哪个窗口（`getevent` 加 `dumpsys input`）；
- root 标志的显示效果和切换延迟；
- 崩溃以后能否恢复；
- 输入法打开时、手机旋转后的表现。

## 19. 方案：桌面模式改为独立的 KWin（2026-10-03，用户决定，待确认细节）

**决定**：桌面模式不再是用户 KWin 里的第二块输出 CAST-n，改为和助理屏一样的独立 KWin。原因：
- 同一个 KWin 里的中转窗口会被 KWin 当作“别的应用”：一碰就关菜单（`PopupInputFilter::touchDown`）、抢走焦点；
- 独立的 KWin 天然没有这个问题，输入从外面送进去，和真鼠标、真副屏一样。

**现状**（梳理结果，详见当时的调查）：
- **桌面模式**：
  - APK 的 `desktop-mode` 让宿主给用户 KWin 一块 Cast 输出（`cast.rs` `sync_user_cast`）；
  - Plasma Mobile 的 external-screen 补丁在第二块屏上放一个桌面外壳（Folder View 桌面加任务栏）；
  - 浮窗和全屏用 `zkde_screencast` 录这块输出，用 fake input 送鼠标；
  - 电视的“电脑模式”就是呈现这块输出（来源 0）。
- **助理屏工作区**：
  - `kwin_wayland --virtual`（1920×1080，KGSL）；
  - 私有 D-Bus 和私有 `XDG_CONFIG_HOME`；
  - 里面只有壁纸（`rungic-workspace-desktop`），没有 plasmashell、通知、剪贴板同步和输入法；
  - 画面流带指针，输入走 fake input（只有鼠标，没有键盘）；电视通过呈现器。
- **依赖 CAST 的地方**：
  - `rungic_cua` 的 `desktop_in_use`、`launch` 的 CAST 前缀、`agent_output`；
  - `audio-follow` 按窗口是否在 CAST 上分配声音；
  - 验收里的 `desktop_mode_output`、提示词和技能文档；
  - §17 计划中“电视等于 CAST 输出加导播台窗口”这一点。

**拟定方案（分阶段）**：
1. **桌面实例**：
   - 新增一种独立的 KWin，例如 `rungic-desktop.service`。复用工作区的启动方式：virtual 后端、KGSL、私有总线，Wayland socket 为 `wayland-desktop`。
   - 里面运行完整的 Plasma 桌面外壳（plasmashell 的桌面版，带任务栏、通知、系统托盘）。
   - 用户配置与手机共享，桌面布局单独保存。
   - 生命周期随桌面模式开关，不会因闲置被关闭。
2. **浮窗和全屏**：
   - 改用工作区那种画面流（`rungic-workspace-stream` 的同一套）；
   - 触摸屏模式下画面不带光标，触控板模式下带系统光标；流的指针模式可切换，切换时不闪黑。
   - 输入从外面送进这个实例。
3. **键盘和输入法**：把安卓输入法的文字和按键送进桌面实例（经它的输入法或虚拟键盘接口）。助理屏以后也可以复用这条路。
4. **电视**：“电脑模式”改为呈现桌面实例，和工作区同一套呈现路径；导播台不变。§17 中“电视等于 CAST 输出”的设想相应修改。
5. **剪贴板和声音**：
   - 剪贴板在手机会话和桌面实例之间双向同步；
   - 声音沿用工作区的独立声道，桌面模式显示在哪里，声音就跟到哪里。
6. **应用**：
   - 在桌面实例里打开的应用就在桌面实例里运行；
   - 微信、Firefox 这类同时只能运行一份的应用，在两边之间切换时沿用 `switch.py` 的“关掉再在另一边打开”；
   - 手机上的应用不能再直接拖到桌面屏。
7. **收尾**：
   - 更新 `rungic_cua`、`audio-follow`、验收、提示词和文档；
   - Plasma Mobile 的 external-screen 补丁保留，供接真显示器或在电脑上使用；
   - Linux 全屏和光标的在途改动按新结构收拢；为 CAST 加的第二路录屏流不再需要。

**待用户确认**：剪贴板同步、应用规则、配置共享、电视电脑模式、生命周期（见当天对话）。

### 19.1 原型（2026-10-03，实机，用 9 号工作区临时验证）

**用户确认**：剪贴板双向同步；应用在哪个实例打开就在哪里运行，单实例应用用 switch.py 的方式在两边切换；应用配置共用，桌面布局单独保存；电视“电脑模式”投独立桌面；生命周期随桌面模式开关（关闭时请应用退出，有未保存内容则通知、不强关）。

**做法**：
- 在工作区 KWin 里启动 `plasmashell -p org.kde.plasma.desktop --no-respawn`；
- `XDG_CONFIG_HOME` 指向私有目录，里面链接用户 `~/.config` 的全部文件，只排除 `plasmashellrc`、`kwinrc`、`kwinoutputconfig.json`、`plasma-org.kde.plasma.desktop-appletsrc`、`plasma-mobile/`、`kdedefaults/`；
- `XDG_CONFIG_DIRS` 去掉 plasma-mobile 那一层；去掉 `PLASMA_DEFAULT_SHELL` 和 `PLASMA_PLATFORM`。

**为什么 plasmashell 必须用私有配置目录**：它的程序名在 `main.cpp` 的 `KAboutData` 里固定为 "plasmashell"，所以 `plasmashellrc`（面板尺寸、屏幕编号与接口的对应）和手机的移动版外壳是同一个文件，会互相覆盖。KWin 的 `kwinrc` 在 `main.cpp` 里同样写死了文件名。

**结果**：
- 完整的桌面起来了：开始菜单、固定的应用、图标任务管理器（Dolphin 运行时显示为活动）、托盘、时钟，壁纸和用户的相同。
- 私有总线按需拉起了：通知（plasmashell）、klipper、门户（kde、gtk、kwallet）、kded6、ActivityManager、kglobalaccel、ksecretd（密码库）、plasma-nm、bluez-obex、dconf、StatusNotifierWatcher 等。

**发现的问题**：
1. ~~窗口盖住任务栏~~：**不是问题**，是截图方式造成的假象。`rungic-cua screenshot` 默认只截活动窗口（`server.py` `screenshot(scope='window')`），再按整屏尺寸输出。用 KWin 脚本打印的窗口信息：
   - 任务栏是 dock，层级 3，位于 y=1018，高 62；
   - Dolphin 是普通窗口，层级 2，最大化后为 1920×1034；
   - 最大化可用区域给任务栏留出了底部。
2. **重复的会话服务共用同一份数据**：
   - ksecretd（密码库文件）有两个实例同时运行，有损坏数据的风险；
   - kactivitymanagerd 共用同一个数据库；
   - klipper 共用同一份历史文件；
   - kglobalaccel、plasma-nm 也各起了一份。
   - 需要逐项决定：转发到手机会话（密码库必须只有一个实例），还是给桌面实例单独一份（活动、klipper 历史），或者禁用。
3. 日志里 `org.kde.plasma.icontasks` 缺少 `ui/main.qml`，但任务栏能正常工作，待查原因。

### 19.2 0 号工作区：独立桌面（2026-10-03，实机验收）

**身份**：独立桌面就是工作区 0。宿主、APK 和导播台里，0 号本来就代表“电脑模式”；`rungic-workspace-env 0`、声音、无障碍总线等工具都能直接复用。

**和助理屏工作区的不同**（`rungic-workspace`，slot 0 分支）：
- 配置和数据：`XDG_CONFIG_HOME`、`XDG_DATA_HOME` 都是镜像目录（`rungic-desktop-dirs`）。
  - 里面每一项都是指向用户 `~/.config`、`~/.local/share` 的链接；
  - 例外是桌面独有的几项：KWin 和桌面外壳的状态（文件名写死，手机的 KWin 和 Plasma Mobile 也写同名文件）、全局快捷键、活动数据、kded、klipper 历史、kscreen，以及手机专用的 plasma-mobile 配置层。
  - `watch` 每 3 秒同步一次：桌面这边新建、并且已经放置 10 秒的文件挪回用户目录，再换成链接；手机那边新增或删除的文件，桌面这边跟着增删链接。
  - 实测 KConfig（`kwriteconfig6`）会顺着链接写，链接保留，写的就是用户的原文件。
  - `XDG_STATE_HOME`、`XDG_CACHE_HOME` 是桌面私有的。
- 去掉 `PLASMA_DEFAULT_SHELL`、`PLASMA_PLATFORM`，所以桌面里的程序按桌面形态运行。
- 桌面外壳：运行 `plasmashell -p org.kde.plasma.desktop`，退出后自动重启；不启用看守进程（不冻结，也不因闲置关闭）。
- 总线：`dbus/desktop.conf`。服务目录 `desktop-services` 排在最前，把 `org.freedesktop.secrets`、`org.kde.kwalletd6`、`org.kde.kwalletd5`、`org.kde.secretservicecompat` 和 kwallet 门户后端交给 `rungic-bus-forward`。
- `rungic-bus-forward`：在桌面总线上占住这些名字，每个调用原样转给用户总线上的同名服务（需要时由那边启动），回复、错误和信号原样转回。测试见 `tools/tests/test_bus_forward.py`：两条真实总线，覆盖调用、错误名和信息、自省、信号。
- RemoteSurface 屏幕登记：`ws-0`，名称 "Computer desktop"，类型 desktop。

**实机验收**（G100 S，0 号以无头方式运行，不显示在手机上）：
- 单元处于 active；plasmashell 在运行；截图里有完整的桌面（任务栏、开始菜单、固定应用、托盘、时钟、用户壁纸）。
- 在桌面总线和手机总线上查询 `org.freedesktop.Secret.Service.Collections`，结果相同；桌面那边占用该名字的是 `rungic-bus-forward`；整台手机只有 1 个 ksecretd。
- 活动数据库在桌面私有目录；`kdeglobals` 链接到用户文件；`kwinrc` 是桌面私有的。
- 停止单元后，相关进程全部退出。
- 内存：0 号运行时手机可用内存约 2.5 GB。

**下一步**：
- 剪贴板双向同步；
- 键盘和输入法；
- 把桌面模式的开关、浮窗、全屏接到 0 号，按规则处理光标；
- 电视的电脑模式改投 0 号；
- 声音跟随，以及适配看守进程；
- 更新 `rungic_cua` 等依赖。

### 19.3 剪贴板（2026-10-03，实机验收）

- 0 号里运行一份 `rungic-clipboard`（和手机会话里的是同一个程序，每个显示一份），形成“手机会话 ⇄ 安卓剪贴板 ⇄ 独立桌面”：手机、安卓应用、桌面三边互通。
- 启动时以安卓剪贴板的当前内容为准；退出后自动重启。
- **实测**：
  - 手机上 `wl-copy` 复制，桌面里 `wl-paste` 读到相同内容；反过来也一样。
  - 测试前把用户的剪贴板存在手机上，结束后恢复；只检查了“不是测试字符串”，没有读取内容。
  - 第一次测试时，后台运行的 `wl-copy` 继承了输出管道，远程命令一直等它结束，导致超时；`wl-copy` 的输出要重定向。

### 19.4 桌面模式接到 0 号，以及键盘（2026-10-03，已部署，待用户实测）

**命令行（`rungic-desktop-mode`）**：
- `on`：启动 `rungic-workspace@0` 并打开浮窗。如果 APK 里还留着旧的桌面模式开关，顺手关掉，宿主就不再生成 CAST 输出。
- `off`、`close`、浮窗的关闭按钮：`rungic-cua close-workspace 0`，先请应用退出；如果有应用因未保存内容没有关掉，就发通知（查看 / 不保存直接关闭），不强行关闭。
- `toggle`、`ensure`、`status`：以 0 号是否在运行为准；是否在电视上，仍取 APK 的状态。

**浮窗（AgentScreen）**：
- 0 号也走工作区画面流（`rungic-workspace-stream --pointer-hidden`），主画面不带指针。
- 全屏触控板模式下，画面流程序按 `pointer-stream on` 另建一路带指针的画面（`pointer-node N`），叠在主画面上。
- 原来专门为 CAST 写的录屏、模拟输入、错位修正和按观看节流的代码全部删除。

**键盘（第一版：手动）**：
- 全屏工具栏新增键盘按钮，点了弹出安卓输入法，再点收起。
- 浮窗里有一个看不见的输入框接收输入：提交的文字经画面流程序的 `text` 命令，用 KWin 的 `VirtualKeyboard.commitText` 送进对应屏幕获得焦点的输入框，中文也行；退格（输入框为空时）、回车、Tab、Esc、方向键、Delete、Home、End、翻页，经 `key` 命令作为真实按键送进去。
- 助理屏的全屏同样可用。
- **以后再做**：在桌面里点中输入框时自动弹出键盘，需要一个小 KWin 补丁来报告“有输入框被点中”。
- 以前的桌面模式也不会在桌面屏的输入框上自动弹出键盘（`textInputOnExternalOutput`），所以第一版没有丢掉旧行为。

**暂未处理**：
- 电视的电脑模式仍然投 CAST，下一步改投 0 号；
- 声音跟随、看守进程对 0 号的处理；
- Agent 工具里以 CAST 为目标的逻辑。

**实机验收**（2026-10-03 00:07–00:26，G100 S，用户同意后进行）：
- **切换**：`rungic-desktop-mode on` 后，KWin 只剩 WL-0（kded 弹出一次“Display Removed”，来自旧 CAST 断开）；浮窗显示 0 号桌面，画面里没有光标。
- **全屏**：画面铺满并转成横向；工具栏先显示（退出、触控板（按用户设置高亮）、键盘、电视、关闭），随后自动隐藏。
- **触控板模式**：显示 KWin 自己画的系统光标，跟着手指移动。
  - 修复一：带指针的那路画面原来“ready 之后才可见”。但 KPipeWire 只在可见时接收数据，两边互相等待，那路流一直处于 suspended。改为一直可见，ready 之前透明度为 0。
- **底边上滑呼出工具栏**：
  - 修复二：从底边起手的触摸要先压住，直到走出 3.2 mm 再判断（APK 的 stripDecided）。移植时漏了这一步，结果手指刚动 1.3 mm 就被当成移动，永远等不到判断。
  - 起点落在安卓 24dp 的边缘区内（x=15 像素）时，安卓的系统栏会闪出来，触摸则被当成触控板移动；从边缘区之外、我们的条带之内起手，工具栏正常出现。
- **触摸屏模式**：没有光标。
  - 点任务栏的开始按钮，开始菜单打开；打开期间上滑呼出工具栏、点键盘按钮，菜单都不会关闭。
  - 点菜单里的 Dolphin，Dolphin 正常启动。之前“一碰菜单就消失”的问题不再出现。
  - 长按文件夹弹出右键菜单，点菜单外面菜单关闭。
- **键盘**：点键盘按钮后，用 `adb input text` 打出的 “dolphin” 进入了开始菜单的搜索框。
  - **未解决**：安卓输入法没有真正弹出来（`dumpsys input_method` 显示 `mInputShown=false`）。adb 的输入是直接按键，绕过了输入法，所以真实打字时能否弹出键盘还要再查。
- **收尾**：用户的触控板设置恢复为 true；测试打开的 Dolphin 已关闭；息屏时间改回 60 秒。

### 19.5 键盘被压住：输入法面板在 overlay 层最上面（2026-10-03）

- **现象**：全屏时点键盘按钮，KWin 的 `VirtualKeyboard` 报告 `active=true`、`visible=true`，plasma-keyboard 也在运行，但屏幕上看不到键盘。
- **说明**：手机上随文本框弹出的是 Linux 这边的屏幕键盘（plasma-keyboard，带 Rime）；安卓键盘要从 Rungic 菜单的“Android 键盘”切换。所以 `dumpsys input_method` 的 `mInputShown=false` 本身不说明问题。
- **原因**：输入法面板在 KWin 里属于 OverlayLayer（`Window::belongsToLayer` 中 `isInputMethod()`），和我们的全屏窗口同一层。全屏窗口是之后才被激活、提上来的，于是盖住了键盘。
- **修法**：KWin 补丁 `input-panel-above-overlay.patch`。在 `Workspace::constrainedStackingOrder` 里，把 OverlayLayer 中的输入法窗口排到这一层的最后（`std::stable_partition`）。需要重启用户会话的 KWin 才生效。

### 19.6 电视的电脑模式和声音（2026-10-03，已实现，用户实测中）

**电视**（只改 Linux，与 §17 的方向一致）：
- 电视投电脑模式时，宿主照旧在用户 KWin 里建出 CAST 输出，并零拷贝投到电视（`sync_user_cast`，来源 0）。
- 桌面模式的浮窗进程在 CAST 上放一个 overlay 层的图层窗口（`Floater::placeOnCast`，作用域 `rungic-agent-screen-tv`），显示 0 号的画面，盖住 Plasma Mobile 放在那里的桌面外壳。
- 投屏控制面板的触控板，经宿主落到 CAST 上，成为这个窗口收到的鼠标移动、按键和滚轮，再按比例转进 0 号（一格滚轮 120 = 15 个轴单位）。
- 键盘模式经 HostTextInput 提交的文字，由窗口里一个获得焦点的输入框接收，再经 `typeText`、`key` 转进 0 号。
- 电视上看到的光标是用户 KWin 自己的，位置和 0 号的指针一一对应；0 号的画面不带指针，所以不会出现两个光标。
- 桌面模式在电视上时，0 号的画面流继续运行，浮窗隐藏（状态为 tv）。

**声音**：
- 0 号启动后回环一直开着（`rungic-workspace-sound 0 listen`）：桌面开着就该听得到，不靠看守进程判断。
- `audio-follow`：投屏时，电视在电脑模式（`content == 'desktop'`）就把 0 号的回环送去电视，否则留在手机。测试见 `test_audio_follow.py`。

**用户第一次电视实测（2026-10-03）发现的问题与修正**：
- **电视黑屏约 30 秒才出画面**：电视窗口原来接的是浮窗已在用的那条 0 号画面流。KWin 的屏幕录制流给新加入的接收方不补发当前帧，要等到下一次画面变化（例如时钟走到下一分钟）才有第一帧。改为电视单独开一条新流（`rungic-workspace-stream` 的 `tv-stream on|off` → `tv-node N`，与指针流同样是附加流；`AgentScreen::setTvShown`、`tvNodeId`），新流一建立就有首帧。已部署，浮窗进程已重启，待用户复测。
- **桌面投到电视后没有声音**：0 号的回环不存在。0 号是在 `listen` 那一行加入之前启动的，之后没有补建。现场执行 `rungic-workspace-sound 0 listen` 后，回环在 0 号的 sink（安卓输出，投屏时即电视）上播放。`rungic-workspace` 为 0 号加了一个每 30 秒检查的保持循环，回环丢了会补上。
- **声音面板里没有输入和输出设备**：plasma-pa 6.6 的 `showVirtualDevices` 默认为 false，只列有声卡的设备。手机上的所有输入和输出都是虚拟的（安卓通道、管道、各工作区的 null sink），所以一个都不显示。Plasma 6.6 把系统托盘里的小程序放在托盘自己的配置组里，Plasma 脚本接口访问不到，因此直接写布局文件。
  - 新增 `rungic-desktop-plasma`，在 0 号环境中运行，给所有音量小程序写入 `showVirtualDevices=true`。
  - 运行时机：在 plasmashell 启动前写入，因为外壳运行中改的文件会在它退出时被覆盖。外壳第一次生成布局后 20 秒，用 `--needed` 检查；仍缺这项设置时，停掉外壳，由循环重新启动（这时会补写）。
  - 注意：手动运行必须带 0 号的 `XDG_CONFIG_HOME`。一次未带该变量的手动运行写进了手机自己的布局文件，已删除那一项。
  - 现场已写入（0 号布局文件第 91 行），外壳已重启，待用户确认面板里能看到设备。测试见 `test_desktop_plasma.py`。

### 19.7 Agent 工具（2026-10-03）

- `router.desktop_in_use()`：0 号在运行，或电视在投电脑模式，就在“用户的桌面”上工作。
- 目标 `desktop` 的子进程改为在 0 号的环境里运行（`workspace_env(env, 0)`）；桌面模式没开时，先执行 `rungic-desktop-mode on`。`desktop_where` 返回 `workspace: 0`，以及 `rungic-workspace-env 0 COMMAND`。
- 0 号里需要显示画面时，执行 `rungic-desktop-mode ensure`，而不是打开助理屏。
- 发语音消息时，要求活动窗口在 Agent 当前工作的那块输出上，不再要求它在 CAST 上（以前在工作区里会误报）。
- 提示词（`agent.md`）和技能 `rungic-phone-desktop` 都按新的桌面模式更新。
- `test_router.py` 按新模型改写。

**实机验收（2026-10-03 01:15 之后，会话重启以加载 KWin 补丁）**：
- `rungic_plasma.py restart-session` 报告 “Desktop did not become ready”，但会话实际已重启：KWin 新启动，plasmashell 在运行，桌面模式浮窗自动恢复（0 号不属于图形会话，重启期间一直在运行）。
- 全屏后点键盘按钮：屏幕键盘出现在全屏画面上面（`VirtualKeyboard.visible=true`，截图可见），补丁生效。
- **新问题**：全屏画面是在窗口里转成横向的，而屏幕键盘仍按手机竖屏排布，用户横握手机时键盘是侧着的。待处理。
- 收尾：键盘已收起、已退出全屏；用户的触控板设置仍为 true。
- 电视的电脑模式由用户实测。

### 19.8 独立桌面和工作区里的 Firefox 用桌面版配置（2026-10-03，用户确认方案，已实现，后台实测通过）

用户要求：投屏和独立桌面里的 Firefox 应该是桌面版配置，以免被网站跳到移动版网页；手机上继续用移动版。用户确认 Agent 工作区（1–9 号）也一起用桌面版。

**现状（读源码，mobile-config-firefox 5.4.1）**：
- 移动版配置全部由 Firefox 每次启动时执行的 `mobile-config-autoconfig.js` 加载 `boot.sys.mjs` 完成：安卓 UA 和按站点改写 UA、触摸相关默认值、样式表、标签计数、`about:mobile`。
- 这些都只存在于运行中的进程里（默认分支的设置、运行时注册的样式），不写进 `prefs.js`。所以两边共用一个配置目录，每次启动各自决定，切换时不需要清理。
- 唯一的例外是静态的 `defaults/pref/mobile-config-prefs.js` 里的 `browser.uidensity=2`，两边都会读到。

**做法**：
- 补丁 `packages/mobile-config-firefox/debian/patches/rungic/desktop-session.patch`：`getenv("RUNGIC_WORKSPACE")` 非空时不加载移动配置，并把 `browser.uidensity` 的默认值改回 0；配置目录 `chrome/mobile-config-firefox.log` 里会记一行。
- 用 `RUNGIC_WORKSPACE` 判断，而不是 `PLASMA_PLATFORM`：后者由 `startplasma` 同步进 systemd 用户环境，会串进工作区；`RUNGIC_WORKSPACE` 从不写进 systemd 环境，`switch.py` 把应用交回手机时也会去掉它。

**顺带修正（实机确认存在）**：0 号的 plasmashell 环境里有 `QT_QUICK_CONTROLS_MOBILE=true`，KDE 应用会按手机布局显示。
- `rungic-workspace` 和 `rungic-workspace-env` 在 0 号去掉 `PLASMA_DEFAULT_SHELL`、`PLASMA_PLATFORM` 和 `QT_QUICK_CONTROLS_MOBILE`。
- `rungic_cua/server.py` 的 `import_session_environment()` 原来会从 systemd 环境用 `setdefault` 把这些变量补回去，现在在 0 号跳过（`PHONE_ONLY`）。
- 1–9 号工作区的这几个变量不变。

**手机上后台实测（2026-10-03，临时配置目录，无头模式，不碰用户的配置和屏幕）**：
- `RUNGIC_WORKSPACE=3`：UA 为 `Mozilla/5.0 (X11; Linux x86_64; rv:156.0) Gecko/20100101 Firefox/156.0`，`maxTouchPoints` 为 0；配置目录的日志里有“Rungic workspace 3: a desktop, mobile configuration not loaded”。（Firefox 在所有 Linux 上都把平台报成 x86_64。）
- 不设该变量（手机）：UA 为 `Android 16; Mobile`，`maxTouchPoints` 为 1，和以前一样。
- 用户的 Firefox 和独立桌面需要重启后才用上新配置；0 号的环境变量修正要等 0 号重启。

**仍待用户实测**：
- 0 号和工作区里：`navigator.userAgent` 是 Firefox 自己的 Linux UA，`navigator.maxTouchPoints` 为 0，没有 `about:mobile`，地址栏在顶部。
- 切回手机：安卓 UA 和底部工具栏恢复。来回各切两次。
- 注意事项：
  - 用户在 `about:mobile` 或 `about:config` 里改过的设置（例如竖排标签）两边都生效。
  - 网站记在 cookie 或 service worker 里的“移动版”可能还会跳，清该站数据即可。
  - Firefox 不能在两边同时运行（`switch.py` 先关后开）。2026-10-05 起改为各用一份配置文件，见 19.12。


### 19.9 全屏的浮动键盘（2026-10-03，用户确认方案，已实现，离屏测试通过，待用户实测）

用户反馈：桌面模式全屏时，弹出的键盘方向不对（从手机竖屏的底部弹出），而且即使转过来也不好用。要求：方向跟随窗口旋转；做成像安卓平板浮动输入法那样可拖动的浮窗。

**调查（读源码，未在手机上测）**：
- 现在弹出的是系统键盘 plasma-keyboard 6.6.6，由 KWin 摆放：只有“铺满屏宽”一个尺寸选项；浮动模式在上游只是停滞的草稿 MR !36；另有 MR !109 准备用自己的引擎替换 Qt VKB。
- KWin 的 `InputPanelV1Window::resetPosition` 只会把键盘放在屏幕底部（toplevel）或输入光标下（overlay），没有旋转和自由放置。
- 要让系统键盘旋转和浮动，得同时改 plasma-keyboard 和 KWin，而“画面在窗口里旋转”是我们全屏窗口自己的设计（§17.1），所以键盘放在窗口里做，不改共享层。

**做法**：
- 全屏窗口自带键盘：`agent/screen/qml/FloatingKeyboard.qml`，用 Qt Virtual Keyboard 的 `InputPanel`，放在会旋转的 `stage` 里，方向自然跟随画面。
  - 进程启动时设 `QT_IM_MODULE=qtvirtualkeyboard`、`QT_VIRTUALKEYBOARD_DESKTOP_DISABLE=1`，布局路径指向 rungic-plasma-input 的布局（`/usr/share/rungic-rime/plasma/keyboard/layouts`），并加入 `Rungic.Rime` 的导入路径。中文用的是与手机键盘相同的 Rime 插件和布局；语言取 `plasmakeyboardrc` 的 `enabledLocales`。
  - 样式暂用 Qt 内置的 default：plasma-keyboard 的 Breeze 样式依赖编译在 plasma-keyboard 程序里的 `org.kde.plasma.keyboard` 模块，别的进程加载不了。
- 浮动：
  - 默认约一块手机键盘宽（舞台高度的 1.05 倍，最多舞台宽度的一半）。拖顶部一栏移动，松手吸附到底部中间或两角。
  - 双指缩放，范围是舞台宽度的 0.34–0.8；放大超过上限就停靠成整宽，停靠时缩小又浮起来。
  - 大小、位置、是否停靠记在 `~/.config/rungic-agent-screenrc`，桌面模式和助理屏分开记。
  - 默认位置离底边 52 像素，留出上滑呼出工具栏的区域。
- 顶部一栏：Esc、Tab、Ctrl、Alt（点一下保持，作用于下一个键后松开）、四个方向键、停靠/浮动切换、收起；中间显示正在输入的拼音。按住 Ctrl 或 Alt 时，字母和数字按键码发送（Ctrl+C、Alt+F4 等）。
- 隐藏输入框设 `ImhNoAutoUppercase | ImhNoPredictiveText`：它总是空的，否则每个字母都会被当作句首大写。
- 用户词库：与手机键盘共用一份（`~/.local/share/plasma-rime`，用户决定，同一时间只有一个键盘在用）。librime 的用户词库是独占锁的 LevelDB，所以 Rime 插件改为只在键盘使用时持有会话、用完释放（见 docs/41 同日一节）。
- 代价：这个进程不再有 Wayland text-input，手机 KWin 输入法提交给它的文字（电视的键盘模式，`commitHostText`）会由 KWin 改成按键送来。

**手机上离屏测试（2026-10-03，`agent/screen/tests/tst_floating_keyboard.qml`，qmltestrunner，临时 Rime 目录）**：键盘加载正常，语言为 zh_CN；在 2400×1080 的舞台里宽 1134、高 434，位于底部中间、离底边 52；输入 `nihao` 时显示拼音 `ni hao`，空格上屏“你好”。部署后已重启桌面模式的浮窗进程。

**用户实测（2026-10-03）**：全屏横握时，浮动键盘的方向正确。

**待实机验收**：
- 全屏横握：键盘方向正确（已确认）；拖动、吸附、缩放、停靠正常；按键不会漏到下面的画面（FullTouch）。
- 中英文输入、候选、退格、回车、方向键、Ctrl+C/V 进到独立桌面；收起键盘或退出全屏后键盘消失；助理屏全屏同样可用。
- 手机键盘不会同时弹出。
- 电视的键盘模式：英文、中文和表情仍能送达（KWin 按键回退路径）。
- 内存：窗口进程加载键盘后的增量。

### 19.10 浮动键盘的整体设计：按手机尺寸重做样式和候选栏（2026-10-03，用户反馈后，已部署，手机实测）

用户反馈：中文候选词部分太小，要整体设计。

**原因**：手机屏幕是 360×800 逻辑像素（3 倍缩放），横过来的舞台只有 800×360。Qt 虚拟键盘的内置样式按 2560×800 的大屏设计，字号随键盘宽度缩放（`scaleHint = 宽度 / 2560`）。键盘宽约 400 时，按键上的字约 10 像素（约 2 毫米），自带候选栏高约 13 像素。

**设计**：
- 自己的样式 `agent/screen/vkb/rungic/style.qml`（GPL-3.0-or-later，按 `KeyboardStyle` 公开接口自写，未拷贝 Qt 的样式文件），装到 `/usr/lib/rungic-agent-screen/qml/QtQuick/VirtualKeyboard/Styles/rungic/`；窗口进程加了这个导入路径，`VirtualKeyboardSettings.styleName = "rungic"`。
  - 所有尺寸取自按键自身的高度：字母为键高的 0.46，功能键文字 0.36，角标 0.24，图标 0.5。不再随键盘宽度缩放。
  - 键盘区的宽高比由浮动键盘设定（`aspect`）：浮动 2.6，停靠 4.4。
  - 深色，与工具栏一致：底色 #1c1e22，按键 #33363d，功能键 #25282d，按下 #4b505a，回车和大写锁定用 Breeze 蓝 #3daee9。图标用 Breeze 的符号图标。
  - 注意：样式的 id 不能叫 `style`（键盘的组件里有同名属性，会遮住它，Qt 自己的样式用 `currentStyle`）；内联组件看不到外层 id，颜色写在组件自身。
- 候选栏是浮动键盘自己的，不用 Qt 的（样式里 `selectionListHeight: 0`）。顶栏 40 像素，三种状态（`barState`，可用 `forcedState` 强制）：
  - **keys**：没有输入时，Esc、Tab、Ctrl、Alt、四个方向键，以及停靠/浮动、收起。
  - **composing**：左上角小字显示拼音，下面一行候选，字号 19，可横向滑动，第一个（空格会选它）用蓝色；右边箭头展开。
  - **expanded**：全部候选以 42 像素高的格子铺在键盘区上方，可滚动；选中后收起。
  - 候选来自 `InputContext.inputEngine.wordCandidateListModel`，选择用 `selectItem`。
- 尺寸：浮动默认宽为舞台宽的 0.52（最多 480），范围 300 到舞台宽的 0.78；离底边 18 像素，避开呼出工具栏的那条边（3.2 毫米）。
- 键盘自己的“收起”键（或系统收起键盘）会关掉浮动键盘。
- 键盘打开时，全屏工具栏出现在键盘上方，不再盖住最下面一排键。

**验证**：
- 离屏 `qmltestrunner`（`agent/screen/tests/tst_floating_keyboard.qml`）：加载、拼音出候选、展开、选词上屏“你好”通过。但离屏截图不可靠：打字之后截出的顶栏是空的（手机上软件渲染、Docker 里 llvmpipe 都一样），而真实窗口画得出来。设计要看真实窗口。
- 手机真实窗口（桌面模式全屏，临时改成触摸屏模式以便点开工具栏，测完恢复为触控板模式）：
  - 键盘方向正确，字清楚；
  - 输入 nihao：拼音在左上，候选“你好 妳好 逆号 拟好 你 拟”字大，第一个为蓝色；
  - 展开后候选格正常；
  - 工具栏在键盘上方。
- 测试输入用退格删除，没有提交进桌面；最后多出的一次退格作为按键发给了桌面（无焦点输入框）。

### 19.11 独立桌面里 Firefox 闪烁（2026-10-03，未证实原因，改动后未再出现）

- 用户反馈：独立桌面里只有 Firefox 闪，例如在输入框里打字、切换页面时；其他程序不闪。以前的方案（桌面是手机 KWin 的 CAST 输出，没有单独的 KWin 和录屏）没有这个问题。
- 调查（读源码）：Firefox 156 在两边都用 Adreno 的 OpenGL ES 3.2（freedreno），硬件 WebRender，没有 Vulkan。dma-buf 和原生合成器都关着，视频解码也不参与。怀疑过两种竞争：一是 KGSL 没有隐式同步，KWin 录屏抓到应用没画完的缓冲；二是 KPipeWire 过早把缓冲还给 KWin。
- 对照实验（Agent 工作区 8、9，`.work/diag/flicker-e1/`）：Firefox 17 轮、对照 4 轮，每轮打 40 个键。分别直接录原始画面，以及经同样的 `PipeWireSourceItem` 再录一次。默认设置、关局部重画、软件渲染、去掉 `FD_KGSL_DMABUF_UBWC`、1 倍和 1.35 倍缩放、窗口最大化，全部 0 帧闪回。两种竞争都没有证据，也没有复现用户的现象。实验的播放端只有一路画面。
- 之后的改动（§20 第 1、2 项，§20.1 的 KWin 补丁，独立桌面和手机会话的重启）部署后，用户说不再闪烁。
- 推测（未证实）：用户当时在触控板全屏，同时有两路 0 号录屏，带指针的一路盖在不带指针的一路上，两路各自出帧。Firefox 只重画局部时，两路之间短暂不一致，看起来像闪；其他程序整块重画，不明显。§20 之后不带指针的那一路暂停，只剩一路。其他可能：手机 KWin 不再在全屏下面合成造成的时序变化，或者重启本身。
- 如果再出现：先查触控板模式下的两路画面（临时恢复两路并存做对照），再按上面的实验录原始画面和手机屏幕逐帧比较。

### 19.12 手机和工作区的 Firefox 各用一份配置文件（2026-10-05，用户批准方案 1、2，已实现，G100 S 实测通过）

- **现象**（用户反馈）：Codex 任务在工作区里打开浏览器后，再从手机的应用抽屉点 Firefox，提示 Firefox 已在运行、没有响应，打不开。
- **原因**：
  - 0 号（独立桌面）为了和手机共享设置，把 `~/.config/mozilla` 链接成了用户的目录（rungic-desktop-dirs），两边用同一个 Firefox 配置文件。G100 S 上配置文件在 `~/.config/mozilla/firefox`（XDG 位置），没有 `~/.mozilla`。
  - 一个配置文件同时只能被一个 Firefox 进程使用。第二次启动时，Firefox 想把请求交给已经运行的那个，可它在另一个显示和另一条私有会话总线上，工作区空闲 60 秒后还会被冻结，所以找不到它，只看到配置文件被锁，就报错。
  - 1–9 号工作区各有自己的 `XDG_CONFIG_HOME`，不冲突，但里面是空的新配置文件，没有用户的登录状态。
  - `switch.py` 的单实例切换只处理 Agent 打开用户已经开着的应用，反过来的情况没人管。
- **同一个 Firefox、两个屏幕各开窗口**：做不到。一个 Firefox 进程只连一个 Wayland 显示，各个空间是独立的 KWin。
- **对照**：Grok Bot 一个账号只有一台云端电脑，所有 Bot 共享浏览器配置和登录，官方说明不按 Bot 隔离；有用户报告每个 Agent 的浏览器窗口仍要单独登录。技术细节没有公开。
- **方案**（用户批准 1、2；不采用「提示被占用、一键收回」，要保证两边任何时候都能打开）：
  1. 各用一份配置文件：0 号不再链接 `mozilla`（rungic-desktop-dirs 的私有项，已有的链接在下次启动时去掉）。在工作区里，`/usr/bin/firefox` 发现 `RUNGIC_WORKSPACE` 非空且调用方没有指定配置文件时，用 `--profile ~/.local/state/rungic-workspaces/<N>/firefox`（按工作区编号定位置：经 `rungic-workspace-env` 启动的程序沿用调用方的 `XDG_CONFIG_HOME`，也就是用户的；第一次实机测试时配置文件因此建到了用户的 mozilla 目录里）。
  2. 登录状态单向复制：启动前由 `/usr/libexec/rungic-firefox-workspace-profile` 从用户的默认配置文件（`installs.ini` 的默认项，否则 `Default=1`）复制 `cookies.sqlite`、`key4.db`、`cert9.db`、`logins.db`（Firefox 156 的密码库）和 `logins.json`、`cert_override.txt`、`prefs.js`（用户的设置和已接受的使用条款，否则新配置文件一启动就是首次运行对话框）。数据库先连同预写日志按文件原样复制，再单独打开副本检查、存进配置文件；中途被写坏的副本检查不过就重取，最多三次。不直接对用户的数据库用 SQLite 备份接口：用户的 Firefox 运行时锁着 `cookies.sqlite`，第一次实机测试时备份一直等这把锁，Firefox 启动不了。工作区的 Firefox 正在运行时（`.parentlock` 被锁）不动。复制失败只在 stderr 记一行，Firefox 照常启动。Agent 在工作区里新登录的不写回用户的配置文件，下一次启动又从用户的重新复制。
  3. Firefox 从 `switch.py` 的单实例名单里去掉：Agent 打开 Firefox 时不再先关掉用户的。
- **代价**：
  - 用户在 `about:config` 里的设置不再带到工作区。
  - Agent 的 Firefox 开着时，用户在手机上新登录的网站，要等它下次启动才带过去。
  - Agent 拿到用户的 cookie，等于能以用户身份访问网站；共享配置文件时本来就是这样，现在变成明确的复制。
- **实机（G100 S，开发覆盖，2026-10-05）**：
  - 手机上的 Firefox 开着，同时在 1 号工作区启动 Firefox：两个进程同时运行，工作区的带 `--profile ~/.local/state/rungic-workspaces/1/firefox`，用户的 mozilla 目录没有多出东西。
  - 工作区里再次启动 Firefox，新页面开在已运行的那个里（同一工作区内的单实例转交正常）。
  - 用户的 52 个 cookie 都复制过去；工作区里打开 bilibili 是登录状态（头像、消息数），没有首次运行的条款对话框。
  - 第一次实测暴露的问题都已修正：配置文件位置随了用户的 `XDG_CONFIG_HOME`；对用户正在用的 `cookies.sqlite` 做 SQLite 备份一直等锁；Firefox 156 的密码在 `logins.db`；新配置文件弹出首次运行对话框（现在连 `prefs.js` 一起复制）。
  - 测试后关掉了工作区的 Firefox、停掉了 1 号工作区，删除了截图。
- **离线测试**：`tools/tests/test_firefox_workspace_profile.py`（复制内容、正在运行时不动、失败不拦启动、包装脚本只在工作区加配置文件）、`tools/tests/test_desktop_dirs.py`（0 号不再链接 mozilla，旧链接被去掉）、`tools/test_switch.py`。

## 20. 浮窗在后台省电：全屏不透明、少画无用的帧（2026-10-03，用户批准第 1、2 项，已部署实测）

用户问：浮窗打开时（尤其全屏盖住整个屏幕时），背后的 Plasma Mobile 有没有降低渲染频率？

**调查结论（读 KWin 6.6.6、Qt 源码，未实测）**：没有降。
- 遮挡剔除：KWin 只在窗口不透明时跳过被它完全盖住的窗口（`WorkspaceScene::preparePaintSimpleScreen`、`collectDamage`、`paintSimpleScreen`）。Wayland 表面是否不透明，看缓冲区有没有 alpha 和 `wl_surface.set_opaque_region`（`SurfaceInterfacePrivate::applyState`）。我们的浮窗是整屏 ARGB 表面且没有不透明区域，所以全屏时 KWin 每帧仍把主屏、壁纸、两条面板和应用都合成一遍。
- 帧回调：`WorkspaceScene::frame` 给输出上所有可见项目发帧回调，不看是否被遮挡；只有最小化、隐藏的窗口会被暂停（`WindowItem::computeVisibility`）。被盖住的程序如果在做动画，仍按满帧率渲染。（Weston 16 会跳过完全被盖住的表面。）
- 我们的后端没有覆盖层，也不能直接扫描输出客户端缓冲区：手机上每一帧都是整屏合成，加 `glFinish`，再交给宿主。Qt 的 `eglSwapBuffers` 每帧都把整个表面标为受损，所以浮窗每画一帧（小窗模式也一样），KWin 都要重新合成整个屏幕。
- 浮窗自身的无用帧：
  - 助理工作时的两个呼吸点是无限循环动画，按屏幕刷新率（120 Hz）重画，收成侧边标签时也在跑；
  - 触控板全屏时两路录屏同时运行；
  - 投到电视时，浮窗隐藏了，主画面流仍在接收；
  - 桌面全屏圆角半径为 0，图片仍开着离屏图层。

**实现（第 1、2 项）**：
1. 全屏到位后声明不透明：`Floater::setOpaque` 在表面上设一个覆盖全部的不透明区域（`wl_surface_set_opaque_region`，KWin 会裁到表面大小，旋转不用重设）。Qt 只对没有 alpha 的窗口自己设，它的 `setOpaqueArea` 是私有接口，所以这里经 `QPlatformNativeInterface` 拿 `wl_surface`（需要 `Qt6::GuiPrivate`，构建依赖加 `qt6-base-private-dev`）。窗口重新显示时 Qt 会换新表面，`visibleChanged` 时重设。
   - QML 条件：`full && !leaving && !morphing && visible && backdrop.opacity >= 1`。两种全屏背景（桌面模式的黑色、助理屏的 #101215 加模糊壁纸）都完全不透明。开始退出（`leaving` 变为 true）的同一刻撤销，随下一帧提交，在第一帧透明画面之前生效。
2. 浮窗自身：
   - 呼吸点改成每秒 10 步（`Timer` 100 ms，余弦，周期 1.4 s），亮度范围不变，侧边标签上仍会呼吸（docs/88 的行为保留）。
   - 触控板全屏：指针画面有了帧之后，不带指针的那路暂停（KPipeWire 隐藏的项目不接收）。指针画面在不再需要后保留 400 ms（`pointerHeld`），等底下那路重新有了帧再撤，避免出现空白。占位图标在指针画面下不显示。
   - 主画面流只在窗口可见时接收：投到电视、全屏交给别处显示时暂停。
   - 圆角半径为 0 时不开离屏图层。

**实测（2026-10-03，桌面模式全屏、触控板模式，`.work/diag/fullscreen-pause/sample.py` 每项约 12 秒；CPU 为单核百分比，kwin_wayland 是两个 KWin 之和）**：

| 场景 | 改动 | GPU 忙 | KWin | 浮窗进程 | SurfaceFlinger | APK | 手机每秒合成 |
|---|---|---|---|---|---|---|---|
| 桌面静止 | 前 | 6.0 | 8.6 | 0.1 | 1.4 | 1.0 | 0 |
| | 后 | 3.5 | 1.5 | 0.0 | 1.4 | 1.0 | 0 |
| 0 号里 60 帧动画（GL 小球） | 前 | 70.8 | 48.6 | 24.4 | 23.9 | 35.7 | 74 |
| | 后 | 51.1 | 41.4 | 13.6 | 22.1 | 28.3 | 76 |
| 盖住的手机界面后面 60 帧动画，0 号静止 | 前 | 31.9 | 21.1 | 0.2 | 24.7 | 35.8 | 56 |
| | 后 | 20.4 | 20.3 | 0.1 | 25.0 | 34.3 | 71 |

- 0 号在动时，GPU 忙从 70.8% 降到 51.1%，浮窗进程减半：手机 KWin 不再合成浮窗下面的各层，触控板模式下只剩一路录屏。
- 后面的程序在动、屏幕上什么都没变时，GPU 忙从 31.9% 降到 20.4%。但手机仍然每秒合成并交出约 70 帧，SurfaceFlinger 和 APK 的开销没变：KWin 收到被盖住窗口的提交后照样安排一帧（`Item::scheduleFrame` 不看遮挡），损坏区域为空也会合成并交给宿主，后面的程序也照常收到帧回调（测试动画 59 fps）。这部分要靠第 3 项（KWin 补丁）才能省下。
- 第 2 项的呼吸点只在助理屏工作中出现，本轮没有测；投到电视时主画面流暂停也还没有在电视上核对。

**仍待核对**（每种状态各采样 10 秒：`gpu_busy_percentage`、kwin_wayland、plasmashell、浮窗进程、surfaceflinger、APK 的 CPU，以及 KWin 每帧的 Paint 耗时）：
- 全屏（桌面在动 / 静止）、小窗且助理工作中、侧边标签、投到电视，改动前后对比。
- 协议核对：`WAYLAND_DEBUG=client` 下，全屏到位后有 `set_opaque_region(wl_region)`，开始退出时有 `set_opaque_region(nil)`。
- 进入、退出全屏仍然无缝；触控板模式进出没有空白或闪烁。

**第 3 项**：见 §20.1。

### 20.1 第 3 项：KWin 对整屏被盖住的窗口降帧（2026-10-03，用户决定范围，已实现并部署，L1 通过，实机测量通过）

用户决定的范围：只在一个输出**整个**被**单个**不透明、未变换、完全可见的窗口盖住时降帧（我们的全屏浮窗，或任何不透明区域覆盖整个输出的应用/全屏窗口）。桌面窗口之间的部分遮挡行为完全不变。

**调研（读源码，2026-10-03；未在这些合成器上实测）**：
- KWin：master（`392e8c07`，2026-10-01）和 6.6.6 都没有按遮挡停帧回调。
  - `Window::maybeSendFrameCallback`（MR !3450，Plasma 5.27）只服务离屏渲染：缩略图、窗口录屏、保留的 X11 窗口。它只在窗口项不可见（最小化、隐藏、别的桌面）时按满刷新率发，不管遮挡。
  - 最接近的是草稿 MR [!9732](https://invent.kde.org/plasma/kwin/-/merge_requests/9732)（“stop driving fully occluded windows”，2026-08，未合并）：复用遮挡剔除，任何被完全遮挡的窗口都不再安排帧、不发帧回调，没有低频兜底；它自己记下的缺口有：带 alpha 的窗口（Konsole）永远不算不透明，多输出、门户录屏未测。
  - 前身是 [!9712](https://invent.kde.org/plasma/kwin/-/merge_requests/9712)。评审里 Xaver Hugl 建议“一开始就不要为被遮挡的窗口安排帧”。另有 [!3256](https://invent.kde.org/plasma/kwin/-/merge_requests/3256)（2022，WIP）和 [Bug 467032](https://bugs.kde.org/show_bug.cgi?id=467032)。
- 其他合成器：
  - Weston 14（[MR 1524](https://gitlab.freedesktop.org/wayland/weston/-/merge_requests/1524)）：不可见的表面不发帧回调和呈现反馈，没有兜底。后果是 `weston-simple-egl` 被盖住时停住（issue 1044）。
  - wlroots 0.16（[MR 3554](https://gitlab.freedesktop.org/wlroots/wlroots/-/merge_requests/3554)）：只给可见缓冲区发 frame done，只有可见的损坏才安排帧。sway 同样。
  - mutter（MR 918、2662、3019）：不给被遮住的 actor 发；全遮住 3 秒后发 xdg `suspended`；FIFO 屏障每次舞台更新都放行。
  - Smithay 系（niri、cosmic-comp）：被遮挡的表面约 995 ms 发一次，与本方案相同。
- 客户端停在等帧回调时的表现：
  - Qt 6.10：100 ms 收不到回调（`QT_WAYLAND_FRAME_CALLBACK_TIMEOUT`）就把窗口当作未曝光，Qt Quick 停画；迟到的回调会重新曝光、画一帧。按 1 Hz 发时，Qt 窗口每秒“曝光一次再取消”。
  - GTK4：帧时钟无超时，就按回调频率跑，即 1 fps。
  - Mesa EGL：交换间隔为 1 时 `eglSwapBuffers` 无超时地等回调，所以必须有低频兜底。
  - Mesa Vulkan：有 fifo-v1 时，KWin 6.6 的 FIFO 兜底定时器（≥30 Hz）放行屏障。
  - Firefox：1 秒没有 vsync 才算被遮挡（推断，未测）。
  - Chromium：跟随 `suspended`。
- 已知坑：KWin 的 xdg ping 500 ms 未回应就标记“无响应”，1 秒弹出强杀提示。被盖住、在 `eglSwapBuffers` 里等回调的客户端回答不了，所以 ping 时要立刻发回调。

**设计（`packages/kwin/debian/patches/rungic/fullscreen-occlusion-throttle.patch`）**：
- 判定在每个输出的主视图每帧结束时做（`WorkspaceScene::frame` → `updateCoverage`），用这一帧 `prePaint` 留下的结果，条件与 `paintSimpleScreen` 的遮挡剔除相同：
  - 屏幕没有变换；
  - 从上往下找第一个窗口：未被特效设为半透明或变换，能遮挡（`shouldRenderItem`/`shouldRenderHole`），并且它的设备坐标不透明区（窗口不透明度为 1 时才有）包含整个输出。
- 它下面、包围矩形完全在这个输出内的窗口记为“被盖住”（`Item::setCoveredIn(view)`）。离屏渲染的窗口（缩略图、窗口录屏）、输入法、锁屏窗口除外。
- 被盖住的窗口：
  - 这个输出的帧不再给它们发帧回调（`Item::collectItems` 跳过）；
  - 它们的提交和损坏不再为这个输出安排帧（`RenderView::scheduleRepaint` 跳过），但重绘区域照常累积。
- 心跳：每秒发一次帧回调（`framePainted(nullptr, …)`，与 `maybeSendFrameCallback` 相同，会放行 FIFO 屏障，呈现反馈在下一次提交时 discarded）。心跳同时检查遮盖窗口是否还在、是否仍然不透明（为了没有新帧的变化），不是就补一次整屏重绘。
- ping（关闭、聚焦）被盖住的窗口时立刻发帧回调（`XdgToplevelWindow::sendPing`）。
- 被盖住的窗口开始离屏渲染（缩略图、录屏）时，`WindowItem::updateVisibility` 安排一次场景重绘，下一帧重新判定。
- 遮盖结束（窗口消失、撤销不透明区、变半透明、特效变换、层叠改变）都会产生一帧。这一帧里重新判定，下面的窗口用累积的重绘区域画出，并立刻收到积压的帧回调，所以退出全屏时背后的程序马上恢复。
- 部分遮挡：只要没有一个窗口盖满整个输出，就什么都不做。输出录屏（FilteredSceneView）和叠加层视图不参与判定。
- 与上游草稿 !9732 的区别：只处理整屏覆盖，保留 1 Hz 心跳和 ping 时立即发，改动集中在 `scene/` 的六个文件和 `xdgshellwindow.cpp`。

**L1（2026-10-03，主机 x86 构建环境 `rungic-build-kwin:26.04`，xvfb，`.work/hostbuild/kwin`）**：
- 打补丁后的 KWin 全部编译通过。
- 新增集成测试 `testFullscreenOcclusion` 6/6 通过，连跑 3 次结果一致。测试客户端每收到一次帧回调就画下一帧：
  - 未遮盖时 500 ms 收到 28–29 次；
  - 被不透明全屏窗口盖住时 2.5 s 只收到 2 次，期间输出呈现帧数 ≤1；
  - 遮盖窗口关闭后 300 ms 内收到回调，500 ms 内恢复到 29–30 次；
  - 遮盖窗口带 alpha 时 31 次，被一个不透明但不满屏的窗口完全挡住时 31–32 次（不受影响）；
  - 刚收到心跳后被 ping，150 ms 内收到帧回调。
- 全套 158 个测试（xvfb，串行）：134 个通过。失败的 24 个与 2026-09-26 基线（`.work/hostbuild/kwin-final-xvfb-failed.txt`）是同一组程序。逐个运行看失败的函数，都是环境原因：没有服务端装饰插件（`isDecorated()`）、光标主题、锁屏界面、X11。没有与帧回调或重绘调度有关的失败。

**部署（2026-10-03 03:30，用户同意直接部署重启）**：
- `rungic_dev.py deploy kwin`，开发覆盖 `4:6.6.6-0ubuntu0.1+rungic9+dev20261002t192629.f7ffbe5`，Mac mini 增量构建 170 秒；手机上的 `libkwin.so` 含 `sendCoveredFrameCallbacks`。
- 会话已重启：手机 KWin、plasmashell 是新进程，桌面模式浮窗自动恢复。
- 重启步骤仍报 “Desktop did not become ready”（与 §19.4 相同的误报：就绪检查用 `pidof kwin_wayland` 比较新旧进程，工作区的 KWin 一直在跑）。verify 为 `apt=ok, integrity=drift`。

**实测（2026-10-03，桌面模式全屏、触控板模式，同一脚本：`perf.py` + `.work/diag/fullscreen-pause/sample.py`，每项约 12 秒；CPU 为单核百分比，kwin_wayland 是所有 KWin 之和；“前”为部署前几分钟的同一会话状态）**：

| 场景 | 补丁 | GPU 忙 | KWin | 浮窗进程 | SurfaceFlinger | APK | 手机每秒合成 | 背后动画 fps |
|---|---|---|---|---|---|---|---|---|
| 桌面静止 | 前 | 8.0 | 21.7 | 0.1 | 1.2 | 1.1 | 0 | |
| | 后 | 2.0 | 0.1 | 0.1 | 1.6 | 1.2 | 0 | |
| 0 号里 60 帧动画（GL 小球） | 前 | 45.0 | 33.5 | 13.4 | 20.3 | 28.0 | 50.4 | |
| | 后 | 49.6 | 35.1 | 13.0 | 22.2 | 28.4 | 54.9 | |
| 盖住的手机界面后面 60 帧动画（GTK4），0 号静止 | 前 | 21.9 | 21.0 | 0.2 | 24.5 | 35.2 | 70.2 | 61–65 |
| | 后 | 2.2 | 0.2 | 0.1 | 1.4 | 1.2 | 0 | 1.0 |

- 背后有程序在动时：手机不再合成（每秒 70 → 0）。SurfaceFlinger 从 24.5% 降到 1.4%，APK 从 35.2% 降到 1.2%，GPU 忙从 21.9% 降到 2.2%，与静止时相同。背后的 GTK4 动画按心跳降到 1 fps。
- 0 号在动时不变（在测量波动范围内）。
- “桌面静止”在部署前 KWin 有 21.7%，部署后只有 0.1%。部署前背后可能有在动的东西（会话重启后不再有），不全算在补丁上。
- 退出全屏：背后动画在动时，从工具栏点退出。点击所在的 2 秒窗口里平均 10 fps，下一个窗口就回到满帧（2 秒平均 141.9，之后 57–61）。退出的变形动画期间，背后的程序已经恢复。截图里背后的窗口画面正常。
- 测完已重新进入全屏（用户原来的状态）。测试动画、0 号的小球已停止，临时文件已删除。

**仍待核对**：
- Plasma Mobile 面板、主屏在全屏期间和退出时没有残影或旧画面（本轮截图未见异常）；
- 任务切换、锁屏、键盘弹出正常；
- Qt 应用在背后时每秒“曝光一次”的开销；
- 用户实际使用中的观察。

## 21. 窗口层级：全屏盖住授权框等系统窗口（2026-10-03，分析，未改代码）

**现象（用户报告）**：桌面模式浮窗进入全屏后，0 号里的应用请求 root 权限（polkit），密码框被盖在全屏下面。§19.5 的屏幕键盘是同一类问题，当时只用 KWin 补丁单独修了输入法这一种窗口。

### 21.1 授权框从哪里弹出来（实机，2026-10-03）

- 整台手机只有一个 polkit 代理：`plasma-polkit-agent.service` 里的 `polkit-kde-authentication-agent-1`，环境为 `WAYLAND_DISPLAY=wayland-0`、用户总线、`XDG_SESSION_ID=c126`，也就是手机会话。
- 0 号（`rungic-workspace@0`）和助理屏工作区的进程都在 `user@1000.service` 下，`XDG_SESSION_ID` 同样是 c126。docs/83 已经实测过：这类进程的请求会回落到用户的显示会话。所以独立桌面里任何应用要授权，密码框都由手机会话的代理画在**手机的 KWin** 上，不在 0 号桌面里。
- 手机外观（`org.kde.breeze.mobile`）的 `systemdialog/SystemDialog.qml` 是 `Qt.FramelessWindowHint | Qt.Dialog`，`showMaximized()`，背景半透明压暗。它是普通的 xdg_toplevel。
- 同类的路由：0 号的密码库（`org.freedesktop.secrets`、kwallet）经 `rungic-bus-forward` 转到手机会话（§19.2），所以解锁提示同样出现在手机 KWin 上。
- 顺带发现：1 号工作区的私有总线上另有一个 `ksecretd`（pid 3458），这与 §19.2 写的“整台手机只有 1 个 ksecretd”不符；目前只有 0 号做了转发。这是 §19.1 提到的数据损坏风险，另行处理。

### 21.2 KWin 怎样排层（源码，kwin 6.6.6、plasma-mobile 6.6.5）

KWin 的层从低到高（`effect/globals.h`）：Desktop、Below、Normal、Above、Notification、**Active**（处于活动状态的全屏窗口）、Popup、CriticalNotification、OnScreenDisplay、**Overlay**。

- layer-shell 的四层对应关系（`LayerShellV1Window::belongsToLayer`）：background → Desktop，bottom → Below，**top → Above，overlay → Overlay**。
- Overlay 层里还有：输入法、KWin 内部窗口、画中画（`Window::belongsToLayer`）。
- Plasma Mobile 自己的外壳界面全部是 layer-shell overlay：状态栏和导航栏（`containments/panel`、`taskpanel`，`setWindowLayer(LayerOverlay)`）、通知弹窗、音量提示、控制中心（ActionDrawer）、操作按钮。Rungic 的语音助手浮层（`agent/assistant/app/overlay.cpp`）、投屏选择（`CastPicker.qml`）也在 overlay。
- 在 Plasma Mobile 的设计里，**overlay 层是外壳**，位于所有应用之上，全屏应用也不例外。全屏应用是 Active 层的普通窗口，面板看到“当前窗口全屏”（`windowMaximizedTracker.isCurrentWindowFullscreen`）后自己转成 hidden，只留一条触摸带，用来下拉。

**我们的全屏**（`Floater::setFullscreen`，§17.2）：浮窗这个 layer surface 切到 overlay，并声明接收键盘。KWin 因此激活它，激活时会把它提到 Overlay 层最上面。结果是两层问题：

1. **跨层**：Normal 到 OnScreenDisplay 各层的窗口，永远在它下面。授权框（Normal）、门户的对话框、密码库解锁、各类严重通知和 OSD 都在这里，也包括 PC 上 Plasma 桌面的同类窗口。
2. **同层**：`Workspace::addWaylandWindow` 对不要键盘、不会被激活的新窗口调用 `restackWindowUnderActive`。活动窗口和新窗口在同一层时，新窗口放到活动窗口**下面**。我们的全屏正是 Overlay 层的活动窗口，所以之后出现的 Plasma Mobile 通知弹窗、音量提示、Rungic 语音助手浮层都会被压在下面。键盘（§19.5）就是这样被压住的（输入面板不接收键盘焦点）。只有要键盘、会被激活的 overlay 窗口（例如 CastPicker）能浮上来。

**根本原因**：全屏在语义上是“一个全屏应用”。我们为了盖住面板、又不触发面板的滑出动画，把它声明成了外壳最高层的一部分，并抢到了同层的最上面。KWin 和 Plasma 按标准层级为“全屏应用之上”预留的所有窗口，都因此被盖住。逐个窗口打补丁（输入法、授权框、通知、OSD……）没有尽头；在 PC 上的 Plasma 桌面也会遇到同样的问题，这不符合 §17 的“PC 上直接可用”。

### 21.3 影响面（源码推断；授权框和键盘是用户实际遇到的）

| 窗口 | 所在层 | 现在（overlay 全屏） | 改成标准全屏（Active 层）后 |
|---|---|---|---|
| polkit 授权框（手机外观） | Normal，会被激活 | 被盖住；还会抢走键盘焦点 | 被激活后全屏退到 Normal，授权框在上面；关闭后焦点回到全屏 |
| 门户对话框、kwallet 解锁（手机会话） | Normal | 被盖住 | 同上 |
| 屏幕键盘 plasma-keyboard | Overlay，输入法 | 原本被盖住，现靠补丁 `input-panel-above-overlay` | 天然在上面，这一用途不再需要补丁 |
| Plasma Mobile 通知弹窗、音量提示 | Overlay，不要键盘 | 新出现时被压在下面 | 在上面（Plasma Mobile 对全屏应用的设计行为） |
| Rungic 语音助手浮层 | Overlay，不要键盘 | 被压在下面 | 在上面 |
| CastPicker | Overlay，要键盘 | 被激活后浮在上面 | 在上面 |
| Plasma Mobile 状态栏和导航栏 | Overlay | 被盖住，下拉和主页手势失效（§17.2 的取舍） | 看到全屏后自己隐藏，留触摸带下拉；与 §18 边缘手势的分工要重新核对 |
| 浮动键盘（§19.9） | 在全屏窗口里面 | 正常 | 不变 |
| 锁屏 | 安卓锁屏，在整个 Linux 输出之上 | 不受影响 | 不变 |

电视上的 `placeOnCast` 也是整块输出的 overlay surface，属于同一类。

### 21.4 方向

**A. 全屏改用标准全屏：xdg_toplevel `set_fullscreen`（建议）**
- KWin 的规则就是为这种情况设计的：只有活动时才在 Active 层，被别的窗口激活就退下；Popup、严重通知、OSD、外壳和输入法都在它上面。
- Plasma Mobile 和 PC 上的 Plasma 都按全屏应用对待它，不用为任何一种系统窗口单独处理。§19.5 的补丁在这一用途上可以考虑去掉。
- §20.1 的遮挡降帧针对的是“最上面、不透明、盖满整个输出的窗口”，对 toplevel 同样有效。
- 要解决 §17.2 当初放弃它的两个原因：
  - 新窗口要从第一次 configure 起就是全屏尺寸，即先设置全屏状态再首次提交，不能先按普通尺寸排一次版；
  - 画面交接：全屏窗口用同一个 PipeWire 节点再开一路消费，等它画出第一帧带画面的内容后，浮窗才藏起画面，变形动画在全屏窗口里从浮窗的位置开始。这样不会出现 §17.2 里“两边都没有画面”的十几帧。
- 面板会按 Plasma Mobile 的方式滑走，能否与变形动画同时进行、看起来自然，要上机看。如果不自然，改的是 Plasma Mobile 面板对所有全屏应用的隐藏方式，属于共享层。
- 失去焦点的规则（§17.1：助理屏 0.4 秒退出）要重新设计：授权框弹出不应该把全屏退回浮窗。

**B. 保留 overlay，在 KWin 里加一条“全屏 layer surface”规则（不建议）**
- 要在 KWin 里重新实现一遍 Active 层的语义，还要逐一区分 Overlay 层里的面板、通知、OSD，哪些该在上、哪些该在下。
- Plasma Mobile 的面板看不到 layer surface 的“全屏”，必须另改外壳。
- PC 上的 Plasma 也要带这个补丁。这仍然是在为一个非标准窗口重造标准机制。

**另一层：提示该出现在哪块屏幕上（与 A/B 无关，同样要做）**
- 0 号的授权框、密码库解锁现在画在手机上，并且是手机外观。电视的电脑模式、RemoteSurface 远程观看 `ws-0` 时，人看不到它们，桌面就会卡在等授权。
- 规则应当是：**提示出现在发起它的应用所在的那块屏幕上**；没有人在看的无头工作区，转到手机。
- polkit 每个会话只能注册一个代理，而 `user@1000` 下的进程都回落到同一个显示会话，标准的“按会话选代理”区分不了 0 号。可能的做法是只注册一个代理，按请求里的 `polkit.subject-pid` 所属的 cgroup（`rungic-workspace@N.service`）把请求交给对应显示上的界面。这一项还没有调研上游和类似项目，未开始选型。

**待做**：用户选定方向后，先查 KDE / GNOME 上“画中画转全屏”的已有实现，以及 polkit 多显示代理的上游状态，再动手。验收时按 21.3 的表逐项实测：授权框、通知、音量提示、语音助手、键盘、面板下拉，以及 PC（或无头 KWin）上的 Esc 和授权框。

### 21.5 用户决定与调研（2026-10-03）

**用户决定**：全屏改成标准全屏窗口；先调研再实现。

**用户追问：桌面是横的，授权框为什么不横着出来？**
- 全屏的横屏是窗口自己把内容转了 90 度（§17.1，只转浮窗、不转手机）。手机 KWin 的输出始终是竖屏 1080×2400，手机会话里的窗口都按竖屏排。
- 授权框由手机会话唯一的 polkit 代理画在手机 KWin 上（21.1），不属于 0 号的横屏桌面（1920×1080），所以是竖的，还是手机外观。§19.9 的浮动键盘要放在全屏窗口里自己画，也是这个原因。
- 结论：标准全屏只能让授权框露出来。要它横着出现，得让 0 号发起的提示画在 0 号里面（21.4 的“另一层”）。手机会话自己的通知和音量提示在全屏上仍然是竖的，除非全屏时真的转动手机的输出。这一条用户之前否决过（§17.1），这次没有改。

**调研：标准全屏能不能做到无缝进出**（源码，未上机）
- **画中画转全屏，别的项目怎么做**：KWin 6.6.6 自带的 xx-pip-v1 是独立的窗口角色（`xx_pip_shell_v1.get_pip`），只有移动、缩放和 origin，没有全屏；`Window::belongsToLayer` 把它放在 Overlay。浏览器、播放器的画中画也都是另开一个窗口，全屏时用 xdg_toplevel 的 `set_fullscreen`。没有找到“同一个表面从画中画切到标准全屏”的做法，所以浮窗和全屏必须是两个窗口。
- **第一帧就是全屏尺寸**：
  - Qt 6.10.2 的 `QWaylandXdgSurface::Toplevel` 在构造时调用 `requestWindowStates(window->windowStates())`，因此 `showFullScreen()` 会在第一次提交之前就发出 `set_fullscreen(output)`（qtbase `qwaylandxdgshell.cpp`）。
  - KWin 的 `XdgToplevelWindow::initialize` 在第一次 configure 时就执行 `setFullScreen(... initialFullScreenMode ...)`。
  - 所以 §17.1 那次“新窗口先按错误尺寸排一次版”的问题可以避免：等窗口拿到全屏尺寸后，再把舞台移进去。
- **画面移到另一个窗口后多快出图**：KPipeWire 6.6.4 的 `PipeWireSourceItem::itemChange(ItemSceneChange)` 会释放旧纹理并标记重建；`updatePaintNode` 用最后一帧 dmabuf 的参数重新导入（`m_createNextTexture`）。所以不用等工作区出下一帧，新窗口的下一帧就有画面。§17.2 实测的约 110 ms，是新窗口第一次建立纹理、特效和着色器的开销。
- **做法**：
  - 交接期间，离开的那个窗口留一张画面静帧（`grabToImage`），放在画面原来的位置；
  - 新窗口画出两帧之后，才撤掉静帧、开始变形；
  - 全屏窗口在活动时位于 Active 层，比浮窗所在的 Above 层高，所以静帧和新画面叠在同一位置，看不出交接。
- **会变的行为**（Plasma Mobile 6.6.5 源码）：
  - 全屏窗口是一个应用：面板看到当前窗口全屏（`isCurrentWindowFullscreen`）后转为 hidden，并留一条触摸带（`containments/panel` 的 `updateTouchArea`，约一个 gridUnit），从顶边可以下拉。
  - 它会出现在任务切换器里，也能被切走；从任务切换器关闭它，改为回到浮窗，不关掉屏幕本身。
  - 原来的 overlay 方案下，全屏期间不能下拉、不能回主页（§17.2 的取舍），这一条不再成立。顶边触摸带与全屏里的触摸会不会冲突，以及与 §18 边缘手势的分工，要上机核对。

**调研：授权框能不能出现在 0 号里**（源码，未实现）
- polkit 127（`polkitbackendinteractiveauthority.c` `get_authentication_agent_for_subject`）只有两种选法：
  - 按请求进程（必须精确等于注册的那个进程）；
  - 按会话。0 号和手机的进程同属会话 c126，按会话分不开。
- 每个请求的 details 里带有 `polkit.subject-pid` 和 `polkit.caller-pid`（同一文件的 `add_pid`）。代理可以据此查到请求进程在哪个显示上：KIO 从 0 号启动的应用在 `app.slice` 下，cgroup 看不出属于哪个工作区，所以要看进程环境里的 `WAYLAND_DISPLAY`。
- polkit-kde-agent 6.6.4 的 `PolicyKitListener::initiateAuthentication` 只在自己的显示上建 `QuickAuthDialog`。它的 `org.kde.Polkit1AuthAgent` 接口只用来把对话框挂到请求方的窗口上（xdg-foreign），不能换显示；一个 Qt 进程也只连得上一个 Wayland 显示。
- 可行的方向：仍由手机会话的代理注册，它按 `subject-pid` 判断，把来自 0 号的请求转给 0 号里运行的同一个代理界面。真正的密码校验由 polkit-agent-helper-1 凭 cookie 完成，与在哪个进程里显示无关。
- 本轮没有找到上游或类似项目里“一个会话、多块显示”的 polkit 代理实现，只能记为“本轮未找到”，不等于不存在。密码库解锁提示（经 `rungic-bus-forward` 转到手机的 ksecretd）属于同一类问题，尚未调研。


### 21.6 实现：全屏是一个标准全屏窗口（2026-10-03，已部署为开发覆盖；无头实测和手机上的桌面模式实测通过，部分项目待测）

**做法**（`agent/screen/qml/Main.qml`、`floater.cpp`）：
- 浮窗仍是原来的 layer surface（top 层、不接收键盘）。`Floater::setFullscreen` 不再切换图层，已删除。
- 新增 `fullWindow`：一个普通的 QML `Window`（无边框、透明）。`Floater::showFullscreen` 把它放到手机屏幕上并调用 `showFullScreen()`。
  - 窗口标题沿用“桌面”和“助理屏”。app_id 就是程序本来的 `com.rungic.DesktopMode` 或 `com.rungic.AgentScreen`，所以任务切换器里显示的是它们。
- 舞台（画面、工具栏、FullTouch、浮动键盘）的 `parent` 绑定到 `stageInFull`：全屏时放在 `fullWindow` 里，否则放在浮窗里。
  - 键盘焦点相关的 `escapeKey` 和 `keyboardField` 移进了 `fullWindow`，因为浮窗从不接收键盘。
- **进入**：
  1. `grabToImage` 截下浮窗里的画面，作为静帧放在原位；
  2. 显示全屏窗口；
  3. 窗口拿到全屏尺寸后，舞台移进去，按全屏排版，再变换回浮窗的位置；
  4. 全屏窗口画完两帧后撤掉静帧，开始 0.32 秒的变形动画。
- **退出**：动画反向播放。结束时在全屏窗口里留一张静帧，舞台回到浮窗；浮窗画完两帧后，隐藏全屏窗口。
- **从外部关闭**（例如任务切换器）：改为退出全屏、回到浮窗，不关掉屏幕本身。浮窗被隐藏时（比如投到电视上），全屏直接撤掉，不播动画（`dropFullscreen`）。
- 不透明区域（§20）改为设在全屏窗口上（`Floater::setOpaque(window, …)`）。
- 关键步骤写 `console.info` 日志，前缀为 `fullscreen:`。

**无头实测**（G100 S，2026-10-03 05:45–06:05；临时的 9 号工作区，在它的显示上运行新版 `--desktop`，画面是 0 号桌面；手机屏幕上的浮窗没有重启）：
- 进入全屏后，KWin 的堆叠顺序里多出“桌面”（`com.rungic.DesktopMode`）：layer 5（Active）、fullScreen、active，尺寸 1920×1080。截图里 0 号桌面铺满。
- 弹出一个 GTK 对话框（会被激活，作用与授权框相同）：
  - 全屏窗口退到 layer 2（Normal），对话框排在它上面，截图可见；
  - 点“取消”后，全屏窗口回到 layer 5 并重新成为活动窗口。
- Esc 退出全屏：全屏窗口隐藏，浮窗回到原来的位置和大小。
- 三次进出全屏，从点按到开始变形分别是 174、127、148 ms。第一次是截静帧 42 ms、映射 32 ms、新窗口画出 100 ms，后两次映射只要约 10 ms。
- 测试环境的坑：
  - `--workspace 9` 的浮窗会在几秒内正常退出：Rungic 应用没有登记 9 号助理屏，平台桥报告未开启，按设计退出。所以改用 `--desktop` 测。
  - fake input 的点击要在同一个 `WorkspaceInput` 会话里先移动、再点，点完等一会儿再关闭；否则松开事件会丢。
- 清理：测试窗口和对话框已结束，9 号工作区已停止，临时文件已删除。

**部署**：`rungic_dev.py deploy rungic-agent-screen --restart never`，开发覆盖 `0.510+dev20261002t214954.e6693dd.dirty`，`apt=ok`。没有重启用户手机上的桌面模式浮窗，因此手机屏幕上仍在运行旧版，直到浮窗进程重启。

**手机上的桌面模式实测**（2026-10-03 06:11–06:27，G100 S，用户同意重启桌面模式浮窗并在屏幕上测试；测试前确认屏幕上没有触点，即 `ABS_MT_TRACKING_ID=-1`；截图和录屏在 `.work/verify/20261003-fullscreen-phone/`）：
- **进入全屏**：画面转成横屏、铺满屏幕，Plasma Mobile 的状态栏和导航栏自己收起。手机 KWin 的堆叠顺序：
  - `com.rungic.DesktopMode`：layer 5（Active），fullScreen，active；
  - Plasma Mobile 的两个面板：layer 9（Overlay），处于隐藏状态。
- **授权框**：
  - 发起方式：从 0 号的环境发起 `pkcheck --action-id org.freedesktop.hostname1.get-product-uuid --process $$ --allow-user-interaction`。这个命令只询问授权，不做任何事。它要经 `systemd-run --user --scope` 放进 `user@1000.service`，和 0 号里真实启动的应用一样；直接从 adb 启动的进程不属于任何会话，polkit 会回答“no agent is available”。
  - 手机外观的 “Authentication Required” 显示在全屏桌面之上（`4-auth.png`）。堆叠顺序：全屏窗口退到 layer 2，`polkit-kde-authentication-agent-1` 在它上面并处于活动状态。授权框是竖屏的，与 21.5 的分析一致。
  - 授权框弹出期间，状态栏和导航栏重新出现，因为当前窗口已不是全屏窗口。
  - 点密码框后，plasma-keyboard 显示在最下面，授权框随之上移（`8-s.png`）。
  - 点“取消”后，`pkcheck` 返回 Not authorized，没有授予任何权限；全屏窗口回到 layer 5 并重新成为活动窗口，面板再次收起。
- **交接**：
  - 第一版有缺陷：舞台在全屏窗口刚 `visible` 时就移了过去，但手机上全屏窗口约 0.6 秒后才出第一帧。录屏里浮窗位置的画面消失了约 0.6 秒，变形动画也大半没被看到（`fs-enter.mp4` 第 26 帧）。9 号工作区里这一步很快，所以没有发现。
  - 修法：等全屏窗口真正画出第一帧（`frameSwapped`）之后，才把舞台移进去；浮窗里的静帧一直留到全屏窗口把画面画出来（`handoffIn`）。
  - 修改后再录一次（第一次进入，`fs-enter2.mp4`）：浮窗位置没有亮度突跳，静帧停留两帧之后开始变形，没有空白帧。日志里从点按到开始变形共 153 ms：截静帧 22 ms、全屏窗口第一帧 67 ms、画出画面 64 ms。
- **退出全屏**（从左边缘滑出工具栏，点退出；`fs-leave.mp4`）：画面连续地变回浮窗位置，没有空白帧，浮窗的位置和大小不变。
- **和原来 overlay 方案在观感上的不同**（都是 Plasma Mobile 对全屏应用的标准行为）：
  - 进入时，状态栏和导航栏在变形开始后约 0.1 秒才收起；
  - 退出时，主屏先显示模糊的壁纸，等全屏窗口隐藏后，主屏内容再淡入。
- **收尾**：浮窗已经用 `rungic-desktop-mode ensure` 以正常方式重启，处于普通窗口状态，与测试前相同（位置是默认值，测试前的拖动位置没有保存）；临时脚本和日志已删除。
- **部署**：修复后的开发覆盖为 `0.510+dev20261002t222124.e6693dd.dirty`，`apt=ok`。完整性检查是 drift：有 21 个开发覆盖和 2 个不属于任何包的 `/usr` 文件，前两次部署时就已如此。

**仍待实测**：
- 顶边触摸带下拉；在任务切换器里切走和切回；从任务切换器关闭。
- 输入密码通过授权后回到全屏（本次只测了取消）。
- 浮动键盘、Ctrl/Alt 组合键、手机键盘的文字提交。
- 直接触摸模式下点黑边呼出工具栏（本次是触控板模式）。
- 触控板模式下的指针画面；投电视；§20 的不透明区域和遮挡降帧仍然生效。

**后续**：
- `input-panel-above-overlay.patch`（§19.5）只是为原来的 overlay 全屏加的。手机实测确认键盘照常显示在新的全屏之上后，按“缩小补丁”的原则移除。
- 授权框出现在 0 号里（21.5 的方向）另行实现。

### 21.7 授权框出现在 0 号里：横屏、桌面外观（2026-10-03，用户决定方案；已部署为开发覆盖，手机实测通过）

**用户决定**：0 号里的授权框画在 0 号里。桌面只在浮窗里、没有全屏时，授权框仍留在 0 号，浮窗上显示提示，用户点了全屏再输入；不自动切换模式，也不改到手机上弹。

**各模式的规则**：

| 桌面所在 | 授权框 |
|---|---|
| 手机上全屏 | 在 0 号桌面里（横屏、桌面外观），用全屏的浮动键盘输入 |
| 只在浮窗里 | 在 0 号桌面里；浮窗画面底部显示“需要授权 · 点此全屏后输入”（琥珀色圆点），点它进入全屏；收到边缘时，标签上的圆点变为琥珀色并呼吸 |
| 电视的电脑模式、RemoteSurface 远程观看 `ws-0` | 在 0 号桌面里，在哪看就在哪输 |
| 助理屏工作区（无头，没有人在里面看） | 手机上，和以前一样 |
| 手机自己的应用 | 手机上，和以前一样 |

**调研补充**（源码）：
- polkit 127 回应验证结果时（`authentication_agent_response`），只要求调用者是 uid 0，也就是辅助程序 polkit-agent-helper-1；会话按 cookie 查找，并核对 `agent->creator_uid` 等于辅助程序调用者的 uid（`get_authentication_session_for_uid_and_cookie`）。不要求必须是注册代理的那个进程。所以同一用户的另一个进程拿着 cookie 就能完成验证。
- `polkit.subject-pid`：请求方以 D-Bus 名字作为 subject 时（KAuth），polkit 会把它解析成进程号（`add_pid`）。
- polkit-qt 0.200 的 `Agent::AsyncResult` 只是包了一层 `GSimpleAsyncResult`。自己创建一个，就能让 `PolicyKitListener` 原样弹框和验证，并在完成回调里拿到结果。
- 上游和类似项目：本轮没有找到“一个登录会话、多个合成器和会话总线”的 polkit 代理分发方案。搜到的都是同一合成器、多块显示器各开一个对话框（例如 Ukishima PR #8）。
- 0 号的外观：0 号的 `XDG_CONFIG_DIRS` 里没有 plasma-mobile 那一层，`kdedefaults/` 也是 0 号私有的（§19.2），所以 `LookAndFeelPackage` 取默认值 `org.kde.breeze.desktop`。它的 `SystemDialog` 是带标题的 `Qt.Dialog`，用 `show()` 显示，不会像手机外观那样最大化。

**做法**：
- **分发规则**：按请求进程的**会话总线**来分，不按显示名。授权框交给请求方所在会话总线上的代理界面来画。0 号的应用用 0 号的私有总线（`$XDG_RUNTIME_DIR/rungic-workspace-0.bus`）。这条规则不是 0 号专用的：哪条总线上运行着代理界面，那条总线上的应用的授权框就画在那里。
- **`packages/polkit-kde-agent-1`**（新增组件，Ubuntu `4:6.6.4-0ubuntu1`，`+rungic1`，登记在发布的 `rebuilt` 里），补丁 `rungic/delegate-prompt-to-requester-bus.patch`：
  - **路由**：手机会话里向 polkit 注册的代理，从 `/proc/<subject-pid>/environ` 读请求进程的 `DBUS_SESSION_BUS_ADDRESS`。
    - 只接受同一用户、在 `$XDG_RUNTIME_DIR` 下、不是自己那条总线的 Unix socket 地址（`promptroute.cpp`，单元测试 `routetest`）；
    - 地址可以接受时，调用那条总线上 `org.kde.polkit-kde-authentication-agent-1` 的 `org.kde.Polkit1AuthAgent.Delegate.Begin`，等待 `Finished(cookie, error)`；
    - polkit 取消时转发 `Cancel`；代理界面中途退出，请求按出错结束；
    - 环境读不到、地址不可接受，或 `Begin` 调用失败时，照常在手机上弹框。
  - **代理界面**：同一个程序加 `--delegate`。它不向 polkit 注册，在所在总线上提供 `Delegate` 接口，用 `PolicyKitListener` 原有的对话框和 `Session` 完成验证；`Prompting` 属性和 `PromptingChanged` 信号表示有请求在等待。
    - 它同时以标准名字导出 `/org/kde/Polkit1AuthAgent`，所以 0 号里 KAuth 应用调用 `setWindowHandleForAction` 时，对话框会挂到该应用的窗口上。
- **0 号总线**：`agent/workspace/dbus/desktop-services/org.kde.polkit-kde-authentication-agent-1.service`，按需激活 `--delegate`。
- **浮窗**：
  - `rungic-workspace-stream` 在工作区总线上监听 `PromptingChanged`，输出 `prompting 1|0`；
  - `AgentScreen.prompting` 据此更新；`Main.qml` 在非全屏时显示上表中的提示。
- **部署时重启**：`release/packages.json` 的 `user_restart` 里登记了 `plasma-polkit-agent.service`，以及带 `--delegate` 的完整命令行（`pkill -xf` 精确匹配，不会误杀刚重启的手机端代理）。

**测试**：
- **L1**：`routetest`（13 种地址：本用户的另一条总线、带 guid、自己那条总线及其另一种写法、`$XDG_RUNTIME_DIR` 之外、`..` 越界、别的用户、abstract、tcp、相对路径、未知键、空、多个地址取第一个；另测环境变量解析和没有运行目录的情况）。在 Mac mini 的 arm64 构建容器里另开 `BUILD_TESTING=ON` 的构建目录编译运行，通过。打包构建本身带 `nocheck`，不跑测试。
- **手机实测**（G100 S，2026-10-03 06:51–07:03，用户同意重启桌面模式浮窗并在屏幕上测试；测试前确认没有触点；截图在 `.work/verify/20261003-polkit-delegate/`）：
  - 发起方式同 21.6：从 0 号的环境经 `systemd-run --user --scope` 运行 `pkcheck … --allow-user-interaction`，只询问、不执行任何操作。
  - **0 号的请求**：手机端代理日志 “goes to the delegate on /run/user/1000/rungic-workspace-0.bus”；0 号总线激活了 `--delegate`。0 号里出现桌面外观的横向对话框 “Authentication Required”，带标题栏，任务栏里有它的图标（`1-ws0.jpg`）。手机上没有再弹竖屏框。
  - **浮窗状态下的提示**：画面底部显示提示条，琥珀色圆点（`1-phone-s.png`）。点提示条后进入全屏，授权框横着出现在全屏桌面里，提示条消失（`2-full-s.png`）。
  - **输入**：打开全屏的浮动键盘，用 adb 输入 `abc`（经隐藏输入框，以输入法提交的方式送进 0 号），密码框显示 3 个圆点（`4-ws0-crop.jpg`）。没有提交；按 Esc 取消后返回 Not authorized，`Prompting` 变回 false。
  - **由 polkit 发起的取消**：结束等待中的 `pkcheck` 后，手机端代理打出 “Cancelling authentication” 并转发给代理界面，0 号里的对话框关闭，浮窗提示消失（`8-s.png`）。
  - **回归**：手机会话自己的请求仍弹出手机外观的框（`5-s.png`），取消后返回 Not authorized。
- **发现并修复的崩溃**：第一版的代理界面在用户取消后崩溃（KCrash，pid 6920）。
  - 原因：原版 `PolicyKitListener` 取消一次会多次调用 `finishObtainPrivilege`（取消按钮、`Session` 的 completed、窗口关闭），每次都会完成结果对象。手机端代理（未改动的那部分逻辑）的日志里也能看到同样的重复。polkit-qt 从不释放它的 `AsyncResult`，所以原版没事；第一版代理界面在完成回调后用 `singleShot(0)` 删掉了自己的 `AsyncResult`，13 ms 后的那次重复调用用了已释放的对象。
  - 修法：完成后保留到下一个请求完成时才删除。修复后，日志里同样出现了完成之后的第二次 “Dialog cancelled”，进程 9606 没有崩溃，之后也没有新的崩溃记录。
- **测试方法上的教训**：浮动键盘没打开时，全屏里的 Esc 按设计会退出全屏，而不是送进 0 号；第一次重测时把“退出了全屏”误当成“没有取消”。键盘打开时，Esc 才会送进 0 号。
- **部署**：开发覆盖 `4:6.6.4-0ubuntu1+rungic1+dev20261002t225644.f9d1056.dirty`，以及同一轮的 `rungic-agent-screen`；`apt=ok`。完整性检查是 drift，与 21.6 相同，都是已有的开发覆盖和两个早已存在的 `/usr` 文件。
- **工具修正**：`rungic_dev.py` 原先优先用基线发布里记录的 `user_restart`，工作区新登记的重启项被忽略，第一次部署后手机端代理没有重启（只好手动重启）。现在把发布的清单和工作区的清单合并，工作区优先；`tools/test_rungic_dev.py` 19 项通过。重启时 systemd 提示单元文件已变、需要 `daemon-reload`，重启脚本不执行它，暂未处理。

**未测和遗留**：
- 电视的电脑模式、RemoteSurface 远程观看 `ws-0` 时的授权框（按设计应和全屏时一样）；
- 输入正确密码后授权成功（测试中不输入用户密码）；
- KAuth 应用（Discover、系统设置）把对话框挂到自己窗口上的行为；
- 对话框在 0 号里被放在左上角（0 号 KWin 默认的摆放方式），可以改成居中；
- 手机端浮窗显示英文提示：浮窗进程的语言本来就是英文（标签 “Desktop” 也是），和这次改动无关；
- 0 号的密码库解锁提示（经 `rungic-bus-forward` 转到手机）仍在手机上，属于同一类问题，尚未处理。


## 22. 退役 APK 全屏与投屏测试图；会话就绪误报的修复（2026-10-03，G100 S 实机）

**APK 2.30**（提交 `168aa02`）：§17 顺序第 4 步的一部分。
- 删除 `AgentFullscreen`、`DirectGestures`、`CastTest` 和平台桥 `cast-test` 操作；`desktop-mode`、`agent-screen`、`director` 不再接受 `fullscreen` 请求，回复里也没有 `fullscreen`、`directorFullscreen`；呈现器只剩电视一个归属，导播台不再有手机布局。
- 保留：电视的导播台与呈现器、`TouchpadGestures` 与 `PointerTransfer`（投屏控件的电视触控板；`FullTouch.qml` 是它们的移植）。
- Linux 一侧删除 `AgentScreen::fullscreen()`、`Director::fullscreen()`、`fullscreenShown`（QML 早已不调用），以及 keeper、`rungic-agent-screen` 对这两个字段的读取；平台桥契约同步。新旧版本混用时，缺少的字段读作 false。

**部署**（开发覆盖，基线 20260930.10）：8 个包（`rungic-agent-screen`、`rungic-cast`、`rungic-cua`、`rungic-plasma-bridges`、`rungic-plasma-config`、`rungic-plasma-services`、`rungic-plasma-session`、`rungic-voice-agent`）版本 `+dev20261003t045430.168aa02`，`[verify] apt=ok`，完整性 `drift`（开发覆盖本身）。APK 2.30 用 `adb install -r` 安装。

**实机结果**：
- 桌面、dock 与部署前一致；部署和两次会话重启期间没有新的崩溃，没有失败的单元。
- 验收：`session.ready`、`session.units`、`contract.platform-bridge`（7/7，2.30 的回复）、`desktop-mode.workspace`（桌面模式开着，只读核对）通过。
- 直接查询：`cast-test` 回复 `Unsupported operation`；`desktop-mode`、`agent-screen` 的回复不再含 `fullscreen`。
- 未测：手机上的触控全屏操作（会打断用户正在用的桌面模式；无头系统测试 `desktop_mode_fullscreen` 覆盖窗口层级）；电视投屏（需要电视）。

**会话就绪误报**（§19.4 记过两次）：部署的重启步骤又报 “Desktop did not become ready”，桌面其实已在 6 秒内就绪。原因：`system/rungic-plasma` 用 `pidof kwin_wayland`/`pidof plasmashell` 记下重启前的进程，要求就绪时“全是新进程”；而 0 号（桌面模式）和其他工作区各有一个 KWin 和 plasmashell，会话重启时它们照常运行，条件永远不成立。改为只看会话自己的两个用户单元 `plasma-kwin_wayland.service`、`plasma-plasmashell.service` 的主进程。手机上的控制器换成新版（旧版存为 `/data/adb/rungic-plasma/rungic-plasma.before-20261003`，与仓库 HEAD 的旧版哈希相同），`rungic_plasma.py restart-session` 在工作区 0、1 都在运行时 6.7 秒报告就绪。控制器属于发布清单的 Android 侧文件，没有开发覆盖机制，下一次正式发布会按清单部署同一文件。


## 2026-10-07：私有 portal 激活与 Wayland 就绪（离线修复）

第一轮 G100 的三次启动均出现工作区 KDE portal 提前连接 Wayland 失败。两份完整日志显示请求来自 KWin 自身，发生在工作区脚本等待 socket 之前；只延后发布总线地址不能挡住这个请求。socket 已经监听也不能证明合成器正在处理请求。

私有总线现有的优先服务目录增加 KDE 后端激活入口。`rungic-workspace-portal` 不创建 Qt GUI，不自行请求 portal；先对该工作区地址做非阻塞连接，再等一次真正的 Wayland `sync` 回复，总时间不超过 20 秒。没有地址时不回退到用户桌面，超时或断连以非零退出并记录地址。构建时从上游 D-Bus 服务文件读取后端的实际路径和参数（不同发行版的 libexec 目录不同）。成功后 `exec` 该上游 `xdg-desktop-portal-kde`，保持原进程身份、后端参数及退出码。服务文件只安装到工作区私有目录，工作区 0 和 Agent 工作区使用它，用户主桌面的上游服务文件不变。不增加后端崩溃后的重试。

有界 roundtrip 复用呈现器已有机制，提取到 `wayland-ready.h`，使用单调时钟、处理读写错误并在成功或失败时释放 callback；呈现器保留原来的超时参数。连接等待仅针对地址未提供服务，不是对 portal 崩溃的恢复。20 秒留在 D-Bus 默认激活超时之内。

上游查阅：[Wayland Client API](https://wayland.freedesktop.org/docs/html/apb.html)、[KWin v6.6.6 main_wayland.cpp](https://github.com/KDE/kwin/blob/v6.6.6/src/main_wayland.cpp)。本机协议库为 Wayland 1.24.0，MIT；KWin 源码 GPL-2.0-or-later。保留上游 portal，不改 KWin、Qt 或 portal 的实现。单纯 `test -S`、固定等待和崩溃重试均未采用。

本地回归使用真实 libwayland 服务和 D-Bus：先建 socket、暂停服务分发，此时激活不能启动后端；允许分发后才执行。还检查地址延迟出现、无响应超时、断连、无地址、后端缺失、参数/PID/退出码，以及私有 D-Bus 激活名称。后端为最小测试进程，不将这些结果写成 KDE portal 已通过。`tools/system/tests/workspace_portal.py` 另外启动真实工作区 KWin 和上游 KDE portal，连续三次检查其私有总线名称与实际可执行文件；它是软件渲染证据，不能代替手机 GPU 和整机三次启动验收。


本轮开发验证结果：本地工作区回归 13 项通过。Mac mini 的现有隔离系统镜像 `rungic-system:e14d334e6523` 上，新 portal 系统检查（35.8 秒）及既有 `workspace_headless`（46.3 秒）通过。portal 连续三轮均取得私有总线名称，名称所有者执行文件为上游后端，早连失败签名为零；既有输入、无障碍、声音及用户会话独立性也通过。原件在 `.work/system-tests/20261007-061856/`，工作树归档摘要 `86fc9d803a5e`。首次固定 libexec 路径导致测试失败的原报告 `.work/system-tests/20261007-061626/` 保留。当前实际设备和原候选未改动。


### Portal 构建边界

私有总线的模板采用 `CMAKE_INSTALL_FULL_LIBEXECDIR`，与 helper 的 GNUInstallDirs 安装目录一致。
上游激活命令从构建环境的 KDE portal service 文件读取，因此必须在目标发行版环境中原生构建，
构建容器与目标 rootfs 使用同一发行版及同一 `xdg-desktop-portal-kde` 版本。
当前打包入口是 Ubuntu 26.04 ARM64 原生容器；模拟 ARM64 执行也仍是目标环境内的原生构建。
CMake 对交叉编译直接报错，避免把宿主的 portal 路径装进不同的目标系统。
20 秒就绪上限小于 D-Bus 默认 25 秒激活超时，留出后端 exec 和名称注册时间。
