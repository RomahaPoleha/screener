import json
from channels.generic.websocket import AsyncWebsocketConsumer


class ImpulseConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("impulses", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("impulses", self.channel_name)

    async def impulse_message(self, event):
        await self.send(text_data=json.dumps(event["data"]))
