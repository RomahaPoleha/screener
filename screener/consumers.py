import json
import logging
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class ImpulseConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Сначала принимаем соединение, потом пытаемся добавить в группу
        await self.accept()
        logger.info("Impulse WS client connected")
        try:
            await asyncio.wait_for(
                self.channel_layer.group_add("impulses", self.channel_name),
                timeout=5.0
            )
        except Exception as e:
            logger.warning(f"Impulse WS group_add failed (continuing without group): {e}")

    async def disconnect(self, close_code):
        try:
            await asyncio.wait_for(
                self.channel_layer.group_discard("impulses", self.channel_name),
                timeout=5.0
            )
        except Exception:
            pass
        logger.info(f"Impulse WS client disconnected: {close_code}")

    async def receive(self, text_data=None, bytes_data=None):
        pass

    async def impulse_message(self, event):
        try:
            await self.send(text_data=json.dumps(event["data"]))
        except Exception as e:
            logger.error(f"Impulse WS send error: {e}")