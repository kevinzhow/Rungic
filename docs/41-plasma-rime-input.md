# Plasma Mobile 的 Rime 中文输入

2026-09-23，Ubuntu 26.04 ARM64、Plasma Keyboard 6.6.6、Qt Virtual Keyboard 6.10.2、librime 1.16.1。

用户实际发现旧键盘输入一段后自动收起重开、候选消失、拼音排在中文前。本轮改用 **Plasma 官方屏幕键盘 + Rime 简体拼音**，保留英文切换；不改 Phosh 的输入法。

## 旧问题的证据

- 14:32:29 和 14:32:37，KWin 日志记录 `Input Method crashed "maliit-keyboard" ... 11 QProcess::CrashExit`。自动重启导致键盘收起又弹出，不是用户误触。尚未取得这两次崩溃的完整调用栈，因此不把所有崩溃归结为同一个源码缺陷。
- 核对 Maliit 2.3.1 及当前 master 的 `PinyinPlugin::finishedProcessing`：当已完成请求的 `word != m_nextWord` 时，又发出 `parsePredictionText(word)`，反复处理旧请求。本机也出现相同旧拼音持续生成候选的日志。旧结果竞争会影响快速输入；不能当成已完成适配。
- `PinyinAdapter::genCandidatesForCurrentSequence` 把原始/部分转换拼音先放到候选数组中。这是旧插件行为，与用户期望的中文优先不同。
- SVG 功能图标缺失是独立问题，已补齐 `libqt5svg5` 和 `qt5-image-formats-plugins`。更换后的 Qt6 键盘不再依赖这些 Qt5 图标补丁。

## 调研和选型

| 路线 | 核验结果与选择 |
|---|---|
| 修 Maliit + libpinyin | 可以修旧请求循环及候选排序，但仍需调试旧插件的选词/预编辑状态及崩溃。没有直接复用的 Rime 插件在本轮搜索中得到确认 |
| Fcitx5 + fcitx5-rime + fcitx5-osk | Fcitx5 在 KWin Wayland 下有正式入口，Rime 后端成熟；独立 OSK 使用 Rust/Iced，项目说明仍有修饰键转发问题，默认辅助程序涉及 evdev 权限。可作备选，未安装或启用 root 键盘辅助服务 |
| Plasma Keyboard + Qt 输入法扩展 + librime | 采用。复用已安装桌面键盘、布局、候选栏和语言选择器，用 Qt 公开的 `QVirtualKeyboardAbstractInputMethod` 接入发行版 Rime C API。不改 KWin，也不使用 Qt 私有 ABI |

来源：[Maliit 插件源码](https://github.com/maliit/keyboard/blob/2.3.1/plugins/pinyin/src/pinyinplugin.cpp)、[Maliit 候选生成](https://github.com/maliit/keyboard/blob/2.3.1/plugins/pinyin/src/pinyinadapter.cpp)、[Fcitx Wayland 配置](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland/en)、[fcitx5-osk](https://github.com/fortime/fcitx5-osk)、[Qt 输入法公开接口](https://doc.qt.io/qt-6/qvirtualkeyboardabstractinputmethod.html)、[Plasma Keyboard 6.6.6](https://invent.kde.org/plasma/plasma-keyboard/-/tree/v6.6.6)、[Rime 1.16.1 API](https://github.com/rime/librime/blob/1.16.1/src/rime_api.h)。实际源码副本在 `.work/refs/plasma-mobile-20260923/upstream/input/` 和 `upstream/maliit/`。

## 部署与数据

源码、CMake、安装脚本：[plasma/rime/](../desktop/rime)。适配代码使用 GPL-3.0-or-later；当前发行版 librime 包为 GPL-3.0-only，Qt 布局保留 GPL-3.0-only 许可，朙月拼音和 prelude 数据包为 LGPL-3，详细版权随系统包保留。

容器内依赖：

```sh
apt-get install --no-install-recommends qt6-virtualkeyboard-dev librime-dev \
    librime-bin rime-data-luna-pinyin rime-prelude rime-essay
sh /root/rime/install.sh
```

安装器只构建和安装，不自动改用户配置。此设备已设置：

```ini
# ~/.config/kwinrc
[Wayland]
InputMethod=/usr/local/share/applications/moto-plasma-rime.desktop

# ~/.config/plasmakeyboardrc
[General]
enabledLocales=zh_CN,en_US
```

包装入口仅为键盘进程设置 QML 搜索目录；其他桌面应用不受影响。布局在 `/usr/local/share/moto-rime/plasma/keyboard/layouts`，中文布局替换输入引擎，其余链接发行版布局。升级后重跑安装器；如果上游中文布局入口变化，安装器报错要求核对，避免静默用回旧引擎。`kwinrc.pre-rime` 是本轮切换前备份。会话重启后正式生效。

默认方案为 `luna_pinyin_simp`，用户词频和定制配置位于 `~/.local/share/plasma-rime/`，目录权限 0700，离线处理。`default.custom.yaml` 首次创建，此后不覆盖用户修改。隐藏密码字段不交给 Rime；敏感字段关闭词频学习。暂未预装雾凇等第三方词库，也未承诺任意方案免适配。

候选列表直接使用 Rime 顺序，不手动插入拼音候选；拼音显示在预编辑区。选词、空格、退格和标点经过同一个 Rime 会话处理，切换字段时按 Qt 的 reset/update 契约清理状态。当前候选读取上限 200 项。会话只在使用键盘时存在，见文末 2026-10-03 一节。

## 已验收与边界

- 独立测试数据目录进行 300 次快速组合、首候选和提交测试通过（你好、中国、测试）。
- 实际触屏连续输入“你好、中国、测试、我不知道、中文、输入法”，首候选为中文，提交正确。
- 四次键盘收起再打开通过，测试期间同一个键盘 PID 持续存活。
- 切换 American English 并输入 hello，再切回简体中文通过。
- **日常 Firefox profile** 地址栏实际触屏拼音预编辑、点击“你好”提交通过；该测试没有启用 Marionette。
- 当时键盘进程 8079 连续运行，未出现新的 `Input Method crashed` 日志。这是本轮实测结果，不代表已完成长时间稳定性验证。

证据：`rime-engine-test.log`、`rime-nihao.png`、`rime-continuous.png`、`rime-language-menu.png`、`rime-english.png`、`firefox-rime-preedit.png`、`firefox-rime-committed.png`。材料位于 `.work/refs/plasma-mobile-20260923/`。

整套桌面、后端与后续验收继续记录在 [40 篇](40-plasma-mobile-integration.md)。

## 键盘前端的语言菜单与旋转修复

另发现 Plasma Keyboard 6.6.6 的 `LanguagePopup.show()` 把坐标设置为捕获旧按键对象的 `Qt.binding`。切换语言销毁旧按键后，旋转会不断访问已失效对象。核对当前主线仍有相同写法；本轮 `plasma/keyboard-popup.patch` 改为打开菜单时一次性计算坐标，在键盘尺寸变化时关闭该菜单。

使用 `plasma/build-desktop-fixes.sh` 构建独立 `/usr/local/libexec/moto-plasma-keyboard`，Rime包装入口执行此二进制。适配Rime仍只用Qt公开输入法接口；前端改动在Plasma Keyboard自己的源码中，按其GPL许可保留补丁。安装顺序需先准备这个前端，再运行 `rime/install.sh`，否则包装入口缺少可执行文件。

> 2026-09-26起由`moto-plasma-input`包安装（`/usr/lib/moto-rime`、`/usr/libexec/moto-plasma-rime`），Rime包装入口直接执行重建的`plasma-keyboard +moto1`，不再有私有的`/usr/local`副本（61篇）。

修复后再验切换语言、横竖屏、输入 `nihao`、首候选“你好”和提交，没有新的LanguagePopup失效对象日志或输入法崩溃。证据：`rime-final-nihao.png`、`rime-menu-fixed.png`。英语切换仍保留；默认中文方案仅为朙月简体拼音，未来可单独评估雾凇拼音，不在本轮未经验证替换词库。

整机重启后再次验收：点Plasma入口自动启动，默认简体中文；真实触屏`nihao`首候选“你好”，键盘PID266持续运行，无新的输入法CrashExit或LanguagePopup错误。截图`rime-after-reboot-nihao.png`。

## 2026-10-03：两个键盘进程共用一份用户词库

**原因。** 全屏远程桌面窗口（`agent/screen`）将内嵌 Qt VKB 的 InputPanel 并加载同一个 `Rungic.Rime` 插件。用户决定两边共用同一份用户词库 `~/.local/share/plasma-rime`：在一处学到的词，另一处也排在前面。`luna_pinyin.userdb` 是 LevelDB，同一时间只允许一个进程打开（`LOCK` 文件的 fcntl 写锁）。旧实现在构造输入法时建会话、析构时才销毁，plasma-keyboard 只要加载了中文布局就一直占着词库，键盘收起也不释放；另一进程会打不开词库。2026-10-03 实机核对时 plasma-keyboard 没有打开 `plasma-rime` 下的文件（当时可能未加载中文布局），因此尚无实机冲突记录。

**源码核对**（librime 1.16.1、LevelDB 1.23、Qt VKB 6.10.2、plasma-keyboard 6.6.6，副本在 `.work/src-ref/`、`.work/pq/plasma-keyboard`）：

- `UserDictionaryComponent::Create` 的 `db_pool_` 只保存 weak_ptr；最后一个会话销毁时 `LevelDb` 析构、`Close()`，同步释放 `LOCK`。`ConfigComponentBase::GetConfigData`、`DictionaryComponent` 的方案配置和词典缓存也是 weak_ptr：全部会话关闭后，下次建会话要重新加载。
- **打开失败不能交给 librime 降级。** `UserDictionary::Load` 打开失败时安排 `userdb_recovery_task`。`UserDbRecoveryTask::Run` 先执行 `leveldb::RepairDB`：LevelDB 1.23 的 `repair.cc` 不取 `LOCK`，会改写 MANIFEST、把日志移到 `lost/`；修复失败还会把目录改名为 `.old` 后重建。对另一进程正打开的词库执行这些会损坏它。因此，在本进程确认可以独占词库之前，任何会话都不能尝试打开它。
- `create_session` 先按 `schema_list`/`user.yaml` 建默认方案（`Switcher::CreateSchema`）。翻译器在建引擎时通过 `UserDictionaryComponent::Create` 读取 `<命名空间>/enable_user_dict`；`schema_open` 与会话的 `Schema` 共用同一份内存配置。
- Qt VKB 的 `PlatformInputContext::updateInputPanelVisible` 在面板隐藏，或焦点对象不再接受输入时发出 `QInputMethod::visibleChanged`。plasma-keyboard 收到 KWin 的 deactivate 时先 `reset()`，没有上下文时再 `setVisible(false)`；键盘上的收起键走同一路径。
- `Keyboard.qml` 的 `updateInputMethod` 由最先显示的页面调用自己的 `createInputMethod()`，`sharedLayouts` 内的页面共用该实例。数字字段（`ImhPreferNumbers` → `symbolMode`）先显示 `symbols` 页。Ubuntu 的 Qt VKB 插件只有 Hangul、Hunspell、Thai，没有 Pinyin 和手写。

**改动**（`desktop/rime/`）：

- 第一个需要 Rime 的键才建会话；没有组合时，Backspace、Enter、Esc 不建会话。不在切到拼音模式时建：每次键盘显示都会切模式，只显示不打字的键盘也会占用词库。
- 没有组合时，面板隐藏（包括失焦）立即关闭会话；面板一直显示时，5 秒无键（`idleInterval`）后关闭。组合中（有预编辑或候选）不关闭；`reset`、提交或选词后重新计时。
- 目录锁 `rungic-userdb.lock`（flock，进程内计数）。只有持锁时才建“共享会话”（使用词库）；关闭最后一个共享会话后，先 `join_maintenance_thread` 再放锁。拿到目录锁后，再用 `F_GETLK` 检查 `*.userdb/LOCK` 是否被不取目录锁的进程持有（例如升级后仍运行旧插件的 plasma-keyboard）。
- 拿不到锁时建“访客会话”：建会话期间在内存中关闭各方案翻译器的 `enable_user_dict`，建完立即恢复。访客会话可以输入中文，但不学习，也读不到已学词。之后每个词的间隙重试共享会话，下次建会话也会重试。启动时探测配置是否共享，探测不通过就不建访客会话（按键按英文上屏）。
- 常驻一个方案配置句柄，会话关闭后不必重新解析方案 YAML。
- `layouts.py` 同时替换 `symbols.qml`，并断言 `zh_CN` 下不再有页面创建 `PinyinInputMethod`。此前数字字段先打开 symbols 页时，会报 `PinyinInputMethod is not a type`，随后改用 Qt 默认输入法（Hunspell），直到切到字母页才建 Rime，标点因此为半角。
- `rungic-rime-check` 未设 `RUNGIC_RIME_USER_DIR` 时改用临时目录（旧版会把 300 次组合写进真实词库）。它新增会话生命周期、两进程共享、外来锁和耗时检查。`tests/run.sh` 负责构建并运行该检查和 `tests/tst_session.qml`（offscreen Qt VKB）；`tools/tests/test_plasma_rime.py` 汇总调用。

**离线验证**（K8-Plus x86_64，Docker 内 Ubuntu 26.04 的 librime 1.16.1 / Qt 6.10.2，非实机）：

- `rungic-rime-check` 全部通过：无会话时没有打开的 `LOCK`，会话期间有 1 个，关闭后归零；本进程持锁时，另一进程得到访客会话，可输入“你好”，且不打开词库；释放后对方得到共享会话，本进程选出的“式”在对方排第一；外来 fcntl 锁同样得到访客会话，词库目录文件不变，没有 `lost/`。
- `tst_session.qml` 7 项通过：symbols 页先出现时也创建 Rime（`,` → `，`）；只显示键盘不建会话；隐藏、失焦或空闲时关闭；组合中隐藏不关闭，`reset` 后关闭。旧 `symbols.qml` 作对照时，第 1 项失败并出现 `PinyinInputMethod is not a type`。
- 建会话加第一个键的耗时：用户目录在 tmpfs 上首次 2.3 ms、重开中位数 2.2 ms；在 ext4/NVMe 上首次 35.5 ms、重开中位数 19.4 ms。LevelDB 每次打开都会写新的 MANIFEST/CURRENT 并 fsync，这部分受文件系统限制。不缓存方案配置时，tmpfs 上约 5.8 ms；会话常驻时约 0.4 ms。

**实机结果（2026-10-03，开发覆盖 rungic-plasma-input 0.510+dev20261002t182059）**：

- 部署时不重启会话，只对 plasma-keyboard 发 SIGTERM。KWin 把信号退出视为崩溃，会自动重启输入法（`InputMethod` 的 `CrashExit` 分支，20 秒内少于 5 次）。新进程 02:25:24 启动；桌面模式浮窗也已重启，以便加载新插件。
- 容器内 `rungic-rime-check`（用户目录在 `~/.cache`，与 `~/.local/share` 同一文件系统）全部通过：访客会话、释放后转为共享会话、两进程互通学到的词、外来锁不触发修复。
- 耗时：建会话加第一个键，首次 24.6 ms，重开中位数 13.5 ms，不需要预建会话。

**仍待用户实测**（以下第 1、2 步已完成，其余需要在界面上操作）：

1. 用 `tools/rungic_dev.py deploy rungic-plasma-input` 装开发覆盖，并确认 plasma-keyboard 进程是部署后启动的（`ps -o pid,lstart -C plasma-keyboard`）。旧进程会一直持有词库，新进程只能用访客会话。
2. 耗时：在容器内执行 `mkdir -p ~/.cache/rime-check && RUNGIC_RIME_USER_DIR=$HOME/.cache/rime-check /usr/libexec/rungic-rime-check`。目录要与 `~/.local/share` 在同一文件系统，不能用 tmpfs 的 `/tmp`。记录 `time:` 行；如果重开中位数明显超过 50 ms，再考虑在面板显示时预建会话或延长空闲时间。
3. 释放：在手机键盘上输入中文，期间 `ls -l /proc/$(pgrep -x plasma-keyboard)/fd | grep plasma-rime` 应能看到 `luna_pinyin.userdb/LOCK` 和 `rungic-userdb.lock`；收起键盘后两项消失。键盘保持显示且 5 秒不按键，两项也应消失。不打字时，`flock -n ~/.local/share/plasma-rime/rungic-userdb.lock true` 应返回 0；组合中应返回 1。
4. 数字字段先打开 symbols 页，输入 `,` 得到 `，`，日志中没有 `PinyinInputMethod is not a type`。
5. 收起再打开键盘后，刚选过的非首位候选仍排第一，说明会话关闭时已写盘。全屏键盘接入后，再在两边交替输入同一拼音核对共享；对方持有词库时，本边应能输入中文但不学习。

**剩余风险**：访客会话依赖 librime 方案配置缓存共享这一实现细节。启动时虽有探测，升级 librime 后仍须重新核对 `UserDictionary::Load` 的恢复逻辑和配置缓存。访客会话的引擎只在建立时读取开关；VKB 不发送切换方案的热键，所以不会重建翻译器。两个进程在 rime-data 升级后同时启动时，可能并发执行 `workspace_update` 写 `build/`，这是原有风险，现在两进程更容易遇到。实机上的 fsync 延迟未知。全屏键盘须加载同一插件、不设 `RUNGIC_RIME_USER_DIR`，并通过 `QInputMethod` 显示和隐藏面板；否则只能等空闲超时释放。

## 2026-10-03：敏感字段其实在学习词频（已修，离线验证）

- **发现**：Mac mini 上的无头系统测试 `rime_keyboard`（`tools/system/tests/`，Ubuntu 26.04 arm64 容器，librime 1.16.1，非实机）新增“敏感字段不学习”的检查：在 `Qt::ImhSensitiveData` 字段里输入 shi、选第 6 个候选“式”，再到普通字段输入 shi，首位变成了“式”。
- **原因**：插件对敏感字段调用 `set_option(session, "_no_learning", true)`，但 librime 没有这个开关（`librime.so.1.16.1` 中不含任何 `learn` 字样），设了等于没设；共享会话照常把选词写进用户词库。
- **修复**：敏感字段改用访客会话（`RimeRuntime::openGuest`，建会话时关闭 `enable_user_dict`），既不读也不写用户词库；离开敏感字段、没有组合时再回到共享会话。`sessionShared` 在敏感字段里为 false。
- **验证**：`tst_session.qml` 的 `test_7_sensitive_field_does_not_learn` 修复前失败、修复后通过；同一轮还检查了密码字段不进 Rime（`test_6`）、用户目录 0700 与 `default.custom.yaml` 只在没有时创建（`check.cpp`、`run.sh`）。实机尚未复核。

## 2026-10-07：按键的无障碍名称与标识

- **为什么**：读屏和操作手机的 Agent 都要先找到按键；图标键（Shift、退格、空格、切换语言）原本没有任何文字。
- **调研**：plasma-keyboard 6.6.6 与上游 master 的 `BreezeKeyPanel.qml`、Breeze `style.qml`、候选列表都没有 `Accessible` 声明；Qt Virtual Keyboard 6.10.2 的按键是普通 Item。字母键里的 QQC2.Label 会被 Qt 自动暴露为静态文字，但 Shift、退格、空格、切换语言是图标，没有文字。2026-10-07 用 KDE GitLab API 搜 plasma-keyboard 的 MR/issue（accessib、a11y、screen reader、Accessible），只有键盘导航 !25/!29，没有按键无障碍的工作（本轮未找到，不代表不存在）。
- **补丁** `packages/plasma-keyboard/debian/patches/rungic/accessible-keys.patch`（`+rungic2`）：每个按键面板是 `Accessible.Button`，名称是它输入的字符（大写状态时为大写）或功能（经 i18n 翻译，给读屏用）；另有不随界面语言变化的 `Accessible.id`：`key:<字符>`（无字符的键为 `key`）、`shift`、`backspace`、`space`、`enter`、`language`、`symbol`、`mode`、`hide`、`handwriting`，候选词 `candidate`、长按备选 `alternate`，语言列表项 `language:<地区>`。只加名称，不加 `onPressAction`：Qt 给按钮角色暴露的 Press 动作什么也不做，测试必须真实触摸。我们自己的浮动键盘（`agent/screen/vkb/rungic/style.qml`、`FloatingKeyboard.qml`）用同一套标识，顶栏另有 `esc`、`tab`、`ctrl`、`alt`、方向键、`hide`、`dock`。`rungic-a11y` 的节点输出增加 `id` 字段。第 3 轮之后再请 Kevin 决定是否提交给 KDE。
- **两个坐标事实**（测试工具在实机上点手机键盘时同样要遵守）：
  1. plasma-keyboard 的窗口铺满整个输出（`InputPanelWindow` 的 `height: Screen.height`），按键画在底部，AT-SPI 坐标相对输出；KWin 的输入法面板（`inputMethod` 窗口，`clientGeometry` 只有可触摸的那块，例如 720×450 位于 y=1050）记在 KWin 自己名下，不能按 plasma-keyboard 的 PID 找。`rungic_agent.ui_tap` 目前按 PID 和窗口尺寸找窗口，点不到这个键盘；按截图坐标触摸即可。
  2. 语言列表是 Qt Quick 弹出层，AT-SPI 给它的内容以弹出层自己为原点（对话框节点在 0,0）。`LanguagePopup.show()` 把列表左下角放在语言键左上角，所以按这个锚定换算位置，并以语言确实切换作为验证。2026-10-07 无头测试中按报告坐标或面板偏移去点都不中，按锚定换算后切换成功。
- **中文句号**：中文字母页右下角的键是全角点“．”（U+FF0E），句号“。”（U+3002）在符号页的第 2 页，长按“．”的备选里也有。验收清单的 `你好，中国。` 用的是“。”。
- **验证**：2026-10-07 在 Mac mini 无头 KWin 上，两个键盘都逐键真实触摸输入 `Rungic E2E AbC123\n你好，中国。\n`，读回全文 37 字节与预期一致（当时的无头测试工具未合入）。实机由验收清单 E2E-02、E2E-08 覆盖（docs/121）。

## 2026-10-07：新账户没有 Rime、KWrite 里拼音逐键上屏（G100 实机，已修）

发版验收清单（docs/121）第一次在 G100 上跑时发现两处，均已在实机复核。

- **新账户用的不是 Rime 键盘，也没有英文**：plasma-mobile 在自己的 `~/.config/plasma-mobile/kwinrc`（XDG_CONFIG_DIRS）里写 `InputMethod=…/org.kde.plasma.keyboard.desktop`，我们只在迁移旧路径时（`rungic-usr-paths.sh`）改输入法；`plasmakeyboardrc` 没有 `enabledLocales` 时 plasma-keyboard 只开系统语言，语言键是灰的，设置页的语言列表里也没有 Rime 中文。新的 kconf_update `rungic-keyboard-defaults.sh` 在 KWin 启动前，用户自己的 kwinrc 没写输入法时设成 `rungic-plasma-rime.desktop`，没写语言时设成 `zh_CN,en_US`（与浮动键盘的默认一致）；只读用户自己的文件（绝对路径，不级联），用户选过的不动。改了用户 kwinrc 之后，正在运行的 KWin 要到下次会话才换输入法进程（`kwriteconfig6 --notify` 和开关虚拟键盘都不行）。
- **KWrite 里每个拼音字母都上屏成首候选**（“nihao”→“你i和啊哦”）：KTextEditor 把预编辑算进光标周围的文字，经 Wayland 到键盘就成了“周围文字变了”，Qt VK 调输入法的 `update()`，插件在那里提交组合。而且回报比下一个键晚一步（显示 “ni h” 时才收到含 “ni” 的回报）。设置页那种普通 Qt 输入框不这样，所以无头测试没发现。修复：组合开始时记下周围文字和光标，`update()` 里如果只是本次组合显示过的某个预编辑被原样算进了光标处，就不提交；光标移开或文字在别处被改才提交（`desktop/rime/echo.h`，`rungic-rime-check` 的 10 条检查）。实机：KWrite 里 “ni hao” 留在预编辑区，首候选“你好”，屏幕键盘逐键打出整段夹具并保存为 37 字节、SHA256 与预期一致。
