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

### Portrait default

A portrait (a face was found and the picture cropped) defaults to 64 px and the **retro sprite look** (flat tones, speckle removed, bold outline); "smooth", "detailed" or `retro=false` turns it off, and a picture with no face keeps the smooth 48 px default.

## Camera

The camera belongs to the **orb app**, not to the backend. Clicking a camera button in the orb (beside the text box or on a card) opens a separate floating window: always on top, dark and translucent like the orb, remembering where you put it. It shows a live mirrored preview and a round shutter button and does **not** take a picture by itself.

1. **Shutter** (the button, or Space) starts a 3-second countdown, then captures one frame and turns the camera off.
2. **Retake** goes back to the live preview; **Use Photo** sends that one frame to the backend (`POST /v1/photo`, localhost, bearer token) and closes the window. The photo is held in memory for an hour as "this" for the art tools. Nothing is written to disk unless you say "save this card" (`photo.jpg` and a note in `~/Julius Saved/`).
3. **Close** (the window's button) or **Esc** stops the camera immediately. The camera light is on only while the live preview or the countdown is showing, and is off whenever the window is closed (or showing a shot you are deciding about).

By voice, "take a photo" or "take a selfie" opens the same window and starts the countdown by itself. "Take a selfie and make it pencil sketch" continues to the style after you press Use Photo, or by itself 5 seconds after the shot if you do not respond. The `[vision] camera = false` setting and a macOS camera refusal switch the buttons off with a short explanation (`panel.camera_status`). The first use makes macOS ask Julius Orb for camera permission.

## Sprite mode

`image_style` with `style` = `sprite` ("make me a sprite", "make a game sprite of me", "turn me into a Game Boy character") builds a head-only game sprite from **measurements**, not from a model. "Pixel art" and "pixelate" stay on the photo filter above; the two are shown side by side so the difference is visible.

Apple Vision, inside the one-shot helper (`visiond landmarks`, on this Mac only), supplies the face landmarks (jaw contour, eyes, brows, nose, mouth) and a person mask. `julius/art/sprite.py` then builds the head cell by cell at sprite scale:

- **Head shape** from the jaw contour closed over the forehead; **hair** is one solid, simplified mass cut from the person mask above and beside the face (dark facial hair inside the jaw line is kept as a beard); both are reduced to the grid by coverage, holes filled, specks removed.
- **Eyes** are 1 or 2 pixel marks, **brows** and **mouth** short lines (the corners step up for a smile; a second row when open), the **nose** is one optional pixel.
- **Colours**: skin and hair are measured in the photo and each mapped to the nearest palette shade. `gameboy` (default) maps by lightness: outline = darkest shade, skin one of the lightest, hair darker than the skin; `pico8` and `nes` (a 16-colour NES-like selection) take the nearest colour. The outline is 1 px in the darkest shade, the background transparent.
- **Options**: `head` 16 (default, on a 32x32 canvas), 24 or 32; `palette`; `chibi` (rounder head, features low and wide-set); `big_eyes` (2x2 with a glint).

Same picture and options always give the same sprite. The card (**panel.art**, kind `sprite`) shows the photo, the sprite and the pixel-art filter output in a row; "save this card" writes `sprite-1x.png` (transparent), `sprite-4x.png` and `sprite-8x.png` (nearest-neighbour zooms) and `card.md`. Without a face in the picture it says so instead of guessing. The body comes later, in the Sprite studio stage.

## Photo style filters (step C)

`image_style` with `style` = `cel_shade`, `comic`, `pencil`, `pop_art` or `watercolor` (any wording: "comic book", "pencil sketch", "make it a cartoon"). Pillow and numpy only (no OpenCV or scikit-image, so no new licence question); the same input and settings always give the same picture. The picture is worked at up to 900 px on the long side and shown as a **panel.art** card of kind `filter` (before and after, settings as text). "Save this card" writes `restyled.png` and `card.md`. Transparent areas become white paper. Three knobs, each used where it makes sense:

| Style | What it does | colors | line | smooth |
|---|---|---|---|---|
| cel_shade | bilateral smoothing, flat colours, black ink lines on strong edges | 6 | 2 | 3 |
| comic | flat colours, a 45-degree halftone of dots in the shadows, ink lines | 4 | 2 | 2 |
| pencil | colour-dodge sketch on light paper with grain | none | 2 | 1 |
| pop_art | luminance in bands mapped to a bold fixed palette, thick ink | 4 | 3 | 2 |
| watercolor | washes of flat colour, pigment at the edges, paper texture | 14 | 1 | 4 |

Say "show me all five styles" (`style` `all`) for one card with the original and all five at their defaults, side by side. "Take a selfie and make it a pencil sketch" takes the photo and applies the style in one go.

`colors` is 2 to 32, `line` 1 to 5 (ink weight, or pencil softness), `smooth` 0 to 5. Left out, each style uses its own defaults.

## Not built yet

C: style filters (cel-shade, comic ink, pencil sketch, posterize, watercolor-ish). D: tracing guide and drawing digitizer. E: logo to SVG with vtracer. F: pixel grid editor, sprite-sheet slicing and assembly, animation preview. OpenCV and scikit-image licences get verified before C uses either.
