# Rungic developer guide and documentation index

Start with the [project philosophy: working together](philosophy.md).

For developers: where the project came from, which document covers each capability, the repository layout, how to get started, and the index of all documents. For the product itself see the [project home](../README.md); engineering conventions are in [AGENTS.md](../AGENTS.md). The documents themselves are written in Chinese; their titles are translated below.

## Project history

Rungic is an AgentOS for Android phones: a Plasma Mobile desktop in an Ubuntu container, an Android host app, hardware bridges, and voice and desktop automation agents. The main development device is the Motorola moto g100s (XT2537-4, Adreno 710). The project was renamed Rungic on 2026-09-26 (formerly Moto Android / Plasma Mobile); see [70](70-rungic-rebrand.md). It maintains Ubuntu 26.04 ARM64, the Plasma Mobile 6.6 series, and the Android host, GPU, input, network and media bridges.

Phosh was dropped on 2026-09-23. The shared media, network and clipboard implementations live on in `shared/`.

## Agent capabilities and their documents

Agent Ready is a system capability, independent of the selected agent. The bundled Codex assistant demonstrates an end-to-end integration. The table below describes that implementation and the services it uses; it is not a requirement to use Codex or a claim that every replacement agent is already integrated.

| Capability | What it does | Documents |
|---|---|---|
| Voice conversation | Hold Home to call it; GPT Realtime talks and reads results aloud, Codex runs the task in the background | [59](59-voice-agent.md), [67](67-home-assistant.md) |
| Visible work | Chat interface (the main conversation and others); a task plan checklist, spoken step updates, pictures and files right in the answer | [87](87-agent-app-redesign.md)–[89](89-agent-progress.md) |
| Desktop settings and sign-in | Choose Codex (default) or the Luna API executor in Settings → Desktop operation; Codex sign-in is a separate choice. Voice and TTS still use the API key | [101](101-codex-sign-in-and-api-key.md), [106](106-codex-desktop-operation.md) |
| Operating desktop apps | Desktop MCP tools for screenshots, input, windows and launching; Codex reasons over screenshots by default with the Agent’s selected model and sign-in; a separate Luna API executor remains optional | [60](60-computer-use.md), [68](68-luna-computer-use.md), [106](106-codex-desktop-operation.md) |
| Assistant's screen (workspace) | A desktop of the assistant's own: its own KWin, private D-Bus and accessibility buses, its own Xwayland; shown in a floating window or on the TV. Display/input separation under the same Linux account, not a filesystem security sandbox | [65](65-agent-screen.md), [research/91](research/91-agent-workspaces.md) |
| Where it works | On the user's desktop while desktop mode or casting is on, otherwise on its own screen; the user can say which | [research/91](research/91-agent-workspaces.md) |
| Single-instance apps | WeChat, Telegram and similar apps move to the assistant's screen when needed; the user is asked before an instance they use is closed | [research/91](research/91-agent-workspaces.md) |
| Phone functions | Brightness, clipboard, orientation, vibration, network and display information, Android settings panels (`rungic-platform`); casting, screenshots | [59](59-voice-agent.md) |
| Calls and voice messages | Codex handles UI actions by default; local audio helpers still use API voice services. Full call and voice-message workflows have not been validated with the new default; accepting a dial request is not proof that a call connected | [63](63-call-proxy.md), [106](106-codex-desktop-operation.md) |
| Proactive suggestions | Local fault collection and version-scoped compatibility matching; grouped, swipeable Folio widgets; reminders, investigation reports, revision-bound repair approval and persistent task state | [Proactive suggestions](research/proactive-system-care.md), [Knowledge](../compatibility/README.md) |
| Agent widget | Separate pixel-art widget; observed Codex token usage and account-supplied quota/reset data, with links into the assistant. API-key and subscription modes are distinguished | [Proactive suggestions](research/proactive-system-care.md) |
| Editable instructions | The guidelines and skills live in the user's home (`~/.config/rungic-voice-agent/prompts`, `~/.codex/skills`) and apply as soon as they change; a password or a detour that changes the outcome is asked about first | [59](59-voice-agent.md) |
| Native diagnostics | Merged logs, crash symbolization, evidence snapshots and control-level UI actions as MCP tools for development agents | [55](55-agent-native-debugging.md) |

## Integrating another agent

Start with the [Agent Ready interface reference](agent-ready-interfaces.md): both MCP servers, their tool inventory and startup, phone/workspace commands, suggestion and assistant D-Bus methods, and the shared Linux interfaces. It distinguishes reusable system entry points from the bundled assistant's integration contracts.

Users can run another compatible agent in the Linux environment and connect it to the tools it supports. Reusing system tools and replacing the bundled assistant's complete experience are different integration scopes: voice, chat progress, suggestion execution and usage currently have explicit Codex/assistant connections that need adaptation.

| Layer | Reusable interface and source | What a replacement must provide |
|---|---|---|
| Desktop operation | [Desktop MCP server](../agent/computer-use/rungic_cua/server.py): `desktop_screenshot`, `desktop_act`, window and launch tools, plus `desktop_goal` | MCP client configuration and workspace routing. The bundled Codex reasons over screenshots and calls actions itself by default; the separate Luna goal helper is available in explicit API mode |
| Phone and casting | `rungic-platform --request` structured commands and `rungic-cast`; contracts in the [phone settings skill](../agent/assistant/skills/rungic-phone-settings/SKILL.md) and the [screens skill](../agent/assistant/skills/rungic-screens/SKILL.md) | Command invocation, result/error handling and capability checks before operations |
| Files, software and authorization | Linux tools, shared Android storage, PackageKit/`pkgcli` and polkit; [filesystem boundaries](69-filesystem-capabilities.md), [app installation](45-plasma-app-store.md) | Respect filesystem capabilities and use the user's system authentication flow for privileged operations |
| Suggestions and knowledge | [C++ service](../agent/suggestions/service.h), `com.rungic.Suggestions` D-Bus methods and `rungic-suggestions` CLI; [versioned knowledge data](../compatibility/README.md) | Consume issue evidence, report investigation and repair state, and preserve plan/evidence revision checks. Current issue opening and task handoff target the bundled voice assistant and need rerouting |
| Conversation and execution | [Voice/task bridge](../agent/assistant/rungic_voice_agent.py), currently speaking Codex `app-server` JSON-RPC | Map the replacement's sessions, start/stop, progress, results and errors into the assistant UI and voice flow |
| Usage display | [Usage collector](../agent/suggestions/usage.cpp) and `AgentUsage`/`UsageChanged` | Account identity, timestamped usage and any provider-supplied quota/reset windows. Local token observations are not a billing total and do not include realtime voice usage |
| Development and delivery | [Development MCP server](../tools/rungic_agent_mcp.py), [diagnostics](55-agent-native-debugging.md), [build skill](../.agents/skills/rungic-three-stage-image/SKILL.md) and versioned package tools | Development-host/device access and the applicable build, deployment and acceptance workflow; these are not automatically granted to a phone-side assistant |

The bundled agent runs with the desktop user's access, and its Codex task settings currently use `danger-full-access` with `approvalPolicy=never`. Instructions to confirm destructive actions are behavioral policy; the workspace is not an agent sandbox. Package authorization and the suggestion service's revision-bound approval are separate mechanisms. Replacing the agent therefore includes reviewing its permissions and confirmation behavior, not just changing a model name.

An integration should be accepted through observable outcomes: launch an app on the selected desktop, perform an action, return a usable file, report and stop a task, investigate a suggestion without applying a repair, reject stale repair approval, and show unavailable usage honestly. Confirm the user's password dialog stays on the user's desktop and that the agent's input targets the intended workspace. Existing Codex acceptance does not establish these behaviors for another agent.

## Proactive system care: implemented scope

The service records observations separately from agent tasks and user reminders. Its current collectors cover recent Linux coredumps, failed system/user services, unfinished package operations, low disk space and verified compatibility entries matching installed versions and optional environment fields. Missing or stale evidence is a coverage gap, not a clean bill of health. It is not a general detector for all Android faults or all missing hardware acceleration.

The user-facing path is **observation → grouped suggestion → user handoff → investigation → confirmed repair plan → verification**. Repeated evidence updates an existing issue; related cards can share a swipeable stack without asserting a shared root cause. Cards and group controls open the corresponding app views. The Folio widgets preserve the native home screen, its favorites and app drawer; the usage widget is separate.

The ledger persists issue, task and reminder state across restarts. Users can defer, schedule a reminder or dismiss suggestions. Notification selection respects inhibition, evidence freshness, presentation history and throttling; it does not implement an “after this app exits” trigger. Investigation reports carry a conclusion, confidence and next action. A repair approval captures the exact plan and evidence revision; task completion alone does not resolve the issue.

The [compatibility knowledge base](../compatibility/README.md) is reviewed data, not executable repair scripts. `verified` entries can generate suggestions; `research` and `superseded` entries cannot. `policy` entries preserve intentional choices and do not generate optimization prompts. Shared backend fixes belong at the common system layer where possible; component changes remain in the repository's pinned upstream and patch-queue workflow.

Upstream cooperation currently provides project metadata, local facts export and manually recorded issue/PR state. `rungic-suggestions feedback ID` produces local material; it does not upload it or open a PR. A requested contribution still needs reproduction, a scoped patch, validation and the upstream project's contribution procedure. Automatic submission/status synchronization is not implemented, and an upstream merge does not resolve an installed-device issue without deployment and verification.

Acceptance evidence and historical revisions are in the [system-care record](research/proactive-system-care.md). It includes G100 validation of grouping, vertical swipes, current-card navigation and task recovery. API-key usage was tested live; subscription quota/reset presentation used fixtures. These scopes should be kept separate when extending the implementation.

## System features and their documents

| Area | Features | Documents |
|---|---|---|
| Foundation | Ubuntu 26.04 ARM64 (glibc) in LXC on Android 16, root through Magisk; `~/Shared` is Android's shared storage | [38](38-plasma-mobile.md), [40](40-plasma-mobile-integration.md), [69](69-filesystem-capabilities.md) |
| Desktop | Stock Plasma Mobile 6.6.5, KWin 6.6.6 with an Android host backend; Rime Chinese input, screen recording, edge back gesture | [40](40-plasma-mobile-integration.md), [41](41-plasma-rime-input.md), [72](72-kwin-android-host-isolation.md) |
| Display | Zero-copy presentation with explicit sync and UBWC-compressed output; 120 Hz requested while touching; native resolution and display-size policy | [49](49-plasma-performance.md), [57](57-zero-copy-explicit-sync.md), [85](85-phone-display-size-policy.md) |
| Second screen | Desktop mode (the full desktop in a floating window); casting to a TV with our own Miracast source, the phone as touchpad and keyboard | [65](65-agent-screen.md), [66](66-pointer-gestures.md), [84](84-miracast-source.md) |
| GPU | Mesa on KGSL (freedreno GL/GLES, Turnip Vulkan 1.4); X11 apps on the GPU through Xwayland glamor and DRI3; a Flatpak GL extension, KGSL in `--device=dri` | [51](51-plasma-vulkan-benchmark.md), [research/93](research/93-xwayland-kgsl-gpu.md), [research/94](research/94-mesa-base.md) |
| Audio and video | System speaker and microphone; cameras through libcamera and PipeWire; H.264/HEVC/VP9 hardware decoding, H.264 hardware encoding; screen-sharing portal | [48](48-plasma-media-pipelines.md), [62](62-linux-virtual-audio.md) |
| System services | Two-way clipboard and clipboard history; Wi-Fi, Bluetooth and cellular state from Android; SSH on by default; rootless Docker in the container | [83](83-service-policy.md), [85](85-lxc-rootless-docker.md), [Clipboard history](research/clipboard-history.md) |
| Apps | Firefox (WebGL, hardware video), Blender (Vulkan viewport), Krita 6, Telegram, VS Code, WeChat; installs through Discover and `pkgcli`, with the system's password dialog | [36](36-firefox-input-fix.md), [45](45-plasma-app-store.md), [90](90-blender-vulkan-incident.md) |
| Delivery | Upstream components pinned with patch queues; a local APT repository and release metapackage, automatic acceptance after deployment, rollback by package; dev releases cut from origin/main and deployed to every connected phone | [61](61-delivery-diagnostics-plan.md), [71](71-upstream-patch-queue.md), [73](73-reduce-upstream-changes.md), [109](109-dev-release-channel.md) |
| Delivery stages | Device/GKI preparation → independent RungicOS image → separate installation/upgrades. APT updates exist; the standalone first-install/full-rootfs update tool remains to be implemented. G100 full-flash acceptance belongs to the earlier path | [75](75-image-build-separation.md), [80](80-g100-image-installation-retrospective.md), [83](83-x70-air-pro-onboarding.md) |

The acceptance scope of each item is in its documents. Known limits: Turnip's Wayland presentation flickers on KGSL, so the desktop stays on GLES ([56](56-kwin-vulkan-quantification.md)); Mesa is still based on a community branch, and a move to upstream was tried and reverted ([research/94](research/94-mesa-base.md)).

## Repository layout

| Directory | Contents |
|---|---|
| `android/` | Android host APK (`app/`), our Rust host modules (`host/`), and Android build scripts |
| `agent/` | Assistant app and voice/task bridge, computer-use tools, Codex integration, suggestions, agent screen and workspaces |
| `desktop/` | Plasma session integration, UI components, casting, recording, input, desktop hardware controls and benchmarks |
| `system/` | Container startup, accounts, rootfs management, Android bridge scripts, system defaults and diagnostics |
| `packaging/` | Build definitions and maintainer scripts for Rungic's own packages |
| `release/` | Release package selection, service restart policy and acceptance scenarios |
| `packages/` | Pinned upstream sources and DEP-3 patches, including the Android host, Smithay, Winit, KDE and Firefox mobile configuration ([71](71-upstream-patch-queue.md), [migration completion](73-reduce-upstream-changes.md#remaining-source-trees-migrated-2026-09-30)) |
| `shared/` | Common Linux interfaces for media, network, clipboard and GPU |
| `tools/` | Management, ROM, build and diagnostic tools |
| `kernel/`, `lxc/` | Kernel and container configuration |
| `docs/` | Implementation documents; `research/` keeps reusable findings |
| `benchmarks/` | Selected raw performance data and analyses |
| `provenance/` | Upstream origin records, versions, checksums, migration evidence and exact-hash audit exceptions |
| `signing/development/` | The development APK signing identity, synced at the user's request |
| `.work/` | Not synced: downloads, dependencies, caches, logs, media, packages and other keys |

Boundaries and migration notes: [repository scope](52-git-repository-scope.md). The remote is the private repository [kevinzhow/Rungic](https://github.com/kevinzhow/Rungic), default branch `main`. The documents contain device identities and network configuration and are not redacted for publication. The former `plasma/` source directory was split by responsibility; installed paths, service names and the Android application ID retain their existing names. The obsolete GSI-only `cutout/` overlay was removed; current cutout handling reads Android's display metadata.

## Status and entry points

- Stock Android 16 with Magisk 31, SELinux enforcing. LXC is deployed; Docker runs rootless inside the container ([85](85-lxc-rootless-docker.md)). The three-stage flash package has been verified with a wiped install on the G100 ([80](80-g100-image-installation-retrospective.md)).
- The Plasma APK and the Ubuntu container run at the native 1080×2400 with 30/60/90/120 Hz and automatic refresh policies. KWin stays on GLES; the Vulkan comparison and its limits are in [51](51-plasma-vulkan-benchmark.md), the value of a native Vulkan KWin in [56](56-kwin-vulkan-quantification.md).
- Media and display still have open acceptance items; see [48](48-plasma-media-pipelines.md) and [50](50-plasma-display-settings.md). An installation that succeeds is not a completed acceptance.
- Device management: `python3 tools/rungic_plasma.py status`. Development environment: `source tools/work-env.sh`. APK build: `bash android/build-apk.sh`, output under `.work/`.
- Graphics and backend architecture: [40](40-plasma-mobile-integration.md) and the [shared bridges](../shared/README.md). A fresh build machine still needs the SDK/NDK and some dependencies.
- Remote source checks and multi-machine work: [53](53-remote-system-development.md). Upstream modifications live in `packages/` and are edited through `tools/pq.py prepare/export`. Linux upstream packages use `tools/build_on_device.py`. For Android, `tools/prepare_android_host.py` assembles the host and its Smithay/Winit dependencies under `.work/`, then `android/build-native-core.sh` cross-compiles the library. Firefox mobile configuration is prepared from its recipe by `tools/rungic_package.py`. No directly tracked upstream source-tree exceptions remain; see [73](73-reduce-upstream-changes.md#remaining-source-trees-migrated-2026-09-30).

Which layer a vendor adaptation belongs in, and the conditions for moving it to a shared backend: [73](73-reduce-upstream-changes.md#下沉的边界条件自原-54-篇迁入).

Delivery, acceptance and diagnostics: [61](61-delivery-diagnostics-plan.md). Every file this project puts on the container's rootfs comes from a package (`packaging`, patch queues and vendor rebuilds), deployed through the local APT repository and the release metapackage (`tools/rungic_release.py deploy|rollback|status`), followed by automatic acceptance (`tools/rungic_acceptance.py`); `rungic-integrity` checks for drift. The rootfs is an ext4 image (`system/rootfs-image`); deployment can take a dm-snapshot first and return to it when acceptance fails. `/home`, crash reports and the local repository are not rolled back with it.

Current delivery direction: prepare the Android/GKI base once when compatible, build RungicOS independently, and install or update Rungic separately; contracts, existing tools and remaining work: [75](75-image-build-separation.md).

## Documentation index

Every feature, the experience it must give and the code, tests and documents behind it: the [feature inventory](feature-inventory.md) (generated from `quality/`, rules in [quality/README.md](../quality/README.md)). Before changing a feature, look it up there; before deleting a file, ask it who owns it.

01–21 cover the device, ROM and container history; the early Phosh-only installation documents were removed. 28–35 keep research on shared interfaces; 38 onward cover Plasma. A status recorded as verified at the time does not mean the feature is accepted today.

| Document | Topic |
|---|---|
| [01-device.md](01-device.md) | Device identity |
| [02-linux-feasibility.md](02-linux-feasibility.md) | Why not a Linux distribution |
| [03-gsi-dsu.md](03-gsi-dsu.md) | Trying the official Android 17 GSI with DSU |
| [04-permanent-gsi.md](04-permanent-gsi.md) | Flashing the Android 17 GSI permanently |
| [05-magisk-root.md](05-magisk-root.md) | Magisk root (init_boot) |
| [06-pitfalls.md](06-pitfalls.md) | Pitfalls |
| [07-restore-stock.md](07-restore-stock.md) | Returning to stock MYUI |
| [08-commands.md](08-commands.md) | Command reference |
| [09-stock-magisk.md](09-stock-magisk.md) | Checking stock MYUI with Magisk |
| [10-stock-debloat.md](10-stock-debloat.md) | A debloated stock firmware: W1WAA36.48-23-10 |
| [11-stock-install.md](11-stock-install.md) | Installing the debloated system and Magisk on the device |
| [12-offline-magisk.md](12-offline-magisk.md) | Debloated package v2: the abandoned system-app approach |
| [13-offline-magisk-user-app.md](13-offline-magisk-user-app.md) | Magisk offline first boot v3: a regular app install |
| [14-oneclick-package.md](14-oneclick-package.md) | v3 one-step full reinstall package (delta packaging) |
| [15-container-reassessment.md](15-container-reassessment.md) | Docker and LXC reassessed (2026-09-22) |
| [16-lxc-prerequisites.md](16-lxc-prerequisites.md) | LXC prerequisites measured (2026-09-22) |
| [17-lxc-installation.md](17-lxc-installation.md) | LXC deployment and verification (2026-09-22) |
| [18-termux-lxc.md](18-termux-lxc.md) | Managing LXC from Termux (2026-09-22) |
| [21-memory-audit.md](21-memory-audit.md) | RAM use of the stock system (2026-09-22) |
| [28-capability-audit.md](research/28-capability-audit.md) | Phosh everyday capabilities and Android hardware interfaces |
| [29-reuse-research.md](research/29-reuse-research.md) | Existing solutions researched before adapting Phosh |
| [30-feature-adaptation.md](research/30-feature-adaptation.md) | Feature-by-feature adaptation and its acceptance scope (began with Phosh; kept current for Plasma) |
| [31-backend-integration.md](research/31-backend-integration.md) | How the desktop connects to the Android backend: architecture, interfaces, research and where to change what |
| [32-network-integration.md](research/32-network-integration.md) | Android networking in GNOME and Phosh |
| [33-capture-integration.md](research/33-capture-integration.md) | Microphone, camera, photos and video recording |
| [34-hardware-codec-audit.md](research/34-hardware-codec-audit.md) | Hardware video encoding and decoding: device checks and candidates |
| [35-hardware-codec-integration.md](research/35-hardware-codec-integration.md) | Android hardware codecs for Linux apps and Firefox |
| [74-vaapi-feasibility.md](research/74-vaapi-feasibility.md) | The codec bridge as a VA-API driver (it cannot replace the current patches) |
| [36-firefox-input-fix.md](36-firefox-input-fix.md) | Fixing the Firefox address-bar input crash |
| [37-linux-distribution-evaluation.md](37-linux-distribution-evaluation.md) | Alpine, Debian or Ubuntu, and the migration limits |
| [38-plasma-mobile.md](38-plasma-mobile.md) | A standalone Plasma Mobile: version target, distribution and deployment |
| [39-magisk-daemon-crash.md](39-magisk-daemon-crash.md) | Why the Magisk 31.0 daemon exited |
| [40-plasma-mobile-integration.md](40-plasma-mobile-integration.md) | Ubuntu Plasma Mobile: the desktop and the Android backend |
| [41-plasma-rime-input.md](41-plasma-rime-input.md) | Rime Chinese input in Plasma Mobile |
| [42-plasma-runtime-acceptance.md](42-plasma-runtime-acceptance.md) | Plasma Mobile runtime fixes and acceptance |
| [43-plasma-panel-workarea.md](43-plasma-panel-workarea.md) | Plasma status bar height and app placement |
| [44-plasma-user-account.md](44-plasma-user-account.md) | First account and password setup |
| [45-plasma-app-store.md](45-plasma-app-store.md) | The Plasma Mobile app store |
| [46-plasma-recording-and-edge-back.md](46-plasma-recording-and-edge-back.md) | Plasma screen recording and the Android edge back gesture |
| [47-plasma-input-window-flicker.md](47-plasma-input-window-flicker.md) | Windows jumping while typing in Plasma Mobile |
| [48-plasma-media-pipelines.md](48-plasma-media-pipelines.md) | Plasma media sharing interfaces and quality acceptance |
| [49-plasma-performance.md](49-plasma-performance.md) | Plasma animation, list frame rates and Android scheduling |
| [50-plasma-display-settings.md](50-plasma-display-settings.md) | KDE display settings and Android's native resolution |
| [51-plasma-vulkan-benchmark.md](51-plasma-vulkan-benchmark.md) | The Plasma Vulkan path and its performance |
| [52-git-repository-scope.md](52-git-repository-scope.md) | The private repository and local working directories |
| [55-agent-native-debugging.md](55-agent-native-debugging.md) | Agent-native debugging: collection, crash scenes, tracing and control-level actions |
| [56-kwin-vulkan-quantification.md](56-kwin-vulkan-quantification.md) | What a native Vulkan KWin would gain |
| [57-zero-copy-explicit-sync.md](57-zero-copy-explicit-sync.md) | Zero-copy presentation and explicit sync |
| [58-miracast-desktop-feasibility.md](58-miracast-desktop-feasibility.md) | Casting the desktop over Miracast with the phone as touchpad |
| [59-voice-agent.md](59-voice-agent.md) | The voice agent: GPT Realtime driving Codex |
| [60-computer-use.md](60-computer-use.md) | Computer use: a Linux backend for arc-cua and JEV (rungic-cua) |
| [61-delivery-diagnostics-plan.md](61-delivery-diagnostics-plan.md) | Delivery, acceptance and diagnostics: packaging, releases, rollback, crash chain, rootfs snapshots |
| [62-linux-virtual-audio.md](62-linux-virtual-audio.md) | A Linux speaker and microphone (system virtual audio devices) |
| [63-call-proxy.md](63-call-proxy.md) | The call agent: making and answering calls for the user |
| [64-goal-computer-use.md](64-goal-computer-use.md) | Goal-level computer use: typesafe-computer-use with OCR on the phone's GPU |
| [65-agent-screen.md](65-agent-screen.md) | The assistant's screen: a second output on demand, in a floating window or cast |
| [66-pointer-gestures.md](66-pointer-gestures.md) | Pointer gestures and movement: direct touch, touchpad, TV |
| [67-home-assistant.md](67-home-assistant.md) | Holding Home to call the voice assistant |
| [68-luna-computer-use.md](68-luna-computer-use.md) | GPT-6 Luna computer use (deciding where to click from the picture) |
| [69-filesystem-capabilities.md](69-filesystem-capabilities.md) | File system and container capabilities |
| [70-rungic-rebrand.md](70-rungic-rebrand.md) | The Rungic (AgentOS) rename: naming, migration research, phases and progress |
| [71-upstream-patch-queue.md](71-upstream-patch-queue.md) | Upstream components as patch queues: practice, layout, patch rules, tools, tests and the KWin pilot |
| [72-kwin-android-host-isolation.md](72-kwin-android-host-isolation.md) | KWin's Android host support as a protocol and a separate backend |
| [73-reduce-upstream-changes.md](73-reduce-upstream-changes.md) | Changing less upstream code: finishing the patch queues, extension points and shared services |
| [75-image-build-separation.md](75-image-build-separation.md) | Splitting Android firmware, RungicOS rootfs and kernel builds, and the cross-device contract |
| [76-g100-memory-audit.md](76-g100-memory-audit.md) | Android memory use on the XT2533-4 G100 |
| [77-g100-three-ci-assessment.md](77-g100-three-ci-assessment.md) | Three image CI pipelines, the G100 as the first device spec, runners and cache cleanup |
| [78-g100-firmware-inventory.md](78-g100-firmware-inventory.md) | XT2533-4 G100 stock firmware sources, extraction and offline checks |
| [79-g100-ci-execution.md](79-g100-ci-execution.md) | The first run of the G100 three-stage image CI |
| [80-g100-image-installation-retrospective.md](80-g100-image-installation-retrospective.md) | G100 full image retrospective: the problems and the best path |
| [81-end-to-end-user-experience.md](81-end-to-end-user-experience.md) | The end-to-end user experience: review and improvements |
| [82-first-run-ux-refactor.md](82-first-run-ux-refactor.md) | First-run experience rework: first batch |
| [83-service-policy.md](83-service-policy.md) | The system services page and optional SSH login |
| [83-x70-air-pro-onboarding.md](83-x70-air-pro-onboarding.md) | Bringing up the X70 Air Pro (vantage) |
| [84-miracast-source.md](84-miracast-source.md) | Our own Miracast source, without the vendor casting components |
| [85-lxc-rootless-docker.md](85-lxc-rootless-docker.md) | Rootless Docker in the Plasma container: trials and packaging |
| [85-phone-display-size-policy.md](85-phone-display-size-policy.md) | The phone display-size policy and its implementation |
| [86-x70-miracast-assessment.md](86-x70-miracast-assessment.md) | Completing Miracast on the X70 Air Pro |
| [87-agent-app-redesign.md](87-agent-app-redesign.md) | Assistant app v3: a chat interface and a design system library (2026-09-29) |
| [88-agent-visible-work.md](88-agent-visible-work.md) | Making the assistant's work visible: pictures in the chat, screen captions, phone functions (2026-09-29) |
| [89-agent-progress.md](89-agent-progress.md) | The assistant's progress: task state, spoken updates by event, conversation ownership (2026-09-29) |
| [90-blender-vulkan-incident.md](90-blender-vulkan-incident.md) | The Blender rendering incident and CPU rendering by default (2026-09-29) |
| [91-agent-workspaces.md](research/91-agent-workspaces.md) | Workspaces: a GUI space of each agent's own (2026-09-29) |
| [92-agent-task-speed.md](research/92-agent-task-speed.md) | Why tasks like Blender modelling are slow, and how other agents speed them up (2026-09-30) |
| [93-xwayland-kgsl-gpu.md](research/93-xwayland-kgsl-gpu.md) | X11 apps on the GPU with KGSL: approaches for Xwayland (2026-09-30) |
| [94-mesa-base.md](research/94-mesa-base.md) | The Mesa base: the lfdevs branch or upstream with our own patches (2026-09-30) |
| [91-x70-independent-install.md](91-x70-independent-install.md) | X70 Air Pro: the independent three-stage installation |
| [92-x70-android-base-end-to-end.md](92-x70-android-base-end-to-end.md) | X70: installing on a freshly flashed Android base, end to end |
| [93-x70-independent-image-revalidation.md](93-x70-independent-image-revalidation.md) | X70: the rebuilt independent image and its first-install recheck |
| [94-build-fingerprints.md](94-build-fingerprints.md) | Building, reusing and verifying images by input fingerprint |
| [95-install-use-case-tests.md](95-install-use-case-tests.md) | Installation use-case tests: how they are written, and the first three fixes |
| [96-desktop-recovery-after-apk-restart.md](96-desktop-recovery-after-apk-restart.md) | The desktop recovering after the APK's data is cleared or it is force-stopped |
| [97-local-development-deploy.md](97-local-development-deploy.md) | Development overlays on top of a release (2026-09-30) |
| [98-agent-model-selection.md](98-agent-model-selection.md) | Choosing the agent's model and reasoning effort (2026-10-01) |
| [99-codex-standalone.md](99-codex-standalone.md) | Codex from its official standalone install, with update prompts in Settings (2026-10-01) |
| [100-login-environment.md](100-login-environment.md) | The desktop session's login environment: ~/.local/bin and ~/.profile (2026-10-01) |
| [101-full-duplex-phone-mode.md](101-full-duplex-phone-mode.md) | Agent phone mode: GPT Realtime, task control and shared communication audio (2026-10-02) |
| [101-codex-sign-in-and-api-key.md](101-codex-sign-in-and-api-key.md) | Codex sign-in and the OpenAI API key: two separate credentials (2026-10-01) |
| [102-design-system-choices-and-swipe-back.md](102-design-system-choices-and-swipe-back.md) | Design system gaps: choice feedback, bottom sheets, swipe back (2026-10-01) |
| [103-krita-save-dialog-hang.md](103-krita-save-dialog-hang.md) | Krita stops responding: the portal's Save As result never arrived (2026-10-01) |
| [104-system-monitor-gpu-disks.md](104-system-monitor-gpu-disks.md) | GPU and disks in the system monitor: ksystemstats with KGSL and mountinfo (2026-10-03) |
| [105-test-debt-2026-10-03.md](105-test-debt-2026-10-03.md) | Paying down the feature inventory's test debt: results, defects found and fixed, what is left (2026-10-03) |
| [95-agent-usage-providers.md](research/95-agent-usage-providers.md) | A common interface for agent usage, and where Claude Code's data comes from (2026-09-30) |
| [96-agent-curated-briefing.md](research/96-agent-curated-briefing.md) | Suggestion cards the agent curates (the briefing layer) |
| [97-headless-agent-work.md](research/97-headless-agent-work.md) | The agent working with the phone locked and the screen off; desktop-mode fullscreen and auth prompts (§21) |
| [cellular-call-agent.md](research/cellular-call-agent.md) | The agent making calls with the SIM: feasibility |
| [clipboard-background.md](research/clipboard-background.md) | The background clipboard bridge rework (2026-09-29) |
| [g100-ssh-connectivity-20260929.md](research/g100-ssh-connectivity-20260929.md) | G100 SSH over the LAN (2026-09-29) |
| [g100-system-update-20260929.md](research/g100-system-update-20260929.md) | A G100 full-system incremental update (2026-09-29) |
| [g100-ugreen-miracast.md](research/g100-ugreen-miracast.md) | G100 casting to a UGREEN receiver, covered by Moto's external-display UI |
| [mac-offline-desktop-link.md](research/mac-offline-desktop-link.md) | A MacBook reaching the phone's desktop without a network: opendrop-rs assessed |
| [miracast-video-modes.md](research/miracast-video-modes.md) | Miracast video modes: real capabilities, exact selection and acceptance |
| [106-codex-desktop-operation.md](106-codex-desktop-operation.md) | Codex desktop operation by default, with optional API execution (2026-10-03) |
| [107-android-sms.md](107-android-sms.md) | Android SIM text messages, send status and filtered replies (2026-10-04) |
| [108-codec-bridge-buffers.md](108-codec-bridge-buffers.md) | Hardware video decoding straight through msm_vidc V4L2 (5x less CPU), the MediaCodec bridge with DMA-BUFs and 10-bit as fallback; the Iris driver (2026-10-04) |
| [109-dev-release-channel.md](109-dev-release-channel.md) | The dev release channel: releases from origin/main with the APK, bundles, deploy/status on every phone, GitHub pre-releases; how apt keeps our packages (2026-10-04) |
| [113-independent-linux-services.md](113-independent-linux-services.md) | Linux user services and replaceable hardware backends: their lifetime, Android 16 root probes, what is still to check |
| [114-task-control.md](114-task-control.md) | Stepping into work under way: words kept until they reach the task, verified stop, take over and give back in the director, work continued after a restart (2026-10-06) |
| [115-push-to-talk-coordinator.md](115-push-to-talk-coordinator.md) | Push-to-talk on the call's voice coordinator (press mode): work starts only from the user's words, results are said out of band (2026-10-06) |
| [116-own-network.md](116-own-network.md) | Linux's own network: its own namespace, connections made by pasta as the Rungic app, so Android and VPN apps treat Linux as the app; a Settings service, on by default (2026-10-06) |
| [117-media-backend.md](117-media-backend.md) | The microphone, cameras and phone speaker in an independent root media backend, out of the app's process: a call's audio outlives the app; the app keeps permissions, being in front and Android's call (2026-10-06) |

- [118：独立卸载 Rungic](118-standalone-uninstall.md)：只读预览、默认保留家目录、purge 范围及失败报告。
| [119-app-removal-status.md](119-app-removal-status.md) | 卸载中断后 App 提示与独立标记读取，真机体验待验 |
