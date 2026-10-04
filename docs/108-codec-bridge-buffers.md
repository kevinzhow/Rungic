# 视频硬件解码：V4L2 直通，MediaCodec 桥支持 DMA-BUF 与 10bit

2026-10-04。G100 S（ZY32MVJS25，SM6435 parrot）、Android 16、APK 2.30 → 2.33。起因是 Kevin 让调研高通 Iris 视频驱动能不能用（结论见文末），讨论后决定先优化现有的 MediaCodec 编解码桥（[35 篇](research/35-hardware-codec-integration.md)）：它走 Android 公开接口，换机型也能用，出错由 Codec2 兜底。

## 基线：硬件解码这条路比软件解码还费 CPU

G100 S 容器里用 GStreamer 解码到 fakesink，每项 3 轮，数字稳定（`real` 含 gst-launch 启动）：

| 项目 | 桥（APK 2.30） | 软件 avdec_h264（4 线程） |
|---|---|---|
| 1080p H.264 吞吐 | 约 80 帧/秒；720p 也只有 85–100，卡在每帧固定开销 | 约 240 帧/秒 |
| 300 帧 1080p 总 CPU | 4.8 秒：Linux 0.9 + APK 2.5 + Codec2 服务（media.hwcodec）1.4 | 3.8 秒 |
| 实时播放 1080p60 | 约一个核的 105%：Linux 20% + APK 55% + Codec2 29% | — |

测试片是合成画面（testsrc2），软件解码很轻松；真实视频软件解码重得多，但桥本身的开销是实打实的。

simpleperf（APK 与 media.hwcodec，6 秒，2 kHz）：

- APK 的 `CodecLooper`/`MediaCodec_loop` 线程约 1/4 时间在 `libsfplugin_ccodec_utils.so` 的 `CopyRow_NEON`：ByteBuffer 模式下 Codec2 框架每帧把硬件帧复制成一份普通内存图像。
- 会话线程：`memcpy` 与 `DirectByteBuffer.get` 把三个平面复制进共享内存。半平面的 U、V 是两个重叠视图，色度被复制两遍。`saveParameters` 在 Java 里逐字节扫描整帧码流找 SPS/PPS，约占 9%。
- Linux 端 `rungic_codec_copy_i420` 再逐字节把交错色度拆成 I420。
- Codec2 服务每帧映射、解映射一次缓冲（`qcom_sg_attach`、`unmap_page_range`）。

## 改动

### 协议 v2（`quality/contracts/codec.json`）

- 消费者先发 `MAGIC2` 加选项：`OPTION_BUFFERS`（解码帧留在解码器自己的缓冲里）、`OPTION_TEN_BIT`（10bit 输出 P010）。旧 APK 回 `ERROR`（Channel version），`codec-client` 换 v1 重开，进程内记住，之后直接用 v1。
- 每条记录多 5 个字：三个平面偏移、缓冲槽号（-1 表示共享内存）、位深。某个槽第一次出现时，记录第一个字节带上该缓冲的 DMA-BUF（SCM_RIGHTS）；之后只发槽号。槽号只增不减，换缓冲组就换新号。
- APK 持有这一帧，直到 Linux 回 ACK。语义和以前一样，同一时间只有一帧在路上。

### APK（2.33）

- 解码器输出到 `ImageReader`（`YUV_420_888`，10bit 用 `YCBCR_P010`），`releaseOutputBuffer(index,true)` 渲染后按时间戳取回 Image。配置里加高通的 `vendor.qti-ext-dec-forceNonUBWC.value=1`：渲染到 Surface 时高通解码器默认写 UBWC 压缩格式，CPU 读不了。
- ImageReader 不要 CPU 用途：有 CPU 用途时，gralloc 会在 Codec2 每次交回缓冲时都映射一遍，Linux 自己映射一次就够了。
- 新的 JNI 库 `librungicmedia.so`（`android/app/jni/media/buffers.c`）：从 HardwareBuffer 取 DMA-BUF（`AHardwareBuffer_getNativeHandle`），每个缓冲取一次布局，带描述符写 socket。
  - 高通线性解码格式（`0x7fa30c04` NV12_VENUS、`0x7fa30c0a` P010_VENUS）在 NDK 里只显示成一个平面，按 msm_media_info 的规则计算：行距取 `AHardwareBuffer_Desc.stride`，Y 行数按 32 对齐，CbCr 紧跟其后。
  - 其他格式用 `AHardwareBuffer_lockPlanes` 读取；读不了（例如 UBWC）就报 IOException，Linux 记住后改用共享内存。
  - **不能调用 `Image.getPlanes()`**：缓冲是 UBWC 时，框架会在 JNI 里直接 abort（`NewDirectByteBuffer` 收到空指针），整个 APK 连同桌面一起崩溃。开发时实际发生过一次。
- 输入缓冲大小按分辨率设置（原来是 16 MiB）。Codec2 每帧都要映射、解映射一次输入块，16 MiB 的块光是解映射就占 APK 解码线程约 1/5。
- 码流参数扫描到第一个图像片段就停；通道的读写加了缓冲。
- 没有 `librungicmedia.so` 时退回共享内存。编码端没有改动。

### Linux

- `codec-client.c`：v2 协商与回退；按通道保存槽（mmap 一次，读之前和读之后各做一次 `DMA_BUF_IOCTL_SYNC`）。`RungicCodec`、`RungicCodecConfig` 的布局不变，新字段加在 `RungicCodecFrame` 末尾，私有 FFmpeg 不用重编也能用新库。新增 `rungic_codec_open_options`、`rungic_codec_copy_nv12`、`rungic_codec_copy_p010`。
- 默认打开缓冲模式；Firefox 的预加载（`RUNGIC_CODEC_PRECONNECT`）暂不打开，因为 RDD 沙箱里 DMA-BUF 的 sync ioctl 还没验证过。可以用 `RUNGIC_CODEC_BUFFERS=0/1` 覆盖。
- GStreamer 解码器默认输出 NV12（下游不接受时回到 I420），10bit 码流（h265 `main-10`、VP9 profile 2）输出 `P010_10LE`。

## 实机结果

G100 S 装 APK 2.33（`adb install -r`，数据保留），Linux 端用 `rungic-codec` 开发覆盖。

**正确性**：GStreamer 解码后写出原始帧，和 FFmpeg 软解比 md5，以下全部逐字节一致：
- H.264：720p、1080p 带 3 个 B 帧、1080p60 300 帧；
- HEVC 1080p；
- VP9 1080p；
- HEVC Main10 1080p（P010）；
- 强制下游只接 I420 的情况。

旧 APK（2.30）配新 Linux 端时自动退回 v1，8bit 格式同样一致；10bit 明确报错，播放器会改用软件解码。

**性能**（容器里 GStreamer 解码到 fakesink，各 3 轮中位数）：

| | APK 2.30 + 旧 Linux 端 | APK 2.33 + 新 Linux 端 |
|---|---|---|
| 1080p H.264 吞吐 | 82 帧/秒 | 83 帧/秒 |
| 720p H.264 吞吐 | 85 帧/秒 | 103 帧/秒 |
| 300 帧 1080p 总 CPU（Linux + APK + Codec2） | 0.90 + 2.50 + 1.40 = 4.8 秒 | 0.88 + 2.23 + 1.55 = 4.7 秒 |
| 实时 1080p60 单核占用 | 20% + 55% + 29% | 22% + 49% + 31% |
| HEVC Main10 1080p | 不支持 | 75 帧/秒 |

**复制去掉了，CPU 却只少了一点。** 改后重新 profile，大头是 MediaCodec/Codec2 框架本身每帧的开销：
- `ALooper` 消息投递、binder 回调（`onInputBuffersReleased` 等）、`renderOutputBuffer`、ImageReader 释放缓冲；
- 我们自己的同步轮询：`dequeueInputBuffer(1 ms)`、`dequeueOutputBuffer(2 ms)` 每次都是对 MediaCodec 线程的一次同步往返。

吞吐卡在约 82 帧/秒，因为整个协议是一帧一帧同步往返的：Linux 发一帧，等 APK 回 DONE 才发下一帧。

### 异步 MediaCodec

会话改为等待 MediaCodec 的回调（空闲输入、输出、错误都放进同一个队列），不再用 1–2 ms 超时轮询；一次交换在帧排进解码器后立即返回，解码器等待输出时照样把输出交给 Linux。正确性同上全部一致，但**吞吐量和 CPU 都没变**：1080p 仍约 83 帧/秒，实时 1080p60 是 Linux 20–23% + APK 47–51% + Codec2 31%。

### 与原厂 V4L2 直通对比

同一块硬件、同一段 300 帧 1080p H.264：

| 路径 | 总 CPU | 吞吐 |
|---|---|---|
| MediaCodec 桥（本篇全部改动之后） | 约 4.7 秒（Linux 0.9 + APK 2.2 + Codec2 服务 1.6） | 约 83 帧/秒 |
| 原厂 V4L2 直通（Android 侧 root 测试程序，含逐帧复制出 NV12） | 约 0.85 秒（user 0.57 + sys 0.28） | 约 268 帧/秒 |

- **CPU**：每帧约 12 ms 花在 Codec2 框架和高通编解码服务进程里，我们这边能去掉的复制、轮询、过大的输入块都已经去掉了。
- **吞吐**：83 帧/秒是 Codec2 按码流帧率给解码器设的时钟；V4L2 直通不设帧率，驱动按最高负载跑。实时播放够用，但吞吐由框架决定。

**结论**：要在性能上有数量级的提升，只能让 Linux 绕开 Codec2，直接用 msm_vidc 的 V4L2 接口，MediaCodec 桥（本篇的 v2）作为其他机型和失败时的回退。代价见文末附录：只收 DMABUF、调用顺序必须固定（错了会让固件复位）、要把设备节点映射进 LXC。

## V4L2 直通后端（`shared/media/codec-v4l2.c`）

Kevin 定了方向（2026-10-04）：解码绕开 Codec2，`librungiccodec` 直接用 msm_vidc 的 V4L2 解码器，MediaCodec 桥作为回退。GStreamer 和私有 FFmpeg 都经过同一个 `codec-client`，上层不用改。

- **选择**：解码器（非编码器）在 `/dev` 里找 QUERYCAP 名为 `msm_vidc_decoder`、OUTPUT 格式里有该编码的节点。找不到、打不开或启动失败，就走 MediaCodec 桥。`RUNGIC_CODEC_V4L2=0` 关闭；Firefox 预加载（`RUNGIC_CODEC_PRECONNECT`）下也不用，因为沙箱打不开设备，除非设 `=1`。
- **调用顺序**（固件只认这一种）：
  1. S_FMT OUTPUT，REQBUFS（DMABUF），从 `/dev/dma_heap/system` 分配并映射，订阅 SOURCE_CHANGE，STREAMON OUTPUT。
  2. 只排入一个码流单元。之后到来的单元先留在进程里，直到 SOURCE_CHANGE 出现。
  3. S_FMT CAPTURE 设为 NV12 或 P010，G_SELECTION 取可见区域，按 MIN_BUFFERS_FOR_CAPTURE 再加 4 个缓冲，STREAMON CAPTURE，再排入留着的单元。
  4. 排空用 `V4L2_DEC_CMD_STOP`，一直取到带 LAST 标志的缓冲。
- **刷新**（seek）：整段关掉，在同一个描述符号上重开（`dup3`，因为消费者持有这个号），并把保存的 SPS/PPS/VPS 放在下一个单元前面。不在会话中途重启流。中途换分辨率直接报错，由消费者按新 caps 重开。
- **帧**：时间戳原样带进带出，用它对回帧号。帧直接放在 CAPTURE 缓冲里，读之前和读之后各做一次 `DMA_BUF_IOCTL_SYNC`，消费者用 `rungic_codec_copy_nv12/p010/i420` 复制出去。
- **容器**：控制器在 `/dev/video32` 存在时，按当前设备号授予 rw，并在 `plasma.config` 里用 `optional` 绑定；DMA 堆原本就为 GPU 映射了。没有这个节点的手机照常启动。
- **离线测试**（`tools/tests/test_codec_v4l2.py`）：一个假的 msm_vidc 设备（`codec_v4l2_driver.c`，用 `--wrap` 截获 open、ioctl、poll、close、dup3）。它按真驱动的规则把违规记成错误：
  - 非 DMABUF；
  - OUTPUT 开流前就排入码流单元；
  - CAPTURE 配好之前排入第二个单元；
  - 在 SOURCE_CHANGE 之前配置 CAPTURE。

  SOURCE_CHANGE 要第三次查询才出现，模拟真驱动的延迟。把“只排一个单元”这条去掉的变体，测试会报 5 次违规。

**G100 S 实测**（控制器与 LXC 配置已更新，重启了一次容器；GStreamer 自动选中 V4L2，logcat 中没有 MediaCodec 会话）：

| | MediaCodec 桥（APK 2.30） | V4L2 直通 |
|---|---|---|
| 实时 1080p60，单核占用合计 | 约 105%（Linux 20 + APK 55 + Codec2 29） | **约 23%**（Linux 22 + APK 1） |
| 300 帧 1080p 解码总 CPU | 4.8 秒 | **0.9 秒** |
| 1080p H.264 吞吐（含 gst-launch 启动） | 82 帧/秒 | **239 帧/秒** |
| 1080p HEVC / VP9 / HEVC Main10 | 77 / 81 / 不支持 | **183 / 189 / 153 帧/秒** |

- 正确性：全部测试片与 FFmpeg 软解逐字节一致，整轮没有出现固件错误。
- 剩下的 Linux 侧约 22%，是 GStreamer 本身加上把帧复制给下游的开销。下一步把 CAPTURE 缓冲作为 GStreamer 的 dmabuf 内存直接交出去，做到零复制。

## V4L2 编码（`/dev/video33`）

同一个后端加上编码器。录屏（`desktop/recording/recorder.py` 里的 `rungich264enc`）、Snapshot 相机录像、私有 FFmpeg 的编码器都经过 `codec-client`，自动用上。

- **调用顺序**（G100 S 上试出来的）：
  1. S_FMT CAPTURE（H.264/HEVC）→ S_FMT OUTPUT（NV12；驱动返回行距和对齐后的高度）→ 再取一次 CAPTURE 的 sizeimage，它会随图像尺寸更新；
  2. S_PARM 帧率；控制项：VBR、码率、GOP = 关键帧间隔 × 帧率、无 B 帧、H.264 Constrained Baseline（WebRTC 需要）或 HEVC Main、IDR 前带 SPS/PPS；
  3. 两路 REQBUFS（DMABUF）→ **先 STREAMON OUTPUT，再 STREAMON CAPTURE**，然后才排入码流缓冲。

  第一次试时先排码流缓冲、再开两路流，在送入第一张图时触发了 `venus_c2_parsing.c:1231` 断言，和解码那次同一类（固件找不到会话），视频核心被复位一次，当时 Android 没有在用编解码器。改成上面的顺序后正常。
- **输入**：消费者给的是紧密排列的 I420（GStreamer 元素和私有 FFmpeg 都这样），在这里交错成 NV12 写进 OUTPUT 缓冲。
- **输出**：码流里开头的参数集（H.264 SPS/PPS，HEVC VPS/SPS/PPS）拆出来，有变化时作为 CONFIG 记录给出，帧数据作为 ENCODED，关键帧带标志，和 MediaCodec 桥的约定一致。强制关键帧用 `V4L2_CID_MPEG_VIDEO_FORCE_KEY_FRAME`，排空用 `V4L2_ENC_CMD_STOP`。
- **测试替身**：把"开流顺序"和"两路都开流才能排缓冲"记成违规；输出的每帧带上该图第一个 Y、Cb、Cr 字节，用来检查 I420→NV12 的交错。先开 CAPTURE 的变体会被拦下。

**G100 S 实测**（容器多映射了 `/dev/video33`，又重启了一次容器）：1080×2400@30 的 I420 测试画面实时送 10 秒，2 轮中位数：

| | MediaCodec 桥 | V4L2 直通 |
|---|---|---|
| 实时 30 帧 | 跟不上：300 帧用了 12 秒（约 25 帧/秒） | 跟上；满速约 53 帧/秒 |
| 编码段 CPU（减去测试画面源） | APK 7.3 秒 + Codec2 1.5 秒，再加 Linux 侧复制 | 约 0.24 秒（约一个核的 2–3%） |

- 两边都是 300 帧，2 秒一个关键帧，互相比 PSNR 约 74 dB。
- 验收 `recording.quicksetting`（从快捷设置录屏，生成带视频和 AAC 音轨的 MP4）和 `codec.hw` 都通过；期间 logcat 中没有 MediaCodec 会话。
- 剩下的录屏开销主要在把屏幕画面转成 I420。下一步让 PipeWire 的 dmabuf 直接进编码器。

## 附：Iris 驱动与原厂 V4L2 直通

- G100 S 的视频硬件就是 Iris 这一代：设备树 `qcom,msm-vidc-parrot qcom,msm-vidc-iris2`，固件 `vpu20_1v.mbn`（VPU2 单管线），由原厂 `msm_video.ko` 驱动，导出 `/dev/video32`（解码）、`/dev/video33`（编码）。
- 上游 Iris（主线 7.3-rc5）支持 sm8250、sc7280、sm8550、sm8650、sm8750、qcs8300、x1p42100、milos，没有 parrot，也没有 SM8845。直接装不上：内核、设备树格式都对不上，而且硬件同一时间只能归一个驱动，换掉 `msm_video` 会让 Android 的 `c2.qti.*` 全部失效。
- 原厂驱动本身就是标准的有状态 V4L2 解码器，测试程序（Android 侧 root，NDK 编译）在 G100 S 上验证：H.264（含 B 帧、854×480、720×1280、2560×1440）、HEVC Main/Main10、VP9 Profile 0（含 superframe）/2 全部与 FFmpeg 软解逐字节一致；1080p 约 170–210 帧/秒（含写文件），硬件上限 2560×1440。
- 限制：只接受 DMABUF（`/dev/dma_heap/system`），MMAP/USERPTR 一律 EINVAL，所以现成的 FFmpeg `v4l2m2m`、GStreamer `v4l2` 解码器都不能直接用；必须先开 OUTPUT 流、喂一帧、等 `SOURCE_CHANGE`，再配 NV12 的 CAPTURE（默认是 UBWC 的 Q12C）。第一次把 QBUF 放在 STREAMON 之前，触发了固件断言（`venus_c2_parsing.c`），驱动强制复位了视频核心；之后能自行恢复，但 Android 正在进行的解码会被打断。
- 结论：后来用作默认解码路线，见上文“V4L2 直通后端”。
