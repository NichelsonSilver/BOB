"""Frontend WebSocket broadcast hub.

One endpoint — `/api/ws` — that the dashboard subscribes to. Every connected
client receives the same fan-out. Push-only, JSON frames `{event, data}`;
clients filter on `event`.

Eventos previstos (los productores llegan en fases posteriores):
  "signal.new"      — nueva señal emitida (live/analyst.py, Fase 5)
  "signal.update"   — KPI Seguridad recalculado in-live para el trade abierto
  "market.tick"     — precio/candle update del símbolo activo (Fase 1/6)
  "paper.outcome"   — outcome de una señal en paper tracking (Fase 5)
  "conn.status"     — estado de las conexiones a fuentes de datos
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

router = APIRouter(tags=["ws"])


class BroadcastHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)
        logger.info("ws: client connected ({} total)", len(self._clients))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)
        logger.info("ws: client disconnected ({} remain)", len(self._clients))

    async def stop(self) -> None:
        async with self._lock:
            for ws in list(self._clients):
                try:
                    await ws.close()
                except Exception:  # pragma: no cover
                    pass
            self._clients.clear()

    async def publish(self, event_type: str, payload: Any) -> None:
        """Fan out a single event to every connected client.

        Dead sockets are removed silently — the caller doesn't need to care
        about connection state.
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


broadcast_hub = BroadcastHub()


@router.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await broadcast_hub.connect(websocket)
    try:
        while True:
            # No client → server frames yet; this keeps the socket alive
            # and surfaces disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await broadcast_hub.disconnect(websocket)
