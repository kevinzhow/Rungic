// SPDX-License-Identifier: GPL-2.0-or-later
#include "session.h"
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QNetworkProxy>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QSaveFile>
#include <QUuid>
#include <algorithm>
#include <signal.h>

static QString uuid(){return QUuid::createUuid().toString(QUuid::Id128);}
static QByteArray startTime(qint64 pid){QFile f(QString("/proc/%1/stat").arg(pid));if(!f.open(QIODevice::ReadOnly))return {};auto d=f.readAll();return d.mid(d.lastIndexOf(')')+2).split(' ').value(19);}
static QJsonObject fn(QString name,QString description,QJsonObject properties,QStringList required){QJsonArray r;for(auto k:required)r.append(k);return {{"type","function"},{"name",name},{"description",description},{"parameters",QJsonObject{{"type","object"},{"properties",properties},{"required",r},{"additionalProperties",false}}}};}
Session::Session(QObject *parent):QObject(parent),audio(this){
    clock.start();journal=QDir::homePath()+"/.local/share/rungic-voice-agent/phone-tasks.json";
    leases=QString::fromLocal8Bit(qgetenv("XDG_RUNTIME_DIR"))+"/rungic-task-leases";
    QDir().mkpath(leases);QFile::setPermissions(leases,QFileDevice::ReadOwner|QFileDevice::WriteOwner|QFileDevice::ExeOwner);
    processStart=QString::fromLatin1(startTime(QCoreApplication::applicationPid()));restore();
    timer.setInterval(20);connect(&timer,&QTimer::timeout,this,[this]{tick();});timer.start();
    connect(&ws,&QWebSocket::connected,this,[this]{connected=true;phase="connecting";state();});
    connect(&ws,&QWebSocket::textMessageReceived,this,[this](QString text){incoming(QJsonDocument::fromJson(text.toUtf8()).object());});
    connect(&ws,&QWebSocket::disconnected,this,[this]{if(!id.isEmpty())stop("Voice connection ended; tap to resume");});
    connect(&ws,&QWebSocket::errorOccurred,this,[this](QAbstractSocket::SocketError){if(!id.isEmpty())stop("Voice connection failed; tap to resume");});
    audio.ready=[this]{if(configured&&!inputBlocked){phase="connected";state();}};
    audio.failed=[this](QString reason){stop(reason);};
    audio.microphone=[this](const QByteArray &data){
        if(!configured||muted||inputBlocked||id.isEmpty())return;
        if(ws.bytesToWrite()>32768){stop("Network cannot keep up with live audio; tap to resume");return;}
        send({{"type","input_audio_buffer.append"},{"audio",QString::fromLatin1(data.toBase64())}});
    };
    audio.speech=[this](bool value){onSpeech(value);};
    audio.flushed=[this](quint64 oldPlayed,quint64){
        if(!truncateItem.isEmpty()&&configured){
            const quint64 frames=oldPlayed>truncateStart?oldPlayed-truncateStart:0;
            send({{"type","conversation.item.truncate"},{"item_id",truncateItem},{"content_index",0},{"audio_end_ms",double(std::min<quint64>(truncateSamples*1000/24000,frames*1000/48000))}});
        }
        truncateItem.clear();playedSamples=0;state();
    };
}
void Session::event(QJsonObject o,bool keep){if(!o.contains("conversation"))o["conversation"]=conversation;o["time"]=double(QDateTime::currentMSecsSinceEpoch())/1000;if(output)output({{"type","event"},{"event",o},{"keep",keep}});}
// Said, the reply not begun: the words are being taken in (spoken, not yet submitted) or a reply was
// asked for and has not started. The app's call bar shows it as "Thinking".
bool Session::thinking() const{
    if(id.isEmpty()||localSpeech||serverSpeech||responseActive||!playback.pending.isEmpty())return false;
    return (!submitted&&!utterance.isEmpty())||!expected.isEmpty();
}
// What the app's call bar and panel show, as the live event and as the snapshot (the app reopened).
QJsonObject Session::fields() const{
    return {{"sessionId",id},{"conversation",conversation},{"phase",phase},{"microphone",configured&&audio.opened&&!muted},{"muted",muted},
            {"speaking",!playback.pending.isEmpty()||responseActive},{"listening",localSpeech},{"thinking",thinking()},
            {"startedAt",double(started)},{"focusedTask",tasks.focused},{"tasks",tasks.snapshot()}};
}
void Session::state(){auto o=fields();o["type"]="phone-state";event(o,false);}
void Session::receive(QJsonObject o){
    const auto type=o["type"].toString();
    if(type=="rpc-result") {auto f=callbacks.take(o["id"].toInt());if(f)f(o);}
    else if(type=="request")question(o);
    else if(type=="notification")notification(o["method"].toString(),o["params"].toObject());
    else if(type=="command")command(o["method"].toString(),o["args"].toObject(),[this,o](QJsonObject r){if(output)output({{"type","reply"},{"id",o["id"]},{"result",r}});});
}
void Session::command(QString method,QJsonObject args,std::function<void(QJsonObject)> done){
    if(method=="BackendReset"){
        stop("Task connection changed; tap to resume voice");
        for(auto &t:tasks.rows)if(!t.terminal()){lease(t,false);t.status="interrupted";changed(t.id);}
        done({{"ok",true}});
    } else if(method=="StartPhoneMode"){
        QString target=args["conversationId"].toString();if(target.isEmpty()){done({{"error","A conversation is required"}});return;}
        if(!id.isEmpty()){if(target==conversation){done({{"sessionId",id}});return;}done({{"error","Another conversation owns the phone session"}});return;}
        prompt=args["instructions"].toString();
        if(prompt.isEmpty()){done({{"error","Phone mode instructions unavailable"}});return;}
        start(target,args["language"].toString());done(id.isEmpty()?QJsonObject{{"error","Voice could not start"}}:QJsonObject{{"sessionId",id}});
    } else if(method=="StopPhoneMode"){
        if(args["sessionId"].toString()!=id){done({{"error","Stale phone session"}});return;}stop();done({{"ok",true}});
    } else if(method=="SetPhoneMuted"){
        if(args["sessionId"].toString()!=id||id.isEmpty()){done({{"error","Stale phone session"}});return;}
        muted=args["muted"].toBool();if(!muted){inputBlocked=true;lastVoice=clock.elapsed();phase="connecting";}
        if(muted){if(!submitted)++generation;localSpeech=serverSpeech=false;inputItems.clear();transcripts.clear();utterance.clear();commitPending=false;submitted=true;send({{"type","input_audio_buffer.clear"}});}
        audio.mute(muted);state();done({{"ok",true}});
    } else if(method=="StopSpeaking"){
        if(args["sessionId"].toString()!=id){done({{"error","Stale phone session"}});return;}narrationSuppressed=true;stopSpeaking();done({{"ok",true}});
    } else if(method=="StopTaskById"){
        auto *t=tasks.target(args["taskId"].toString());if(!t){done({{"error","Choose a task to stop"}});return;}auto tid=t->id;stopTask(tid);done({{"ok",true},{"taskId",tid}});
    } else if(method=="AnswerTask"){done(answer(args["taskId"].toString(),args["answers"].toObject()));
    } else if(method=="FocusTask"){
        auto *t=tasks.find(args["taskId"].toString());if(!t){done({{"error","Unknown task"}});return;}tasks.focused=t->id;state();done({{"ok",true}});
    } else if(method=="PhoneSnapshot"){done(fields());}
    else if(method=="Foreground") {if(!args["visible"].toBool()&&!id.isEmpty())stop("Voice paused while Plasma is hidden");done({{"ok",true}});}
    else if(method=="ExternalBusy"){externalBusy=args["busy"].toBool();if(!externalBusy)runQueue();done({{"ok",true}});}
    else if(method=="SendPhoneText"){
        if(id.isEmpty()||args["conversationId"].toString()!=conversation){done({{"error","Text belongs to another conversation"}});return;}
        QString text=args["text"].toString().trimmed();if(text.isEmpty()){done({{"ok",true}});return;}
        stopSpeaking();++generation;utterance=uuid();submitted=true;localSpeech=serverSpeech=false;inputItems.clear();transcripts.clear();send({{"type","input_audio_buffer.clear"}});
        QJsonObject item{{"type","message"},{"role","user"},{"content",QJsonArray{QJsonObject{{"type","input_text"},{"text",text}}}}};
        send({{"type","conversation.item.create"},{"item",item}});
        event({{"type","message"},{"id",utterance},{"role","user"},{"text",text}});lastUser=clock.elapsed();requestReply({text,utterance,generation,false});done({{"ok",true}});
    } else if(method=="SubmitTask"){
        auto target=args["conversationId"].toString();auto input=args["input"].toArray();
        if(target.isEmpty()||input.isEmpty()){done({{"error","A conversation and input are required"}});return;}
        const auto tid=tasks.add(args["text"].toString(),false,target,"typed:"+uuid());
        if(tid.isEmpty()){done({{"error","The task queue is full; finish or stop a task first"}});return;}
        tasks.find(tid)->input=input;changed(tid);runQueue();done({{"taskId",tid}});
    } else if(method=="Reconcile"){
        for(const auto &t:tasks.rows)if(t.status=="interrupted"&&!t.thread.isEmpty()){
            const auto tid=t.id;rpc("thread/read",{{"threadId",t.thread},{"includeTurns",true}},[this,tid](QJsonObject o){
                auto *t=tasks.find(tid);if(!t)return;auto thread=o["result"].toObject()["thread"].toObject();auto turns=thread["turns"].toArray();
                if(turns.isEmpty()){changed(tid);return;}auto turn=turns.last().toObject();auto status=turn["status"].toString();
                if(status=="inProgress"){
                    t->turn=turn["id"].toString();t->status="running";rpc("thread/resume",{{"threadId",t->thread},{"taskId",t->id},{"readOnly",t->readOnly}});lease(*t,true);
                }else if(status=="completed")t->status="completed";else if(status=="interrupted")t->status="stopped";else t->status="failed";
                changed(tid);
            });
        }
        done({{"ok",true}});
    } else done({{"error","Unknown phone command"}});
}
void Session::start(QString target,QString lang){
    QFile keyFile(QDir::homePath()+"/.config/rungic-voice-agent/openai-api-key");
    if(!keyFile.open(QIODevice::ReadOnly)||keyFile.size()>8192){event({{"type","error"},{"conversation",target},{"text","Set the OpenAI API key in Agent settings"}});return;}
    auto key=keyFile.readAll().trimmed();if(key.isEmpty())return;
    conversation=target;language=lang;id="voice_"+uuid();phase="connecting";muted=false;configured=false;connected=false;submitted=true;localSpeech=serverSpeech=false;narrationSuppressed=false;inputBlocked=false;lastVoice=-10000;acknowledgements.clear();
    playback=ReplyBuffer();responses.clear();expected.clear();deferred.clear();assistantText.clear();truncateItem.clear();inputItems.clear();transcripts.clear();utterance.clear();++generation;
    QString proxy=QString::fromLocal8Bit(qgetenv("https_proxy"));if(proxy.isEmpty())proxy=QString::fromLocal8Bit(qgetenv("HTTPS_PROXY"));
    if(proxy.isEmpty())proxy=QString::fromLocal8Bit(qgetenv("http_proxy"));
    if(!proxy.isEmpty()){QUrl u(proxy);auto type=u.scheme().startsWith("socks")?QNetworkProxy::Socks5Proxy:QNetworkProxy::HttpProxy;ws.setProxy(QNetworkProxy(type,u.host(),u.port(type==QNetworkProxy::Socks5Proxy?1080:8080),u.userName(),u.password()));}
    else ws.setProxy(QNetworkProxy::NoProxy);
    QNetworkRequest req(QUrl("wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1-mini"));req.setRawHeader("Authorization","Bearer "+key);key.fill(0);
    started=QDateTime::currentSecsSinceEpoch();ws.open(req);audio.start(id);lastUser=clock.elapsed();state();
    const auto current=id;QTimer::singleShot(25000,this,[this,current]{if(id==current&&(!configured||!audio.opened))stop("Voice connection timed out; tap to retry");});
}
void Session::stop(QString reason){
    const auto old=id;if(old.isEmpty())return;
    // The call's summary, kept in its conversation: how long it was and what was started in it
    // (the tasks of this conversation created since it began), as the app shows it after the call.
    if(started>0){
        QJsonArray work;for(const auto &t:tasks.rows)if(t.conversation==conversation&&t.created>=started)work.append(QJsonObject{{"taskId",t.id},{"text",t.text},{"status",t.status}});
        event({{"type","phone-ended"},{"sessionId",old},{"startedAt",double(started)},{"seconds",double(std::max<qint64>(0,QDateTime::currentSecsSinceEpoch()-started))},{"tasks",work},{"reason",reason}});
    }
    started=0;id.clear();configured=connected=false;phase="closed";playback.clear();localSpeech=serverSpeech=false;submitted=true;++generation;
    ws.abort();audio.close();responses.clear();expected.clear();deferred.clear();inputItems.clear();transcripts.clear();truncateItem.clear();utterance.clear();
    if(!reason.isEmpty())event({{"type","phone-notice"},{"text",reason}});state();save();
}
void Session::send(QJsonObject o){if(!connected)return;if(!o.contains("event_id"))o["event_id"]="event_"+uuid();ws.sendTextMessage(QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact)));}
void Session::configure(){
    prompt+="\nCurrent system time (UTC): "+QDateTime::currentDateTimeUtc().toString(Qt::ISODate)+". Resolve relative dates in the user's or requested location's timezone.";
    QJsonObject words{{"original_words",QJsonObject{{"type","string"},{"description","The user's original words, with negations, constraints and corrections"}}}};
    QJsonObject target=words;target["task_id"]=QJsonObject{{"type","string"},{"description","Exact taskId from the trusted task snapshot"}};
    auto start=words;start["access"]=QJsonObject{{"type","string"},{"enum",QJsonArray{"read_only","exclusive"}},{"description","read_only for research, web queries and terminal commands that only read, wait or calculate; exclusive for edits, GUI interaction, device control, external writes or uncertain effects"}};
    QJsonArray tools{fn("start_task","Start a clear complete new request; independent tasks may run in parallel. Never execute discussions or hypotheses.",start,{"original_words","access"}),fn("steer_task","Correct or constrain the named running task. This does not stop it.",target,{"task_id","original_words"}),fn("stop_task","Explicitly cancel the named execution task. Speaking, backchannels and negated stop are not cancellation.",{{"task_id",target["task_id"]}},{"task_id"}),fn("task_status","Read the actual status of a named task.",{{"task_id",target["task_id"]}},{"task_id"}),fn("stop_speaking","Stop narration only; leave all tasks running.",{}, {}),fn("answer_task","Answer a pending task question using the user's explicit answer. Never invent an answer or approve a change implicitly.",{{"task_id",target["task_id"]},{"answers",QJsonObject{{"type","object"},{"additionalProperties",QJsonObject{{"type","array"},{"items",QJsonObject{{"type","string"}}}}}}}},{"task_id","answers"})};
    QJsonObject input{{"format",QJsonObject{{"type","audio/pcm"},{"rate",24000}}},{"transcription",QJsonObject{{"model","gpt-4o-mini-transcribe"}}},{"turn_detection",QJsonObject{{"type","semantic_vad"},{"eagerness","medium"},{"create_response",false},{"interrupt_response",true}}}};
    if(QRegularExpression("^[a-z]{2}$").match(language).hasMatch()){auto t=input["transcription"].toObject();t["language"]=language;input["transcription"]=t;}
    QJsonObject out{{"format",QJsonObject{{"type","audio/pcm"},{"rate",24000}}},{"voice","marin"}};
    QJsonObject config{{"type","realtime"},{"instructions",prompt+"\nTrusted task snapshot:\n"+QString::fromUtf8(QJsonDocument(trusted()).toJson(QJsonDocument::Compact))},{"output_modalities",QJsonArray{"audio"}},{"tools",tools},{"tool_choice","auto"},{"audio",QJsonObject{{"input",input},{"output",out}}}};
    send({{"type","session.update"},{"session",config}});
}
void Session::onSpeech(bool value){
    if(muted)return;localSpeech=value;if(value)lastVoice=clock.elapsed();if(!configured||inputBlocked)return;
    if(value){
        narrationSuppressed=false;
        lastVoice=lastUser=clock.elapsed();
        if(submitted||utterance.isEmpty()){++generation;utterance=uuid();inputItems.clear();transcripts.clear();submitted=false;commitPending=false;}
        stopSpeaking();
    }
    state();
}
void Session::incoming(QJsonObject o){
    const auto type=o["type"].toString();
    if(type=="session.created"){configure();return;}
    if(type=="session.updated"){if(!configured){inputBlocked=localSpeech||clock.elapsed()-lastVoice<700;if(inputBlocked)event({{"type","phone-notice"},{"text","The connection is ready; pause briefly and repeat the complete request"}});}configured=true;if(audio.opened&&!inputBlocked&&(muted||(audio.sourceReady&&audio.captureReady)))phase="connected";state();return;}
    if(type=="error"){
        auto e=o["error"].toObject();auto code=e["code"].toString();
        if(code.contains("cancel_not_active")||code.contains("truncate")||code.contains("commit_empty")){if(code.contains("commit_empty"))commitPending=false;return;}
        stop(e["message"].toString("Voice protocol error; tap to resume"));return;
    }
    if(type=="input_audio_buffer.speech_started"){
        if(muted||inputBlocked)return;
        serverSpeech=true;lastUser=clock.elapsed();if(utterance.isEmpty()||submitted){++generation;utterance=uuid();inputItems.clear();transcripts.clear();submitted=false;}
        QString item=o["item_id"].toString();if(!item.isEmpty()&&!inputItems.contains(item))inputItems.append(item);stopSpeaking();state();
    } else if(type=="input_audio_buffer.speech_stopped"){serverSpeech=false;state();}
    else if(type=="input_audio_buffer.committed"){
        commitPending=false;serverSpeech=false;auto item=o["item_id"].toString();if(!submitted&&!item.isEmpty()&&!inputItems.contains(item))inputItems.append(item);
    } else if(type=="conversation.item.input_audio_transcription.completed"){
        const auto item=o["item_id"].toString();if(inputItems.contains(item))transcripts[item]=o["transcript"].toString().trimmed();
    } else if(type=="conversation.item.input_audio_transcription.failed"){
        if(inputItems.contains(o["item_id"].toString())){submitted=true;event({{"type","phone-notice"},{"text","I couldn't understand that; please repeat"}});}
    } else if(type=="response.created"){
        auto responseId=o["response"].toObject()["id"].toString();
        if(expected.isEmpty()){send({{"type","response.cancel"},{"response_id",responseId}});return;}
        auto context=expected.takeFirst();responses[responseId]=context;
        if(context.generation!=generation){send({{"type","response.cancel"},{"response_id",responseId}});return;}
        responseActive=true;state();
    } else if(type=="response.output_audio.delta"){
        auto response=o["response_id"].toString();auto context=responses.value(response);
        if(!responses.contains(response)||context.generation!=generation||localSpeech||narrationSuppressed)return;
        if(playback.response!=response){playStart=audio.written;playedSamples=0;}
        if(!playback.append(response,o["item_id"].toString(),QByteArray::fromBase64(o["delta"].toString().toLatin1())))stop("Reply audio exceeded the buffer limit");
    } else if(type=="response.output_audio_transcript.delta"){
        const auto response=o["response_id"].toString();if(responses.contains(response)&&responses[response].generation==generation){}
    } else if(type=="response.output_audio_transcript.done"){
        const auto response=o["response_id"].toString();if(responses.contains(response)&&responses[response].generation==generation)event({{"type","message"},{"id",o["item_id"]},{"role","assistant"},{"text",o["transcript"]}});
    } else if(type=="response.function_call_arguments.done"){
        tool(o["name"].toString(),QJsonDocument::fromJson(o["arguments"].toString().toUtf8()).object(),o["call_id"].toString(),o["response_id"].toString());
    } else if(type=="response.cancelled"){
        const auto response=o["response_id"].toString(o["response"].toObject()["id"].toString());if(response==playback.response)stopSpeaking();
    } else if(type=="response.done"){
        const auto r=o["response"].toObject();const auto response=r["id"].toString();
        if(responses.contains(response)&&responses[response].generation==generation){responseActive=false;if(r["status"]=="cancelled"&&response==playback.response)stopSpeaking();state();}
        responses.remove(response);
    }
}
void Session::stopSpeaking(){
    if(!playback.item.isEmpty()&&(!playback.pending.isEmpty()||audio.player)){
        if(audio.flushing){
            // This newer response has not reached playback while the previous
            // generation is being flushed. Its heard duration is exactly zero.
            send({{"type","conversation.item.truncate"},{"item_id",playback.item},{"content_index",0},{"audio_end_ms",0}});
        } else {
            truncateItem=playback.item;truncateStart=playStart;truncateSamples=playedSamples;audio.stopPlayback();
        }
    }
    playback.clear();
    if(responseActive){send({{"type","response.cancel"}});responseActive=false;}
}

void Session::tick(){
    const auto now=clock.elapsed();
    if(localSpeech)lastVoice=now;
    if(configured&&inputBlocked&&!localSpeech&&now-lastVoice>=700){inputBlocked=false;send({{"type","input_audio_buffer.clear"}});if(audio.opened&&(muted||(audio.sourceReady&&audio.captureReady)))phase="connected";state();}
    if(configured&&audio.opened&&!id.isEmpty()){
        if(localSpeech)lastVoice=now;
        if(!submitted&&!localSpeech&&!muted&&!utterance.isEmpty()){
            if(serverSpeech&&!commitPending&&now-lastVoice>=2000){commitPending=true;send({{"type","input_audio_buffer.commit"}});}
            bool complete=!inputItems.isEmpty();QStringList parts;
            for(const auto &item:inputItems){if(!transcripts.contains(item)){complete=false;break;}parts.append(transcripts[item]);}
            if(complete&&!serverSpeech&&!commitPending&&now-lastVoice>=700){
                submitted=true;QString text=parts.join(" ").trimmed();
                if(!text.isEmpty()){event({{"type","message"},{"id",utterance},{"role","user"},{"text",text}});requestReply({text,utterance,generation,false});}
            } else if(now-lastVoice>10000){submitted=true;event({{"type","phone-notice"},{"text","That utterance was not completed; please repeat"}});}
        }
        if(!audio.flushing&&!playback.pending.isEmpty()&&!localSpeech&&audio.written-std::min(audio.written,audio.played)<4800){
            auto chunk=playback.pending.left(960);playback.pending.remove(0,chunk.size());playedSamples+=chunk.size()/2;lastPlaybackPush=now;audio.play(chunk);
        }
        if(!narrationSuppressed&&!notices.isEmpty()&&!localSpeech&&!serverSpeech&&!responseActive&&playback.pending.isEmpty()&&now-lastPlaybackPush>300&&now-lastUser>1500&&now-lastProgress>5000){
            QString text=notices.join("\n");notices.clear();lastProgress=now;requestReply({text,{},generation,true},"Report only these verified task updates in one short sentence. Do not start or modify any task.");
        }
    }
    if(configured&&!narrationSuppressed&&!responseActive&&expected.isEmpty()&&!localSpeech&&playback.pending.isEmpty()&&now-lastPlaybackPush>300&&!acknowledgements.isEmpty()){
        const auto c=acknowledgements.takeLast();acknowledgements.clear();
        if(c.generation==generation)requestReply({c.text,c.utterance,generation,true},"Acknowledge the actual tool result accurately in one short sentence. Do not call tools.");
    }
    if(configured&&!responseActive&&expected.isEmpty()&&!localSpeech&&!deferred.isEmpty()&&(!deferred.last().progress||(playback.pending.isEmpty()&&now-lastPlaybackPush>300))){
        auto c=deferred.takeLast();deferred.clear();if(c.generation==generation)requestReply(c,c.instruction);
    }
    bool changedAny=false;
    for(auto &t:tasks.rows)if(t.status=="stopping"&&t.backendStopped&&!toolsActive(t)){t.status="stopped";changed(t.id);changedAny=true;}
    if(changedAny)runQueue();
}
void Session::requestReply(ResponseContext context,QString instruction){
    if(!configured||context.generation!=generation)return;
    if(responseActive||!expected.isEmpty()||(context.progress&&(!playback.pending.isEmpty()||clock.elapsed()-lastPlaybackPush<300))){context.instruction=instruction;deferred.append(context);if(deferred.size()>8)deferred.removeFirst();return;}
    expected.append(context);state();
    QJsonObject response;
    if(context.progress){response["tool_choice"]="none";response["instructions"]=instruction+"\nVerified updates:\n"+context.text;}
    send({{"type","response.create"},{"response",response}});
}
void Session::tool(QString name,QJsonObject args,QString callId,QString responseId){
    if(callId.isEmpty())return;const auto context=responses.value(responseId);
    auto result=[this,callId,context](QJsonObject o){
        if(!configured)return;send({{"type","conversation.item.create"},{"item",QJsonObject{{"type","function_call_output"},{"call_id",callId},{"output",QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact))}}}});
        // Read the actual tool result once the current model response has finished.
        if(context.generation==generation)acknowledgements.append(context);
    };
    if(!responses.contains(responseId)||context.generation!=generation||context.progress||context.text.isEmpty()){result({{"error","This utterance was interrupted or has no finalized transcript"}});return;}
    const QString raw=context.text;
    if(name=="start_task"){
        const auto tid=tasks.add(raw,args["access"]=="read_only",conversation,id+":"+context.utterance);if(tid.isEmpty()){result({{"error","The task queue is full; finish or stop a task first"}});return;}changed(tid);runQueue();result(tasks.find(tid)->json());
    } else if(name=="stop_speaking"){narrationSuppressed=true;stopSpeaking();result({{"ok",true},{"tasksContinue",true}});}
    else {
        auto *t=tasks.target(args["task_id"].toString());if(!t||t->conversation!=conversation){result({{"error","Choose a task from this conversation"}});return;}QString tid=t->id;
        if(name=="steer_task"||name=="stop_task"){
            if(controlUtterance!=context.utterance){controlUtterance=context.utterance;controlledTask.clear();}
            if(!controlledTask.isEmpty()&&controlledTask!=tid){result({{"error","This utterance already controls another task. Name one task per request."}});return;}
            controlledTask=tid;
        }
        if(name=="answer_task") {
            for(auto q:t->question["questions"].toArray())if(q.toObject()["isSecret"].toBool()){result({{"error","Type secret answers on the task card"}});return;}
            result(answer(tid,args["answers"].toObject()));
        }
        else if(name=="task_status")result(t->json());
        else if(name=="stop_task"){stopTask(tid);result(tasks.find(tid)->json());}
        else if(name=="steer_task"){
            if(steerUtterance!=context.utterance){steerUtterance=context.utterance;steeredTasks.clear();}
            if(steeredTasks.contains(tid)){result({{"correctionAlreadySubmitted",true},{"task",t->json()}});return;}
            if(t->status=="queued"){
                steeredTasks.insert(tid);t->text+='\n'+raw;
                if(!t->input.isEmpty())t->input.append(QJsonObject{{"type","text"},{"text",raw}});
                changed(tid);result(t->json());
            }
            else if(t->status=="running"&&!t->turn.isEmpty()){
                steeredTasks.insert(tid);
                const auto turn=t->turn;rpc("turn/steer",{{"threadId",t->thread},{"expectedTurnId",turn},{"input",QJsonArray{QJsonObject{{"type","text"},{"text",raw}}}}},[this,tid,result,raw](QJsonObject o){
                    if(o.contains("error")){result({{"error","The task changed before this correction was applied. Check its status and start a follow-up task."}});return;}
                    if(auto *t=tasks.find(tid)){t->text+='\n'+raw;changed(tid);result(t->json());}
                });
            } else result({{"error","The task is no longer running. Start a follow-up task with the requested correction."},{"task",t->json()}});
        } else result({{"error","Unknown task tool"}});
    }
}
void Session::rpc(QString method,QJsonObject params,std::function<void(QJsonObject)> done){
    int rid=++requests;if(done)callbacks[rid]=done;
    if(output)output({{"type","rpc"},{"id",rid},{"method",method},{"params",params}});
    if(done)QTimer::singleShot(30000,this,[this,rid]{auto f=callbacks.take(rid);if(f)f({{"error","Task protocol timed out"},{"uncertain",true}});});
}
void Session::lease(Task &t,bool active){
    const auto path=leases+"/"+t.id+".json";
    if(!active||t.readOnly){QFile::remove(path);return;}
    QSaveFile f(path);if(f.open(QIODevice::WriteOnly)){f.setPermissions(QFileDevice::ReadOwner|QFileDevice::WriteOwner);f.write(QJsonDocument(QJsonObject{{"taskId",t.id},{"exclusive",true},{"pid",double(QCoreApplication::applicationPid())},{"startTime",processStart}}).toJson(QJsonDocument::Compact));f.commit();}
}
bool Session::toolsActive(const Task &t){
    QFile f(leases+"/"+t.id+".json.tools");if(!f.open(QIODevice::ReadOnly))return false;auto o=QJsonDocument::fromJson(f.readAll()).object();auto pid=qint64(o["pid"].toDouble());
    return o["active"].toBool()&&pid>0&&o["startTime"].toString().toLatin1()==startTime(pid);
}
void Session::changed(QString tid){
    auto *t=tasks.find(tid);if(!t)return;save();event({{"type","phone-task"},{"conversation",t->conversation},{"task",t->json()}});
    if(t->terminal()&&t->conversation==conversation)notices.append(t->text.left(80)+": "+t->status+". "+t->result.left(500));
    if(configured)send({{"type","session.update"},{"session",QJsonObject{{"type","realtime"},{"instructions",prompt+"\nTrusted task snapshot:\n"+QString::fromUtf8(QJsonDocument(trusted()).toJson(QJsonDocument::Compact))}}}});
    state();
}
void Session::runQueue(){if(externalBusy)return;for(auto tid:tasks.schedule())startTask(tid);}
void Session::startTask(QString tid){
    auto *t=tasks.find(tid);if(!t)return;changed(tid);lease(*t,true);
    rpc("thread/start",{{"taskId",tid},{"readOnly",t->readOnly},{"conversation",t->conversation}},[this,tid](QJsonObject o){
        auto *t=tasks.find(tid);if(!t)return;
        if(o.contains("error")){t->status=t->cancelRequested?"stopped":"failed";t->result=o["error"].toString();lease(*t,false);changed(tid);runQueue();return;}
        t->thread=o["result"].toObject()["thread"].toObject()["id"].toString();
        if(t->thread.isEmpty()){t->status="failed";t->result="No task thread returned";lease(*t,false);changed(tid);runQueue();return;}
        if(t->cancelRequested){t->backendStopped=true;changed(tid);return;}
        rpc("turn/start",{{"threadId",t->thread},{"taskId",tid},{"input",t->input.isEmpty()?QJsonArray{QJsonObject{{"type","text"},{"text",t->text}}}:t->input}},[this,tid](QJsonObject o){
            auto *t=tasks.find(tid);if(!t)return;
            if(o.contains("error")){
                if(o["uncertain"].toBool()){t->cancelRequested=true;t->status="stopping";t->result="Start acknowledgement lost; awaiting verified task termination";lease(*t,false);if(!t->turn.isEmpty())stopTask(tid);changed(tid);return;}
                t->status=t->cancelRequested?"stopped":"failed";t->result=o["error"].toString();lease(*t,false);changed(tid);runQueue();return;
            }
            t->turn=o["result"].toObject()["turn"].toObject()["id"].toString();
            if(t->status=="starting")t->status="running";
            if(t->cancelRequested)stopTask(tid);changed(tid);
        });
    });
}
void Session::stopTask(QString tid){
    auto *t=tasks.find(tid);if(!t||t->terminal())return;tasks.stop(tid);lease(*t,false);
    if(!t->question.isEmpty()){rpc("ServerResponse",{{"requestId",t->question["requestId"]},{"result",QJsonObject{{"answers",QJsonObject{}}}}});t->question={};}
    changed(tid);
    if(!t->turn.isEmpty())rpc("turn/interrupt",{{"threadId",t->thread},{"turnId",t->turn}},[this,tid](QJsonObject o){if(o.contains("error")){if(auto *t=tasks.find(tid)){t->result="Stop requested; awaiting task completion acknowledgement";changed(tid);}}});
    runQueue();
}
void Session::notification(QString method,QJsonObject p){
    auto *t=tasks.byThread(p["threadId"].toString());if(!t)return;const auto tid=t->id;
    if(method=="turn/started"){
        t->turn=p["turn"].toObject()["id"].toString();if(t->cancelRequested)t->status="stopping";if(t->status=="starting")t->status="running";if(t->cancelRequested)stopTask(tid);else changed(tid);
    } else if(method=="turn/completed"){
        auto turn=p["turn"].toObject();if(!t->turn.isEmpty()&&turn["id"].toString()!=t->turn)return;
        t->backendStopped=true;lease(*t,false);
        auto status=turn["status"].toString();
        if(t->cancelRequested)t->status=toolsActive(*t)?"stopping":"stopped";
        else t->status=status=="completed"?"completed":status=="interrupted"?"stopped":"failed";
        if(turn["error"].isObject())t->result=turn["error"].toObject()["message"].toString();changed(tid);runQueue();
    } else if(method=="item/completed"){
        auto item=p["item"].toObject();auto type=item["type"].toString();
        if(type=="agentMessage"&&item["phase"]=="final_answer"){t->result=item["text"].toString().left(16000);changed(tid);}
        else if(type=="commandExecution"||type=="mcpToolCall"||type=="fileChange")event({{"type","phone-task-detail"},{"conversation",t->conversation},{"taskId",tid},{"item",item}});
    } else if(method=="item/started"){
        auto item=p["item"].toObject();event({{"type","phone-task-detail"},{"conversation",t->conversation},{"taskId",tid},{"item",item}},false);
    }
}
void Session::save(){
    QDir().mkpath(QFileInfo(journal).absolutePath());QSaveFile f(journal);if(f.open(QIODevice::WriteOnly)){f.setPermissions(QFileDevice::ReadOwner|QFileDevice::WriteOwner);f.write(QJsonDocument(tasks.snapshot()).toJson(QJsonDocument::Compact));f.commit();}
}
void Session::restore(){QFile f(journal);if(f.open(QIODevice::ReadOnly))tasks.restore(QJsonDocument::fromJson(f.readAll()).array());}

void Session::question(QJsonObject message){
    auto p=message["params"].toObject();auto *t=tasks.byThread(p["threadId"].toString());if(!t)return;
    if(message["method"]=="item/tool/requestUserInput"){
        if(t->terminal()||t->cancelRequested||(!p["turnId"].toString().isEmpty()&&p["turnId"].toString()!=t->turn)){
            rpc("ServerResponse",{{"requestId",message["id"]},{"result",QJsonObject{{"answers",QJsonObject{}}}}});return;
        }
        t->question={{"requestId",message["id"]},{"questions",p["questions"]}};
        t->status="waiting_input";changed(t->id);
        notices.append("Task "+t->id+" needs the user's answer: "+QString::fromUtf8(QJsonDocument(p["questions"].toArray()).toJson(QJsonDocument::Compact)));
    } else rpc("ServerResponse",{{"requestId",message["id"]},{"result",QJsonObject{{"decision","decline"}}}});
}
QJsonObject Session::answer(QString tid,QJsonObject answers){
    auto *t=tasks.find(tid);if(!t||t->status!="waiting_input"||t->question.isEmpty())return {{"error","This task has no pending question"}};
    QJsonObject values;
    for(auto v:t->question["questions"].toArray()){
        auto q=v.toObject();auto key=q["id"].toString();auto a=answers[key].toArray();
        if(a.isEmpty()||a.first().toString().trimmed().isEmpty())return {{"error","Answer every pending question"}};
        values[key]=QJsonObject{{"answers",a}};
    }
    const QJsonValue request=t->question["requestId"];t->question={};t->status="running";changed(tid);
    rpc("ServerResponse",{{"requestId",request},{"result",QJsonObject{{"answers",values}}}});return {{"ok",true},{"taskId",tid}};
}

QJsonArray Session::trusted() const {
    QJsonArray result;int finished=0;
    for(auto i=tasks.rows.crbegin();i!=tasks.rows.crend();++i){
        const auto &t=*i;if(t.conversation!=conversation)continue;
        if(t.terminal()&&finished++>=8)continue;
        result.append(QJsonObject{{"taskId",t.id},{"text",t.text.left(500)},{"status",t.status},{"readOnly",t.readOnly},{"result",t.result.left(500)},{"question",t.question}});
    }
    return result;
}
