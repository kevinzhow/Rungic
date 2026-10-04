# 功能清单与质量治理

`quality/` 记录 Rungic 的功能、体验要求、已知问题，以及对应的代码、文档和检查。
一条功能描述用户能感知的一件事。

[docs/feature-inventory.md](../docs/feature-inventory.md) 是自动生成的总览。不要直接修改它。
修改 `quality/` 数据后，运行：

```sh
python3 tools/feature_inventory.py render --write
```

## 用途

- **查找功能**：每条功能列出代码、文档、检查和已知问题。改动前用 `feature ID` 查看这些信息。用 `owner PATH` 查文件归属。
- **检查体验**：每条体验要求都要有检查。测试声明自己检查的体验编号。清单检查会发现测试改名或删除造成的覆盖缺失。
- **确定清理范围**：每个跟踪文件都要有功能认领。每篇文档都要分类。`report` 列出无主文件、退役功能的残留和被取代的文档，供删除或重构时参考。

这套做法参考三种已有方法：

- Android CDD/CTS：要求有编号，测试声明对应的要求。
- Linux `MAINTAINERS`：记录路径归属。
- 故事地图：按用户场景组织功能。

## 数据文件

| 文件 | 内容 |
|---|---|
| `features/<领域>.yaml` | 该领域的用户场景、功能和体验要求 |
| `interfaces.yaml` | Linux 系统功能使用的 Android 接口 |
| `contracts/` | 接口查询及使用方依赖的回复字段 |
| `docs.yaml` | 文档分类 |
| `baseline.json` | 现有测试欠账的基线 |

### 功能记录

```yaml
- id: desktop-mode.fullscreen        # 稳定的英文编号：领域.名字
  title: 桌面模式全屏                 # 用户能说出的功能名称
  scenario: desktop-mode.use         # 所属用户场景
  status: live                       # live 在用 | experimental 实验 | retired 已退役
  platform: linux                    # linux 与 Android 无关 | android 依赖 Android
  interfaces: [platform-bridge]      # 使用的 Android 接口，定义在 interfaces.yaml
  summary: 一句话说明用户得到什么。
  experience:                        # 每条体验写一个可检查的要求
    - id: E1
      text: 进出全屏没有空白帧。退出后浮窗回到原位。
      evidence:                      # 人工验证的文档位置和日期，超过 90 天算过期
        - {doc: docs/research/97-headless-agent-work.md, date: 2026-10-03, note: §21.6}
      gap: 只能人眼判断，等待录屏比对工具   # 可选：说明暂时无法检查的原因
      device: 帧率取决于手机 GPU 和 Android 刷新   # 可选：说明系统测试无法替代的原因
  pitfalls:                          # 已知问题、限制和容易误判的情况
    - text: 手机上全屏窗口的第一帧要晚约 0.6 秒。
      docs: [docs/research/97-headless-agent-work.md]
  code: [agent/screen/qml/Main.qml, agent/screen/]   # 认领的路径，目录以 / 结尾，支持 ** 通配
  docs: [docs/research/97-headless-agent-work.md]    # 说明该功能的文档
```

功能记录按以下规则编写：

- 一条功能描述一件用户能感知的事。例如“桌面模式全屏”或“授权框出现在桌面里”。不要用模块、服务或重构代替用户功能。
- 体验描述用户看到的结果，并且必须能判断是否符合要求。例如“退出后浮窗回到原来的位置和大小”。不要只写“全屏逻辑正确”。性能、恢复和失败时的表现也属于体验。
- 打包、发布、诊断和补丁队列等工程能力放在“交付与运维”领域。这些功能的用户是开发者和 Agent。
- 保留退役功能的记录，并设置 `status: retired`。删除它的代码和文档。`report` 会列出尚未删除的内容。
- 确实需要保留退役功能的文件时，写 `keep: 保留理由`。例如 AGENTS.md 要求保留的历史或恢复用整包工具。有 `keep` 的内容不计为待清理项。

### 声明检查范围

在测试实际检查体验的位置写 `covers` 注释。任何语言都可以使用，补丁里的测试也使用同一规则。

```python
# covers: desktop-mode.fullscreen/E1            离线单元测试，默认层级
# covers[system]: desktop-mode.fullscreen/E2    无头 Linux 系统测试，如 KWin --virtual
# covers[consumer]: iface:platform-bridge       Linux 使用方，对着替身检查契约
# covers[provider]: iface:platform-bridge       Android 提供方，在手机上检查契约
// covers[device]: desktop-mode.tv/E2           未列入 acceptance.json 的手机测试
```

实机验收在 `release/acceptance.json` 的场景中声明：

```json
"covers": ["display.size/E1", "iface:kwin-android-host"]
```

只有检查确实验证了对应体验时，才声明覆盖它。

### 检查层级

多数功能属于 Linux 系统，使用 `platform: linux`。
这类功能应当能在没有 Android 的环境中测试，无论底层设备是 Android 手机还是 PC。

| 层级 | 运行位置 | 检查范围 |
|---|---|---|
| 单元 | 本机离线，`tools/run-tests.sh` | 逻辑、协议和解析 |
| 系统 | Mac mini 的 ARM64 容器，`tools/system_test.py` 运行无头 `kwin_wayland --virtual` | 窗口、层级、D-Bus 和多个程序协作 |
| 契约 | Linux 使用方离线对着替身，Android 提供方在手机上 | Linux 功能与 Android 之间的每个接口 |
| 实机 | 手机，`tools/rungic_acceptance.py` 或人工检查 | 整体可用性、性能、时序和功耗 |

`report` 会列出以下欠账：

- `device-only`：Linux 功能只有手机检查，需要补系统测试。
- `one-sided-contract`：接口只检查了使用方或提供方，需要补另一方的检查。

某一方暂时无法检查时，在 `interfaces.yaml` 中写明原因：

```yaml
gaps: {consumer: 使用方暂时无法检查的原因}
# 或 gaps: {provider: 提供方暂时无法检查的原因}
```

与体验的 `gap` 一样，写明原因后，这一项不计为欠账。

Linux 功能也可能包含只有手机能验证的体验。
例如性能、帧率、时序、功耗、画质、音质，以及依赖 Android 或硬件的结果，如相机出画和 120 Hz 刷新。
在这类体验中写 `device: 原因`。原因必须说明系统测试为什么无法替代实机检查。
这些体验仍需实机验收或人工验证，但不计为 `device-only` 欠账。
能够独立检查的 Linux 逻辑仍需单元测试或系统测试。

### 文档分类

| `kind` | 含义 | 处理要求 |
|---|---|---|
| `reference` | 当前实现的说明 | 保持最新。必须有功能引用。 |
| `journal` | 调研、实现和实测的过程记录 | 必须有功能引用。将结论写入功能的体验和注意事项。 |
| `research` | 可复用的调研 | 必须有功能引用。 |
| `history` | 已结束的设备或路线记录 | 保留作证据。不要求功能引用，不再更新。 |
| `index` | 索引或总览 | 无额外处理要求。 |
| `superseded` | 已由其他文档取代的内容 | 用 `superseded_by` 指定替代文档。确认独有事实已迁移后删除。 |

## 检查与清理

```sh
python3 tools/feature_inventory.py check
python3 tools/feature_inventory.py check --strict
python3 tools/feature_inventory.py report
```

`check` 将断裂引用报为错误，将其余问题报为警告。
`report` 列出完整警告，说明需要补的检查、需要清理的文件和需要决定的问题。
`--strict` 按以下规则决定是否失败。
`tools/test_feature_inventory.py` 和 `tools/run-tests.sh` 使用这些规则。

**结构性警告必须清零。** 以下情况都会导致失败：

- 文件没有功能认领。
- 文档未分类，或需要功能引用却无人引用。
- 已被取代的文档尚未删除。
- 退役功能仍有代码，且没有 `keep` 理由。
- 接口没有功能使用。

新增文件时必须写功能认领。新增功能时必须写体验要求。

**测试欠账只许减少。** 欠账类型包括 `untested`、`device-only`、`one-sided-contract` 和 `stale-evidence`。
现有欠账记在 `quality/baseline.json`。新增欠账会导致检查失败，但 `stale-evidence` 按下面的日历规则处理。
补上测试后，运行以下命令收紧基线：

```sh
python3 tools/feature_inventory.py check --update-baseline
```

基线扩大时，diff 会显示新增项，必须说明理由。
人工验证超过 90 天时，`report` 提示 `stale-evidence`。测试不会仅因日期变化而失败。
重新验证后，更新 `evidence` 的日期。

`tools/run-tests.sh` 还检查两类引用：

- `tools/tests/test_acceptance_scenarios.py` 检查 `release/acceptance.json` 中每个场景是否有对应检查函数。
- `tools/pq.py lint` 检查补丁头 `X-Rungic-Tests` 中的 L3 场景是否存在。尚未编写的场景标为 `(to write)`。

删除代码或文档前，依次检查：

1. 用 `owner PATH` 确认文件归属。
2. 用 `git grep` 确认没有引用。
3. 核对构建输入，包括 `packaging/*/package.json` 的 `paths` 和配方的 `overlay`。

每项清理单独提交，并说明依据。

## 手机检查与用户状态

提供方契约检查和其他实机检查运行在用户的日常机上。检查必须保留用户正在使用的状态。

2026-10-03，`desktop-mode.workspace` 的第一版检查为验证开关而先关闭、再打开桌面模式。
输出格式与检查预期不同，检查将状态读成空值，关闭了用户正在使用的工作区 0，并要求应用退出。
随后用 `rungic-desktop-mode on` 恢复。浮窗进程未受影响。

手机检查遵守以下规则：

- 根据实际运行的部件判断状态。无法理解状态时，报告失败，不执行切换。
- 桌面模式正在使用时，只读取和核对状态。
- 只有桌面模式原本关闭时，才允许检查开关往返。检查结束后恢复原状态。
- 开关往返会短暂显示手机上的桌面浮窗，因此只属于 `full` 验收。部署后的冒烟验收不执行这个往返。
- 新增实机检查时，先运行只读步骤。确认实际读到的状态后，再加入改变状态的步骤。
