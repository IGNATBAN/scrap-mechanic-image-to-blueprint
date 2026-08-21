"""Самопроверка SM_Pixel: py tools/verify.py

Главное, что проверяем — склейка блоков не искажает картинку:
прямоугольники обязаны покрывать ровно те же клетки и теми же цветами.
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # консоль Windows по умолчанию не в UTF-8
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import blocks, blueprint, imageproc, materials, mesh, palette, quant, tiles  # noqa: E402

FAILS: list[str] = []
CHECKS = 0


def check(name: str, ok: bool, extra="") -> None:
    global CHECKS
    CHECKS += 1
    print(("  OK   " if ok else "  FAIL ") + name + ((" — " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(name)


def rebuild(rects, w, h):
    """Собрать сетку обратно из прямоугольников."""
    canvas = np.full((h, w), -1, dtype=np.int64)
    for x, y, rw, rh, color in rects:
        if (canvas[y:y + rh, x:x + rw] != -1).any():
            raise AssertionError(f"прямоугольники перекрываются в ({x},{y})")
        canvas[y:y + rh, x:x + rw] = color
    return canvas


def case_merge(name: str, rgb: np.ndarray, mask: np.ndarray, max_bound: int = 255) -> None:
    h, w = mask.shape
    rects = mesh.merge_rects(rgb, mask, max_bound)
    keys = mesh.color_keys(rgb)
    try:
        canvas = rebuild(rects, w, h)
    except AssertionError as e:
        check(f"склейка «{name}»", False, str(e))
        return

    covered = canvas != -1
    same_cells = bool((covered == mask).all())
    same_color = bool((canvas[mask] == keys[mask]).all()) if mask.any() else True
    within = all(r[2] <= max_bound and r[3] <= max_bound for r in rects)
    check(
        f"склейка «{name}»",
        same_cells and same_color and within,
        f"{len(rects)} деталей на {int(mask.sum())} клеток"
        + ("" if same_cells else "; покрытие не совпало")
        + ("" if same_color else "; цвета не совпали")
        + ("" if within else "; превышен max_bound"),
    )


def main() -> int:
    print("\n=== палитра ===")
    check("40 цветов, все hex", len(palette.PALETTE_HEX) >= 8 and all(len(c) == 6 for c in palette.PALETTE_HEX),
          f"{len(palette.PALETTE_HEX)} цветов")
    pal = palette.palette_rgb()
    same = palette.quantize(pal.reshape(1, -1, 3), "none")[0]
    check("цвет палитры квантуется сам в себя", bool((same == pal).all()))

    rng = np.random.default_rng(1)
    noise = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for mode in ("none", "floyd", "bayer"):
        q = palette.quantize(noise, mode)
        allowed = {tuple(c) for c in pal}
        used = {tuple(c) for c in q.reshape(-1, 3)}
        check(f"дизеринг «{mode}» не выходит за палитру", used <= allowed, f"{len(used)} цветов")

    print("\n=== склейка блоков ===")
    solid = np.zeros((40, 60, 3), np.uint8)
    solid[:] = (223, 127, 0)
    case_merge("сплошная заливка", solid, np.ones((40, 60), bool))
    r = mesh.merge_rects(solid, np.ones((40, 60), bool))
    check("сплошная заливка = 1 деталь", len(r) == 1, f"{len(r)}")

    stripes = np.zeros((32, 32, 3), np.uint8)
    stripes[:, ::2] = (255, 0, 0)
    stripes[:, 1::2] = (0, 0, 255)
    case_merge("вертикальные полосы", stripes, np.ones((32, 32), bool))
    check("полосы склеились по вертикали", len(mesh.merge_rects(stripes, np.ones((32, 32), bool))) == 32,
          f"{len(mesh.merge_rects(stripes, np.ones((32, 32), bool)))}")

    holes = rng.integers(0, 4, (48, 48), dtype=np.uint8)
    rgbh = (holes[..., None] * np.array([60, 40, 20], np.uint8)).astype(np.uint8)
    case_merge("блоки с дырами", rgbh, holes != 0)

    photo = rng.integers(0, 256, (70, 90, 3), dtype=np.uint8)
    case_merge("шум (худший случай)", photo, np.ones((70, 90), bool))

    checker = np.indices((30, 30)).sum(0) % 2
    checker_rgb = np.repeat((checker[..., None] * 255).astype(np.uint8), 3, axis=2)
    case_merge("шахматка", checker_rgb, np.ones((30, 30), bool))

    case_merge("ограничение длины 4", solid, np.ones((40, 60), bool), max_bound=4)
    case_merge("одна строка", solid[:1], np.ones((1, 60), bool))
    case_merge("один столбец", solid[:, :1], np.ones((40, 1), bool))
    case_merge("пусто", solid, np.zeros((40, 60), bool))

    print("\n=== формат чертежа ===")
    rects = mesh.merge_rects(stripes, np.ones((32, 32), bool))
    text = blueprint.build_json(rects, 32, 32, blocks.DEFAULT_BLOCK, blueprint.VERTICAL)
    data = json.loads(text)
    check("blueprint.json разбирается", data.get("version") == 4 and len(data["bodies"]) == 1)
    child = data["bodies"][0]["childs"][0]
    check("поля детали как у игры",
          sorted(child) == ["bounds", "color", "pos", "shapeId", "xaxis", "zaxis"], str(sorted(child)))
    check("цвет — 6 знаков в верхнем регистре без решётки",
          len(child["color"]) == 6 and child["color"].upper() == child["color"] and "#" not in child["color"],
          child["color"])
    check("поворот по умолчанию 1/3", child["xaxis"] == 1 and child["zaxis"] == 3)

    zs = [c["pos"]["z"] for c in data["bodies"][0]["childs"]]
    check("вертикально: картина стоит от земли", min(zs) == 0 and max(zs) < 32, f"z {min(zs)}..{max(zs)}")
    hor = json.loads(blueprint.build_json(rects, 32, 32, blocks.DEFAULT_BLOCK, blueprint.HORIZONTAL))
    check("горизонтально: всё лежит на z=0", all(c["pos"]["z"] == 0 for c in hor["bodies"][0]["childs"]))

    thick = json.loads(blueprint.build_json(rects, 32, 32, blocks.DEFAULT_BLOCK, blueprint.VERTICAL, depth=3))
    check("толщина уходит в глубину (Y)", thick["bodies"][0]["childs"][0]["bounds"]["y"] == 3)

    desc = json.loads(blueprint.description_json("Тест", "11111111-2222-3333-4444-555555555555"))
    check("description.json как у игры",
          sorted(desc) == ["description", "localId", "name", "type", "version"] and desc["type"] == "Blueprint")

    print("\n=== позиция пикселя ===")
    # красная точка в левом верхнем углу должна оказаться слева и сверху
    mark = np.zeros((8, 8, 3), np.uint8)
    mark[0, 0] = (255, 0, 0)
    m = np.zeros((8, 8), bool)
    m[0, 0] = True
    one = json.loads(blueprint.build_json(mesh.merge_rects(mark, m), 8, 8, blocks.DEFAULT_BLOCK, blueprint.VERTICAL, center=False))
    pos = one["bodies"][0]["childs"][0]["pos"]
    check("верх-лево картинки = x=0, z=верх", pos["x"] == 0 and pos["z"] == 7, str(pos))
    onh = json.loads(blueprint.build_json(mesh.merge_rects(mark, m), 8, 8, blocks.DEFAULT_BLOCK, blueprint.HORIZONTAL, center=False))
    posh = onh["bodies"][0]["childs"][0]["pos"]
    check("на земле: верх картинки = дальний край (+Y)", posh["x"] == 0 and posh["y"] == 7, str(posh))

    print("\n=== сквозной прогон ===")
    from PIL import Image
    import io

    src = Image.fromarray(rng.integers(0, 256, (256, 256, 3), dtype=np.uint8), "RGB")
    buf = io.BytesIO()
    src.save(buf, "PNG")
    raw = buf.getvalue()

    t0 = time.time()
    grid = imageproc.build_grid(raw, width=128, color_mode="palette", method="fs")
    rects = mesh.merge_rects(grid.keys, grid.mask)
    text = blueprint.build_json(rects, grid.width, grid.height, blocks.DEFAULT_BLOCK)
    dt = time.time() - t0
    check("шум 128 блоков: считается быстро", dt < 10, f"{dt:.2f} c, {len(rects)} деталей")
    check("итог — валидный JSON", len(json.loads(text)["bodies"][0]["childs"]) == len(rects))
    check("иконка 128x128 PNG", Image.open(io.BytesIO(imageproc.icon_png(grid))).size == (128, 128))
    check("превью рисуется", imageproc.raw_png(grid)[:4] == b"\x89PNG")

    # реалистичный случай: плавный градиент, здесь склейка обязана дать выигрыш
    xs = np.linspace(0, 255, 300)
    grad = np.stack(np.meshgrid(xs, xs), -1)
    grad = np.concatenate([grad, np.full((300, 300, 1), 128.0)], -1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(grad, "RGB").save(buf, "PNG")
    t0 = time.time()
    g2 = imageproc.build_grid(buf.getvalue(), width=300, color_mode="palette", method="none")
    r2 = mesh.merge_rects(g2.keys, g2.mask)
    dt2 = time.time() - t0
    saved = 1 - len(r2) / (g2.width * g2.height)
    check("градиент 300x300: склейка экономит больше половины", saved > 0.5, f"{saved:.0%} за {dt2:.2f} c")
    case_merge("градиент (пиксель-в-пиксель)", g2.rgb, g2.mask)

    print("\n=== разбивка на модули ===")
    photo2 = rng.integers(0, 5, (61, 97), dtype=np.uint8)
    rgb2 = (photo2[..., None] * np.array([50, 30, 17], np.uint8)).astype(np.uint8)
    mask2 = np.ones((61, 97), bool)
    base = mesh.merge_rects(rgb2, mask2)
    keys2 = mesh.color_keys(rgb2)

    for cols, rows in ((1, 1), (2, 1), (1, 3), (3, 2), (5, 4), (97, 61), (12, 12)):
        tl = tiles.cut(base, 97, 61, cols, rows)
        canvas = np.full((61, 97), -1, dtype=np.int64)
        overlap = False
        for t in tl:
            for x, y, w, h, color in t.rects:
                gx, gy = x + t.x0, y + t.y0
                if (canvas[gy:gy + h, gx:gx + w] != -1).any():
                    overlap = True
                canvas[gy:gy + h, gx:gx + w] = color
                if not (0 <= x and x + w <= t.width and 0 <= y and y + h <= t.height):
                    overlap = True  # деталь вылезла за свой модуль
        ok = not overlap and bool((canvas == keys2).all())
        check(f"модули {cols}×{rows} собираются в исходную картинку", ok,
              f"{len(tl)} модулей, {sum(t.parts for t in tl)} деталей")

    counts, total = tiles.count(base, 97, 61, 3, 2)
    cut3x2 = tiles.cut(base, 97, 61, 3, 2)
    by_pos = {(t.row, t.col): t.parts for t in cut3x2}
    check("быстрый счёт совпадает с реальной нарезкой",
          total == sum(t.parts for t in cut3x2)
          and all(counts[r][c] == by_pos[(r, c)] for r in range(2) for c in range(3)),
          f"{total}")

    tl = tiles.cut(base, 97, 61, 3, 2)
    bottom_left = min(tl, key=lambda t: t.order)
    check("модуль 1-1 — левый нижний угол картинки",
          bottom_left.label(2, 3) == "1-1" and bottom_left.col == 0 and bottom_left.row == 1,
          f"row={bottom_left.row} col={bottom_left.col}")
    check("порядок сборки снизу вверх",
          [t.label(2, 3) for t in sorted(tl, key=lambda t: t.order)]
          == ["1-1", "1-2", "1-3", "2-1", "2-2", "2-3"])
    wide = tiles.cut(base, 97, 61, 11, 10)
    check("при 10+ модулях номера с ведущим нулём", wide[0].label(10, 11) == "10-01", wide[0].label(10, 11))

    p = tiles.plan(base, 97, 61, target=400)
    rec = p["recommended"]
    opt = next(o for o in p["options"] if o["cols"] == rec["cols"] and o["rows"] == rec["rows"])
    check("рекомендация укладывается в потолок", opt["maxParts"] <= 400, f"{rec['cols']}×{rec['rows']} → {opt['maxParts']}")

    def score(o):
        return o["modules"] * (1 + 0.35 * (o["skew"] - 1))

    fits = [o for o in p["options"] if o["maxParts"] <= 400]
    check("рекомендация — лучший компромисс «мало модулей / удобная форма»",
          abs(score(opt) - min(score(o) for o in fits)) < 1e-9,
          f"{score(opt):.2f} против {min(score(o) for o in fits):.2f}")
    check("рекомендованный модуль не вытянутая полоса", opt["skew"] <= 2.5,
          f"{opt['tileWidth']}×{opt['tileHeight']}, вытянутость {opt['skew']}")

    # узкая полоса формально экономит модули, но сваривать её неудобно
    strip = tiles.plan(base, 97, 61, target=760)
    srec = strip["recommended"]
    sopt = next(o for o in strip["options"] if o["cols"] == srec["cols"] and o["rows"] == srec["rows"])
    cheaper = [o for o in strip["options"] if o["maxParts"] <= 760 and o["modules"] < sopt["modules"]]
    check("ради удобной формы допускается лишний модуль",
          not cheaper or all(c["skew"] > sopt["skew"] for c in cheaper),
          f"выбрано {srec['cols']}×{srec['rows']} (вытянутость {sopt['skew']}), "
          f"отвергнуто {[(c['cols'], c['rows'], c['skew']) for c in cheaper]}")
    check("вариант без разбивки присутствует", any(o["modules"] == 1 for o in p["options"]))
    check("разбивка добавляет деталей на швах",
          all(o["totalParts"] >= len(base) for o in p["options"]))

    huge = tiles.plan(base, 97, 61, target=10 ** 9)
    check("если и так влезает — рекомендуется один чертёж",
          huge["recommended"] == {"cols": 1, "rows": 1}, huge["recommended"])

    guide = tiles.instructions("Тест", 3, 2, [by_pos[(0, c)] for c in range(3)] + [by_pos[(1, c)] for c in range(3)], "vertical")
    check("памятка по сборке собирается",
          "сварочный аппарат" in guide.lower() and "ряд 1" in guide and "1-1" in guide,
          f"{len(guide)} символов")

    print("\n=== квантование цвета ===")
    pal40 = palette.base_palette()
    for method in quant.METHODS:
        idx_q = quant.quantize(noise, pal40, method)
        check(f"метод «{method}» не выходит за палитру",
              idx_q.min() >= 0 and idx_q.max() < len(pal40), f"индексы {idx_q.min()}..{idx_q.max()}")

    # градиент — тот случай, ради которого дизеринг и существует
    ramp = np.repeat(np.linspace(0, 255, 256, dtype=np.uint8)[None, :], 64, axis=0)
    ramp_rgb = np.repeat(ramp[..., None], 3, axis=2)
    err = {}
    for method in ("none", "fs", "stucki", "bluenoise"):
        idx_q = quant.quantize(ramp_rgb, pal40, method)
        err[method] = quant.perceived_error(ramp_rgb, pal40.rgb[idx_q])
    check("дизеринг лучше плоской заливки на градиенте",
          err["fs"] < err["none"] and err["stucki"] < err["none"],
          " ".join(f"{k}={v:.4f}" for k, v in err.items()))

    # вес яркости обязан беречь именно светлоту
    lab_src = quant.to_oklab(noise)
    dl = {}
    for weight in (0.6, 2.5):
        idx_q = quant.quantize(noise, pal40, "none", lum_weight=weight)
        dl[weight] = float(np.abs(lab_src[..., 0] - pal40._lab[idx_q][..., 0]).mean())
    check("вес яркости уменьшает сдвиг светлоты", dl[2.5] < dl[0.6],
          f"вес 0.6 → {dl[0.6]:.4f}, вес 2.5 → {dl[2.5]:.4f}")

    spectrum = np.fft.fftshift(np.abs(np.fft.fft2(quant.bluenoise_mask() - 0.5)))
    mid = spectrum.shape[0] // 2
    low = spectrum[mid - 6:mid + 7, mid - 6:mid + 7].sum() / spectrum.sum()
    check("маска синего шума действительно синяя", low < 0.01, f"низких частот {low:.4%}")

    strong = quant.quantize(ramp_rgb, pal40, "fs", strength=1.0)
    weak = quant.quantize(ramp_rgb, pal40, "fs", strength=0.0)
    flat = quant.quantize(ramp_rgb, pal40, "none")
    check("сила 0 отключает дизеринг", bool((weak == flat).all()))
    check("сила 1 меняет картинку", not bool((strong == flat).all()))

    fit = quant.fit_to_palette(ramp_rgb, pal40)
    check("автоподгонка не ухудшает", fit["error"] <= fit["baseError"] + 1e-6,
          f"{fit['baseError']:.4f} → {fit['error']:.4f}")

    print("\n=== разные блоки как доп. цвета ===")
    if materials.available():
        overlays = materials.usable_blocks()
        check("таблица наложений собрана", len(overlays) > 10, f"{len(overlays)} блоков")
        clear = min(overlays, key=lambda o: o.alpha + sum(o.tint) * 3)
        shown = materials.apply(pal40.rgb, clear)
        check("блок без наложения почти не меняет краску",
              int(np.abs(shown.astype(int) - pal40.rgb.astype(int)).max()) <= 24,
              f"{clear.name}, alpha={clear.alpha:.3f}")

        wide = materials.build_palette(palette.PALETTE_HEX, blocks.DEFAULT_BLOCK,
                                       [o.uuid for o in overlays])
        check("расширенная палитра больше исходной", len(wide) > len(pal40),
              f"{len(pal40)} → {len(wide)}")
        check("каждый материал знает свой блок",
              all(b for b in wide.block) and len(wide.block) == len(wide))

        rnd = rng.integers(0, 256, (2000, 1, 3), dtype=np.uint8)
        lab_rnd = quant.to_oklab(rnd)
        e40 = float(np.sqrt(((lab_rnd - pal40._lab[pal40.nearest(lab_rnd)]) ** 2).sum(-1)).mean())
        ewide = float(np.sqrt(((lab_rnd - wide._lab[wide.nearest(lab_rnd)]) ** 2).sum(-1)).mean())
        check("разные блоки заметно точнее по цвету", ewide < e40 * 0.75,
              f"{e40:.4f} → {ewide:.4f}")

        tight = materials.build_palette(palette.PALETTE_HEX, blocks.DEFAULT_BLOCK,
                                        [o.uuid for o in overlays], dedupe=0.05)
        check("слияние близких цветов уменьшает набор", len(tight) < len(wide),
              f"{len(wide)} → {len(tight)}")

        gw = imageproc.build_grid(raw, width=64, color_mode="palette", method="none",
                                  base_block=blocks.DEFAULT_BLOCK,
                                  extra_blocks=[o.uuid for o in overlays])
        rr = mesh.merge_rects(gw.keys, gw.mask)
        resolve = blueprint.palette_resolver(gw.palette, blocks.DEFAULT_BLOCK)
        doc = json.loads(blueprint.build_json(rr, gw.width, gw.height, resolve))
        shapes = {c["shapeId"] for c in doc["bodies"][0]["childs"]}
        check("в чертеже действительно разные блоки", len(shapes) > 1, f"{len(shapes)} видов")
        known = {o.uuid for o in overlays} | {blocks.DEFAULT_BLOCK}
        check("все блоки чертежа взяты из таблицы игры", shapes <= known,
              sorted(shapes - known)[:2])
    else:
        check("таблица наложений собрана", False, "нет data/materials.json")

    print("\n=== кадрирование, кисть, свои швы ===")
    g_full = imageproc.build_grid(raw, width=64)
    g_crop = imageproc.build_grid(raw, width=64, crop=[0, 0, 128, 64])
    check("обрезка меняет пропорции сетки",
          g_crop.height != g_full.height and g_crop.width == 64,
          f"{g_full.width}x{g_full.height} → {g_crop.width}x{g_crop.height}")
    check("обрезка «весь кадр» игнорируется",
          imageproc.crop_box((256, 256), [0, 0, 256, 256]) is None)
    check("обрезка не вылезает за картинку",
          imageproc.crop_box((256, 256), [-50, -50, 1000, 1000]) is None)

    g_edit = imageproc.build_grid(raw, width=32, color_mode="palette", method="none",
                                  edits=[[3, 4, "D02525"], [5, 6, None]])
    check("кисть красит нужную клетку",
          tuple(int(v) for v in g_edit.rgb[4, 3]) == (0xD0, 0x25, 0x25),
          tuple(int(v) for v in g_edit.rgb[4, 3]))
    check("ластик убирает блок", not bool(g_edit.mask[6, 5]))
    check("кисть не трогает соседей", bool(g_edit.mask[6, 6]) and bool(g_edit.mask[4, 4]))

    base_rects = base
    ex = tiles.clean_edges(97, [30, 70], [])
    ey = tiles.clean_edges(61, [25], [])
    custom = tiles.cut(base_rects, 97, 61, 0, 0, ex, ey)
    check("свои швы дают нужное число модулей", len(custom) == 6, len(custom))
    widths = sorted({t.width for t in custom})
    check("модули по своим швам неравные", widths == [27, 30, 40], widths)
    canvas = np.full((61, 97), -1, dtype=np.int64)
    for t in custom:
        for x, y, w_, h_, color in t.rects:
            canvas[y + t.y0:y + t.y0 + h_, x + t.x0:x + t.x0 + w_] = color
    check("неравные модули тоже собираются в картинку", bool((canvas == keys2).all()))

    check("мусорные швы отбрасываются",
          tiles.clean_edges(100, [0, 100, -5, 500, 50], []) == [0, 50, 100],
          tiles.clean_edges(100, [0, 100, -5, 500, 50], []))
    by_size = tiles.edges_by_size(100, 30)
    check("режим «модуль 30 блоков»", by_size == [0, 30, 60, 90, 100], by_size)

    print("\n=== дробление без предела ===")
    big = tiles.cut(base_rects, 97, 61, 40, 30)
    check("40×30 = 1200 модулей режется", len(big) == 1200, len(big))
    check("тысяча модулей всё ещё покрывает картинку",
          sum(t.parts for t in big) >= len(base_rects))
    huge_plan = tiles.plan(base_rects, 97, 61, target=20)
    rec_h = huge_plan["recommended"]
    check("план предлагает крупное дробление", rec_h["cols"] * rec_h["rows"] > 30, rec_h)
    check("список вариантов не разрастается",
          len(huge_plan["options"]) <= tiles.LIST_LIMIT, len(huge_plan["options"]))

    print("\n=== каталог блоков ===")
    cat = blocks.catalog()
    check("блоки загружены", len(cat) >= 20, f"{len(cat)}")
    check("uuid уникальны", len({b['uuid'] for b in cat}) == len(cat))
    check("блок по умолчанию есть", blocks.get(blocks.DEFAULT_BLOCK).uuid == blocks.DEFAULT_BLOCK)

    print()
    if FAILS:
        print(f"ПРОВАЛЕНО {len(FAILS)} из {CHECKS}: " + ", ".join(FAILS))
        return 1
    print(f"ВСЁ ЗЕЛЁНОЕ — {CHECKS} проверок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
