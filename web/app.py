"""Веб-интерфейс конвертера: FastAPI + статика."""

from __future__ import annotations

import base64
import os
import time
import urllib.parse
import uuid as uuidlib

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from core import blocks, blueprint, imageproc, materials, mesh, palette, paths, quant, tiles

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

MAX_UPLOAD = 64 * 1024 * 1024          # 64 МБ хватит на любое фото
# Считать сетку и склейку можно и на десятках миллионов клеток — замеры на
# картинке 3764x6688 показали, что склейка идёт линейно (2.6 млн клеток за
# 2.5 с). Упирается всё не в клетки, а в ЧИСЛО ДЕТАЛЕЙ: на ширине 1200 их
# уже 1.96 млн, а это ~275 МБ JSON, которые игра не откроет ни при каком
# дроблении. Поэтому здесь мягкий потолок на время расчёта, а не запрет:
# если запросили больше — считаем в уменьшенном виде и честно сообщаем.
MAX_CELLS = 2_000_000                  # бюджет на интерактивный предпросмотр (~4 с)
PARTS_SOFT = 50_000                    # выше — игра начинает страдать
PARTS_HARD = 2_000_000                 # выше — чертёж физически не собрать
SESSION_TTL = 6 * 3600
MAX_EDITS = 400_000

app = FastAPI(title="SM_Pixel", docs_url=None, redoc_url=None)

# Загруженные картинки живут в памяти процесса: слайдеры в интерфейсе
# дёргают предпросмотр десятки раз, перезагружать файл каждый раз глупо.
_images: dict[str, dict] = {}
_downloads: dict[str, dict] = {}
_previews: dict[str, dict] = {}

GAME_DIR = paths.find_game_dir()
BLUEPRINTS_DIR = paths.find_blueprints_dir()

_palette_from_game = palette.load_from_game(GAME_DIR)
_blocks_added = blocks.load_from_game(GAME_DIR)

# Шрифты самой игры: отдаём прямо из папки установки, ничего не копируя.
FONT_DIR = os.path.join(GAME_DIR, "Data", "Gui", "Fonts") if GAME_DIR else ""
FONTS = {
    # Shentox — шрифт интерфейса Scrap Mechanic. Кириллицы в нём нет, поэтому
    # игра берёт для русского NotoSans-Medium; здесь ровно та же схема.
    "ui": "Shentox_SemiBold.otf",
    "ui-medium": "Shentox_Medium.otf",
    "ui-regular": "Shentox_Regular.otf",
    "cyr": "NotoSans-Medium.ttf",
    "cyr-bold": "NotoSans-SemiBold.ttf",
    "digits": "ScrapMechanic-TechNumbers.otf",
    "digital": "SMDigital.otf",
}


def _gc() -> None:
    now = time.time()
    for store in (_images, _downloads, _previews):
        for key in [k for k, v in store.items() if now - v["ts"] > SESSION_TTL]:
            store.pop(key, None)


def _flag(raw: dict, key: str, default: bool = False) -> bool:
    val = raw.get(key, default)
    return val if isinstance(val, bool) else str(val).lower() in ("1", "true", "on", "yes")


def _num(raw: dict, key, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(raw.get(key, default))))
    except (TypeError, ValueError):
        return default


def _params(raw: dict) -> dict:
    """Разобрать и подчистить параметры из интерфейса."""
    bg = str(raw.get("background", "FFFFFF")).lstrip("#")[:6].upper()
    if len(bg) != 6 or any(c not in "0123456789ABCDEF" for c in bg):
        bg = "FFFFFF"

    method = str(raw.get("method", "none"))
    if method not in quant.METHODS:
        method = "none"

    extra = raw.get("extra_blocks") or []
    if not isinstance(extra, list):
        extra = []

    crop = raw.get("crop")
    if not (isinstance(crop, (list, tuple)) and len(crop) == 4):
        crop = None

    return {
        "crop": crop,
        "width": _num(raw, "width", 128, 1, 4096, int),
        "height": _num(raw, "height", 0, 0, 4096, int) or None,
        "keep_ratio": _flag(raw, "keep_ratio", True),
        "resample": str(raw.get("resample", "auto")),
        "color_mode": "palette" if raw.get("color_mode") == "palette" else "exact",
        "method": method,
        "strength": _num(raw, "strength", 1.0, 0.0, 1.5),
        "lum_weight": _num(raw, "lum_weight", 1.0, 0.5, 3.0),
        "serpentine": _flag(raw, "serpentine", True),
        "base_block": str(raw.get("block") or blocks.DEFAULT_BLOCK),
        "extra_blocks": [str(u) for u in extra][:64],
        "dedupe": _num(raw, "dedupe", 0.012, 0.0, 0.06),
        "autofit": _flag(raw, "autofit", False),
        "alpha_mode": "flatten" if raw.get("alpha_mode") == "flatten" else "cutout",
        "alpha_threshold": _num(raw, "alpha_threshold", 128, 0, 255, int),
        "background": bg,
        "brightness": _num(raw, "brightness", 1.0, 0.2, 3.0),
        "contrast": _num(raw, "contrast", 1.0, 0.2, 3.0),
        "saturation": _num(raw, "saturation", 1.0, 0.0, 3.0),
        "gamma": _num(raw, "gamma", 1.0, 0.3, 3.0),
        "flip_h": _flag(raw, "flip_h", False),
    }


def _resolver(grid, base_block: str):
    if grid.palette is not None:
        return blueprint.palette_resolver(grid.palette, base_block)
    return blueprint.rgb_resolver(base_block)


def _build(image_id: str, raw: dict):
    _gc()
    entry = _images.get(image_id)
    if not entry:
        raise HTTPException(404, "Картинка не найдена — загрузите её заново")

    params = _params(raw)
    src = entry["size"]
    box = imageproc.crop_box(src, params["crop"])
    inner = (box[2] - box[0], box[3] - box[1]) if box else src
    w, h = imageproc.target_size(inner, params["width"], params["height"], params["keep_ratio"])

    # Не отказываем, а считаем в уменьшенном виде: запрет посреди работы
    # бесполезен, а картинка с честной пометкой — полезна.
    clamped = None
    if w * h > MAX_CELLS:
        shrink = (MAX_CELLS / (w * h)) ** 0.5
        safe_w = max(8, int(w * shrink))
        clamped = {"requestedWidth": w, "requestedHeight": h, "requestedCells": w * h,
                   "usedWidth": safe_w, "maxWidth": safe_w}
        params = dict(params, width=safe_w, height=None, keep_ratio=True)

    edits = [stroke for batch in entry["edits"] for stroke in batch]
    grid = imageproc.build_grid(entry["data"], edits=edits, **params)
    grid.clamped = clamped

    merge = _flag(raw, "merge", True)
    max_bound = int(_num(raw, "max_bound", mesh.MAX_BOUND_DEFAULT, 1, 892, int))
    rects = mesh.merge_rects(grid.keys, grid.mask, max_bound) if merge else mesh.split_rects(grid.keys, grid.mask)
    return grid, rects, params


def _split_params(raw: dict, grid_w: int, grid_h: int, entry: dict) -> tuple[int, int, list | None, list | None]:
    """Сколько модулей и где именно проходят швы."""
    if not _flag(raw, "split"):
        return 1, 1, None, None

    mode = str(raw.get("split_mode", "count"))
    if mode == "size":
        size_x = int(_num(raw, "module_w", 64, 1, 4096, int))
        size_y = int(_num(raw, "module_h", size_x, 1, 4096, int))
        ex = tiles.edges_by_size(grid_w, size_x)
        ey = tiles.edges_by_size(grid_h, size_y)
        return len(ex) - 1, len(ey) - 1, ex, ey

    cols = int(_num(raw, "cols", 1, 1, grid_w, int))
    rows = int(_num(raw, "rows", 1, 1, grid_h, int))

    custom = entry.get("edges") or {}
    ex = tiles.clean_edges(grid_w, custom.get("x"), tiles.edges(grid_w, cols)) if custom.get("x") else None
    ey = tiles.clean_edges(grid_h, custom.get("y"), tiles.edges(grid_h, rows)) if custom.get("y") else None
    if ex:
        cols = len(ex) - 1
    if ey:
        rows = len(ey) - 1
    return cols, rows, ex, ey


def _rect_buffer(rects) -> bytes:
    """Прямоугольники для холста: сырые int32 x,y,w,h — без JSON и base64.

    На пятидесяти тысячах деталей JSON весил бы мегабайты, а так это
    ровно 16 байт на деталь, и браузер читает их как Int32Array.
    """
    import numpy as np

    # Холст рисует границы только начиная с трёхкратного увеличения, а на
    # миллионах деталей буфер весил бы под сотню мегабайт — не отдаём.
    if not rects or len(rects) > 400_000:
        return b""
    arr = np.asarray(rects, dtype=np.int32)[:, :4]
    return arr.tobytes()


def _store_preview(png: bytes, rects=None) -> str:
    token = uuidlib.uuid4().hex
    _previews[token] = {"data": png, "rects": _rect_buffer(rects), "ts": time.time()}
    if len(_previews) > 24:                     # старые превью держать незачем
        for key in sorted(_previews, key=lambda k: _previews[k]["ts"])[:-24]:
            _previews.pop(key, None)
    return token


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/config")
def config() -> JSONResponse:
    return JSONResponse(
        {
            "blocks": blocks.catalog(),
            "defaultBlock": blocks.DEFAULT_BLOCK,
            "palette": palette.swatches(),
            "paletteCols": palette.PALETTE_COLS,
            "paletteFromGame": _palette_from_game,
            "blocksFromGame": _blocks_added,
            "methods": [{"id": k, "title": v} for k, v in quant.METHODS.items()],
            "materials": materials.catalog(),
            "materialsReady": materials.available(),
            "fonts": {k: bool(FONT_DIR and os.path.isfile(os.path.join(FONT_DIR, v)))
                      for k, v in FONTS.items()},
            "gameDir": GAME_DIR,
            "blueprintsDir": BLUEPRINTS_DIR,
            "steamId": str(paths.steam_id(BLUEPRINTS_DIR)),
            "supported": imageproc.SUPPORTED,
            "maxCells": MAX_CELLS,
            "target": tiles.TARGET_PARTS,
        }
    )


@app.get("/api/font/{name}")
def font(name: str) -> Response:
    """Шрифты берём прямо из установленной игры, в проект ничего не копируем."""
    filename = FONTS.get(name)
    if not filename or not FONT_DIR:
        raise HTTPException(404, "Шрифт недоступен")
    path = os.path.join(FONT_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(404, "Шрифт не найден в папке игры")
    media = "font/otf" if filename.lower().endswith(".otf") else "font/ttf"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> JSONResponse:
    data = await file.read()
    if not data:
        raise HTTPException(400, "Пустой файл")
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "Файл больше 64 МБ")
    try:
        size = imageproc.load(data).size
    except Exception:
        raise HTTPException(400, "Не удалось прочитать картинку. Поддерживаются: " + ", ".join(imageproc.SUPPORTED))

    _gc()
    image_id = uuidlib.uuid4().hex
    name = os.path.splitext(os.path.basename(file.filename or "картинка"))[0][:60] or "картинка"
    _images[image_id] = {"data": data, "size": size, "name": name, "ts": time.time(),
                         "edits": [], "edges": {}}
    return JSONResponse({"id": image_id, "width": size[0], "height": size[1], "name": name})


@app.post("/api/edits")
async def edits(request: Request) -> JSONResponse:
    """Мазки кистью копятся на сервере — в запросе предпросмотра их гонять незачем."""
    body = await request.json()
    entry = _images.get(str(body.get("id", "")))
    if not entry:
        raise HTTPException(404, "Картинка не найдена")

    if _flag(body, "clear"):
        entry["edits"].clear()
    undo = int(_num(body, "undo", 0, 0, 999, int))
    for _ in range(undo):
        if entry["edits"]:
            entry["edits"].pop()

    add = body.get("add")
    if isinstance(add, list) and add:
        total = sum(len(b) for b in entry["edits"])
        if total + len(add) > MAX_EDITS:
            raise HTTPException(400, "Слишком много правок — очистите кисть")
        entry["edits"].append(add)

    entry["ts"] = time.time()
    return JSONResponse({"strokes": len(entry["edits"]),
                         "cells": sum(len(b) for b in entry["edits"])})


@app.post("/api/edges")
async def edges(request: Request) -> JSONResponse:
    """Свои границы модулей — их тянут мышью прямо по картинке."""
    body = await request.json()
    entry = _images.get(str(body.get("id", "")))
    if not entry:
        raise HTTPException(404, "Картинка не найдена")
    if _flag(body, "reset"):
        entry["edges"] = {}
    else:
        entry["edges"] = {
            "x": [int(v) for v in (body.get("x") or [])][:4096],
            "y": [int(v) for v in (body.get("y") or [])][:4096],
        }
    return JSONResponse({"edges": entry["edges"]})


@app.post("/api/preview")
async def preview(request: Request) -> JSONResponse:
    body = await request.json()
    started = time.time()
    grid, rects, params = _build(body.get("id", ""), body)
    entry = _images[body["id"]]

    cols, rows, ex, ey = _split_params(body, grid.width, grid.height, entry)
    tile_list = tiles.cut(rects, grid.width, grid.height, cols, rows, ex, ey) if cols * rows > 1 else []
    actual_rows = max((t.row for t in tile_list), default=0) + 1
    actual_cols = max((t.col for t in tile_list), default=0) + 1
    overlay = [t.as_dict(actual_rows, actual_cols) for t in tile_list]

    info = mesh.stats(rects)
    info.update(
        {
            "modules": sorted(overlay, key=lambda t: t["order"]),
            "moduleMax": max((t["parts"] for t in overlay), default=0),
            "moduleTotal": sum(t["parts"] for t in overlay),
            "gridWidth": grid.width,
            "gridHeight": grid.height,
            "sourceWidth": grid.source_size[0],
            "sourceHeight": grid.source_size[1],
            "meters": [round(grid.width * 0.25, 2), round(grid.height * 0.25, 2)],
            "ms": int((time.time() - started) * 1000),
            "colorMode": params["color_mode"],
            "paletteSize": len(grid.palette) if grid.palette is not None else 0,
            "error": round(grid.error, 4),
            "fit": grid.fit,
            "strokes": len(entry["edits"]),
            "clamped": getattr(grid, "clamped", None),
            "partsSoft": PARTS_SOFT,
            "partsHard": PARTS_HARD,
            "edgesX": ex,
            "edgesY": ey,
        }
    )

    resolve = _resolver(grid, params["base_block"])
    used = blueprint.used_blocks(rects, resolve)
    info["blocksUsed"] = [
        {"uuid": u, "title": blocks.get(u).title, "parts": n}
        for u, n in sorted(used.items(), key=lambda kv: -kv[1])
    ]

    target = int(_num(body, "target", tiles.TARGET_PARTS, 200, 200000, int))
    token = _store_preview(imageproc.raw_png(grid), rects if len(rects) <= 300_000 else None)
    result = {
        "image": "/api/preview-image/" + token,
        "rects": "/api/preview-rects/" + token,
        "stats": info,
        "plan": tiles.plan(rects, grid.width, grid.height, target),
    }
    if grid.palette is not None and _flag(body, "want_palette", True):
        result["palette"] = [
            {"hex": "%02X%02X%02X" % tuple(int(v) for v in grid.palette.rgb[i]),
             "paint": grid.palette.paint[i], "block": grid.palette.block[i]}
            for i in range(len(grid.palette))
        ]
    return JSONResponse(result)


@app.get("/api/preview-image/{token}")
def preview_image(token: str) -> Response:
    entry = _previews.get(token)
    if not entry:
        raise HTTPException(404, "Превью устарело")
    return Response(content=entry["data"], media_type="image/png",
                    headers={"Cache-Control": "private, max-age=600"})


@app.get("/api/preview-rects/{token}")
def preview_rects(token: str) -> Response:
    entry = _previews.get(token)
    if not entry:
        raise HTTPException(404, "Превью устарело")
    return Response(content=entry.get("rects") or b"", media_type="application/octet-stream",
                    headers={"Cache-Control": "private, max-age=600"})


@app.post("/api/estimate")
async def estimate(request: Request) -> JSONResponse:
    """Во что обойдётся запрошенный размер — без того, чтобы его считать.

    Полный расчёт на 25 миллионах клеток занял бы минуты и всё равно упёрся
    бы в число деталей. Поэтому считаем эскиз шириной 320 и переносим долю
    «деталей на клетку» на запрошенный размер: на замерах эта доля держится
    в пределах 0.72-0.82 и от размера почти не зависит.
    """
    body = await request.json()
    entry = _images.get(str(body.get("id", "")))
    if not entry:
        raise HTTPException(404, "Картинка не найдена")

    params = _params(body)
    box = imageproc.crop_box(entry["size"], params["crop"])
    inner = (box[2] - box[0], box[3] - box[1]) if box else entry["size"]
    want_w, want_h = imageproc.target_size(inner, params["width"], params["height"], params["keep_ratio"])

    probe_w = min(320, want_w)
    probe = imageproc.build_grid(entry["data"], **dict(params, width=probe_w, height=None, keep_ratio=True))
    probe_rects = mesh.merge_rects(probe.keys, probe.mask)
    probe_cells = max(1, probe.width * probe.height)
    ratio = len(probe_rects) / probe_cells

    cells = want_w * want_h
    parts = int(cells * ratio)
    target = int(_num(body, "target", tiles.TARGET_PARTS, 200, 200000, int))

    max_w = min(want_w, max(8, int((MAX_CELLS * want_w / max(1, want_h)) ** 0.5)))
    parts_at_max = int(max_w * (max_w * want_h / max(1, want_w)) * ratio)

    # какая ширина укладывается в разумный чертёж целиком
    def width_for(limit_parts: int) -> int:
        if ratio <= 0:
            return want_w
        return max(8, int((limit_parts / ratio * want_w / max(1, want_h)) ** 0.5))

    return JSONResponse(
        {
            "requestedWidth": want_w, "requestedHeight": want_h, "cells": cells,
            "parts": parts, "partsPerCell": round(ratio, 3),
            "modules": max(1, -(-parts // max(1, target))),
            "widthForOneBlueprint": min(want_w, width_for(PARTS_SOFT)),
            "widthForComfort": min(want_w, width_for(target)),
            "maxComputableWidth": max_w,
            "partsAtMax": parts_at_max,
            "modulesAtMax": max(1, -(-parts_at_max // max(1, target))),
            "bytesEstimate": parts * 140,
        }
    )


@app.post("/api/autofit")
async def autofit(request: Request) -> JSONResponse:
    """Подобрать гамму / насыщенность / контраст под палитру и вернуть числа."""
    body = await request.json()
    grid, _, params = _build(body.get("id", ""), dict(body, color_mode="exact", method="none"))
    pal = materials.build_palette(
        palette.PALETTE_HEX, params["base_block"], params["extra_blocks"], dedupe=params["dedupe"]
    )
    fit = quant.fit_to_palette(grid.rgb, pal, params["lum_weight"])
    return JSONResponse(fit)


@app.post("/api/export")
async def export(request: Request) -> JSONResponse:
    body = await request.json()
    grid, rects, params = _build(body.get("id", ""), body)
    entry = _images[body["id"]]

    base_block = params["base_block"]
    resolve = _resolver(grid, base_block)
    orientation = blueprint.HORIZONTAL if body.get("orientation") == "horizontal" else blueprint.VERTICAL
    depth = int(_num(body, "depth", 1, 1, 16, int))
    name = str(body.get("name") or entry["name"])[:60].strip() or "Картинка"

    cols, rows, ex, ey = _split_params(body, grid.width, grid.height, entry)
    tile_list = tiles.cut(rects, grid.width, grid.height, cols, rows, ex, ey) if cols * rows > 1 else []
    if tile_list:
        rows = max(t.row for t in tile_list) + 1
        cols = max(t.col for t in tile_list) + 1

    only = body.get("only_module")
    if tile_list and only:
        tile_list = [t for t in tile_list if t.label(rows, cols) == str(only)]
        if not tile_list:
            raise HTTPException(400, f"Модуль {only} не найден")

    items: list[dict] = []
    if tile_list:
        for tile in sorted(tile_list, key=lambda t: t.order):
            items.append(
                {
                    "name": f"{name} {tile.label(rows, cols)}",
                    "text": blueprint.build_json(tile.rects, tile.width, tile.height,
                                                 resolve, orientation, True, depth),
                    "icon": imageproc.icon_png(
                        imageproc.sub_grid(grid, tile.x0, tile.y0, tile.width, tile.height)),
                    "note": (f"Модуль {tile.label(rows, cols)} из {cols * rows}: "
                             f"{tile.width}x{tile.height} блоков, {tile.parts} деталей. "
                             f"Ряды снизу, столбцы слева. Сварить с соседями."),
                }
            )
    else:
        items.append(
            {
                "name": name,
                "text": blueprint.build_json(rects, grid.width, grid.height,
                                             resolve, orientation, True, depth),
                "icon": imageproc.icon_png(grid),
                "note": f"{grid.width}x{grid.height} блоков, {len(rects)} деталей. Сделано в SM_Pixel.",
            }
        )

    total_parts = sum(t.parts for t in tile_list) if tile_list else len(rects)
    result: dict = {"parts": total_parts, "bytes": sum(len(i["text"]) for i in items),
                    "modules": len(items)}

    extras: dict[str, bytes | str] = {}
    if tile_list and not only:
        ordered = sorted(tile_list, key=lambda t: t.order)
        overlay = [t.as_dict(rows, cols) for t in ordered]
        counts = [t.parts for t in sorted(tile_list, key=lambda t: (t.row, t.col))]
        guide = tiles.instructions(name, cols, rows, counts, orientation)
        note = (f"{grid.width}×{grid.height} блоков · {cols}×{rows} = {cols * rows} модулей · "
                f"{total_parts} деталей · самый тяжёлый модуль {max(counts)}")
        map_png = imageproc.assembly_map_png(grid, overlay, f"Схема сборки «{name}»", note)
        extras["СБОРКА.txt"] = guide
        extras["СХЕМА.png"] = map_png
        result["guide"] = guide
        result["moduleNames"] = [i["name"] for i in items]

        token = uuidlib.uuid4().hex
        _downloads[token] = {"data": map_png, "name": f"{name} — схема.png",
                             "type": "image/png", "ts": time.time()}
        result["map"] = f"/api/download/{token}"
        result["mapPng"] = "data:image/png;base64," + base64.b64encode(map_png).decode()

    if body.get("to_game"):
        if not BLUEPRINTS_DIR:
            raise HTTPException(400, "Папка чертежей Scrap Mechanic не найдена. Скачайте ZIP.")
        written = [
            blueprint.write_folder(BLUEPRINTS_DIR, item["name"], item["text"], item["icon"],
                                   item["note"], overwrite_same_name=_flag(body, "replace"))
            for item in items
        ]
        result["path"] = os.path.dirname(written[0])
        result["written"] = len(written)

    if body.get("to_zip"):
        token = uuidlib.uuid4().hex
        _downloads[token] = {"data": blueprint.zip_bundle(items, extras), "name": f"{name}.zip",
                             "type": "application/zip", "ts": time.time()}
        result["download"] = f"/api/download/{token}"

    return JSONResponse(result)


@app.get("/api/download/{token}")
def download(token: str) -> Response:
    entry = _downloads.get(token)
    if not entry:
        raise HTTPException(404, "Ссылка устарела — соберите чертёж заново")
    media = entry.get("type", "application/zip")
    ext = ".png" if media == "image/png" else ".zip"
    ascii_name = "".join(ch for ch in entry["name"] if ch.isascii() and ch not in '"\\').strip()
    if not ascii_name.lower().endswith(ext):
        ascii_name = (ascii_name or "sm_pixel").rsplit(".", 1)[0] + ext
    quoted = urllib.parse.quote(entry["name"])
    return Response(
        content=entry["data"],
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"},
    )


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
