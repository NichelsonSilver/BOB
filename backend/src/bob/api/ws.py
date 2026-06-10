"""Frontend WebSocket broadcast hub.

One endpoint — `/api/ws` — that the dashboard subscribes to. Every
connected client receives the same fan-out: periodic bot status
snapshots plus any ad-hoc events published via `broadcast_hub.publish`.

Kept intentionally minimal — push-only, JSON frames, no per-client
filtering yet. Clients filter on `event_type` in the payload.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


class BroadcastHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._poller_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._manager_ref: Any = None
        self._poll_interval = 2.0

    def attach_manager(self, manager: Any) -> None:
        """Attach the BotManager so the poller can publish status snapshots."""
        self._manager_ref = manager

    async def start(self) -> None:
        if self._poller_task is None or self._poller_task.done():
            self._poller_task = asyncio.create_task(
                self._poll_loop(), name="ws-status-poller"
            )

    async def stop(self) -> None:
        if self._poller_task and not self._poller_task.done():
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
        async with self._lock:
            for ws in list(self._clients):
                try:
                    await ws.close()
                except Exception:  # pragma: no cover
                    pass
            self._clients.clear()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.info("ws: client connected (%d total)", len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        logger.info("ws: client disconnected (%d remain)", len(self._clients))

    async def publish(self, event_type: str, payload: Any) -> None:
        """Fan out a single event to every connected client.

        Dead sockets are removed silently — the caller doesn't need to
        care about connection state.
        """
        frame = {"event": event_type, "data": payload}
        dead: list[WebSocket] = []
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_json(frame)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)

    async def _poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                if self._manager_ref is None:
                    continue
                async with self._lock:
                    has_clients = bool(self._clients)
                if not has_clients:
                    continue
                try:
                    snapshot = self._manager_ref.list_all()
                except Exception as e:  # pragma: no cover
                    logger.warning("ws: snapshot failed: %s", e)
                    continue
                await self.publish("bots.snapshot", snapshot)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover
                logger.exception("ws: poller crashed: %s", e)


broadcast_hub = BroadcastHub()


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await broadcast_hub.connect(websocket)
    try:
        while True:
            # We don't currently process client → server frames; this
            # keeps the socket alive and surfaces disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcast_hub.disconnect(websocket)
