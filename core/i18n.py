"""Строки интерфейса и генерируемых документов на двух языках.

Единственный источник — data/i18n.json. Его же читает JavaScript обеих
версий, поэтому русский и английский не могут разъехаться между сайтом и
локальной программой: строка правится в одном месте.

Ключи с подстановками записаны как {name} и подставляются через format,
одинаково в Python и в JS.
"""

from __future__ import annotations

import json
import os

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "i18n.json")

DEFAULT = "ru"
_strings: dict[str, dict[str, str]] = {}


def load() -> dict[str, dict[str, str]]:
    global _strings
    if _strings:
        return _strings
    try:
        with open(DATA, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        _strings = {DEFAULT: {}}
        return _strings
    _strings = {k: v for k, v in raw.items() if not k.startswith("_")}
    return _strings


def languages() -> list[str]:
    return [k for k in load()]


def normalize(lang: str | None) -> str:
    """Привести язык к поддерживаемому. Всё незнакомое считаем английским."""
    code = (lang or "").strip().lower()[:2]
    if code in load():
        return code
    return "en" if code and code != DEFAULT else DEFAULT


def t(key: str, lang: str = DEFAULT, **kw) -> str:
    """Строка по ключу. Если перевода нет — берём русский, потом сам ключ."""
    table = load()
    text = table.get(normalize(lang), {}).get(key)
    if text is None:
        text = table.get(DEFAULT, {}).get(key, key)
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return text
    return text
