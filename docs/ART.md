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

Two routes reach the same code. A fixed pattern answers the plain phrasings with no model call ("turn this photo I just dropped into pixel art", "make sunset.jpg pixel art, 32 pixels, gameboy palette, outline, 8x"); everything else goes to the brain's **`image_style`** tool ("make my selfie look 8-bit", "turn this into a retro game sprite", "give this pic a Game Boy look"). `image_style(source, style, size, colors, palette, outline, transparent, zoom, dither, full_photo, compare, retro)` only transforms a picture you provide and never creates art from scratch: with no picture named it uses the one dropped on the orb, or asks for one, and "draw me a dragon" is declined. `style` is `pixel_art` for now (step C's filters will be added as more styles). The settings are:

| Setting | What it does |
|---|---|
| size | pixels on the long side of the art itself, 8 to 256 (default 48, or 64 for a portrait, which is capped at 128); the aspect ratio is kept |
| colors | palette size taken from the picture, 2 to 64 (default 16), by median cut with no dithering |
| palette | a fixed palette instead: `gameboy` (4 greens), `pico8` (16), `c64` (16), `1bit` (black and white) |
| outline | one dark pixel (the palette's darkest colour) around the cut-out subject (the canvas grows by one pixel each side), or, on a solid picture, ink on the bright side of strong edges only (a luminance step of 0.3 or more after levels) |
| dither | "dither on": ordered 4x4 Bayer dithering, the same pixels every time |
| retro | "retro" or "sprite": speckle removed with a majority filter, flat tone areas, and a bold outline along the edges between tone shapes |
| full photo | "full photo" skips the portrait crop |
| transparent | the colour that fills the border, and everything of it connected to the border, becomes transparent (works best when the background is clearly different from the subject; a one-colour picture is left alone) |
| scale (zoom) | export zoom 1, 2, 4 or 8 (default 4), nearest neighbour so every art pixel stays a crisp square; never larger than 4096 px on a side |

How it works: area-average down to about 3x the final size, edge-preserving (bilateral) smoothing, area-average to the final size. For palettes of 8 colours or fewer (gameboy, 1bit, or `colors` of 8 or less) the levels are stretched (2nd to 98th percentile, plus half-strength histogram equalisation) and each pixel is mapped by luminance, darkest tone to darkest colour, so a flat bright photo still uses every tone; larger palettes snap to the nearest colour. Then optional dither, background cut-out, outline, and the nearest-neighbour export. Half-transparent pixels do not exist in pixel art, so alpha is thresholded.

The side panel shows a **panel.art** card: before and after side by side (the result drawn with hard pixel edges), the settings used and the palette. "Save this card" writes a folder in `~/Julius Saved/` (the `[ui] saved_dir` setting) with `pixel-art.png` (the export), `pixel-art-1x.png` and a short `card.md`. The PNG has an alpha channel only when something is transparent.

### Portraits and the comparison grid

A photo with a face in it is a **portrait**: Apple's Vision framework finds the face inside the one-shot helper (`visiond faces`: the picture goes in on stdin, only boxes come back; nothing is uploaded or written). The picture is cropped to head and shoulders (3 face-widths wide, 0.8 face-heights above and 1.2 below, kept inside the picture), the levels are taken from the face so it gets the whole tone range, and the default size is 64 px (128 at most). With no face, no helper, or "full photo", the whole picture is used at the usual 48.

"Show me a pixel art comparison grid of this photo" (or `compare=true`) shows one card with nine labelled variants: 32, 48 and 64 px, in rows *smooth*, *dither* and *retro sprite*; palette `gameboy` unless another is named. Pick one with "48 pixels, retro".

## Camera

"Take a photo" / "take a selfie" (also the camera button in the orb) starts a 3-second countdown with a live, mirrored preview in a **panel.camera** card, then takes one full frame. The helper (`visiond live 3`) keeps the camera session open the whole time, so the camera light is on while the preview runs and while the photo is taken, and goes off when the helper exits. Preview frames and the photo come back over the helper's stdout into memory (preview frames in a small separate slot of three, so they never push real pictures out); nothing is written to disk. The photo is held in memory for an hour and is "this" for the art tools until something else is dropped or another photo is taken: "take a selfie and make it pixel art" does both. "Save this card" writes `photo.jpg` and a note to `~/Julius Saved/`; nothing else saves it. The `[vision] camera = false` setting switches it off. "Take a photo" no longer goes to the vision look (which describes a camera frame with Claude); "what am I holding" still does.

## Not built yet

C: style filters (cel-shade, comic ink, pencil sketch, posterize, watercolor-ish). D: tracing guide and drawing digitizer. E: logo to SVG with vtracer. F: pixel grid editor, sprite-sheet slicing and assembly, animation preview. OpenCV and scikit-image licences get verified before C uses either.
