import asyncio

from twilio.rest import Client as TwilioClient

from homunculus.channels.base import Channel
from homunculus.channels.models import OutboundMessage
from homunculus.types import ChannelId
from homunculus.utils.config import TwilioConfig
from homunculus.utils.logging import get_logger

log = get_logger()


class TwilioSmsChannel(Channel):
    channel_id: ChannelId = ChannelId("sms")

    def __init__(self, config: TwilioConfig) -> None:
        self._config = config
        self._client = TwilioClient(config.account_sid, config.auth_token)

    async def send(self, message: OutboundMessage) -> None:
        log.info("sending_sms", recipient=message.recipient_phone)
        await asyncio.to_thread(
            self._client.messages.create,
            body=message.body,
            from_=self._config.phone,
            to=message.recipient_phone,
        )
