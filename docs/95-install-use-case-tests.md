# 装机用例测试：写法与首批三个修复

2026-09-30，审查 d006ba6（独立安装与 X70 冷启动修复）发现 3 个会实际出错的问题。用户要求先修这 3 个，并讨论这类问题怎么写用例测试，再在当时能连接的真机上验证。本篇记录用例测试的分层写法、这 3 个用例、真机证据，以及验证中新发现的 2 个既有问题。证据目录 `.work/verify/20260930-review-fixes/`。

## 为什么已有单元测试没抓到

- **跨模块约定没有测试**：`setup.py` 的报错文字和 `AccountSetup` 的文字匹配各自都有测试，但中间 `MainActivity.control()` 包装报错信息这一步没人测。
- **测试环境没覆盖真实变体**：音频 PID 测试只造了“普通用户进程复用 PID”，没有“内核线程复用 PID”。
- **状态组合没有穷举**：首装门控只测了若干状态，没有覆盖“来源文件有/无 × product seed 有/无/旧 × 安装状态”。

## 用例测试的写法

每个用例写成“前置状态 → 用户动作 → 用户能看到的结果 + 系统必须保持的条件”，同一个用例 ID 贯穿三层：

| 层 | 位置 | 成本 | 职责 |
|---|---|---|---|
| L1 离线跨模块 | `tools/run-tests.sh` | 秒级 | 用真实脚本、真实报错文字，在临时目录里替换绝对路径后运行，覆盖跨模块约定与状态组合 |
| L2 真机故障注入 | `rungic_acceptance.py`（待增加 `install` 级别） | 分钟级，不重刷 | 在真机上构造前置状态，验证后自动还原现场 |
| L3 整机生命周期 | 专用测试机 | 小时级 | 全新首装、连续冷启动、升级/续装、失败恢复，结论写入对应 docs |

`tools/run-tests.sh` 是离线测试的统一入口：pytest 覆盖 `tools/ci`、`tools/`、`tools/tests` 和 `system/account`；另外编译运行 APK 的纯 Java 测试，并检查已跟踪 sh 脚本的语法。没有 PySide6 时会跳过 QML 卡片测试并提示 `sh tools/dev-setup.sh`。`system/account/test_setup.py` 原先在模块顶层调用 `unittest.main()`，导致 pytest 收集中断，已改为 `__main__` 保护。

离线夹具在子进程启动前拒绝设备入口、构建机 SSH 入口和绝对路径的真实 SSH／ADB／SCP／SFTP 命令。通过 PATH 的远程命令会执行拦截脚本并记下违规；即使子脚本忽略返回码，测试结束时也会报错。入口拒绝信号不能被传输失败回退的 `except Exception` 吞掉。传输协议测试仍可运行临时目录内的脚本替身；开发包同步测试显式替换构建机暂存步骤，不访问真实构建机。

## 三个用例

### UC-account-taken：首装时用户名已被占用

- **问题**：d006ba6 在 `control()` 抛出的异常信息前加了 `Control <action> failed (exit N): `，`AccountSetup` 用 `equals` 做完全匹配，于是永远匹配不上。用户输入已被占用的用户名时，表单被关闭，显示“账户未完成”。
- **修复**：新增纯 Java 类 `ControlException`：`getMessage()` 仍是给日志用的诊断文字，`output` 保存控制器的原始输出。`usernameTaken()` 取输出的最后一个非空行做匹配（`setup.py` 最后打印报错，前面可能有 lxc-attach 的噪声）。`home` 操作的 Toast 改用 `userText()`，不再显示英文包装文字。
- **L1**：`android/app/tests/ControlExceptionTest.java` 从 `setup.py` 源码读出全部 14 条 `SetupError` 文字，模拟“前面有 lxc 噪声、结尾有空行”的合并输出，断言只有两条用户名冲突文字会让表单保留。按 d006ba6 的逻辑模拟，匹配结果为 `false`，确认测试能抓到这个 bug。
- **真机（G100 S，不含 UI）**：
  - 该机已设置账户，走 APK 同一 `su --mount-master` 路径调用 `account-setup`，真实输出为单行“初始账户已设置，请在系统账户设置中修改密码”。
  - 另通过同一 `lxc-attach` 通道加载容器内真实的 `setup.py`（哈希与仓库一致），只把两个账户标记路径改为不存在的位置，让它走到 `validate()`。这一步在任何修改之前就会报错，没有副作用。用户名 `root`、`daemon` 的真实输出都是“这个用户名已被使用”。
  - 把这三份真实输出交给修复前后的判断逻辑：d006ba6 都会关掉表单；修复后两条用户名冲突留在表单，“已设置”仍按失败处理（`t2-classification.log`）。
- **未验收**：真实账户表单里的界面交互需要一台未设置账户的首装设备（L3）。

### UC-audio-kthread-pid：重启后保存的 PID 被内核线程复用

- **问题**：`clear_stale_pid` 在 `readlink /proc/PID/exe` 失败且进程不是僵尸时，一律按失败处理。内核线程即使对 root 也没有 exe 链接，所以只要旧 PID 被内核线程复用，每次启动都会以 `phase=android-audio` 失败。
- **修复**：进程状态为 `Z` 或带有 `Kthread: 1` 时，才判定为“不可能是音频守护进程”；其他无法检查的进程仍按失败处理。主机内核 7.0 和 G100 S 内核 6.6 都有 `Kthread:` 字段；缺少该字段的旧内核仍会按失败处理。
- **L1**：`test_android_audio.py` 新增用例，用任何 Linux 上都存在的 PID 2（kthreadd）。修复前失败，修复后通过。
- **真机（G100 S）**：
  - 先部署 d006ba6 版脚本：停止音频，把 pid 文件写成 `2`，再执行 `start`，返回 1，pid 文件仍为 2，pactl 连接被拒（`t1a-reproduce.log`）。
  - 换上修复版：`start` 返回 0，pid 文件更新为新的 pulseaudio 进程，`android_output` 为 RUNNING，watch 在运行（`t1b-fixed.log`）。之后 smoke 的播放、录音两项均通过。

### UC-app-data-cleared：安装完成后清除 APK 数据再打开

- **问题**：来源文件 `files/rungic-install-source.properties` 只有 `standalone.py install` 会写，首启脚本不会重新生成。清除数据后：
  - 复用底座上还有旧 product seed 时，APK 用旧 release 去比对，一直停在“等待”；
  - 纯底座时，APK 直接判定 ready，跳过了 APK 侧的门控（root 控制器仍有自己的门控）。
- **修复**：
  - `rungic-firstboot.sh` 每次运行都重新写出来源文件；以 legacy product 路径运行时，则删除不属于自己的来源文件。
  - 控制器新增 `install-publish`：只有完成标记与当前 release 一致时，才执行对应的首启脚本，此时脚本只发布状态后退出；安装未完成或没有托管安装时什么都不做。
  - APK 在来源文件和状态文件都不存在时（数据被清除的特征），每个进程最多请求一次 `install-publish`，然后再读取状态。这样清除数据后不用重启手机。
- **L1**：
  - `tools/ci/test_install_republish.py` 在临时目录里运行真实的控制器分支和首启脚本，覆盖 5 种情况：复用底座上的独立安装、legacy product、安装进行中、没有托管安装、开机路径重新发布。拿修复前的源码运行，这些用例都会失败。
  - `FirstBootStateTest` 增加“旧 product seed + 独立安装状态”的组合，确认缺少来源文件时停在 `RELEASE_MISMATCH`，有来源文件时放行。
- **真机（G100 S）**：
  - 场景 1（该机真实状态，没有托管安装）：`pm clear` 后 `install-publish` 返回 “No managed installation”，APK 直接放行。
  - 场景 2：在 `/data/adb` 下临时模拟一个已完成的独立安装（`active.env`、完成标记、payload 中的仓库版首启脚本）。先停容器，再 `pm clear`，然后打开 APK：root 重新发布了来源文件和 `ready/complete` 状态，属主为 APK uid，SELinux 标签与 APK 数据目录一致；首启日志只有 “already installed”；桌面 10 秒内启动（`t5-*.log`）。测完删除了全部模拟文件。
- **未验收**：“复用底座上还有旧 product seed”这一支需要 X70 等带 product seed 的设备；开机路径的重新发布只有 L1 覆盖。

## 验证中发现的既有问题（已由 [96 篇](96-desktop-recovery-after-apk-restart.md) 修复）

1. **清除 APK 数据后，正在运行的容器仍绑定已删除的目录**：容器内 `/mnt/android-wayland` 指向 `/data/com.rungic.plasma/files/tmp//deleted`。APK 重建的 `files/tmp` 在容器里不可见，KWin/plasmashell 连不上，APK 显示“暂时无法进入”。在 APK 重新创建 `files/tmp` 之前，所有经过 `rungic-plasma-enter` 的控制命令也都会报 `bind Android Wayland socket directory`。停止容器后由 APK 重新启动即可恢复。
2. **APK 被强制停止后，plasmashell 可能停在 failed**：Wayland 服务端随 APK 消失，systemd 在合成器回来之前约 1 秒内重启 plasmashell，又断开一次，退出码 255，unit 进入 failed。APK 重开后 KWin 恢复，plasmashell 不会再被拉起；控制器等待 120 秒后报错，APK 显示“暂时无法进入”。Android 因内存紧张杀掉 APK 时也可能走到这条路径。执行 `rungic-plasma restart-session`，再在 APK 里点“重新检查”即可恢复。

两者已在共享层修复：控制器的 start 检查 bind 是否失效，会话等待上一个会话的 stop 作业结束，控制器在 plasmashell 已 failed 时改走 restart-session。机制、真机对比和边界见 96 篇。

## 本轮真机范围与还原

- **设备**：`10.77.0.16:35577`，ZY32MVJS25 / `mumba_cn`（G100 S）/ W1WAA36.48-23-10，K8-Plus 通过 Wi-Fi/VPN 连接。该机是早期手工/APT 部署，既没有 `active.env` 也没有 product seed。
- **部署**：APK 2.27（K8 重建，原生库与已装的 2.24 逐一核对 SHA 一致，JNI 接口未变）、`rungic-plasma`、`android-audio`。控制脚本和音频脚本部署前的版本备份在 `device-backup/`。部署也带上了 d006ba6 本身对这两个脚本的改动（安装门控、lxc-start umask）。
- **`pm clear` 的影响**：会撤销 5 个运行时权限，已按清除前状态重新授予（`POST_NOTIFICATIONS` 原本未授予，保持不变）。OCR 模型和 `shared_prefs` 已从手机上的备份恢复：prefs 内容与之前一致，SELinux 标签补上了 MLS 类别。`restorecon` 只给 `s0`，必须按目录标签 `chcon`。
- **备份**：手机上 `/data/local/tmp/rungic-app-backup.tar`（root 0600，不含运行时 socket）。toybox tar 不能打包 socket，会报 `unknown file type '140000'`。
- **smoke 结果**：场景 1 修复容器后 9/9 通过。最终一轮除 `camera.frames` 只收到 1 帧外全部通过；单独重跑 `camera.frames` 通过，判为偶发，尚未查明原因。
- **Magisk**：没有查询 Magisk 数据库。备份命令中原本附带的一条未加 `COALESCE` 的 SELECT 在执行前被停止（docs/39），su 授权是否保留由 APK 清除数据后正常调用 su 来验证。


## 2026-10-07：账户创建后立即进入桌面（离线修复）

第一轮生命周期验收 B05 发现：账户助手停止桌面、共享存储和用户服务后未恢复；控制器用已消失的 `rungic-session.env` 检查失败状态，把检查失败当成会话健康，随后等待就绪超时。

账户助手只负责停止会话和账户事务，成功或回滚后均由控制器作为唯一入口恢复会话。系统会话单元自行拉起共享存储和 PAM 用户管理器；已提交账户保留真实状态，禁止重复设置密码。控制器直接读系统会话状态：inactive/failed 等状态执行 start，activating 继续等待；active 且环境文件缺失时，以 systemd 的单调时间计算 60 秒期限，期限内等待，过期才 restart。环境已存在时先检查用户管理器和两个用户单元；查询失败或 failed 立即恢复。系统会话 active 未满 60 秒时，允许用户单元 inactive（可能有等待 KWin 的启动作业）；满 60 秒后两个用户单元均 active 才保持会话。

`system/account/test_setup.py` 连续运行真实的 `configure()` 与真实控制器 `start`，替代 Android、LXC 与系统服务。覆盖成功后直接进入、密码失败回滚后进入、服务启动失败不报就绪、已提交账户保留、原始错误保留、启动中等待、环境缺失超时恢复及健康会话。另连续执行 root 用户名被拒、成功创建、start、再 start，只产生一次会话启动。替身保留 activating 到 active 的过渡，不能以立即就绪掩盖竞态。修复前成功与环境缺失两条用例无法就绪；首次修改曾误重启 activating 会话，评审后补回归并修正。

本节仅记录离线证据。没有修改第一轮候选或操作手机；原生表单到可操作桌面的行为须在第二轮新候选上重新验收。

### Agent 可用性与人工体验补验（task #84）

首页只有权威 `account/read` 明确返回空账户时才显示未登录；服务缺失、读回超时或格式不完整显示暂时连不上，保留已有登录信息并自动重试。登录入口直接打开已有账户设置页；账户页不能把连接未知显示成已退出登录。未登录、断连、陈旧状态下，建议卡隐藏发起新 Agent 调查的入口，保留本地记录和已有任务，连接恢复后重新启用。

崩溃卡概述为“一个系统组件意外退出”，明确退出记录数、原因和实际影响仍需确认，进程与签名保留在详情。不能只凭进程名断言文件选择或桌面已损坏。连接状态集中在用量卡，避免每张建议重复提醒。

开发回归覆盖真实 QML 的登录点击、未知状态、操作隐藏和恢复，原生服务的记录保留与文案，以及单实例 `--sign-in` 到账户页。D-Bus 与 Codex 回读有替身，手机 GPU、中文显示与真人登录流程仍需第 2 轮候选验证。
