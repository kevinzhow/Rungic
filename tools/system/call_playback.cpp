// SPDX-License-Identifier: GPL-2.0-or-later
// A reply of the phone mode played as a call plays it, for tools/system/tests/communication_audio.py:
// the real Session (agent/assistant/session) with its tick() deciding when to play, its Audio (pulsesink
// into PulseAudio's android_communication) and the communication service on
// $XDG_RUNTIME_DIR/rungic-communication.sock. No Realtime connection: the reply is put in the session's
// buffer as its audio deltas would put it. Prints one JSON line at the end (or on a failure).
//   call_playback SECONDS
#include "session.h"
#include <QCoreApplication>
#include <QJsonDocument>
#include <cmath>
#include <cstdio>

static void say(const QJsonObject &o) {
    std::printf("%s\n", QJsonDocument(o).toJson(QJsonDocument::Compact).constData());
    std::fflush(stdout);
}

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    gst_init(&argc, &argv);
    const double seconds = argc > 1 ? QByteArray(argv[1]).toDouble() : 3.0;
    Session s;
    s.output = [](QJsonObject) {};
    s.id = "playback-probe";
    s.conversation = "playback";
    s.configured = true;
    s.audio.failed = [&](const QString &error) {
        say({{"failed", error}});
        QTimer::singleShot(100, &app, &QCoreApplication::quit);
    };
    s.audio.microphone = [](const QByteArray &) {};   // no Realtime connection to send it to
    s.audio.start(s.id);
    // A 440 Hz tone, 24 kHz mono s16le: the Realtime API's reply audio.
    QByteArray tone(int(24000 * seconds) * 2, 0);
    auto *samples = reinterpret_cast<qint16 *>(tone.data());
    for (int i = 0; i < tone.size() / 2; ++i)
        samples[i] = qint16(8000 * std::sin(2 * M_PI * 440 * i / 24000.0));
    QTimer wait;
    bool begun = false;
    QObject::connect(&wait, &QTimer::timeout, &app, [&] {
        if (begun || !s.audio.opened)
            return;
        begun = true;
        // As the first audio delta of a response does (Session::incoming).
        s.playback.append("probe-response", "probe-item", tone);
        s.playStart = s.audio.written;
        s.playedSamples = 0;
        say({{"playing", true}, {"written", double(s.audio.written)}});
        QTimer::singleShot(int(seconds * 1000) + 1500, &app, [&] {
            say({{"pushedSamples", double(s.playedSamples)}, {"pendingBytes", double(s.playback.pending.size())},
                 {"played", double(s.audio.played)}, {"written", double(s.audio.written)}});
            app.quit();
        });
    });
    wait.start(20);
    QTimer::singleShot(int(seconds * 1000) + 8000, &app, [&] { say({{"failed", "timed out"}}); app.quit(); });
    return app.exec();
}
