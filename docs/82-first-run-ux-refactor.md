# 首启 UX 重构：第一批实施

日期：2026-09-28。对应 [81 篇全流程审计](81-end-to-end-user-experience.md) 的安装、账户、桌面进入与通知问题。沿用 [80 篇](80-g100-image-installation-retrospective.md) 已验证的安全初始化顺序，不改变 Magisk 的部署触发时机。

## 用户路径

1. 打开 Rungic，显示当前实际准备阶段。安装状态缺失、版本不匹配或内容损坏时继续等待，不能打开账户表单。
2. 原子安装状态匹配本次 release 后，由 root 控制器核验安装完成标记、共享挂载与容器账户环境。
3. 环境准备通过才显示创建账户表单，说明这是 Linux 账户密码，与 Android 锁屏不同；提供明确标签、密码可见开关与键盘下一项操作。只保存非敏感的用户名草稿，密码提交后清空。
4. 创建操作由账户助手持有独立事务锁。查询会报告创建进行中；调用方超时后先查询真实状态，不能直接诱导重复提交。
5. 启动会话后等待手机显示链路确认，再移除 loading。旧帧、投屏输出和人工合成的超时反馈不能满足门槛。
6. 准备中、等待账户、运行中及需要处理的通知与界面保持一致。返回 Android 保留会话；关闭会话另行说明会结束 Linux 应用并要求确认。

loading 页面可滚动；等待期间可以返回 Android，稍后继续。错误详情与面向用户的操作提示分开。状态停留超过三分钟只提示“较长时间没有阶段更新”，不将它认定为安装失败。

## 实现与契约

| 层 | 实现 | 契约 |
|---|---|---|
| 首启生产者 | `tools/ci/rungic-firstboot.sh` | 原子 `schema=2`，含 release、state、phase、updated、error；区分校验、空间、共享存储错误；存储等待有界，空间预检保守预留整镜像与 512 MiB |
| Android 入口 | APK 2.8 / versionCode 56，`StartupScreen`、`FirstBootState`、`MainActivity` | 兼容 schema 1/2，拒绝未知未来 schema；按状态放行；检查按钮只重新读取/核验，不盲目重做安装 |
| 账户 | `plasma/account/setup.py` 与 `plasma/rungic-plasma` | 助手自身持有 `account.lock`；`--status` 返回 `pending`；保持原账户校验、stdin 传密码与失败回滚 |
| 包与镜像 | `rungic-plasma-session` | `/usr/share/rungic/account-protocol` 为 2；rootfs 报告记录 `account_status_protocol=2`，组包拒绝旧账户组件，安装验收检查实机标记 |
| 手机画面 | `presentation_gate.rs`、Presenter、GLES backend、JNI | 请求在渲染线程设置屏障；当前请求之后的手机帧才能确认；Surface 销毁与取消使旧请求失效 |

APK 与 native `.so` 必须一起构建。账户助手、宿主控制器和包含协议标记的新 rootfs 必须一起发布；不能用仅更新 APK 的结果代替新版镜像账户流程验收。

### 显示确认的定义

优先复用现有 SurfaceControl Presenter 的真实 transaction completion：有效 fence 时间或 latch 反馈，且手机 Presenter 可见。渲染反馈的超时合成值不放行。GLES 路径使用 `EGL_ANDROID_get_frame_timestamps` 的 `EGL_DISPLAY_PRESENT_TIME_ANDROID` 正时间值；不以 `eglSwapBuffers` 返回或提交帧计数当作显示证明。

这确认了手机图形输出链路。画面可能是 Plasma 启动画面或欢迎页，**不代表桌面全部服务与应用已经可交互**。若某设备不支持这些反馈，将显示可恢复的显示确认超时，不假定其他机型与 G100 等价。60 秒是显示检查的超时边界，不是强行放行的等待时间。

## 复用调查与取舍

实施前核对了 79/80 篇、research/31、现有 Android 入口、账户助手和 native Presenter 源码，并查阅以下一手接口说明：

- [Android SurfaceControl.Transaction](https://developer.android.com/reference/android/view/SurfaceControl.Transaction)：复用本项目已有 NDK 事务完成通道，核对它与提交计数的区别。
- [Khronos EGL_ANDROID_get_frame_timestamps](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_get_frame_timestamps.txt)：核对 pending/invalid、frame ID、display present time 和启用方式；常量、签名另对照本地 NDK r26 的 `EGL/eglext.h`。选择实际显示时间；放弃以 swap 返回假定画面可见。
- [Android 原生 View 可访问性](https://developer.android.com/guide/topics/ui/accessibility/views/apps-views)：复用 TextView、ProgressBar、ScrollView 与表单 labelFor/live region，无需新增 UI 框架。
- [KDE 状态变化指导](https://develop.kde.org/hig/status_changes/)：采用持续状态与可理解的后续动作，避免把底层失败直接作为要求用户重新输密码的理由。

本轮没有复制新上游实现或引入第三方依赖，平台 API 与扩展按接口调用；现有 native 外来树及其许可证来源仍按 `vendor/manifest.json` 和既有来源记录维护，不以本轮修改重新授权。

## 验证记录与边界

已完成：

- Android Java 编译、ARM64 native 编译、APK 打包和签名校验。
- 安装状态测试：schema 1/2、未知版本、release 不匹配、损坏/截断、阶段陈旧、存储等待和校验失败。
- 账户助手 6 项测试：校验、成功提交、失败回滚、已有密码保护、只读状态、事务锁期间 pending 优先于完成标记。
- 显示门槛 3 项测试：旧帧与新请求隔离、取消后排队请求不能复活、屏障前的提交不放行。
- 首启/控制器 shell 语法及 CI Python 编译检查。
- G100 保留现有账户的候选 APK 启动检查：显示确认后 loading 移除、通知为“Rungic 正在运行”，屏幕进入 Plasma Mobile 欢迎页；测试后恢复息屏。此结果不代表完成欢迎向导或创建了新账户。

产物、日志、截图与更新前 APK 备份保存在 `.work/ux/`；构建日志为 `.work/ux-native-build.log`、`.work/ux-apk-build.log`。

尚未验证：**包含新账户包、新控制器及新首启脚本的完整清数据刷入**。本轮不重刷、不清除账户，也不进行摄像头验收。实机 Shell 的 root 授权未开放；不能把 Rungic 应用自己的 root 授权当作 Shell 已获授权。

## 后续整包必须覆盖的场景

1. 清数据首启，安装中进入/退出 Rungic、锁屏解锁后恢复阶段显示。
2. 准备全部通过后才出现表单；非法输入仍在表单修正，环境故障返回准备状态。
3. 提交账户时切后台/超时/重进，不创建第二个账户，不丢失已成功提交的状态。
4. 当前手机输出确认前保持 loading；Surface 重建、息屏恢复及显示超时有明确结果。
5. 系统预装 APK（含 JNI 库）与新账户包共同验收，不能依赖 `pm install -r` 覆盖来证明镜像可用。

审计中的左右返回一致性、助手/投屏引导、刷机主机与手机的进度衔接、Plasma 欢迎向导整合仍需后续批次处理。本轮状态文件提供阶段更新，不提供解压字节百分比或持续工作心跳。缓存只按已验证产物的明确路径清理，保留本轮回退与证据材料，不执行全局 prune。

### 本轮构建完成记录

源码提交 `96a89955`。Mac mini 按系统代理配置构建 `rungic-plasma-session_0.296_arm64.deb` 成功；解包确认协议标记为 2，账户助手与提交源码逐字节一致。该包尚未安装到手机，也未进入新版 rootfs。包位于 `.work/apt/repo/`，其 SHA-256 为 `7188be5b6e60431dc6029063f6ece74859a5c6d7b24202c084ad06f11033f5e1`。

最终 APK SHA-256 为 `9b70c965c4f7f5b41d78190624e29d89340b6444f51fc28691cd4c80cb819fba`，内含当前编译的 native 库；沿用 G100 `.5` 的无 OCR 构建配置。通过流式更新安装后再次进入 Plasma 欢迎页，并检查了切到 Android 后返回。`final-resume.png`、`final-startup.log`、`final-errors.log` 留在 `.work/ux/device/`；错误日志未见本轮 AndroidRuntime / RungicWayland 异常。测试结束 `mWakefulness=Dozing`。此次不经过只读 product 预装路径，不能替代完整镜像验证。

最终 APK 部署回归通过后，确认没有运行中的 cargo/rustc，定向清理本机 native target（933 MiB）、APK staging、Java 测试类与测试可执行文件，合计约 951 MiB。签名 APK、可复用 native 库、Deb 包、回退 APK、日志和截图保留；`.work/ux/` 现约 12 MiB。账户包尚未部署，其远程工作目录未按“部署通过”清理；未执行全局 Docker/Podman prune。

### 取消额外的 Plasma Mobile 欢迎向导（2026-09-29）

用户要求首次进入直接到桌面。核对固定 Plasma Mobile 6.6.5 源码：`kded/start/start.cpp` 分别启动 envmanager 与 `plasma-mobile-initial-start`；后者 `initialstart/settings.cpp` 从 `plasmamobilerc` 的 `[InitialStart] wizardRun` 判断是否显示向导，`initialstart/main.cpp` 在加载界面前检查并退出。相关上游文件为 GPL-2.0-or-later / LGPL-2.0-or-later，来源 [KDE Plasma Mobile](https://invent.kde.org/plasma/plasma-mobile)，版本/源码哈希见 `packages/plasma-mobile/recipe.json`。同类新组件 Plasma Setup 的账户创建流程不是本机实际运行的这个程序，不能混用关闭方式。

采用上游已有 KConfig 开关：新增 `plasma/config/etc/xdg/plasmamobilerc`，设置 `[InitialStart] wizardRun=true`。由 `rungic-plasma-config` 打包为系统默认，适用于新账户，不依赖复制个人 home 或首次启动脚本逐用户写配置。保留用户显式覆盖的能力；需要查看向导时仍可手动 `plasma-mobile-initial-start --test-wizard`。不删除程序、不关闭 kded 模块，envmanager 的正常桌面配置继续运行。Rungic 的安装 ready、账户配置、真实画面 loading 门槛保持原样。

G100 / portov / 5038 / ZY32M9MRVP 已部署该单一配置文件，并关闭当前已有的一个欢迎向导进程。验证：隔离空 HOME/XDG_CONFIG_HOME，显式 phone 平台，KConfig 继承 true；实际 ARM64 欢迎程序用 offscreen 平台运行，返回 0 并报告不启动向导；现有账户按实际会话环境重新启动该程序同样直接退出。证据 `.work/experiments/g100-welcome-20260929/verification.json`。这是干净配置与现有账户验收，不是重新清数据刷入整包的验收；镜像后续构建通过配置包获得该默认值。


## 首装离开前台后的通知（2026-10-07）

复用 `DesktopService`、`FirstBootState` 和原有账户控制器。仅首装窗口、Activity 不可见时，每五秒读取原子安装状态；前台回来即取消后台任务。两者的控制器调用共用同一个所有权锁，取消中的进程也会终止。未知状态或账户查询失败保留上一条通知，不推断安装失败。

准备中显示真实阶段；ready 后独立确认账户。尚未配置时显示“Rungic 已准备好／点此设置账户，完成后即可进入桌面”；失败显示“Rungic 准备没有完成／点此查看原因”。已配置账户或失败后停止后台检查。点击仍进入原有 `MainActivity`，由原有准备、账户或失败状态决定页面。

原常驻 `desktop` 渠道继续使用 LOW。需要用户回来操作的提醒使用新 `install-action` 渠道（DEFAULT），标题与正文设为 PUBLIC，并使用 `setOnlyAlertOnce(true)`；不修改用户已经设置的旧渠道。Android 的渠道创建后重要性由用户控制，PUBLIC 也不能覆盖用户或系统的锁屏通知设置。来源：[渠道文档](https://developer.android.google.cn/develop/ui/compose/notifications/channels?hl=en)、[通知文档](https://developer.android.google.cn/develop/ui/compose/notifications/create-notification?hl=en)。

离线验证运行实际 Java 服务、状态文件读取、生命周期取消和调用所有权；通知系统和账户控制器是替身。完整 Android Java 编译通过。第二轮仍需真实锁屏截图、通知内容、点击后页面以及前后台切换证据，宿主结果不能代替。
