// SPDX-License-Identifier: GPL-2.0-or-later
#pragma once
#include "audio.h"
#include "core.h"
#include <QElapsedTimer>
#include <QJsonObject>
#include <QTimer>
#include <QWebSocket>
#include <functional>
struct ResponseContext {QString text,utterance;quint64 generation=0;bool progress=false;QString instruction;};
class Session : public QObject {
public:
    std::function<void(QJsonObject)> output;
    Audio audio;
    Tasks tasks;
    QWebSocket ws;
    QTimer timer;
    QString id,conversation,phase="closed",language,prompt,utterance,focusedQuestion;
    QStringList inputItems;
    QHash<QString,QString> transcripts,assistantText;
    QHash<QString,ResponseContext> responses;
    QList<ResponseContext> expected,deferred;
    ReplyBuffer playback;
    QString truncateItem;
    quint64 generation=0,playStart=0,playedSamples=0,truncateStart=0,truncateSamples=0;
    bool narrationSuppressed=false,inputBlocked=false;
    QList<ResponseContext> acknowledgements;
    QString steerUtterance;
    QSet<QString> steeredTasks;
    QString controlUtterance,controlledTask;
    bool connected=false,configured=false,localSpeech=false,serverSpeech=false,commitPending=false,submitted=false,responseActive=false,muted=false,externalBusy=false;
    qint64 lastVoice=0,lastUser=0,lastProgress=0,lastPlaybackPush=-10000;
    qint64 started=0;   // the call's start, seconds since the epoch (its time on the app's call bar)
    QElapsedTimer clock;
    QString journal,leases,processStart;
    QStringList notices;
    QHash<int,std::function<void(QJsonObject)>> callbacks;
    int requests=0;
    explicit Session(QObject *parent=nullptr);
    void receive(QJsonObject message);
    void command(QString method,QJsonObject args,std::function<void(QJsonObject)> done);
    void start(QString conversation,QString language);
    void stop(QString reason={});
    void state();
    QJsonObject fields() const;
    bool thinking() const;
    void event(QJsonObject o,bool keep=true);
    void send(QJsonObject o);
    void configure();
    QJsonArray trusted() const;
    void incoming(QJsonObject o);
    void onSpeech(bool value);
    void tick();
    void stopSpeaking();
    void requestReply(ResponseContext context,QString instruction={});
    void tool(QString name,QJsonObject args,QString callId,QString responseId);
    void rpc(QString method,QJsonObject params,std::function<void(QJsonObject)> done={});
    void notification(QString method,QJsonObject params);
    void question(QJsonObject message);
    QJsonObject answer(QString taskId,QJsonObject answers);
    void runQueue();
    void startTask(QString taskId);
    void stopTask(QString taskId);
    void changed(QString taskId);
    void lease(Task &task,bool active);
    bool toolsActive(const Task &task);
    void save();
    void restore();
};
