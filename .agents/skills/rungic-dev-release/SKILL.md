---
name: rungic-dev-release
description: 本项目改动上机的路径：本地开发覆盖（tools/rungic_dev.py，工作区构建，装在已安装发布之上，可见可撤销）、dev 发布渠道（rungic_release.py dev 只从 origin/main 出发布，含 APK，deploy --all / status --all 覆盖所有连着的手机，GitHub 预发布要用户确认）与正式发布（提交 → rungic_package 构建 → rungic_release 元包 → 部署、验收、快照）。用于“改完装到手机看看”“部署到手机”“出一个发布”“所有手机更新到 main”“回到发布版本”，以及设计系统/界面改动的实机核对（状态总览截图）。
---

# Rungic 开发覆盖与正式发布

用户于 2026-09-30 要求区分开发和发布两条流程，2026-10-04 批准了 dev 发布渠道。方法和实测结果见 [docs/97](../../../docs/97-local-development-deploy.md)，发布机制见 [docs/61](../../../docs/61-delivery-diagnostics-plan.md)，dev 渠道和 apt 保护见 [docs/109](../../../docs/109-dev-release-channel.md)，最近一次发布的经验见 [docs/96](../../../docs/96-desktop-recovery-after-apk-restart.md)。先读 `AGENTS.md`。

## 选哪条

| 情况 | 路径 |
|---|---|
| 试一下改动、给用户看效果、调试 | **开发覆盖**：`rungic_dev.py deploy`，不需要提交 |
| 改动已合并到 main，要让手机（一台或全部）用上 main | **dev 发布**：`rungic_release.py dev`，再 `deploy --all` |
| 用户说“提交 / 出发布 / 正式部署” | **正式发布**：提交、构建、部署 |
| 撤销试验 | `rungic_dev.py reset [包]` |

- 不要用 `dpkg -i` 装包，也不要直接替换容器里的文件。那样 apt 和 Discover 会想把包换回发布版本，完整性检查也会报漂移（docs/61）。
- G100 S（ZY32MVJS25）是用户的日常机。装开发覆盖可以，但必须经工具，保证状态里看得见、能撤销。
- 开发覆盖只管容器里的 deb。APK、`rungic-plasma` 控制器和发布清单里的 `android` 文件还没有覆盖机制，要单独安装，并在 docs 中记录。dev 发布带 APK，部署时手机上的版本更低才装（docs/109）。

## 每次开始前

1. **核对本机**（AGENTS.md「网络」）：`hostnamectl`、`uname -m`、`ip route`，以及系统代理。
2. **核对构建机**：在 Mac mini 上执行 `hostname; uname -m; route -n get default; scutil --proxy`，并确认容器 `rungic-build` 在运行。设备包默认在这里构建。
   - 不要用 `--host phone`：它会把 Qt、CMake 等构建依赖装进日常机，重任务还会引发低内存查杀。
3. **核对手机**：`adb devices -l`，按序列号确认是哪台手机。adb server 是共用的，不能 `kill-server`；也不要断开 Wi-Fi。
4. **看手机现状**：`python3 tools/rungic_release.py status --all`，每台手机一行（发布、渠道、落后 main 多少、APK、覆盖数、apt 保护）；单台细节用 `python3 tools/rungic_dev.py status`（基线和覆盖）和 `python3 tools/rungic_release.py status`（rootfs 快照、完整性）。

## 开发覆盖

```sh
python3 tools/rungic_dev.py deploy <包>...      # 后台运行，不要套短超时
python3 tools/rungic_dev.py status
python3 tools/rungic_dev.py reset [<包>...]
```

- **包名**：取 `packaging/<名>/package.json` 里 `paths` 覆盖到改动文件的那些包。`tools/rungic_package.py list` 里显示 stale 或 uncommitted 的就是它们。
- **上游组件**：改了 `packages/<名>` 的补丁队列（例如 `plasma-mobile`），直接 `deploy <组件名>`。它经 `build_on_device.py` 在 Mac mini 上构建，第一次是全量构建，可能要几十分钟；只覆盖发布里登记的那几个二进制包。`reset <组件名>` 一次撤销全部（docs/97）。
- **版本号**：`<该包在发布里的版本>+dev<UTC 时间>.<短 sha>[.dirty]`。再次部署时，之前的覆盖保留，基线不变。
- **部署后逐项核对**：
  - 记录里 `[verify] apt=ok`，也就是每个覆盖都满足 Installed 等于 Candidate；
  - 可选：在容器里跑 `apt list --upgradable` 和 `apt-get -s dist-upgrade`，确认不涉及任何 rungic 包；
  - 完整性检查：`release.dev` 列出了覆盖；如果 `summary.state` 是 drift，逐项看是不是部署前就有的；
  - 记录在 `.work/dev-deploy/<时间>-deploy/`。
- **会重启什么**：按 `release/packages.json` 的 `user_restart`、`service_restart`、`session_restart` 重启。比如 `rungic-design` 会重启 plasmashell 和语音浮层。提前告诉用户。
- **rootfs 快照**：开发覆盖不做快照；上一次发布的快照没 commit 也不影响开发部署。但之后执行 `rollback --snapshot` 会连覆盖一起丢掉。

## dev 发布渠道（2026-10-04）

只从 origin/main 出，一个版本号对应 main 的一个提交，APK 是发布的一部分。各开发机不要再各自从工作区出发布。

**什么时候出**：有 PR 合并进 main、要让手机用上；手机上积了很多开发覆盖、要回到一个干净的基线；要发给别人装。出之前确认 main 已经包含要的改动（PR 已合并），开发覆盖里没合并的试验会被发布部署清掉，先问用户。

```sh
# 在一个干净的、HEAD 就是 origin/main 的工作树里（例如 git worktree add … origin/main）
python3 tools/rungic_release.py dev                     # 构建缺的包、组件和 APK，出 YYYYMMDD.N，写发布包
python3 tools/rungic_release.py deploy <版本> --all      # 所有连着的 Rungic 手机，后台运行，不加超时
python3 tools/rungic_release.py status --all
```

- `dev` 拒绝不是 origin/main 或不干净的工作树；补丁队列改了没加 changelog 条目也拒绝。构建机默认 Mac mini。
- 新工作树的 `.work/apt` 要链接到本机共用的 APT 仓库（`ln -s <主工作树>/.work/apt .work/apt`），否则会从空仓库重建全部包。耦合包的版本取自默认手机（`RUNGIC_SERIAL`）上装的版本，或 `--coupled-json`。
- APK 构建需要本机的 Android SDK、NDK 和原生库；不行时用 `--apk 文件`，或 `--no-apk`（发布不带 APK）。改了 APK 要提高 versionCode，否则装了同号 APK 的手机不会更新。
- 发布包 `rungic-<版本>.tar` 默认在 `.work/release-bundles/`（`--out`、`RUNGIC_RELEASE_OUT`）。别的机器部署：`deploy --all --from rungic-<版本>.tar`，不要在那台机器上另出同号的发布。
- 部署时手机缺的 deb 由手机直接从构建机（Mac mini）的发布池取，部署这台机器只推索引；构建机或手机的密钥不可用时才退回经本机推（docs/109“包从构建机直接到手机”）。不要再让手机经 K8 取包。
- `deploy --all` 逐台部署，某台失败继续下一台，最后有汇总表；每台的记录在 `.work/deploy/<时间>-<版本>-<序列号>/`，每次部署追加到 `release/history.json`，随下一个提交带上。上一次部署的快照没 commit 的手机会中止（记为 aborted），照“正式发布”第 4 步问用户。
- APK：部署在容器装好之后，手机上的 versionCode 更低才 `adb install -r` 并重新打开；`--restart never` 不装。APK 不会随回滚降级。
- **GitHub 预发布要用户确认**：先 `python3 tools/rungic_release.py publish <版本>`（或 `dev --publish`），它只给出 `gh release create dev-<版本> … --prerelease` 命令和说明文件，不访问 GitHub。把命令和说明给用户看，用户明确同意后才执行 `publish <版本> --yes`。不要自己建或推 `dev-*` tag。
- 部署后核对 `status --all` 的 apt 一列：`ok` 表示每个发布包都按版本 pin 住、元包 Protected、unattended-upgrades 名单在。

## 正式发布

1. **提交到 main**：按逻辑分组提交，提交信息末尾带 attribution。`rungic_package.py` 和 `rungic_release.py build` 都要求干净的提交。
2. **构建包**：`python3 tools/rungic_package.py list` 找出 stale 的包，再 `build <包>... --host macmini`。版本是 `0.<提交数>`。
3. **生成元包**：`python3 tools/rungic_release.py build --note "…"`，得到 `YYYYMMDD.N`。它会查询手机上 coupled 包的版本，开发覆盖不影响这一步。
4. **处理快照**：上一次部署留下的快照还在时，部署会中止。要问用户：commit 上一版（接受它）再部署，还是用 `--snapshot never` 部署。不要替用户决定。
5. **部署**：`python3 tools/rungic_release.py deploy <版本>`。
   - 要在后台运行，不要加客户端超时：经 Wi-Fi 会超过 10 分钟，2026-09-30 的一次部署就是被 590 秒超时杀掉的（docs/96）。
   - 顺序是：快照 → 安装 → 写 pin → 清掉开发覆盖（记录里的 `dev-overlay` 步骤）→ Android 侧文件 → 重启 → 完整性检查 → 冒烟验收。
   - 验收失败会自动回滚到快照。
6. **部署后**：看记录 `.work/deploy/<时间>-<版本>/deploy.json` 的 `result`，以及 `rungic_dev.py status`：覆盖应当为空，基线就是新发布。新快照要等用户接受后，再执行 `rungic_release.py commit`。
7. **写进 docs**：在对应篇目里写版本、提交、包数、各步结果和验收边界。研究结论、离线校验和实机结果要分开标注。

## 界面和设计系统改动的核对

- **本机离线**：`python3 tools/design_gallery.py local <目录> [--section A,B] [--rev <提交>]`。
  - 用 PySide6 渲染 `desktop/design/qml` 的状态总览（需要先执行 `sh tools/dev-setup.sh`）。
  - `--rev` 渲染某个提交的版本，用来做改动前后对照。
- **手机**：`python3 tools/design_gallery.py phone <目录>`。以桌面用户身份、用 offscreen 平台运行已安装的 `rungic-design-gallery`，不在用户屏幕上开窗口；每节每种主题各一张，另拼一张 `sheet.png`。
- **已知限制**：software 后端不画 `MultiEffect`，所以 Thumbnail 和 LivePicture 的示例图是空的。这不是回退；这类控件要在真实会话里另行确认。
- 截图和记录放 `.work/verify/<日期>-<主题>/`。

## 改这些工具时

- 离线测试不能碰手机。`tools/conftest.py` 会拦住 `rungic_device._run`，测试一旦走到 adb 就直接失败。
- 测试也不能碰 GitHub 和共享的 APT 仓库：`gh` 和 `adb_install` 换成替身，`POOL`、`RELEASES`、`APKS`、`RELEASE_HISTORY` 指向临时目录（见 `tools/test_rungic_release_channel.py`）。
- 部署流程里新加的设备操作，要走调用方模块自己的 `run`，比如 `rungic_release.run`，或者把 runner 作为参数传进去。这样测试里的替身才能拦住它。2026-09-30 曾有一个测试因此删掉了真手机上的开发覆盖（docs/97）。

## 汇报

- 说明走的是哪条路径、覆盖或发布的版本、重启了什么；
- apt 和完整性检查的结果，其中已有的漂移单独列出；
- 截图的位置，以及哪些没有验证到。
