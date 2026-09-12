import asyncio
import json
import logging
from urllib.parse import parse_qs

logger = logging.getLogger(__name__)

# Active connections: set of Connection objects
_active_connections = set()
_loop = None
_redis_listener_started = False

class Connection:
    def __init__(self, send, teacher_id=None):
        self.send = send
        self.teacher_id = str(teacher_id) if teacher_id else None

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other


async def _start_redis_listener():
    """Listens to Redis pub/sub if Redis is available and running."""
    global _redis_listener_started
    if _redis_listener_started:
        return
    _redis_listener_started = True

    try:
        from django.conf import settings
        redis_url = getattr(settings, "REDIS_URL", None)
        if not redis_url:
            return

        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, decode_responses=True)
        pubsub = r.pubsub()
        await pubsub.subscribe("risala_slot_events")
        logger.info("Subscribed to Redis channel risala_slot_events")

        while True:
            try:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("data"):
                    payload_str = message["data"]
                    await _broadcast_locally(payload_str)
            except Exception as e:
                logger.debug("Redis pubsub listener loop error: %s", e)
            await asyncio.sleep(0.05)
    except Exception as e:
        logger.debug("Redis listener not started (running in memory mode): %s", e)


async def _broadcast_locally(payload_str: str):
    """Broadcasts a JSON payload string to all matching active connections."""
    if not _active_connections:
        return

    try:
        data_obj = json.loads(payload_str)
        teacher_id = None
        if isinstance(data_obj, dict):
            slot_data = data_obj.get("data", {})
            if isinstance(slot_data, dict):
                teacher_id = str(slot_data.get("teacher_id")) if slot_data.get("teacher_id") else None
    except Exception:
        teacher_id = None

    dead_conns = []
    for conn in list(_active_connections):
        if conn.teacher_id and teacher_id and conn.teacher_id != teacher_id:
            continue
        try:
            await conn.send({"type": "websocket.send", "text": payload_str})
        except Exception:
            dead_conns.append(conn)

    for dc in dead_conns:
        _active_connections.discard(dc)


async def websocket_application(scope, receive, send):
    global _loop
    _loop = asyncio.get_running_loop()

    # Start redis listener in background if not already started
    asyncio.create_task(_start_redis_listener())

    # Extract query params, e.g. ws://.../ws/slots/?teacher_id=...
    query_string = scope.get("query_string", b"").decode("utf-8")
    params = parse_qs(query_string)
    teacher_id = params.get("teacher_id", [None])[0]

    conn = Connection(send, teacher_id)

    try:
        while True:
            event = await receive()

            if event["type"] == "websocket.connect":
                await send({"type": "websocket.accept"})
                _active_connections.add(conn)
                # Send confirmation
                await send({
                    "type": "websocket.send",
                    "text": json.dumps({"type": "connected", "teacher_id": teacher_id})
                })

            elif event["type"] == "websocket.disconnect":
                break

            elif event["type"] == "websocket.receive":
                text = event.get("text", "")
                if text == "ping":
                    await send({"type": "websocket.send", "text": "pong!"})
                else:
                    try:
                        data = json.loads(text)
                        if data.get("type") == "ping":
                            await send({"type": "websocket.send", "text": json.dumps({"type": "pong"})})
                    except Exception:
                        pass
    finally:
        _active_connections.discard(conn)


def broadcast_slot_event(event_type: str, slot_data: dict):
    """
    Broadcasts a slot update event to all connected clients.
    Can be called synchronously from Django views, signals, or background tasks.
    """
    global _loop
    payload = json.dumps({
        "type": event_type,
        "data": slot_data
    })

    # 1. Publish to Redis if possible (for cross-worker broadcasting)
    try:
        from django.conf import settings
        redis_url = getattr(settings, "REDIS_URL", None)
        if redis_url:
            import redis
            r = redis.from_url(redis_url)
            r.publish("risala_slot_events", payload)
            # If Redis publish succeeded, local listener in each worker will broadcast
            return
    except Exception:
        pass

    # 2. Fallback: direct in-memory broadcast
    if not _active_connections or _loop is None or _loop.is_closed():
        return

    try:
        asyncio.run_coroutine_threadsafe(_broadcast_locally(payload), _loop)
    except Exception as e:
        logger.debug("Could not schedule in-memory websocket broadcast: %s", e)
