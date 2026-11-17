# Body and clothing on the sprite layer system: plan (not built)

Status: plan only, 2026-11-16. It extends the editable sprite document (`julius/art/sprite_doc.py`): the head stays exactly as it is; a body and clothes are more layers on the same canvas, with the same rules (parameters re-render, palette locked, per-layer pixel edits, undo/redo, `.julius-sprite` files). Nothing generative: every pixel is drawn by code from parameters, measured from the user's photo, or drawn by the user in the editor.

**Which sprite**: body, clothing and animation start from his **main sprite** (`art.tool.main_sprite()`, set by "make this my main sprite") unless he names another.

## 1. The canvas and the rig

- The head canvas (2 x head) becomes the top of a taller **figure canvas**: for a 24 px head, 48 x 96 (normal) or 48 x 72 (chibi). The head document is embedded unchanged; its `outline` keeps following the head.
- A **rig**: named joints in canvas coordinates: neck, shoulders (L/R), elbows, wrists, hips (L/R), knees, ankles, toes. Every body and clothing part is drawn relative to joints, never to fixed pixels. A **pose** is a set of joint positions (plus a facing: front, side, three-quarter); the default pose is "stand, front".
- Proportions from parameters: `build` (slim, average, broad), `height` (short, average, tall: leg and torso length), `proportions` (chibi: about 2 heads tall, short limbs; normal: about 4 to 5 heads tall for sprites). Optionally measured from a full-body photo (Vision body pose: shoulder width over head width, leg length over torso) when one is given.

## 2. Body layers

Drawing order, back to front, each with its own outline rule and parameters:

| Layer | Drawn from | Parameters |
|---|---|---|
| back arm | shoulder, elbow, wrist | thickness from build |
| legs | hips, knees, ankles | length from height, gap, thickness |
| torso | neck, shoulders, hips | width from build, length from height |
| head attachment (neck) | neck to head base | length 1 to 2 px |
| front arm, hands | shoulder, elbow, wrist | hand size 1 to 2 px |
| feet | ankles, toes | length, facing |

Skin uses the head's skin colour role; the shared outline is recomputed around head + body + clothes.

## 3. Clothing layers

Each clothing item is a **parameterised shape attached to the rig**, drawn in palette colour roles, on its own layer above the body: shirt (t-shirt, long sleeve), hoodie (with hood and pocket), jacket (open/closed), pants (long, jeans with a seam shade), shorts, shoes (sneakers, boots), hat (cap and beanie move here from the head layers, so they work with a body too).

- **Colours** are roles mapped to the locked palette (`main`, `shade`, `accent`), so "red hoodie" picks the palette's nearest red by hue, and a Game Boy sprite keeps its four shades (by role, like the head).
- **Fit** follows the body: sleeves end at the wrist joint (long) or mid upper arm (short); pants end at the ankle; the hoodie covers the torso to the hips.
- **Voice edits** through the same `sprite_edit` words: "red hoodie and jeans", "take off the jacket", "shorts instead of pants", "white sneakers", "make the hoodie darker". Card buttons gain a small clothing row (instant, like the head buttons).

## 4. Starter set (code-drawn)

Ten items to begin with, drawn from parameters so they fit any build and size: t-shirt, long-sleeve shirt, hoodie, jacket, jeans, chinos, shorts, sneakers, boots, cap. Each in its own module function with a unit test that renders it at 16, 24 and 32 px heads, chibi and normal, and checks: inside the canvas, attached to the right joints, palette-locked, outline closed.

## 5. Personal item library

- In the editor window, **"Save as item"** on a clothing layer (or a selection on any layer) stores it as an item: the pixels relative to the rig joints it covers (for example a jacket pattern stored relative to shoulders and hips), its colour roles (not fixed colours, so it recolours with the palette), the head size and proportions it was drawn at, and a name.
- Items live in `~/Julius Saved/Sprite items/<name>.julius-item` (JSON), listed by voice ("what items do I have"), added by name ("put on my denim jacket"), and shown as a strip on the card.
- An item drawn for one size is scaled by joint distances (nearest neighbour, then cleaned of stray pixels) when used on another size; the result is shown so it can be touched up and saved back.

## 6. Following poses (for rotoscoped animation later)

- Rotoscope mode (Sprite studio stage) records a short move; Vision body pose gives the joints per frame; those become a **pose track** for the rig.
- Every body and clothing layer is drawn from joints, so each frame re-renders from the same parameters at that frame's joint positions: the hoodie, jeans and shoes follow the arms and legs automatically.
- Personal items and pixel edits are stored relative to joints (the nearest one and its segment), so they move with their joint; small rotations use a pixel-safe method (RotSprite-style) or pre-drawn angles (8 directions) for limbs that rotate a lot.
- Consistency between frames: the palette is locked and colours are roles, so nothing flickers; an onion skin in the editor shows the previous and next frames for touch-ups; touch-ups per frame are stored as frame edits on top of the layer, like today's pixel edits.
- Export: a sprite sheet (one row per move) and a frame-timing JSON.

## 7. Effort per part

| Part | Effort |
|---|---|
| Figure canvas, rig, default pose, proportions (chibi / normal), outline across head and body | 2 days |
| Body layers with build and height parameters, tests across sizes | 1.5 days |
| Clothing framework (colour roles, fit to rig, layering) | 1.5 days |
| Starter set of 10 items with tests | 2 days |
| Voice edits and card buttons for clothing (instant endpoint), routing ×3 and the shake | 1 day |
| Personal item library (save from editor, list, wear, rescale) | 2 days |
| Pose following groundwork (joint-relative edits and items, a pose track format, 8-direction limb drawing) | 2 days |
| Docs and a visual check sheet per size | 0.5 day |
| **Total** | **about 12.5 days** (the rotoscope recording and sheet export are the later Sprite studio stage) |
