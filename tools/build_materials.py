"""Собрать таблицу «как блок тонирует краску»: py tools/build_materials.py

Зачем. В Scrap Mechanic краска красит блок, а текстура блока даёт рисунок и
собственную яркость. Один и тот же цвет на пластиковой панели, картоне и
металле выглядит по-разному. Если разрешить конвертеру ставить разные блоки,
палитра из 40 цветов превращается в несколько сотен оттенков — как раз тех
приглушённых и тёмных, которых в палитре краскопульта нет.

Как устроена текстура. Файл из поля "dif" в .shapeset — это НЕ альбедо, а
наложение поверх краски: RGB задаёт собственный тон материала, альфа — силу
наложения. У бетона RGB чёрный, а альфа доходит до 34/255 — текстура просто
слегка притемняет краску грязью и швами. У картона альфа до 255 и коричневый
RGB — там краска почти не видна.

Значит цвет блока, покрашенного краской P:

    итог = P x (1 - alpha) + tint,   где alpha = среднее альфы,
                                          tint  = среднее (RGB x альфа)

Обе величины считаются в линейном свете (усреднять sRGB нельзя) и хранятся
в data/materials.json. Это приближение среднего вида блока: настоящий шейдер
добавляет затенение по нормалям, блики и карту asg — но для подбора цвета
такой модели достаточно, а проверить результат можно предпросмотром.

Результат кладётся в data/materials.json, чтобы не читать при каждом запуске
десятки TGA по 2048x2048.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from core import paths  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "materials.json")


def resolve(path: str, game_dir: str) -> str | None:
    """$GAME_DATA/... и $SURVIVAL_DATA/... -> реальный путь."""
    if not path:
        return None
    for token, sub in (("$GAME_DATA", "Data"), ("$SURVIVAL_DATA", "Survival"), ("$CHALLENGE_DATA", "ChallengeData")):
        if path.startswith(token):
            real = os.path.join(game_dir, sub, path[len(token) + 1:].replace("/", os.sep))
            if os.path.isfile(real):
                return real
    return None


def _to_linear(arr: np.ndarray) -> np.ndarray:
    return np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)


def overlay(path: str) -> tuple[float, list[float]] | None:
    """Средняя сила наложения и средний премультиплицированный тон."""
    try:
        img = Image.open(path).convert("RGBA")
        # усреднять надо по всем текселям, но 256x256 хватает и читается быстро
        if max(img.size) > 256:
            img = img.resize((256, 256), Image.Resampling.BOX)
    except (OSError, ValueError) as exc:
        print("  не прочитал", os.path.basename(path), exc)
        return None

    arr = np.asarray(img, dtype=np.float32) / 255.0
    alpha = arr[..., 3]
    tint = _to_linear(arr[..., :3]) * alpha[..., None]
    return float(alpha.mean()), [float(v) for v in tint.reshape(-1, 3).mean(axis=0)]


def main() -> int:
    game = paths.find_game_dir()
    if not game:
        print("Игра не найдена — таблицу не собрать")
        return 1

    shapesets = [
        os.path.join(game, "Data", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
        os.path.join(game, "Survival", "Objects", "Database", "ShapeSets", "blocks.shapeset"),
    ]

    entries: dict[str, dict] = {}
    for shapeset in shapesets:
        if not os.path.isfile(shapeset):
            continue
        with open(shapeset, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        for raw in data.get("blockList") or []:
            uuid, name = raw.get("uuid"), raw.get("name")
            if not uuid or uuid in entries:
                continue
            dif = resolve(str(raw.get("dif") or ""), game)
            result = overlay(dif) if dif else None
            if result is None:
                continue
            alpha, tint = result
            entries[uuid] = {
                "name": name,
                "alpha": round(alpha, 5),
                "tint": [round(v, 5) for v in tint],
                "tiling": raw.get("tiling"),
                "glass": str(raw.get("physicsMaterial") or "").lower() == "glass",
            }
            print("  %-26s наложение=%.3f тон=%s" % (name, alpha, ["%.3f" % v for v in tint]))

    if not entries:
        print("Ни одной текстуры прочитать не удалось")
        return 1

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump({"model": "final_linear = paint_linear * (1 - alpha) + tint", "blocks": entries},
                  fh, ensure_ascii=False, indent=1)
    print(f"\nЗаписано {len(entries)} блоков в {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
