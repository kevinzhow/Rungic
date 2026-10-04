# 系统交付、验收与诊断改进方案

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

2026-09-26。本文最初是改进方案；同日按“实施记录”一节实施，前面各节保留为方案原文，与实施不同之处以实施记录为准。基线是`origin/agent-native-debugging`（`817e4a7d`），下文55–59篇均指该分支中的文档。方案遵循AGENTS.md：优先使用Linux与发行版的标准机制，在共享层解决问题；修改后用多个独立应用交叉验收；研究结论与已验证功能分开写。

## 目标

1. **可重建**：容器rootfs上属于本项目的每一项修改都有版本，都能从仓库重新生成，并能查出它来自哪个提交。
2. **升级安全**：Ubuntu底座升级不会悄悄破坏本地修改；每次发布都能回到上一版本。
3. **回归可重复**：每次发布后自动运行交叉验收，结果可以和上一版本比较。
4. **故障可追溯**：崩溃有符号，能按签名归并，并能对应到发布版本。

不在范围内：更换发行版或改用声明式系统；修改Android全局设置；改变用户用Discover/apt安装应用的方式。

## 现状与问题

| 类别 | 现状（分支源码） | 问题 |
|---|---|---|
| vendor组件出包 | kwin用`dpkg-buildpackage`，但设了`nostrip`与`-g0`（`plasma/build-kwin.sh`）；Mesa由`package-mesa.py`手工组包，包之间有精确版本依赖；display、media组件改装Ubuntu原包（`package-display.py`、`package-media.py`）；`dpkg -i`安装后靠hold锁住12个包 | 三种出包方式并存；没有调试符号；hold只阻止升级，不表达依赖关系 |
| 热替换 | 至少8处dpkg-divert：Panel.qml、convergentwindows、录屏quicksetting、mobileshell与taskpanel插件、`libkscreenosdplugin.so`、`CompactApplet.qml`、Mesa libgallium（`build_on_device.py divert`及各`install*.sh`） | 底座升级后被覆盖的文件与新版本不匹配，只能靠文档提醒人工核对 |
| 自有程序 | 约40个文件装在`/usr/local/{bin,libexec,lib,share}`，由`build-desktop-fixes.sh`、`recording/`、`rime/`、`voice-agent/install.sh`、`deploy_plasma_diagnostics.py`等分别安装 | 没有统一清单，`dpkg -S`查不到归属，无法整体卸载或回退 |
| 系统配置 | systemd单元与drop-in、`/etc/plasma/gpu-env`、tmpfiles、APT源与Pin；部分“按本机已存配置”手工部署（42篇） | 设备状态与仓库可能不一致 |
| 用户配置 | 每次会话都改写`kscreenlockerrc`（`plasma/session`）；`display.py`持续写`plasmamobilerc`；`migrate-display.py`自带标记 | 强制覆盖用户选择；迁移机制是自制的 |
| 本机配置与凭据 | `/etc/profile.d/proxy.sh`、`/etc/plasma/audio-cookie`、`/etc/moto-plasma/account.json`、语音agent的API Key | 必须保持不入包、不入仓库，但目前没有清单说明它们应当存在 |
| 完整性 | 55篇偶然发现`/`、`/usr`属主为UID1000 | 没有例行的漂移检查 |
| 崩溃 | 容器内能取得core，由`moto-coredump-collect`用gdb回溯（55篇P1已验收） | 库帧大多是`??`；报告与发布版本无关联；同一问题无法归并 |
| 验收 | MCP、AT-SPI、perfetto追踪与证据包已具备（55篇） | 验收仍按次手工组织；48、50篇有大量待验收项 |
| 协作 | `main`落后活跃分支44个提交 | 容易按过时的基线做判断 |

## 总体结构

```text
Ubuntu 26.04底座（官方源，apt管理）
  └─ moto本地APT仓库（+motoN重建包与moto-*新增包，版本精确耦合）
       └─ 发布元包 moto-plasma-release（版本=一次发布，记录git提交）
本机配置（代理、音频cookie、账户、API Key）：不入包，由清单声明
用户数据与配置（/home）：不随系统回滚
```

## 1. 包与本地仓库

**包的划分**

| 类型 | 包 | 说明 |
|---|---|---|
| 替换型 | kwin、mesa、kscreen、plasma-mobile、qtmultimedia、libcamera等 | 版本为“Ubuntu版本+motoN”；vendor中补齐Ubuntu对应的`debian/`，按vendor规则先提交上游导入，再提交本机修改。目前被divert的文件回到所属包内重建 |
| 新增型 | `moto-plasma-session`（init、会话、单元与drop-in）、`moto-plasma-bridges`（platform、媒体、剪贴板、网络、亮度、显示）、`moto-plasma-input`（Rime与键盘）、`moto-plasma-recording`、`moto-plasma-diagnostics`（core采集、a11y、tracing）、`moto-voice-agent`、`moto-plasma-config`（`/etc/xdg`默认值与`kconf_update`脚本） | 取代现在的`/usr/local`与各安装脚本；文件装到`/usr`或`/etc`的标准位置 |
| 第三方二进制 | `moto-codex`（封装Codex官方发行包，构建时核对SHA256） | 保留59篇的版本目录与切换方式 |
| 发布 | `moto-plasma-release` | 以`=`依赖本次发布的全部包；在`/usr/share/moto/release.json`记录git提交与构建信息 |

**版本耦合**

- 与底座存在ABI或QML耦合的包，对相关Ubuntu包写精确依赖，例如插件依赖`plasma-workspace (= …)`。
- 底座想升级时，apt会拒绝或要求一起升级，这取代hold和“记得重新divert”。

**仓库**

- 在构建侧用`apt-ftparchive`生成文件型仓库，同步到设备后以`file:`源提供。
- 按origin设Pin，优先级1001，使我们的版本优先，并允许回退降级。
- 仓库保留最近几次发布的旧版本。

**调试符号**

- 改为带调试信息构建，交给debhelper自动生成`-dbgsym`包（Ubuntu为`.ddeb`），放在同一仓库，按需安装。
- 为控制手机上的编译内存与时间，先评估`-g1`（函数名与行号表）是否够用于回溯。

**开发迭代**

- `build_on_device.py divert`继续作为开发期的快速手段。
- 发布前的完整性检查若发现本项目的divert仍存在，就判定为“开发态”，不能作为发布状态。

## 2. 配置与迁移

| 类别 | 机制 | 首批对象 |
|---|---|---|
| 系统默认值 | `/etc/xdg/*rc`，KConfig级联，由`moto-plasma-config`以conffile安装；用户修改自然覆盖默认值。只有必须锁定的项才用`[$i]` | `kscreenlockerrc`：改为默认值，不再每次会话都写 |
| 用户一次性迁移 | `kconf_update`的`.upd`（`Version=6`），由kded6在启动和发现新文件时执行，按用户记录在`kconf_updaterc`（已核对KF6 6.24的`kded.cpp:50,527,586`） | `migrate-display.py`改写为kconf_update脚本，兼容已有的标记 |
| 系统迁移 | 包的maintainer脚本，用`dpkg --compare-versions`判断版本 | 随包变动逐项加入 |
| 运行时硬件值 | 仍由共享服务维护，例如挖孔与面板高度 | `display.py`；在文档中说明这不是用户偏好 |
| 本机配置与凭据 | 不入包；`.work/device/`中的清单只声明路径与属主，不保存内容；部署时只检查是否存在 | 代理、音频cookie、`account.json`、API Key |

## 3. 发布与部署

主机侧增加一个统一入口，名称待定，例如`tools/rungic_release.py`，经`rungic_device.py`访问设备：

- `build`：出包，生成仓库索引，写入`.work`；
- `deploy <版本>`：按下面的步骤部署；
- `rollback`：回到上一个发布；
- `status`：当前发布、git提交，以及和仓库是否一致。

`deploy`的步骤：

1. **预检**：容器运行中；没有未完成的dpkg操作；可用空间足够；完整性检查通过，或把差异记录下来。
2. **记录**：当前发布与`dpkg -l`写入`.work/deploy/<时间>/`。
3. **同步与安装**：同步仓库，在`systemd-run`与锁内执行`apt-get install moto-plasma-release=<版本>`。
4. **迁移**：随包执行，用户级迁移在下次会话由kded执行。
5. **重启**：按包触发器的要求重启会话。
6. **复核**：完整性检查与冒烟验收。
7. **保存**：全过程运行记录。

回滚：`apt-get install --allow-downgrades moto-plasma-release=<上一版本>`，回到本项目各包的上一版本。Ubuntu底座包的回退取决于旧版本是否仍能取得，完整回退依赖第7节的快照。

底座更新策略：Ubuntu更新只经发布流程进入；须确认容器内unattended-upgrades的状态（未核验）。

## 4. 完整性与漂移检查

以只读MCP工具提供，部署前后各运行一次，也可以随时调用，输出JSON：

- `dpkg --verify`：包内文件被改动或缺失；
- `dpkg-divert --list`：本项目的divert（开发态）；
- `/usr`、`/etc`中不属于任何包、也不在本机配置清单里的文件；
- `/`、`/usr`、`/etc`、`/var`的属主与模式异常（例如UID1000事件）；
- 实际安装的版本与发布元包的依赖是否一致。

## 5. 发布后自动验收

- 场景用数据文件描述，每个场景包括前置条件、步骤（调用现有MCP与`rungic_agent`能力）、判定和证据。
- 结果写入`.work/acceptance/<发布>/<时间>/`；性能指标与上一发布比较。
- 分三级：
  - 冒烟：每次部署运行，目标5分钟内；
  - 完整：发布候选运行；
  - 人工：明确列出，不由自动结果替代。

| 能力 | 自动判定 | 仍需人工 |
|---|---|---|
| 会话 | 重启会话后KWin与plasmashell就绪；期间无新崩溃签名 | — |
| 显示 | `kscreen-doctor`应用并撤销模式、缩放与刷新率；截图尺寸与Android报告一致 | 视觉瑕疵 |
| 输入 | AT-SPI聚焦文本框；Rime提交中文后读回文本 | 候选界面体验 |
| 相机 | 经PipeWire取N帧，帧内容不是纯色、时间戳单调；停止后设备和`capture.sock`客户端已释放 | 画质 |
| 声音 | 播放流进入目标sink；录音源有非零电平；停止后sink与source恢复空闲挂起 | 实际声学效果 |
| 录屏与编解码 | 用AT-SPI按下录屏按钮；ffprobe核对时长、帧率、音轨；GStreamer与FFmpeg硬件编解码路径各测一次 | 音画同步主观检查 |
| 性能 | 沿用`kwin_pipeline_run.py`、`compbench_run.py`、帧统计，与上一发布设定阈值比较 | — |
| 投屏 | — | 需要真实接收端（58篇） |

每个场景结束时恢复现场，包括无障碍开关、显示模式和亮度，只使用可恢复的操作。

## 6. 崩溃诊断链

1. **标准格式**：采集脚本在现有文本行之外，再写一条systemd-coredump格式的journal条目：
   - `MESSAGE_ID=fc2e22bc6ee647b6b90729ab34a250b1`、`COREDUMP_PID/UID/SIGNAL/EXE/COMM/CMDLINE/TIMESTAMP`，以及指向`core.zst`的`COREDUMP_FILENAME`；
   - 依据：systemd v259的`coredumpctl.c`只按MESSAGE_ID选取条目（:116），按`COREDUMP_FILENAME`读取任意路径的core，并支持`.zst`（:999-1015）；
   - 这样`coredumpctl list/info/debug`可以直接使用。
   - 需要安装systemd-coredump包：
     - 要屏蔽它的`sysctl.d/50-coredump.conf`，不能依赖容器的`/proc/sys`只读来防止修改Android全局`core_pattern`；
     - 还要核对它与apport-core-dump-handler能否共存（未核验）。
2. **关联信息**：`info.json`增加发布版本、exe与各库的build-id，以及崩溃签名（符号化后前几帧的规范化结果；无符号时用“模块+偏移”）。
3. **符号化**：本项目的包用第1节的`-dbgsym`；Ubuntu的包优先使用ddebs仓库，须核对arm64是否可用；debuginfod直连慢、经代理超时（55篇），只作后备。gdb开启debuginfod时要用`-iex`。
4. **归并**：MCP按签名给出次数、首次与最近出现时间、涉及的发布，并能筛选“某发布之后新出现”的签名，用于判断回归。
5. **不装drkonqi**：KCrash 6.24在`core_pattern`不是管道且找得到drkonqi时，会改走DrKonqi实时调试并以`_exit(253)`结束（`kcrash.cpp:207`、`:753-755`），KDE程序将不再留下core。现在正因为没有drkonqi，才由`raise(sig)`交给内核写core（`:620-626`）。确需DrKonqi时，必须同时设置`KCRASH_DUMP_ONLY=1`。
6. **隐私**：
   - 持有凭据的服务（语音agent、codex app-server）在单元中设`LimitCORE=0`；
   - core只留在设备和`.work`，给云端agent的只有回溯与摘要。

## 7. rootfs镜像与快照（后置）

放在1–4之后实施：只有状态已知、可重建，回滚才有意义。

**先用ext4镜像**

- 做法：rootfs迁入`/data/adb`下的稀疏ext4镜像，经loop挂载，挂载只在Plasma的mount namespace内生效；SELinux仿照Docker的专用镜像类型（19篇）。
- 好处：不需要改内核，还能避开casefold F2FS对OverlayFS的拒绝。
- 回滚：内核已有`DM_SNAPSHOT=y`，可以做单个升级前快照，并用snapshot-merge回滚。
- 独立存放：`/home`、core目录（`/data/app_dump`、`/var/lib/moto-cores`）和本地仓库放在单独的镜像或目录，不随系统回滚。

**btrfs，满足条件后再做**

依据是已读的`android15-6.6-2025-05_r16`源码：

- `gki_defconfig`没有btrfs，且`BUILD.bazel`设置`trim_nonlisted_kmi = True`，另编模块无法加载，只能编进自编Image并重刷boot；
- btrfs会`select BLK_CGROUP_PUNT_BIO`，这会改变`struct blkcg_gq`（`block/blk-cgroup.h:76-79`），可能改变块层导出符号CRC，使原厂vendor模块无法加载；
- 候选修复：去掉该select，并在`fs/btrfs/bio.c:468-471`未启用时直接`submit_bio`；
- 前提：与当前boot相比KMI符号CRC零变化，441个原厂模块全部加载，性能对照可以接受。

btrfs的收益是多快照、廉价克隆的测试容器、send/receive增量备份以及压缩。

**从零重建**

有了第1节的仓库，用mmdebstrap按“Ubuntu包清单＋发布元包”生成rootfs镜像，用于空白机安装和K8上的可复现构建。

## 8. 协作

- 功能分支验收后尽快合入`main`，发布打tag；发布元包记录的提交SHA与tag对应。
- 开始调研前先执行`git fetch`并查看活跃的远端分支；是否写入AGENTS.md由用户决定。

## 分阶段计划

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 准备（不改设备） | 合入基线分支；在设备上只读执行`dpkg -S`等命令，生成现有文件清单并分类；决定仓库信任方式；测量`-g1`对kwin、Mesa构建时间与内存的影响 | 清单覆盖第3节所列全部安装脚本的产物 |
| 1 仓库与发布骨架 | 本地仓库；把现有kwin、Mesa、display、media的deb原样纳入发布元包；完整性检查工具；`deploy/rollback/status` | 从仓库安装发布后`dpkg -l`与现状一致；回滚与再部署成功；完整性检查只报出已知项 |
| 2 自有程序与配置入包 | `/usr/local`、单元、drop-in改为moto-*包；`kscreenlockerrc`改为默认值；`migrate-display`改为kconf_update | `dpkg -S`能找到本项目全部文件的归属；冒烟验收通过；用户修改在重新部署后保留 |
| 3 消除divert | 为被divert的组件补齐`debian/`并重建；libgallium并入Mesa包 | `dpkg-divert --list`没有本项目项；模拟底座升级时apt拒绝或一起升级 |
| 4 崩溃诊断链（可与1–2并行） | 标准格式、dbgsym、签名归并、发布关联、`LimitCORE=0` | KDE程序、非KDE程序和KWin的可控崩溃都能在`coredumpctl`与MCP中一致出现；自建组件帧已符号化；持有凭据的服务不产生core |
| 5 自动验收 | 冒烟与完整两级场景、与上一发布比较 | 连续两次发布得到可比较的报告；人为注入的回归能被判定为失败 |
| 6 rootfs镜像与快照 | ext4镜像与dm-snapshot；btrfs按第7节的条件决定 | 迁移前后验收一致；升级失败后可回到升级前状态；性能对照记录在案 |

每一阶段的证据写入`.work`，实施后同步更新40、42、55篇与README。

## 风险与待定

- **手机上的构建成本**：完整打包plasma-mobile、Mesa、kwin（带调试信息）的时间和内存需要实测。K8若为x86主机，需要另建ARM64构建环境。
- **安全更新延迟**：精确依赖会让相关Plasma包的安全更新必须等我们重建后才能进入，这是有意的取舍，需要约定重建节奏。
- **仓库信任**：root所有的本地`file:`源加`trusted=yes`，还是用本地密钥签名。密钥规则见AGENTS.md，目前只有APK签名密钥允许同步。
- **未核验项**：systemd-coredump与apport能否共存、ddebs的arm64可用性、unattended-upgrades状态、容器内`/proc/sys`在各启动路径下是否都只读。
- **自动验收的边界**：画质、声学效果、投屏无法自动判定，保留人工清单。
- **存储占用**：调试符号、旧版本和core都占空间，需要设置保留上限并纳入完整性检查。

## 实施记录（2026-09-26）

以下是在`agent-native-debugging`上实施的结果。“已验证”指在手机上实际运行过；其余写明未验证或未完成。

### 核对方案的现状判断

实施前用只读命令核对了设备（`moto-integrity`首轮结果与手工检查），与“现状与问题”一节不一致之处：

| 方案所写 | 实际 |
|---|---|
| hold了12个包 | 只hold了8个（Mesa 7个与plasma-camera）；kwin `+moto17`、kscreen、libcamera、qtmultimedia都没有保护 |
| unattended-upgrades状态未核验 | 已启用，允许`resolute`与`-security`，黑名单为空，9-24/25自动升级过xdg-desktop-portal、NetworkManager、sqlite。Ubuntu若发布kwin安全更新（`0ubuntu0.2`高于`0ubuntu0.1+moto17`），会被静默替换 |
| 至少8处divert | 13处本地divert（`install-mobile-plugins.sh`新增5处） |
| `/usr/local`约40个文件 | 5024个（moto-clicker venv与私有FFmpeg为主）；另有自有文件放在dpkg管理的`/usr`（quicksettings、用户单元、D-Bus服务、desktop文件） |
| ddebs的arm64未核验 | 可用：`resolute`与`resolute-updates`都有arm64；ddebs没有`-security`套件 |
| systemd-coredump与apport能否共存未核验 | apport.service带`ConditionVirtualization=!container`，容器内不启动；systemd-coredump只与`core-dump-handler`冲突，可共存 |
| 容器内`/proc/sys`只读 | `/proc/sys`只读；`moto-plasma-init`只把`/proc/sys/user`重新挂为可写（Flatpak需要），`kernel.core_pattern`不受影响 |
| `dpkg --verify`可直接作完整性检查 | 直接运行有35540行噪声（最小化镜像的`path-exclude`）；按dpkg过滤规则去掉后只有2项 |
| `main`落后44个提交 | 实施时落后117个 |

### 已实施

**P0（已验证）**：`plasma/config/etc/apt/apt.conf.d/51moto-unattended-upgrades`把重建、被divert和Qt私有ABI相关的包列入unattended-upgrades黑名单，其余包保留自动安全更新（dry run只升级curl）。语音agent单元`LimitCORE=0`，其子进程codex app-server实测core上限为0。

**完整性检查（已验证）**：`plasma/diagnostics/moto-integrity`与MCP工具`integrity`，按方案第4节检查，另加本机配置清单（`moto-local-config.json`：只有路径、属主、权限）、systemd mask、崩溃链前提（DrKonqi须配`KCRASH_DUMP_ONLY`、coredump sysctl已屏蔽、持凭据单元`LimitCORE=0`）。首轮发现的13个缺失的zh_CN翻译（语言包含规则晚于这些包安装）和一个被改动的`wl-paste.fish`已通过重装修复。

**崩溃诊断链（已验证）**：
- `moto-coredump-collect`记录发布版本、可执行文件所属包、各模块build-id（`eu-unstrip`）和签名（崩溃线程越过信号、abort、Qt fatal、KCrash、syscall-cancel转发帧后的前5帧；有符号时用函数名，否则模块+偏移；内联帧归入调用者模块）。
- 每条报告同时以systemd-coredump格式写入journal：`coredumpctl list/info/debug`直接读取`/var/lib/moto-cores/*/core.zst`（kalk与sleep的可控SIGSEGV实测）。
- core每个签名最多保留2个：kaccess的abort循环（两天188次）曾挤掉其他崩溃的全部core。
- `moto-crash-symbols`按精确版本安装报告需要的`-dbgsym`（ddebs经单独的源文件，日常`apt update`不取其索引；libc6回退到`-dbg`），重做回溯与签名。kaccess报告从`libc+0x8e0fc/libQt6Core+0xe3f5c`变为`init_platform < createPlatformIntegration`。
- 2026-09-27修订：符号化移出手机。在容器里用gdb加载桌面程序的调试信息要数GB，而容器没有内存上限：两次手机上的符号化，以及之后崩溃时自动采集的gdb加载了这些装在手机上的1.7 GB调试包，都让Android的低内存查杀杀掉了VPN与Plasma APK。现在由`tools/rungic_crash_symbolize.py`只从手机取报告和崩溃时映射的那些文件（手机上只做`dpkg-query`与`tar`），在构建机（Mac mini，`build_on_device.py --host macmini`的容器）上组成sysroot，解开精确版本的`-dbgsym`（本项目的取发布仓库，Ubuntu的取ddebs并缓存在构建机的卷里），用手机自己的`rungic-coredump-collect`跑gdb，只把`backtrace.txt`与`info.json`写回手机。手机上的`rungic-crash-symbols`默认拒绝运行（`--on-phone`时限1 GiB），崩溃时的采集服务限768 MiB，手机上不再保留调试包。gdb改为每帧都打印地址（`set print frame-info location-and-address`），有符号时内联帧才能归到正确的模块。
- 安装systemd-coredump时先屏蔽其`50-coredump.conf`；实测`core_pattern`未变。MCP：`crash_groups`（次数、首末时间、所属发布、`new_in_release`）、`crash_symbolize`。

**发布、部署与回滚（已验证）**：`tools/rungic_release.py`（仓库、元包、`deploy`/`rollback`/`status`）与`tools/rungic_acceptance.py`。
- 仓库：`.work/apt/repo`，apt-ftparchive索引，origin `moto`、label `moto-plasma`；设备`/var/lib/moto-apt`，`file:`源`Trusted: yes`（仅root可写），pin 1001。选用`trusted=yes`而非本地签名：AGENTS.md只授权同步APK签名密钥。
- 元包`moto-plasma-release=<YYYYMMDD.N>`：对`plasma/release/packages.json`中的全部包精确依赖，含`/usr/share/moto/release.json`（git提交）。
- 保护已安装的发布（2026-09-28）：
  - 原来只有“仓库整体 pin 1001，加上元包的精确依赖”。仓库为回滚保留了很多历史版本（比如已不属于发布的 kscreen +rungic1），它们在 1001 下同样可能成为候选；Discover 为了完成升级，也可以把元包卸掉，之后精确依赖就不再约束任何东西。那天用户在 Discover 里看到我们定制的包“有更新”，直接原因是测试时绕过发布、用 `dpkg -i` 装了新版本，apt 于是想把它们换回发布版本。
  - 现在：仓库整体 pin 降到 100；每次部署成功后，写入 `/etc/apt/preferences.d/rungic-release`，把发布里的每个包（包括元包）钉在精确版本上，优先级 1001；元包带 `Protected: yes`，apt 不再允许顺带卸掉它。
  - 部署和回滚都按 `包名=版本` 显式安装，并带 `--allow-downgrades`，不受这些优先级影响。
  - 不在发布清单里的 Ubuntu 包，仍然可以在 Discover 里正常更新。
  - 2026-10-04 补充（[docs/109](109-dev-release-channel.md)）：实机只读核对和真实 apt、unattended-upgrades 的离线模拟确认这套 pin 有效，元包被删后 pin 仍然有效；补上两处缺口：与耦合包同源的已装兄弟包（plasma-workspace 的私有库）按已装版本一起 pin，unattended-upgrades 另有按发布生成的精确名单（`52rungic-release`）。G100 S 只读核对：`apt-cache policy` 中发布包的已装版本都是 1001 的候选，`apt-get -s dist-upgrade` 不动任何发布包或耦合包，容器里的 unattended-upgrades 每天运行、从未升级过发布包；plasma-workspace 的私有库（libtaskmanager6 等）优先级 500，没有 pin。离线测试 `tools/test_rungic_release_pins.py`。
  - 测试包应当收进发布仓库、随发布部署，不要直接 `dpkg -i` 到日常使用的手机上。本地开发改用 `tools/rungic_dev.py` 的开发覆盖：有自己的仓库和 pin，状态里可见，可以一条命令撤销（docs/97，2026-09-30）。
- 部署：预检、记录dpkg状态与完整性、同步仓库、在transient unit中安装（**每个包都带精确版本**：apt不会为满足依赖自动降级，回滚需要这一点）、安装成功后再同步发布中的Android侧文件（失败时两侧都停在上一版本）、解除被发布取代的hold、按需重启会话、完整性与冒烟验收、记录到`.work/deploy/`。
- 实测：20260926.1（现有18个重建包原样入库）部署；20260926.2（kwin moto18）部署并重启会话；`rollback`回到.1（实际降级kwin并重启会话）；20260926.3部署结果`ok`。

**冒烟验收（已验证，8项约70秒）**：会话（KWin与plasmashell的PID稳定且plasmashell运行≥15秒）、关键用户单元、部署期间的新崩溃签名（已知签名单列）、KScreen与Android尺寸一致、Android文字输入抽屉搜索（OCR读回：会话刚重启时结果视图不进入AT-SPI树，搜索框也不暴露文本）、摄像头帧（数量、PTS递增、非恒定填充、停止后节点空闲；最长帧间隔作为指标）、播放进入默认sink并恢复挂起、录音电平与挂起。

**完整验收（已实现）**：应用启动、KScreen缩放应用与撤销、硬件编解码往返（motoh264enc编码、私有FFmpeg `h264_moto`解码：90/90帧、1280x720、3.0秒）、快捷设置录屏、Rime（引擎提交中文首选；文本框获得焦点后键盘出现）、合成器时序（`kwin_pipeline_run.py`，paint或呈现间隔p95较上一发布劣化超过15%即失败）。

**打包（P3/P4）**：`tools/rungic_package.py`从`plasma/packaging/<名称>/`构建14个自有包（主机构建的可复现，设备构建的用dpkg-shlibdeps并按build-id拆出`-dbgsym`），`tools/vendor_debian.py`把Ubuntu打包导入vendor（先单独提交导入，再提交`+moto`变更）。
- 新增包：moto-plasma-config、-session、-bridges、-input、-recording、-diagnostics，moto-voice-agent、moto-cua（可迁移的venv）、moto-agent-screen、moto-cast、moto-codex（官方包，固定SHA256）、moto-codec（私有FFmpeg在`/usr/lib/moto-codec`）、moto-firefox（包级divert `/usr/bin/firefox`）、moto-snapshot（Ubuntu无snapshot包）。
- 重建包：plasma-mobile `+moto2`（含录屏快捷设置，preinst接管原有的本地divert）、plasma-settings、plasma-keyboard、xdg-desktop-portal-kde `+moto1`、wl-clipboard `2.3.0-0+moto1`、KWin `+moto19`（`-g1`与dbgsym）、Mesa `+moto2`（libgallium回到包内，preinst接管divert）。
- `/usr/local`→`/usr`：单元、D-Bus、desktop文件（KWin按可执行路径授权）、QML、脚本、Android侧`moto-plasma`与LXC init路径同步修改；用户设置中的旧路径由kconf_update迁移（kwinrc输入法、Codex MCP命令与技能链接、每用户portal覆盖与单元链接），`plasma/session`在KWin启动前运行kconf_update。
- 配置：`kscreenlockerrc`改为`/etc/xdg`默认值（`[$i]`），会话不再每次写入；portal通知默认值；`migrate-display.py`改为kconf_update脚本（兼容原标记）；快捷设置磁贴插入改为kconf_update（没有列表的用户保持Plasma Mobile默认）。
- 取得的源码：三个probe源码、`dev.moto.Platform.desktop`、journald与PipeWire配置、deb-src列表此前只存在于手机，已收入仓库。
- `-g1`成本（plasma-mobile完整构建，LTO）：墙钟14分钟、CPU 52分钟，dbgsym 3.7 MB。

### 实施中发现的问题

- **kaccess abort循环**（已修复）：plasma-desktop 6.6.6的`kaccess/main.cpp`写死`QT_QPA_PLATFORM=xcb`（注释：只与X有关，也为Xwayland应用服务）；会话的KWin不带`--xwayland`，没有`DISPLAY`，Qt连不上显示即abort（KCrash初始化之前），`plasma-workspace.target`拉起的`plasma-kaccess.service`带`Restart=on-failure`，每次会话启动崩溃5–6次后被systemd放弃（签名`f6f8756afbd8`；9-25为30个core，9-26因反复部署达165个）。它的功能是X11的XKB辅助功能（Wayland下由KWin的StickyKeys/SlowKeys/BounceKeys/MouseKeys插件提供）、屏幕阅读器快捷键Meta+Alt+S与登录时启动Orca（崩溃在注册之前，从未生效；Orca未安装）。moto-plasma-config加入`plasma-kaccess.service.d/moto.conf`：`ConditionEnvironment=DISPLAY`，没有X显示时跳过（状态inactive、结果success，目标仍active），将来Xwayland导出`DISPLAY`后自动恢复；不用mask。已知签名随之从`acceptance.json`删除，再出现即验收失败。剩余：`kcm_access`保存时直接`startDetached("kaccess")`，绕过systemd仍会崩一次；手机版设置默认只列handset模块，该KCM未声明FormFactors且`NoDisplay=true`，只有手动运行`systemsettings kcm_access`才会遇到。
- **桌面用户服务的轻量重启**（2026-09-30，用户要求部署后自动生效）：此前只有系统服务（`service_restart`）与整个会话（`session_restart`）两档，建议组件、语音助手的新 QML/代码要等手动重启 `rungic-suggestions`、plasmashell 才出现。`release/packages.json` 新增 `user_restart`：包变化时在桌面用户会话中重启其列出的、正在运行的用户单元（plasmashell 排最后，它加载其他包的 QML 插件与小组件），列出的程序路径则退出其正在运行的实例（助手 App，下次打开即新代码）；未运行的单元不启动。已触发整个会话重启时跳过这一档，`--restart never` 也跳过。回滚到没有此设置的旧发布时使用仓库当前的设置。
- **会话重启就绪误报**（已修复）：`moto-plasma restart-session`在旧KWin/plasmashell退出前就判定就绪，且`set -e`下两次会话之间的空`pidof`使脚本退出；现在要求新PID。
- **私有FFmpeg命令行不可用**（moto-codec已修复）：rpath只含`/usr/local/lib/moto-codec`，工具加载了系统libavformat。
- **录屏收尾超时**（2026-09-27已修复，发布`20260927.7`，原因与验证见[48篇](48-plasma-media-pipelines.md)末节）：停止后`Timed out finalizing recording`，只留下`.partial.mp4`；`~/Videos`已有多组此类残留。本次在手机编译负载下测得，空闲时的表现待复测。
- **用户单元只为首个用户启用**：display、brightness、media、clipboard在`~/.config`中启用；包改为全局启用，kconf_update清理旧链接。
- **升级时误删启用链接**（已修复）：旧版生成的postinst把单元启用链接列为`obsolete`，每次升级都删一遍；运行中的会话掩盖了问题，直到容器重启后会话没有起来。现在按deb-systemd-helper的方式启用（记录`was-enabled`，不覆盖用户的禁用），`obsolete`中的符号链接只在悬空时删除；`moto-integrity`的`units`项检查各包`/usr/share/moto/units/*.list`中的单元是否仍启用。
- **容器启动后的第一次Android文字输入大小写错误**（未修复，已记录）：容器刚启动时，第一次`input text "Calcul"`偶尔以`CaICUL`之类的大小写到达。原因未确认（疑为Android输入法与修饰键状态）。验收的OCR读回改为不区分大小写并记录`case_exact`；验收失败后自动重试一次，重试通过的记为`flaky`。
- **应用抽屉网格空白**（已修复；2026-09-27查明写入者：plasmashell在OpenGL启动失败时写入，见docs/73“发布20260927.9的部署验收”，会话启动时已自动清除）：失败的20260926.11部署后，抽屉的搜索框与面板正常，但应用网格不绘制（AT-SPI树中有各项名称与坐标）。原因是用户`~/.config/kdeglobals`中出现了`[QtQuickRendererSettings] SceneGraphBackend=software`，plasma-integration据此让QtQuick改用软件渲染；删除后重启plasmashell即恢复。该文件在09:47:32与`plasma-org.kde.plasma.desktop-appletsrc`（桌面版shell的文件）、`kglobalshortcutsrc`以及`user-session-migration/(null).state`（`XDG_CURRENT_DESKTOP`为空）一同写入，当时该次部署的plasmashell没有起来。系统程序中只有plasma-integration平台主题（只读）和`kcm_qtquicksettings`含这个键，仓库与历史中都没有写入它的代码。当时的journal已随快照回滚丢失（此后部署失败时先保存证据包再回滚）。`moto-integrity`新增`user_overrides`检查，报告这一项以及用户`environment.d`中的`QT_QUICK_BACKEND`、`QSG_RHI_BACKEND`、`LIBGL_ALWAYS_SOFTWARE`。
- **旧版本的崩溃被算到新版本**（已修复）：20260926.13部署时，建快照的重启仍运行.12，kaccess在那时崩溃5次；收集器逐个处理core（每个约35秒），安装.13后才写完报告，并读取当时的`release.json`，把这些崩溃记为.13。验收又从部署开始统计新崩溃，于是`crash.new`失败并回滚（证据包显示安装后`plasma-kaccess.service`已因`ConditionEnvironment=DISPLAY`被跳过）。现在验收从安装完成时统计；`moto-coredump-collect`比较崩溃时间与`moto-plasma-release`的安装时间（dpkg `.list`的mtime），早于安装的记为`version: null`并注明`before`。
- **家目录不随快照回滚**：这是有意的设计，但失败部署期间写入用户配置的内容会保留下来，上一条就是这种情况。排查部署后的异常时，要检查部署时间段内修改过的`~/.config`文件。
- **KWin构建成本**：`-g1`完整构建37.3分钟，libkwin6的dbgsym为8.8 MB；Mesa 7.4分钟。私有FFmpeg的库没有调试信息（未处理）。
- **前置摄像头冷启动**：第一次打开时出帧延迟可达数秒，验收按每帧10秒超时并记录`first_frame_s`（未修复）。

### P6 rootfs镜像与快照（已验证）

rootfs从目录迁入ext4镜像，升级前自动建立dm-snapshot；btrfs按第7节的建议暂不实施（需要重编内核并核对KMI）。

- **布局**：`/data/adb/moto-lxc/runtime/var/lib/lxc/plasma/images/rootfs.img`（稀疏ext4，160G上限）与`rootfs.cow`（快照存在时的COW，32G上限），`images/state`为`none|snapshot|merging`。`/home`、`/var/lib/moto-cores`和`/var/lib/moto-apt`放在`state/`下以bind mount挂入，不随系统回滚；Android侧写给容器的文件（音频cookie、共享内存标签、账户）放在`state/host`，挂到`/var/lib/moto-host`。
- **Android侧工具**：`plasma/rootfs-image`（`status/attach/detach/snapshot/rollback/commit/migrate`），只用Android自带的losetup、mke2fs、e2fsck和dmctl。LXC挂载的总是dm设备`moto-plasma-root`：平时是linear，快照时是snapshot-origin（另有只读的`moto-plasma-before`视图），回滚时是snapshot-merge，合并完成后下次attach回到linear。
- **SELinux**：内核loop worker以`u:r:kernel:s0`运行，读不了`adb_data_file`，loop设备会返回I/O错误。仿照docker的做法，`plasma/rootfs.sepolicy.rule`定义`moto_plasma_image`，镜像文件打上这个标签，只允许kernel访问这类文件。
- **LXC接入**：LXC的存储后端不接受普通块设备，所以配置里仍写目录，由`lxc.hook.pre-mount`（`plasma/rootfs-mount-hook`）在容器的mount namespace中把dm设备挂到该目录。`moto-plasma start`在镜像模式下先attach，`stop`后detach。
- **踩过的坑**：toybox losetup只接受64字节以内的路径，且默认autoclear；`mount -o context=`被拒绝，改为给镜像根打标签；`snapshot-merge`状态要读`dmctl`输出的最后一行（第一行是表头）；最初的`migrate`在复制前移动了数据，改为先带排除项复制，成功后再移动。
- **发布集成**：镜像模式下`rungic_release.py deploy`先停容器建快照，重启后等待会话稳定再安装；验收（失败时重试一次）通过则保留快照，直到`rungic_release.py commit`；失败则先保存证据包（`.work/diag/*-deploy-<版本>-failed`，因为journal会随回滚丢失），再停容器合并快照。`rollback --snapshot`可以手动回到快照。
- **实测**：迁移前后冒烟验收一致；20260926.10与.11共3次验收失败，都自动回到快照，回滚后`moto-plasma-release`与dpkg状态为部署前的版本。容器启动到会话就绪：目录11.2秒，镜像8.2秒。顺序写：目录378–953 MB/s，镜像467–618 MB/s；顺序读：目录约585 MB/s，镜像约740 MB/s。合成器paint p95 3.415 ms、呈现间隔p95 16.7 ms，与迁移前处于同一水平。
- **保留的回退**：迁移前的目录rootfs保存为`rootfs.pre-image`（约24G），确认镜像模式稳定后再删除。迁移前的包状态备份在`.work/backups/pre-packages-20260926.tar.gz`。

### 发布记录

| 版本 | 结果 |
|---|---|
| 20260926.1 | 现有18个重建包原样入库；安装成功，冒烟验收`camera.frames`失败（摄像头冷启动出帧慢，此后改为按帧超时） |
| 20260926.2 | kwin moto18，安装并重启会话；`input.text`失败（当时验收用AT-SPI读搜索框，此后改为OCR）；随后`rollback`回到.1，实际降级kwin并重启会话 |
| 20260926.3–.6 | 自有包陆续入库，验收通过 |
| 20260926.7 | 容器重启后会话没有起来，冒烟6项失败（启用链接被升级删除）；修复后发.8 |
| 20260926.8–.9 | 验收通过；.9期间迁入镜像rootfs |
| 20260926.10–.11 | 快照部署；验收失败（首次输入大小写、plasmashell未就绪、抽屉空白），3次自动回到快照 |
| 20260926.12 | moto-integrity增加`user_overrides`；快照部署，完整性clean，冒烟验收8项一次通过（无flaky）；镜像模式下完整验收15项全部通过（录屏34.1秒正常收尾，paint p95 3.802 ms、呈现间隔p95 16.706 ms、后置21.9 fps、编码1.84秒/解码0.65秒），随后`commit`丢弃快照，rootfs回到linear |

### Linux内存上限（2026-09-27，发布20260927.14）

容器原先没有内存上限，Linux侧的内存用量会让Android的低内存查杀去杀VPN与Plasma APK。Android的内存控制器是cgroup v1（`/dev/memcg`），容器进程原先记在Android的`apps`组里。现在`rungic-plasma`启动脚本在启动容器前建立`/dev/memcg/rungic-plasma`、写入上限并把自己放进去，`lxc-start`与整个容器随之继承；超限时由内核在容器内回收或结束进程。

- 档位：最低2 GB、3 GB、4 GB（默认）、5 GB、6 GB、无上限；选择保存在`/data/adb/rungic-plasma/memory-limit`。`rungic-plasma memory-limit <档位>`即时生效，并把已在运行、尚未入组的容器进程连同其内存计费一起迁入（`memory.move_charge_at_immigrate=3`）；`memory-status`只读取状态（JSON）。
- 设置入口：Rungic设备面板“内存”分组，显示Linux已用内存、上限与峰值，下拉选择档位（APK 2.5的`container-memory`操作以root调用启动脚本）。
- Android的`sh`（mksh）只有32位整数：上限用`M`后缀写入，字节换算交给`awk`；遍历进程只用shell内建命令（每个进程调用一次grep要一分钟）。
- 验证：面板改为5 GB后内核上限为5368709120；容器重启后`lxc-start`与plasmashell都在`/memory:/rungic-plasma`，上限保持4 GB，Linux用量约1.1–2 GB。

#### swap 也计入上限、用量分开显示（2026-10-03）

- **问题**：G100 S（开机约 6.8 天）只读读取 `/dev/memcg/rungic-plasma`：上限 4096 MiB，当前 usage 1638 MiB（程序 rss 598、文件缓存 1039，其中 tmpfs 227），峰值 4096 MiB，`failcnt` 282 968；**容器已有 938 MiB 换出到 swap**，`memory.memsw.limit_in_bytes` 不限，`memory.swappiness` 100。Android 的 swap 是 zram（`/proc/swaps` 只有 zram0，压缩后仍占 RAM），换出的页不再计入 `memory.limit_in_bytes`，容器实际占用的 RAM 会超过档位。面板显示的 usage 又包含文件缓存，重度使用后接近上限，看起来像"占满了"。
- **修改**（`system/rungic-plasma`）：
  - 同时写 `memory.memsw.limit_in_bytes`（内存 + swap）：档位的 1.25 倍（`MEMORY_SWAP_SHARE=4`，4 GB 档为 5120 MiB；无上限时两者都是 -1）。内核要求任何时刻 memsw 不小于内存上限：先试写内存上限（调高时会失败，忽略），再写 memsw，最后再写一次内存上限。内核没有 swap 计费（无 memsw 文件）时只写内存上限。
  - 这个 cgroup 的 `memory.swappiness` 设为 40（Android 全局为 100），先回收文件缓存，少换出程序内存。
  - `memory-status` 增加 `swap_limit_mib`、`programs_mib`（total_rss）、`cache_mib`（total_cache）、`shmem_mib`（total_shmem）、`swap_mib`（total_swap）。设备面板在总用量下面多一行显示程序、文件缓存（其中内存文件）和 swap；旧启动脚本没有这些字段时只显示原来的一行。
- **离线验证**：`tools/tests/test_memory_limit.py` 在 sh 里对 cgroup 替身运行启动脚本的内存函数（写入值、无上限、无 swap 计费的内核、状态 JSON 与缺失 memory.stat）；`tools/tests/test_device_panel.py` 覆盖面板的两种显示。替身是普通文件，不模拟内核的写入顺序约束，**实机尚未部署验证**（档位切换的写入顺序、`failcnt` 与容器内 OOM 的变化需上机确认）。
- **实机（G100 S `ZY32MVJS25`，2026-10-04，开发部署，未发正式版本）**：
  - 控制器单独安装（开发覆盖不管 Android 侧文件）：原文件与 main 相同（sha256 `c3ec1ded…`，已备份），新文件 `318f10c6…`。`rungic-plasma memory-limit` 按原 4 GB 档重新应用后：`memory.limit_in_bytes` 4294967296、`memory.memsw.limit_in_bytes` 5368709120（原为不限）、`memory.swappiness` 40（原为 100）。
  - 档位切换写入顺序：依次 6144 → 2048 → unlimited → 4096 均成功、上限值正确，最后恢复 4096。降到 2048 时内核回收了容器文件缓存；之后 `memory.oom_control` 的 `oom_kill` 为 0，KWin、plasmashell 未重启。
  - 设备面板：`rungic_dev.py deploy rungic-plasma-bridges --restart never`（这个包默认会重启桌面会话，以及网络、蓝牙、基带服务；为不打断日常机，这次不重启，面板下次打开时生效），开发覆盖 `rungic-plasma-bridges 0.510+dev20261003t172123.ec85cd0`，`apt=ok`；完整性 drift 只有部署前已有的两个 unowned 文件（graphviz `config8`、`rungic_cua/keyring.py`）。
  - 端到端：容器内 `rungic-platform --request '{"op":"container-memory"}'` 经平台桥、APK 和控制器返回新字段（程序 681、文件缓存 1085，其中内存文件 260，swap 988 MiB）。面板界面本身未截图，由离线测试覆盖。
  - 回退：装回备份的控制器，再写 `memory.memsw.limit_in_bytes=-1` 和 `memory.swappiness=100`；`rungic_dev.py reset rungic-plasma-bridges`。
- **为什么不另加磁盘 swap**：Moto 自带的"内存扩展"在用。2026-10-03 同一台 G100 S 只读读取：zram0 由厂商 zram 模块提供 hybridswap（`/sys/block/zram0/hybridswap_*`），`hybridswap_enable` 为 `hybridswap enable out_to_eswap enable swapd enable`，后备设备 `/dev/block/loop50`（/data 上的文件）；`hybridswap_meminfo` 显示 eswap 总量约 683 MiB、已用 133 MiB（原始 351 MiB），`hybridswap_stat_snap` 累计写出 5326 批、读回 5727 次，失败记录 0。它不依赖内核配置里的 `CONFIG_ZRAM_WRITEBACK`（该项在当前自建内核 `6.6.87-android15-8-maybe-dirty-4k` 里未开，但厂商模块自带写出机制）。另挂一个全局 swapfile 会与它和 lmkd（`swap_free_low_percentage=10`）的判断相互影响，并增加闪存写入，暂不加。
