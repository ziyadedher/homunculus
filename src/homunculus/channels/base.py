from abc import ABC, abstractmethod

from homunculus.types import ChannelId, Contact


class Channel(ABC):
    channel_id: ChannelId

    @abstractmethod
    async def deliver(self, contact: Contact, body: str) -> None: ...
