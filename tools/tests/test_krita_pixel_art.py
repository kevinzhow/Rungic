# SPDX-License-Identifier: MIT
"""The rungic-krita-pixel-art skill (2026-10-05): pixel.py draws exact pixels through Krita's API
(here a stand-in of it; the real Krita 6.0.1 was checked on the G100 S with kritarunner and in a
call, where the agent drew a 64x64 scene with it in about nine minutes)."""
import importlib.util
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
