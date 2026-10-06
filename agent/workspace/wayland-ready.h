// SPDX-License-Identifier: MIT
// One bounded Wayland sync for startup clients. A listening socket alone is not readiness.
#pragma once
#include <wayland-client.h>
#include <chrono>
#include <algorithm>
#include <cerrno>
#include <poll.h>

namespace rungic {
using Clock = std::chrono::steady_clock;

inline int remainingMs(Clock::time_point deadline)
{
    const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
    return left > 0 ? int(left) : 0;
}

inline bool waylandRoundtrip(wl_display *display, Clock::time_point deadline)
{
    bool done = false;
    wl_callback *callback = wl_display_sync(display);
    if (!callback)
        return false;
    static const wl_callback_listener listener{.done = [](void *data, wl_callback *, uint32_t) {
        *static_cast<bool *>(data) = true;
    }};
    wl_callback_add_listener(callback, &listener, &done);
    while (!done && remainingMs(deadline) > 0) {
        if (wl_display_dispatch_pending(display) < 0)
            break;
        if (done)
            break;
        if (wl_display_prepare_read(display) != 0)
            continue;
        const int flushed = wl_display_flush(display);
        if (flushed < 0 && errno != EAGAIN) {
            wl_display_cancel_read(display);
            break;
        }
        pollfd fd{.fd = wl_display_get_fd(display), .events = short(POLLIN | (flushed < 0 ? POLLOUT : 0)), .revents = 0};
        const int status = poll(&fd, 1, remainingMs(deadline));
        const int error = errno;
        if (status > 0 && (fd.revents & POLLIN)) {
            if (wl_display_read_events(display) < 0)
                break;
        } else {
            wl_display_cancel_read(display);
            if (status < 0 && error == EINTR)
                continue;
            if (status <= 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL)))
                break;
        }
    }
    // Dispatch may have delivered sync at the deadline: retain that valid response.
    if (!done && wl_display_get_error(display) == 0)
        wl_display_dispatch_pending(display);
    wl_callback_destroy(callback);
    return done;
}
} // namespace rungic
