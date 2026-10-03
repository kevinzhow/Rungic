// SPDX-License-Identifier: GPL-2.0-or-later
// Local voice-message lifecycle. Codex decides every UI action; this helper owns audio only.
#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QSocketNotifier>
#include <QSet>
#include <QTimer>
#include <fcntl.h>
#include <unistd.h>

class Recording : public QObject {
public:
    QFile output;
    QProcess route, player;
    QTimer expiry, playLimit;
    QByteArray input, routed, pcm;
    QSet<QByteArray> streams;
    bool ready = false, recording = false, attempted = false, played = false, closing = false;
    Recording(const QString &binary, const QString &path) {
        output.open(STDOUT_FILENO, QIODevice::WriteOnly, QFileDevice::DontCloseHandle);
        QFile audio(path);
        if (!audio.open(QIODevice::ReadOnly)) { reply({{"error", "Audio file unavailable"}}); QTimer::singleShot(0, qApp, &QCoreApplication::quit); return; }
        pcm = audio.readAll();
        ::fcntl(STDIN_FILENO, F_SETFL, ::fcntl(STDIN_FILENO, F_GETFL) | O_NONBLOCK);
        auto *watch = new QSocketNotifier(STDIN_FILENO, QSocketNotifier::Read, this);
        connect(watch, &QSocketNotifier::activated, this, [this] {
            char data[4096]; auto n = ::read(STDIN_FILENO, data, sizeof(data));
            if (!n) { close(); return; }
            if (n < 0) return;
            input.append(data, n);
            while (input.contains('\n')) {
                auto line = input.left(input.indexOf('\n')); input.remove(0, line.size() + 1);
                command(QJsonDocument::fromJson(line).object()["phase"].toString());
            }
        });
        route.setProcessChannelMode(QProcess::SeparateChannels);
        connect(&route, &QProcess::readyReadStandardOutput, this, [this] {
            routed += route.readAllStandardOutput();
            while (routed.contains('\n')) {
                auto line = routed.left(routed.indexOf('\n')); routed.remove(0, line.size() + 1);
                if (line == "ready" && !ready) { ready = true; expiry.start(180000); reply(state()); }
                auto parts = line.split(' ');
                if (line.startsWith("routed source-output")) streams.insert(parts.value(2));
                if (line.startsWith("gone source-output")) streams.remove(parts.value(2));
                recording = !streams.isEmpty();
            }
        });
        connect(&route, &QProcess::finished, this, [this](int, QProcess::ExitStatus) {
            recording = false;
            if (!closing) { reply({{"error", "Audio routing stopped"}}); close(); }
        });
        connect(&route, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
            if (!closing) { reply({{"error", "Audio routing failed"}}); close(); }
        });
        connect(&player, &QProcess::started, this, [this] { player.write(pcm); player.closeWriteChannel(); });
        connect(&player, &QProcess::finished, this, [this](int code, QProcess::ExitStatus status) {
            playLimit.stop(); played = code == 0 && status == QProcess::NormalExit;
            auto result = state(); if (!played) result["error"] = "Voice playback failed; do not send";
            if (!closing) reply(result);
        });
        connect(&player, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
            if (player.state() == QProcess::NotRunning && !closing) reply({{"error", "Voice playback failed; do not send"}});
        });
        expiry.setSingleShot(true); playLimit.setSingleShot(true);
        connect(&expiry, &QTimer::timeout, this, [this] { reply({{"error", "Recording expired; cancel in the app"}}); close(); });
        connect(&playLimit, &QTimer::timeout, this, [this] { player.kill(); });
        expiry.start(10000);
        route.start("rungic-audio-route", {"--binary", binary, "--microphone"});
    }
    QJsonObject state() const {
        return {{"ready", ready}, {"recording", recording}, {"played", played},
                {"playbackAttempted", attempted}, {"seconds", double(pcm.size()) / 48000.0}};
    }
    void reply(QJsonObject value) { output.write(QJsonDocument(value).toJson(QJsonDocument::Compact) + '\n'); output.flush(); }
    void command(const QString &phase) {
        expiry.start(180000);
        if (phase == "status") reply(state());
        else if (phase == "play") {
            if (!ready || !recording) { reply({{"error", "Recording has not reached the Linux microphone; nothing was played"}}); return; }
            if (attempted) { auto value = state(); if (!played) value["error"] = "Playback was already attempted; do not retry or send"; reply(value); return; }
            attempted = true; playLimit.start(120000);
            player.start("pacat", {"--device=linux_microphone_input", "--raw", "--format=s16le", "--rate=24000", "--channels=1", "--latency-msec=30"});
        } else if (phase == "close") { reply(state()); close(); }
        else reply({{"error", "Unknown recording phase"}});
    }
    void close() {
        if (closing) return; closing = true;
        expiry.stop(); playLimit.stop();
        if (player.state() != QProcess::NotRunning) { player.kill(); player.waitForFinished(1000); }
        route.closeWriteChannel();
        if (!route.waitForFinished(3000)) { route.kill(); route.waitForFinished(1000); }
        QCoreApplication::quit();
    }
    ~Recording() override { close(); }
};

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    if (app.arguments().size() != 3) return 2;
    Recording recording(app.arguments()[1], app.arguments()[2]);
    return app.exec();
}
