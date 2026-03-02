import httpx

from homunculus.channels.base import Channel
from homunculus.channels.models import OutboundMessage
from homunculus.types import ChannelId
from homunculus.utils.config import TelegramConfig
from homunculus.utils.logging import get_logger

log = get_logger()

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramChannel(Channel):
    channel_id: ChannelId = ChannelId.TELEGRAM

    def __init__(self, config: TelegramConfig, http_client: httpx.AsyncClient) -> None:
        self._config = config
        self._http_client = http_client

    async def send(self, message: OutboundMessage) -> None:
        log.info("sending_telegram", recipient=message.recipient_id)
        url = f"{TELEGRAM_API_BASE}/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": message.recipient_id,
            "text": message.body,
        }
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_send_failed", status=resp.status_code, body=resp.text)

    async def send_with_inline_keyboard(
        self, chat_id: str, text: str, buttons: list[list[dict[str, str]]]
    ) -> str | None:
        """Send a message with an inline keyboard. Returns the message_id or None on failure."""
        url = f"{TELEGRAM_API_BASE}/bot{self._config.bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": {"inline_keyboard": buttons},
        }
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_send_keyboard_failed", status=resp.status_code, body=resp.text)
            return None
        data = resp.json()
        result = data.get("result", {})
        msg_id = result.get("message_id")
        return str(msg_id) if msg_id is not None else None

    async def answer_callback_query(self, callback_query_id: str, text: str) -> None:
        """Acknowledge an inline keyboard button press."""
        url = f"{TELEGRAM_API_BASE}/bot{self._config.bot_token}/answerCallbackQuery"
        payload = {
            "callback_query_id": callback_query_id,
            "text": text,
        }
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_answer_callback_failed", status=resp.status_code, body=resp.text)

    async def edit_message_text(self, chat_id: str, message_id: str, text: str) -> None:
        """Edit the text of an existing message."""
        url = f"{TELEGRAM_API_BASE}/bot{self._config.bot_token}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        resp = await self._http_client.post(url, json=payload)
        if resp.status_code != 200:
            log.error("telegram_edit_message_failed", status=resp.status_code, body=resp.text)
