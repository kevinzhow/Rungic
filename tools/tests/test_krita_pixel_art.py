# SPDX-License-Identifier: MIT
"""The rungic-krita-pixel-art skill (2026-10-05): pixel.py draws exact pixels through Krita's API
(here a stand-in of it; the real Krita 6.0.1 was checked on the G100 S with kritarunner), and
setup.py turns on the Scripter plugin in kritarc only while Krita does not run."""
import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'agent/assistant/skills/rungic-krita-pixel-art'


class Node:
    def __init__(self, name, kind, w, h):
        self._name, self._type, self.w, self.h = name, kind, w, h
        self.px = bytearray(b'\xff' * (4 * w * h))       # Krita's new document: white

    def name(self): return self._name
    def type(self): return self._type

    def setPixelData(self, data, x, y, w, h):
        assert len(data) == 4 * w * h
        for row in range(h):
            start = 4 * ((y + row) * self.w + x)
            self.px[start:start + 4 * w] = data[4 * w * row:4 * w * (row + 1)]

    def pixel(self, x, y):
        start = 4 * (y * self.w + x)
        b, g, r, a = self.px[start:start + 4]
        return f'#{r:02x}{g:02x}{b:02x}{a:02x}'


class Root:
    def __init__(self): self.children = []
    def childNodes(self): return list(self.children)
    def addChildNode(self, node, above): self.children.append(node)


class Document:
    def __init__(self, w, h):
        self.w, self.h, self.root, self.active, self.saved = w, h, Root(), None, []
        self.root.children.append(Node('Background', 'paintlayer', w, h))
    def width(self): return self.w
    def height(self): return self.h
    def rootNode(self): return self.root
    def activeNode(self): return self.active
    def setActiveNode(self, node): self.active = node
    def createNode(self, name, kind): return Node(name, kind, self.w, self.h)
    def refreshProjection(self): pass
    def setBatchmode(self, value): pass
    def saveAs(self, path): self.saved.append(path); return True
    def exportImage(self, path, info): self.saved.append(path); return True
    def clone(self):
        twin = Document(self.w, self.h)
        twin.saved = self.saved
        return twin
    def scaleImage(self, w, h, xres, yres, strategy): self.saved.append(('scaled', w, h, strategy))
    def close(self): pass


def load_pixel():
    krita = types.ModuleType('krita')
    made = []

    class App:
        def createDocument(self, w, h, name, model, depth, profile, res):
            made.append(Document(w, h))
            return made[-1]
        def activeDocument(self): return None
        def activeWindow(self): return None
        def resources(self, kind): return {}
    krita.Krita = types.SimpleNamespace(instance=lambda: App())
    krita.InfoObject = object
    sys.modules['krita'] = krita
    spec = importlib.util.spec_from_file_location('pixel', SKILL / 'pixel.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, made


# covers: agent.instructions/E5
def test_pixel_draws_exact_pixels_in_the_open_document(tmp_path):
    p, made = load_pixel()
    doc = p.canvas(8, 6, 'test')
    base = doc.rootNode().childNodes()[0]
    assert base.pixel(0, 0) == '#00000000', 'transparent, not Krita\'s white'
    p.palette({'s': '#9fd3e6', 'g': '#4f8a3c'})
    p.rect(0, 0, 8, 3, 's')
    assert base.pixel(7, 2) == '#9fd3e6ff' and base.pixel(0, 3) == '#00000000'
    hills = p.layer('hills')
    p.rows(1, 3, ['.gg', 'ggg'])
    assert hills.pixel(1, 3) == '#ffffffff' and hills.pixel(2, 3) == '#4f8a3cff', "'.' leaves a pixel"
    p.line(0, 5, 7, 5, '#000000')
    assert all(hills.pixel(x, 5) == '#000000ff' for x in range(8))
    p.dither(0, 0, 2, 1, 's', 'g')
    assert (hills.pixel(0, 0), hills.pixel(1, 0)) == ('#9fd3e6ff', '#4f8a3cff')
    p.rect(6, 4, 10, 10, 'g')          # clipped at the canvas
    paths = p.save(tmp_path / 'tea.kra', scale=8)
    assert paths == [str(tmp_path / 'tea.kra'), str(tmp_path / 'tea.png'), str(tmp_path / 'tea-8x.png')]
    assert ('scaled', 64, 48, 'NearestNeighbor') in doc.saved, 'enlarged by nearest neighbour: sharp'


def run_setup(tmp_path, rc_text=None, krita_running=False):
    (tmp_path / 'bin').mkdir(exist_ok=True)
    pgrep = tmp_path / 'bin/pgrep'
    pgrep.write_text(f'#!/bin/sh\nexit {0 if krita_running else 1}\n')
    pgrep.chmod(0o755)
    rc = tmp_path / 'config/kritarc'
    rc.parent.mkdir(exist_ok=True)
    if rc_text is not None:
        rc.write_text(rc_text)
    done = subprocess.run([sys.executable, str(SKILL / 'setup.py')], capture_output=True, text=True,
                          env={**os.environ, 'XDG_CONFIG_HOME': str(tmp_path / 'config'),
                               'PATH': f'{tmp_path / "bin"}:{os.environ["PATH"]}'})
    return json.loads(done.stdout), rc


# covers: agent.instructions/E5
def test_setup_turns_on_the_scripter_and_keeps_the_rest_of_kritarc(tmp_path):
    result, rc = run_setup(tmp_path, '[General]\ntheme=Dark\n\n[python]\nenable_mutator=true\n')
    assert result == {'scripter': True, 'changed': True}
    text = rc.read_text()
    assert '[General]\ntheme=Dark' in text and 'enable_mutator=true' in text and 'enable_scripter=true' in text
    assert run_setup(tmp_path)[0] == {'scripter': True, 'changed': False}, 'once on, nothing to do'


def test_setup_without_a_kritarc_and_with_krita_running(tmp_path):
    result, rc = run_setup(tmp_path)
    assert result['changed'] and rc.read_text() == '[python]\nenable_scripter=true\n'
    other = tmp_path / 'other'
    other.mkdir()
    result, rc = run_setup(other, '[General]\n', krita_running=True)
    assert 'error' in result and rc.read_text() == '[General]\n', 'Krita running: kritarc left as it is'
