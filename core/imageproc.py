"""Подготовка изображения: кадрирование, масштаб, альфа, коррекция, материалы."""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from . import materials, palette as pal, quant

# Pillow читает всё это без дополнительных зависимостей.
SUPPORTED = ["png", "jpg", "jpeg", "webp", "bmp", "gif", "tif", "tiff", "tga", "ico", "ppm", "jfif"]

Image.MAX_IMAGE_PIXELS = 300_000_000


@dataclass
class Grid:
    """Готовая сетка блоков."""

    rgb: np.ndarray                    # (H, W, 3) uint8 — как будет выглядеть
    keys: np.ndarray                   # (H, W) int32 — ключ материала для склейки
    mask: np.ndarray                   # (H, W) bool — где блок вообще есть
    width: int
    height: int
    source_size: tuple[int, int]
    palette: quant.Palette | None = None   # None => ключ это упакованный RGB
    error: float = 0.0                 # ошибка «как видно с расстояния», OKLab
    error_close: float = 0.0           # ошибка по каждому блоку, OKLab
    fit: dict | None = None            # что подобрала автоподгонка
    origin: tuple[int, int] = (0, 0)   # сдвиг в локальных координатах чертежа


def load(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # учесть поворот из EXIF у фото с телефона
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def target_size(src: tuple[int, int], width: int, height: int | None, keep_ratio: bool) -> tuple[int, int]:
    sw, sh = src
    width = max(1, int(width))
    if keep_ratio or not height:
        return width, max(1, round(width * sh / sw))
    return width, max(1, int(height))


_RESAMPLE = {
    "nearest": Image.Resampling.NEAREST,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "lanczos": Image.Resampling.LANCZOS,
}


def pick_resample(name: str, src: tuple[int, int], dst: tuple[int, int]) -> Image.Resampling:
    """auto: пиксель-арт увеличиваем «ближайшим», фото уменьшаем плавно."""
    if name in _RESAMPLE:
        return _RESAMPLE[name]
    if dst[0] >= src[0]:
        return Image.Resampling.NEAREST
    return Image.Resampling.BOX if src[0] / dst[0] < 2.5 else Image.Resampling.LANCZOS


def crop_box(src: tuple[int, int], crop) -> tuple[int, int, int, int] | None:
    """Привести рамку обрезки к целым пикселям внутри картинки."""
    if not crop:
        return None
    try:
        x, y, w, h = (float(v) for v in crop)
    except (TypeError, ValueError):
        return None
    sw, sh = src
    x0 = max(0, min(sw - 1, int(round(x))))
    y0 = max(0, min(sh - 1, int(round(y))))
    x1 = max(x0 + 1, min(sw, int(round(x + w))))
    y1 = max(y0 + 1, min(sh, int(round(y + h))))
    if (x0, y0, x1, y1) == (0, 0, sw, sh):
        return None
    return x0, y0, x1, y1


def build_grid(
    data: bytes,
    *,
    crop=None,
    width: int = 128,
    height: int | None = None,
    keep_ratio: bool = True,
    resample: str = "auto",
    color_mode: str = "exact",          # exact | palette
    method: str = "none",               # см. quant.METHODS
    strength: float = 1.0,
    lum_weight: float = 1.0,
    serpentine: bool = True,
    base_block: str = "",
    extra_blocks: list[str] | None = None,
    dedupe: float = 0.012,
    autofit: bool = False,
    alpha_mode: str = "cutout",         # cutout | flatten
    alpha_threshold: int = 128,
    background: str = "FFFFFF",
    brightness: float = 1.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    gamma: float = 1.0,
    flip_h: bool = False,
    edits=None,
    pattern: bool = True,               # учитывать узор ПРИ ПОДБОРЕ
    pattern_known: bool = True,         # фаза узора предсказуема (не дробим)
    orientation: str = "vertical",      # от него зависит фаза узора
    center: bool = True,
) -> Grid:
    img = load(data)
    src = img.size

    box = crop_box(src, crop)
    if box:
        img = img.crop(box)

    dst = target_size(img.size, width, height, keep_ratio)
    img = img.resize(dst, pick_resample(resample, img.size, dst))
    if flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

    rgba = np.array(img, dtype=np.uint8)
    alpha = rgba[..., 3]
    rgb = rgba[..., :3]

    if alpha_mode == "flatten":
        bg = np.array([int(background[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)
        a = (alpha.astype(np.float32) / 255.0)[..., None]
        rgb = np.clip(rgb.astype(np.float32) * a + bg * (1 - a), 0, 255).astype(np.uint8)
        mask = np.ones(rgb.shape[:2], dtype=bool)
    else:
        mask = alpha >= int(alpha_threshold)
        if not mask.all():
            rgb = rgb.copy()
            rgb[~mask] = 0        # прозрачное не должно «протекать» в дизеринг

    rgb = _adjust(rgb, brightness, contrast, saturation, gamma)

    fit_info = None
    if color_mode == "palette":
        from . import blueprint

        origin = blueprint.origin_of(dst[0], dst[1], orientation, center)
        # Ячейки нужны всегда, когда фаза известна: даже если подбор их не
        # использует, предпросмотр обязан показывать то, что покажет игра.
        material_set = materials.build_palette(
            pal.PALETTE_HEX, base_block or "", list(extra_blocks or []), dedupe=dedupe,
            with_cells=pattern_known,
        )
        if autofit:
            fit_info = quant.fit_to_palette(rgb[mask].reshape(-1, 1, 3) if mask.any() else rgb,
                                            material_set, lum_weight)
            rgb = _adjust(rgb, 1.0, fit_info["contrast"], fit_info["saturation"], fit_info["gamma"])

        keys = quant.quantize(
            rgb, material_set, method,
            strength=strength, lum_weight=lum_weight, serpentine=serpentine, mask=mask,
            origin=origin, use_pattern=pattern,
        ).astype(np.int32)
        # предпросмотр показывает узор: цвет берётся для той позиции, в
        # которой блок окажется в постройке
        shown = material_set.shown(keys, origin)
        # «издали» — честная метрика для дизеринга, «в упор» — для заливок
        error = quant.perceived_error(rgb, shown, mask)
        grid = Grid(rgb=shown, keys=keys, mask=mask, width=dst[0], height=dst[1],
                    source_size=src, palette=material_set, error=error, fit=fit_info,
                    origin=origin)
        grid.error_close = quant.mean_error(rgb, material_set, keys, mask, origin)
    else:
        keys = _pack(rgb)
        grid = Grid(rgb=rgb, keys=keys, mask=mask, width=dst[0], height=dst[1],
                    source_size=src, palette=None, error=0.0)

    if edits:
        apply_edits(grid, edits)
    return grid


def _pack(rgb: np.ndarray) -> np.ndarray:
    a = rgb.astype(np.int32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def apply_edits(grid: Grid, edits) -> int:
    """Ручные правки кистью: [[x, y, "RRGGBB"], [x, y, null], ...]."""
    applied = 0
    lookup: dict[str, int] = {}

    for item in edits:
        try:
            x, y, value = int(item[0]), int(item[1]), item[2]
        except (TypeError, ValueError, IndexError):
            continue
        if not (0 <= x < grid.width and 0 <= y < grid.height):
            continue

        if value is None:                       # стёрли — здесь блока нет
            grid.mask[y, x] = False
            applied += 1
            continue

        hexcolor = str(value).lstrip("#")[:6].upper()
        if len(hexcolor) != 6:
            continue
        key = lookup.get(hexcolor)
        if key is None:
            key = _key_for(grid, hexcolor)
            lookup[hexcolor] = key
        grid.keys[y, x] = key
        grid.mask[y, x] = True
        if grid.palette is None:
            grid.rgb[y, x] = _unpack(key)
        elif grid.palette.patterned:
            # цвет зависит от места: та же краска рядом ляжет иначе
            ox, oz = grid.origin
            cz = (grid.height - 1 - y + oz) % grid.palette.period
            cx = (x + ox) % grid.palette.period
            grid.rgb[y, x] = grid.palette.cells[cz, cx, key]
        else:
            grid.rgb[y, x] = grid.palette.rgb[key]
        applied += 1

    return applied


def _key_for(grid: Grid, hexcolor: str) -> int:
    rgb = np.array([[int(hexcolor[i:i + 2], 16) for i in (0, 2, 4)]], dtype=np.uint8)
    if grid.palette is None:
        return int(_pack(rgb)[0])
    lab = quant.to_oklab(rgb.reshape(1, 1, 3))
    return int(grid.palette.nearest(lab)[0, 0])


def _unpack(key: int) -> np.ndarray:
    return np.array([(key >> 16) & 255, (key >> 8) & 255, key & 255], dtype=np.uint8)


def _adjust(rgb: np.ndarray, brightness: float, contrast: float, saturation: float, gamma: float) -> np.ndarray:
    if gamma != 1.0:
        lut = np.clip(((np.arange(256) / 255.0) ** (1.0 / max(gamma, 1e-3))) * 255.0, 0, 255).astype(np.uint8)
        rgb = lut[rgb]
    if brightness == contrast == saturation == 1.0:
        return rgb
    img = Image.fromarray(rgb, "RGB")
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    return np.array(img, dtype=np.uint8)


# ── картинки ─────────────────────────────────────────────────────────────────


def _rgba(grid: Grid) -> Image.Image:
    h, w = grid.rgb.shape[:2]
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[..., :3] = grid.rgb
    out[..., 3] = np.where(grid.mask, 255, 0)
    return Image.fromarray(out, "RGBA")


def raw_png(grid: Grid) -> bytes:
    """Сетка один-в-один, без масштаба — её рисует и правит холст в браузере."""
    buf = io.BytesIO()
    _rgba(grid).save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()


def _font(size: int):
    from PIL import ImageFont

    for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_tiles(img, tiles: list[dict], scale: float, label: bool = True):
    """Нарисовать границы модулей и их номера поверх картинки."""
    from PIL import ImageDraw

    draw = ImageDraw.Draw(img, "RGBA")
    size = max(11, min(28, int(min(t["width"] for t in tiles) * scale / 4))) if tiles else 12
    font = _font(size)
    for t in tiles:
        x0, y0 = t["x0"] * scale, t["y0"] * scale
        x1, y1 = (t["x0"] + t["width"]) * scale - 1, (t["y0"] + t["height"]) * scale - 1
        draw.rectangle([x0, y0, x1, y1], outline=(255, 255, 255, 235), width=2)
        draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=(0, 0, 0, 160), width=1)
        if not label:
            continue
        text = t["label"]
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pad = 4
        draw.rectangle([cx - tw / 2 - pad, cy - th / 2 - pad, cx + tw / 2 + pad, cy + th / 2 + pad],
                       fill=(0, 0, 0, 170))
        draw.text((cx - tw / 2 - box[0], cy - th / 2 - box[1]), text, font=font, fill=(255, 255, 255, 255))
    return img


def sub_grid(grid: Grid, x0: int, y0: int, width: int, height: int) -> Grid:
    """Кусок сетки — для иконки отдельного модуля."""
    return Grid(
        rgb=grid.rgb[y0:y0 + height, x0:x0 + width],
        keys=grid.keys[y0:y0 + height, x0:x0 + width],
        mask=grid.mask[y0:y0 + height, x0:x0 + width],
        width=width,
        height=height,
        source_size=grid.source_size,
        palette=grid.palette,
    )


def assembly_map_png(grid: Grid, tiles: list[dict], title: str, note: str, max_side: int = 1000) -> bytes:
    """Схема сборки: картинка с границами модулей, их номерами и числом деталей."""
    from PIL import ImageDraw

    h, w = grid.rgb.shape[:2]
    pic = _rgba(grid)
    scale = max(1, min(max_side // max(w, h), 16)) if max(w, h) <= max_side else max_side / max(w, h)
    pic = pic.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.NEAREST)
    draw_tiles(pic, tiles, scale)

    head, foot = 46, 30
    canvas = Image.new("RGB", (pic.width + 24, pic.height + head + foot), (18, 21, 26))
    canvas.paste(Image.alpha_composite(Image.new("RGBA", pic.size, (18, 21, 26, 255)), pic).convert("RGB"),
                 (12, head))
    d = ImageDraw.Draw(canvas)
    d.text((12, 12), title, font=_font(20), fill=(240, 244, 250))
    d.text((12, pic.height + head + 6), note, font=_font(14), fill=(150, 162, 179))

    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def icon_png(grid: Grid) -> bytes:
    """Иконка чертежа: игра использует 128x128 RGBA."""
    img = _rgba(grid)
    img.thumbnail((128, 128), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    canvas.paste(img, ((128 - img.width) // 2, (128 - img.height) // 2))
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()
