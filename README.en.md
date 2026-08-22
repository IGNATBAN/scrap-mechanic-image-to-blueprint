**English** · [Русский](README.md)

# SM_Pixel

A converter that turns images into Scrap Mechanic blueprints. You feed it an
ordinary picture and get a structure made of painted blocks that the game
opens as one of its own blueprints.

It comes in two forms: a page in the browser and a local Python program. Both
compute the same thing, and that is verified automatically.

**[Open in the browser →](https://ignatban.github.io/scrap-mechanic-image-to-blueprint/)**

---

## Quick start

### In the browser

Open the [page](https://ignatban.github.io/scrap-mechanic-image-to-blueprint/)
and drop an image on it. Everything runs locally, the file is never uploaded —
the site has no server at all, it is plain static hosting.

The result downloads as an archive. Put the folders from it into

```
%APPDATA%\Axolot Games\Scrap Mechanic\User\User_<your SteamID>\Blueprints\
```

and the blueprint shows up in the game's Blueprints list.

### Locally

```bash
pip install -r requirements.txt
py run.py
```

This opens `http://127.0.0.1:8792`. The game folder and the blueprints folder
are found automatically, so there is no archive to unpack — the program writes
blueprints straight there. It also reads the palette, the block catalogue and
the interface fonts from your installed copy of the game, which means it picks
up mods and patches.

---

## How it works

The path from image to blueprint is five steps, each in its own module.

### 1. Image to grid

The picture is cropped, scaled to the requested size in blocks and adjusted:
brightness, contrast, saturation, gamma. One block in game is 0.25 m, so a
200×120 grid becomes a 50×30 metre structure.

Transparent pixels can either be cut out — leaving holes in the build — or
filled with a chosen background.

### 2. Colour matching

There are two modes.

**Exact RGB** takes the colour straight from the image. The blueprint format
does not restrict the palette, the engine renders any shade, and the result is
as close to the original as it gets. The downside is that the in-game paint
tool cannot produce those shades, so you cannot repaint a block to the same
colour by hand.

**Game palette** uses only the forty paint tool colours. The build can then be
touched up in game, but forty colours is not much, and without extra tricks the
image falls apart.

Nearest-colour matching happens in **OKLab** rather than RGB. In OKLab the
Euclidean distance roughly matches how different two colours look, so shadows
do not turn muddy and highlights do not go acid.

The lightness axis can be weighted separately. That is a trade, not an
improvement:

| weight | lightness error | colour error |
|---|---|---|
| 1.0 | 0.0621 | 0.0339 |
| 1.4 | 0.0582 | 0.0380 |
| 1.8 | 0.0551 | 0.0427 |
| 3.0 | 0.0493 | 0.0536 |

The default is 1.0. Raising it makes sense where light and shadow matter more:
faces, text, logos.

### 3. Dithering

So that forty colours do not look like a poster, neighbouring blocks are set to
different colours and the eye blends them into an intermediate tone from a
distance. Six error diffusion kernels are available — Floyd–Steinberg, Jarvis,
Stucki, Burkes, Sierra, Atkinson — plus two ordered masks: a Bayer matrix and
blue noise.

Diffusion runs in OKLab and follows a **serpentine** path: even rows left to
right, odd rows right to left. That removes the diagonal worm artefacts a
one-directional pass leaves behind.

The blue noise mask is generated with the void-and-cluster method
(`core/bluenoise.py`). Unlike a Bayer matrix it has no periodicity: a regular
pattern reads as a chequered ripple on blocks, while blue noise reads as an
even tone. The resulting mask holds 0.06 % of its energy in the low frequencies
against roughly 4 % for white noise.

Judging dithering pixel by pixel makes no sense — it deliberately places
inaccurate colours next to each other. So the tool reports a **distance error**:
both the original and the result are blurred over a small window and only then
compared. Next to it sits a close-up error, per block: dithering makes that one
worse while large flat areas make it better. Both are worth watching.

### 4. Extending the palette with materials

A block texture in Scrap Mechanic is not an albedo but an **overlay on top of
the paint**. In the `dif` file from a `.shapeset` the RGB carries the material's
own tone and the alpha channel carries the strength of the overlay:

```
result = paint × (1 − alpha) + tint
```

The values are taken from the game files (`tools/build_materials.py`, cached in
`data/materials.json`). The spread is wide: glass has `alpha = 0.00` and shows
the paint as is, plastic 0.05, concrete 0.06, wood-1 0.16, wood-2 0.36,
metal-2 0.57.

What this buys in practice is exactly the muted and dark part of the spectrum
that the paint tool palette lacks. Forty paint colours across a couple of dozen
blocks, with near-duplicates filtered out, give several hundred distinguishable
colours, and the average error drops by roughly half.

Shiny blocks are left out of the set. The `A` channel of the `asg` file is
reflection strength, and a block with a lot of it reflects the sky in game
instead of showing the paint. Measured against a screenshot from the game:
for `blk_concrete1` (reflection 0.08) the model lands within 4 of 255, for
`blk_metal2` (0.50) it is off by +82 of 255.

### 4b. Pattern: a block has more than one shade

The game maps the texture **across the body of the build**, not per block: a
texture spans exactly `tiling` blocks, and a block at local position (x, z)
shows its own cell `(x mod tiling, z mod tiling)`. Two things follow.

First: a wall of identical blocks in one paint is not a flat fill but a
repeating pattern. The lightness spread between the darkest and the lightest
position reaches 89 of 255 for insulation and 67 for concrete-3, while for
concrete-1 and metal-1 it is two.

Second: if you know which cell lands on which grid square, you can match colour
more precisely. Without dithering the search runs against the right position
directly; inside error diffusion there is no time for that, so the shared
lookup table's answer is rewritten through a remap table built in advance —
same speed. The error drops by 15–20 % "from afar" and 20–25 % "up close", and
flat areas lose the plaid ripple that was never in the original.

All of this rests on the pattern phase being equal to the blueprint's local
coordinates, and that is **verified in the game itself**: a blueprint of fifteen
16×16 fields at different offsets was built, the screenshot read back by
machine, and all fifteen matched with no correction (0.85 against 0.21 for the
runner-up). The same shot confirmed that merging blocks into rectangles does not
change the pattern: a field built block by block, in rows, and as a single part
looks identical.

When the picture is split into modules the pattern is left out of matching: the
player welds the modules, the welded body has its own coordinate system, and the
overall shift of the pattern stays unknown. A wrong phase is worse than none —
one block off doubles the error.

The preview always shows the pattern when the phase is known, even if matching
itself does not use it. The local version also has a mode that draws the grid at
several pixels per block with the real texture from the game folder; the web
version does not, because there is no game there.

### 5. Merging into scaled blocks

The blueprint format stores a `bounds` field per block — a block can be
stretched. The game's own blueprints contain blocks with `bounds` up to 892, so
placing one part per pixel is simply wasteful.

The grid is split into single-colour rectangles by a greedy algorithm: from
every free cell it stretches the widest possible run of one material, then
pushes it downwards while the rows below match. Column occupancy is kept in a
"busy until row N" array and row equality in prefix sums, so checking whether a
whole run matches costs two operations.

The payoff depends on the picture. A smooth 300×300 gradient is 90,000 cells
that collapse into 761 parts. A photo in palette mode gives about −35 %. Pure
noise gives nothing: there is nothing to merge.

The picture stays pixel for pixel the same — there is a test that rebuilds the
grid from the rectangles and compares it to the original.

---

## Splitting into modules

A large picture does not fit into one blueprint. The comfortable ceiling is
around 10,000 parts; past 50,000 the game lags noticeably.

The structure is cut into modules: standalone blueprints that you place one by
one and join with the Weld Tool. Cutting does not recompute the merge — it
slices the already-built rectangles along module borders. Because of that the
sum of the modules is exactly the same picture as the whole blueprint, the part
count of each module is known up front, and the seams are nearly free: on a
200×112 grid a 3×2 split added three parts to 21,133.

The module count is chosen automatically. Layouts are enumerated, the exact
part count per module is computed for each, and the best of the ones that fit
the cap is picked by `modules × (1 + 0.35 × skew)`. Skew is there for a reason:
without it the recommendation degenerates into strips — a 7×1 layout formally
saves one module but produces 43×195 block pieces that are awkward to place and
weld.

There is no hard limit; there can be thousands of modules. Besides the
automatic choice there is a "set the module size in blocks" mode, and the seams
can be dragged with the mouse right on the picture — for instance so that a cut
does not run across a face.

The module set ships with `MAP.png`, the picture with the grid and numbers
overlaid, and `ASSEMBLY.txt` with the build order. Modules are named
row-column with rows counted from the bottom: `1-1` is the bottom-left corner.
With ten or more modules per side the numbers are zero-padded, otherwise the
game sorts the blueprint list as 1, 10, 2.

---

## Game formats

Everything below was read from an installed copy of the game.

### The paint tool palette

It lives in `Data/Render/PaintPalette/primary.paintpalette` — a plain JSON file
with an array of 64 `RRGGBBAA` values. Forty of them are real colours; the
remaining 24 slots are `00000000` and are not shown in the interface. The grid
is 10 columns by 4 rows: the row sets the brightness, the column the hue (grey,
yellow, lime, green, cyan, blue, purple, magenta, red, orange).
`accent.paintpalette` is byte-for-byte identical.

Next to it sits `Data/Templates/paint_palette.template` with a **different** set
of forty colours — that one is a placeholder template, not the paint palette.

### The blueprint is not limited to the palette

The game's own blueprints use 229 distinct colours, and 195 of them are not in
the palette. The most common one, `606469`, is not a palette colour. In
`advanced_car.blueprint` the value is `DF7F01` while the palette orange is
`DF7F00`: a difference of one would not survive if the engine snapped colours
to the palette.

Forty colours is a paint tool interface limit, not an engine limit.

### blueprint.json

```json
{"bodies":[{"childs":[
  {"bounds":{"x":19,"y":1,"z":1},
   "color":"9B683A",
   "pos":{"x":-4,"y":14,"z":2},
   "shapeId":"df953d9c-234f-4ac2-af5e-f0490b223e71",
   "xaxis":1,"zaxis":3}
]}],"version":4}
```

The game writes `version: 4` and still reads the older `1`. The colour is six
characters in upper case with no hash. `xaxis: 1, zaxis: 3` means "no
rotation". The **Z axis points up**, so an upright picture lies in the X–Z
plane and a floor mosaic in X–Y.

Alongside it sits `description.json`:

```json
{"description":"…","localId":"<uuid>","name":"…","type":"Blueprint","version":0}
```

The folder name must equal `localId`. The icon is `icon.png`, 128×128 RGBA.

### Welding

From the game's own tutorial text, `Data/Gui/Language/English/InterfaceTags.txt`,
key `TUTORIAL_WELD_MESSAGE`:

> Weld your creations together! Grab the Weld Tool, press [action] on a loose
> creation, then press [action] on another one to stick them together. You can
> also join parts that are already touching while on the Lift.

That last sentence is what dictates the build order in the assembly note:
assemble on the lift, row by row from the bottom up.

---

## The web version

The site is static hosting on GitHub Pages. The core is ported to JavaScript;
`web-static/js/` mirrors `core/`. All of the code weighs 312 KB, the image is
processed in the browser and is never sent anywhere.

Some capabilities are unavailable in a browser for technical reasons:

| | site | program |
|---|---|---|
| Colour, dithering, materials, merging, modules, editor | yes | yes |
| Archive with blueprints | yes | yes |
| Writing straight into the game folder | no | yes |
| Palette and blocks from the installed game | no | yes |
| The game's interface fonts | no | yes |
| Compute size | up to 2M cells per pass | unlimited |

About the fonts: the game's interface font, Shentox, is commercial. The
metadata of `Shentox_SemiBold.otf` carries `Copyright (c) 2014 by Eduardo Manso`
and the identifier `com.myfonts.emtype.shentox.semi-bold.wfkit2` — the licence
was bought through MyFonts. Redistributing that file is not allowed, so the
site uses the free Play and Rubik, while the local version reads the real fonts
from your copy of the game.

### Reference vectors

Two implementations of one algorithm drift apart after the very first edit
unless something ties them together. `tests/vectors.json` pins the input and
the expected output of every layer of the core; it is checked by both
`tools/verify.py` and `web-static/tests` — in the browser and on Node in CI.
The site is not published if it computes anything different from the local
version.

The match is required bit for bit, including the text of `blueprint.json`. To
get there, three things had to be reproduced in JavaScript.

**Single precision.** Python computes OKLab in `float32` while JavaScript works
in `float64` by default. Without `Math.fround` the palette indices drift at the
boundaries.

**Resampling.** `canvas.drawImage` gives different results in different
browsers, so Pillow's algorithm is reproduced in full: the filter kernels,
`precompute_coeffs` and the 22-bit fixed-point arithmetic. All sixteen checked
variants match, Lanczos included.

**Pillow's rounding.** `convert("L")` rounds rather than truncates: `0x8000` is
added before the shift. And in `Image.blend` the `alpha` parameter is declared
as a `float`, so the whole expression is evaluated in single precision —
`1.3f * 90` comes out as 116.99998, which truncates to 116, not 117.

---

## Interface languages

The interface, the assembly notes and the file names inside the archive come in
Russian and English. Both versions read one dictionary, `data/i18n.json`, so a
string is edited in a single place and the two versions cannot drift apart.
The language is picked in the header and remembered in the browser.

---

## Repository layout

```
core/               the Python core
  palette.py        the paint tool palette
  quant.py          OKLab, colour matching, six dithering kernels, blue noise
  bluenoise.py      void-and-cluster mask generator
  materials.py      extending the palette with blocks
  imageproc.py      cropping, scaling, adjustments, preview, icons
  mesh.py           merging cells into scaled blocks
  tiles.py          splitting into modules, layout choice, assembly note
  blueprint.py      blueprint.json, description.json, archive
  paths.py          finding the installed game and the blueprints folder
  blocks.py         block catalogue
  textures.py       block textures from the game folder (local version only)
  i18n.py           interface strings

web/                local version: FastAPI and static files
web-static/         web version: the same core in JavaScript
data/               material table, blue noise mask, dictionary
tests/vectors.json  the reference shared by both implementations

tools/
  verify.py           self-check
  make_vectors.py     regenerate the reference
  build_materials.py  build the material table from the game files
  build_bluenoise.py  recompute the blue noise mask
  build_web.py        assemble the data for the web version
  quality.py          compare colour matching modes on real pictures
```

---

## Checks

```bash
py tools/verify.py              # the Python core, 108 checks
node web-static/tests/node.mjs  # the same core in JavaScript against the reference
```

These cover the palette, every dithering mode, merging over eight kinds of grid
(noise, chequerboard, holes and length limits among them), the blueprint
format, pixel placement in both orientations, module splitting with a rebuild
back into the original picture, the layout choice logic, speed and the block
catalogue.

The pattern is checked separately: the cell tables, the equality of their mean
with the overall overlay, and — most importantly — that the pattern phase
formula yields exactly the coordinates `build_json` writes. Were those to drift
apart, the preview would show one thing and the game would draw another.

To compare colour matching modes on your own picture:

```bash
py tools/quality.py "path\to\photo.png"
```

The tool prints both errors — distance and close-up — and writes a side-by-side
comparison sheet of every mode.

---

## Licence

The code is under [MIT](LICENSE).

Scrap Mechanic is a trademark of Axolot Games. This project is unofficial and
not affiliated with Axolot Games. The repository contains no game assets: the
palette, block textures and fonts are read at runtime from your own installed
copy.
