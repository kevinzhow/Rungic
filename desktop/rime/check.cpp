// SPDX-License-Identifier: GPL-3.0-or-later
// Engine and session-lifecycle check for the Rime keyboard (docs/41). Uses a temporary user
// directory unless RUNGIC_RIME_USER_DIR is set; with it set to a live directory it takes part
// in the same user-dictionary locking as the keyboards.
//   rungic-rime-check            compositions, session lifecycle, sharing between processes
//   rungic-rime-check --second   (internal) the other process of the sharing check
#include <QCoreApplication>
#include <QDir>
#include <QElapsedTimer>
#include <QProcess>
#include <QTemporaryDir>
#include <QTextStream>
#include <algorithm>
#include <csignal>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include "echo.h"
#include "runtime.h"

static QTextStream out(stdout);
static int failures = 0;
static void check(bool ok, const QString &what) {
    out << (ok ? "ok   " : "FAIL ") << what << Qt::endl;
    if (!ok) ++failures;
}

// LevelDB LOCK files this process has open, i.e. user dictionaries it holds.
static int userDbLocks() {
    int n = 0;
    for (const QString &fd : QDir("/proc/self/fd").entryList(QDir::AllEntries | QDir::System | QDir::NoDotAndDotDot)) {
        char target[4096];
        const ssize_t size = ::readlink(QFile::encodeName("/proc/self/fd/" + fd).constData(), target, sizeof target);
        if (size > 0 && QByteArray(target, size).endsWith(".userdb/LOCK")) ++n;
    }
    return n;
}

static RimeApi *api() { return RimeRuntime::instance().api; }

// Types the pinyin, returns the first candidate after selecting the candidate at index.
static QString type(RimeSessionId s, const QByteArray &pinyin, int index = 0, QString *first = nullptr) {
    api()->clear_composition(s);
    for (char c : pinyin) if (!api()->process_key(s, c, 0)) return {};
    RIME_STRUCT(RimeContext, context);
    if (!api()->get_context(s, &context)) return {};
    if (first && context.menu.num_candidates > 0) *first = QString::fromUtf8(context.menu.candidates[0].text);
    api()->free_context(&context);
    if (!api()->select_candidate(s, index)) return {};
    RIME_STRUCT(RimeCommit, commit);
    if (!api()->get_commit(s, &commit)) return {};
    const QString text = QString::fromUtf8(commit.text);
    api()->free_commit(&commit);
    return text;
}

static bool compositions(RimeSessionId s) {
    const QList<QPair<QByteArray, QString>> examples{{"nihao", "你好"}, {"zhongguo", "中国"}, {"ceshi", "测试"}};
    for (int repeat = 0; repeat < 100; ++repeat)
        for (const auto &example : examples)
            if (type(s, example.first) != example.second) return false;
    return true;
}

static double median(QList<double> v) { std::sort(v.begin(), v.end()); return v.isEmpty() ? 0 : v[v.size() / 2]; }

// Session open and the first key, as the keyboard does them.
static double openAndFirstKey(RimeRuntime::Session &s) {
    QElapsedTimer t;
    t.start();
    if (!RimeRuntime::instance().open(s)) return -1;
    api()->process_key(s.id, 'n', 0);
    RIME_STRUCT(RimeContext, context);
    api()->get_context(s.id, &context);
    api()->free_context(&context);
    return t.nsecsElapsed() / 1e6;
}

static int second() {
    auto &rime = RimeRuntime::instance();
    QTextStream in(stdin);
    RimeRuntime::Session s;
    check(rime.open(s) && !s.shared, "while the other process holds the dictionary: a guest session");
    check(userDbLocks() == 0, "the guest session leaves the user dictionary closed");
    check(type(s.id, "nihao") == "你好", "the guest session types Chinese");
    rime.close(s);
    out << "WAITING" << Qt::endl;
    in.readLine();   // the first process has released it
    check(rime.open(s) && s.shared, "after the release: a shared session");
    check(userDbLocks() == 1, "the shared session holds the user dictionary");
    QString first;
    type(s.id, "shi", 0, &first);
    out << "LEARNED " << first << Qt::endl;
    rime.close(s);
    check(userDbLocks() == 0, "closing it releases the user dictionary");
    return failures ? 1 : 0;
}

// covers: desktop.rime/E7
static void echoes() {
    check(preeditEcho("ab", 1, {"ni"}, "ab", 1), "echo: the editor keeps the preedit apart");
    check(preeditEcho("ab", 1, {"ni"}, "anib", 3), "echo: the editor counts the preedit, cursor after it (KWrite)");
    check(preeditEcho("", 0, {"n"}, "n", 1), "echo: first letter in an empty document");
    check(preeditEcho("ab", 1, {"ni"}, "anib", 1), "echo: the editor counts the preedit, cursor before it");
    check(preeditEcho("", 0, {"n", "ni", "ni h"}, "ni", 2), "echo: the report of an earlier preedit comes late");
    check(!preeditEcho("ab", 1, {"ni"}, "ab", 2), "outside change: the cursor moved");
    check(!preeditEcho("ab", 1, {"ni"}, "anixb", 3), "outside change: the text changed");
    check(!preeditEcho("ab", 1, {"ni"}, "anib", 2), "outside change: the cursor moved inside the preedit");
    check(!preeditEcho("", 0, {"n", "ni"}, "nix", 3), "outside change: text typed after a preedit");
    check(!preeditEcho("ab", -1, {"ni"}, "ab", 1), "no recorded start: not an echo");
}

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    QTemporaryDir temporary;
    if (!qEnvironmentVariableIsSet("RUNGIC_RIME_USER_DIR")) qputenv("RUNGIC_RIME_USER_DIR", QFile::encodeName(temporary.path()));
    auto &rime = RimeRuntime::instance();
    if (!rime.ready) { out << "FAIL schema " << RimeRuntime::schema << " is not deployed" << Qt::endl; return 1; }
    if (app.arguments().contains("--second")) return second();
    echoes();

    // covers: desktop.rime/E6
    // The user's dictionary and settings: a directory only the user can read, and the default
    // settings written there when there are none (run.sh checks that a user's own are kept).
    struct stat dir {};
    check(::stat(rime.user.constData(), &dir) == 0 && (dir.st_mode & 0777) == 0700, "the user directory is 0700");
    QFile defaults(QFile::decodeName(rime.user + "/default.custom.yaml"));
    check(defaults.open(QIODevice::ReadOnly) && defaults.readAll().contains("luna_pinyin_simp"),
          "default.custom.yaml created with the simplified pinyin schema");

    // Lazy sessions and the 300 compositions.
    check(userDbLocks() == 0, "no session: the user dictionary is closed");
    RimeRuntime::Session s;
    const double cold = openAndFirstKey(s);
    check(s.id && s.shared, "a shared session");
    check(userDbLocks() == 1, "the session holds the user dictionary");
    check(compositions(s.id), "300 rapid compositions, Chinese first candidate, selection and commit");
    rime.close(s);
    check(userDbLocks() == 0, "closing the last session releases the user dictionary");
    QList<double> again;
    for (int i = 0; i < 20; ++i) { again.append(openAndFirstKey(s)); rime.close(s); }
    out << QString("time: session open + first key %1 ms first, %2 ms median of 20 reopenings")
               .arg(cold, 0, 'f', 1).arg(median(again), 0, 'f', 1) << Qt::endl;
    check(rime.guests, "schema edits reach new sessions (guest sessions possible)");

    // Two processes: a guest while this one holds the dictionary, the learned word shared after.
    QString learned;
    check(rime.open(s) && s.shared, "this process takes the user dictionary");
    learned = type(s.id, "shi", 5);   // Rime puts a selected candidate first from then on
    QProcess other;
    other.setProcessChannelMode(QProcess::ForwardedErrorChannel);
    other.start(app.applicationFilePath(), {"--second"});
    QString output;
    while (!output.contains("WAITING") && other.waitForReadyRead(30000)) output += QString::fromUtf8(other.readAll());
    rime.close(s);
    other.write("released\n");
    other.waitForFinished(30000);
    output += QString::fromUtf8(other.readAll());
    out << output;
    check(other.exitStatus() == QProcess::NormalExit && other.exitCode() == 0, "the other process passed");
    check(!learned.isEmpty() && output.contains("LEARNED " + learned), "a word learned here is first there (" + learned + ")");

    // A LevelDB lock taken without the directory lock (an older plugin) also yields a guest.
    const QByteArray lock = rime.user + "/luna_pinyin.userdb/LOCK";
    const pid_t child = fork();
    if (child == 0) {
        const int fd = ::open(lock.constData(), O_RDWR);
        struct flock l {};
        l.l_type = F_WRLCK;
        l.l_whence = SEEK_SET;
        if (fd < 0 || ::fcntl(fd, F_SETLK, &l) != 0) _exit(1);
        ::sleep(3);
        _exit(0);
    }
    ::usleep(300000);
    const auto before = QDir(QFile::decodeName(rime.user + "/luna_pinyin.userdb")).entryList(QDir::Files);
    check(rime.open(s) && !s.shared, "a foreign LevelDB lock: a guest session");
    check(type(s.id, "nihao") == "你好", "the guest session types Chinese");
    rime.close(s);
    ::kill(child, SIGTERM);
    int status = 0;
    ::waitpid(child, &status, 0);
    check(QDir(QFile::decodeName(rime.user + "/luna_pinyin.userdb")).entryList(QDir::Files) == before
              && !QDir(QFile::decodeName(rime.user + "/luna_pinyin.userdb/lost")).exists(),
          "no recovery ran against the held database");
    check(rime.open(s) && s.shared, "once it is free: a shared session again");
    rime.close(s);

    out << (failures ? "FAIL" : "PASS") << ": Rime engine and session lifecycle" << Qt::endl;
    return failures ? 1 : 0;
}
