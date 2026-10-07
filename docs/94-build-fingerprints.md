# 按输入指纹构建、复用和验证独立镜像

2026-09-30。用户要求产物带版本哈希，以实际输入判断能否复用，不按组件类别决定强制重编或默认复用。起因是 [86 篇](86-x70-miracast-assessment.md) 的旧投屏 JAR 被混入新镜像。本轮共享 OS/Android 组件重新构建，用户随后指定实机验收改用 USB G100；不使用 X70 的固件配置或内核报告。

## 两种哈希与复用规则

`tools/build_artifact.py` 为每个构建生成 `build.json`：

- `input_sha256`：规范化 JSON 的 SHA-256，包含组件名、目标架构、相关源码/补丁的内容与文件模式、声明的依赖、工具链身份、参数、命令、显式环境和输出清单。依赖若来自其他组件，包含其输入指纹和实际产物摘要，形成依赖关系。源码哈希来自当前文件内容，覆盖未提交改动；整个仓库提交号不作缓存键，改无关文档不会令组件失效。
- `outputs`：每个实际产物的 SHA-256 与字节长度，用于检测损坏、错配与替换。它不能单独证明源码新旧。

缓存位置 `.work/build-cache/<component>/<input_sha256>/`。构建前计算当前期望输入，只有该键下的报告、输出清单和每个产物校验均通过才命中。输入变化产生新目录；构建失败、输入在构建期间变化或输出缺失不发布缓存。并发同键构建使用文件锁；失败暂存保留在 `.pending-*` 供诊断。已有目录缺报告或文件校验不符明确报错，不静默跳过，也不覆盖旧产物。

配方中的依赖位置不进入摘要，内容身份进入；命令和显式环境本身会进入摘要，仍应使用 `{repo}`、`{output}`、`{input:名称}` 占位符，避免无关绝对路径造成额外失效。目录身份包含目录项、权限、链接本身及 xattr；镜像根目录输入另外声明 `ownership: true`。不跟随目录链接，配方必须显式声明构建实际读取的外部链接目标。mtime 不参与缓存键；本机制检查复用一致性，不承诺不同构建时间得到逐字节相同的镜像。

参考 [Reproducible Builds 的构建环境记录](https://reproducible-builds.org/docs/recording/) 与 [SLSA 的来源记录定义](https://slsa.dev/spec/v1.2/provenance)，复用现有编译器、包管理器与镜像工具。没有复制其实现，也不声称获得 SLSA 等级、签名证明或完全隔离的构建环境。工具和依赖的声明完整性仍是配方维护者责任；裸二进制不能反推其原始源码构建参数。

## 执行入口

```sh
source tools/work-env.sh
python tools/build_artifact.py /path/to/recipe.json
python tools/build_artifact.py /path/to/recipe.json --check
```

配方声明 `schema: 1`、`component`、`target`、`sources`（仓库相对路径）、`dependencies`（具名实际输入）、`tools`（实际工具文件/目录或已解析的不可变镜像 ID）、`parameters`、`environment`、`command` 与 `outputs`。依赖可指定预期文件 `sha256`；源码构建依赖还指定对应 `build.json`。执行器隔离常见宿主环境，只提供基础 PATH/HOME/locale 与明确声明的环境；网络代理若需要，由配方明确传入，不能隐式选旧宿主配置。私钥内容和账号凭据不写入报告。

组合 rootfs/host 的执行器需在 `podman unshare` 内运行，以正确读取客体属主和受限 home；APT 索引容器在外层独立执行，不能在该用户命名空间里再启动 Podman。此次第一轮 host 构建因此失败，失败目录未发布；修正为独立 `image-repository` 阶段后通过。

相关工具：

| 入口 | 职责 |
| --- | --- |
| `build_artifact.py` | 通用输入指纹、构建、缓存命中及输出校验 |
| `ci/build_image_packages.py` | 从当前源码构建投屏桌面包，并生成匹配的 release 元包 |
| `ci/build_image_repo.py` | 合并固定包基线与新包，使用固定构建容器生成索引和包摘要清单 |
| `ci/build_fingerprinted_rootfs.py` | 复制已声明的二进制根目录基线，隔离安装更新包，生成逐包已安装内容指纹及新 ext4 镜像 |
| `ci/build_fingerprinted_host.py` | 消费已验证的仓库归档、rootfs 模板、控制器与 Android 组件，生成新 host seed |
| `ci/standalone.py pack --build-plan …` | 重新核对每个期望配方、记录、产物和组件依赖，绑定最终 APK/rootfs/host/sparse writer |

新的独立包使用 manifest schema 2，携带 `build-manifest.json`。离线 verify 检查其中的输入指纹自洽性、完整的组件依赖及主要产物绑定；旧 schema 1 包仍可按原摘要验证，不能被追认为具有新构建证明。pack 必须提供本次构建计划，不能拿任意旧记录代替当前期望输入。

## 本轮构建范围

运行目录 `.work/ci/runs/rungic-20260930-fingerprints/`，含本轮 `recipes/*.json`、`build-plan.json`、构建结果、缓存复验与安装包。本机现场为 mibook / x86_64，系统代理 none，Git/下载沿用 `192.168.5.45:6152`。Android 编译容器以解析后的完整镜像 ID 调用，SDK、API JAR、Clang/sysroot、Rust 工具链和原生链接库进入指纹。

8 个构建阶段：Android 原生库、投屏 JAR、APK、静态入口/稀疏写入工具、桌面投屏与 release 包、APT 仓库、rootfs、host seed。原生库和入口程序重新走固定源码编译，APK 使用这次原生库；没有再从旧 APK 随手抽取 JNI。当输入未改变时，这些组件和 rootfs 都允许复用。

**旧依赖的边界：** Ubuntu/定制上游软件包基线、Alpine LXC 运行时、Termux 与部分原生运行库仍是明确选定的二进制输入，完整内容参与下游指纹。本轮没有给历史文件补写不存在的原始编译证明，也没有重编全部发行版或全部历史上游包。rootfs 附带 `usr/share/rungic/build/packages.inventory.json`，为每个已安装包记录版本、架构及已安装文件内容摘要；APT 阶段另输出 `.deb` 的 `packages.artifacts.json`。这些是二进制/安装内容身份，不能冒充源码来源证明。旧 `rungic_package.py` 等其他直接构建入口尚未全部自动切换到本执行器；发行时须通过这里的显式配方和 pack 检查。

本轮 OS 包集合 `20260930.17`，`rungic-cast 0.374+cast4`，APK 2.27。独立 G100 载荷 `portov-20260930.8`，manifest SHA-256 `26b65e6d4333a8be361855cc164931dec5130455db0ed8e005ce2d64b6767476`。压缩 rootfs 1,809,754,746 字节，host seed 219,344,355 字节，APK 4,371,364 字节；根文件系统为 16 GiB 稀疏 ext4。原始 rootfs SHA-256 `4a5b4d21edcc0ecb814a21818311b0a6104a144bd3d298757e1aadf0c38dfcc4`。

## 验证记录

- 54 项主机测试与 18 个 subtests 通过：相同输入、无关文档变动、源码/依赖/工具/目标/参数变化、产物损坏、缺少记录、构建中输入变化、构建失败、错误输出路径，以及 manifest 的产物替换、缺绑定、缺依赖等。
- 8 个实际组件第二次调用同一执行器全部命中，保留相同输入指纹和产物摘要；见 `cache-reuse-verification.json`。不仅是以测试夹具模拟缓存。
- 新 rootfs 的 `apt-get check`、`dpkg --audit`、账户模板和家目录约束、预装排除项、ext4 文件系统检查通过；最终 pack/verify 通过。
- G100 固件 `W1VT36H.1-51-8`、slot a、内核 `6.6.87-android15-8-g86c6642d582e-ab14676406-4k`、SELinux Enforcing；活动 boot 前 36,663,296 字节 SHA 与已保留 `.5` 镜像一致。复用该底座，不刷 Android。

## G100 实机结果

用户明确将目标改为当前 USB G100，序列号 `ZY32M9MRVP`。本轮采用**受控、保留账户的镜像替换**：复用经活动 boot 摘要核对的 Android/GKI 底座，未刷 Android、未清数据，也未调用拒绝覆盖已有运行时的独立首装入口。本次迁移脚本不作为已完成的通用升级器。

安装前停止旧容器，将整个旧 LXC 运行时、原镜像与 snapshot、控制器保留在手机 `/data/adb/rungic-fingerprints-20260930-backup/`；账户数据、应用私有数据和控制器另有归档，复制到运行目录 `device-backup/` 并比对摘要。私有身份文件和归档限制访问权限，不进入 Git。回退脚本已准备，但本轮未执行回退验收。

新载荷在手机核对文件摘要后，用本次编译的稀疏写入器解压 rootfs；**在写入账户配置之前**核对完整原始镜像 SHA，匹配上文构建产物。随后保留 home、账户身份、共享状态及 APK 数据，安装本次 host seed/APK。账户迁移辅助脚本第一次因继承 Android PATH 找不到 Linux 管理命令而失败；确认二进制存在后，显式设置 Linux PATH 继续完成迁移。这是本轮操作脚本的问题，没有以临时修改镜像内程序来通过验收。

结果：

- OS `20260930.17`、`rungic-cast 0.374+cast4`、APK 2.27 已部署；桌面实际截图正常，账户 `kevinzhow` 及 Agent 原有登录配置保留。身份对比确认 UID/GID、口令摘要、组、subuid/subgid、SSH 主机密钥和 machine-id 均保留，公开记录只保存比较布尔值。
- 9 项 smoke 全部通过：会话、关键服务、无新增崩溃、显示几何、文字输入、相机帧、空闲抑制、播放、录音。历史 core 数据保留，不把旧崩溃记录算作本次新增故障。相机有变化的帧通过并释放资源；音频实际流与非零录音通过，不代替主观画质/音质判断。
- 一轮整机重启通过。ADB 归属从 5038 转为 5037，重新按精确序列号确认；Android 正常锁屏经滑动解锁后，先前单次启动的 Rungic 进入 Plasma，无需再次打开。重启后 SSH socket 启用且监听、systemd 无失败服务、dpkg audit 通过，cgroup 目录 0755；追加播放和录音两项复验通过。此处不把 Android 锁屏期间无法访问容器解释为镜像启动失败。
- 已部署投屏 JAR SHA `b63334e5bc6f1e5ae226d994db4c08e92a1bef0ec43899c4d9f7d20db9228e8a` 与本次编译产物一致，重启后仍一致。协议 1 扫描首次发现两台可用 TCL 电视，重启后再次发现一台可用 TCL；没有连接接收端，本轮不声称通过电视画面、声音或投屏选择器 UI 验收。
- 设备中保留 `/usr/share/rungic/build/payload-build-manifest.json`、包内容清单与基线记录，便于后续比对。已删除传输载荷与迁移临时文件，保留完整旧系统备份。

证据均在本轮运行目录：`identity-preservation.json`、`device-payload-check.log`、`acceptance/report.json`、`desktop-reboot.png`、`reboot-services-after-unlock.log`、`reboot-audio/report.json`、`cast-scan.json`、`cast-scan-after-reboot.json`、`final-system.log` 与 `cleanup.log`。smoke 工具自动附带了上次 X70 指标对比，跨机型数值不能作为本次性能改善结论。

本轮通过的是构建复用检查、离线镜像检查和 G100 保留账户更新及重启检查；没有重新验收空白账户首装、Android 清数据刷入或故障回退，不能用本轮结果替代这些边界。


## 完整首装编排

`tools/ci/prepare_rootfs.py` 从 Ubuntu ARM64 最小树开始，复用
`system/ubuntu-packages.txt` 和现有 `build_rootfs_image.py`。
在原生 ARM64 构建容器内，以 root（或构建用 user namespace 内的 root）运行：

```bash
python3 tools/ci/prepare_rootfs.py --packages "$checked_package_repo" \
  --source-commit "$source_sha" --firefox-version "$firefox_version" \
  --output "$new_attempt_dir" --size-gib 16
```

`--packages` 指向已核对的本地 APT 仓库，包含 `release.json`、`Packages`、
`Release` 和该 release 的精确 DEB。参数绑定源码 SHA、Firefox 版本、Ubuntu
suite 与镜像大小。构建记录仍应通过 `build_artifact.py` 的输入指纹固定仓库、
工具链和脚本；完成凭据不能补足上游二进制的原始源码证明。

编排先安装拥有 Mozilla 源、密钥和 pin 的 `rungic-plasma-config`，再刷新源并
安装完整 release 和运行依赖。临时 APT 源选择写在 `/var/lib/rungic-apt`，
编排不手写包拥有的 `/etc/apt` 配置。安装脚本来自磁盘，所有包管理命令
关闭 stdin；配置询问或维护脚本读取输入失败会停止构建，不能吞掉后续命令。
随后创建锁定口令的模板账户，生成中英文 locale，核验 APT、dpkg、项目 venv
及其运行导入，记录安装包集合，再清理本轮下载缓存。

`root-install.complete` 仅在最后一步写入，包含源码 SHA、release 版本及
文件摘要、dpkg 状态摘要和脚本摘要。镜像命令的 `--install-source` 必须匹配
它；缺少或不匹配时，在创建镜像目录之前拒绝。已有带完成凭据的树不能省略
此参数。镜像入口必须显式选择 `--install-source` 或 `--unverified-root`，两者都不给就拒绝。历史手动树用后一项，报告的 `install_receipt` 为 null；验证模式记录安装源码与凭据文件 SHA-256。带凭据的树不能用未验证模式直接绕过核验。

每次 `--output` 必须是不存在的新目录；失败的目录保留，工具没有续跑或
补齐完成凭据的选项。`--prepare-only` 在安装检查结束后停止；之后仍需用
带 `--install-source` 的现有镜像器打包。同一个 output 不能再次执行首装。
`--qemu` 供已有 ARM64 binfmt 配置的 x86 runner 使用，完整交叉安装仍需在
相应 runner 验证。此工具不接触手机，也不修改已冻结的候选。

### 2026-10-07 原生完整首装验证

工具源码 `a31509a162cb90c930193320a7d6a75f5f5102d2` 在 Mac 的独立 ARM64
构建容器完成最小树、配置包、完整 release、模板账户、locale、APT／dpkg、
venv 导入、最后完成凭据、封镜和独立读回。实际安装 1501 个包，release
`20261007.1` 的 88 个锁定包及元包均匹配，Firefox 为 `157.0.1~build1`。
`e2fsck -fn` 返回 0，压缩流完整性及两份包锁摘要一致。

| 产物 | SHA-256 |
| --- | --- |
| 16 GiB ext4 | `2f6d0ed59c501ac17570a0282513b777f33617ee5cc76710b3a44025636312a1` |
| gzip（1,773,837,848 字节） | `381d31ead2924621c84dc5ef55d6d55fd166b4380279add7a0f92e84053fd7be` |
| 安装及镜像包锁 | `e9a027650103cc7e17a82f9aa2a2964217fe3589cb5e832b60e98bdd32074aa3` |

反例也经同一原生入口：配置 DEB 的维护脚本遇到 EOF 返回 42，完整首装
非零退出，没有账户、完成凭据或镜像；重用其输出目录被拒绝，失败树的
包状态不变。安装已成功的正例因构建盘余量不足而暂停编排父进程，包管理
子进程正常完成；已验证归档释放空间、重新检查构建卷和宿主余量后恢复
同一父进程，未重跑安装。该容量暂停不等同于允许续跑失败的树。

最初两次真实失败（APT `.sources` 格式、非锁定运行包参数覆盖精确版本）
原样保留，修复后使用新目录重建。离线检查通过 1343 项及 685 个子用例，
原生正反例不代替未执行的 QEMU 路径和手机首装验证。

基线更新路径 `build_fingerprinted_rootfs.py` 的输入是构建配方明确声明并整体
指纹绑定的二进制树（历史安装模板，或正式首装工具的输出），不是本次完整
首装。它只在新副本里、更新 dpkg 前移除已失效的首装凭据，再显式以
`--unverified-root` 封镜；原基线和原凭据不改，也不补造完成证明。镜像报告
保留 `install_receipt: null`，该路径的来源由现有 baseline／build-manifest
记录承担。其 chroot 调用也关闭 stdin。
