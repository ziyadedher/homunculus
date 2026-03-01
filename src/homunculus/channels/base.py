from abc import ABC, abstractmethod

from homunculus.channels.models import OutboundMessage
from homunculus.types import ChannelId


class Channel(ABC):
    channel_id: ChannelId

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None: ...
