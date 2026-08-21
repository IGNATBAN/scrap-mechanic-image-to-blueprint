"""Каталог блоков Scrap Mechanic.

Источник истины — .shapeset игры:
  Data/Objects/Database/ShapeSets/blocks.shapeset          (ваниль)
  Survival/Objects/Database/ShapeSets/blocks.shapeset      (выживание)

Список ниже вычитан из установленной игры и захардкожен, чтобы конвертер
работал без установленной игры. Если игра найдена — список дополняется
из её файлов (см. load_blocks).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Block:
    name: str          # внутреннее имя из shapeset
    title: str         # человекочитаемое
    uuid: str
    color: str         # цвет по умолчанию, hex без #
    tiling: int        # плотность текстуры; чем больше, тем мельче рисунок
    material: str
    glass: bool = False
    flat: int = 0      # 0..3 — насколько текстура «плоская» (3 = лучше всего для картинки)


# fmt: off
BLOCKS: list[Block] = [
    # ── лучшие для мозаики: ровная матовая поверхность ───────────────────────
    Block("blk_plasticwall",  "Пластиковая панель",   "e981c337-1c8a-449c-8602-1dd990cbba3a", "eeeeee", 8,  "Plastic",   flat=3),
    Block("blk_drywall",      "Гипсокартон",          "b145d9ae-4966-4af6-9497-8fca33f9aee3", "979797", 8,  "Rock",      flat=3),
    Block("blk_plastic",      "Пластик",              "628b2d61-5ceb-43e9-8334-a4135566df7a", "0b9ade", 4,  "Plastic",   flat=2),
    Block("blk_concrete2",    "Бетон 2",              "ff234e42-5da4-43cc-8893-940547c97882", "8d8f89", 8,  "Rock",      flat=2),
    Block("blk_concrete1",    "Бетон 1",              "a6c6ce30-dd47-4587-b475-085d55c6a3b4", "8d8f89", 8,  "Rock",      flat=2),
    Block("blk_concrete3",    "Бетон 3",              "e281599c-2343-4c86-886e-b2c1444e8810", "c9d7dc", 8,  "Rock",      flat=2),
    Block("blk_metal1",       "Металл 1",             "8aedf6c2-94e1-4506-89d4-a0227c552f1e", "675f51", 8,  "Metal",     flat=1),
    Block("blk_metal2",       "Металл 2",             "1016cafc-9f6b-40c9-8713-9019d399783f", "869499", 8,  "Metal",     flat=1),
    Block("blk_metal3",       "Металл 3",             "c0dfdea5-a39d-433a-b94a-299345a5df46", "88a5ac", 8,  "Metal",     flat=1),
    Block("blk_cardboard",    "Картон",               "f0cba95b-2dc4-4492-8fd9-36546a4cb5aa", "a48052", 16, "Cardboard", flat=2),
    Block("blk_wood1",        "Дерево 1",             "df953d9c-234f-4ac2-af5e-f0490b223e71", "9b683a", 8,  "Wood",      flat=0),
    Block("blk_wood2",        "Дерево 2",             "1897ee42-0291-43e4-9645-8c5a5d310398", "dc9153", 8,  "Wood",      flat=0),
    Block("blk_wood3",        "Дерево 3",             "061b5d4b-0a6a-4212-b0ae-9e9681f1cbfb", "f2ad74", 8,  "Wood",      flat=0),
    Block("blk_scrapwood",    "Хлам-дерево",          "1fc74a28-addb-451a-878d-c3c605d63811", "cd9d71", 8,  "Wood",      flat=0),
    Block("blk_scrapmetal",   "Хлам-металл",          "1f7ac0bb-ad45-4246-9817-59bdf7f7ab39", "df6226", 16, "Scrapmetal",flat=0),
    Block("blk_scrapstone",   "Хлам-камень",          "30a2288b-e88e-4a92-a916-1edbfc2b2dac", "848484", 8,  "Rock",      flat=1),
    Block("blk_bricks",       "Кирпич",               "0603b36e-0bdb-4828-b90c-ff19abcdfe34", "af967b", 8,  "Rock",      flat=0),
    Block("blk_tiles",        "Плитка",               "8ca49bff-eeef-4b43-abd0-b527a567f1b7", "bfdfed", 4,  "Rock",      flat=0),
    Block("blk_insulation",   "Утеплитель",           "9be6047c-3d44-44db-b4b9-9bcf8a9aab20", "fff063", 8,  "Plastic",   flat=1),
    Block("blk_carpet",       "Ковролин",             "febce8a6-6c05-4e5d-803b-dfa930286944", "368085", 4,  "Fabric",    flat=2),
    Block("blk_bubblewrap",   "Пупырка",              "f406bf6e-9fd5-4aa0-97c1-0b3c2118198e", "9acfd2", 2,  "Bubblewrap",flat=0),
    Block("blk_treadplate",   "Рифлёный лист",        "f7d4bfed-1093-49b9-be32-394c872a1ef4", "43494d", 4,  "Metal",     flat=0),
    Block("blk_sand",         "Песок",                "c56700d9-bbe5-4b17-95ed-cef05bd8be1b", "c69146", 8,  "Sand",      flat=1),
    Block("blk_caution",      "Предупреждающий",      "09ca2713-28ee-4119-9622-e85490034758", "ce9e0c", 8,  "Plastic",   flat=0),
    # ── стекло: цвет получится полупрозрачным ────────────────────────────────
    Block("blk_glass",        "Стекло",               "5f41af56-df4c-4837-9b3c-10781335757f", "e4f8ff", 8,  "Glass",  glass=True, flat=2),
    Block("blk_glasstile",    "Стеклоблок",           "749f69e0-56c9-488c-adf6-66c58531818f", "c2f9ff", 8,  "Glass",  glass=True, flat=1),
    Block("blk_armoredglass", "Бронестекло",          "b5ee5539-75a2-4fef-873b-ef7c9398b3f5", "3abfb1", 4,  "Glass",  glass=True, flat=1),
]
# fmt: on

# Блок по умолчанию: самая ровная матовая поверхность из всех.
DEFAULT_BLOCK = "e981c337-1c8a-449c-8602-1dd990cbba3a"  # blk_plasticwall

_BY_UUID = {b.uuid: b for b in BLOCKS}


def get(uuid: str) -> Block:
    return _BY_UUID.get(uuid) or _BY_UUID[DEFAULT_BLOCK]


def catalog() -> list[dict]:
    """Список для веб-интерфейса, отсортирован «сначала самые плоские»."""
    # сначала матовые и ровные, стекло — в конец отдельной группой
    ordered = sorted(BLOCKS, key=lambda b: (b.glass, -b.flat, b.title))
    return [asdict(b) for b in ordered]


def load_from_game(game_dir: str | None) -> int:
    """Дополнить каталог блоками из установленной игры (в т.ч. из обновлений).

    Возвращает число добавленных блоков. Существующие записи не трогает —
    у них есть переведённые названия и оценка flat.
    """
    if not game_dir or not os.path.isdir(game_dir):
        return 0

    added = 0
    candidates = [
        os.path.join(game_dir, "Data", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
        os.path.join(game_dir, "Survival", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for raw in data.get("blockList") or []:
            uuid = raw.get("uuid")
            name = raw.get("name")
            if not uuid or not name or uuid in _BY_UUID:
                continue
            color = str(raw.get("color", "cccccc"))[:6]
            block = Block(
                name=name,
                title=name.replace("blk_", "").replace("_", " ").capitalize(),
                uuid=uuid,
                color=color,
                tiling=int(raw.get("tiling") or 8),
                material=str(raw.get("physicsMaterial") or ""),
                glass=str(raw.get("physicsMaterial") or "").lower() == "glass",
                flat=0,
            )
            BLOCKS.append(block)
            _BY_UUID[uuid] = block
            added += 1
    return added
