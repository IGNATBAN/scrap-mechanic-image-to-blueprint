"""Собрать данные для веб-версии: py tools/build_web.py

Веб-версия обязана считать по тем же числам, что и десктопная. Чтобы файлы
не разъехались руками, они не копируются в репозиторий дважды, а собираются
этим скриптом из единственного источника — папки data/ и tests/.

  data/materials.json  -> web-static/data/materials.json   (как есть)
  data/bluenoise.npy   -> web-static/data/bluenoise.json    (npy в браузере не читается)
  tests/vectors.json   -> web-static/tests/vectors.json     (как есть)
"""

from __future__ import annotations

import json
import os
import shutil
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web-static")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def main() -> int:
    os.makedirs(os.path.join(WEB, "data"), exist_ok=True)
    os.makedirs(os.path.join(WEB, "tests"), exist_ok=True)

    src = os.path.join(ROOT, "data", "materials.json")
    dst = os.path.join(WEB, "data", "materials.json")
    shutil.copyfile(src, dst)
    print("materials.json  ->", os.path.getsize(dst), "байт")

    mask = np.load(os.path.join(ROOT, "data", "bluenoise.npy")).astype(np.float32)
    out = os.path.join(WEB, "data", "bluenoise.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"size": int(mask.shape[0]),
                   "values": [round(float(v), 7) for v in mask.ravel()]}, fh)
    print("bluenoise.json  ->", os.path.getsize(out), "байт")

    # canvas.js один на обе версии: десктоп грузит его обычным скриптом,
    # веб — модулем, поэтому копии дописывается строчка экспорта.
    canvas_src = os.path.join(ROOT, "web", "static", "canvas.js")
    canvas_dst = os.path.join(WEB, "js", "canvas.js")
    text = open(canvas_src, encoding="utf-8").read()
    if "export { Viewer }" not in text:
        text += chr(10) + "export { Viewer };" + chr(10)
    os.makedirs(os.path.dirname(canvas_dst), exist_ok=True)
    with open(canvas_dst, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("canvas.js       ->", len(text), "знаков (+ export)")

    vec = os.path.join(ROOT, "tests", "vectors.json")
    if os.path.isfile(vec):
        dst = os.path.join(WEB, "tests", "vectors.json")
        shutil.copyfile(vec, dst)
        print("vectors.json    ->", os.path.getsize(dst), "байт")
    else:
        print("vectors.json    -> нет, сначала py tools/make_vectors.py")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
