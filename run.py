"""Запуск SM_Pixel.

    py run.py            — запустить и открыть браузер
    py run.py --no-open  — без браузера
    py run.py --port 9000
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = 8792


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    import uvicorn

    from web.app import BLUEPRINTS_DIR, GAME_DIR

    url = f"http://{args.host}:{args.port}/"
    print("SM_Pixel — картинка в чертёж Scrap Mechanic")
    print("  игра:    ", GAME_DIR or "не найдена")
    print("  чертежи: ", BLUEPRINTS_DIR or "не найдены (доступен только ZIP)")
    print("  адрес:   ", url)

    if not args.no_open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("web.app:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
