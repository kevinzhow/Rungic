# 121：发版验收清单（Agent 在真机上操作一遍）

用户于 2026-10-07 明确：界面交互的验收就是构建完成后的一次简单验收。重新安装之后，由 Agent 像用户一样看着手机屏幕、通过 ADB 触摸操作，把下面列好的项目走一遍。丰富的自动化测试留给离线单元测试和无头系统测试；这里不再写专用的读回框架。它取代了 task #103 的冻结规范（v1.1/v1.2，约 5 万字）和 core 验收计划，那些没有合入。

- **什么时候跑**：每个发布候选重新安装之后跑一遍；开发覆盖（docs/97）装好后也可以先跑来找问题，但只有干净安装的候选那次算发版验收。日常改动继续靠 `tools/run-tests.sh` 和 `rungic_acceptance.py smoke`。
- **谁来跑**：Agent，按 skill `.agents/skills/rungic-phone-acceptance/SKILL.md`。人看报告复核。
- **哪台手机**：USB 连在 mibook 上的 G100（`ZY32M9MRVP`）。同一个 adb 上的 G100 S 是用户日常机，不碰。

## 怎么判

每项三种结果：**通过**、**失败**、**没跑**（写明原因，例如没有获准账号）。

- 看屏幕判断为主。每项写了“看到什么算过”，看不到就是失败，不是“差不多”。
- 屏幕上看不出来的事实（文件内容是否一字不差、声音进了哪个输出、两边是不是同一个文件），用每项给的一行命令核对。命令只用来核对结果，不能代替用户操作：打开应用、输入、保存都在屏幕上做。
- 失败了照实记下，附截图和现象，不为了变绿重试到通过；重试另记一次。
- 每项做完把自己打开的东西关掉，不动用户原有的窗口和文件。

## 准备

```sh
export SSH_AUTH_SOCK=…                      # 能登录 mibook 的 ssh-agent
ssh -fN -L 127.0.0.1:15037:127.0.0.1:5037 mibook     # 本机 15037 → mibook 的 adb
# .work/device.env：RUNGIC_ADB_PORT=15037 RUNGIC_SERIAL=ZY32M9MRVP RUNGIC_TRANSPORT=ZY32M9MRVP
python3 tools/rungic_agent.py screenshot .work/acceptance/<run>/00-start.png
```

在 mibook 上直接跑时用它自己的 adb（端口 5037），不需要隧道。操作用 `tools/rungic_agent.py`：

| 命令 | 作用 |
| --- | --- |
| `screenshot PATH` | 手机最终画面（SurfaceFlinger），1080×2400 像素 |
| `tap X Y` / `swipe X1 Y1 X2 Y2 [--ms N]` | 按截图像素触摸；起止点相同的 swipe 是长按 |
| `key BACK` / `key HOME` / `key ENTER` | Android 按键 |
| `text ASCII` | 往当前焦点输入 ASCII（不经过屏幕键盘，只用于网址等，E2E-02/08 不能用） |
| `exec 'CMD' [--as user\|container\|shell\|root]` | 跑一条核对命令，默认桌面用户 |
| `keyboard-type 'Abc 12\n'` / `keyboard-pinyin nihao 你好` / `keyboard-press language` | 点屏幕键盘上真实的键（按无障碍标识找键，大小写和符号页自动切）；拼音点完字母后按候选文字选词。E2E-02/08 用它 |

本轮唯一编号 `RUN`（例如 `20261007a`）用在文件名和网页里，避免读到上一轮的旧结果。

## 清单

### A0 重新安装并进入桌面

1. `tools/ci/standalone.py uninstall … --yes-delete --purge`（会删 Linux 家目录，先确认）；`standalone.py install <载荷> --manifest-sha256 …`（docs/118、docs/91）。
2. 打开 Rungic，看首装进度，按提示创建账户。

**算过**：首装显示真实阶段而不是空白；拒绝 root 这类用户名时表单下方直接写出原因；创建后进入 Plasma 桌面，底栏和抽屉可用；锁屏能看到需要操作的提醒。
**核对**：`exec 'cat /usr/share/rungic/release.json | head -5'` 是这次的候选版本。

### E2E-01 抽屉里打开、关闭四个应用

从抽屉依次打开浏览器（Firefox）、终端、文件管理器（Dolphin）、编辑器（KWrite），每个做一次不改数据的操作（打开菜单再关上），再从任务切换器的卡片关闭。

**算过**：点哪个开哪个；窗口出现并能操作；关闭后卡片消失，没有崩溃提示。
**核对**：关闭后 `exec 'systemctl --user list-units "app-*" --no-legend'` 里没有这次启动的单元残留。

### E2E-02 屏幕键盘输入中英文

打开 KWrite 新文档，只用屏幕上的键盘逐键点（`keyboard-type`、`keyboard-pinyin`；切中英文用 `keyboard-press language` 后点弹出的语言）：`Rungic E2E AbC123`、换行、拼音选词输入 `你好，中国。`、换行。句号“。”在符号页第 2 页（中文页右下角是全角点“．”，见 docs/41）。

**算过**：文档里显示的内容与上面一字不差，大小写、数字、标点、换行都对，没有残留的拼音或候选。
**核对**：和 E2E-03 一起用保存的文件核对字节。

### E2E-03 保存后重新打开

在 KWrite 里“另存为” `~/Shared/rungic-e2e-RUN/editor.txt`（在文件对话框里新建目录），关掉 KWrite，再从文件管理器打开这个文件。

**算过**：重新打开后内容不变，标题是这个文件。
**核对**：`exec 'sha256sum ~/Shared/rungic-e2e-RUN/editor.txt; wc -c < ~/Shared/rungic-e2e-RUN/editor.txt'`：37 字节，SHA256 `0275085f7d23e07cd22321f305372cd78db4cd052445d3a7aec4a137bb7b15af`（`printf 'Rungic E2E AbC123\n你好，中国。\n' | sha256sum` 现算）。

### E2E-04 Shared 两边互通

- Linux → Android：E2E-03 的文件，Android 一侧读：`exec 'sha256sum /storage/emulated/0/Plasma/rungic-e2e-RUN/editor.txt' --as shell`，与 Linux 一侧相同。
- Android → Linux：`exec 'echo RUN-from-android > /storage/emulated/0/Plasma/rungic-e2e-RUN/android.txt' --as shell`，在文件管理器里看到它并打开，内容是 `RUN-from-android`。
- Android 媒体库看得到：`exec "content query --uri content://media/external/file --projection _display_name --where \"_display_name='android.txt'\"" --as shell` 有一行。

**算过**：两个方向内容一致；文件管理器里看得到 Android 写的文件。
**还原**：删掉 `rungic-e2e-RUN` 目录。

### E2E-05 有声视频和相机

1. 准备测试视频放进 Shared：12 秒彩色测试图（画面一直在动），声音是 880 Hz 响 1 秒、静 1 秒交替。
   `ffmpeg -f lavfi -i "testsrc2=duration=12:size=640x360:rate=30" -f lavfi -i "sine=frequency=880:duration=12,volume='if(lt(mod(t,2),1),1,0)':eval=frame" -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest rungic-e2e-video.mp4`
2. 从文件管理器用视频播放器打开，播放，音量开到一半以上。
3. 播放的同时在 mibook 上听：`python3 .agents/skills/rungic-phone-acceptance/listen.py --seconds 8`（mibook 自带麦克风，Kevin 已批准；声音只在内存里算，只打印每 0.5 秒 880 Hz 的占比和结论，不存录音）。
4. 打开相机应用，隔两秒截两次取景画面。

**算过**：视频画面在动（隔两秒截两次图不同）；`listen.py` 输出 `heard: true`（响、静交替被听到）；相机取景是实时画面（两次截图不完全相同，不是黑屏或定格）；关掉播放器和相机后不再占用。
**核对**：播放时 `exec 'pactl list sink-inputs short; pactl list sinks short'`，这个流进的是 `android`，不是 `rungic_ws*` 这类工作区输出；关掉后这个流消失。

### E2E-06 浏览器上网

1. 在 mibook 上起一个只有一页的服务，页面正文含 `RUN`（`python3 -m http.server`），用 mibook 的局域网地址，不用 `adb reverse`。
2. Firefox 打开 `http://<mibook>:<端口>/RUN.html`，再打开 `https://www.kernel.org/`。

**算过**：页面正文显示 `RUN`，服务端日志有这次请求；kernel.org 正常显示“The Linux Kernel Archives”，没有证书错误。

### E2E-07 Codex 安装与状态

在产品的设置入口查看 Codex 状态，点安装，等完成。

**算过**：安装前写“未安装”，并给出安装入口；装好后写“未登录”，并给出登录入口；不显示“正在工作”之类的话。有获准账号时再登录，发一句“原样回复 RUN”，收到 `RUN`。没有账号时“可用”这一步记没跑。
**核对**：`exec 'ls -l ~/.codex/packages/standalone/current/bin/codex'` 安装前不存在，安装后存在。

### E2E-08 PC 模式

从控制中心进入 PC 模式，在 PC 桌面里打开 KWrite，用 PC 视图里的屏幕键盘输入 `PC08 RUN`，再退出 PC 模式。

**算过**：手机上显示独立的 PC 桌面；输入的文字出现在 PC 桌面的 KWrite 里；退出后回到原来的手机界面，原来的窗口还在。
**核对**：进入后 `exec 'systemctl --user list-units "*workspace*" --no-legend'` 有 0 号桌面在跑，退出后没有了。

### E2E-09 助理屏

不经过 Codex，直接调用 rungic-cua 的 `desktop_launch` 在助理工作区打开 KWrite，用 `desktop_act` 输入 `Rungic09 RUN`，`desktop_screenshot` 截图。

**算过**：KWrite 只出现在助理屏里，手机前台的应用和指针没被抢走（操作前、中、后各截一次图对比）；手机上的助理屏画面里能看到 `Rungic09 RUN`。
**还原**：关掉这次的 KWrite 和助理工作区。

## 报告

写到 `.work/acceptance/<RUN>/report.md`，截图放同一目录：

```markdown
# 验收 RUN（候选 <版本>，G100 ZY32M9MRVP，<日期>）
| 项 | 结果 | 现象 / 原因 | 截图 |
| --- | --- | --- | --- |
| A0 | 通过 | … | a0-desktop.png |
```

最后一段写：失败了哪些、没跑哪些和原因、和上一轮比有什么变化。

## 2026-10-07 第一次实跑（G100，第 2 轮底座加开发覆盖，非发版验收）

编号 20261007a，报告和截图在 K8 `.work/acceptance/20261007a/`（report.md）。用来检验清单本身和找问题，不算发版验收：A0（重装）没跑，装的是第 2 轮底座加开发覆盖。

| 项 | 结果 | 要点 |
| --- | --- | --- |
| E2E-01 | 通过 | 四个应用从抽屉打开、各做一次界面操作、从任务切换器关闭，进程都退干净 |
| E2E-02 | 前两次失败，修复后通过 | 新账户没有 Rime 也没有英文；KWrite 里拼音逐键上屏（docs/41 2026-10-07，已修） |
| E2E-03 | 通过 | 磁盘 37 字节、SHA256 与预期一致，从文件管理器重开内容不变 |
| E2E-04 | 部分失败 | 两个方向内容一致；Linux 一侧写的文件不进 Android 媒体库，Linux 一侧删掉后媒体库留下旧行 |
| E2E-05 | 通过（听音没跑） | 画面逐帧推进，声音流进 android；相机实时取景、关掉后释放；mibook 内置麦克风的 ALSA 采集开关关着，采到的是静音 |
| E2E-06 | 通过 | 局域网随机码页面和 kernel.org HTTPS |
| E2E-07 | 通过（范围：未安装至未登录） | 产品入口一键安装 codex-cli 0.160.1；没有获准账号，“可用”没跑 |
| E2E-08 | 退出时失败 | 独立 0 号桌面、浮动键盘输入都正常；关桌面模式时 KWrite 未保存的内容被直接关掉，没有提示 |
| E2E-09 | 通过（画面里的字太小，逐字核对靠工具截图） | 产品入口（rungic-workspace-env 1 rungic-cua mcp）在助理工作区开 KWrite、截图、输入；手机前台不受影响；有未保存内容时关闭工作区先拒绝，丢弃后关掉 |

其他发现：开发覆盖装完不重启 rungic-voice-agent；Firefox 首次打开弹“设为默认浏览器”吞掉输入；adb 文字输入偶发冒号后的字符变成 Shift 字符；切到别的应用的输入框时键盘不自动弹出、窗口缩小后露出壁纸；全屏工具栏图标显示成白块、按钮没有无障碍名称；电池磁贴显示 0%（Android 是 80%）；xdg-document-portal.service 启动失败；不在工作区里运行的 rungic-cua 还在等 CAST 输出（桌面模式改成独立 KWin 之后不会再有）。

判定靠截图加一条命令有效：每项都留了前后截图，屏幕看不出的（文件字节、媒体库、服务端日志、声音流、相机占用、工作区单元）用命令核对。

## 2026-10-08 KernelSU 实跑（G100，PR #57，非发版验收）

编号 20261008a（装机后第一遍）和 20261008b（重启后第二遍），记录和截图在 K8 `.work/acceptance/20261008a/`（notes.md）。用来验证 KernelSU 下的 root 路径，不算发版验收：G100 的 init_boot 换成 KernelSU v3.3.0 LKM（android15-6.6），安装包是第 2 轮 rootfs 加 PR #57 的 APK、host-seed 和 firstboot，之后用开发覆盖装上 main 的 9 个包。

| 项 | 第一遍 | 重启后 | 要点 |
| --- | --- | --- | --- |
| A0 | 通过 | — | 首装完成后在 KernelSU 管理器给 Rungic 授权（打开“显示系统应用”才看得到它），账户表单建好 tester，进入桌面 |
| 重启 | — | 通过 | boot_completed 33 秒；kernelsu 模块已加载；不打开 App，56 秒内容器和 Plasma 都起来了（KernelSU 运行 /data/adb/service.d）；打开 App 直接进桌面 |
| E2E-01 | 通过 | 通过 | |
| E2E-02 | 通过 | 失败后修复通过 | 重启后语言键变灰、只剩中文：浮动键盘写坏了 plasmakeyboardrc（见下） |
| E2E-03 | 通过 | 通过 | 37 字节，SHA256 一致 |
| E2E-04 | 部分失败 | 部分失败 | 同 20261007a；原因已查清（见下） |
| E2E-05 | 通过（听音没跑，相机画面太暗） | 同左 | 声音流进 android，AudioFlinger 有活动音轨；凌晨房间暗，相机只能确认在出帧、关掉后释放 |
| E2E-06 | 通过 | 失败后修复通过 | 重启后 Linux 断网：own-network 目录权限（见下） |
| E2E-07 | 通过（范围：未安装至未登录） | 通过 | codex-cli 0.161.0，重启后仍在 |
| E2E-08 | 退出时失败 | 退出时失败 | 同 20261007a |
| E2E-09 | 通过 | 通过 | |

KernelSU 特有的只有一处：没授权时 App 只显示笼统的“暂时无法进入”。已在 #57 补上提示（install.desktop-entry/E7）：KernelSU 不给未授权的应用提供 su，启动 su 报 `error=2`；App 认出这种失败，按首次启动记下的 root 方式（安装状态里的 `root=`）写出去管理器哪里授权。实机撤销授权后看到提示，重新授权后点“重新检查”进入桌面。

重启后发现的 main 问题，都和 root 方式无关：

- **重启后 Linux 断网。**“自己的网络”默认开启（#43）。开机由 rungic-runtime 拉起容器，它设了 `umask 077`，`/run/rungic-own-network` 被建成 0700，以 App uid 运行的 pasta 打不开 netns，每 30 秒重试一次。目录改成 755 后 pasta 立刻起来。
- **浮动键盘写坏手机键盘的配置。**`agent/screen/qml/FloatingKeyboard.qml` 用 QML Settings（QSettings 的 INI 格式）读 `plasmakeyboardrc`：QSettings 把 General 组写成 `[%General] enabledLocales=@Invalid()`，又把原来的列表改写成 `zh_CN, en_US`，KConfig 读到的是带空格的 " en_US"。用一次浮动键盘就会触发，两遍都复现。
- **媒体库（E2E-04）。**Linux 的 `~/Shared` 是 root 身份的 bindfs，挂在 Android 的 FUSE 上。MediaProvider 对 uid 0 的操作直接放行，但不更新数据库。对照：adb shell（uid 2000）写的文件进了媒体库，Linux 写的、改名的、`su 0` 写的都没进。

其他：重装 A0 时家目录归 root 所有，是重新打包安装包时用了 `tar --owner=0` 造成的，不是产品问题；全新账户的无障碍默认关闭，`keyboard-*` 工具要先 `rungic-a11y enable`；Linux 走自己的网络时 Plasma 状态栏没有 Wi-Fi 图标；Plasma 时钟用的是 UTC，Android 是 Asia/Shanghai。

修复后在同一台 G100 上复验（开发覆盖装上修复）：
- 浮动键盘：切换语言、打字、关掉窗口之后，`plasmakeyboardrc` 一个字节都没变。修复迁移 `rungic-keyboard-repair` 用真实的 kreadconfig6/kwriteconfig6 把写坏的文件改回 `zh_CN,en_US`，并去掉了 `[%General]`。
- 媒体库：bindfs 改成以 uid 2000 运行，带上账户组，因为家目录是 0750。在 `~/Shared` 里新建、删除，以及编辑器那种“写临时文件再改名”（QSaveFile、GIO 两种命名都试了）都会同步到媒体库，SQLite WAL 正常。目录的行在 rmdir 后还留着，adb shell 自己删目录也一样，是 Android 的行为。
- 网络：重启后 `/run/rungic-own-network` 是 0755，pasta 一直在跑，默认路由是 10.0.2.2。但在**打开 Rungic 之前连不上网**：Android 的后台网络限制拦住了 Rungic 的 uid（`blocked=APP_BACKGROUND`，进程状态 CACHED）。打开 App 后，或者 DesktopService 前台服务在跑时，局域网和 kernel.org 都通。所以开机后、强行停止 App 后，Linux 在“自己的网络”模式下都是断网的，要另外处理（见下）。另外，上一次开机失败时写下了 network-mode.failed，这次开机先按设计回到共享网络跑一次，再下一次开机才用回自己的网络。
