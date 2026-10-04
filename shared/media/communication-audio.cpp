// SPDX-License-Identifier: GPL-2.0-or-later
// Shared communication profile: PulseAudio PCM devices, Android AEC and playback cursor.
#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalServer>
#include <QLocalSocket>
#include <QProcess>
#include <QRegularExpression>
#include <QSocketNotifier>
#include <QTimer>
#include <QtEndian>
#include <gst/gst.h>
#include <gst/app/gstappsrc.h>
#include <gst/app/gstappsink.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>

static QByteArray json(const QJsonObject &o) { return QJsonDocument(o).toJson(QJsonDocument::Compact)+'\n'; }
static void reply(QLocalSocket *s, QJsonObject o) { if(s && s->state()==QLocalSocket::ConnectedState) s->write(json(o)); }
static QByteArray pcm(GstAppSink *sink, quint64 *epoch=nullptr) {
    GstSample *sample=gst_app_sink_pull_sample(sink); if(!sample)return {};
    GstBuffer *b=gst_sample_get_buffer(sample); GstMapInfo m;
    if(epoch)*epoch=GST_BUFFER_OFFSET(b);
    QByteArray data; if(gst_buffer_map(b,&m,GST_MAP_READ)){data=QByteArray(reinterpret_cast<const char *>(m.data),m.size);gst_buffer_unmap(b,&m);}
    gst_sample_unref(sample);return data;
}

class Communication;
struct AudioContext {Communication *audio;QString session;};
class Communication : public QObject {
public:
    QLocalServer server;
    QLocalSocket *owner=nullptr, *mic=nullptr, *output=nullptr, *control=nullptr;
    QString runtime=QString::fromLocal8Bit(qgetenv("XDG_RUNTIME_DIR"));
    QString session, inputPath, outputPath;
    int inputFd=-1,outputFd=-1,sharedFd=-1,micRetries=0; QString sourceModule,sinkModule;
    QSocketNotifier *outputWatch=nullptr;
    // The call's playback is paced by the clock (readOutput): what was sent since `paced` started,
    // against the time that has passed, keeping Android `Lead` bytes ahead of what it plays.
    static constexpr qint64 BytesPerSecond=48000*2, Lead=BytesPerSecond*80/1000;
    QElapsedTimer paced; qint64 sent=0; bool drained=false;
    GstElement *pipeline=nullptr,*micSrc=nullptr,*speakerSrc=nullptr;
    bool micHeader=false,outHeader=false,controlHeader=false,muted=false,flushing=false,closing=false;
    quint64 epoch=1; QTimer position;
    QHash<int,QJsonObject> pending; int ids=0;
    Communication() {
        if(runtime.isEmpty())qFatal("XDG_RUNTIME_DIR is required");
        const QString path=runtime+"/rungic-communication.sock";
        // systemd owns the single instance; don't unlink a live server.
        QLocalSocket probe;probe.connectToServer(path);
        if(probe.waitForConnected(100))qFatal("Communication backend already running");
        QLocalServer::removeServer(path);server.setSocketOptions(QLocalServer::UserAccessOption);
        if(!server.listen(path))qFatal("Cannot listen to communication control socket");
        connect(&server,&QLocalServer::newConnection,this,[this]{accept();});
        position.setInterval(100);connect(&position,&QTimer::timeout,this,[this]{
            if(controlHeader && pending.size()<4)android({{"op","position"}});
        });
    }
    ~Communication(){close();}
    QString pactl(QStringList args) {
        QProcess p;p.start("pactl",args);if(!p.waitForFinished(3000)||p.exitCode()!=0){qWarning("Communication PulseAudio request failed: %s",p.readAllStandardError().constData());return {};}
        return QString::fromUtf8(p.readAllStandardOutput()).trimmed();
    }
    void accept() {
        while(server.hasPendingConnections()) {
            auto *s=server.nextPendingConnection();s->setReadBufferSize(65536);
            connect(s,&QLocalSocket::readyRead,this,[this,s]{
                while(s->canReadLine()) {
                    if(s->bytesAvailable()>65536){s->abort();return;}
                    QJsonParseError error;auto o=QJsonDocument::fromJson(s->readLine(),&error).object();
                    if(error.error!=QJsonParseError::NoError){reply(s,{{"error","Invalid request"}});continue;}
                    if(!owner) {
                        if(o["op"]!="open"||o["sessionId"].toString().isEmpty()){reply(s,{{"error","Open a session first"}});s->disconnectFromServer();continue;}
                        owner=s;session=o["sessionId"].toString();
                        if(!open(o["muted"].toBool())){reply(owner,{{"error","Communication audio could not start"}});close();}
                    } else if(owner!=s) {reply(s,{{"error","Communication audio is in use"}});s->disconnectFromServer();}
                    else command(o);
                }
            });
            connect(s,&QLocalSocket::disconnected,this,[this,s]{if(owner==s)close();s->deleteLater();});
        }
    }
    bool open(bool initialMuted) {
        epoch=1;muted=initialMuted;flushing=false;micRetries=0;paced.invalidate();sent=0;
        inputPath=runtime+"/rungic-communication-input.pcm";outputPath=runtime+"/rungic-communication-output.pcm";
        for(const auto &path:{inputPath,outputPath}) {
            struct stat st;
            if(::lstat(path.toLocal8Bit(),&st)==0) {
                if(!S_ISFIFO(st.st_mode)||st.st_uid!=getuid())return false;
                ::unlink(path.toLocal8Bit());
            }
        }
        // PulseAudio creates its pipe endpoints and refuses a pre-existing FIFO.
        // The runtime directory is private; tighten the nodes before opening them.
        sourceModule=pactl({"load-module","module-pipe-source","source_name=android_communication_microphone","file="+inputPath,"format=s16le","rate=48000","channels=1","source_properties=device.description=Communication_microphone"});
        sinkModule=pactl({"load-module","module-pipe-sink","sink_name=android_communication","file="+outputPath,"format=s16le","rate=48000","channels=1","sink_properties=device.description=Communication_audio"});
        if(sourceModule.isEmpty()||sinkModule.isEmpty())return false;
        // A new communication endpoint follows the existing phone output level.
        // Creating a call must not reset the user's quiet speaker setting.
        auto level=QRegularExpression("(\\d+)%").match(pactl({"get-sink-volume","android_phone"}));
        if(level.hasMatch())pactl({"set-sink-volume","android_communication",level.captured(1)+"%"});
        ::chmod(inputPath.toLocal8Bit(),0600);::chmod(outputPath.toLocal8Bit(),0600);
        inputFd=::open(inputPath.toLocal8Bit(),O_RDWR|O_NONBLOCK|O_CLOEXEC);
        outputFd=::open(outputPath.toLocal8Bit(),O_RDWR|O_NONBLOCK|O_CLOEXEC);
        if(inputFd<0||outputFd<0)return false;
        ::fcntl(inputFd,F_SETPIPE_SZ,16384);::fcntl(outputFd,F_SETPIPE_SZ,4096);
        outputWatch=new QSocketNotifier(outputFd,QSocketNotifier::Read,this);
        connect(outputWatch,&QSocketNotifier::activated,this,[this]{readOutput();});
        output=channel("communication-output",false);return true;
    }
    QLocalSocket *channel(const QString &op,bool microphone) {
        auto *s=new QLocalSocket(this);s->setReadBufferSize(19200);
        connect(s,&QLocalSocket::connected,this,[this,s,op]{s->write(json({{"op",op},{"sessionId",session}}));});
        connect(s,&QLocalSocket::readyRead,this,[this,s,microphone]{
            bool &header=microphone?micHeader:(s==control?controlHeader:outHeader);
            if(!header) {
                if(!s->canReadLine())return;
                auto o=QJsonDocument::fromJson(s->readLine()).object();
                if(!o["ok"].toBool()){
                    const auto error=o["error"].toString();
                    if(microphone&&error.contains("正在使用")&&micRetries++<20){
                        const auto token=session;mic=nullptr;s->abort();s->deleteLater();
                        QTimer::singleShot(100,this,[this,token]{if(session==token&&!muted)mic=channel("communication-microphone",true);});return;
                    }
                    fail(o["error"].toString("Android communication audio unavailable"));return;}
                header=true;
                if(microphone) {
                    if(!dsp(o["aec"].toBool())){fail("WebRTC audio processing unavailable");return;}
                    if(sharedFd<0)sharedFd=::open((runtime+"/rungic-microphone.pcm").toLocal8Bit(),O_WRONLY|O_NONBLOCK|O_CLOEXEC);
                    ready();
                } else if(s==output) {
                    control=channel("communication-control",false);
                    if(!muted)mic=channel("communication-microphone",true);
                } else {position.start();ready();}
            }
            if(microphone && pipeline) {
                auto data=s->readAll();if(!muted)push(micSrc,data,0);
            } else if(s==control) {
                while(s->canReadLine())controlReply(QJsonDocument::fromJson(s->readLine()).object());
            } else if(s->bytesAvailable())s->readAll();
        });
        connect(s,&QLocalSocket::disconnected,this,[this,s]{if(!closing&&(s==output||s==control||s==mic))fail("Communication audio disconnected; resume explicitly");});
        connect(s,&QLocalSocket::errorOccurred,this,[this,s](QLocalSocket::LocalSocketError){if(!closing&&(s==output||s==control||s==mic))fail("Android communication backend is unavailable");});
        s->connectToServer("/mnt/android-wayland/capture.sock");return s;
    }
    bool dsp(bool hardwareAEC) {
        if(pipeline)return true;
        const QByteArray spec=QByteArray("appsrc name=speaker format=time is-live=true do-timestamp=true caps=audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! webrtcechoprobe name=reference ! appsink name=played emit-signals=true sync=false async=false ")+
            "appsrc name=mic format=time is-live=true do-timestamp=true caps=audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved ! webrtcdsp echo-cancel="+(hardwareAEC?"false":"true")+" probe=reference gain-control=false ! appsink name=captured emit-signals=true sync=false async=false";
        GError *error=nullptr;pipeline=gst_parse_launch(spec.constData(),&error);
        if(error){g_error_free(error);return false;}if(!pipeline)return false;
        auto *bus=gst_element_get_bus(pipeline);
        gst_bus_set_sync_handler(bus,+[](GstBus *,GstMessage *m,gpointer p)->GstBusSyncReply{
            auto *c=static_cast<AudioContext *>(p);auto *self=c->audio;auto token=c->session;
            if(GST_MESSAGE_TYPE(m)==GST_MESSAGE_ERROR)QMetaObject::invokeMethod(self,[self,token]{if(self->session==token&&!self->closing)self->fail("Shared communication processing failed");},Qt::QueuedConnection);
            gst_message_unref(m);return GST_BUS_DROP;
        },new AudioContext{this,session},+[](gpointer p){delete static_cast<AudioContext *>(p);});gst_object_unref(bus);
        micSrc=gst_bin_get_by_name(GST_BIN(pipeline),"mic");speakerSrc=gst_bin_get_by_name(GST_BIN(pipeline),"speaker");
        auto *captured=gst_bin_get_by_name(GST_BIN(pipeline),"captured");auto *played=gst_bin_get_by_name(GST_BIN(pipeline),"played");
        g_signal_connect_data(captured,"new-sample",G_CALLBACK(+[](GstAppSink *sink,gpointer p)->GstFlowReturn{
            auto *c=static_cast<AudioContext *>(p);auto *self=c->audio;auto token=c->session;auto data=pcm(sink);
            QMetaObject::invokeMethod(self,[self,data,token]{if(self->session==token&&self->inputFd>=0&&!self->muted&&!self->closing) {
                const auto n=::write(self->inputFd,data.data(),data.size());
                if(n!=data.size())self->fail("Communication microphone consumer stalled");
                // Other standard PulseAudio clients share this physical recording.
                // Their independent queues may drop a frame, but cannot stall phone audio.
                if(self->sharedFd>=0)::write(self->sharedFd,data.data(),data.size());
            }},Qt::QueuedConnection);return GST_FLOW_OK;
        }),new AudioContext{this,session},+[](gpointer p,GClosure *){delete static_cast<AudioContext *>(p);},GConnectFlags(0));
        g_signal_connect_data(played,"new-sample",G_CALLBACK(+[](GstAppSink *sink,gpointer p)->GstFlowReturn{
            auto *c=static_cast<AudioContext *>(p);auto *self=c->audio;auto token=c->session;quint64 e=0;auto data=pcm(sink,&e);
            QMetaObject::invokeMethod(self,[self,data,e,token]{if(self->session==token)self->speaker(data,e);},Qt::QueuedConnection);return GST_FLOW_OK;
        }),new AudioContext{this,session},+[](gpointer p,GClosure *){delete static_cast<AudioContext *>(p);},GConnectFlags(0));
        gst_object_unref(captured);gst_object_unref(played);
        if(gst_element_set_state(pipeline,GST_STATE_PLAYING)==GST_STATE_CHANGE_FAILURE)return false;
        // Negotiate the echo reference even when the first thing the user does
        // is unmute while no application is playing into the communication sink.
        push(speakerSrc,QByteArray(1920,0),epoch);return true;
    }
    void ready(){if((muted||micHeader)&&outHeader&&controlHeader)reply(owner,{{"type","ready"},{"sessionId",session},{"epoch",double(epoch)},{"microphone",!muted&&micHeader},{"source","android_communication_microphone"},{"sink","android_communication"}});}
    void push(GstElement *src,const QByteArray &data,quint64 e) {
        if(!src||data.isEmpty())return;guint64 queued=0;g_object_get(src,"current-level-bytes",&queued,nullptr);if(queued>19200){fail("Shared audio processing stalled");return;}GstBuffer *b=gst_buffer_new_allocate(nullptr,data.size(),nullptr);gst_buffer_fill(b,0,data.data(),data.size());GST_BUFFER_OFFSET(b)=e;gst_app_src_push_buffer(GST_APP_SRC(src),b);
    }
    // PulseAudio's pipe sink writes as fast as the pipe is read, so the reading sets the pace: as much
    // as the time since the start allows, plus Lead; the rest waits in the pipe (PA's backpressure
    // bounds the queue). Reading a fixed 20 ms and then waiting 20 ms fell behind by every
    // millisecond the loop was late, and Android's 100 ms track ran dry: 10-21 % of a call's speech
    // was underrun, heard as stutter (docs/101). After an empty pipe what was not sent is forgone
    // (Android has played it out by then), so a pause is not made up later as a burst; a flush
    // starts again.
    void readOutput() {
        constexpr qint64 Block=1920;char b[Block];
        if(flushing) {while(outputFd>=0&&::read(outputFd,b,sizeof(b))>0){}paced.invalidate();return;}
        if(!paced.isValid()){paced.start();sent=0;drained=false;}
        if(drained){sent=std::max(sent,paced.nsecsElapsed()*BytesPerSecond/1000000000);drained=false;}
        while(outputFd>=0) {
            const qint64 due=paced.nsecsElapsed()*BytesPerSecond/1000000000;
            const qint64 allowed=(due+Lead-sent)&~qint64(1);
            if(allowed<=0) {
                if(!outputWatch)return;
                outputWatch->setEnabled(false);const auto token=session;
                const int wait=int(std::max<qint64>(1,(Block/2-allowed)*1000/BytesPerSecond));
                QTimer::singleShot(wait,Qt::PreciseTimer,this,[this,token]{if(session==token&&outputWatch){outputWatch->setEnabled(true);readOutput();}});
                return;
            }
            const ssize_t n=::read(outputFd,b,std::min<qint64>(allowed,Block));
            if(n<=0){drained=true;return;}
            sent+=n;
            if(outHeader){if(speakerSrc)push(speakerSrc,QByteArray(b,n),epoch);else if(muted)speaker(QByteArray(b,n),epoch);}
        }
    }
    void speaker(const QByteArray &data,quint64 e) {
        if(flushing||closing||!output||!outHeader||e!=epoch)return;
        if(output->bytesToWrite()>9600){fail("Communication playback stalled");return;}
        QByteArray packet(12,0);qToBigEndian<quint64>(e,reinterpret_cast<uchar *>(packet.data()));qToBigEndian<quint32>(data.size(),reinterpret_cast<uchar *>(packet.data()+8));packet+=data;output->write(packet);
    }
    void android(QJsonObject o,QJsonObject client={}) {
        if(!control||!controlHeader)return;int id=++ids;o["id"]=id;pending[id]=client;control->write(json(o));
    }
    void command(QJsonObject o) {
        if(o["sessionId"].toString()!=session){reply(owner,{{"id",o["id"]},{"error","Stale session"}});return;}
        const auto op=o["op"].toString();
        if(op=="close"){reply(owner,{{"id",o["id"]},{"ok",true}});close();}
        else if(op=="position")android({{"op","position"}},o);
        else if(op=="flush") {
            if(flushing){reply(owner,{{"id",o["id"]},{"error","Playback flush already pending"}});return;}
            flushing=true;++epoch;readOutput();
            // The client disconnected its PA stream before this request. Drain the pipe tail;
            // packets already in Android's socket carry the old epoch and cannot reappear.
            android({{"op","flush"},{"epoch",double(epoch)}},o);
        } else if(op=="mute") {
            muted=o["muted"].toBool();
            if(muted&&mic){auto *s=mic;mic=nullptr;micHeader=false;s->abort();s->deleteLater();char b[4096];while(::read(inputFd,b,sizeof(b))>0){} }
            else if(!muted&&!mic&&outHeader)mic=channel("communication-microphone",true);
            reply(owner,{{"id",o["id"]},{"ok",true},{"muted",muted}});ready();
        }
    }
    void controlReply(QJsonObject o) {
        const auto client=pending.take(o["id"].toInt());
        if(client["op"]=="flush") {
            const auto token=session;QTimer::singleShot(40,this,[this,o,client,token]{if(session!=token)return;readOutput();flushing=false;auto r=o;r["id"]=client["id"];reply(owner,r);});
        } else {
            o["type"]="position";if(!client.isEmpty())o["id"]=client["id"];reply(owner,o);
        }
    }
    void fail(const QString &why){reply(owner,{{"type","error"},{"error",why}});close();}
    void close() {
        if(closing)return;closing=true;position.stop();pending.clear();
        if(outputWatch){delete outputWatch;outputWatch=nullptr;}
        for(auto **s:{&mic,&output,&control})if(*s){(*s)->abort();(*s)->deleteLater();*s=nullptr;}
        if(pipeline){gst_element_set_state(pipeline,GST_STATE_NULL);gst_object_unref(pipeline);pipeline=nullptr;}
        if(micSrc){gst_object_unref(micSrc);micSrc=nullptr;}if(speakerSrc){gst_object_unref(speakerSrc);speakerSrc=nullptr;}
        if(!sinkModule.isEmpty())pactl({"unload-module",sinkModule});if(!sourceModule.isEmpty())pactl({"unload-module",sourceModule});sinkModule.clear();sourceModule.clear();
        if(sharedFd>=0)::close(sharedFd);sharedFd=-1;
        if(inputFd>=0)::close(inputFd);if(outputFd>=0)::close(outputFd);inputFd=outputFd=-1;
        if(!inputPath.isEmpty())::unlink(inputPath.toLocal8Bit());if(!outputPath.isEmpty())::unlink(outputPath.toLocal8Bit());
        micHeader=outHeader=controlHeader=false;session.clear();
        auto *s=owner;owner=nullptr;if(s)s->disconnectFromServer();closing=false;
    }
};
int main(int argc,char **argv){QCoreApplication app(argc,argv);gst_init(&argc,&argv);Communication audio;return app.exec();}
