// SPDX-License-Identifier: MIT
// Private workspace bus activation only. Wait for its KWin, then exec the distribution's portal backend.
#include "wayland-ready.h"
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    // Leave time for exec/name ownership before D-Bus's default 25-second activation timeout.
    int timeoutMs = 20000, first = 1;
    if (argc > 2 && std::strcmp(argv[1], "--timeout-ms") == 0) {
        char *end = nullptr;
        const long value = std::strtol(argv[2], &end, 10);
        if (!*argv[2] || *end || value < 1 || value > 20000)
            return 2;
        timeoutMs = int(value);
        first = 3;
    }
    if (first < argc && std::strcmp(argv[first], "--") == 0)
        ++first;
    if (first >= argc) {
        std::cerr << "usage: rungic-workspace-portal [--timeout-ms N] -- BACKEND [ARG...]\n";
        return 2;
    }
    const char *name = std::getenv("WAYLAND_DISPLAY");
    const char *runtime = std::getenv("XDG_RUNTIME_DIR");
    if (!name || !*name || (name[0] != '/' && (!runtime || !*runtime))) {
        std::cerr << "workspace portal: workspace Wayland address is missing\n";
        return 1;
    }
    const std::string path = name[0] == '/' ? name : std::string(runtime) + '/' + name;
    sockaddr_un address{};
    address.sun_family = AF_UNIX;
    if (path.size() >= sizeof(address.sun_path)) {
        std::cerr << "workspace portal: Wayland address is too long\n";
        return 1;
    }
    std::strcpy(address.sun_path, path.c_str());
    const auto deadline = rungic::Clock::now() + std::chrono::milliseconds(timeoutMs);
    wl_display *display = nullptr;
    while (rungic::remainingMs(deadline) > 0) {
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
        if (fd < 0)
            break;
        int status = connect(fd, reinterpret_cast<sockaddr *>(&address), sizeof(address));
        int error = errno;
        if (status < 0 && error == EINPROGRESS) {
            pollfd pending{.fd = fd, .events = POLLOUT, .revents = 0};
            status = poll(&pending, 1, rungic::remainingMs(deadline));
            socklen_t size = sizeof(error);
            if (status > 0 && getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) == 0 && error == 0)
                status = 0;
            else
                status = -1;
        }
        if (status == 0) {
            display = wl_display_connect_to_fd(fd); // owns fd, including on failure
            break;
        }
        close(fd);
        if (error != ENOENT && error != ECONNREFUSED && error != EAGAIN && error != EINTR)
            break;
        // Retry unavailable connections, never a crashed backend. This does not assert readiness.
        poll(nullptr, 0, std::min(100, rungic::remainingMs(deadline)));
    }
    const bool ready = display && rungic::waylandRoundtrip(display, deadline);
    if (display)
        wl_display_disconnect(display);
    if (!ready) {
        std::cerr << "workspace portal: Wayland did not respond within " << timeoutMs << " ms (" << path << ")\n";
        return 1;
    }
    execv(argv[first], argv + first);
    std::cerr << "workspace portal: cannot execute backend: " << std::strerror(errno) << '\n';
    return 1;
}
