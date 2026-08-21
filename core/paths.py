"""Поиск установленной игры и папки чертежей пользователя."""

from __future__ import annotations

import glob
import os
import re

APPDATA_REL = os.path.join("Axolot Games", "Scrap Mechanic", "User")

_STEAM_HINTS = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
    r"D:\Steam",
    r"D:\SteamLibrary",
    r"E:\Steam",
    r"E:\SteamLibrary",
]


def _steam_libraries() -> list[str]:
    """Все библиотеки Steam из libraryfolders.vdf + стандартные пути."""
    libs: list[str] = []
    for hint in _STEAM_HINTS:
        if os.path.isdir(hint):
            libs.append(hint)
            vdf = os.path.join(hint, "steamapps", "libraryfolders.vdf")
            if os.path.isfile(vdf):
                try:
                    text = open(vdf, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                    libs.append(m.group(1).replace("\\\\", "\\"))
    seen, out = set(), []
    for lib in libs:
        key = os.path.normcase(os.path.abspath(lib))
        if key not in seen:
            seen.add(key)
            out.append(lib)
    return out


def find_game_dir() -> str | None:
    """Папка установки Scrap Mechanic или None."""
    env = os.environ.get("SM_GAME_DIR")
    if env and os.path.isdir(env):
        return env
    for lib in _steam_libraries():
        cand = os.path.join(lib, "steamapps", "common", "Scrap Mechanic")
        if os.path.isdir(os.path.join(cand, "Data")):
            return cand
    return None


def find_user_dirs() -> list[str]:
    """Все папки User_<steamid> (обычно одна)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    root = os.path.join(appdata, APPDATA_REL)
    if not os.path.isdir(root):
        return []
    return sorted(d for d in glob.glob(os.path.join(root, "User_*")) if os.path.isdir(d))


def find_blueprints_dir() -> str | None:
    """Папка, куда игра складывает чертежи. Создаётся, если её ещё нет."""
    env = os.environ.get("SM_BLUEPRINTS_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    users = find_user_dirs()
    if not users:
        return None
    # если папок несколько — берём ту, где уже больше чертежей
    def score(user_dir: str) -> int:
        bp = os.path.join(user_dir, "Blueprints")
        return len(os.listdir(bp)) if os.path.isdir(bp) else -1

    best = max(users, key=score)
    bp = os.path.join(best, "Blueprints")
    os.makedirs(bp, exist_ok=True)
    return bp


def steam_id(user_dir_or_bp: str | None) -> int:
    """Вытащить SteamID64 из пути User_<id>."""
    if not user_dir_or_bp:
        return 0
    m = re.search(r"User_(\d+)", user_dir_or_bp)
    return int(m.group(1)) if m else 0
