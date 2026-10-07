# 目标级电脑操作：typesafe-computer-use + 手机 GPU OCR

> 2026-09-27：APK的OCR分三种构建（`RUNGIC_APK_OCR`，见[73篇](73-reduce-upstream-changes.md)）。默认`runtime`：LiteRT运行库与`librungicocr.so`（去掉调试信息后0.9MB）在APK中，APK约8.3MB；PP-OCRv6模型（约77MB）在首次OCR请求时由APK在后台下载，按`assets/ocr/sources.json`固定的URL与SHA-256校验，经Linux侧请求中的代理（`https_proxy`）下载；下载完成前请求立即返回错误，`linux_ocr.py`改用CPU上的RapidOCR。`bundle`把模型一起打包（约85MB），`none`不含OCR（约4.2MB）。

> 改名说明（2026-09-26）：Rungic改名B阶段之后，容器内的`moto-*`包、程序、单元、路径，`MOTO_*`变量和`dev.moto.*`名称改为`rungic-*`、`RUNGIC_*`、`com.rungic.*`；Android侧的名称在C阶段（2026-09-27）改为APK `com.rungic.plasma`、`/data/adb/rungic-*`（镜像在`/data/adb/rungic-lxc/images/`）、容器中的`/var/lib/rungic-{host,cores,apt}`、`rungic-gpu-alloc`、`rungic-cast`、`debug.rungic.*`、dm `rungic-root`与SELinux `rungic_image`。对照与边界见[70篇](70-rungic-rebrand.md)。下文按时间记录的内容保留当时的名称。

> 当前默认（2026-10-03）：由 Codex 使用 Agent 所选模型和当前登录，看截图并逐步执行桌面操作，见 [106 篇](106-codex-desktop-operation.md)。本篇保留 AT-SPI/OCR/JEV 备用方案的历史实现与验收；选择 `rungic-cua plan atspi` 并重启 MCP 后使用。独立 Luna API 方案见 [68 篇](68-luna-computer-use.md)，可在 Agent App「设置 → 桌面操作」选择。

2026-09-25。用户要求：整个电脑操作过程（任务流程与每一步点什么）都由 JEV 决定，参考 [awlevin/typesafe-computer-use](https://github.com/awlevin/typesafe-computer-use)。60 篇的做法是 Codex 规划子任务、JEV 只在子任务内选控件；本篇改为把整个目标交给 JEV，逐步决策。

## 选型与来源

| 组件 | 版本/来源 | 许可证 | 用途 |
|---|---|---|---|
| typesafe-computer-use | `24eb2924`（0.2.0），原样导入 `vendor/typesafe-computer-use`，随后单独提交本地修改 | MIT | 每步：截图 → OCR + 无障碍控件 → JEV 选择动作（kind/item/site/offscreen）→ 执行；写手模型只负责要输入的文字和停止时的回答 |
| typesafe-sdk / openai / anthropic | 0.6.0 / 2.54.0 / 1.6.0（pip，装在 `/usr/local/lib/moto-clicker/venv`，`--system-site-packages`） | 各自许可 | JEV 客户端；写手走 OpenAI Chat Completions |
| 写手模型 | `gpt-6-luna`（OpenAI API，经代理） | 服务 | 输入文字、停止后的回答与追问 |
| LiteRT | 2.2.0：`vendor/litert` 仅 C API 头文件（`145c752`）；运行库取自 Maven AAR | Apache-2.0 | APK 内 GPU 推理 |
| PP-OCRv6 Small（LiteRT 版） | `litert-community/PP-OCRv6-Small-LiteRT` `ce3822e0`：det 640、rec 320/640/960、18,710 类 | Apache-2.0 | 文字检测与识别；下载地址与 SHA-256 见 `provenance/ocr-20260925/sources.json` |

## 架构

```
语音助手 Codex ──MCP desktop_goal(goal, app, replies)──► moto-cua ──► moto-clicker run（venv）
   typesafe_computer_use.runner ── 每步 ──► linux.py（本仓库新增的平台适配器）
      截图   moto-screenshot（C，KWin ScreenShot2，约0.1 s）
      窗口   KWin 脚本（moto_cua.kwin），输出几何与缩放
      控件   AT-SPI 树（moto_cua.a11y），每次截图读一次
      输入   RemoteDesktop 门户（指针/按键）+ KWin commitText（文字）
      OCR    linux_ocr.py ──platform.sock {"op":"ocr"}──► APK OcrBridge ──JNI──► libmotoocr.so（LiteRT GPU）
   JEV（api.typesafe.ai）逐步选动作；写手（gpt-6-luna）写文字/回答
```

- **显示坐标**：“显示器”是活动窗口所在的输出；屏幕点是该输出的逻辑坐标，截图按原生像素，缩放比即输出的设备像素比。
- **文字兼容路径（2026-10-07）**：正常使用 KWin `commitText`。只有实际 UnknownMethod 才退回已有 ASCII keysym 输入，保留原文字的大小写和符号；其他 D-Bus 错误直接报错。该 TypeSafe 入口没有目标控件的 EditableText 引用，因此缺少 `commitText` 时非 ASCII 文字明确失败；不会静默略过。AT-SPI backend 有实际控件引用时仍使用 EditableText 输入中文。原生 ARM64 测试验证实际文本框读回；Android 键盘和手机呈现仍需单独验收。
- **点击**：无障碍元素的 press 也用真实指针点击当前位置（Qt/微信对指针事件更可靠），只有没有位置的元素才用 AT-SPI 动作。
- **追问**：写手需要用户信息时，本轮以 `outcome: "question"` 结束，由语音助手问用户，再带 `replies` 重新调用。对话只由语音助手持有。
- **中止**：创建 `$XDG_RUNTIME_DIR/moto-clicker/abort`（`moto-clicker stop`），代替上游的“鼠标甩到左上角”。
- **测试隔离**：Linux 适配器仅在 `CLICKER_PLATFORM=linux` 时启用（启动器设置）；上游 `tests/conftest.py` 清掉该变量，测试永远不会操作本机。

### 对上游的本地修改
- `linux.py`、`linux_ocr.py`（新增）；`platform_adapter.py` 的选择逻辑；`tests/conftest.py` 的隔离。
- AT-SPI 适配细节：
  - Qt Quick 的无名 filler 常与父节点同框，上游按（角色、名称、框）去重会把整棵子树当重复跳过。无名容器改为不报告框。
  - 焦点：容器也带 FOCUSED，优先取文本控件，否则取最深的焦点节点。之前误报为“无焦点”导致 JEV 点击搜索框后被判为停滞。

## 截图：moto-screenshot

KWin 只把 ScreenShot2 授权给 `.desktop` 中声明 `X-KDE-DBUS-Restricted-Interfaces` 的可执行文件（按 `/proc/<pid>/exe` 匹配）。给 python3 授权等于授权所有脚本，因此单独写了一个小 C 程序（`plasma/cua/screenshot/`），输出 JSON 头 + 原始像素。实测整屏约 0.1 s（spectacle 约 0.7 s）。

## OCR：APK 内 GPU

调研与实测（本机 SM6435 / Adreno 710，Android 16）：
- 本机 Hexagon CDSP 在设备树中关闭，QNN HTP 与 NPU 路线不可用；无 NNAPI HAL。可用的加速器只有 Adreno 710（OpenCL）。
- 容器 CPU：RapidOCR + onnxruntime（PP-OCRv5 mobile）整屏约 2.0 s；Ubuntu 与 pip 版 onnxruntime 相同，限制 det 尺寸无改善；CPU 亲和性为 0–7，未被 Android cpuset 限制。
- LiteRT GPU（CompiledModel，C API 经 JNI）：
  - PP-OCRv5（社区固定形状）：det 640² 146 ms，rec 48×320 每行 37 ms；XNNPACK CPU 的 rec 76 ms。模型形状固定，不能 resize（上采样的尺寸被写死）。
  - **PP-OCRv6 Small**（采用）：

    | GPU 精度 | det 640² | rec 320/640/960 | 1080×2400 整屏（det 缩放0.5，2块） |
    |---|---|---|---|
    | fp32 | 129 ms | 21/38/56 ms | ~1.0 s |
    | fp16（默认） | 57 ms | 11.5/21/30 ms | ~0.5 s（含 7.7 MB 像素传输） |

    两种精度在测试截图上识别文字相同。模型卡称默认精度下 37 个张量用例只过 27 个、显式 FP32 全过；请求可带 `"fp32": true` 切换。
- 用户确认 PP-OCRv6 Small 速度已足够，不再做 Tiny、批处理等进一步优化。

实现（`plasma/native-apk/jni/ocr/`）：
- 预处理按 PaddleOCR：BGR 平面，det 用 ImageNet 均值方差，rec 用 (v−127.5)/127.5，右侧补归一化零。
- det 固定 640²：大图按 `det_scale`（每逻辑点约 1.5 像素）缩放后切成重叠 1/8 的块，各块概率图取最大值拼回整图，再只做一次连通域，块缝不会切断文字行。
- DB 后处理：概率 0.2、框分 0.45、unclip 1.4，轴对齐框（屏幕文字水平）。
- rec 选能放下自然宽度的最窄模型；超过 960 的行在最空的列处切开分段识别，不压缩。
- GPU 设置经 opaque options（TOML）传入：精度与编译程序缓存（首次约 5 s，之后 0.5 s）。
- `OcrBridge`：平台桥 `{"op":"ocr","width","height","format":"rgb","bytes","det_scale"}` + 原始像素，单独线程读取，GPU 引擎常驻单线程；首次使用时从 APK assets 解出模型。
- 构建：`plasma/build-apk.sh` 调 `tools/fetch_ocr_assets.py` 校验下载，用 NDK 编 `libmotoocr.so`；`.tflite` 不压缩打包。APK 1.37 约 86 MB。

## 实机结果（2026-09-25）

- 系统设置目标（电视上，“找到关于此系统并读出 Plasma 版本”）：
  - 第一次：焦点误报，JEV 在未聚焦的地方想输入，随后在错误的窗格滚动，到步数上限。修复焦点后，JEV 自己点击搜索框、输入“About this System”、回车。
  - 目标本身不可达：本机没有 kinfocenter（移动版为 kcm_mobile_info），且电视中途掉线（`Display Removed`，自动重连中）。
- 每步约 2–4 s：截图 0.15 s、控件树 0.3–0.7 s、OCR 0.3–1.2 s、JEV 0.4–1.4 s。
- 写手生成输入文字约 7–8 s，待改（OpenAI 适配器丢弃了上游的“关闭思考”）。

## 待办
- 写手延迟；滚动目标（上游固定滚动窗口中心，多窗格应用会滚错区域）。
- 助理屏（65 篇）：Agent 在按需开启的虚拟屏上工作，可与投屏无缝互转。
- 更换真实目标（微信文件传输助手）验收；更新语音助手技能与提示词，改用 `desktop_goal`。
