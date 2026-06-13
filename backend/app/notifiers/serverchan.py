"""Server酱(微信通道)。credentials: {"send_key": "SCT..."}"""
import httpx

from app.notifiers.base import EVENT_LABELS, Notification, Notifier, register


@register
class ServerChanNotifier(Notifier):
    channel = "serverchan"

    def send(self, n: Notification) -> None:
        key = self.credentials.get("send_key", "")
        if not key:
            raise ValueError("Server酱需要 send_key")
        with httpx.Client(timeout=30, trust_env=False) as c:
            r = c.post(f"https://sctapi.ftqq.com/{key}.send", data={
                "title": f"[{EVENT_LABELS.get(n.event, n.event)}] {n.title}"[:32],
                "desp": f"**{n.title}**\n\n{n.message}",
            })
            r.raise_for_status()
