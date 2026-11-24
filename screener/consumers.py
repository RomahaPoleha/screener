import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class ImpulseConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        try:
            await self.channel_layer.group_add("impulses", self.channel_name)
        except Exception as e:
            logger.warning(f"Impulse WS group_add error (continuing): {e}")
        try:
            await self.accept()
            logger.info("Impulse WS client connected")
        except Exception as e:
            logger.error(f"Impulse WS accept error: {e}")

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard("impulses", self.channel_name)
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