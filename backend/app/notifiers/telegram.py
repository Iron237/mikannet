"""Telegram Bot 通道:有封面发 sendPhoto,无封面发 sendMessage。走代理(可关)。"""
import httpx

from app.config import settings
from app.notifiers.base import EVENT_LABELS, Notification, Notifier, register


@register
class TelegramNotifier(Notifier):
    channel = "telegram"
    # credentials: {"bot_token": "...", "chat_id": "..."}

    def _client(self) -> httpx.Client:
        proxy = settings.proxy_url if self.use_proxy else None
        return httpx.Client(proxy=proxy, timeout=30, trust_env=False)

    def send(self, n: Notification) -> None:
        token = self.credentials.get("bot_token", "")
        chat_id = self.credentials.get("chat_id", "")
        if not token or not chat_id:
            raise ValueError("Telegram 需要 bot_token 和 chat_id")
        api = f"https://api.telegram.org/bot{token}"
        caption = f"<b>{EVENT_LABELS.get(n.event, n.event)}</b>\n{n.title}\n{n.message}"
        with self._client() as c:
            if n.poster_path:
                with open(n.poster_path, "rb") as f:
                    r = c.post(f"{api}/sendPhoto",
                               data={"chat_id": chat_id, "caption": caption,
                                     "parse_mode": "HTML"},
                               files={"photo": f})
            else:
                r = c.post(f"{api}/sendMessage",
                           data={"chat_id": chat_id, "text": caption, "parse_mode": "HTML"})
            r.raise_for_status()
