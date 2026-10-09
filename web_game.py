import asyncio
import os
import sys
import threading
import types
from pathlib import Path

from aiohttp import web, WSMsgType

BASE = Path(__file__).resolve().parent
WWW = BASE / "www"
PORT = int(os.environ.get("SERVER_PORT", "7113"))

game = None


def _load_game():
    try:
        import websockets
    except ImportError:
        stub = types.ModuleType("websockets")
        stub.exceptions = types.SimpleNamespace(
            ConnectionClosed=ConnectionError
        )
        sys.modules["websockets"] = stub

    src = (BASE / "game_server.py").read_text(encoding="utf-8")
    src = src.replace("\nasyncio.run(main())", "\n")

    mod = types.ModuleType("game_server_mod")
    mod.__dict__["__file__"] = str(BASE / "game_server.py")

    exec(
        compile(src, "game_server.py", "exec"),
        mod.__dict__
    )

    return mod


class _WS:
    def __init__(self, ws):
        self._ws = ws

    async def send(self, text):
        await self._ws.send_str(text)

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        async for msg in self._ws:
            if msg.type == WSMsgType.TEXT:
                yield msg.data
            elif msg.type in (
                WSMsgType.CLOSE,
                WSMsgType.CLOSING,
                WSMsgType.CLOSED,
                WSMsgType.ERROR,
            ):
                break


async def ws_route(request):
    ws = web.WebSocketResponse(heartbeat=25)
    await ws.prepare(request)

    try:
        await game.handler(_WS(ws))
    except Exception as e:
        print("game ws error:", repr(e))

    return ws


async def index(request):
    return web.FileResponse(WWW / "index.html")


async def static(request):
    name = request.match_info["name"]
    p = (WWW / name).resolve()

    if WWW.resolve() not in p.parents or not p.is_file():
        raise web.HTTPNotFound()

    return web.FileResponse(p)


async def _serve():
    app = web.Application()

    app.router.add_get("/ws/game", ws_route)
    app.router.add_get("/", index)
    app.router.add_get("/{name:.+}", static)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print("🎲 بازی منچ اجرا شد روی پورت", PORT)

    while True:
        await asyncio.sleep(3600)


def _thread_main():
    global game

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        game = _load_game()

        loop.run_until_complete(_serve())

    except Exception as e:
        print("web_game stopped:", repr(e))


def start():
    threading.Thread(
        target=_thread_main,
        daemon=True
    ).start()


if __name__ == "__main__":
    _thread_main()