# 122 设计：开机收敛与按顺序的一次性迁移（P1、P2）

2026-10-08。设计稿，还没实现。起因是 dev release 20261008.1 之前的核对（docs/121 复验、#79）：首装和发版各有一份 Android 文件清单，#57 只改了其中一份。电池优化白名单写在 firstboot 里，而发版送不到 firstboot。Kevin 当天同意的顺序是：P0 统一文件清单（#79，已合并）→ 发 dev release → 本设计（P1 开机收敛 + P2 一次性迁移）→ P3 宿主程序进发布清单 → P4 drift 覆盖全部类别。

2026-10-08 调研过的业界做法（systemd tmpfiles/sysusers、ostree 的 /etc 三方合并、Android SettingsProvider 的 UpgradeController、NixOS stateVersion、KDE kconf_update、GitOps 的漂移检测）。共同点有四条：期望状态只在一处声明；每次开机或更新时按声明收敛一遍，可以重复执行；做不到幂等的改动按顺序只执行一次；更新后核对实际状态。

## 现在的问题

把“手机上应当成立的状态”按谁负责、何时更新分成五类（2026-10-08，main a2b4e78）：

| 类别 | 例子 | 现在靠什么 | 发版能不能更新 |
| --- | --- | --- | --- |
| 容器里的系统文件 | `/usr`、包里的 `/etc` | deb 包 + APT pin | 能 |
| 容器里只做一次的默认值 | 服务默认开关（`/var/lib/rungic/service-defaults` 账本） | 包的 postinst | 能，但没有统一的写法 |
| 用户配置 | `~/.config` 里的 KDE 设置 | kconf_update，`Id=…-v1`，记在 `kconf_updaterc` | 能 |
| Android 一侧的文件 | `/data/adb/rungic-plasma/*`、`service.d` | 发版清单 `android`（#79 起和首装一致） | 能；投屏的文件除外 |
| Android 的设置 | appops、root 授权、电池白名单、目录属主和标签、运行时权限 | firstboot 的“完成标记”之后，或宿主安装器 `standalone.py` | **不能**，只有重装才会再执行 |

最后一类是这次要解决的主要问题。`tools/ci/rungic-firstboot.sh` 第 85–90 行检查完成标记 `/data/adb/rungic-firstboot.complete`（内容是 RELEASE_ID），标记对得上就直接退出。所以下面这些只在首装执行一次：

- `appops set com.rungic.plasma SYSTEM_ALERT_WINDOW allow`（:270–275）；
- Magisk 的 root 授权，写进 `policies` 表（:276–283）；KernelSU 只打印一句提示；
- 家目录、`files/tmp`、`/storage/emulated/0/Plasma` 的属主、权限和 SELinux 标签（:143–146、:258–263）。

运行时权限（RECORD_AUDIO、CAMERA、POST_NOTIFICATIONS、BLUETOOTH_CONNECT/SCAN、READ_PHONE_STATE）连 firstboot 里都没有，只在宿主安装器 `standalone.py:469–476` 里授予。

还有两个相关缺口：

- **投屏的文件**：`/data/adb/rungic-wfd/service.d/rungic-wfd-sepolicy.sh` 等由 `shared/android/rungic-cast/install.sh` 安装，不在发版清单里。20261008.1 部署后，G100 S 的 drift 只差这一个文件。
- **drift 看不到 Android 的设置**：`rungic_release.py drift` 比较包、APK 和 Android 侧文件，不比较上面这些设置的实际值。

电池白名单已经在 #79 挪进了 `rungic-runtime`，每次开机执行一次，在 G100 上验证过（重启后不打开 App，强行停止 App 后 Linux 照样联网）。P1 就是把这个做法推广成一套机制。

## P1：开机收敛

### 做什么

新增 Android 侧脚本 `system/rungic-converge`，装到 `/data/adb/rungic-plasma/rungic-converge`，列进发版清单 `android` 和首装的 `ANDROID_FILES`（两份清单由 #79 的测试保证一致）。它是“Android 的设置”这一类的**唯一声明**：每一项写清楚检查什么、不对时怎么改、改完怎么确认。

```
rungic-converge apply     # 检查每一项，不对的改过来，再确认
rungic-converge check     # 只检查、不改，供 drift 和诊断用
```

每一项的结果是四种之一，写一行日志到 `$STATE/host.log`（和 rungic-runtime 同一个文件、同一种格式），并汇总到 `$STATE/converge.json`：

- `ok`：本来就对，没动；
- `changed`：改过之后确认对了；
- `refused`：改了但系统不接受，或改完确认仍不对（记下原因）；
- `report`：这一项只报告、不改（见下面的“谁说了算”）。

任何一项失败都不阻止 Linux 启动。每一项有超时，整体不超过 30 秒。

### 什么时候执行

1. **每次开机**：`rungic-runtime watch` 等到解锁后（现在白名单所在的位置，:85–92）执行一次 `apply`，替代现在内联的白名单代码。
2. **每次部署之后**：`rungic_release.py deploy` 在同步完 Android 侧文件（`sync_android`）之后执行一次 `apply`，记进部署记录。这样发版带来的新设置当场生效，不用等重启。
3. **首装**：firstboot 结尾不再自己写 appops 和 Magisk 授权，改成调用同一个 `rungic-converge apply`。首装和日常只有一份代码。

firstboot 保留只做一次的事：解包、校验、写 rootfs、生成 SSH 主机密钥、写完成标记。

### 收敛哪些项

| 项 | apply 做什么 | 谁说了算 |
| --- | --- | --- |
| 电池优化白名单 | `dumpsys deviceidle whitelist +com.rungic.plasma` | Rungic（Kevin 2026-10-08 已同意每次开机恢复） |
| 悬浮窗 appops | `appops set … SYSTEM_ALERT_WINDOW allow`，失败时用 shell 身份重试 | Rungic |
| Magisk root 授权 | 查 `policies` 表，缺了再 `INSERT OR REPLACE` | Rungic |
| KernelSU root 授权 | 只检查（没有可写的接口），未授权时 `report`；App 里已有提示（#57） | 用户 |
| App 数据目录、`files/tmp` | 属主是 App 的 uid，标签同 App 数据目录 | Rungic |
| `/storage/emulated/0/Plasma` | 存在，属主和标签正确 | Rungic |
| Linux 家目录 | 属主 1000:1000、权限、标签（:143–146 的规则） | Rungic |
| 运行时权限 | 只检查，未授予时 `report` | **待定**，见“要 Kevin 定的事” |
| `service.d` 启动钩子 | 已经每次由 firstboot 的标记前部分安装（:77–84），不重复 | — |

“谁说了算”是指：用户在系统设置里改了这一项，下次收敛要不要改回来。Rungic 运行必需、用户不会去管的项，收敛时改回来；用户在设置界面里有意做的选择，只报告、不覆盖。

### 投屏文件

投屏文件属于“Android 侧文件”，不是设置，所以不放进收敛脚本。做法是把 `rungic-cast/install.sh` 安装的文件也列进发版清单，让 `sync_android` 照常更新。投屏是可选组件（docs/75），所以只更新手机上已经装了投屏的那些文件，没装的不新装。#79 的双向测试目前把投屏排除在外（“投屏有自己的安装器”），这一条要相应改成：投屏文件在发版清单里，带“仅在已安装时更新”的标记。

### 测试

`tools/ci/test_converge.py`，沿用 `test_runtime_boot.py` 的办法：在临时目录里跑真实脚本，替换 `dumpsys`、`appops`、`pm`、`magisk`、`ls -Z`/`chcon`。对每一项覆盖四种情况：

- 已经对：`ok`，没有任何写操作；
- 不对：执行写操作，结果是 `changed`；
- 系统拒绝：结果是 `refused`，后面的项照常执行，Linux 照常启动；
- `check` 模式：任何情况下都不写。

另外检查：`rungic-runtime` 每次开机只调用一次，并且在启动容器之前；firstboot 里不再有 appops 和 Magisk 授权的代码（防止又出现两份）。

真机验证：在 G100 上把每一项手动改坏（撤销白名单、撤销悬浮窗权限、删掉 Magisk 授权、改坏目录属主），重启后不打开 App，看日志里每项都是 `changed`，再跑一遍 `check` 全是 `ok`。

## P2：按顺序的一次性迁移

### 什么时候需要

收敛只适合“可以反复执行、结果总是一样”的事。有些改动只能做一次，例如：把旧位置的数据搬到新位置、修复被旧版本写坏的配置（#78 的 `rungic-keyboard-repair-v1`）、给已有用户设一个新默认值但以后尊重用户的修改（服务默认开关）。

### 现有做法已经是“账本”

调研后改一下之前的提法：我原来建议用一个整数 `state_version`，逐级加一。但仓库里现有的两种一次性机制其实都是“账本”，记下哪些迁移已经做过：

- kconf_update：每条迁移一个 Id，做过的记在 `~/.config/kconf_updaterc`；
- 服务默认值：`/var/lib/rungic/service-defaults`，每个服务一行。

账本比整数更适合我们：几个 PR 经常并行开发、合并顺序不定。用整数时，两个 PR 都会加“第 7 号迁移”，合并时冲突，或者其中一台手机已经跑过另一个 7 号。用账本时，每条迁移有自己的名字，没跑过的就跑，和合并顺序无关。

所以 P2 不引入新的版本号，而是**把账本做成统一的写法，并补上缺的那一个范围**。

### 三个范围

| 范围 | 迁移放在哪里 | 账本 | 谁来执行、什么时候 |
| --- | --- | --- | --- |
| 用户（`~/.config` 等） | kconf_update（不变） | `~/.config/kconf_updaterc` | `desktop/session`，KWin 启动前（不变） |
| 容器系统（`/etc`、`/var/lib`） | `/usr/lib/rungic/migrations/system/<名字>.sh`，由对应的包提供 | `/var/lib/rungic/migrations` | 新的 `rungic-migrate system`，在包的 postinst 里调用 |
| Android 侧（`/data/adb`、App 数据） | `/data/adb/rungic-plasma/migrations/<名字>.sh`，随发版清单 | `/data/adb/rungic-plasma/migrations.done` | `rungic-converge apply` 的第一步 |

名字统一用 `YYYYMMDD-简短说明`，例如 `20261008-keyboard-repair`。执行时按名字排序，跳过账本里已有的。日期前缀只用来排序，账本记的是完整名字。

规则：

1. **每条迁移被打断后可以重跑。** 先做完、确认，再追加进账本。脚本中途失败就不写账本、停在这一条，后面的不执行，下次再试；日志写明哪条、为什么失败。
2. **迁移只往前，旧版本要能继续工作。** 回滚（`rollback`，或按包回滚）不会撤销迁移。所以迁移只能增加旧版本也能接受的东西，不能删掉旧版本还要读的东西。要删就分两次发版：先停用，确认没有回滚需要后再删。`rollback --snapshot` 会把容器的 rootfs 连同账本一起恢复，这种情况下两者天然一致。
3. **用户配置只用 KDE 自己的工具写**（kreadconfig6/kwriteconfig6），不用 QSettings。#78 的键盘配置就是被 QSettings 写坏的。kconf_update 的 Id 不再带发布号，靠名字唯一。
4. **现有账本不迁移。** `service-defaults` 和 `kconf_updaterc` 保持原样，继续有效。新的“一次性默认值”按上面的写法写成迁移。postinst 里那段服务默认值的代码留着，以后不再往 postinst 里加新的一次性逻辑。

### 测试

- `rungic-migrate` 的离线测试：按名字顺序执行；跳过已做的；中途失败不写账本、下次从这条重试；重复执行没有副作用；回滚后（账本里有、迁移文件已不存在）不报错。
- 一条规则测试：仓库里每个迁移文件的名字符合 `YYYYMMDD-…`、没有重名，并且容器系统范围的迁移由某个包的 `paths` 覆盖，保证它们真的会进发布。

## drift 的变化（P4 的前半部分）

P1 完成后，`rungic_release.py drift` 增加一个类别“Android 设置”：在手机上跑 `rungic-converge check`，把不是 `ok` 的项列出来。`report` 类的项（比如用户撤销了相机权限）单独列出，不算部署失败。部署的最后一步跑一次 drift，“Android 设置”里有 `refused` 时标为部署有问题。

## 分几步做

每一步一个 PR，各自有离线测试和真机验证：

1. `rungic-converge`（白名单、悬浮窗、Magisk/KernelSU 授权、目录）+ `rungic-runtime` 改为调用它 + 测试。G100 上按上面的方法验证。
2. firstboot 改为调用 `rungic-converge`，删掉自己的那几行。用一个干净安装验证首装仍然正常。
3. 部署后执行收敛；drift 增加“Android 设置”类别。
4. 投屏文件进发版清单（只更新已安装的）。实现时改为整包：投屏有编译出来的 `rungic-cast.jar` 和一份覆盖全部文件的 SHA256SUMS，只换脚本会让安装器判它损坏；所以 `dev` 编出 jar、和脚本一起打成投屏包随发布走，部署时用投屏自己的 `install.sh` 安装。
5. `rungic-migrate` + 两个账本 + 规则测试；把 #78 的键盘修复迁移作为第一个例子（它已经是 kconf_update，只需改名对齐规则）。

P3（宿主程序 `rungic-plasma-enter`、`rungic-lxc-enter` 带 sha256 进发布清单）和 P4 的后半部分（每个发布自动列出各类变更）不在本设计里。

## 决定（2026-10-08）

1. **运行时权限**：Kevin 还没定。先按建议实现为只报告、不补；要改成每次开机补上，只需改 `rungic-converge` 里这一项。
2. **部署后立即执行收敛**：Kevin 同意。
3. **迁移用账本、不用整数版本号**：Kevin 同意。
