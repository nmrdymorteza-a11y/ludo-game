import asyncio
import time
import json
import random
import websockets

rooms = {}
closed_rooms = set()

COLORS = ["red", "yellow", "green", "blue"]

# مسیر مشترک 52 خانه‌ای منچ
# فعلاً شماره خانه‌ها برای منطق سرور استفاده می‌شوند.
TRACK_LENGTH = 40
TEST_MODE = False
MIN_PLAYERS = 2   # حالت تست تک‌نفره؛ برای بازی واقعی بذار 2
FINAL = 43   # خانه‌های قل: 52 تا 55
OFFSETS = {"red": 1, "blue": 11, "green": 21, "yellow": 31}


def abs_pos(player, rel):
    return (rel + OFFSETS.get(player.get("color"), 0)) % TRACK_LENGTH


def create_pieces():
    return [-1, -1, -1, -1]
    # -1 یعنی مهره هنوز وارد بازی نشده
    # 0 تا 51 یعنی روی مسیر


def create_room():
    return {
        "players": [],
        "turn": 0,
        "dice": None,
        "started": False,
        "host": None,
        "expires": time.time() + 300
    }


def public_player(player):
    return {
        "id": player["id"],
        "name": player["name"],
        "color": player["color"],
        "pieces": player["pieces"]
    }


def room_state(room):
    game = rooms[room]

    return {
        "type": "state",
        "players": [
            public_player(p)
            for p in game["players"]
        ],
        "turn": game["turn"],
        "dice": game["dice"],
        "winner": game.get("winner"),
        "deadline": game.get("deadline"),
        "started": game.get("started"),
        "host": game.get("host"),
        "expires": game.get("expires"),
        "status":
            "waiting"
            if not game.get("started")
            else "playing"
    }


async def send_state(room):
    if room not in rooms:
        return

    message = json.dumps(
        room_state(room),
        ensure_ascii=False
    )

    for player in rooms[room]["players"][:]:
        try:
            await player["socket"].send(message)
        except:
            pass


async def send_error(websocket, message):
    await websocket.send(
        json.dumps({
            "type": "error",
            "message": message
        }, ensure_ascii=False)
    )


def can_enter_piece(player):
    return any(
        piece == -1
        for piece in player["pieces"]
    )


def enter_piece(player):
    for i in range(4):
        if player["pieces"][i] == -1:
            player["pieces"][i] = 0
            return i

    return None


def move_piece(player, piece_index, dice):
    pieces = player["pieces"]

    if piece_index < 0 or piece_index >= 4:
        return False

    position = pieces[piece_index]

    if position == -1:
        if dice != 6:
            return False

        pieces[piece_index] = 0
        return True

    new_position = position + dice

    if new_position >= TRACK_LENGTH:
        # فعلاً اگر از مسیر خارج شود،
        # آن را به خانه نهایی می‌رسانیم.
        pieces[piece_index] = TRACK_LENGTH
    else:
        pieces[piece_index] = new_position

    return True


def hit_opponents(room, player, piece_index):
    # برنده: هر 4 مهره در خانه‌های قل
    if all(p >= TRACK_LENGTH for p in player["pieces"]):
        room["winner"] = player.get("name", "؟")

    position = player["pieces"][piece_index]
    if position < 0 or position >= TRACK_LENGTH:
        return

    mine = abs_pos(player, position)
    for opponent in room["players"]:
        if opponent["id"] == player["id"]:
            continue
        for i in range(4):
            p = opponent["pieces"][i]
            if 1 <= p < TRACK_LENGTH and abs_pos(opponent, p) == mine:
                opponent["pieces"][i] = -1



async def expire_room(room_name, game):
    await asyncio.sleep(300)
    if rooms.get(room_name) is not game or game.get("started"):
        return
    closed_rooms.add(room_name)
    msg = json.dumps({"type": "closed",
        "message": "⌛ ۵ دقیقه گذشت و بازی شروع نشد. این لینک باطل شد."},
        ensure_ascii=False)
    for p in game["players"][:]:
        try:
            await p["socket"].send(msg)
        except:
            pass
    rooms.pop(room_name, None)


async def handle_start(room_name, player):
    game = rooms.get(room_name)
    if not game or game.get("started"):
        return
    if game.get("host") != player["id"]:
        await send_error(player["socket"], "فقط سازندهٔ بازی می‌تواند شروع کند.")
        return
    if len(game["players"]) < 2:
        await send_error(player["socket"], "حداقل ۲ بازیکن لازم است.")
        return
    game["started"] = True
    game["expires"] = None
    game["turn"] = 0
    game["dice"] = None
    await send_state(room_name)


async def handle_join(websocket, data):
    room_name = data.get("room", "test-room")

    if room_name in closed_rooms:
        await websocket.send(json.dumps({"type": "closed",
            "message": "🚫 این لینک باطل شده است."}, ensure_ascii=False))
        return room_name, None

    if room_name not in rooms:
        rooms[room_name] = create_room()
        asyncio.ensure_future(expire_room(room_name, rooms[room_name]))

    game = rooms[room_name]

    if game.get("started"):
        await send_error(websocket, "❌ بازی شروع شده است.")
        return room_name, None

    if len(game["players"]) >= 4:
        await send_error(
            websocket,
            "❌ این اتاق ۴ بازیکن دارد و پر است."
        )
        return room_name, None

    player = {
        "id": str(id(websocket)),
        "name": data.get("name", "بازیکن"),
        "color": COLORS[len(game["players"])],
        "pieces": create_pieces(),
        "socket": websocket
    }

    game["players"].append(player)
    if game.get("host") is None:
        game["host"] = player["id"]

    await websocket.send(json.dumps(
        {"type": "you", "id": player["id"]}, ensure_ascii=False))

    await send_state(room_name)

    return room_name, player


def blocked_by_safe(game, player, new_position):
    if not (0 <= new_position < TRACK_LENGTH):
        return False
    mine = abs_pos(player, new_position)
    for opp in game["players"]:
        if opp["id"] == player["id"]:
            continue
        if opp["pieces"].count(0) and abs_pos(opp, 0) == mine:
            return True
    return False


def can_move(player, dice, game=None):
    for pos in player["pieces"]:
        if pos == -1:
            if dice == 6 and 0 not in player["pieces"]:
                return True
        else:
            new_pos = pos + dice
            if game is not None and blocked_by_safe(game, player, new_pos):
                continue
            if new_pos <= FINAL and not (
                new_pos >= TRACK_LENGTH and new_pos in player["pieces"]
            ):
                return True
    return False


TURN_SECONDS = 10


def cancel_timer(game):
    t = game.get("timer")
    if t and not t.done():
        t.cancel()
    game["timer"] = None
    game["deadline"] = None


async def turn_timeout(room_name, turn_index, token):
    try:
        await asyncio.sleep(TURN_SECONDS)
    except asyncio.CancelledError:
        return
    game = rooms.get(room_name)
    if not game or game.get("token") != token:
        return
    if game["dice"] is None:
        return
    game["dice"] = None
    game["tries"] = 0
    game["turn"] = (turn_index + 1) % len(game["players"])
    await send_state(room_name)


def start_timer(room_name):
    game = rooms.get(room_name)
    if not game:
        return
    cancel_timer(game)
    game["token"] = game.get("token", 0) + 1
    import time
    game["deadline"] = time.time() + TURN_SECONDS
    game["timer"] = asyncio.ensure_future(
        turn_timeout(room_name, game["turn"], game["token"])
    )


async def handle_roll(room_name, player):
    if room_name not in rooms:
        return

    game = rooms[room_name]
    players = game["players"]
    if not game.get("started"):
        await send_error(player["socket"], "⏳ بازی هنوز شروع نشده.")
        return
    if game.get("winner"):
        await send_error(player["socket"], "🏆 بازی تمام شده است.")
        return

    if len(players) < MIN_PLAYERS:
        await send_error(
            player["socket"],
            "برای شروع حداقل ۲ بازیکن لازم است."
        )
        return

    turn = game["turn"]

    if turn >= len(players):
        game["turn"] = 0
        turn = 0

    current = players[turn]

    if not TEST_MODE and current["id"] != player["id"]:
        await send_error(
            player["socket"],
            "⏳ هنوز نوبت شما نیست."
        )
        return

    if not TEST_MODE and game["dice"] is not None:
        return

    number = random.randint(1, 6)

    game["dice"] = number

    # اگر ۶ آمد، بازیکن می‌تواند مهره وارد کند.
    # اگر مهره‌ای وارد نکرد، همچنان می‌تواند
    # یکی از مهره‌های موجود را حرکت دهد.
    await send_state(room_name)

    if not can_move(player, number, game):
        cancel_timer(game)
        await asyncio.sleep(1.2)
        game["dice"] = None
        if number != 6:
            none_on_board = all(x == -1 for x in player["pieces"])
            tries = game.get("tries", 0) + 1
            if none_on_board and tries < 3:
                game["tries"] = tries
            else:
                game["tries"] = 0
                game["turn"] = (turn + 1) % len(players)
        await send_state(room_name)
    else:
        start_timer(room_name)
        await send_state(room_name)


async def handle_move(room_name, player, data):
    if room_name not in rooms:
        return

    game = rooms[room_name]
    players = game["players"]
    if not game.get("started"):
        await send_error(player["socket"], "⏳ بازی هنوز شروع نشده.")
        return
    if game.get("winner"):
        await send_error(player["socket"], "🏆 بازی تمام شده است.")
        return

    if len(players) < MIN_PLAYERS:
        return

    turn = game["turn"]

    if turn >= len(players):
        turn = 0
        game["turn"] = 0

    current = players[turn]

    if current["id"] != player["id"]:
        await send_error(
            player["socket"],
            "⏳ هنوز نوبت شما نیست."
        )
        return

    dice = game["dice"]

    if dice is None:
        await send_error(
            player["socket"],
            "🎲 اول تاس بینداز."
        )
        return

    try:
        piece_index = int(data.get("piece", -1))
    except:
        piece_index = -1

    pieces = player["pieces"]

    if piece_index < 0 or piece_index >= 4:
        await send_error(
            player["socket"],
            "❌ شماره مهره نامعتبر است."
        )
        return

    position = pieces[piece_index]

    # مهره بیرون از بازی
    if position == -1:

        if dice != 6:
            await send_error(
                player["socket"],
                "❌ برای آوردن این مهره باید ۶ بیاوری."
            )
            return

        if 0 in pieces:
            await send_error(
                player["socket"],
                "❌ خانهٔ شروع پر است؛ مهرهٔ روی آن را حرکت بده."
            )
            return

        pieces[piece_index] = 0

    else:

        new_position = position + dice

        if new_position > FINAL or (new_position >= TRACK_LENGTH and new_position in pieces):
            await send_error(
                player["socket"],
                "❌ این مهره با این تاس نمی‌تواند حرکت کند."
            )
            return

        if blocked_by_safe(game, player, new_position):
            await send_error(
                player["socket"],
                "🛡️ آن خانه امن است؛ با مهرهٔ دیگر حرکت کن."
            )
            return

        pieces[piece_index] = new_position

    # اگر مهره به خانه نهایی رسیده
    # دیگر نمی‌تواند حرکت کند.
    if pieces[piece_index] == TRACK_LENGTH:
        pass

    # زدن مهره‌های حریف
    hit_opponents(
        game,
        player,
        piece_index
    )

    if game.get("winner"):
        closed_rooms.add(room_name)

    # ۶ یعنی دوباره نوبت همین بازیکن
    if dice == 6:
        game["turn"] = turn
    else:
        game["turn"] = (
            turn + 1
        ) % len(players)

    game["dice"] = None
    game["tries"] = 0
    cancel_timer(game)

    await send_state(room_name)


async def leave_room(room_name, player):
    game = rooms.get(room_name)
    if not game:
        return
    players = game["players"]
    idx = next((i for i, p in enumerate(players)
                if p["id"] == player["id"]), None)
    if idx is None:
        return
    players.pop(idx)
    if not players:
        cancel_timer(game)
        rooms.pop(room_name, None)
        return
    if game.get("host") == player["id"]:
        game["host"] = players[0]["id"]
    if game.get("started"):
        if game["turn"] == idx:
            cancel_timer(game)
            game["dice"] = None
            game["tries"] = 0
            game["turn"] = idx % len(players)
        elif idx < game["turn"]:
            game["turn"] -= 1
        if game["turn"] >= len(players):
            game["turn"] = 0
        if len(players) == 1:
            cancel_timer(game)
            game["dice"] = None
            game["winner"] = players[0]["name"]
            closed_rooms.add(room_name)
    await send_state(room_name)


async def handler(websocket):
    room_name = None
    player = None

    try:

        async for raw in websocket:

            try:
                data = json.loads(raw)
            except:
                continue

            action = data.get("action")
            print("📩", action, data, flush=True)

            if action == "join":

                room_name, player = await handle_join(
                    websocket,
                    data
                )

            elif action == "roll":

                if player:
                    await handle_roll(
                        room_name,
                        player
                    )

            elif action == "leave":
                if player:
                    await leave_room(room_name, player)
                    player = None
                    room_name = None

            elif action == "start":
                if player:
                    await handle_start(room_name, player)

            elif action == "move":

                if player:
                    await handle_move(
                        room_name,
                        player,
                        data
                    )

    except websockets.exceptions.ConnectionClosed:
        pass

    finally:

        if room_name and room_name in rooms and player:

            game = rooms[room_name]

            game["players"] = [
                p for p in game["players"]
                if p["id"] != player["id"]
            ]

            if not game["players"]:
                del rooms[room_name]

            else:
                game["turn"] = 0
                game["dice"] = None
                game["host"] = game["players"][0]["id"]

                await send_state(room_name)


async def main():

    print("🎲 سرور منچ واقعی فعال شد")
    print("👥 ظرفیت هر اتاق: 4 نفر")
    print("🎯 حداقل بازیکن: 2 نفر")
    print("⚔️ سیستم حرکت و زدن مهره فعال است")
    print("🌐 WebSocket: ws://0.0.0.0:8765")

    async with websockets.serve(
        handler,
        "0.0.0.0",
        8765
    ):
        await asyncio.Future()


asyncio.run(main())
