# Android SIM 短信

## 范围与来源

2026-10-04，按用户要求新分支实现安卓短信，经 Linux 命令向 10000 发测试短信并检查回复。Opus 提供初稿 8d04c20，mibook 接手实现与 USB G100 验收；不更改默认短信应用，不删除、标记或改写已有消息。

使用 Android 官方 [SmsManager](https://developer.android.com/reference/android/telephony/SmsManager) 的短信分段、发送回调与送达 PDU，以及 [Telephony.Sms](https://developer.android.com/reference/android/provider/Telephony.Sms) 内容提供者。保留平台桥 UID 0/1000 校验；耗时发送在独立线程，主 UI 与其他请求不等待无线电。SEND_SMS/READ_SMS 是受限权限，需要安装允许及运行时授权；应用已有 root 授权路径按需授予并复核，失败明确返回。没有新增 RECEIVE_SMS 权限，收件通过系统短信库读取。

## 接口

- `op: sms, action: send, to, text`，可选 `subscription`；默认活动短信卡，无默认且只有一张活动卡时使用它，否则明确报错。最多 1000 Unicode 字符，以 SmsManager.divideMessage 分段。
- `status: sent|failed|pending`、`parts`、`sentParts`、`subscription`、`submittedAt`。全部分段无线电成功才是 sent；失败或缺少回报不自动重发。部分成功可伴随失败。
- `delivery: delivered|failed|pending|unconfirmed`；根据回执 format 区分 3GPP 与 3GPP2，只接受实际 status-report PDU。只有每个分段收到已送达状态才给 `delivered: true`。受理、暂存、仅收到广播、缺失/未知格式、网络不提供回执或失败回执不能冒充送达。
- `action: list, box: inbox|sent|all, from?, since?(毫秒), limit`；短号严格匹配，长号码允许国际前缀，按时间倒序读取。结果 `messages` 与 `truncated`；扫描最多 2000 行，达到结果条数上限时再查一个匹配行，确有省略才标 truncated；扫描上限后的记录无法检查时也标 truncated。空 cursor 报错，超限不可宣称完整无消息。读取不写短信库。
- `rungic-sms send 10000 "查询话费"`；用返回的 submittedAt 执行 `rungic-sms list --from 10000 --after <submittedAt> --wait 60`。绝对时间避免漏掉发送接口返回前已收到的回复；等待超时退出 1 并给 timedOut。普通查询无消息退出 0；发送失败/未决退出 1，参数或接口错误退出 2。

平台提供者验收只读查询，不会发短信；消息内容在契约检查报告中整体隐藏。实机发送另行人工授权，本次目标仅 10000，内容为查询，不办理业务。电信 [官方短信营业厅说明](https://m.gd.189.cn/gd/sms/) 列出的常规短信指令目标为 10001；本次仍遵循用户指定的 10000，未获回复时不宣称短信营业厅完整验收。

## 回归与实机记录

### 离线与 Android 运行时

- 短信状态、分段失败、重复回调、实际送达状态码、号码/Unicode 校验、查询时间窗、无回复退出码、权限错误及不自动重试：15 个针对性测试通过。连同文档索引与既有 APK 准备门槛契约，22 项、15 个子测试通过。
- APK 全量 Java/资源编译、签名校验通过；既有 FirstBootState/ControlException Java 回归通过。
- 全量离线首次为 899 passed、16 failed、3 errors、6 skipped、509 subtests，随后 Qt/Python 退出崩溃；新增文档未列入索引的失败已修复且单独复测通过。其余 15 个失败和 3 个 Java fixture 错误全部在原始 main df49e28c 的全量对照中复现（884 passed、16 failed、3 errors、508 subtests；对照另一个失败是未建本地 cache 目录，建目录后单测通过）。因此不声称全量通过；Python/JDK 容器启动环境、媒体编码和已有接口测试问题仍在。首次 runner 的独立 javac 还受容器工作目录影响，修正启动环境后实际 Java 测试通过。
- 清单严格检查：161 个功能，0 错误，44 个既有 device-only 提示；补齐 docs 索引，差异空白检查通过。

首次真实短信发送确实成功，但 CLI 等不到回调而返回 pending。根因是每个 PendingIntent 的 Intent 带 `rungic-sms://...` URI，而原过滤器只声明 action、没有对应 data scheme。Android 官方 [IntentFilter](https://developer.android.com/reference/android/content/IntentFilter) 的数据匹配规则要求双方一致。修复提取为生产 SmsIntents，过滤器声明 scheme；保留应用私有 PendingIntent 与 RECEIVER_NOT_EXPORTED，不开放外部回调入口。sentAt 记录无线电回报结束的时间，不包含随后等待送达的时间。

`android/test-sms-intents.sh` 构建生产 SmsIntents 和 SmsIntentDriver，经指定手机的 app_process 使用真实 Android IntentFilter；不调用 SmsManager，不读写短信库。回归确认旧缺少 scheme 的过滤器返回 NO_MATCH_DATA，修复后发送/送达 Intent 匹配，并检查分段、请求隔离。本次 Android 16 实机通过。可先在 APK 构建容器内使用 `RUNGIC_ANDROID_JAR`、`RUNGIC_ANDROID_BUILD_TOOLS` 运行 `bash android/test-sms-intents.sh --build-only`，再在宿主执行：

```sh
RUNGIC_SERIAL=ZY32M9MRVP bash android/test-sms-intents.sh --run-only
```

### USB G100 真实短信

- 设备 ZY32M9MRVP，Android 16，中国电信，活动 subscription 1；原 APK 2.30/78，安装 2.31/79，保留数据。修复后的 APK SHA-256：`54503fa3bf03891f864672743bf6ec5b4c0855e1e002ea2ca1dad77ceabd6ee3`。
- **首轮只发了一条**：10000，内容“查询话费”，submittedAt=1791081791665。初版 CLI 返回 pending/sentParts=0，但系统发件箱 id 5、date=1791081792193 证明已发出；10000 的对应新收件 id 6、date=1791081795918 是电信话费查询链接。未打开链接、未办理业务、未改发 10001。
- 修复后重新安装 APK，再用原 submittedAt 查询：回复仍可检出，read=false 保持未读，默认短信应用仍为 com.android.messaging。首轮当时没有重发短信，未复测真实无线电回调。后续 review 修正与第二次实测见下节；运营商实际送达 PDU、多 SIM 与真实长短信分段仍未确认，不能将离线或合成 PDU 回归冒充运营商实测。
- 空时间窗 `--wait 0` 返回 timedOut/退出 1；无效 subscription -1 明确拒绝，未提交第二条短信。telephony 提供者 3 个只读查询全部通过，检查不发送短信，私有消息字段被隐藏。

### 开发覆盖与恢复

使用项目 rungic-dev-release 技能（`.agents/skills/rungic-dev-release/SKILL.md`），没有发布新 rootfs、提交旧快照或操作日常 G100 S。

- 源码构建提交 797e431e；APK 后续回调过滤修复单独提交。Mac mini 原生构建两个包：rungic-plasma-bridges `0.358+dev20261004t024128.797e431` 与 rungic-voice-agent `0.510+dev20261004t024128.797e431`。
- 官方开发覆盖 `20260930.19+dev20261004t024128`，基线 20260930.19，39 个原有覆盖保留；apt Installed=Candidate 全部通过，release_mismatch=0。完整性仍有部署前既有的 305 个 missing，changed_files=0，没有新增缺失。
- 独立 worktree 首次构建后因本地包池缺少旧覆盖元数据而在安装前中止；从原工作区复制已有包池记录后经同一官方工具完成安装，没有手工 dpkg 安装或抹去旧覆盖。
- 新 CLI 与 phone skill 的实机 SHA 分别与源码相同。先核实通话 idle、无活动 Agent 任务，再重启网络/蓝牙/蜂窝桥及 voice-agent/overlay；APK 重装后启动 MainActivity，KWin、Plasma 和上述服务均 active。默认短信角色与应用数据保留，原 APK 已备份。

产物、日志和私有回复保存在 `.work/verify/20261004-android-sms/`，不进入源码；开发部署详细记录在此工作树 `.work/dev-deploy/20261004-104128-deploy/`。


## PR #11 review 修正与电信卡复测

2026-10-04，Opus 指出 CDMA 回执按 GSM 区间解释会把成功状态 0x20000 判成失败；GitHub 自动 review 同时指出查询达到 limit 后可能错误返回 truncated=false。两项均修正，功能继续标为 experimental。

### 状态来源与回归

Android 官方 [SmsMessage.getStatus](https://developer.android.com/reference/android/telephony/SmsMessage#getStatus()) 明确区分 GSM 低位状态与 CDMA 高 16 位状态。核对 Android 16 的 AOSP [cdma/SmsMessage.java](https://github.com/aosp-mirror/platform_frameworks_base/blob/android16-release/telephony/java/com/android/internal/telephony/cdma/SmsMessage.java) 与 [BearerData.java](https://github.com/aosp-mirror/platform_frameworks_base/blob/android16-release/telephony/java/com/android/internal/telephony/cdma/sms/BearerData.java)：3GPP2 先右移 16 位再分别读 errorClass 和 messageStatus；errorClass=0/code=2 才是送达，code=0/1 是受理/暂存，code=3 是取消；临时错误等待、永久错误失败。保留值、缺失 format 与非法布局保持未确认。Android 16 对不带状态字段的 DELIVERY_ACK 已在内部正规化为 DELIVERED；普通收到的 SMS 不能代替 status-report。

- SmsStateDriver 增加 GSM/CDMA 格式、成功/受理/暂存/取消/临时/永久/未知状态及有界查询额外匹配行回归，22 项针对性测试与 15 个子测试通过。
- SmsReceiptDriver 构造不涉及 SIM 的 3GPP2 DELIVERY_ACK PDU，在真实 Android 16 调用 SmsMessage.createFromPdu 解码，再检查生产 SmsState 的状态解释；7 个状态组合及普通消息非回执边界通过。现有 android/test-sms-intents.sh 同时执行过滤和 PDU 驱动，两项均通过，临时 dex 执行后删除。
- APK 2.32/80 全量编译、签名校验通过；清单严格检查 0 错误、44 个既有提示，差异空白/脚本语法通过。未因该局部修正重复全量离线套件；已知全量问题仍见前节对照。

### 第二次真实短信

沿用用户授权的同一测试目标和查询内容，先告知复测，再核实 SIM、无活动任务、通话 idle 后安装 APK 2.32/80 并启动桌面。APK SHA-256：`288eac00c2b6e72e9b2791a9587a8e1e240daeaaef62e77a5baaf60faf35a597`。Linux 开发包、覆盖、rootfs 快照均未改动。

- 向 10000 发送第二条“查询话费”，submittedAt=1791083597801；CLI 退出 0，status=sent、parts=1、sentParts=1、sentAt=1791083598228，真实发送回调验证通过，不再误报 pending。此复测不是未知发送结果后的自动重试。
- 15 秒送达等待结束时 delivery=unconfirmed、delivered=false，没有确认运营商实际送达回执。系统发件记录 id 7/date=1791083598169；10000 回复 id 8/date=1791083601037。客服回复是独立证据，不用它伪造送达 PDU。
- 从首次发送时间查询同一短号：limit=1 返回一条且 truncated=true，limit=100 返回两条且 truncated=false，截断修正在实际短信库验证通过。两次查询回复均保持未读，默认短信应用仍为 com.android.messaging；三个只读 telephony 契约查询通过，KWin、Plasma 与 Agent/overlay 服务 active。
- 本轮未新增并发/频率策略；短信请求线程和授权策略的非阻塞建议留待单独设计。未发起第三条短信、没有切换号码或打开查询链接。完整私有回复和日志仅位于 `.work/verify/20261004-android-sms/cdma-review/`。


### 同步最新 main

review 修正提交 16f6098e 后，main 同时合入 DNS 跟随修正 19a10f37 与 Agent 电话界面 dbf2ab6c；PR 的冲突仅在 docs/feature-inventory.md。合入最新 main，保留双方质量定义并重新生成清单，严格检查仍为 0 错误、44 个既有提示。Android 目录与 16f6098e 完全相同，本次不重装 APK、不再发送短信，也不把上游新 Linux 包自动装到设备。

同步后的短信、文档、APK 准备门槛、DNS 与电话界面合并检查最初为 38 passed/1 failed/29 subtests；失败是宿主 Python 3.15 的 concurrent.futures.ThreadPoolExecutor 延迟导入对象不可调用，在原样最新 main dbf2ab6c 对照中同样为 23 passed/1 failed/29 subtests。测试进程启动时显式导入 ThreadPoolExecutor 后，同一组 39 项、29 子测试全部通过；没有修改生产 DNS 代码来规避它。本节不将旧 df49e28c 的全量对照结果误作最新 main 的全量验收，最新同步只执行上述相关检查。
