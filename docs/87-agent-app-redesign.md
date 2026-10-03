# Agent 助手第三版：聊天式界面与独立的设计系统库（2026-09-29）

> 2026-10-03 设置功能更新：「桌面操作」提供 Codex（默认）与 Luna/OpenAI API 选择；「登录方式」仍单独控制 Codex 的 ChatGPT/API key 登录，OpenAI API key 独立成组。进行中的任务或通话会阻止切换执行器。新页面四种状态已离线渲染，切换与忙时拒绝已在开发 G100 经服务接口验证；完整页面触控流程未在本轮验收。当前实现和边界见 [106 篇](106-codex-desktop-operation.md)，登录页见 [101 篇](101-codex-sign-in-and-api-key.md)。下文保留界面改造历史。

用户要求按 Claude Design 画布“Agent · App 设计”（浅色、深色各 23 张）重新设计并实现 Agent 助手，并且要求把设计系统抽成一个独立的库，作为单独的包，可以 import。实现过程中用户又指出，问题多出在控件的状态上（按下、激活时缺图标等），要求按“状态”来设计和实现控件。

## 设计系统库：`com.rungic.design`（包 `rungic-design`）

- 位置：`desktop/design/`（原记为 `plasma/design/`，目录已迁移）。它是一个 QML 模块（一个插件库），安装到 Qt 的 QML 目录（`/usr/lib/aarch64-linux-gnu/qt6/qml/com/rungic/design/`），任何应用都可以 `import com.rungic.design`。
- 助手应用只在运行时导入它，构建时不链接。原因：打包工具的 `build_depends` 只从 Ubuntu 软件源安装，装不了本项目自己的包。
- 应用的 C++ 部分需要知道深浅色时，通过 `engine.singletonInstance("com.rungic.design", "Theme")` 取 `dark` 属性，并按名字连接它的信号。
- 内容：
  - **`Theme`**：设计参数（颜色两套、字号、尺寸、圆角、动效），取自设计稿的 `chat.css`。
    - `mode` 为 system、light 或 dark。应用设置里的“主题”会写入它。
  - **`SystemTheme`**：从助手应用迁来的 C++ 单例，读 `kdeglobals` 判断深浅。
  - **图标**：`Icon` 加上 31 个线条图标。
    - 源文件是 `icons/*.svg`，由 `make-icons.py` 生成 `qml/icons.js`。
    - 绘制时把颜色写进 SVG，再作为 data URL 交给 `Image`；透明度走 `opacity`。
  - **组件**：IconButton、CircleButton、PillButton、PrimaryButton、SecondaryButton、NavItem、ListGroup、ListRow、Toggle、RadioMark、SecretField、Note、Progress、BusyRing、ShineText、Wave、VoiceBar、TextBar、HoldTarget、MetaButton、Tile、UserBubble、SearchField、SideDrawer、BottomSheet、Scrim。
  - **状态总览**：`Gallery` 页，以及程序 `rungic-design-gallery [--theme light|dark] [--section A,B]`。

### 控件按状态实现

- 每个交互控件都写明自己有哪些状态，并在 QML 的 `states` 里集中给出每个状态的底色、文字色和图标色，不再在各处散写 `down ? … : …`。
- 每个控件都有 `visualState`（当前状态）和 `forcedState`（强制状态）。状态总览页用 `forcedState` 把所有控件的所有状态并排画出，深浅各一遍。
- 各控件的状态：

  | 控件 | 状态 |
  |---|---|
  | IconButton、PillButton、SecondaryButton、Tile | normal、pressed、checked、disabled |
  | CircleButton | normal、pressed、disabled |
  | PrimaryButton | normal、pressed、disabled、busy |
  | NavItem | normal、pressed、current、disabled |
  | ListRow | normal、pressed、disabled |
  | Toggle | off、on，各自带 pressed 和 disabled |
  | RadioMark | off、on，以及 disabled |
  | MetaButton | collapsed、expanded、pressed |
  | HoldTarget | idle、active |
  | VoiceBar | idle、pressed、hot、cancel、handsFree、disabled |
  | SecretField | normal、focused、error、disabled |

- **状态总览在实机上查出的问题（均已修复并复查）**：
  1. **强调色底上的图标看不见**：`Theme.onStrong` 一直是黑色，黑色圆形按钮上的图标因此看不见。原因是 QML 把 `on` 加大写字母开头的名字当成信号处理器，这个属性从未被赋值。已改名为 `strongInk`。这正是“按下/激活时缺图标”的根源。
  2. **白色和半透明的图标失效**：`Kirigami.Icon` 的遮罩着色对纯白和半透明颜色不可靠，禁用状态不变淡，白色图标变黑。改为把颜色直接写进 SVG。
  3. **按下背景露出直角**：列表行按下时是直角，盖在圆角列表组上露出直角。现在列表组给首行和末行标记位置，它们的按下背景带上对应的圆角；行的显示或隐藏变化后会重新标记。
  4. **禁用状态有漏项**：禁用的列表行里，状态圆点仍是鲜色；禁用的密钥输入框里，显示按钮仍是实色。
  5. **开关圆钮看不清**：关闭状态的白色圆钮在浅灰轨道上几乎看不见，已按设计补上细边。
- **输入栏同样按状态写**：`Composer` 同一时间只处于 voice、busy、unavailable、hold、cancel、toText、transcribing、handsFree、keyboard、attach 其中之一，各部分的显示、提示文字、键盘弹出都写在它的 `states` 里。
  - 这样修掉了“附件面板和键盘同时出现”“空对话提示与输入栏重叠”两个组合问题。
  - 用户要求打开附件面板时收起键盘：`attach` 状态先收起键盘，面板占据键盘原来的位置。
- **浮层同样按视图写**：浮层同一时间只处于 listen、sending、work、answer 之一。
  - 修掉的问题：取消聆听后误显示“正在处理”。原因是服务先发 listen-cancelled、再发状态变化，浮层把后者当成了“说完等回复”。
  - 松开后、转写回来之前，是单独的 sending 视图，显示“正在识别…”。

## 设计系统的层级与基础画板（2026-09-30）

- **起因**：用户问 Colors、Typography、Dimensions、Icons、Components 是否都仔细设计过。核对后发现没有：
  - 画布“Agent · App 设计”原来只有 23 个页面画板（浅色、深色各一份）和共用的 `chat.css`，没有基础画板，也没有接入设计系统。
  - 颜色：浅色 `faint` 在 fill 上对比度只有 2.8:1；关闭的开关，轨道和底色对比约 1.2:1；深色 `negative` 在 fill 上 4.4:1，不到 4.5:1。
  - 文字与尺寸：画板里用了 9 种字号、11 种行高、20 多种间距、14 种圆角；图标线宽有 1.5 到 2.4 六种。
  - 组件：画布里只画了按下状态，没有禁用和焦点状态。各状态是后来在 QML 的 Gallery 里补上的。
- **用户要求**：补齐基础画板，并把整理结果回写 Theme.qml。
- **Theme.qml 的层级**（画板直接从这个文件读取数值，改动时两边一起改）：
  - **颜色**：每个文字色在 background、side、fill、fill2 四种底色上都不低于 4.5:1；`faint` 不低于 3:1，只作标记，不作文字。
    - 调整的值：浅色 `dim` #5f6b77 → #5e6975，`link` #2272a8 → #206da3，`positive` #1b7a44 → #1b7743，`faint` #8a939c → #7a838b；深色 `negative` #e0606d → #ec7480。
    - 新增 `negativeInk`（危险底上的图标）：浅色白，深色 #141618，替换 HoldTarget 里写死的 `#ffffff`。深色下，白色在新的 `negative` 上只有 2.9:1。
  - **文字**：7 个字号，各配固定行高：24/32、20/30、16/26、15/22、14/20、13/18、12/16，另有等宽 12/18。字重只用 400、500、600。新增 `*Line`、`calloutSize` 和 `weight*`。
  - **间距**：2、4、8、12、16、20、24、32、48（`spaceXxs` 到 `space4xl`）。`inset` = (field − touch) / 2 = 6，不算一级。
  - **圆角**：2、8、12、16、20，胶囊和圆形取高度的一半。
    - `radiusItem` 10 → 12，`radiusInput` 14 → 12。
    - TextBar 26 → `radiusField` 28。
  - **尺寸**：`controlCompact` 32、`controlS` 36、`touch` 44、`controlM` 48、`controlL` 52、`field` 56、`fieldHot` 64、`tile` 84。
  - **图标**：14、16、18、20、22、26、32（`iconXs` 到 `iconHero`）；线宽一律 1.7。
  - **状态与动效**：`disabledOpacity` 0.4、`pressScale` 0.94、`pressScaleWide` 0.98、`pressShade` 0.18、`focusWidth` 与 `focusGap` 均为 2；时长 100、150（新增 `brisk`）、200、250 ms。
- **随之改动的控件**：
  - Toggle：关闭时圆钮的边改为 `faint`（原为 14% 黑），时长用 `brisk`。
  - HoldTarget：图标色用 `negativeInk`。
  - VoiceBar：高度用 `fieldHot`，内边距用 `inset`。
  - TextBar：圆角 28。
  - Note：行高 18。
  - BottomSheet：间距 14 → 12。
  - CodexPage：状态圆里的图标用 `iconHero`。
  - `RungicVoiceAssistantLight.colors`：链接、焦点、次要文字同步为新值。
- **画布**：
  - 新增页面“基础”，含颜色、文字、图标、尺寸与动效、组件（浅色）、组件（深色）六张画板。组件画板画出每个控件在 QML `states` 里的全部状态，另加焦点环。
  - `chat.css` 的颜色改为取自 Theme.qml，并加入 `is-*` 强制状态类（对应 `forcedState`）；新增 `system.css`，只供基础画板排版。
  - 46 张页面画板统一图标线宽 1.7，并按新层级调整了图标尺寸、行高和输入栏圆角。
- **验证**（离线，未上机）：
  - 用 PySide6 在 K8（x86_64）上离线渲染状态总览，浅色、深色各一遍，逐段对比改动前后：开关圆钮的边清楚了，深色取消目标的图标为 #141618，其余只有颜色和圆角的预期变化。渲染时用的是替身：SystemTheme 与 DesignI18n 是桩实现，控件样式为 Basic。
  - 基础画板用 Chromium 本地渲染，检查布局并量出高度。
  - **实机（G100 S）**：`rungic-design`、`rungic-voice-agent` 和 `rungic-plasma-diagnostics` 以开发覆盖装上（docs/97）。
    - 截图方式（现为 `tools/design_gallery.py phone`；本机离线渲染为 `design_gallery.py local`）：以桌面用户身份运行 `rungic-design-gallery --section <节> --shot`，平台用 `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`，不在屏幕上开窗口。21 节 × 浅深两种主题，共 42 张，见 `.work/verify/20260930-design-gallery/`。
    - 结果与离线渲染一致：关闭的开关圆钮有 faint 边；深色取消目标的 × 为深色；危险色、输入框的聚焦和出错边、禁用 40% 都正确。
    - Thumbnail 和 LivePicture 的示例图在截图里是空的。改动前的代码在 software 后端下同样如此（`MultiEffect` 遮罩在 software 后端下不绘制），所以不是回退；带图的状态没能用截图验证。
- **没做的**：
  - 焦点环只在设计和 token 里定义，控件还没有画焦点状态。
  - 控件里其余写死的数字（与层级数值相同的）没有逐个换成 token。
  - 画板 SetInstall 里仍画着 npm 安装方式，而实现里没有提供 npm。

## 应用（`agent/assistant/app`，原 `plasma/voice-agent/app`）

- **主界面**：
  - 顶栏：对话列表、标题、新对话。
  - 对话流：你的话是浅灰气泡，带附件时缩略图在气泡上方。助手的话是正文。
  - Agent 工作中显示流光字“正在处理 · 12 秒 · 当前步骤”；完成后是可展开的“已处理 N 步 · 用时 N 秒”，回答下有复制和朗读。
  - 代打电话、审批、“去设置”是线框块。
- **新对话**：显示“有什么可以帮你？”和三条建议，点建议直接发送。对话在第一次说话或发文字时才在 Agent 里创建。
- **侧边栏**：搜索、新对话、按日分组（今天、昨天、M月D日、更早）、当前对话高亮、长按删除、底部是设置。
- **输入**：
  - 语音条整条可按住说话，轻点进入免提。
  - 按住时上方出现两个目标：滑到 × 取消，滑到“文”改成文字，文字进入编辑框。
  - 键盘模式可发文字，“+”可加照片和文件，最近照片取 `~/Pictures`，4 列显示。
  - “拍照”暂时隐藏：还没有可调用的相机入口。
- **设置**：
  - Codex：已安装、未安装、正在安装三种状态。安装方式原为系统软件包 `apt install rungic-codex`（经 pkexec）或官方脚本；npm 方式未提供，因为设备上没有 Node.js。2026-10-01 起系统不再自带 Codex，只用官方脚本安装和更新，见 docs/99。
  - OpenAI API Key：显示、隐藏，保存前先用 `/v1/models` 实测；没测过的密钥打开页面时自动测一次，不预先显示“可用”；可移除；可改用 ChatGPT 账号（设备码）登录 Codex，或改用 API Key 登录 Codex。
  - 长按 Home 呼出、朗读回答、免提时说完自动发送三个开关。自动发送默认打开，以保持原有行为；设计稿里默认是关。
  - 主题：跟随系统、浅色、深色。
  - 版本、隐私说明。
- **长按 Home 浮层**：遮罩加底部抽屉，内容同上文 listen、sending、work、answer 四个视图。回答直接复用应用的 `ChatEntry`。上拉看完整对话，下拉收起。
  - 浮层跟随应用的主题设置。关闭“长按 Home 呼出”后，浮层不响应 Home。
- **配色方案**：`RungicVoiceAssistant(.colors)` 按新的设计参数重写，状态栏和导航栏与应用同色。
- **删除的旧组件**：光效相关的 Bloom、LightPill、GlowLabel、GlassButton、IdleClock、Spinner、OverlayItem、Style 以及三个着色器；对话列表页、ChatItem、TalkDock。

## 后端（`rungic_voice_agent.py`）

- **按住说话先存本地**：按住期间的音频先留在本地，松手确认发送时再上传，加 900 ms 静音让服务端判定说完。
  - 取消和转文字因此什么都不会发出。原来是边录边传，Codex 的实时会话又没有清空输入缓冲的接口，被丢弃的半句话会并进下一次说话。
  - 实测（以下均为实机验证）：松手后回复照常；取消时日志为“talk: cancelled, nothing sent”。
- **新增 D-Bus 方法**：
  - `SendText`：文字加附件，直接发起一轮 `turn/start`。图片以 `localImage` 交给 Codex；其他文件把路径写进文字，由 Agent 自己读。
  - `TalkToText`：把按住时的全部音频交给 `gpt-4o-mini-transcribe`，结果转成简体。
  - `ReadAloud`：让实时会话原样朗读一段回答。这段朗读只播放声音，不上屏，也不写入记录，见下文。
  - 设置相关：`Setup`、`SetApiKey`、`TestApiKey`、`RemoveApiKey`、`CodexLogin`、`InstallCodex`、`CancelInstall`、`SetPreferences`。
- **偏好**：保存在 `~/.config/rungic-voice-agent/preferences.json`。
  - 朗读关闭时，不播放语音回复。
  - 免提不自动发送时，要点停止才发送。
- **没装 Codex 时**：服务不再因启动失败退出，而是在对话里提示“还差一步：安装 Codex”。
- **没配 Key 时**：只有语音不可用，并提示去配置；打字仍可用 Codex 自己的登录。
- **免提点停止**：说过话就发送，没说话就取消。
- **API Key 的读取**：统一经 `rungic_cua.keys`（在 `rungic-cua` 包里）。以下几处都改为用它：
  - 助手、代打电话（`call_proxy`）；
  - `rungic_cua` 的 `luna` 与 `speech`；
  - `rungic_clicker`。

### API Key 明文存在文件里（用户决定，2026-09-29）

- **设计稿的原意**：API Key 放进系统钥匙串。
- **钥匙串的实测情况**：
  - 容器里 KDE 的 `ksecretd` 缺 QCA OpenSSL 插件，所有 libsecret 客户端都报 `createDLGroup failed`；
  - 还没有钱包，第一次写入会弹出 KDE 钱包的英文创建向导，并卡住调用方。
- **两种钱包都不划算**：
  - 空密码钱包：保护与权限 600 的文件相同；
  - 设密码的钱包：每次开机要解锁一次。
- **钥匙串本身的局限**（按接口规范与源码的理解，未实测）：
  - 不按程序授权，解锁后本用户的任何程序都能读；
  - 不记录访问。
- **用户的决定**：直接明文存文件，不用钱包。
- **现在的实现**：
  - Key 在 `~/.config/rungic-voice-agent/openai-api-key`，文件权限 600，目录权限 700；
  - `rungic_cua.keys` 只读写这个文件；
  - 界面写明“明文保存……以你身份运行的程序（包括 Agent）都能读到它”；
  - `gir1.2-secret-1` 和 `libqca-qt6-plugins` 两个依赖已去掉。
- **清理**：测试时向导写下的 `~/.config/kwalletrc` 已删除；钱包目录一直是空的。
- **更强的方案**（未做）：由单独系统用户下的保管服务持有 Key、代为转发并记账，本机程序用得到 Key 但拿不走。见对话中的设计，留作以后。

## 语音气泡：顺序、占位与朗读（2026-09-29）

- **用户反馈的问题**：
  - Agent 的回复有时出现在用户那句话的上方；
  - 用户的气泡要等转写完成才出现，说话时屏幕上没有反馈；
  - 点某条回答的“朗读”，下面又出现一条内容相同的气泡。
- **原因**：
  - 实时会话里，用户的转写常在回复开始流出之后才完成。原来按到达顺序插入，所以用户气泡落到了回复的下面。
  - 朗读是经 `appendSpeech` 让实时模型把那段话再说一遍。模型说出的内容和其他回复一样，会产生一个转写片段，于是被当成新的一条助手消息，既上屏也写进记录。
- **用户选定的做法（A+B）**：按下就出一个“正在说话”的占位气泡，松手时显示“正在识别…”，转写完成后填入文字。不做边说边出字。
- **服务端的实现**：
  - 按下时发 `talk-started`，松手确认发送时发 `talk-sent`，取消时的 `listen-cancelled` 带上同一次按下的时间 `press`。
  - 用户的转写和消息也带 `press`。
  - 实时回复的片段带开始时间 `started`。
- **界面的实现**（`ChatModel`）：
  - 按下时插入占位气泡；转写到达后，把占位气泡换成转写文字。
  - 同一次按下之后才开始的条目排在它下面，所以回复总在用户那句话之后。
  - 取消时立即去掉占位气泡。松手 15 秒后仍没有转写（没听清），空的占位气泡也会被清掉。
  - 按住时两个目标会盖住列表底部，所以列表的底边距随之增加，占位气泡露在目标上方。
- **朗读的修复**：`ReadAloud` 发出后，下一段助手转写片段记为“朗读”。它的音频照常播放，转写的文字既不发给界面，也不写入记录。
- **服务重启后界面卡住**：
  - 现象：服务重启或崩溃时，正在执行的任务随 Codex 一起结束，但界面一直显示“正在处理 · N 秒”。
  - 修复：`AgentClient` 用 `QDBusServiceWatcher` 监视 `com.rungic.VoiceAgent`，服务重新注册时产生 `agent-restarted`。对话页收到后重开当前对话，浮层收到后重开助手对话。重开时，载入的历史会把未完成的任务标为结束。
- **实机验证**：
  - 按下后占位气泡出现在目标上方，松手后变为“正在识别…”。15 秒没有转写时，占位气泡被清掉；取消时立即消失。以上都是空录音的路径。
  - 点“朗读”后，日志显示播放了 10.6 秒语音，界面上没有新增气泡。
  - 重启服务后，App 立即重开了当前对话；原来卡在“正在处理”的任务显示为“已处理 6 步 · 用时 34 秒”。
- **待验收**：真人说话时，转写填入占位气泡，回复出现在它下面。
- **历史里的旧重复**：修复前，朗读产生的那条重复消息已经写进记录，仍会显示。

## App 一打开就卡死：照片网格的布局循环（2026-09-29）

- **现象**：点开 App 后没有窗口出现，任务栏显示它在运行，主线程一直满载（持续一小时）。关掉后再开，可以复现。
- **定位**：
  - gdb 调用栈：主线程停在 `QQuickWindowPrivate::polishItems` → `QQuickBasePositioner::prePositioning` → `setImplicitSize` → 绑定 → `QQuickItem::setHeight`，反复循环。
  - 在这三个函数上设断点并打印对象类名，确认参与循环的是 `Grid`、它外面的 `ColumnLayout` 和 3 个照片按钮，也就是附件面板里的“最近照片”网格。
- **原因**：
  - 网格放在 `ColumnLayout` 里并填满宽度，格子边长取网格宽度的四分之一。
  - 网格又把各格宽度之和上报为自己的隐式宽度，照片不满 4 张时两者不一致，布局与格子尺寸互相驱动，无法收敛。
  - 附件面板没打开也参与布局，所以一启动就卡死。
  - 之前实测时照片数量没有触发这个循环；Blender 任务渲染的图片存进“图片”目录后，照片变成 3 张。
- **修复**：网格包在一个填满宽度的容器里，格子边长按容器宽度计算并取整，网格的隐式宽度不再回到布局。
- **实机验证**：在 3 张照片的状态下，App 正常打开，空闲时 CPU 为 0；附件面板中 3 张照片按 4 列的格子排列。
- **规则**：定位器（`Row`/`Column`/`Grid`/`Flow`）里子项的尺寸不能依赖定位器自身的宽高。要按可用宽度计算尺寸时，从外层容器取值。

## 实测（G100 S，实机验证）

- **设计系统状态总览**：浅色、深色两遍，全部控件的全部状态核对过。上文 5 处问题修复后复查通过。
- **新对话与发文字**：新对话页与设计一致。点建议后，文字一轮约 13 秒，回答带“已处理 5 步”和复制、朗读按钮，语音同时朗读。
- **语音**：用户本人语音问天气，走通了松手后一次上传的路径。
- **按住手势**：
  - 按住时语音条变为强调色，出现两个目标。滑到 × 时目标变红，语音条变灰，提示“松开取消”。
  - 松手后日志为“talk: cancelled, nothing sent”。
- **键盘与附件**：
  - 键盘模式下有文字时出现发送按钮；打开附件面板时键盘收起、面板占据原位。
  - 选一张最近照片后，托盘显示缩略图。带图发送后，Agent 描述了照片内容；气泡上方显示缩略图。
- **侧边栏与设置**：
  - 侧边栏、设置、API Key 页与设计一致。
  - 主题切到深色时，应用、状态栏、导航栏同时变化，重启后保留。浮层也跟随深色。
- **浮层**：用 D-Bus `Show`、`Hold` 模拟 Home 键。回答、聆听、免提视图正确。免提 8 秒无语音后取消，浮层随后自行收起。

## 未验证与遗留

- **转文字**：需要真人说话，本轮只验证了按住手势和空音频的路径。
- **按住时的实时字幕**：设计稿有，但 Codex 的实时会话要等说话停顿才出转写，按住期间拿不到，所以没有做。要做需另接 OpenAI 的实时转写。
- **Codex 安装**：未安装状态和安装流程未在实机上执行（本机已安装）。ChatGPT 设备码登录也未走完。
- **发布**：`rungic-design`、`rungic-voice-agent`、`rungic-cua` 0.347 已随正式版本 20260929.1（提交 e0d1e7e1，66 个包）部署到 G100 S。
  - 同一版本也补齐了远端的 KWin `+rungic8` 和 kscreen `+rungic5`；完整性检查为 clean。
  - 部署时手机熄屏，所以部署报告“桌面未就绪”；进程均已运行。
  - 浮层服务在会话启动时第一次失败，3 秒后自动重启成功。
    - 原因：plasmashell 的启动任务连续超时（熄屏时），`After=` 只等任务结束，不管成败，浮层于是在 plasmashell 起来前就启动了。它加载时要通过 D-Bus 询问 plasmashell 的面板，于是卡住，直到 systemd 判它超时。
    - 修复：浮层先等 plasmashell 在会话总线上注册 `org.kde.plasmashell`，最多等 120 秒，`TimeoutStartSec=180`。
    - 实机验证：停掉 plasmashell 后启动浮层，浮层停在 activating；再启动 plasmashell，2 秒后浮层变为 active，重启次数为 0。
  - 部署后的界面还没有在亮屏状态下复查。
- 宽屏布局、附件缩略图的圆角裁切。
