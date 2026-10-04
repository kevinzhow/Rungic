# 本地开发部署：发布之上的开发覆盖（2026-09-30）

用户要求区分两条流程：

- **本地开发**：改什么就部署什么，不需要为每次试验提交、构建和部署一整套版本。
- **线上发布**：以一个版本号为准，约束所有包的精确依赖。

工具是 `tools/rungic_dev.py`。发布仍由 `tools/rungic_release.py` 负责（docs/61）。

## 为什么需要

- **发布流程太重，不适合试验**：
  - `rungic_package.py` 不构建有未提交改动的包；
  - 整套发布经 Wi-Fi 部署要 10 分钟以上（docs/96）；
  - 上一次部署留下的 rootfs 快照没有 commit 时，部署会直接中止。
- **绕过发布也不行**：
  - 直接 `dpkg -i` 或替换文件，装上的版本和发布钉住的版本冲突，Discover 会把我们的包显示为“有更新”，要换回发布版本（docs/61，2026-09-28）；
  - 完整性检查也会报告不一致（docs/85、96）。
- **G100 S 是用户的日常机**：开发改动装上去必须看得见、能撤销，而且 apt 和 Discover 要把它当作已安装的系统，不能和它反着来。

## 其他发行版的做法

以下出自已有了解，这一轮没有重新核对源码。

| 系统 | 开发 | 发布 |
|---|---|---|
| AOSP | userdebug 版本上 `adb remount` 后用 `adb sync` 推送模块 | 签名的整包 OTA，带 build fingerprint |
| ChromeOS | `cros_workon` 从工作区构建，`cros deploy` 装单个包 | 整镜像，按渠道推送 |
| Ubuntu Touch | crossbuilder 从工作区构建 deb 并装到设备 | 系统镜像 |
| Fedora Silverblue | `rpm-ostree usroverlay` 叠一层临时可写层，重启即消失 | 不可变的 ostree 提交 |
| NixOS | `nixos-rebuild test`，不写启动项 | `switch` 加 `flake.lock` |

共同点有四条：

1. 开发产物一眼能认出来；
2. 设备知道自己偏离了哪个发布、偏离了什么；
3. 一条命令就能回到基线；
4. 发布只从干净的提交重建，从不复用开发产物。

## 做法

- **版本**：
  - 开发包的版本是 `<该包在发布里的版本>+dev<UTC 时间>.<短 sha>[.dirty]`，例如 `0.510+dev20260930t133300.32b158c.dirty`。它排在 0.510 之后、0.511 之前，这点由 `dpkg --compare-versions` 验证，写在测试里。
  - `.dirty` 表示这个包的构建路径里有未提交或新增的文件。
- **构建**：
  - `rungic_package.py` 的 `build_host` 和 `build_device` 新增开发模式：直接用工作区的内容，包括未提交的改动，以及 git 不忽略的新文件；版本由调用方给出。
  - 产物放到 `.work/apt/dev`，不进入发布仓库，也不写 `project-builds.json`。
- **传输：手机直接从 Mac mini 取包（2026-10-03，用户要求；AGENTS.md“设备之间直接传输”）**：
  - Mac mini 上构建的包（项目包、上游组件及其 dbgsym）不再拉回 K8：构建容器把它们留在 `/root/rungic-build/dev-pool/`，生成该包的 Packages 条目（`dpkg-scanpackages`）、大小和 SHA-256。
  - K8 的 `.work/apt/dev` 里只放 `<包>.deb.remote` 记录；`rungic_release.index` 把这些条目并进 Packages。
  - 同步时手机经受限密钥（`tools/pq/rungic-transfer get`）直接取，依次试 wire.net（`10.77.0.20`）和局域网（`192.168.5.45`）地址。每个文件核对大小和 SHA-256，最多三次。小文件（开发元包、在 K8 构建的包、索引）仍由 K8 发送。
  - 被新覆盖替换掉的包，在 `prune` 时从 K8 的记录和 Mac mini 的 `dev-pool` 一并删掉。
  - 原因：rungic-cua 有 67 MB（带 OCR 用的 OpenCV 和模型）。经 K8 中转时，Mac mini → K8 只有 0.1–0.5 MB/s，K8 → 手机又要 2.5–3 分钟；2026-10-03 一次 K8 → 手机的 tar 还中途断开（`Unexpected EOF`）。手机直接取实测约 12 MB/s，67 MB 用 5.6 秒。
  - 在手机上构建（`--host phone`）时照旧取回 K8。
  - 实测（2026-10-03，rungic-firefox、rungic-agent-screen、rungic-cua）：同步一步 25 秒（手机取 4 个包，K8 发 6 个小文件），此前同样三个包要 3 分多钟；rungic-cua 的构建一步 104 秒，此前含拉回 K8 为 224–622 秒。整次部署约 5.5 分钟，此前约 21 分钟。剩下较长的是安装后的验证（约 76 秒）。
- **开发元包**：`rungic-release=<发布>+dev<UTC 时间>`。
  - 依赖取发布的全部精确依赖，只把被覆盖的包换成开发版本。它和发布元包一样带 `Protected: yes`。
  - 其中的 `/usr/share/rungic/release.json` 记录基线发布及其包清单（`dev.base`、`dev.base_packages`），以及每个覆盖的版本、提交、是否 dirty 和构建时间。
- **设备上的文件**：
  - 仓库 `/var/lib/rungic-apt-dev`，标签 rungic-dev；
  - 源 `/etc/apt/sources.list.d/rungic-dev.sources`；
  - pin 文件 `/etc/apt/preferences.d/rungic-dev`：只列开发元包和被覆盖的包，优先级 1002，高于发布的 1001。apt 和 Discover 因此把开发版本当作候选，不会提示“更新”；其余包仍由发布的 pin 管。
- **安装**：沿用发布的 `apt_install`：在 transient unit 里按精确版本安装。
  - 重启规则与发布部署相同：变化的系统服务、桌面用户的相关服务，需要时重启会话。
  - 不做 rootfs 快照：撤销是包级的。快照回滚在 2026-09-27 损坏过一次镜像。
  - 发布快照未 commit 时也能部署开发覆盖。但之后如果执行 `rollback --snapshot`，开发覆盖会一起丢掉。
- **验证**：
  - 每个覆盖和开发元包，都要满足 apt 的 Installed 等于 Candidate；
  - 完整性检查认识开发覆盖：`rungic-integrity` 的 `release.dev` 列出基线和覆盖，`summary.state` 为 `development`；它写的两个 apt 文件不算未归属文件。
- **叠加与撤销**：
  - 再次部署时，之前的覆盖保留；开发元包的基线始终是原来那个发布。
  - `reset [包]`：先按基线版本（或剩余覆盖）安装，成功后才删除开发的源、pin 和仓库，并重写发布 pin。
- **与发布的边界**：
  - `rungic_release.py deploy` 安装成功、写好发布 pin 之后，会清掉开发覆盖的源、pin 和仓库（部署记录中的 `dev-overlay` 步骤）；
  - 安装失败时，开发覆盖和它的包都保持原样。
  - `rungic_release.py status` 多了一项 `dev_overlay`。
- **记录**：`.work/dev-deploy/<时间>-deploy|reset/dev.json`，旁边还有前后的包版本和完整性报告。
- **上游组件**（2026-10-01，用户要求“开发支持上游包”）：
  - `deploy NAME` 也接受发布要重建的上游组件，即 `release/packages.json` 的 `rebuilt` 中 `source` 为 `packages/<名>` 的那些，例如 `plasma-mobile`、`xdg-desktop-portal-kde`；
  - 构建复用 `tools/build_on_device.py`：同步补丁队列打好补丁的源码树，只在构建机那份树的 `debian/changelog` 顶部加一条开发版本 `<changelog 的版本>+dev<UTC 时间>.<短 sha>[.dirty]`，`packages/` 里的 changelog 不动；
  - 上一次构建成功、还留着 obj 树时，做增量构建，否则全量构建，全量前先装构建依赖；
  - 只取发布里登记的那几个二进制包（`rebuilt.<名>.packages` 中手机已装的发布包）及其 dbgsym，放进开发仓库。每个二进制包一条覆盖记录，带 `component`；
  - `.dirty` 看 `packages/<名>` 和配方 `overlay` 引用的共享文件；
  - `reset 组件名` 撤销这个组件的全部二进制包，`reset 包名` 只撤销那一个。

## 用法

```sh
python3 tools/rungic_dev.py deploy rungic-design rungic-voice-agent   # 从工作区构建并装到手机
python3 tools/rungic_dev.py deploy plasma-mobile                      # 上游组件：packages/plasma-mobile 的补丁队列
python3 tools/rungic_dev.py status                                    # 基线、覆盖，以及 apt 是否保留它们
python3 tools/rungic_dev.py reset [rungic-design]                     # 回到发布版本
```

- 设备包默认在 Mac mini 上构建（`--host macmini`）。
- 不用 `--host phone`：那会把构建依赖（Qt、CMake 的开发包）装进日常机。

## 未覆盖

- **Android 侧**：APK、`rungic-plasma` 控制器，以及发布清单里 `android` 列出的文件，还没有开发覆盖。现在仍要单独安装，并手动记录。合并到 main 之后可以出 dev 发布，它带 APK，部署时版本更低才装（[docs/109](109-dev-release-channel.md)）。
- **更快的一档**：只改 QML 或脚本时直接替换文件，还没做。现在每次都要构建完整的包。

## 实测

2026-09-30 首次使用，均为实机验证。机器：K8-Plus（x86_64，系统代理未设），构建机 Mac mini（chou-Mac-mini，arm64，Surge 127.0.0.1:6152），手机 G100 S（ZY32MVJS25，经 Wi-Fi/VPN）。

- **部署**：`deploy rungic-design rungic-voice-agent rungic-plasma-diagnostics`，基线是发布 20260930.9，它的 rootfs 快照还没 commit。
  - 三个包在 Mac mini 上分别构建了 135、128、98 秒；
  - 同步 11 个文件，安装成功；
  - 按 `user_restart` 重启了 rungic-voice-agent、语音浮层和 plasmashell。
  - 记录在 `.work/dev-deploy/20260930-223300-deploy/`。
- **apt 的态度**：
  - `status` 显示开发元包和 3 个覆盖的 Installed 都等于 Candidate；
  - `apt list --upgradable` 里没有 rungic 包；
  - `apt-get -s dist-upgrade` 不动任何 rungic 包，也就是 Discover 不会提示把它们换回去。
- **完整性**：新版 `rungic-integrity` 随 diagnostics 覆盖一起装上，`release.dev` 列出了基线和 3 个覆盖。`summary.state` 仍为 drift，唯一的项是部署前就有的未归属文件 `/usr/lib/rungic-cua/rungic_cua/keyring.py`（docs/96）。
- **还没实测**：`reset`。

## 事故：离线测试删掉了手机上的开发覆盖（2026-09-30）

- **现象**：正式部署 20260930.10 时，记录里没有 `dev-overlay` 这一步。可是部署前 `record` 那一步，设备上装的还是开发元包，而开发覆盖的源、pin 和仓库已经不在了。
- **排查**：
  - dpkg、apt 的历史里只有两次安装；
  - rootfs 快照的 commit 是 snapshot-origin 丢弃 COW，保留当前状态；
  - 容器启动脚本不碰 `/etc/apt`；
  - 对手机单独调用 `clear_device()`，它能正确找到并删除覆盖文件。
- **原因**：
  - `tools/test_rungic_release.py` 的部署测试替换了 `rungic_release` 模块里的 `run`；
  - 新加入部署流程的 `rungic_dev.clear_device()` 用的是 `rungic_device.run`，没有被替换；
  - `run-tests.sh` 跑到“安装后出错并回滚”那个用例时，这一步在真手机上执行了，删掉了开发覆盖。
- **影响**：从跑测试到正式部署的这段时间里，开发版本的包还装着，但没有对应的 pin，Discover 可能会提示把它们换回发布版本。正式部署按精确版本装好了发布，最终状态正确。
- **修复**：
  - `rungic_release.deploy` 把自己的 `run` 传给 `clear_device(runner)`；
  - `tools/conftest.py` 给 `tools/` 下的所有测试加了一道保护：替换掉 `rungic_device._run`，任何测试一旦走到 adb 就直接失败；
  - 部署测试里也加了同样的保护。
  - 用修复前的代码跑，这个用例失败；修复后全套 231 个测试通过。
- **结果**：发布部署时“清除开发覆盖”这一步仍然没有在实机上走通过。它的代码路径由离线测试覆盖。

## 发布 20260930.10（2026-09-30）

- **提交**：8bde518（设计系统的层级）、723d23d（开发覆盖）。
- **构建**：在 Mac mini 上重建了 3 个 stale 的包，`rungic-design`、`rungic-plasma-diagnostics`、`rungic-voice-agent`，版本都是 0.514。发布元包 `20260930.10` 固定 70 个包。
- **部署到 G100 S**：
  - 按用户选择，先 commit 了 .9 的快照；
  - 部署在后台运行，没有加客户端超时，结果 `ok`；
  - 过程：拍快照，同步 11 个文件，装好 4 个包（基线是开发覆盖 `20260930.9+dev20260930t133300`），写 71 条 pin，重启 rungic-voice-agent，冒烟验收通过；
  - 记录在 `.work/deploy/20260930-230919-20260930.10/`。
- **完整性**：drift，只有部署前就有的 `keyring.py` 这一项。
- **部署后**：
  - `rungic_dev.py status`：已安装和基线都是 20260930.10，覆盖为空，没有覆盖文件；
  - `tools/design_gallery.py phone` 截的 Toggle 和 HoldTarget 两节正常；
  - 用户接受 `.10` 后，执行了 `rungic_release.py commit`：快照已丢弃，容器重新启动并就绪（native Wayland）。

## 首次部署上游组件（2026-10-01，实机）

- 命令：`rungic_dev.py deploy plasma-mobile rungic-plasma-session`，见 docs/103 的 `startplasmamobile` 补丁。
- Mac mini 上 `plasma-mobile` 留有上一次构建的 obj 树，所以做了增量构建，用时 238 秒；覆盖 `plasma-mobile`、`plasma-mobile-tweaks`，版本 `6.6.5-0ubuntu0.1+rungic9+dev20261001t040034.3fa5265.dirty`。
- 安装成功，apt 校验通过；`plasma-mobile` 在 `session_restart` 里，会话按规则重启，报告 “Plasma Mobile ready”。
- 完整性 `drift` 是早已存在的 `/usr/lib/rungic-cua/rungic_cua/keyring.py`（不属于任何包），与这次无关。


## 新加入 `rebuilt` 的组件（2026-10-03）

- 问题：已装发布里没有的组件（例如刚加入 `rebuilt` 的 `ksystemstats`，发布 `20260930.10` 不含它），原来 `deploy` 直接拒绝：“the installed release has none of its packages”。
- 现在：这类组件以手机上已装的发行版版本为基准，覆盖记录里写 `base`（如 `6.6.6-0ubuntu0.1`）；再次部署沿用第一次记下的 `base`；`reset` 时把它按该版本装回（`apt-get install 包=版本`），而不是留在开发版本。离线测试 `test_component_new_to_the_release`、`test_reset_restores_the_distribution_build`。
- 实测：2026-10-03 部署 `ksystemstats`（增量构建 54 s），apt 校验通过；reset 路径尚未在实机执行。见 docs/104。

## 增量构建与缓存过期（2026-10-03，Mac mini 实测）

**起因**：一次部署 10 个自有包用了约 15 分钟，每个包 70–200 秒，即使只改了一行。用户要求改成增量构建，30 天没有增量编译过的缓存自动清空。

**原因**（逐步计时，`rungic-agent-screen`）：
- 每次构建前删掉 Mac mini 上的源码目录 `/root/rungic-packages/<包>/src`，CMake 的构建目录在源码树里，跟着被删，所以每次全量编译。
- 更大的开销是 SSH：一次构建约 80 次往返，每次新建连接约 3.3 秒；真正的编译与打包只占几秒。

**做法**：
- 源码树和构建目录保留（`rungic_package.sync_script`）：新源码先解压到 `incoming/`，按内容同步过去，内容没变的文件保留原来的时间，make/ninja 只重编改过的部分；从仓库删掉的源文件按上次的文件清单 `src.manifest` 删除，构建产物不在清单里，不受影响。第一次（没有清单）或 `--clean` 时直接用新树。
- 构建脚本：FFmpeg 的 `configure` 只在参数或脚本变了时重跑（它重写 config.h，会让所有对象重编）；snapshot 只在没有配置过时 `meson setup`；Flatpak GL 的 Mesa 构建目录保留，选项变了才重建。
- SSH 连接复用（`ControlMaster`，保持 10 分钟，socket 在 `$XDG_RUNTIME_DIR`）：单次往返从 3.3 秒降到 0.6 秒。
- 过期：每个构建目录（自有包的、上游组件的 `/root/rungic-build/<组件>`）构建时写 `.rungic-last-build`；每次构建前删掉 30 天没构建过的（`build_on_device.expire`，`CACHE_DAYS`）。只清这些构建目录，不动发布池、垫片和工具。
- 从头构建：`rungic_dev.py deploy 包 --clean`、`rungic_package.py build 包 --clean`。

**实测**（只构建，不部署）：同一个包没有改动时，120 秒（每次全量）→ 112 秒（只加增量，仍是全量的 SSH 开销）→ 36 秒（加连接复用），其中约 12 秒是实测脚本把 deb 拉回 K8；部署时 deb 从 Mac mini 直接送到手机，没有这一段。第二次构建的日志里没有编译输出。

## Mesa 与 Debian Meson 组件的开发构建（2026-10-03）

- USB G100（ZY32M9MRVP）从 `20260930.19` 更新组件时发现两处工具路径不匹配；两次错误均发生在构建阶段、覆盖安装之前。
- Mesa 配方固定源码 `98f3d622`，只有补丁和 changelog，没有 Debian control/rules。使用既有 `desktop/mesa-meson-options` 和 `desktop/package-mesa.py`，由 `build_mesa.package` 按开发版本打包；不对它调用 `apt-get build-dep .`。测试 `test_mesa_uses_its_meson_packager_and_records_the_development_version` 验证版本、包记录与构建选择。
- Flatpak `1.16.6` 的 Debian rules 明确使用 `dh --buildsystem=meson`，保留的 obj 树内为 `build.ninja`，原工具却无条件调用 make，实测报“no makefile found”。增量入口先检查 `build.ninja`，有则调用 Ninja，否则使用 make，再执行原有 `debian/rules binary`。这同时适用于 Debian Meson 的 Xwayland，无需新增配方特判。
- 离线回归用真实 make/Ninja 构建小型源码树，再由打包脚本核验编译结果；第二次修改输入后重跑，检查新内容进入打包结果。测试 `test_incremental_build_compiles_make_and_ninja_trees_before_packaging` 覆盖两种生成器，离线测试不访问手机。

## USB G100 补齐当前部件与 Android 入口（2026-10-03）

用户指定“正在插着的机器”：USB G100 `ZY32M9MRVP`，不是日常 G100 S。走开发覆盖；基线仍为 `20260930.19`，已有 rootfs snapshot 保留，没有提交快照、重刷底座或清数据。

### 源码与更新范围

- 部件构建源码 `6aff36bbee6709e420ec2d9b638f3fe6fc320f4d`，分支 `deploy/g100-components-20261003`：main `331bbdea` 加已安装的 Codex 默认桌面改动和禁用 AgentScreen 时的启动黑窗修复（`38933f6c`）。截至 `4497127e` 的后续构建/传输工具和验收契约修正不改变这些部件的输入，逐项核对 `identity_paths`。
- 重建并安装 16 个自有包：agent-screen、cast、codec、design、docker、firefox、flatpak-gl、plasma-bridges、plasma-config、plasma-diagnostics、plasma-input、plasma-recording、plasma-services、plasma-session、snapshot、suggestions，完整包名前缀为 `rungic-`。voice-agent、cua、codex 三个已有覆盖的源码路径与整合版本一致，保留它们的版本。
- 重建 8 个上游组件，覆盖 20 个二进制包：KWin `6.6.6+rungic9`、Mesa `26.3.0~devel20260824+rungic3`、Plasma Mobile `6.6.5+rungic9`、portal `6.6.6+rungic2`、ksystemstats `6.6.6+rungic1`、Flatpak `1.16.6+rungic1`、Xwayland `24.1.10+rungic2`、Polkit KDE `6.6.4+rungic1`。完整 Debian 版本见部署记录。
- 已安装开发元包 `20260930.19+dev20261003t130244`，共 39 项覆盖，36 个包本轮更新、3 个保留。Mac mini ARM64 构建，手机直取，大小及 SHA-256 验证；记录 `.work/dev-deploy/20261003-210244-deploy/`，最终 `result=ok`。
- 原机没有 ksystemstats 和 libflatpak0；先通过 APT 安装发行版基线（分别 `6.6.6-0ubuntu0.1`、`1.16.6-1`，以及 libnl 依赖），模拟确认不移除包，再加开发覆盖。新组件的记录包含发行版 `base`，reset 可按该版本恢复；本轮未执行 reset。
- Android APK `2.29/77` → `2.30/78`，`adb install -r` 保留数据。原生宿主从当前输入重新构建；APK SHA-256 `b363fd5a0ed4f1bbffb5df867f5da784b85d07c0bf8c2c2cd8f454ce38a28ac8`，原生输入指纹 `8b9cc1cb6fa2ad0aac8ead3e7b76ae2915de3035130bd7678b1ebffc7a59da2b`。安装后的 APK、三份提取库哈希全部匹配；UID、socket 目录 inode、应用设置文件哈希未变。OCR none 与之前的 APK 相同。
- Android 清单 9 项中只有 `system/rungic-plasma` 内容改变，通过 `sync_android` 备份后原子更新，SHA-256 `c3ec1ded3a311262a7ebe395a71ffc61f41eb7177008e02754ee8b664af0c59b`。没有替换其他 Android 配置文件。

### 本轮发现并修正的部署缺口

- Mesa/Meson 入口及 Debian Ninja 增量构建见上一节。Xwayland 旧缓存缺少当前构建容器的 libXfont2 开发库：工具在失败后走完整构建，补齐构建依赖，成功构建；手机不承担构建任务。
- 构建池清理把 shell 引号写进双引号中的匹配字符串，含 `~` 的 Mesa 包被误删。改为位置参数逐项比较精确文件名；实测保留 `~`、空格和 `$()` 等字面文件名，旧包删除、空保留集清空。修复前回归失败，修复后通过。原始产物还在，按既有 SHA 恢复，无需重编译。
- 直取脚本从 stdin 执行，SSH 也读 stdin，长脚本后面的命令被吞掉，出现截断参数。只给 `get` 重定向 `</dev/null`，上传 `put` 仍可读取内容；真实 shell 加会读取 stdin 的 SSH 替身连续传 180 个包，修复前丢后续命令，修复后全部内容与哈希匹配。
- 旧发布的系统服务名称过时，三项网络/蓝牙/调制解调器桥接进程仍从 9 月 30 日运行。开发重启入口合并工作区当前 `service_restart`；本轮补重启三个服务，全部 active，进程时间更新到 `2026-10-03 13:27:11 UTC`。隔离 shell 回归验证使用新服务名，未变的部件不重启。
- 主会话重启不替换所有后台工作区。发现工作区 0 的 KWin 仍映射已删除的旧 executable，确认没有 busy 标记、助理任务或通话后单独重启该工作区；工作区 1 本轮已重建。按每个 KWin 的 `/proc/PID/exe` 哈希与当前 `/usr/bin/kwin_wayland` 比较，不只检查安装版本。
- host-controller 验收只列出 schema 2，误报 G100 原有的 `portov-20260928.5` 就绪记录（无 schema/error）。`FirstBootState` 源码明确兼容 v1，补齐契约的旧格式回复；真实 Java 消费者验证可进入、release 不匹配及未来 schema 仍阻止进入。没有改写旧安装记录，也没有把部件升级记为新版首装验收。

### 验收与边界

- 40 项 APT 校验（元包加全部覆盖）Installed = Candidate = 记录目标；完整性 release mismatch 0、changed 0。missing 从 308 降到 305，恢复 portal、Polkit、Snapshot 三份中文翻译；按路径比较没有新增缺失。usr 未归属仍 5、etc 从 11 降到 9，属于已存在漂移；不是全系统无漂移。
- 15 项实机检查全部通过：会话稳定、关键用户服务、无新增未知崩溃，以及 platform、communication-audio、clipboard、network、bluetooth、telephony、camera、audio、host-controller、shared-storage、GPU、wifi-display 的只读契约。GPU 实际通过 KGSL/GBM/EGL 离屏渲染；平台接口 8 个只读请求通过。
- 5 项整合源码的原生系统回归通过：desktop_mode_window、desktop_mode_fullscreen、assistant_app、cua_desktop、phone_session_units。工具相关 31 个用例及 16 个 subtest 通过，另外 Android 文件处理 4 个、真实 Java 契约 4 个通过。Fedora 缺 `dpkg-parsechangelog` 的既有用例单独列为环境限制，不计为通过。
- 手机离屏渲染 ChoiceRow、ToggleRow、ChoiceSheet 的浅色/深色总览，6 张截图已检查；ChoiceSheet 这一节展示打开入口，未作为实际弹层打开的交互验收。不会在手机屏幕开窗口。
- 账户保留，SSH socket enabled/active，桌面模式与助理画面开关未改变。原生库/控制器、实际进程、APT/完整性差异和截图证据均在 `.work/verify/20261003-g100-components/`。没有重测通话、微信等完整交互流程；这些只读契约不代替产品使用验收。本轮开发覆盖不发行新 rootfs。

- 提交 PR 前分支同步 main `6cfd7157`（已合并 Codex PR #3 与黑窗 PR #4），工具 31 项及 16 个 subtest、真实 Java 契约 4 项再次通过。此同步没有重新部署手机；main 后续的通话步骤取消（`4514f042`）和 CUA 录音依赖（`4e18c5b3`）不包含在上述设备验收中，设备版本仍以构建源码 `6aff36bb` 和部署记录为准。
