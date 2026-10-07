# 镜像与内核构建拆分：跨手机的兼容契约

## 2026-09-30：Rungic 独立安装的三段式目标

用户明确调整方向：不再追求将 Android 与 Rungic 合并为完整镜像一次刷入，**Rungic 安装成为单独步骤**。以下为当前目标；后面的 2026-09-27 方案保留为历史设计，旧整包的实机结论见 [80 篇](80-g100-image-installation-retrospective.md)。独立 USB/ADB 首装入口及 X70 复用底座验收见 [91 篇](91-x70-independent-install.md)，实际重刷 Android、清数据后的独立安装见 [92 篇](92-x70-android-base-end-to-end.md)；通用用户自助安装与完整镜像升级仍待实现。

| 阶段 | 交付内容 | 何时执行 |
| --- | --- | --- |
| CI1：设备底座 | 基于原厂固件的 GKI/boot、必要的 root/容器能力准备、设备适配与恢复材料、兼容报告 | 新机型、固件/内核变化，或底座能力不满足时；已有兼容底座可复用 |
| CI2：RungicOS | 独立 ext4 rootfs、压缩包、精确包锁、哈希和离线检查报告 | OS 内容或预装集合变化时；不携带 Android 系统分区或独立内核 |
| CI3：独立安装/升级 | OS 镜像、需要的 APK/JNI/宿主桥与 LXC 运行时、版本/协议清单、安装和恢复入口 | 在已准备的 Android 上安装 Rungic，之后独立更新；日常组件更新继续走 APT release |

CI1 的解锁或机型特定底座准备可能清数据，仍须事先备份。CI3 的设计边界是保留 Android 数据、已安装 Android 应用与现有底座；不能因更新 Rungic 自动刷 `super/product/boot/init_boot` 或擦除 `userdata/metadata`。底座确实不兼容时回到 CI1 明确处理。独立安装并不消除内核、驱动和宿主桥依赖，而是将它们变成可核验、可复用的前提。

版本不要求同步发布，但必须兼容：CI2 声明架构、页大小/内核能力、图形媒体后端、宿主桥协议和包版本；CI3 将其与实际设备及宿主版本核对，绑定输入 SHA 和安装结果。相同 Android 版本或同型号不足以替代这些检查。上述契约是设计要求，目前 v1 spec 和工具没有完整表达这些字段，后续实现必须同步生产者、消费者和验收，不能添加无人读取的字段后宣称完成。

**现有实现与待补入口：**

- 已有：固定内核配方、rootfs 构建器、APK 与宿主种子构建、版本化 APT 发布/部署/回滚、rootfs 快照及账户/安装状态契约。
- 已有但属于旧路径：`assemble_product.py` 将 rootfs/宿主载荷放入 product，`assemble_release.py` 组装 Android 分区包，`flash_release.py` 执行整包刷写。它们继续用于明确指定的历史复现和恢复，不是新的独立安装器。
- 新增：`tools/ci/standalone.py pack/verify/install/status`，可信摘要、精确设备/boot 核验、ADB APK 更新安装、root-owned 独立载荷和 release 就绪门槛。`rungic-firstboot.sh` 可消费显式载荷目录；Magisk 兼容模块阻止旧 product 种子在重启时重新接管。X70 已验证复用底座及重刷后无预装 Rungic/Termux 底座上的独立安装及账户进入；重启首次打开仍有一次失败，边界和首启缺陷见 91、92 篇。
- 待实现/验收：用户自助安装、CI1 Magisk 离线就绪、通用完整 rootfs 安全替换及宿主/用户数据联动恢复。独立首装命令拒绝覆盖已有 runtime，不是升级器。
- 现有 `rungic_release.py deploy/rollback` 服务于已安装 Rungic 的 APT 更新；它不是空白设备首装器，也不等于任意完整 rootfs 替换流程。

独立首装从“底座已就绪、没有 Rungic 安装和账户”开始验收：传输/校验载荷 → 安装与挂载准备 → 真实 loading → 账户配置 → Plasma 实际显示。此处的全新 Rungic 状态不要求恢复 Android 出厂设置。升级须验证账户、文件、Agent 登录状态和 Android 数据保留；失败恢复须包含匹配的宿主、配置及数据迁移边界，不能仅把 rootfs 换回去就认定恢复成功。中断重试、首装、升级和恢复分别留证。

最初方案调整只涉及源码与文档；随后 X70 的新镜像构建、独立安装及重启实测另记于 91 篇。G100 旧整包 `.5` 的清数据成功仍按原验收范围保留。

## 历史方案：2026-09-27

2026-09-27 架构分析；同日按用户澄清修订：目标是**以自编 GKI 为基线，为一款手机生成含 RungicOS 的完整、一键刷入发行包**。本文提出拆分方向和验收条件；没有生成新镜像、移植新设备或改变当前手机。现有实机验收仍以 40、42、61 篇为准，G100（`portov_cn`）目前只有原厂镜像的主机侧核验（[78 篇](78-g100-firmware-inventory.md)）。

## 1. 先明确三个不同产物

| 产物 | 当前入口与状态 | 应有的可复用边界 | 必须绑定的输入 |
| --- | --- | --- | --- |
| Android 系统镜像/刷机包 | `tools/build_clean_product.py`、`assemble_clean_rom.py`、`oneclick_flash.py` 服务于 `mumba_cn/W1WAA36`；`prepare_g100_stock.py`、`verify_g100_stock.py` 对 `portov_cn/W1VT36H` 仅做原厂包核验 | 固件校验、分区解析、受控镜像修改、AVB 检查、刷写计划和离线验证的通用引擎 | OEM 原包、机型/渠道/构建号、分区表、动态分区布局、AVB 链、bootloader 和回退版本 |
| RungicOS 镜像 | `rungic_release.py` 已能产出版本化 APT 发布；`rootfs-image` 已在手机上将运行中的 Ubuntu rootfs 迁入 ext4 并提供快照。61 篇提出的 `mmdebstrap` 从零构建尚未实现 | Ubuntu ARM64 基础包、Rungic 软件包、镜像制作、发布清单和迁移/回滚 | 软件仓库快照、包版本及架构、桥协议版本、容器启动提供者及运行能力；部分图形驱动包按硬件选择 |
| 自编 GKI 内核及 boot 镜像 | `kernel/README.md` 是 GKI 实验记录；只有一条补丁和绑定旧镜像哈希的证书恢复脚本，未形成完整的版本化构建配方 | 固定 AOSP/Kleaf 来源、配置片段/补丁、ABI 比对与产物记录 | 设备当前 GKI/KMI、原厂模块与信任锚、boot 格式、原厂 ramdisk/DTB 关系、固件基线 |

三者的“通用”含义不同：**每款目标手机都有自己的自编 GKI 内核产物**。可复用的是 AOSP/Kleaf 构建工具、补丁管理和 ABI 检查方法；实际使用的 ACK 分支/提交、KMI、页大小、配置、补丁、工具链、模块信任关系与 `boot.img` 组装参数由该机型及固件基线配方决定，分别构建、分别验收，不因同 SoC 或同 Android 大版本而复用 boot 二进制。RungicOS 用户空间可跨设备复用的比例最高；Android 分区镜像同样按确切设备和固件基线组装。Android 和 LXC 共享该设备的自编内核；RungicOS 的 rootfs 不另带独立内核。

最终用户拿到的可以是**一个发行包和一个刷入入口**，但包内仍是多种分区镜像、rootfs 载荷、首启安装组件、刷写计划与校验清单；单个 Android `system.img` 无法表达 boot、vendor、动态分区及加密 userdata 上的 RungicOS 安装。

## 2. 建议的模块和发布边界

先在当前仓库内做模块化，直到构建接口和版本锁经第二款设备验证后，再决定是否拆成独立 Git 仓库。这样可让当前跨组件协议、补丁队列和发布记录在同一提交里变化，不增加协作时的漂移。

```text
device profile + 锁定的原厂基线（待实现）                -> 每条流水线共同读取
Kernel builder（由实验记录整理）                         -> 自编 GKI + 设备专属 boot + ABI/模块报告
共享协议与源码：shared/、native/、plasma/、packages/       -> 有版本的 .deb / APK
RungicOS image builder（待实现）                       -> 可安装的 rootfs + manifest
Android firmware builder（由现有单机脚本抽取）            -> 设备专属分区镜像集 + 首启种子
compatibility probe（待实现）                            -> 当前设备及候选内核的逐项证据
release composer / CI（待实现）                          -> 完整一键刷入包 + 成套验收矩阵
```

建议的逻辑路径是 `profiles/devices/<厂商>/<代号>/<固件>.json`、`kernel/targets/<厂商>/<代号>/<固件>/`、`profiles/requirements/<产物>.json`、`tools/image/`、`tools/kernel/`、`tools/compat/`。这些是**拟议接口**，不作为已有命令。设备 profile 明确引用其内核配方，而不是由 CI 猜测；配方固定 ACK/Kleaf manifest、配置片段、补丁、预期 KMI、原厂模块及 boot 组装参数。每一层输入用显式清单和 SHA-256 传递；构建缓存、OEM 原包、镜像、日志和实机材料仍在 `.work/`。既有 `packages/<名称>/recipe.json` 与 DEP-3 补丁队列继续负责修改过的 Linux 上游包；不要为镜像拆分另造一套上游源码树。CI 可以分别构建组件，但只有最终集成流水线能给一键刷入包授予发布资格。

设备 profile 只描述经过核验的事实及该固件特有的布局，不把“支持”写成一枚布尔值。其身份至少包含制造商、产品代号、SKU/渠道、Android fingerprint 和 vendor/boot 基线；活动槽是每次探测的运行状态，不写成设备型号的一部分。profile 记录分区几何、AVB 关系、GKI/KMI、原厂来源哈希、图形/音频候选后端与实机证据。`mumba_cn` 与 `portov_cn` 应为两个独立 profile；同一销售名称、同一 GPU 或同一 Android 大版本均不足以合并。

## 3. 用需求清单与探测结果决定能否运行

RungicOS 发布清单声明**硬要求**、可选功能和桥协议版本。探测器生成 `capability-report.json`，每个结论带实际观测值、探测方法、设备/固件标识、时间和证据路径。报告分成两部分：**原机/固件事实**（分区、OEM 模块、bootloader 等）和**候选自编 GKI 的能力及实测结果**（内核配置、ABI、模块、容器、图形等）；最终兼容性必须基于将要随包交付的新内核，不能只测正在运行的旧内核。匹配器读取清单并给出差异，不在匹配期间刷写或修改现有系统。未知的硬要求按未通过处理，不能自动推断为支持。

例如（仅为拟议的数据结构，具体阈值须从现有实现与实机证据确认）：

```json
{
  "contract": 1,
  "requires": {
    "arch": "aarch64",
    "launcher": "magisk-lxc-v1",
    "bridge_protocol": "1.x",
    "rootfs_storage": ["ext4", "loop", "dm-snapshot"],
    "desktop_output": "wayland-buffer-import-v1"
  },
  "optional": ["camera.pipewire", "codec.h264.encode", "cast.miracast"]
}
```

每个键都要有对应的实测探针和失败解释，不能让 profile 自己填 `supported: true` 来绕过测试。桥接层先做版本握手和能力协商，再启用对应服务；设备特有代码只实现共享后端所需的适配器，不进入每个 Linux 应用。

**静态预检（无需改设备）**：ARM64/页大小、Android/API 与 vendor 基线、当前内核和候选 GKI 的 KMI、`/proc/config.gz` 或等效配置证据、SELinux 模式、可用空间、文件系统和设备节点、计划交付的 Magisk/LXC/APK/桥协议版本。Android 固件与内核候选另外逐项比对原包哈希、分区大小、槽位、AVB 链和原厂模块信息。设备型号只作身份校验，不替代能力检测；当前手机没有 LXC 或 Magisk，不应因此否定一个本身会安装它们的完整包。

**隔离式能力探测（需当前实现所需的特权，但只用可删除的临时资源）**：实际创建 namespaces 与 LXC 测试容器，测试 overlay/loop/ext4/dm 所需操作及 SELinux 访问，测试 Android APK 与 Linux 桥的握手、共享内存/FD 传递，进行 Wayland buffer 导入和 KWin/GPU 真实渲染。内核 CONFIG、存在某个 `/dev` 节点或单独 GPU 探针通过都不等于这一层可用。探测前后检查临时挂载、进程和设备资源均已释放；对现有 rootfs 镜像和现有 dm 名称不做破坏性测试。

**安装后的验收**：沿用 `rungic_acceptance.py`，并增加 Android 首启、原厂模块、Magisk/LXC 种子安装和完整擦除后的恢复验收；桌面显示和触摸、网络、音频、相机、编解码、挂载/快照分别运行端到端场景；同类能力以多个独立应用交叉核对，并记录哪些功能未测试。发布等级建议是 `source-ready`（来源已锁定）、`image-verified`（镜像结构/哈希验证）、`candidate-booted`（候选 GKI 与 Android 实机开机）、`bundle-validated`（从完整发行包刷入并完成 RungicOS 验收）。每一级都附报告，不把低一级升级成“已支持”。

硬要求的判定可写成：`允许刷入 = 全包哈希正确 ∧ 设备/固件/分区 profile 精确匹配 ∧ 刷写前条件通过`；`允许发布 = 候选 GKI 的 ABI/模块检查通过 ∧ 离线镜像验证通过 ∧ 专用测试机从完整包刷入且端到端验收通过`。可选的相机、硬编解码、投屏等在能力协商中单独标记；缺失时不阻挡基础桌面，但不得宣称功能完整。当前实现若某个能力实际是启动必需，就列为硬要求，不凭期望把它降为可选。

三类产物有额外门槛：

| 门槛 | RungicOS rootfs | Android 分区镜像 | 内核/boot |
| --- | --- | --- | --- |
| 构建前 | 锁定 Ubuntu 包索引、Rungic release、APK/桥协议；核对 ARM64 | 原厂包逐文件校验、精确固件 profile、分区与 AVB 方案 | 固定 ACK/Kleaf 提交和工具链、配置与补丁、原厂 boot/vendor 模块清单 |
| 离线产物 | 包归属、文件权限/xattr、ext4 结构、内容清单 | `lpdump`/`lpunpack`、`avbtool`、镜像大小及刷写顺序 | `Module.symvers`/ABI 与基线比较、模块签名/证书、boot header 与段布局 |
| 设备上 | 容器、桌面和共享硬件接口验收；升级和回滚 | 仅 profile 精确匹配后允许刷写；启动及功能验收另行记录 | 仅 profile 精确匹配后进入实机测试；全量原厂模块加载、硬件、SELinux 与回退验收 |

VINTF 可用于 Android framework/vendor/HAL 的原有兼容检查；它不描述 RungicOS 的 LXC、Wayland、KGSL、桥协议和 ext4 快照需求，须在其上叠加本项目的能力清单。KMI 同系列与代际匹配是内核候选必要条件，但历史上本机已有重编后 3,252 个符号 CRC 变化、97 个模块缺失的反例（`kernel/README.md`）；需要实际 ABI 与模块测试。

## 4. 以自编 GKI 为主线的构建和 CI

1. **固定设备基线。** 对每款目标手机，先锁定完全匹配的 OEM 固件包、vendor 与模块集、分区/AVB 元数据、bootloader、页大小和回退材料。`mumba_cn` 与 `portov_cn` 分别建配方。原厂包在构建缓存中校验后使用，不放入源码目录。旧 `oneclick_flash.py` 的 `mumba` 型号、34 个分片和 `W1WAA36` 哈希只能成为该 profile 的数据；`portov` 有 32 个分片。
2. **按机型/固件配方构建 GKI，再给上层定义已实现的能力。** 将 `kernel/README.md` 中 `mumba` 的固定 ACK/Kleaf 来源、配置增量及补丁整理为该设备的首份配方；新增手机须先核对其自身的源码/KMI/模块基线，不沿用 `mumba` 的产物。CI 对选中的配方构建 `Image`/模块与 ABI 描述，与该配方未改动基线和对应 OEM 模块需求比对符号 CRC、KMI、配置与签名信任关系；组装该设备的 `boot.img` 并核对 header 和 AVB。历史证书恢复脚本只适用于那两个哈希固定的镜像，不作为通用方案。离线检查通过后，该机型的专用测试机启动候选内核并验证所有 OEM 模块、Android 基本硬件、SELinux 和 LXC 所需内核机制；失败停止该机型的整包流水线。
3. **构建 RungicOS rootfs。** 沿用 `rungic_release.py` 的精确版本包和本地 APT 仓库，用 `mmdebstrap` 建 Ubuntu ARM64 用户空间并安装发布元包，制作 ext4 和文件清单。镜像中有桌面和共享后端；`/home`、凭据、core 与本机仓库仍在外部。用候选 GKI 的能力报告核对 rootfs 声明的硬要求，容器和图形在测试机上验收。
4. **构建 Android 底座与首启载入路径。** 从现有单机脚本抽取 OEM 包校验、动态分区、AVB、boot/init_boot、修改叠加与刷写计划。发行包要同时包含或能在首启可靠生成 Magisk 运行时、LXC、Android APK、桥、RungicOS rootfs、启动配置及 SELinux 规则。当前 `tools/rungic-magisk-bootstrap.*` 已验证从 `/product` 给 Magisk 首启播种，**尚未验证 RungicOS rootfs 的播种**。完整清数据后，必须在无需已授权 ADB 的路径上把 rootfs 安装到 `/data/adb/rungic-lxc/images/`，校验 SHA-256，原子写入并保证断电后可重试。首选复用经核验的只读分区首启种子机制；如果压缩 rootfs 放不进分区或受启动/SELinux 限制，就须实现并验证适配该设备的恢复环境安装阶段。直接把 rootfs 塞进 `userdata.img` 会遇到设备的数据加密和首次初始化问题，不能作为未经验证的捷径。
5. **CI 按设备 profile 组合发行包。** 一个 CI 任务只处理一个机型/固件基线，锁定其 OEM 输入、内核配方与 GKI commit/ABI、Android 分区镜像、RungicOS rootfs、APK、桥协议和刷写工具的摘要。CI 顺序为：来源/许可证与哈希 → 该机型 GKI/ABI → rootfs 与 APK/包 → Android 镜像/AVB → 全包离线还原与结构校验 → 对应专用测试机完整擦除刷入 → Android 首启及 RungicOS 端到端验收 → 生成带日志、构建来源和版本矩阵的正式包。日常提交只跑可重复的主机检查；破坏性刷写只在专用测试机上运行。按已确认分工，Mac mini 原生编译 ARM64 软件包并发布不可变快照，当前 Linux x86_64 工作站运行 GKI、rootfs、固件组包三条通用镜像 CI；runner 与设备 profile 分离，具体容量、触发顺序及清理门槛见 [77 篇](77-g100-three-ci-assessment.md)。
6. **一键刷入是发行包的用户接口。** 用户只执行一个入口；它先核验全包及设备身份/bootloader/分区，再按锁定的 flash plan 写入，完成必要的数据清理和首启安装，最后报告 Android 与 RungicOS 的实际状态。必须支持从纯 bootloader 状态开始；不能把“等用户开启 USB 调试、授权 ADB、手工拷 rootfs”隐藏在一键之内。现有 v3 一键包只恢复 Android 精简系统与 Magisk，没有 RungicOS，也没有在发行包中集成自编 GKI。完整 AOSP ROM 构建若后续需要，可做独立底座实现，必须额外核对设备树、vendor 来源和 VINTF/AVB。

这里还有一个必须先验证的设备分发边界：Magisk [官方安装说明](https://topjohnwu.github.io/Magisk/install.html)要求在目标设备本机修补对应的 boot/init_boot/recovery，明确警告不要把某台设备修补后的镜像直接分享给同型号其他手机。现有 v3 与 GKI 实机记录只证明特定测试机可用。若目标是“同一机型的任意一台手机均可用同一包”，必须为 root/首启链另做设备侧生成或等效的跨机验证；在解决前只能把发行范围限定为经过验证的具体设备与固件组合，不能把型号匹配当作全部前提。

## 5. 上游依据与未解决事项

- [AOSP GKI 构建](https://source.android.com/docs/setup/build/building-kernels)、[KMI 规则](https://source.android.com/docs/core/architecture/kernel/android-common)与 [Kleaf API](https://android.googlesource.com/kernel/build/+/refs/heads/main/kleaf/docs/api_reference/kernel.md)：复用官方构建和 ABI 机制。实际设备仍要核对 OEM 模块与签名。
- [AOSP VINTF](https://source.android.com/docs/core/architecture/vintf)、[动态分区](https://source.android.com/docs/core/ota/dynamic_partitions/implement)、[AVB](https://source.android.com/docs/security/features/verifiedboot/avb)：复用原有 Android 兼容与镜像工具，不自行简化其验证链。
- [AOSP 分区说明](https://source.android.com/docs/core/architecture/partitions)、[fastbootd](https://source.android.com/docs/core/architecture/bootloader/fastbootd)与[文件级加密](https://source.android.com/docs/security/features/encryption/file-based)：区分 `boot`、`init_boot`、`vendor_boot`、动态分区与加密 `userdata` 的职责；不能从刷写分区镜像直接推断 `/data/adb` 已有 RungicOS。
- [mmdebstrap 手册](https://manpages.debian.org/trixie/mmdebstrap/mmdebstrap.1.en.html)：有 ARM64 用户空间和 ext4 输出能力；本项目尚未验证 Ubuntu 26.04 包、维护脚本、文件属性和启动配置在该流程中的结果。
- 源码/许可证：Linux 内核源码及补丁按其 GPL-2.0 许可核对；AOSP 构建工具、mmdebstrap、Ubuntu 包分别保留各自来源和许可清单。Motorola 原厂分区镜像是单独的 OEM 输入；公开发布权未核实，不能默认随通用源码或公开镜像发布。当前私有仓库只跟踪配方/补丁/来源记录，生成物继续留在 `.work/`。

本方案的首个可审查里程碑是：选定一款手机的固定固件基线，CI 从锁定源码重建候选 GKI，产生可比较的 ABI/模块报告与 boot 候选；同一设备的检测报告区分原机事实和候选内核结果。下一里程碑才是离线 rootfs 与清数据后首启播种；这两项未通过之前不发布“完整一键刷入”包。`portov` 当前只有原厂镜像主机侧核验，尚无自编 GKI 或 RungicOS 实机结果。


## 2026-09-30：预装应用调整

用户要求移除 Haruna、Marknote、KleverNotes、Journald Browser、Emoji Selector 和 Angelfish。前五个独立包（不含 Emoji Selector）从 `system/ubuntu-packages.txt` 及开发机的显式安装清单移除；`system/ubuntu-excluded-packages.txt` 记录镜像排除集合。用户日后仍可自行安装这些独立应用，不设置 APT 禁装策略。

本地实际包清单表明 Emoji Selector 的程序和菜单属于 `plasma-desktop`，全局快捷键入口属于 `plasma-desktop-data`（6.6.6）。不能因此卸载桌面。`rungic-plasma-config` 安装 `zz-rungic-apps`，使用 [dpkg 标准 path-exclude](https://manpages.debian.org/trixie/dpkg/dpkg.1.en.html) 排除三个精确路径：`/usr/bin/plasma-emojier`、`/usr/share/applications/org.kde.plasma.emojier.desktop`、`/usr/share/kglobalaccel/org.kde.plasma.emojier.desktop`。配置包 postinst 同时清理此前已安装的这三个文件；桌面、Emoji 字体及共享资源保留。现有完整性检查已识别 dpkg 路径排除规则。

复用旧构建 root 树时，先在 `tools/ci/arm64_chroot.py` 提供的构建 chroot 内模拟并执行以下命令；只用于待发行模板，不在开发宿主直接执行，也不自动操作手机：

```sh
apt-get --simulate purge haruna marknote klevernotes kjournaldbrowser angelfish
apt-get purge -y haruna marknote klevernotes kjournaldbrowser angelfish
```

同时构建并安装新版本 `rungic-plasma-config`，更新 release 锁定的版本后再生成 rootfs。`build_rootfs_image.py` 会拒绝仍包含被排除软件包、缺少排除规则或留有 Emoji Selector 入口的输入树。它不会在镜像生成时偷偷卸包，也不会改写旧发行镜像。

离线验证：18 项测试及 204 项子检查通过；基于现有 ARM64 root 树的 APT 模拟仅移除指定五包；配置 DEB 构建通过。一次性无网络容器验证真实 postinst 清除旧 Emoji Selector 入口，随后 fixture 包解包遵守排除规则且保留其他文件；测试容器缺 KDE 配置依赖，因此该项仅验证维护脚本与 dpkg 文件过滤，不等于完整依赖/桌面验收。日志在 `.work/preinstalled-apps-20260930/`。现有 `20260928.7` 镜像和实机未更新，新预装集合将在下一次重建镜像时生效。

## 2026-10-07：预装文本编辑器

用户要求预装一个文本编辑器。预装清单增加 KWrite，release 使用 Ubuntu ARM64 的 `kwrite=4:25.12.3-0ubuntu1` 精确版本；配置包随源码重新构建。Marknote、KleverNotes 等原有五个独立包仍在排除集合中。

当前 Ubuntu KWrite 的硬依赖包含完整 `kate` 包及 `libkf6texteditor-katepart`，不能卸载 Kate 来保留 KWrite。沿用现有 dpkg 路径排除机制，增加 `/usr/share/applications/org.kde.kate.desktop`：保留依赖程序和共享资源，只留下 KWrite 的应用入口。配置包现有 postinst 同样清除复用根文件系统中的旧 Kate 入口。封镜检查同时要求 KWrite 已安装、程序可执行、桌面文件存在，并拒绝遗留 Kate 入口；不会在封镜时修补输入树。

无头 ARM64 系统测试使用实际 Ubuntu KWrite 与 Plasma Mobile，从中英文抽屉启动，编辑中英文正文，经真实文件对话框保存至普通桌面用户的 `Shared` 目录，关闭后重新启动并打开、修改、再次保存，核对文件正文和属主。KTextEditor 6.24.0 的 [Enable Accessibility 配置默认打开](https://invent.kde.org/frameworks/ktexteditor/-/blob/v6.24.0/src/utils/kateconfig.cpp)，[正文实现 Text 和 EditableText](https://invent.kde.org/frameworks/ktexteditor/-/blob/v6.24.0/src/view/kateviewaccessible.h)。测试在启动应用前启用无障碍，从欢迎页新建正文后使用实际 AT-SPI 接口编辑和读回，不采集剪贴板。正文和文件名均通过真实 AT-SPI 文本框设置并读回，保存和打开仍使用实际对话框。原生键码输入共用产品的 WorkspaceInput，缺少 Shift 处理的问题另由 task #104 修复；正常 CUA 使用 KWin commitText，旧接口回退也受该缺陷影响。本测试拒绝通过键码替身输入大写或符号，避免悄悄变形。该检查只验证 Linux 编辑器流程；手机屏幕键盘、Android 输入和 Android Shared 挂载的双向写入仍由第三轮实机检查证明。已有冻结候选未修改；下一轮必须从合入后的源码重新构建发行包和镜像。
