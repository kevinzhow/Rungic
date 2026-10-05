# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixel art in Krita, from its Scripter (Tools > Scripts > Scripter): exact pixels with no
antialiasing, drawn into the document open in the window, so the user watches the picture grow.
Load this file in Scripter and run it once; then call the functions below from Scripter.

    canvas(64, 96, 'tea-hills')                 a new document: transparent, zoomed in, grid on
    palette({'s': '#9fd3e6', 'g': '#4f8a3c'})   name the colours once (a palette of a few colours)
    rect(0, 0, 64, 40, 's')                     a filled rectangle
    rows(10, 50, ['..gg..', '.gggg.'])          pixels from rows of palette keys ('.' is left as is)
    line(0, 60, 63, 52, 'g')                    a 1-pixel line (Bresenham)
    ellipse(32, 20, 6, 6, '#ffe9a8')            a filled ellipse
    dither(0, 40, 64, 8, 's', 'g')              a checker dither between two colours
    layer('trees')                              a new layer above, and work there
    brush()                                     the 1-pixel "u) Pixel Art" brush, to draw by hand
    save('~/Pictures/tea-hills', scale=8)       .kra, a 1x .png and an 8x .png (nearest neighbour)

Colours: '#rrggbb', '#rrggbbaa' or a key of palette(). Coordinates are pixels from the top left.
"""
import os

from krita import InfoObject, Krita

_palette = {}
_state = {'doc': None, 'node': None}


def _doc():
    doc = Krita.instance().activeDocument() or _state['doc']
    if doc is None:
        raise RuntimeError('No document: call canvas(width, height) first')
    return doc


def _node():
    doc = _doc()
    node = doc.activeNode() or _state['node']
    if node is None or node.type() != 'paintlayer':
        node = next((n for n in reversed(doc.rootNode().childNodes()) if n.type() == 'paintlayer'), None)
    if node is None:
        raise RuntimeError('No paint layer: call layer(name)')
    return node


def _bgra(colour):
    colour = _palette.get(colour, colour)
    text = colour.lstrip('#')
    if len(text) not in (6, 8):
        raise ValueError(f'colour {colour!r}: use #rrggbb, #rrggbbaa or a palette key')
    r, g, b = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    a = int(text[6:8], 16) if len(text) == 8 else 255
    return bytes((b, g, r, a))


def refresh():
    """Show what was drawn (each function does it)."""
    _doc().refreshProjection()


def palette(colours):
    """Name colours once: palette({'s': '#9fd3e6'}). Returns the whole palette."""
    _palette.update(colours)
    return dict(_palette)


def canvas(width, height, name='pixel-art', background=None):
    """A new document of width x height pixels in the window: transparent (or `background`),
    zoomed in to fit, with the pixel grid on."""
    app = Krita.instance()
    doc = app.createDocument(int(width), int(height), name, 'RGBA', 'U8', '', 72.0)
    base = doc.rootNode().childNodes()[0]
    fill = _bgra(background) if background else bytes(4)
    base.setPixelData(fill * (width * height), 0, 0, width, height)
    _state['doc'], _state['node'] = doc, base
    window = app.activeWindow()
    if window is not None:
        view = window.addView(doc)
        try:
            view.canvas().setZoomLevel(max(1.0, min(1600 / width, 900 / height)))
        except Exception:  # noqa: BLE001 - zoom stays as Krita sets it
            pass
        for name in ('view_grid', 'view_pixel_grid'):
            action = app.action(name)
            if action is not None and action.isCheckable() and not action.isChecked():
                action.trigger()
    doc.refreshProjection()
    return doc


def layer(name):
    """A new paint layer above the others, made the one drawn on."""
    doc = _doc()
    node = doc.createNode(name, 'paintlayer')
    doc.rootNode().addChildNode(node, None)
    doc.setActiveNode(node)
    _state['node'] = node
    doc.refreshProjection()
    return node


def rect(x, y, w, h, colour):
    """A filled rectangle, clipped to the canvas."""
    doc = _doc()
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(doc.width(), int(x) + int(w)), min(doc.height(), int(y) + int(h))
    if x1 <= x0 or y1 <= y0:
        return
    _node().setPixelData(_bgra(colour) * ((x1 - x0) * (y1 - y0)), x0, y0, x1 - x0, y1 - y0)
    refresh()


def pixel(x, y, colour):
    rect(x, y, 1, 1, colour)


def rows(x, y, lines, colours=None):
    """Pixels from rows of keys: rows(4, 10, ['.ab', 'abb']); '.' and ' ' leave a pixel as it is."""
    keys = dict(_palette, **(colours or {}))
    node, doc = _node(), _doc()
    for dy, line in enumerate(lines):
        for dx, key in enumerate(line):
            px, py = int(x) + dx, int(y) + dy
            if key in '. ' or not (0 <= px < doc.width() and 0 <= py < doc.height()):
                continue
            node.setPixelData(_bgra(keys.get(key, key)), px, py, 1, 1)
    refresh()


def line(x0, y0, x1, y1, colour):
    """A 1-pixel line with no antialiasing (Bresenham)."""
    node, value, doc = _node(), _bgra(colour), _doc()
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        if 0 <= x0 < doc.width() and 0 <= y0 < doc.height():
            node.setPixelData(value, x0, y0, 1, 1)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    refresh()


def ellipse(cx, cy, rx, ry, colour):
    """A filled ellipse around (cx, cy)."""
    for dy in range(-int(ry), int(ry) + 1):
        half = int(round(rx * (1 - (dy / ry) ** 2) ** 0.5)) if ry else int(rx)
        rect(int(cx) - half, int(cy) + dy, 2 * half + 1, 1, colour)


def dither(x, y, w, h, first, second):
    """A checker dither of two colours over a rectangle."""
    a, b = _bgra(first), _bgra(second)
    node, doc = _node(), _doc()
    for py in range(max(0, int(y)), min(doc.height(), int(y) + int(h))):
        for px in range(max(0, int(x)), min(doc.width(), int(x) + int(w))):
            node.setPixelData(a if (px + py) % 2 == 0 else b, px, py, 1, 1)
    refresh()


def brush(size=1):
    """The aliased 1-pixel brush ("u) Pixel Art"), to draw by hand on the canvas."""
    app = Krita.instance()
    window = app.activeWindow()
    view = window.activeView() if window else None
    if view is None:
        raise RuntimeError('No view: open the document in the window first')
    preset = app.resources('preset').get('u) Pixel Art')
    if preset is not None:
        view.setCurrentBrushPreset(preset)
    view.setBrushSize(size)


def save(path, scale=8):
    """The document as .kra, a .png of its pixels and a .png scaled `scale` times by nearest
    neighbour, which keeps every pixel sharp. -> the paths."""
    doc = _doc()
    base = os.path.expanduser(str(path))
    for suffix in ('.kra', '.png'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    os.makedirs(os.path.dirname(base) or '.', exist_ok=True)
    doc.setBatchmode(True)
    try:
        doc.saveAs(base + '.kra')
        doc.exportImage(base + '.png', InfoObject())
        out = [base + '.kra', base + '.png']
        if scale and int(scale) > 1:
            big = doc.clone()
            big.setBatchmode(True)
            big.scaleImage(doc.width() * int(scale), doc.height() * int(scale), 72, 72, 'NearestNeighbor')
            big.exportImage(f'{base}-{int(scale)}x.png', InfoObject())
            big.close()
            out.append(f'{base}-{int(scale)}x.png')
        return out
    finally:
        doc.setBatchmode(False)
