# Codex 用官方独立安装，在设置里提示更新（2026-10-01）

- **起因**：用户在设备上有了更新的 Codex（0.159.2），它支持 GPT-6.1-Sol，但语音助手的模型列表里没有。
- **用户的决定**：
  - 始终用最新版本的 Codex；
  - 在设置里提示更新，由用户点“更新”；只跟 stable；
  - 系统包不再自带 Codex，只在设置里提供一键安装，用官网的脚本。

标注：“实测”指在 G100 S 上实际调用；“源码”指核对过 openai/codex `rust-v0.159.2` 的源码或官方安装脚本。

## 为什么旧版本看不到新模型（实测）

- **设备上有两套 Codex**：
  - 系统包 `rungic-codex` 里的 0.156.1，也就是 `/usr/bin/codex` 调用的 `/usr/lib/codex/0.156.1`，语音服务的 app-server 用的就是它；
  - 官方独立安装的 0.159.2：`~/.codex/packages/standalone/current`，外加 `~/.local/bin/codex`，于 2026-09-30 22:42 安装。
- **同一账户，不同版本拿到的模型目录不同**：
  - 0.156.1 的 `model/list` 返回 7 个可见模型，缓存 `~/.codex/models_cache.json` 里记着 `client_version: 0.156.1`；
  - 0.159.2 返回 8 个，多出 **GPT-6.1-Sol**，而且它就是账户默认。
  - 缓存里的模型都没写最低客户端版本，所以筛选发生在 OpenAI 服务端：按请求带的客户端版本决定下发哪些模型。
- **结论**：新模型只会下发给新版本的 Codex。docs/98 的模型选择逻辑本身没问题，缺的是 Codex 版本。

## 上游的安装和更新（源码）

- **官方安装脚本** `https://chatgpt.com/codex/install.sh`：
  - 默认 `RELEASE=latest`，先查 `releases.openai.com/codex/channels/latest`，不可用时退回 GitHub 的 `releases/latest`；
  - 下载后按 `codex-package_SHA256SUMS` 校验，有安装锁；
  - 装到 `~/.codex/packages/standalone/releases/<版本>`，切换 `current` 链接，建 `~/.local/bin/codex`，并在 shell 配置文件里加 PATH；
  - `CODEX_NON_INTERACTIVE=1` 时不提问。
- **`codex update`**（`codex-rs/tui/src/update_action.rs`）：独立安装在 Unix 上执行的就是 `curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh`。所以“安装”和“更新”是同一条命令。
- **更新检查**（`codex-rs/tui/src/updates.rs`）：读 `https://api.github.com/repos/openai/codex/releases/latest`，这个接口不含预发布版和草稿；结果写入 `~/.codex/version.json`。

## 实现

- **`rungic-codex` 包只剩 `/usr/bin/codex`**（架构改为 all）：
  - 它加载 `/etc/profile.d/proxy.sh`，然后运行 `${CODEX_HOME:-~/.codex}/packages/standalone/current/bin/codex`；没装时提示去设置里安装，退出码 127；
  - 包里不再带 Codex 二进制，`agent/codex/codex.json` 的版本锁定已删除；
  - 升级到这个包时，dpkg 会移除旧的 `/usr/lib/codex/0.156.1`。
- **`agent/assistant/codex_install.py`**：
  - 定位独立安装；
  - `command()`：启动脚本存在时用它，否则直接用独立版的二进制；
  - `install_command()`：加载代理，然后运行官方脚本（`CODEX_NON_INTERACTIVE=1`），与 `codex update` 相同；
  - `latest_release()`：读 GitHub 的 `releases/latest`，拒绝预发布版和带后缀的标签；
  - `UpdateCheck`：结果缓存 6 小时，可强制刷新，离线时保留上次的结果。
- **语音服务**：
  - app-server 通过 `codex_install.command()` 启动；
  - 启动 1 分钟后检查一次新版本，之后每 6 小时一次；有无新版本发生变化时，发出 `codex-update` 事件；`Setup` 返回的 `codex.update` 带着检查结果；
  - D-Bus 新增 `CheckCodexUpdate({"force"})`；
  - `InstallCodex` 只剩官方脚本这一种方式。安装或更新完成后，先等没有任务在跑、也没人在说话（最多 30 分钟），再重启 app-server；模型目录随之失效，重新读取。
- **App**：
  - Codex 页的版本行：有新版本时显示“X 已发布”，已是最新时显示“已是最新的正式版”；
  - footer 里有“更新到 X”，更新时标题和进度文字都换成“更新”；
  - “检查更新”会强制检查一次；
  - 未安装时只剩官方脚本这一种方式，并说明装在 `~/.codex`；
  - 设置首页的 Codex 行，有新版本时显示“可更新到 X”。

## 和发布规则的关系

docs/61 要求一个发布锁定所有包的版本。Codex 不在其中：它和用户数据一样放在用户目录里，由用户在设置里更新，不随发布锁定，也不随快照回滚。发布只锁定 `/usr/bin/codex` 这个启动脚本。验收记录里应当写上当时 Codex 的实际版本。

## 离线核对

- `tools/tests/test_codex_install.py`：版本解析（拒绝预发布版）、只接受 stable、缓存与强制检查、离线时保留、未安装时无更新、定位独立安装、安装命令与 `codex update` 一致、启动脚本不再指向 `/usr/lib/codex`。
- 本机离线渲染了 Codex 页的三种状态：有新版本、已是最新、未安装。

## 实机（G100 S，2026-10-01）

- **部署**：用开发覆盖装上 `rungic-codex 0.510+dev20260930t153304…`（只剩启动脚本）和 `rungic-voice-agent 0.514+dev20260930t153304…`，基线是发布 20260930.10。apt 核对通过，重启了 rungic-voice-agent。
- **结果**：
  - `/usr/lib/codex` 已被 dpkg 移除，`codex --version` 是 0.159.2；
  - 语音服务的 app-server 进程是 `~/.codex/packages/standalone/releases/0.159.2-…/bin/codex`；
  - `Models` 列出 8 个模型，首位是 gpt-6.1-sol；“跟随账户默认”解析为 gpt-6.1-sol/medium；
  - `CheckCodexUpdate` 强制检查 GitHub：installed 0.159.2，latest 0.159.2，没有可用更新；
  - `Setup` 的 `codex` 字段报告为已安装、能运行、正在运行。
- **还没验证的**：
  - 在手机上点“一键安装”和“更新到 X”：当前已是最新版，没有可更新的版本；安装路径是官方脚本本身，这次没有重跑；
  - Codex 页和设置行在手机上的显示：只做了离线渲染。

## 默认未安装与状态说明（2026-10-07，task #98）

Codex 保持由用户安装。新桌面上的“未安装”是正常默认状态，打开页面不会自动下载。首页用量卡显示“尚未安装 Codex”，点击安装入口经现有的 `--codex` 单实例导航打开 Agent → 设置 → Codex；此页仍使用原有官方安装流程。首次介绍只有在 Codex 可用时才说它正在后台查看手机；未安装、未登录或状态未知时说明所需步骤，并保留本地记录入口。

安装与连接分开检查：`codex_install.installed()` 检查 `${CODEX_HOME:-~/.codex}/packages/standalone/current/bin/codex` 的文件状态。缺失为 `false`，常规文件为 `true`，无法读取为 `null`。没有执行权限的文件仍为已安装，但 `Setup.codex.runs` 为 `false`，页面说明无法运行。`Setup` 和 `Usage` 都保留这项独立结果；仅凭 app-server 连接失败不能推断未安装。`account/read` 明确返回空账户才是未登录，读取失败仍是无法确认。Codex 安装页在安装未知时只提供重新检查，不提供自动安装；账户读取未知时登录页不提供登录选择。

离线回归覆盖缺失文件、无执行权限、读取失败、服务无 Codex 时继续运行、首页安装入口、条件介绍和原有登录入口；原生 ARM64 构建核对 Qt/QML、翻译目录与用量模型。手机上的中英文首次桌面与实际安装、登录、连接失败恢复仍需第三轮 D02 验收，本节不替代实机结论。
