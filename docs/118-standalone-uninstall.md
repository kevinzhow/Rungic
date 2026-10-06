# 独立卸载 Rungic：范围、保留的家目录与报告

开发入口为 `tools/ci/standalone.py uninstall`。默认只读预览。只有显式设备、端口、`--yes-delete` 和新的报告目录齐备时才执行。这里只卸载 Android 用户 0 的 Rungic，不刷写分区，不改 Android 镜像，不删除 Termux、其前缀、无关用户文件或 Android 共享文件。

本功能尚未通过真机卸载、重启及重装验收。本篇描述实现约定与离线证据。不能据此认定 USB G100 的首装或整版质量通过。

## 预览与执行

以下设备是本次指定的 USB G100：`ZY32M9MRVP`，ADB 端口 `5037`。日常 G100 S 不在本次范围。执行者仍须现场核对设备及授权，不能把示例当成通用设备默认值。

```sh
# 只读预览。也可加 --purge 预览删除当前家目录的计划。
python3 tools/ci/standalone.py uninstall \
  --adb-port 5037 --serial ZY32M9MRVP \
  --report .work/uninstall/preview-001
```

经授权执行默认卸载：

```sh
python3 tools/ci/standalone.py uninstall \
  --adb-port 5037 --serial ZY32M9MRVP --yes-delete \
  --report .work/uninstall/run-001
```

`--purge` **永久删除当前 Linux 家目录，不能恢复**。它不删除以前保留的家目录，不绕过停止、挂载或映射检查。本次 L3 的 A00 由测试负责人在冻结候选及设备条件后执行，使用已授权的 `--purge` 建立新的 Rungic 起点。它不是清空 Android 的全新手机起点。

```sh
python3 tools/ci/standalone.py uninstall \
  --adb-port 5037 --serial ZY32M9MRVP --yes-delete --purge \
  --report .work/uninstall/purge-001
```

预览实际执行 root 侧的路径、进程、挂载表、loop／dm 和 home mount ID 只读检查。报告逐项显示 PASS、BLOCKED 或 UNKNOWN，并标出按当前条件会停下的首个检查。所有检查都执行，即使前项已经阻塞。歧义、非数字或读取失败的挂载解析显示 UNKNOWN。预览不停止服务或取得协调锁。执行时会先停止服务并重新检查，因此预览不是停止成功或实际删除可行的证明。

每次使用新的报告目录。中断后保持相同的保留／purge 选择，用新报告目录重跑。工具从设备上持久保存的操作记录恢复，不从旧报告的绿色结果推断现场。

## 删除和保留的路径

安装与卸载共用 `tools/ci/install_paths.py`。首启脚本使用固定生产路径，不受路径环境变量影响。测试复制脚本并替换路径。清单测试扫描安装器与首启脚本的 `/data/adb/...` 字面路径，要求每项有明确删除或保留归属。宿主载荷、投屏服务及临时文件也有一致性检查。

| 对象 | 行为 |
| --- | --- |
| `rungic-lxc`：镜像、runtime、账户状态、日志与崩溃文件 | 停止并核对资源后删除。默认先保留它内部的 `state/home` |
| `rungic-plasma`、独立安装载荷、旧种子备份、投屏组件 | 删除固定的 Rungic 路径 |
| 首启、独立运行、投屏和策略的 `service.d` 入口及安装临时文件 | 删除，包括已知 `.tmp`／`.new` 中断文件 |
| Termux 内 `.rungic-stage` 和 `usr/tmp/rungic-plasma-audio` | 只删除这两个 Rungic 私有路径。先确认专用音频进程停止，保留 Termux 其他数据 |
| `/data/local/tmp/rungic-<release>` | 仅删除实际安装描述符选中的 release。中断后从记录取得同一范围。其他临时目录列为范围外残留 |
| Rungic Android 应用 | 以 Android shell 身份清数据、删除同一包名的更新、为用户 0 卸载。不直接删除 `/data/app` |
| product 中旧 APK 与首启种子 | 保留，只读 Android 底座不修改 |
| Termux 应用、前缀与无关数据，`/storage/emulated/0/Plasma` | 保留 |
| `/data/adb/rungic-preserved/` 与历史证据备份 | 保留。purge 不推导出清理历史资料的授权 |
| Magisk 空兼容模块、阻止旧种子的标记及协调锁 | 保留，报告逐项说明原因。不能删除仍由进程持有的锁 inode |

未列入删除清单的目录不会因名字中带 `rungic` 就被删除。报告展示范围外残留。“范围内卸载完成”不表示任意历史安装痕迹都已清空。

## 家目录：只移动，不复制

家目录在 `/data/adb/rungic-lxc/runtime/var/lib/lxc/plasma/state/home`。默认把它整体移到 `/data/adb/rungic-preserved/home-<操作ID>/home`，再删除 runtime，因此原有首装前置检查仍可通过。安装器不会自动认领保留的家目录。

1. 停止已安装控制器，读回容器为 STOPPED，核对相关进程、各进程挂载表、mapper 和 loop。任何未知或失败都停止删除。读取某个 PID 的 cmdline／mountinfo 失败时，仅在该 PID 目录已经消失的情况下跳过。目录仍存在而无法读取则停止。
2. 按当前 `/proc/self/mountinfo` 最长挂载路径匹配源和目标父目录。必须是同一 mount ID。源本身及下级残留挂载均拒绝。不同挂载即使设备号相同也拒绝。无法解析或存在歧义也拒绝。
3. 意图记录仅含四项：固定源、固定目标、设备／inode 标识、状态。写入后 `sync`，不遍历或读取全部家目录内容。
4. 在已停止、已协调的受支持流程里重新核对挂载与目标不存在，再 `mv -T`。两次解析必须分别成功且得到有效数字 mount ID，然后才比较。移动后核对 home 根目录的原 inode。rename 没有复制数据，原 inode 上的 ACL、扩展属性与 SELinux 标签随目录保留。工具不声称枚举校验了全部文件或手机不能枚举的 xattr。
5. 同步已移动记录后才删除 runtime。意图损坏、源／目标同时存在、两处均不存在、目标碰撞、home 根目录 inode 变化都报失败。移动完成但记录尚未更新时，重跑先验证目标，再继续。不会复制或覆盖它。

手机 Toybox 的 `getfattr` 仅能按属性名读取，Magisk BusyBox 没有该 applet，因此没有采用复制后“校验全部元数据”的方案。也没有增加私有挂载命名空间或复制空间分支。协调锁覆盖本工具的安装／卸载及已有首启／投屏锁。不把手工 root 命令当成受支持的并发流程。

## 阻止旧 product 重新安装

旧 init_boot 每次启动会重建首启 service，单删 service 无效。卸载保留有效的 `/data/adb/modules/rungic-install-compat`，其 product 首启覆盖脚本只返回成功。同时留下 `rungic-uninstalled`。新独立安装发布有效载荷及 dispatcher 后才解除卸载标记。

本地沙箱证明空脚本不会调用旧种子，不能证明某个固件的 Magisk 覆盖已生效。必须在 USB G100 重启后，不打开应用，读回旧 runtime、账户和安装标记没有重新生成。此项仍待真机验收。

## 报告与失败恢复

`report.json`、`report.md` 和实际发送的 `uninstall-root.sh` 保存在主机。身份不符、步骤失败、超时或中断也保留报告。设备上 `rungic-preserved/uninstall-<操作ID>.log` 和家目录意图记录保存进度。保留记录不包含文件内容或密码。

JSON 分开列出删除、原已不存在、移动保留、失败、未尝试删除、明确保留原因及范围外残留。每条删除路径在 JSON 的 `path_results` 和 Markdown 中分开记录操作结果与最后独立读回。脚本报告已删除而读回失败时明确写“读回未完成，未确认”。不会用操作输出代替确认不存在。移除未完成标记后也独立读回其不存在，才报完成。删除失败或读回失败会非零退出、`complete=false`。停止服务可能改变运行状态，“未尝试删除”不表示手机运行状态完全没变。

标记位于 runtime 外的 `/data/adb/rungic-uninstalling`，不会跟随 runtime 删除。新操作在尚未移动资料、修改拦截或删除路径时失败，正常退出清理会撤掉标记。已开始改变安装或正在恢复旧中断操作时则保留。突然断电、进程被强制杀死或连接不可用时，不能保证退出清理运行，应先重跑卸载检查现场，不能手工删除标记冒充成功。

中断期间标记保持阻止状态，新工具的 install、首启、账户准备与运行入口拒绝启动。控制器在取锁前后都检查。安装器在上传前提示先重新运行卸载。App 独立识别该标记属于另一个待交付功能（task #45）。当前 App 仍可能显示一般启动或首次安装等待信息，不能据此认定卸载继续运行。

继续使用相同 purge 选择恢复。最终读回成功后才明确移除该标记。缺失路径可重复处理。损坏记录或失踪目标不能自动重新创建，应保留现场并由负责人诊断。

## 手动恢复保留的家目录

恢复是另一个操作，不由卸载工具执行，也不能从卸载授权推导。

1. 新安装创建与旧账户同名的账户，核对 passwd 中的 家目录路径及数字 UID/GID。密码由新账户设置。
2. 停止 Linux，确认 Shared 及其余相关挂载均解除，保留新 `state/home` 作为回退。
3. 在同一挂载内把报告中的保留 home 移回原 `state/home`，不得覆盖正在使用的目录。不跨文件系统复制冒充原地恢复。
4. 核对原内容、所有者、权限、链接、ACL、SELinux 标签及实际账户访问，再启动并验证用户文件。保留的家目录及恢复结果须单独记录。

## 离线验证与待验边界

`tools/ci/test_uninstall.py` 在 Bash 与 BusyBox ash 下，在真实临时文件系统中执行生成的 shell：默认与 purge 预览、保留隐藏文件／空目录／权限／链接／xattr、移动前后原 inode、真实 install 前置入口、跨 mount ID、停止／进程／挂载／mapper／loop 失败、读取 /proc 时进程退出及仍在却不可读的对照、二次挂载解析失败与非数字 ID 的禁止移动／删除断言、意图及目标异常、中断重跑、空旧种子拦截、清单新增路径及失败读回。

ADB、包管理器与 Android 内核环境仍为替身。mountinfo 故障为显式现场文件。真实设备的停止与权限、Magisk 重启拦截、APK 更新卸载、默认保留后的完整重装、恢复体验及 L3 连续流程均待独立测试。开发者没有执行卸载、刷机或重启。

## 旧控制器兼容

卸载工具只调用旧版本也支持的 `stop`，不要求 `runtime-status`。停止后，由工具独立检查进程、所有相关挂载表和 dm/loop 映射；任何阻塞或未知仍禁止移动家目录及删除运行时。旧整包 G100 的第一次 purge 因旧控制器没有 `runtime-status` 而失败，停止服务后没有删除内容；此修复的本地替身覆盖该接口边界，不替代后续真机复验。安装载荷与主机工具的提交分别记录。
