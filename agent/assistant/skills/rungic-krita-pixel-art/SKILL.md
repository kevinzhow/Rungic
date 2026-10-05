---
name: rungic-krita-pixel-art
description: >-
  Make pixel art in Krita on this phone while the user watches: sharp pixels on a grid, a small palette,
  no antialiasing, for game sprites, tiles and pixel scenes. Use when the user asks for pixel art,
  像素画 or 像素风 in Krita, or for pixel art and names no app. Read rungic-phone-desktop first for the
  desktop tools.
---

# Pixel art in Krita

Pixel art has exact pixels: a small canvas, a small palette, no blur and no antialiasing.
Krita paints soft strokes by default. Do the work with the scripts and settings below, not with
Krita's defaults.

## When to use this skill

- "在 Krita 里画一幅像素画", "像素风的茶园", "make a 32x32 sprite", "draw pixel art tiles".
- Pixel art when the user names no app. This phone has Krita. It does not have Pixelorama.
- Do not generate the picture with image generation and then open it in Krita. The user asked to
  see it drawn in Krita.

## Files

- `/usr/share/rungic-voice-agent/skills/rungic-krita-pixel-art/setup.py`: run it in the shell
  before Krita starts. It turns on Krita's Scripter plugin. It prints JSON. If Krita runs, close
  Krita first.
- `/usr/share/rungic-voice-agent/skills/rungic-krita-pixel-art/pixel.py`: functions that draw
  exact pixels into the document open in Krita. Its first lines list the functions.

## Steps

1. Plan the picture before you draw. Choose the size and the palette:
   - A sprite or a tile: 16x16 to 64x64.
   - A scene: 64 to 160 pixels wide. A portrait scene (竖屏): 90x160 or 108x192.
   - A palette of 6 to 16 colours, with 2 or 3 shades of each main colour.
   Write the plan as steps (`update_plan`): background, far shapes, near shapes, details, save.
2. Run `setup.py`. Then open Krita with `desktop_launch` on the screen where you work.
3. Open the Scripter: menu Tools > Scripts > Scripter. In its editor, type this line and run it
   (the Run button, or Ctrl+R):
   `exec(open('/usr/share/rungic-voice-agent/skills/rungic-krita-pixel-art/pixel.py').read())`
4. Draw with short scripts, one step at a time. Run each script. The canvas shows the result at
   once, so the user sees the picture grow. Example:
   ```python
   canvas(108, 192, 'xinyang-tea-hills')
   palette({'sky': '#a8d8ea', 'mist': '#e3f1f0', 'far': '#7fa99b', 'tea': '#4f8a3c', 'tea2': '#3b6e2e'})
   rect(0, 0, 108, 90, 'sky')
   layer('far hills')
   ellipse(30, 95, 40, 18, 'far')
   layer('tea rows')
   for y in range(120, 192, 6):
       line(0, y, 107, y - 10, 'tea')
   ```
   - Work from back to front: sky, far hills, near hills, objects, details. Put each depth on a
     layer of its own (`layer(name)`).
   - Use `rows()` for small shapes drawn pixel by pixel (a tea house, a figure, a tree).
   - Use `dither()` for soft transitions between two colours, for example mist on hills.
   - Write `note` text in `desktop_act` in the user's words ("画远山", "加茶垄"), not about scripts.
5. Touch up by hand where it helps. Call `brush()` for the 1-pixel "u) Pixel Art" brush. Zoom in
   and draw with `desktop_act` drags on the canvas.
6. Save: `save('~/Pictures/<name>', scale=8)`. It writes `<name>.kra`, `<name>.png` (the real
   pixels) and `<name>-8x.png` (enlarged by nearest neighbour, sharp). Show the 8x picture in your
   answer and give the .kra file as a link.

## Krita settings for drawing by hand

`pixel.py` draws exact pixels with no settings. When you use Krita's own tools, set them first
(Tool Options docker, right side). From the guide "Making Pixel Art With Krita: Settings"
(pixelglade.net.au, 2026-05-10):

| Tool | Settings |
|---|---|
| Freehand brush | Preset "u) Pixel Art", size 1 px. Brush smoothing: Pixel. |
| Fill (bucket) | Threshold 1, Anti-aliasing off, Feather 0 px, Spread 100 %, Close Gap 0 px. Or the preset "u) Pixel Art Fill". |
| Selection tools | Mode: Pixel Selection. Anti-aliasing off. Feather 0 px. Threshold 1 (contiguous selection). |
| Shapes (rectangle, ellipse, polygon, polyline, bezier) | Fill: Not Filled. Outline: Brush. Use the pixel brush. |
| Transform | Filter: Nearest Neighbor. Other filters make new colours. |
| Text | Text rendering: Optimize Speed (no antialiasing). |
| Grid | View > Show Grid. Grid docker: 1x1, 8x8 or 16x16 px. |
| Scale image | Image > Scale Image To New Size, filter Nearest Neighbor. |

Do not use blur, smudge, soft brushes, the airbrush or bilinear and bicubic scaling. They make
colours between the pixels.

## Facts about this Krita

- Krita 6.0.1 (Ubuntu package). Python scripting works. The brush presets with "Pixel" in the name
  are "u) Pixel Art", "u) Pixel Art Dithering" and "u) Pixel Art Fill".
- In the Python API, `setPixelData` takes bytes in BGRA order. `pixel.py` converts from `#rrggbb`.
- A new document from `createDocument` has a white background layer. `canvas()` makes it
  transparent, or the colour that you give.
