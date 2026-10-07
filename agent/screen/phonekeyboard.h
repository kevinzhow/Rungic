// The phone keyboard's languages for the floating keyboard (FloatingKeyboard.qml): read, never
// written, from plasma-keyboard's KConfig file, [General] enabledLocales=zh_CN,en_US. Not QML's
// Settings: QSettings' INI dialect keeps a group named General as [%General] and rewrites the file
// in its own format, which left KConfig reading " en_US" and the phone with Chinese only
// (G100, 2026-10-08, docs/121).
#pragma once

#include <QFile>
#include <QStandardPaths>
#include <QStringList>

inline QStringList phoneKeyboardLocales(const QString &file)
{
    QFile config(file);
    if (!config.open(QIODevice::ReadOnly | QIODevice::Text))
        return {};
    bool general = false;
    while (!config.atEnd()) {
        const QString line = QString::fromUtf8(config.readLine()).trimmed();
        if (line.startsWith(u'[')) {
            general = line == QLatin1String("[General]");
            continue;
        }
        const qsizetype equals = line.indexOf(u'=');
        if (!general || equals < 0 || line.left(equals).trimmed() != QLatin1String("enabledLocales"))
            continue;
        QStringList locales;
        for (const QString &part : line.mid(equals + 1).split(u','))
            if (!part.trimmed().isEmpty())
                locales << part.trimmed();
        return locales;
    }
    return {};
}

inline QStringList phoneKeyboardLocales()
{
    return phoneKeyboardLocales(QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation)
                                + QStringLiteral("/plasmakeyboardrc"));
}
