"""Настоящая текстура блоков для предпросмотра — только с установленной игрой.

Обычный предпросмотр рисует по одному пикселю на блок: этого хватает, чтобы
увидеть цвет и узор. Но вблизи в игре видна сама текстура — швы бетона,
доски дерева, полосы утеплителя. Здесь та же сетка рисуется с несколькими
пикселями на блок, и в каждый блок кладётся его кусок текстуры.

Текстуры игры в проект не копируются: они читаются из папки установки при
запросе и держатся в памяти в уменьшенном виде. Веб-версии этот режим
недоступен — там игры нет, и раздавать чужие текстуры мы не вправе.

Модель та же, что и везде (краска x (1 - alpha) + тон), плюс затенение из
asg и рельеф по карте нормалей — иначе кирпич от бетона не отличить.
"""

from __future__ import annotations

import json
import os

import numpy as np

from . import quant

# Сколько пикселей на блок. Больше 8 не нужно: на блок в текстуре
# приходится 128 текселей, но человек на экране столько не увидит.
SUB_DEFAULT = 4
SUB_MAX = 8

# Свет для рельефа: сверху-слева и немного на зрителя, как на иконках игры.
_LIGHT = np.array([-0.42, -0.55, 0.72], dtype=np.float32)
_LIGHT /= np.linalg.norm(_LIGHT)

_cache: dict[tuple[str, int], np.ndarray] = {}
_shapes: dict[str, dict] | None = None


def available(game_dir: str | None) -> bool:
    return bool(game_dir and os.path.isdir(game_dir))


def _resolve(path: str, game_dir: str) -> str | None:
    for token, sub in (("$GAME_DATA", "Data"), ("$SURVIVAL_DATA", "Survival"),
                       ("$CHALLENGE_DATA", "ChallengeData")):
        if path.startswith(token):
            real = os.path.join(game_dir, sub, path[len(token) + 1:].replace("/", os.sep))
            if os.path.isfile(real):
                return real
    return None


def _shapeset(game_dir: str) -> dict[str, dict]:
    """uuid -> запись блока из .shapeset. Читается один раз."""
    global _shapes
    if _shapes is not None:
        return _shapes
    _shapes = {}
    for rel in (os.path.join("Data", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
                os.path.join("Survival", "Objects", "Database", "ShapeSets", "blocks.shapeset")):
        full = os.path.join(game_dir, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for raw in data.get("blockList") or []:
            uuid = raw.get("uuid")
            if uuid and uuid not in _shapes:
                _shapes[uuid] = raw
    return _shapes


def _fold(arr: np.ndarray, n: int) -> np.ndarray:
    """Усреднить текстуру до сетки n x n."""
    if arr.ndim == 2:
        arr = arr[..., None]
    h, w, ch = arr.shape
    if h % n == 0 and w % n == 0:
        return arr.reshape(n, h // n, n, w // n, ch).mean(axis=(1, 3))
    ys = np.linspace(0, h, n + 1)
    xs = np.linspace(0, w, n + 1)
    out = np.empty((n, n, ch), dtype=np.float32)
    for j in range(n):
        y0 = min(int(ys[j]), h - 1)
        y1 = min(max(int(np.ceil(ys[j + 1])), y0 + 1), h)
        for i in range(n):
            x0 = min(int(xs[i]), w - 1)
            x1 = min(max(int(np.ceil(xs[i + 1])), x0 + 1), w)
            out[j, i] = arr[y0:y1, x0:x1].reshape(-1, ch).mean(0)
    return out


def tile(uuid: str, sub: int, game_dir: str) -> np.ndarray | None:
    """(n*sub, n*sub, 5): alpha, тон RGB, множитель освещения.

    n — tiling блока, то есть сколько блоков покрывает текстура. Строка 0 —
    низ постройки: ось V текстуры смотрит вниз, а игровая Z вверх.
    """
    key = (uuid, sub)
    if key in _cache:
        return _cache[key]

    raw = _shapeset(game_dir).get(uuid)
    if not raw:
        _cache[key] = None
        return None

    from PIL import Image

    def read(field: str, mode: str = "RGBA"):
        path = _resolve(str(raw.get(field) or ""), game_dir)
        if not path:
            return None
        try:
            return np.asarray(Image.open(path).convert(mode), dtype=np.float32) / 255.0
        except (OSError, ValueError):
            return None

    n = max(1, int(raw.get("tiling") or 8))
    size = n * sub

    dif = read("dif")
    if dif is None:
        _cache[key] = None
        return None
    alpha = _fold(dif[..., 3:4], size)
    tint = _fold(quant.srgb_to_linear((dif[..., :3] * 255).astype(np.uint8)) * dif[..., 3:4], size)

    asg = read("asg")
    occl = _fold(asg[..., 0:1], size) if asg is not None else np.zeros((size, size, 1), np.float32)

    light = np.ones((size, size, 1), dtype=np.float32)
    nor = read("nor", "RGB")
    if nor is not None:
        vec = nor * 2.0 - 1.0
        vec /= np.maximum(np.linalg.norm(vec, axis=-1, keepdims=True), 1e-6)
        ndl = np.clip((vec * _LIGHT).sum(-1, keepdims=True), 0.0, 1.0)
        light = _fold(0.62 + 0.38 * ndl, size)

    out = np.concatenate([alpha, tint, light * (1.0 - occl * 0.85)], axis=2)[::-1].copy()
    if len(_cache) > 48:                      # держать все текстуры незачем
        _cache.clear()
    _cache[key] = out
    return out


def render(grid, game_dir: str, sub: int = SUB_DEFAULT) -> np.ndarray | None:
    """Сетка с текстурой: (H*sub, W*sub, 4) RGBA uint8.

    Считается разом по всем клеткам одного блока: их немного, а numpy
    забирает выборку одним обращением.
    """
    sub = max(1, min(int(sub), SUB_MAX))
    pal = getattr(grid, "palette", None)
    if pal is None or not available(game_dir):
        return None

    h, w = grid.height, grid.width
    ox, oz = getattr(grid, "origin", (0, 0))

    paints = np.array([[int(p[0:2], 16), int(p[2:4], 16), int(p[4:6], 16)] for p in pal.paint],
                      dtype=np.uint8)
    paint_lin = quant.srgb_to_linear(paints)                 # (N, 3)

    keys = np.clip(grid.keys, 0, len(pal.paint) - 1)
    block_of = np.array(pal.block, dtype=object)
    here = block_of[keys]                                     # (H, W) uuid блока

    out = np.zeros((h, sub, w, sub, 3), dtype=np.float32)
    base = paint_lin[keys]                                    # (H, W, 3)

    for uuid in sorted({u for u in np.unique(here) if u}):
        data = tile(uuid, sub, game_dir)
        spot = here == uuid
        if data is None:
            out[:, :, :, :, :] += (spot[:, None, :, None, None] * base[:, None, :, None, :])
            continue
        size = data.shape[0]
        n = size // sub
        # какая ячейка текстуры ляжет на клетку — те же локальные координаты,
        # что и в подборе, только с точностью до подпикселя
        rows = ((np.arange(h - 1, -1, -1) + oz) % n)[:, None] * sub + np.arange(sub)[None, :]
        cols = ((np.arange(w) + ox) % n)[:, None] * sub + np.arange(sub)[None, :]
        patch = data[rows.reshape(-1)[:, None], cols.reshape(-1)[None, :]]   # (H*sub, W*sub, 5)
        patch = patch.reshape(h, sub, w, sub, 5)
        alpha = patch[..., 0:1]
        tint = patch[..., 1:4]
        light = patch[..., 4:5]
        shown = (base[:, None, :, None, :] * (1.0 - alpha) + tint) * light
        out += spot[:, None, :, None, None] * shown

    rgb = quant.linear_to_srgb(out.reshape(h * sub, w * sub, 3))
    alpha_ch = np.where(grid.mask, 255, 0).astype(np.uint8)
    alpha_ch = np.repeat(np.repeat(alpha_ch, sub, axis=0), sub, axis=1)[..., None]
    return np.concatenate([rgb, alpha_ch], axis=2)


def render_png(grid, game_dir: str, sub: int = SUB_DEFAULT) -> bytes | None:
    import io

    from PIL import Image

    arr = render(grid, game_dir, sub)
    if arr is None:
        return None
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, "PNG", optimize=False, compress_level=6)
    return buf.getvalue()
