// rungic-workspace-present: a headless workspace's picture on the Android host (docs/research/97 §13).
//
// A headless workspace (KWin's virtual backend) has no surface on the Android host, so the TV and
// the phone's fullscreen had only a placeholder for it. While one of them shows the workspace, its
// keeper runs this helper in the workspace's environment:
//
//   - the picture: rungic-workspace-stream (a child) records the workspace's output (zkde_screencast,
//     the pointer drawn in) and names its PipeWire node; KPipeWire receives its frames (DMA-BUF);
//   - each frame is drawn by the GPU into a buffer leased from the host (an AHardwareBuffer,
//     /mnt/android-wayland/rungic-gpu-alloc) and committed on the host's ws-N socket as the
//     workspace's fullscreen window, with its GPU fence (explicit sync): the host presents leased
//     buffers zero-copy, as it does a workspace KWin's own (docs/57);
//   - the host's pointer (TV, fullscreen touches) goes back into the workspace through the child.
//
// A frame is drawn only when the host asked for one (a frame callback) and a leased buffer is free:
// a frozen host (the phone asleep) only stops this helper's frames; the workspace's KWin never waits
// for anything here. The helper ends when its stdin closes (the keeper), the host goes or the
// picture does.
#include <QDateTime>
#include <QGuiApplication>
#include <QOffscreenSurface>
#include <QOpenGLContext>
#include <QProcess>
#include <QSurfaceFormat>
#include <QSocketNotifier>
#include <QTimer>

#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <GLES2/gl2ext.h>
#include <drm_fourcc.h>
#include <poll.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <cstring>
#include <iostream>
#include <vector>

#include <pipewiresourcestream.h>

#include "linux-dmabuf-unstable-v1-client-protocol.h"
#include "linux-explicit-synchronization-unstable-v1-client-protocol.h"
#include "xdg-shell-client-protocol.h"
#include <wayland-client.h>
#include "wayland-ready.h"

namespace
{
constexpr int kWidth = 1920, kHeight = 1080;   // the host's workspace output (cast.rs)
constexpr int kBuffers = 3;

void say(const QString &line)
{
    std::cerr << "present: " << line.toStdString() << std::endl;
}

// ---- a buffer leased from the host (docs/57; KWin's android backend allocates the same way) ----
struct Lease {
    int socket = -1;  // the lease lives while this stays open
    int fd = -1;
    uint32_t stride = 0;
};

bool lease(Lease &out)
{
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    std::strcpy(address.sun_path, "/mnt/android-wayland/rungic-gpu-alloc");
    const int s = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (s < 0)
        return false;
    timeval timeout{.tv_sec = 3, .tv_usec = 0};
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    if (connect(s, reinterpret_cast<sockaddr *>(&address), sizeof(address)) < 0) {
        close(s);
        return false;
    }
    // v1: 'UPGM', width, height, fourcc; LINEAR. Only XBGR/ABGR8888 (Android's public formats).
    const uint32_t request[]{0x4d475055, uint32_t(kWidth), uint32_t(kHeight), DRM_FORMAT_XBGR8888};
    if (send(s, request, sizeof(request), MSG_NOSIGNAL) != ssize_t(sizeof(request))) {
        close(s);
        return false;
    }
    uint32_t reply[3]{};
    iovec iov{.iov_base = reply, .iov_len = sizeof(reply)};
    alignas(cmsghdr) char control[CMSG_SPACE(4 * sizeof(int))]{};
    msghdr message{};
    message.msg_iov = &iov;
    message.msg_iovlen = 1;
    message.msg_control = control;
    message.msg_controllen = sizeof(control);
    const ssize_t length = recvmsg(s, &message, MSG_WAITALL | MSG_CMSG_CLOEXEC);
    int fd = -1;
    for (cmsghdr *c = CMSG_FIRSTHDR(&message); c; c = CMSG_NXTHDR(&message, c)) {
        if (c->cmsg_level == SOL_SOCKET && c->cmsg_type == SCM_RIGHTS && c->cmsg_len >= CMSG_LEN(sizeof(int)))
            std::memcpy(&fd, CMSG_DATA(c), sizeof(int));
    }
    if (length != ssize_t(sizeof(reply)) || fd < 0 || reply[0] != 0x4d475055 || reply[2] != DRM_FORMAT_XBGR8888
        || reply[1] < uint32_t(kWidth) * 4 || reply[1] > 65536) {
        if (fd >= 0)
            close(fd);
        close(s);
        return false;
    }
    out = {s, fd, reply[1]};
    return true;
}

// ---- GL: one textured quad from the frame's DMA-BUF into a leased buffer ----
// Rows as they lie in memory on both sides (the frame's first row is the leased buffer's first): no flip.
const char *kVertex = "attribute vec2 p; varying vec2 t; void main() { t = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }";
const char *kFragment = "#extension GL_OES_EGL_image_external : require\n"
                        "precision mediump float; varying vec2 t; uniform samplerExternalOES s; void main() { gl_FragColor = vec4(texture2D(s, t).rgb, 1.0); }";

PFNEGLCREATEIMAGEKHRPROC createImage;
PFNEGLDESTROYIMAGEKHRPROC destroyImage;
PFNGLEGLIMAGETARGETTEXTURE2DOESPROC imageTarget;
PFNEGLCREATESYNCKHRPROC createSync;
PFNEGLDESTROYSYNCKHRPROC destroySync;
PFNEGLDUPNATIVEFENCEFDANDROIDPROC dupFence;

EGLImageKHR importDmabuf(EGLDisplay display, int width, int height, uint32_t format, uint64_t modifier, int fd, uint32_t offset, uint32_t stride)
{
    std::vector<EGLint> attributes{EGL_WIDTH, width, EGL_HEIGHT, height, EGL_LINUX_DRM_FOURCC_EXT, EGLint(format),
                                   EGL_DMA_BUF_PLANE0_FD_EXT, fd, EGL_DMA_BUF_PLANE0_OFFSET_EXT, EGLint(offset),
                                   EGL_DMA_BUF_PLANE0_PITCH_EXT, EGLint(stride)};
    if (modifier != DRM_FORMAT_MOD_INVALID) {
        attributes.insert(attributes.end(), {EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, EGLint(modifier & 0xffffffff),
                                             EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, EGLint(modifier >> 32)});
    }
    attributes.push_back(EGL_NONE);
    return createImage(display, EGL_NO_CONTEXT, EGL_LINUX_DMA_BUF_EXT, nullptr, attributes.data());
}

GLuint compile(GLenum type, const char *source)
{
    const GLuint shader = glCreateShader(type);
    glShaderSource(shader, 1, &source, nullptr);
    glCompileShader(shader);
    GLint ok = 0;
    glGetShaderiv(shader, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512]{};
        glGetShaderInfoLog(shader, sizeof(log), nullptr, log);
        say(QStringLiteral("shader: %1").arg(QString::fromUtf8(log)));
    }
    return shader;
}

// ---- the host: a Wayland client on /mnt/android-wayland/ws-N ----
struct Host;

struct Slot {
    Lease lease;
    wl_buffer *buffer = nullptr;
    EGLImageKHR image = EGL_NO_IMAGE_KHR;
    GLuint texture = 0, framebuffer = 0;
    bool busy = false;  // committed, not released by the host yet
};

struct Host {
    wl_display *display = nullptr;
    wl_registry *registry = nullptr;
    wl_compositor *compositor = nullptr;
    xdg_wm_base *shell = nullptr;
    zwp_linux_dmabuf_v1 *dmabuf = nullptr;
    zwp_linux_explicit_synchronization_v1 *explicitSync = nullptr;
    wl_seat *seat = nullptr;
    wl_pointer *pointer = nullptr;
    std::vector<std::pair<wl_output *, bool>> outputs;  // bool: the host's workspace output (make "Rungic")
    wl_surface *surface = nullptr;
    xdg_surface *xdgSurface = nullptr;
    xdg_toplevel *toplevel = nullptr;
    zwp_linux_surface_synchronization_v1 *surfaceSync = nullptr;
    wl_callback *frame = nullptr;
    bool configured = false;
    bool wantsFrame = true;  // a frame callback came (or none was asked yet)
    Slot pool[kBuffers];
    QProcess *input = nullptr;  // the child that also takes input lines
    double pointerX = 0, pointerY = 0;
};

void outputGeometry(void *, wl_output *, int32_t, int32_t, int32_t, int32_t, int32_t, const char *, const char *, int32_t) {}
void outputMode(void *, wl_output *, uint32_t, int32_t, int32_t, int32_t) {}
void outputDone(void *, wl_output *) {}
void outputScale(void *, wl_output *, int32_t) {}
void outputName(void *, wl_output *, const char *) {}
void outputDescription(void *, wl_output *, const char *) {}
const wl_output_listener kOutputListener{
    .geometry = [](void *data, wl_output *output, int32_t, int32_t, int32_t, int32_t, int32_t, const char *make, const char *, int32_t) {
        auto host = static_cast<Host *>(data);
        for (auto &[o, ours] : host->outputs)
            if (o == output)
                ours = make && std::strcmp(make, "Rungic") == 0;
    },
    .mode = outputMode, .done = outputDone, .scale = outputScale, .name = outputName, .description = outputDescription};

void sendInput(Host *host, const QByteArray &line)
{
    if (host->input && host->input->state() == QProcess::Running)
        host->input->write(line + '\n');
}

const wl_pointer_listener kPointerListener{
    .enter = [](void *data, wl_pointer *, uint32_t, wl_surface *, wl_fixed_t x, wl_fixed_t y) {
        auto host = static_cast<Host *>(data);
        host->pointerX = wl_fixed_to_double(x);
        host->pointerY = wl_fixed_to_double(y);
    },
    .leave = [](void *, wl_pointer *, uint32_t, wl_surface *) {},
    .motion = [](void *data, wl_pointer *, uint32_t, wl_fixed_t x, wl_fixed_t y) {
        auto host = static_cast<Host *>(data);
        host->pointerX = wl_fixed_to_double(x);
        host->pointerY = wl_fixed_to_double(y);
        sendInput(host, QByteArray("pointer ") + QByteArray::number(host->pointerX / kWidth, 'f', 5) + ' '
                            + QByteArray::number(host->pointerY / kHeight, 'f', 5));
    },
    .button = [](void *data, wl_pointer *, uint32_t, uint32_t, uint32_t button, uint32_t state) {
        sendInput(static_cast<Host *>(data), QByteArray("button ") + QByteArray::number(button) + ' ' + QByteArray::number(state));
    },
    .axis = [](void *data, wl_pointer *, uint32_t, uint32_t axis, wl_fixed_t value) {
        sendInput(static_cast<Host *>(data), QByteArray("axis ") + QByteArray::number(axis) + ' '
                                                 + QByteArray::number(wl_fixed_to_double(value), 'f', 3));
    },
    .frame = [](void *, wl_pointer *) {},
    .axis_source = [](void *, wl_pointer *, uint32_t) {},
    .axis_stop = [](void *, wl_pointer *, uint32_t, uint32_t) {},
    .axis_discrete = [](void *, wl_pointer *, uint32_t, int32_t) {},
    .axis_value120 = [](void *, wl_pointer *, uint32_t, int32_t) {},
    .axis_relative_direction = [](void *, wl_pointer *, uint32_t, uint32_t) {}};

const wl_seat_listener kSeatListener{
    .capabilities = [](void *data, wl_seat *seat, uint32_t caps) {
        auto host = static_cast<Host *>(data);
        if ((caps & WL_SEAT_CAPABILITY_POINTER) && !host->pointer) {
            host->pointer = wl_seat_get_pointer(seat);
            wl_pointer_add_listener(host->pointer, &kPointerListener, host);
        }
    },
    .name = [](void *, wl_seat *, const char *) {}};

const xdg_wm_base_listener kShellListener{.ping = [](void *, xdg_wm_base *shell, uint32_t serial) { xdg_wm_base_pong(shell, serial); }};

const wl_registry_listener kRegistryListener{
    .global = [](void *data, wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
        auto host = static_cast<Host *>(data);
        if (!std::strcmp(interface, wl_compositor_interface.name)) {
            host->compositor = static_cast<wl_compositor *>(wl_registry_bind(registry, name, &wl_compositor_interface, std::min(version, 4u)));
        } else if (!std::strcmp(interface, xdg_wm_base_interface.name)) {
            host->shell = static_cast<xdg_wm_base *>(wl_registry_bind(registry, name, &xdg_wm_base_interface, 1));
            xdg_wm_base_add_listener(host->shell, &kShellListener, host);
        } else if (!std::strcmp(interface, zwp_linux_dmabuf_v1_interface.name)) {
            host->dmabuf = static_cast<zwp_linux_dmabuf_v1 *>(wl_registry_bind(registry, name, &zwp_linux_dmabuf_v1_interface, std::min(version, 3u)));
        } else if (!std::strcmp(interface, zwp_linux_explicit_synchronization_v1_interface.name)) {
            host->explicitSync = static_cast<zwp_linux_explicit_synchronization_v1 *>(
                wl_registry_bind(registry, name, &zwp_linux_explicit_synchronization_v1_interface, std::min(version, 2u)));
        } else if (!std::strcmp(interface, wl_output_interface.name)) {
            auto output = static_cast<wl_output *>(wl_registry_bind(registry, name, &wl_output_interface, std::min(version, 2u)));
            host->outputs.push_back({output, false});
            wl_output_add_listener(output, &kOutputListener, host);
        } else if (!std::strcmp(interface, wl_seat_interface.name) && !host->seat) {
            host->seat = static_cast<wl_seat *>(wl_registry_bind(registry, name, &wl_seat_interface, std::min(version, 5u)));
            wl_seat_add_listener(host->seat, &kSeatListener, host);
        }
    },
    .global_remove = [](void *, wl_registry *, uint32_t) {}};

const xdg_surface_listener kXdgSurfaceListener{.configure = [](void *data, xdg_surface *surface, uint32_t serial) {
    xdg_surface_ack_configure(surface, serial);
    static_cast<Host *>(data)->configured = true;
}};
const xdg_toplevel_listener kToplevelListener{
    .configure = [](void *, xdg_toplevel *, int32_t, int32_t, wl_array *) {},
    .close = [](void *, xdg_toplevel *) { QCoreApplication::exit(0); },
    .configure_bounds = [](void *, xdg_toplevel *, int32_t, int32_t) {},
    .wm_capabilities = [](void *, xdg_toplevel *, wl_array *) {}};

const wl_buffer_listener kBufferListener{.release = [](void *data, wl_buffer *) { static_cast<Slot *>(data)->busy = false; }};

const wl_callback_listener kFrameListener{.done = [](void *data, wl_callback *callback, uint32_t) {
    auto host = static_cast<Host *>(data);
    wl_callback_destroy(callback);
    host->frame = nullptr;
    host->wantsFrame = true;
}};

// The first roundtrips with a bound: a frozen host takes the connection and never answers.
bool roundtrip(wl_display *display, int timeoutMs)
{
    return rungic::waylandRoundtrip(display, rungic::Clock::now() + std::chrono::milliseconds(timeoutMs));
}
} // namespace

int main(int argc, char *argv[])
{
    qputenv("QT_QPA_PLATFORM", "wayland");
    QGuiApplication app(argc, argv);
    const QByteArray slot = argc > 1 ? QByteArray(argv[1]) : qgetenv("RUNGIC_WORKSPACE");
    if (slot.isEmpty()) {
        say(QStringLiteral("usage: rungic-workspace-present N"));
        return 2;
    }

    // ---- the host ----
    Host host;
    host.display = wl_display_connect(("/mnt/android-wayland/ws-" + slot).constData());
    if (!host.display) {
        say(QStringLiteral("the host does not offer ws-%1").arg(QString::fromLatin1(slot)));
        return 1;
    }
    host.registry = wl_display_get_registry(host.display);
    wl_registry_add_listener(host.registry, &kRegistryListener, &host);
    if (!roundtrip(host.display, 3000) || !roundtrip(host.display, 3000)) {
        say(QStringLiteral("the host does not answer (the phone asleep?)"));
        return 1;
    }
    wl_output *output = nullptr;
    for (auto &[o, ours] : host.outputs)
        if (ours)
            output = o;
    if (!host.compositor || !host.shell || !host.dmabuf || !output) {
        say(QStringLiteral("the host lacks compositor, xdg_wm_base, linux-dmabuf or its workspace output"));
        return 1;
    }

    // ---- GL on the workspace's EGL display (KPipeWire imports with the same) ----
    // OpenGL ES: the external-image sampler (GL_OES_EGL_image_external) is an ES extension; Qt's
    // default on Mesa is desktop OpenGL.
    QSurfaceFormat format;
    format.setRenderableType(QSurfaceFormat::OpenGLES);
    format.setVersion(3, 0);
    QOpenGLContext context;
    context.setFormat(format);
    if (!context.create() || !context.isOpenGLES()) {
        say(QStringLiteral("no OpenGL ES context"));
        return 1;
    }
    QOffscreenSurface offscreen;
    offscreen.setFormat(context.format());
    offscreen.create();
    context.makeCurrent(&offscreen);
    const EGLDisplay egl = eglGetCurrentDisplay();
    createImage = reinterpret_cast<PFNEGLCREATEIMAGEKHRPROC>(eglGetProcAddress("eglCreateImageKHR"));
    destroyImage = reinterpret_cast<PFNEGLDESTROYIMAGEKHRPROC>(eglGetProcAddress("eglDestroyImageKHR"));
    imageTarget = reinterpret_cast<PFNGLEGLIMAGETARGETTEXTURE2DOESPROC>(eglGetProcAddress("glEGLImageTargetTexture2DOES"));
    createSync = reinterpret_cast<PFNEGLCREATESYNCKHRPROC>(eglGetProcAddress("eglCreateSyncKHR"));
    destroySync = reinterpret_cast<PFNEGLDESTROYSYNCKHRPROC>(eglGetProcAddress("eglDestroySyncKHR"));
    dupFence = reinterpret_cast<PFNEGLDUPNATIVEFENCEFDANDROIDPROC>(eglGetProcAddress("eglDupNativeFenceFDANDROID"));
    if (!createImage || !destroyImage || !imageTarget) {
        say(QStringLiteral("EGL lacks dma-buf import"));
        return 1;
    }
    const bool fences = createSync && dupFence && host.explicitSync;

    const GLuint program = glCreateProgram();
    glAttachShader(program, compile(GL_VERTEX_SHADER, kVertex));
    glAttachShader(program, compile(GL_FRAGMENT_SHADER, kFragment));
    glBindAttribLocation(program, 0, "p");
    glLinkProgram(program);
    GLint linked = 0;
    glGetProgramiv(program, GL_LINK_STATUS, &linked);
    if (!linked) {
        say(QStringLiteral("the copy's shader does not link"));
        return 1;
    }
    const GLfloat quad[]{-1, -1, 1, -1, -1, 1, 1, 1};

    // ---- the leased buffers, as GL framebuffers and as the host's wl_buffers ----
    for (Slot &s : host.pool) {
        if (!lease(s.lease)) {
            say(QStringLiteral("no buffer from the host's allocator"));
            return 1;
        }
        zwp_linux_buffer_params_v1 *params = zwp_linux_dmabuf_v1_create_params(host.dmabuf);
        zwp_linux_buffer_params_v1_add(params, s.lease.fd, 0, 0, s.lease.stride, DRM_FORMAT_MOD_LINEAR >> 32, DRM_FORMAT_MOD_LINEAR & 0xffffffff);
        s.buffer = zwp_linux_buffer_params_v1_create_immed(params, kWidth, kHeight, DRM_FORMAT_XBGR8888, 0);
        zwp_linux_buffer_params_v1_destroy(params);
        wl_buffer_add_listener(s.buffer, &kBufferListener, &s);
        s.image = importDmabuf(egl, kWidth, kHeight, DRM_FORMAT_XBGR8888, DRM_FORMAT_MOD_LINEAR, s.lease.fd, 0, s.lease.stride);
        if (s.image == EGL_NO_IMAGE_KHR) {
            say(QStringLiteral("cannot import a leased buffer: EGL error 0x%1").arg(eglGetError(), 0, 16));
            return 1;
        }
        glGenTextures(1, &s.texture);
        glBindTexture(GL_TEXTURE_2D, s.texture);
        imageTarget(GL_TEXTURE_2D, s.image);
        glGenFramebuffers(1, &s.framebuffer);
        glBindFramebuffer(GL_FRAMEBUFFER, s.framebuffer);
        glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, s.texture, 0);
        if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
            say(QStringLiteral("a leased buffer is not renderable"));
            return 1;
        }
    }

    // ---- the workspace's fullscreen window on the host ----
    host.surface = wl_compositor_create_surface(host.compositor);
    host.xdgSurface = xdg_wm_base_get_xdg_surface(host.shell, host.surface);
    xdg_surface_add_listener(host.xdgSurface, &kXdgSurfaceListener, &host);
    host.toplevel = xdg_surface_get_toplevel(host.xdgSurface);
    xdg_toplevel_add_listener(host.toplevel, &kToplevelListener, &host);
    xdg_toplevel_set_title(host.toplevel, "Agent workspace");
    xdg_toplevel_set_fullscreen(host.toplevel, output);
    if (fences)
        host.surfaceSync = zwp_linux_explicit_synchronization_v1_get_synchronization(host.explicitSync, host.surface);
    wl_surface_commit(host.surface);
    if (!roundtrip(host.display, 3000) || !host.configured) {
        say(QStringLiteral("the host did not configure the window"));
        return 1;
    }

    // Host events, read as they come; a dead host ends the helper.
    QSocketNotifier hostEvents(wl_display_get_fd(host.display), QSocketNotifier::Read);
    auto pump = [&] {
        while (wl_display_prepare_read(host.display) != 0)
            wl_display_dispatch_pending(host.display);
        pollfd p{.fd = wl_display_get_fd(host.display), .events = POLLIN};
        if (poll(&p, 1, 0) > 0) {
            if (wl_display_read_events(host.display) < 0) {
                say(QStringLiteral("the host went away"));
                QCoreApplication::exit(1);
                return;
            }
        } else {
            wl_display_cancel_read(host.display);
        }
        if (wl_display_dispatch_pending(host.display) < 0) {
            say(QStringLiteral("the host connection failed"));
            QCoreApplication::exit(1);
        }
    };
    QObject::connect(&hostEvents, &QSocketNotifier::activated, &app, pump);
    // Writes never block: what does not fit waits for the socket (a frozen host: no more frames).
    QSocketNotifier hostWritable(wl_display_get_fd(host.display), QSocketNotifier::Write);
    hostWritable.setEnabled(false);
    auto flush = [&] {
        const int r = wl_display_flush(host.display);
        hostWritable.setEnabled(r < 0 && errno == EAGAIN);
    };
    QObject::connect(&hostWritable, &QSocketNotifier::activated, &app, flush);

    // ---- the picture ----
    QProcess picture;
    host.input = &picture;
    picture.setProgram(QStringLiteral("/usr/libexec/rungic-workspace-stream"));
    picture.setProcessChannelMode(QProcess::ForwardedErrorChannel);
    PipeWireSourceStream stream;
    stream.setAllowDmaBuf(true);
    QObject::connect(&picture, &QProcess::readyReadStandardOutput, &app, [&] {
        while (picture.canReadLine()) {
            const QByteArray line = picture.readLine().trimmed();
            if (line.startsWith("node ")) {
                const uint node = line.mid(5).toUInt();
                if (!stream.createStream(node, 0)) {
                    say(QStringLiteral("cannot receive node %1").arg(node));
                    QCoreApplication::exit(1);
                }
            } else if (line.startsWith("error ")) {
                say(QString::fromUtf8(line));
                QCoreApplication::exit(1);
            }
        }
    });
    QObject::connect(&picture, &QProcess::finished, &app, [] { QCoreApplication::exit(1); });

    quint64 presented = 0, dropped = 0;
    QObject::connect(&stream, &PipeWireSourceStream::frameReceived, &app, [&](const PipeWireFrame &frame) {
        if (!frame.dmabuf || frame.dmabuf->planes.isEmpty())
            return;  // a cursor-only update, or memory frames (not asked for)
        Slot *slot = nullptr;
        for (Slot &s : host.pool)
            if (!s.busy && !slot)
                slot = &s;
        if (!host.wantsFrame || !slot) {
            ++dropped;  // the host has not asked for one, or holds every buffer (frozen?)
            return;
        }
        const DmaBufAttributes &d = *frame.dmabuf;
        context.makeCurrent(&offscreen);
        const EGLImageKHR source = importDmabuf(egl, d.width, d.height, d.format, d.modifier, d.planes[0].fd, d.planes[0].offset, d.planes[0].stride);
        if (source == EGL_NO_IMAGE_KHR) {
            ++dropped;
            return;
        }
        GLuint texture;
        glGenTextures(1, &texture);
        glBindTexture(GL_TEXTURE_EXTERNAL_OES, texture);
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        imageTarget(GL_TEXTURE_EXTERNAL_OES, source);
        glBindFramebuffer(GL_FRAMEBUFFER, slot->framebuffer);
        glViewport(0, 0, kWidth, kHeight);
        glUseProgram(program);
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 0, quad);
        glEnableVertexAttribArray(0);
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        int fence = -1;
        if (fences) {
            const EGLSyncKHR sync = createSync(egl, EGL_SYNC_NATIVE_FENCE_ANDROID, nullptr);
            glFlush();
            fence = sync != EGL_NO_SYNC_KHR ? dupFence(egl, sync) : -1;
            if (sync != EGL_NO_SYNC_KHR)
                destroySync(egl, sync);
        }
        if (fence < 0)
            glFinish();
        // The source goes back to KWin when this returns: the GPU must be done with it first.
        if (fence >= 0) {
            pollfd p{.fd = fence, .events = POLLIN};
            poll(&p, 1, 50);
        }
        glDeleteTextures(1, &texture);
        destroyImage(egl, source);

        if (host.surfaceSync && fence >= 0)
            zwp_linux_surface_synchronization_v1_set_acquire_fence(host.surfaceSync, fence);
        if (fence >= 0)
            close(fence);
        wl_surface_attach(host.surface, slot->buffer, 0, 0);
        wl_surface_damage(host.surface, 0, 0, INT32_MAX, INT32_MAX);
        host.frame = wl_surface_frame(host.surface);
        wl_callback_add_listener(host.frame, &kFrameListener, &host);
        wl_surface_commit(host.surface);
        slot->busy = true;
        host.wantsFrame = false;
        ++presented;
        flush();
    });

    // Ends with its stdin (the keeper stopping it).
    QSocketNotifier commands(STDIN_FILENO, QSocketNotifier::Read);
    QObject::connect(&commands, &QSocketNotifier::activated, &app, [&] {
        char buffer[256];
        if (read(STDIN_FILENO, buffer, sizeof buffer) <= 0)
            QCoreApplication::quit();
    });
    QTimer report;
    QObject::connect(&report, &QTimer::timeout, &app, [&] {
        say(QStringLiteral("%1 presented, %2 dropped").arg(presented).arg(dropped));
    });
    report.start(10000);

    picture.start();
    say(QStringLiteral("workspace %1 on the host (%2)").arg(QString::fromLatin1(slot), fences ? QStringLiteral("explicit sync") : QStringLiteral("glFinish")));
    const int code = app.exec();
    picture.closeWriteChannel();
    picture.waitForFinished(2000);
    return code;
}
