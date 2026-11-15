# Art tools (stage 5c-art)

Deterministic image processing only: no generative models, no style-transfer networks, nothing leaves this computer, and the same input with the same settings always gives the same pixels. Julius helps with the busywork; the art is yours.

## Where images come from

- **An image dropped on the orb:** always allowed (you chose it). It is only read.
- **A file by name or path:** only inside the folders listed under `[art] folders` in `julius.toml` (default `~/Julius Art`, `~/Pictures`, `~/Desktop`, `~/Downloads`). A path outside them is refused, including a symlink that leads out of them.
- **Never purchased or licensed asset packs.** A folder below an approved folder is skipped when its name says asset, asset pack, purchased, Unity, Unreal, Kenney, Humble, itch, Envato, stock, licensed, bundle and similar, or when it holds a licence/EULA file (packs ship one). A name search does not even look inside such folders. If the image really is yours, drop it on the orb.
- **Photos libraries are never read in place:** nothing inside `Photos Library.photoslibrary` (or any `*.photoslibrary`, `*.photolibrary`, `*.aplibrary`) is opened, searched or listed as an art folder, even through a symlink. Export the picture from Photos or drop it on the orb.
- The protected music locations (Logic, GarageBand, plug-ins, and so on) stay off limits here too (`julius/music/policy.py`).
- Originals are never changed, moved or copied. Pictures held for a card live in memory only (at most an hour) and reach disk only when you say **save this card**.

## Photo to pixel art (step B)

"Turn this photo I just dropped into pixel art", "make sunset.jpg 32 pixels with the gameboy palette, outline, 8x". The `pixel_art` tool takes:

| Setting | What it does |
|---|---|
| size | pixels on the long side of the art itself, 8 to 256 (default 48); the aspect ratio is kept |
| colors | palette size taken from the picture, 2 to 64 (default 16), by median cut with no dithering |
| palette | a fixed palette instead: `gameboy` (4 greens), `pico8` (16), `c64` (16), `1bit` (black and white) |
| outline | one dark pixel around the cut-out subject (the canvas grows by one pixel each side), or, on a solid picture, ink on the bright side of strong edges |
| transparent | the colour that fills the border, and everything of it connected to the border, becomes transparent (works best when the background is clearly different from the subject; a one-colour picture is left alone) |
| scale | export zoom 1, 2, 4 or 8 (default 4), nearest neighbour so every art pixel stays a crisp square; never larger than 4096 px on a side |

How it works: area-average downscale (weighted by opacity), snap each pixel to the nearest palette colour, optional background cut-out, optional outline, then the nearest-neighbour export. Half-transparent pixels do not exist in pixel art, so alpha is thresholded.

The side panel shows a **panel.art** card: before and after side by side (the result drawn with hard pixel edges), the settings used and the palette. "Save this card" writes a folder in `~/Julius Saved/` (the `[ui] saved_dir` setting) with `pixel-art.png` (the export), `pixel-art-1x.png` and a short `card.md`. The PNG has an alpha channel only when something is transparent.

## Not built yet

C: style filters (cel-shade, comic ink, pencil sketch, posterize, watercolor-ish). D: tracing guide and drawing digitizer. E: logo to SVG with vtracer. F: pixel grid editor, sprite-sheet slicing and assembly, animation preview. OpenCV and scikit-image licences get verified before C uses either.
