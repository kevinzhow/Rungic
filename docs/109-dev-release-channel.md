# dev 发布渠道（2026-10-04）

工具：`tools/rungic_release.py` 的 `dev`、`export`、`publish` 子命令，`deploy --all`、`deploy --from`、`status --all`。发布机制本身见 [docs/61](61-delivery-diagnostics-plan.md)，开发覆盖见 [docs/97](97-local-development-deploy.md)，操作步骤见 skill `.agents/skills/rungic-dev-release/SKILL.md`。

## 为什么

2026-10-04 的现状：

- 发布只能从某台开发机经 adb 一台一台地推。
- 每台开发机有自己的 APT 仓库（`.work/apt`），发布号在不同机器上各自增长，同一个号可能是不同的内容。
- 最后一个正式发布是 `20260930.10`。之后合并到 main 的改动只以开发覆盖的形式散落在各台手机上：G100 S 当天装的是 `20260930.10` 之上的 25 个覆盖，`status --all` 显示它的基线提交比 origin/main 落后 195 个提交。
- APK 不属于发布，要另装、另记。
- 没有一个地方能看到出过哪些发布、哪台手机装了什么。

Kevin 批准的做法：只从 origin/main 出发布；APK 是发布的一部分；能一次部署到所有连着的手机、看所有手机的状态；发布可以作为 GitHub 预发布公开（要他确认）；部署历史随仓库提交。另外要查清 Ubuntu 的更新会不会换掉我们定制的包。

## 实现

### 出发布：`rungic_release.py dev`

1. **只从 origin/main**：先 `git fetch origin main`，HEAD 必须等于 origin/main，工作区必须干净（包括未跟踪文件），否则拒绝。这样任何一台机器出的 dev 发布都是 main 的某个提交，不会把本机的试验带进去。
2. **构建发布缺的东西**（构建机默认 Mac mini，`--host`）：
   - 自有包：`rungic_package.current()` 判为过期的，用 `rungic_package.build()` 构建；
   - 上游组件：`release/packages.json` 的 `rebuilt` 里来自 `packages/<名>` 的组件，changelog 版本的包不在仓库里的，经 `build_on_device.py` 构建并 `collect` 进仓库。和 `rungic_dev.py` 一样，上次构建成功、留着 obj 树时增量构建，否则全量构建；Mesa 用它的 Meson 构建和 `build_mesa.package`。
   - 每次组件构建把 `packages/<名>` 及配方 overlay 文件的 git 树哈希记在 `.work/apt/component-builds.json`。补丁队列改了却没加 changelog 条目时，仓库里已有同版本的旧构建，`dev` 会拒绝并要求加一条 changelog（新版本），不会把旧内容当新版本发出去。
   - APK：`android/build-apk.sh`（输出目录由 `RUNGIC_APK_OUT` 指定），或 `--apk 文件`，或 `--no-apk`。原生库仍来自 `android/build-native-core.sh` 的产物。
3. **发布记录**：`build(channel='dev', apk=...)`，元包里的 `/usr/share/rungic/release.json` 和 `.work/apt/releases/<版本>.json` 都多了 `channel`（`dev`；`build` 出的正式发布是 `release`；更早的发布没有这个字段，按正式发布显示）和 `apk`（文件名、包名、versionName、versionCode、sha256、大小）。APK 存在 `.work/apt/apk/Rungic-<versionName>-<versionCode>-<sha 前 12 位>.apk`。
4. **版本号**：仍是 `YYYYMMDD.N`，但要避开本机仓库的号、origin 上已发布的 `dev-*` tag（`git ls-remote`）和 `release/history.json` 里部署过的号。
5. **发布包**：见下一节。
6. `--publish`：生成 GitHub 预发布的命令和说明；只有再加 `--yes` 才真的发布，见“GitHub 预发布”。

### 发布包与 `deploy --from`

`dev` 结束时（或 `export 版本`）写出 `rungic-<版本>.tar`，默认放在 `.work/release-bundles/`，`--out` 或 `RUNGIC_RELEASE_OUT` 可改：

- `repo/`：元包和它精确依赖的、在仓库里的 deb，以及只含这些包的 `Packages`、`Release`；
- `apk/`：发布的 APK；
- `android/<sha256>`：发布清单里的 Android 侧文件；
- `manifest.json`：发布记录、每个文件的 SHA-256，以及 `from_archive`：耦合包（plasma-workspace 等）是 Ubuntu 的，从 Ubuntu 源装，不打进包里。

调试符号包（`-dbgsym`）不在发布包里。

`deploy --from rungic-<版本>.tar` 让没有构建的机器也能部署：先按 manifest 逐个核对文件，再把 deb 导入本机仓库（`import_debs`：同名 deb 内容不同就拒绝）、APK 和 Android 侧文件放进 `.work/apt/apk`、`.work/apt/android`，写入发布记录。本机已有同号发布、但提交或包清单不同，也拒绝。两台机器的构建因此不会混在一起。部署时 Android 侧文件先找工作区，再找导入的副本，最后找发布提交里的版本。

### APK 是发布的一部分

部署在容器一侧装好、写好 pin、同步 Android 侧文件、按规则重启之后，再处理 APK（部署记录里的 `apk` 一步）：

- 用 `dumpsys package com.rungic.plasma` 读手机上 APK 的 versionCode。
- 比发布的低：核对 `.work/apt/apk` 里文件的 sha256，从本机 `adb install -r` 安装（保留数据；Magisk root 下的 `pm install` 曾出现 Binder 错误，见 AGENTS.md），然后 `am start -n com.rungic.plasma/.MainActivity` 重新打开，与 docs/96、97 手动更新 APK 后的做法一致；再确认 versionCode，等会话就绪（`apk-session`），验收从这之后开始统计。
- 不低于发布的：不动（`current`）。没有装 APK 的手机：不首装（首装是 `tools/ci/standalone.py` 的事）。`--restart never`：不装，记为 `skipped: --restart never`，因为换 APK 会让桌面重启。
- 装不上或装完版本不对：部署失败，按原有规则回到快照。APK 本身不会被降级：快照回滚和按包回滚都不动 APK，`adb install -r` 也只往高版本装。

2026-10-04 G100 S 上的 APK 是 2.33（versionCode 81），main 的 `AndroidManifest.xml` 是 2.32（80）：手机上的是开发时单独装的更新版本。此时从 main 出的 dev 发布不会改动它的 APK。以后改了 APK 要同时提高 versionCode，否则已装同号 APK 的手机不会更新；`dev` 发现 APK 内容变了而 versionCode 没变时会打印提示。

### `deploy --all`

- 列出 `adb devices -l` 里状态为 `device` 的设备，逐个 `getprop ro.serialno`，并以 root 检查 Android 侧启动器 `/data/adb/rungic-plasma/rungic-plasma` 是否存在。没有的（模拟器、没 root、未授权）列在 `skipped` 里，写明原因。同一台手机同时走 USB 和 Wi-Fi 只算一次。
- 逐台部署：`rungic_device.selected(序列号, transport)` 在这段时间里设置 `RUNGIC_SERIAL`、`RUNGIC_TRANSPORT`，并清掉上一台的缓存（transport、APK 包名、传输目录）；工具启动的 `rungic_plasma.py` 子进程继承这两个变量。部署记录目录为 `<时间>-<版本>-<序列号>`。
- 某台失败或连不上就记下来，继续下一台；最后打印汇总表（序列号、型号、部署前后的发布、APK 结果、结果），任何一台不是 `ok` 时退出码为 1。
- 可以和 `--from` 一起用：先导入发布包，再部署到所有手机。

### `status --all`

每台一行：已装发布、渠道、发布记录的提交、比 origin/main 落后的提交数（本机 clone 里没有那个提交时为空）、APK 版本、开发覆盖数、apt 保护状态（见下文：没 pin 的包数、元包是否 Protected、unattended-upgrades 名单是否在）。单台的 `status` 也多了 `channel`、`apk`、`apt` 三项。

2026-10-04 在 G100 S 上只读运行的结果：

```
serial      model     release                         channel  commit        behind_main  apk      overlays  apt
ZY32MVJS25  XT2537_4  20260930.10+dev20261004t044524  release  723d23d015be  195          2.33/81  25        no unattended-upgrades list
```

落后数按基线发布的提交计算；开发覆盖来自更新的提交，各自的提交在 `rungic_dev.py status` 里。`no unattended-upgrades list` 是因为按发布生成的名单要到下一次部署才写。

### GitHub 预发布

`publish 版本`（或 `dev --publish`）：

- 说明由 git 生成：上一个 dev 发布（本机的 dev 发布记录或 `dev-*` tag）以来 main 上的 first-parent 提交，以 `(#N)` 结尾的列为合并的 PR，其余列为其他提交；与上一个 dev 发布相比的包版本变化；APK；部署命令。
- 命令：`gh release create dev-<版本> --repo kevinzhow/Rungic --prerelease --target <提交> --title "Rungic dev <版本>" --notes-file <说明> <发布包> <APK>`。
- 不加 `--yes` 只返回这条命令和说明文件的位置，不访问 GitHub。发布会创建 tag、对外可见，必须先给 Kevin 看，得到确认后再执行 `publish 版本 --yes`。只有 `channel` 为 `dev` 的发布能这样发。
- 这次实现没有对 GitHub 实际执行过，没有建 tag，也没有推送 tag。

### 部署历史 `release/history.json`

每次部署（成功、验收失败、中止，以及 `deploy --all` 里出错或连不上的手机）追加一行：`time`、`version`、`commit`、`channel`、`serial`、`result`。这个文件随仓库提交；`.work/deploy/history.json` 仍是本机给 `rollback` 用的记录。

## 我们的包会不会被 Ubuntu 更新换掉

### 现有机制（docs/61，2026-09-28）

1. `/etc/apt/preferences.d/rungic`：我们的仓库整体只有 100，低于 Ubuntu 源（500），仓库里留着的旧构建不会成为候选。
2. `/etc/apt/preferences.d/rungic-release`：每次部署写入，发布里的每个包（含元包）按精确版本 pin 在 1001。开发覆盖另写 `/etc/apt/preferences.d/rungic-dev`，覆盖的包在 1002。
3. 元包 `rungic-release` 精确依赖全部发布包，带 `Protected: yes`。
4. rungic-plasma-config 带的 `51rungic-unattended-upgrades`：一份手写的 unattended-upgrades 黑名单（按包名前缀）。

### 只读核对（G100 S `ZY32MVJS25`，`10.77.0.16:35577`，2026-10-04）

全部在容器里执行（`adb -s 10.77.0.16:35577 shell "su -c 'p=/data/adb/rungic-plasma/rungic-plasma; exec \$p exec sh'"`，脚本经 stdin），只用了 `apt-cache policy`、`apt-get -s`、`apt list`、`apt-config dump`、`dpkg-query`、`apt-mark showauto/showhold`、`systemctl is-enabled/is-active/list-timers`、读 `/etc/apt` 和日志。没有安装、删除、升级、改配置或重启任何东西。当时装的是开发覆盖 `20260930.10+dev20261004t041303`（核对过程中被更新为 `…t044524`，不是这次操作造成的）。

- **pin 生效**：`apt-cache policy` 中
  - `kwin-wayland`：Installed = Candidate = `4:6.6.6-0ubuntu0.1+rungic9+dev20261002t192629.f7ffbe5`（1002）；发布版本 `+rungic9` 为 1001；Ubuntu 的 `4:6.6.6-0ubuntu0.1` 为 500；仓库里 `+rungic1…8`、`+moto17…22` 都是 100。
  - `powerdevil`、`libkwaylandclient6`、`xwayland`、`flatpak`、`mesa-libgallium`：已装的 `+rungicN` 为 1001，是候选；Ubuntu 版本 500。`mesa-libgallium` 仓库里有更高的 `26.3.0~devel20260929+rungic4`，优先级 100，不是候选。
  - 耦合包 `plasma-workspace`、`libplasma7`：已装的 `6.6.6-0ubuntu0.1` 为 1001。
- **升级不动我们的包**：`apt list --upgradable` 只有 firefox、glycin、linux-libc-dev、ubuntu-cloud-minimal 等 12 个；`apt-get -s dist-upgrade` 为 “12 upgraded, 1 newly installed, 0 to remove”，没有任何发布包、耦合包或 rungic 包。
- **元包不会被顺带删掉**：`apt-get -s remove rungic-release` 和 `apt-get -s remove --auto-remove rungic-release` 都只删元包，并提示 “WARNING: The following essential packages will be removed”（Protected 被当作 essential，需要明确输入确认）。发布包全部是手动安装（`apt-mark showauto` 与发布清单没有交集，因为部署按 `包=版本` 逐个点名安装），元包没了也不会被 autoremove；没有 hold。
- **unattended-upgrades 在容器里运行**：`20auto-upgrades` 为 `Update-Package-Lists "1"`、`Unattended-Upgrade "1"`；`apt-daily.timer`、`apt-daily-upgrade.timer`、`unattended-upgrades.service` 都是 enabled/active。允许的来源是 `resolute`、`resolute-security` 和两个 ESM。日志里每天运行，最近几天升级过 libheif、libevent、gstreamer1.0-plugins-good、libkf6coreaddons6、openssl、libmlt 等；`/var/log/apt/history.log*` 里由它发起的升级从来没有涉及发布包（发布包的变化全部来自部署工具）。版本：unattended-upgrades 2.12ubuntu9、apt 3.2.0、python3-apt 3.1.0ubuntu1.1、packagekit 1.3.4-3ubuntu1.2，与开发机（K8-Plus，Ubuntu 26.04.1）上的 unattended-upgrades 版本相同。
- **耦合包的同源兄弟没有保护**：plasma-workspace 源还装着 `libtaskmanager6`、`libkworkspace6-6`、`libnotificationmanager1`、`libkmpris6`、`libklipper6`、`libbatterycontrol6`、`libkfontinst6`、`libkfontinstui6`、`libklookandfeel6`、`plasma-workspace-data`、`plasma-workspace-dev`；libplasma 源还有 `libplasma-dev`、`plasma-desktoptheme`。它们不在发布里，优先级 500（`apt-cache policy libtaskmanager6`）；plasma-workspace 对它们的依赖只有 `>=`（如 `libtaskmanager6 (>= 4:6.6.0)`、`plasma-workspace-data (>= 4:6.6.6-0ubuntu0.1)`）。静态黑名单的 `plasma-workspace`、`libplasma` 前缀只盖住其中三个。
- **改名前的残留**：`moto-plasma-config` 处于 `rc` 状态，留下 conffile `/etc/apt/preferences.d/moto`（`Pin: release o=moto,l=moto-plasma`，1001）和 `51moto-unattended-upgrades`；`moto.sources` 指向 `/var/lib/moto-apt`，与 `/var/lib/rungic-apt` 是同一目录（inode 202993），Release 的 origin 已是 rungic，所以这条 1001 现在不匹配任何东西（policy 里 moto-apt 为 100）。这次没有清理，需要时 `dpkg --purge moto-plasma-config`，由 Kevin 决定。

### 离线模拟（真实 apt 与 unattended-upgrades）

`tools/test_rungic_release_channel.py` 的 `ProtectionTests` 在开发机上用真实的 `apt-get` 和 `/usr/bin/unattended-upgrade`（作为模块载入，调用它自己的 `UnattendedUpgradesCache` 和 `calculate_upgradable_pkgs`），对一个独立的 apt 根目录：我们的仓库有元包和 `kwin-wayland 1.0+rungic1`，“Ubuntu”源（origin Ubuntu，unattended-upgrades 允许）有更高的 `kwin-wayland 1.1`、耦合包 `plasma-workspace 2.1`、兄弟包 `libtaskmanager6 2.1` 和无关的 `libdemo1 1.1`；pin 和名单取自 `pin_release` 实际生成的脚本，静态名单用仓库里的 `51rungic-unattended-upgrades`。

| 状态 | `apt-get -s dist-upgrade` | unattended-upgrades |
|---|---|---|
| 部署后（pin、兄弟包 pin、生成的名单、元包） | 只升级 `libdemo1` | 只升级 `libdemo1` |
| 元包被删掉，pin 还在 | 只升级 `libdemo1` | 只升级 `libdemo1` |
| 没有 pin，只有静态名单 | — | 升级 `libdemo1` 和 `libtaskmanager6` |
| 没有 pin，有生成的名单 | — | 只升级 `libdemo1` |

结论：

- unattended-upgrades 认 pin。它把不允许的来源（我们的仓库）设成 -32768，但已装版本还有 dpkg status 这个来源，版本 pin 仍然是 1001，`find_better_version` 要求新版本的优先级不低于已装版本，所以不升级。代码见 unattended-upgrades 2.12 的 `pinning_from_config`、`find_better_version`。
- 元包被删掉后，发布包的版本仍由 pin 保持；pin 文件不属于任何包，不随元包消失。
- 没有 pin 的时候，静态名单是唯一的保护，而它漏了 `rebuilt` 后来加入的组件和兄弟包。

Discover 经 PackageKit 的 apt 后端使用 libapt 的候选版本，和 `apt-get` 一样受 pin 约束；这次没有在手机上单独查询 PackageKit（会启动它的守护进程），属于推断。

### 发现的缺口与处理

1. **兄弟包**：部署时 `release_siblings()` 在手机上查 `dpkg-query` 的 `${source:Package}`，找出与发布包同源、已装但不在发布里的包，按已装版本一起写进 `rungic-release` pin（1001）。效果：Ubuntu 发布 plasma-workspace 的更新时，它的私有库不会单独升级、和被 pin 住的 plasma-workspace 错开。代价与耦合包相同：这些包的安全更新要等下一个发布（docs/61“安全更新延迟”）。
2. **unattended-upgrades 名单跟着发布走**：部署另写 `/etc/apt/apt.conf.d/52rungic-release`，按精确名字列出发布包、兄弟包和元包（`名字$`，`.`、`+` 用方括号转义，apt.conf 里没有转义符）。它只在 pin 缺失时起作用。静态的 `51rungic-unattended-upgrades` 保留不动。
3. **看得见**：`status`、`status --all` 报告 apt 的保护状态：发布包里有没有哪个的 pin 不是它的发布版本（开发覆盖的 1002 也算），元包是否 Protected，名单是否在。
4. **耦合包在别的手机上装不回来**：耦合包的版本取自出发布时那台手机上装的版本（默认手机，或 `--coupled-json`）。Ubuntu 的 `-updates` 索引只留最新版本，另一台手机如果装着别的版本，部署时要下载的旧版本可能已经不在索引里，安装一步会失败（`deploy --all` 记为该手机失败，继续下一台）。这次没有解决；办法可以是把耦合包的 deb 也收进仓库，或者出发布时核对所有手机的耦合版本，待定。

没有改：仓库整体 pin 100、元包 Protected 的现有做法；它们按上面的核对是有效的。

## 用法

```sh
# 出一个 dev 发布：干净的 origin/main 工作树里
python3 tools/rungic_release.py dev [--host macmini] [--out DIR] [--apk FILE | --no-apk] [--note "…"]
python3 tools/rungic_release.py export 20261004.1 --out DIR      # 已有发布的发布包

# 部署
python3 tools/rungic_release.py deploy 20261004.1 --all           # 所有连着的 Rungic 手机
python3 tools/rungic_release.py deploy --all --from rungic-20261004.1.tar

# 状态
python3 tools/rungic_release.py status --all

# GitHub 预发布：先看命令和说明，Kevin 确认后再加 --yes
python3 tools/rungic_release.py publish 20261004.1
python3 tools/rungic_release.py publish 20261004.1 --yes
```

部署仍然经 Wi-Fi 要十分钟上下，多台手机是依次进行的，放在后台运行，不加客户端超时（docs/96）。

## 离线测试

`tools/test_rungic_release_channel.py`（新）和 `tools/test_rungic_release_deploy.py`（补充）：

- `dev` 拒绝 HEAD 不是 origin/main 或工作区不干净；版本号避开 tag 与历史；只构建过期的自有包；组件缺版本就构建，补丁变了没加 changelog 就拒绝；组件有保留的 obj 树时增量构建、否则先装构建依赖再全量构建，Mesa 走它的打包，构建后收进仓库并记下树哈希，构建失败就停；构建顺序和发布记录里的 channel、APK；APK 记录取自它的 manifest。
- 发布包导出到另一台“机器”（独立的仓库目录）后导入并找得到 Android 侧文件；同号不同提交、同名不同内容、文件被改的发布包都被拒绝，且损坏时什么都不导入。
- APK：低版本才装、装后重新打开；当前或更新的不动；没装的不首装；`--restart never` 不装；装失败或文件不对时部署失败。部署里 APK 在 pin 之后、验收之前，之后等会话就绪；成功和中止的部署都写进历史。
- `deploy --all`：只挑 Rungic 手机、同一台只算一次；一台失败后继续下一台，环境变量逐台切换并在结束后恢复；失败的手机也进历史；汇总表。`status --all` 每台一行。
- `publish`：说明里有 PR、其他提交、包版本变化、APK；不加确认不调用 gh，确认后按同一条命令调用一次；非 dev 发布拒绝。gh 在测试里是替身，不访问 GitHub。
- 保护：上面的离线模拟；生成的名单用 `apt-config` 解析，只匹配精确的包名；兄弟包的识别；状态里对 pin 的读取。

## 还没有验证

- 实机没有执行过 `dev`、`deploy --all`、`deploy --from`、APK 安装这一步和新的 pin/名单写入；`status --all` 只在 G100 S 上只读运行过一次。
- 两台以上手机同时连着时的 `deploy --all`、同一台手机两条连接的去重，只有离线测试。
- Mac mini 上经 `dev` 构建上游组件（`build_component`）和 APK 构建（需要本机的 SDK、NDK 和原生库）没有实际运行。
- GitHub 预发布没有执行，需要 Kevin 确认。
- PackageKit（Discover）对 pin 的态度没有在手机上单独查询。
- 耦合包旧版本下载不到的问题没有解决（缺口 4）。
