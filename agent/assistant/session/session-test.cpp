// SPDX-License-Identifier: GPL-2.0-or-later
// covers: agent.phone-mode/E3 agent.phone-mode/E4 agent.phone-mode/E5 agent.phone-mode/E6 agent.phone-mode/E7
#include "session.h"
#include <QCoreApplication>
#include <QDateTime>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QLocalServer>
#include <QLocalSocket>
#include <QTemporaryDir>
#include <cstdio>
#include <cstdlib>
static void check(bool value,const char *message){if(!value){std::fprintf(stderr,"FAIL %s\n",message);std::exit(1);}}
int main(int argc,char **argv){
    QTemporaryDir dir;qputenv("HOME",dir.path().toUtf8());qputenv("XDG_RUNTIME_DIR",dir.path().toUtf8());
    QCoreApplication app(argc,argv);gst_init(&argc,&argv);Session s;
    QList<QJsonObject> output;s.output=[&](QJsonObject o){output.append(o);};
    s.conversation="origin";s.id="voice-test";s.configured=true;s.generation=1;
    s.responses["reply"]={"请调查这件事，不要改文件", "utterance1", 1, false};
    s.tool("start_task",{{"original_words","删除文件"},{"access","read_only"}},"call1","reply");
    check(s.tasks.rows.size()==1,"one task admitted");auto tid=s.tasks.rows[0].id;
    check(s.tasks.rows[0].text=="请调查这件事，不要改文件","final ASR overrides model's rewritten words");
    s.tool("start_task",{{"access","read_only"}},"call2","reply");check(s.tasks.rows.size()==1,"one utterance cannot duplicate task execution");
    s.generation=2;s.tool("start_task",{},"late-call","reply");check(s.tasks.rows.size()==1,"interrupted tool call cannot start work");
    s.receive({{"type","rpc-result"},{"id",1},{"result",QJsonObject{{"thread",QJsonObject{{"id","backend"}}}}}});
    s.receive({{"type","rpc-result"},{"id",2},{"result",QJsonObject{{"turn",QJsonObject{{"id","turn1"}}}}}});
    check(s.tasks.find(tid)->status=="running","turn starts after its own thread");
    QJsonArray questions{QJsonObject{{"id","scope"},{"question","Choose the scope"}}};
    s.question({{"id",42},{"method","item/tool/requestUserInput"},{"params",QJsonObject{{"threadId","backend"},{"questions",questions}}}});
    check(s.tasks.find(tid)->status=="waiting_input","question holds its task until answered");
    check(s.answer(tid,{}).contains("error")&&s.tasks.find(tid)->status=="waiting_input","empty answer never chooses a default");
    check(s.answer("other",{{"scope",QJsonArray{"today"}}}).contains("error"),"answers cannot target another task");
    check(s.answer(tid,{{"scope",QJsonArray{"today"}}})["ok"].toBool()&&s.tasks.find(tid)->status=="running","complete answer resumes the owning task");
    bool boundAnswer=false;for(auto o:output)if(o["type"]=="rpc"&&o["method"]=="ServerResponse"){
        auto p=o["params"].toObject();boundAnswer=p["requestId"].toInt()==42&&p["result"].toObject()["answers"].toObject()["scope"].toObject()["answers"].toArray()==QJsonArray{"today"};
    }
    check(boundAnswer,"answer retains backend request identity and protocol shape");
    s.stop();check(s.tasks.find(tid)->status=="running","hangup leaves execution running");
    s.stopTask(tid);check(s.tasks.find(tid)->status=="stopping","interrupt request is not completion");
    s.notification("turn/completed",{{"threadId","other"},{"turn",QJsonObject{{"id","turn1"},{"status","interrupted"}}}});
    check(s.tasks.find(tid)->status=="stopping","another chat cannot complete this task");
    s.notification("turn/completed",{{"threadId","backend"},{"turn",QJsonObject{{"id","stale-turn"},{"status","interrupted"}}}});
    check(s.tasks.find(tid)->status=="stopping","stale turn completion ignored");
    s.notification("turn/completed",{{"threadId","backend"},{"turn",QJsonObject{{"id","turn1"},{"status","interrupted"}}}});
    check(s.tasks.find(tid)->status=="stopped","actual backend termination completes cancellation");
    s.question({{"id",43},{"method","item/tool/requestUserInput"},{"params",QJsonObject{{"threadId","backend"},{"turnId","turn1"},{"questions",questions}}}});
    check(s.tasks.find(tid)->status=="stopped"&&s.tasks.find(tid)->question.isEmpty(),"late question cannot revive a stopped task");
    s.id="queued-test";s.configured=true;s.externalBusy=true;
    auto queued=s.tasks.add("inspect attachment",true,"origin","queued-attachment");
    s.tasks.find(queued)->input=QJsonArray{QJsonObject{{"type","text"},{"text","inspect attachment"}}};
    s.responses["correction"]={"only inspect the last page","correction-utterance",s.generation,false};
    s.tool("steer_task",{{"task_id",queued}},"steer1","correction");
    s.tool("steer_task",{{"task_id",queued}},"steer2","correction");
    check(s.tasks.find(queued)->input.size()==2&&s.tasks.find(queued)->input.last().toObject()["text"]=="only inspect the last page","queued attachment retains exactly one correction in execution input");
    s.tasks.find(queued)->status="running";s.tasks.find(queued)->thread="steered-thread";s.tasks.find(queued)->turn="steered-turn";
    s.responses["live-correction"]={"keep both constraints","another-utterance",s.generation,false};
    s.tool("steer_task",{{"task_id",queued}},"steer3","live-correction");
    s.tool("steer_task",{{"task_id",queued}},"steer4","live-correction");
    auto unrelated=s.tasks.add("weather",true,"origin","weather");
    s.tasks.find(unrelated)->status="running";s.tasks.find(unrelated)->thread="weather-thread";s.tasks.find(unrelated)->turn="weather-turn";
    s.tool("steer_task",{{"task_id",unrelated}},"steer-weather","live-correction");
    int corrections=0;for(auto o:output)if(o["type"]=="rpc"&&o["method"]=="turn/steer")++corrections;
    check(corrections==1&&s.tasks.find(unrelated)->text=="weather","duplicate correction calls cannot steer a second task");
    for(const auto &o:output)if(o["type"]=="event"&&o["event"].toObject()["type"]=="phone-task")check(o["event"].toObject()["conversation"]=="origin","history stays with origin after hangup");
    s.id="reconnected";s.phase="connecting";s.configured=false;s.audio.opened=s.audio.sourceReady=s.audio.captureReady=true;
    s.onSpeech(true);s.incoming({{"type","session.updated"}});
    check(s.inputBlocked&&s.phase=="connecting","speech spanning connection readiness requires a complete repeat");
    s.incoming({{"type","input_audio_buffer.speech_started"},{"item_id","partial"}});
    check(s.inputItems.isEmpty(),"connection cannot execute the remaining tail of a request");
    s.onSpeech(false);s.lastVoice=s.clock.elapsed()-1000;s.tick();
    check(!s.inputBlocked&&s.phase=="connected","quiet interval restores listening explicitly");
    s.incoming({{"type","error"},{"error",QJsonObject{{"code","invalid_response"},{"message","test protocol failure"}}}});
    check(s.id.isEmpty()&&s.phase=="closed","unknown protocol failure releases voice resources");
    {
        // covers: agent.phone-mode/E8
        // Mute and hang-up against a stand-in of the Android communication audio backend (its socket,
        // $XDG_RUNTIME_DIR/rungic-communication.sock): muting stops the microphone capture and tells
        // Android to close the physical microphone, while the reply keeps playing; hanging up releases
        // the capture, the playback and the backend's call audio. No sound server here: a capture that
        // cannot start stays as it is (its failure is recorded, not acted on).
        qputenv("PULSE_SERVER","unix:/nonexistent");
        QLocalServer backend;check(backend.listen(dir.path()+"/rungic-communication.sock"),"stand-in backend listens");
        QLocalSocket *peer=nullptr;QList<QJsonObject> requests;bool released=false;
        QObject::connect(&backend,&QLocalServer::newConnection,[&]{peer=backend.nextPendingConnection();
            QObject::connect(peer,&QLocalSocket::readyRead,[&]{while(peer->canReadLine())requests.append(QJsonDocument::fromJson(peer->readLine()).object());});
            QObject::connect(peer,&QLocalSocket::disconnected,[&]{released=true;});});
        auto until=[&](std::function<bool()> done){QElapsedTimer t;t.start();while(!done()&&t.elapsed()<5000)QCoreApplication::processEvents(QEventLoop::AllEvents,20);return done();};
        auto last=[&](QString op){for(int i=requests.size()-1;i>=0;--i)if(requests[i]["op"]==op)return requests[i];return QJsonObject{};};
        Session m;QList<QJsonObject> states;QStringList failures;
        m.output=[&](QJsonObject o){auto e=o["event"].toObject();if(e["type"]=="phone-state")states.append(e);};
        m.audio.failed=[&](QString reason){failures.append(reason);};
        m.id="mute-test";m.conversation="origin";m.configured=true;m.phase="connected";
        m.audio.start(m.id);
        check(until([&]{return !last("open").isEmpty();}),"the call audio is opened with the backend");
        peer->write(QJsonDocument(QJsonObject{{"type","ready"},{"microphone",true}}).toJson(QJsonDocument::Compact)+'\n');
        check(until([&]{return m.audio.opened;}),"backend ready");
        if(!m.audio.recorder)m.audio.recorder=gst_parse_launch("fakesrc ! fakesink",nullptr);   // without webrtcdsp
        m.audio.player=gst_parse_launch("appsrc name=audio ! fakesink",nullptr);m.audio.src=gst_bin_get_by_name(GST_BIN(m.audio.player),"audio");
        check(m.audio.recorder&&m.audio.player,"capturing and playing");
        QJsonObject reply;m.command("SetPhoneMuted",{{"sessionId","mute-test"},{"muted",true}},[&](QJsonObject r){reply=r;});
        check(reply["ok"].toBool(),"mute accepted");
        check(!m.audio.recorder&&m.audio.captureToken.isEmpty(),"muted: the microphone capture is stopped");
        check(until([&]{return last("mute")["muted"].toBool();}),"muted: Android is told to close the physical microphone");
        check(m.audio.player!=nullptr&&m.audio.opened,"muted: the reply keeps playing");
        check(!states.last()["microphone"].toBool()&&states.last()["muted"].toBool(),"muted: the card shows the microphone off");
        m.command("SetPhoneMuted",{{"sessionId","mute-test"},{"muted",false}},[&](QJsonObject r){reply=r;});
        check(until([&]{auto o=last("mute");return o.contains("muted")&&!o["muted"].toBool();}),"unmuted: Android opens the microphone again");
        check(m.audio.recorder!=nullptr||!failures.isEmpty(),"unmuted: capture starts again");
        m.stop();
        check(!m.audio.recorder&&!m.audio.player&&!m.audio.opened,"hung up: capture and playback released");
        check(until([&]{return released;}),"hung up: the backend's call audio is released");
        check(m.phase=="closed"&&m.id.isEmpty(),"hung up: the session is closed");
    }
    // covers: agent.phone-mode/E14 agent.phone-mode/E15
    // The app's call bar and its summary (docs/101): the state says when the call began and when the
    // Agent is taking in what was said; hanging up leaves the call's summary in its conversation.
    {
        Session c;QList<QJsonObject> out;c.output=[&](QJsonObject o){out.append(o);};
        c.conversation="call-conv";c.id="call-summary";c.configured=true;c.generation=1;
        c.started=QDateTime::currentSecsSinceEpoch()-75;
        check(c.fields()["startedAt"].toDouble()==double(c.started),"the state says when the call began");
        c.submitted=false;c.utterance="u1";c.localSpeech=false;
        check(c.thinking()&&c.fields()["thinking"].toBool(),"said and not yet answered: thinking");
        c.localSpeech=true;check(!c.thinking(),"still speaking: not thinking");
        c.localSpeech=false;c.responseActive=true;check(!c.thinking(),"answering: not thinking");
        c.responseActive=false;c.submitted=true;c.utterance.clear();check(!c.thinking(),"nothing said: not thinking");
        auto before=c.tasks.add("old work",true,"call-conv","before");c.tasks.find(before)->created=c.started-10;
        auto during=c.tasks.add("sort the screenshots",true,"call-conv","during");
        c.tasks.add("elsewhere",true,"other-conv","other");
        c.stop("Voice paused while Plasma is hidden");
        QJsonObject ended;bool kept=false;
        for(const auto &o:out)if(o["type"]=="event"&&o["event"].toObject()["type"]=="phone-ended"){ended=o["event"].toObject();kept=o["keep"].toBool();}
        check(!ended.isEmpty()&&kept,"hanging up keeps the call's summary in its conversation");
        check(ended["conversation"]=="call-conv"&&ended["seconds"].toDouble()>=75,"the summary says how long the call was");
        auto work=ended["tasks"].toArray();
        check(work.size()==1&&work[0].toObject()["taskId"]==during,"the summary lists only what was started in this call, in this conversation");
        check(ended["reason"]=="Voice paused while Plasma is hidden","the summary says why the call ended when it was not hung up");
        check(c.started==0&&c.fields()["startedAt"].toDouble()==0,"after the call no start time is left");
    }
    std::puts("session state, transcript fidelity, late events, cancellation and hangup checks passed");
}
