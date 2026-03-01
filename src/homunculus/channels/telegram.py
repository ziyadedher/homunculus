import aiohttp

from homunculus.channels.base import Channel
from homunculus.channels.models import OutboundMessage
from homunculus.types import ChannelId
from homunculus.utils.config import TelegramConfig
from homunculus.utils.logging import get_logger

log = get_logger()

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramChannel(Channel):
    channel_id: ChannelId = ChannelId("telegram")

    def __init__(self, config: TelegramConfig, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session

    async def send(self, message: OutboundMessage) -> None:
        log.info("sending_telegram", recipient=message.recipient_id)
        url = f"{TELEGRAM_API_BASE}/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": message.recipient_id,
            "text": message.body,
        }
        async with self._session.post(url, json=payload) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error("telegram_send_failed", status=resp.status, body=body)
