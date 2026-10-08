"""Text on phone panels: grayscale antialiasing with slight hinting (Kevin 2026-10-08)."""
import configparser
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
ETC = ROOT / 'system/config/etc'


# covers: desktop.display-size/E7
def test_kde_defaults_are_grayscale_antialiasing():
    config = configparser.ConfigParser(strict=False)
    config.optionxform = str
    config.read(ETC / 'xdg/kdeglobals')
    assert dict(config['General']) == {'XftAntialias': 'true', 'XftHintStyle': 'hintslight', 'XftSubPixel': 'none'}


# covers: desktop.display-size/E7
def test_fontconfig_turns_ubuntus_rgb_subpixel_off_after_it():
    conf = ETC / 'fonts/conf.d/11-rungic-grayscale.conf'
    # conf.d is read in name order: after 10-sub-pixel-rgb.conf, before the user's 50-user.conf.
    assert '10-sub-pixel-rgb.conf' < conf.name < '50-user.conf'
    root = ET.fromstring(conf.read_text())
    edits = {(m.get('target'), e.get('name'), e.get('mode'), e.find('const').text)
             for m in root.findall('match') for e in m.findall('edit')}
    assert edits == {('pattern', 'rgba', 'assign', 'none'), ('font', 'rgba', 'assign', 'none')}
