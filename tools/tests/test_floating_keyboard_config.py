"""The floating keyboard (agent/screen) takes the phone keyboard's languages from plasmakeyboardrc
without writing it: QML's Settings (QSettings) rewrote that KConfig file and left the phone keyboard
with one language (G100, 2026-10-08, docs/121). The window reads it (phonekeyboard.h) and hands the
list to FloatingKeyboard; the reading itself is checked on the phone (desktop-mode.floating-keyboard/E7)."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCREEN = ROOT / 'agent/screen'


# covers: desktop-mode.floating-keyboard/E7
def test_no_qml_settings_object_opens_a_kde_config_file():
    for qml in (SCREEN / 'qml').glob('*.qml'):
        for block in re.findall(r'\bSettings\s*\{(.*?)\n    \}', qml.read_text(), re.S):
            location = re.search(r'location:\s*(.+)', block)
            assert location and 'rungic-agent-screenrc' in location.group(1), \
                f'{qml.name}: a Settings object writes a file other than our own rungic-agent-screenrc'


# covers: desktop-mode.floating-keyboard/E7
def test_the_window_reads_the_phone_keyboard_languages_and_hands_them_to_the_keyboard():
    header = (SCREEN / 'phonekeyboard.h').read_text()
    assert 'QIODevice::ReadOnly' in header and 'WriteOnly' not in header and 'ReadWrite' not in header
    assert '[General]' in header and 'enabledLocales' in header and 'plasmakeyboardrc' in header
    assert 'setContextProperty(QStringLiteral("phoneKeyboardLocales"), phoneKeyboardLocales())' in \
        (SCREEN / 'main.cpp').read_text()
    assert re.search(r'FloatingKeyboard \{[^}]*phoneLocales: phoneKeyboardLocales', (SCREEN / 'qml/Main.qml').read_text())
    keyboard = (SCREEN / 'qml/FloatingKeyboard.qml').read_text()
    assert 'property var phoneLocales' in keyboard and 'plasmakeyboardrc"' not in keyboard
