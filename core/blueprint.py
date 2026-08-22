"""Сборка чертежа Scrap Mechanic из прямоугольников.

Формат сверен с файлами, которые пишет сама игра (User/.../Blueprints и
Survival/LocalBlueprints):

blueprint.json
    {"bodies":[{"childs":[ ... ]}],"version":4}
    child = {"bounds":{"x":..,"y":..,"z":..},
             "color":"RRGGBB",              # ВЕРХНИЙ регистр, БЕЗ решётки
             "pos":{"x":..,"y":..,"z":..},  # левый-нижний-ближний угол блока
             "shapeId":"<uuid блока>",
             "xaxis":1,"zaxis":3}           # 1/3 = без поворота

description.json
    {"description":"...","localId":"<uuid>","name":"...",
     "type":"Blueprint","version":0}
    Имя папки чертежа обязано совпадать с localId.

Система координат игры — Z вверх. Значит:
    вертикально («картина»)   -> пиксели ложатся в плоскость X-Z
    горизонтально («на земле») -> пиксели ложатся в плоскость X-Y
"""

from __future__ import annotations

import json
import os
import shutil
import uuid as uuidlib

from .mesh import Rect

VERTICAL = "vertical"
HORIZONTAL = "horizontal"

_CHILD = (
    '{"bounds":{"x":%d,"y":%d,"z":%d},"color":"%s",'
    '"pos":{"x":%d,"y":%d,"z":%d},"shapeId":"%s","xaxis":1,"zaxis":3}'
)


def origin_of(grid_w: int, grid_h: int, orientation: str = VERTICAL, center: bool = True) -> tuple[int, int]:
    """Сдвиг картинки в локальных координатах чертежа: (по горизонтали, по вертикали).

    От него зависит, какая ячейка текстуры ляжет на какую клетку: развёртка
    в игре считается от локальных координат блока (проверено снимком).
    Клетка (x, y) картинки оказывается в локальных
        (x + ox,  grid_h - 1 - y + oz).
    Обе формулы обязаны совпадать с build_json ниже — иначе узор в
    предпросмотре разъедется с постройкой.
    """
    ox = -(grid_w // 2) if center else 0
    oz = (-(grid_h // 2) if center else 0) if orientation == HORIZONTAL else 0
    return ox, oz


def rgb_resolver(shape_id: str):
    """Ключ = упакованный цвет 0xRRGGBB, блок один на всю постройку."""
    return lambda key: ("%06X" % key, shape_id)


def palette_resolver(palette, fallback_block: str):
    """Ключ = индекс в наборе материалов: у каждого свой цвет и свой блок."""
    def resolve(key: int):
        paint = palette.paint[key]
        block = palette.block[key] or fallback_block
        return paint.upper(), block

    return resolve


def build_json(
    rects: list[Rect],
    grid_w: int,
    grid_h: int,
    resolve,
    orientation: str = VERTICAL,
    center: bool = True,
    depth: int = 1,
) -> str:
    """Собрать текст blueprint.json.

    resolve: ключ материала -> (цвет "RRGGBB", uuid блока). Результаты
    кэшируются: разных материалов всегда сильно меньше, чем деталей.

    Строки JSON клеим вручную: на десятках тысяч деталей это на порядок
    быстрее и экономнее по памяти, чем json.dumps со словарями.
    """
    if isinstance(resolve, str):                 # совместимость: передали uuid блока
        resolve = rgb_resolver(resolve)

    off_x = -(grid_w // 2) if center else 0
    parts: list[str] = []
    depth = max(1, int(depth))
    cache: dict[int, tuple[str, str]] = {}

    def material(key: int) -> tuple[str, str]:
        got = cache.get(key)
        if got is None:
            got = resolve(key)
            cache[key] = got
        return got

    if orientation == HORIZONTAL:
        off_y = -(grid_h // 2) if center else 0
        for x, y, w, h, key in rects:
            color, shape = material(key)
            # строка 0 — верх картинки; сверху вниз = уменьшение Y
            parts.append(_CHILD % (w, h, depth, color, x + off_x, grid_h - (y + h) + off_y, 0, shape))
    else:
        for x, y, w, h, key in rects:
            color, shape = material(key)
            # строка 0 — верх картинки; сверху вниз = уменьшение Z
            parts.append(_CHILD % (w, depth, h, color, x + off_x, 0, grid_h - (y + h), shape))

    return '{"bodies":[{"childs":[' + ",".join(parts) + ']}],"version":4}'


def used_blocks(rects: list[Rect], resolve) -> dict[str, int]:
    """Сколько деталей какого блока — для сводки в интерфейсе."""
    if isinstance(resolve, str):
        return {resolve: len(rects)}
    counts: dict[str, int] = {}
    cache: dict[int, str] = {}
    for _, _, _, _, key in rects:
        block = cache.get(key)
        if block is None:
            block = resolve(key)[1]
            cache[key] = block
        counts[block] = counts.get(block, 0) + 1
    return counts


def description_json(name: str, local_id: str, note: str = "") -> str:
    return json.dumps(
        {
            "description": note or "Сделано в SM_Pixel — конвертер картинки в чертёж",
            "localId": local_id,
            "name": name,
            "type": "Blueprint",
            "version": 0,
        },
        ensure_ascii=False,
        indent=3,
    )


def write_folder(
    target_dir: str,
    name: str,
    blueprint_text: str,
    icon: bytes | None = None,
    note: str = "",
    local_id: str | None = None,
    overwrite_same_name: bool = False,
) -> str:
    """Создать папку чертежа. Возвращает путь.

    Имя папки = localId, как это делает игра.
    """
    local_id = local_id or str(uuidlib.uuid4())
    path = os.path.join(target_dir, local_id)

    if overwrite_same_name:
        old = _find_by_name(target_dir, name)
        if old:
            shutil.rmtree(old, ignore_errors=True)

    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "blueprint.json"), "w", encoding="utf-8") as fh:
        fh.write(blueprint_text)
    with open(os.path.join(path, "description.json"), "w", encoding="utf-8") as fh:
        fh.write(description_json(name, local_id, note))
    if icon:
        with open(os.path.join(path, "icon.png"), "wb") as fh:
            fh.write(icon)
    return path


def _find_by_name(target_dir: str, name: str) -> str | None:
    if not os.path.isdir(target_dir):
        return None
    for entry in os.listdir(target_dir):
        desc = os.path.join(target_dir, entry, "description.json")
        if not os.path.isfile(desc):
            continue
        try:
            with open(desc, encoding="utf-8") as fh:
                if json.load(fh).get("name") == name:
                    return os.path.join(target_dir, entry)
        except (OSError, ValueError):
            continue
    return None


def zip_bundle(items: list[dict], extras: dict[str, bytes | str] | None = None,
               lang: str = "ru") -> bytes:
    """Архив с одним или несколькими чертежами плюс произвольные файлы рядом.

    items: [{"name": ..., "text": ..., "icon": bytes|None, "note": ...}, ...]
    """
    import io
    import zipfile

    buf = io.BytesIO()
    folders = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for item in items:
            local_id = str(uuidlib.uuid4())
            folders.append(local_id)
            z.writestr(f"{local_id}/blueprint.json", item["text"])
            z.writestr(f"{local_id}/description.json", description_json(item["name"], local_id, item.get("note", "")))
            if item.get("icon"):
                z.writestr(f"{local_id}/icon.png", item["icon"])
        for path, data in (extras or {}).items():
            z.writestr(path, data)

        from . import i18n

        many = len(folders) > 1
        where = "%APPDATA%\\Axolot Games\\Scrap Mechanic\\User\\User_<SteamID>\\Blueprints\\"
        head = (i18n.t("doc.whereMany", lang, n=len(folders)) if many
                else i18n.t("doc.whereOne", lang))
        note = [head, where, i18n.t("doc.whereTail", lang)]
        if many:
            note += ["", i18n.t("doc.whereGuide", lang)]
        z.writestr(i18n.t("doc.whereName", lang), "\r\n".join(note) + "\r\n")

    return buf.getvalue()


def zip_bytes(name: str, blueprint_text: str, icon: bytes | None, note: str = "",
              lang: str = "ru") -> bytes:
    """Один чертёж архивом — чтобы перенести на другой компьютер."""
    return zip_bundle([{"name": name, "text": blueprint_text, "icon": icon, "note": note}], lang=lang)
