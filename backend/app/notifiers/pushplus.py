"""PushPlus(微信通道)。credentials: {"token": "..."}"""
import httpx

from app.notifiers.base import EVENT_LABELS, Notification, Notifier, register


@register
class PushPlusNotifier(Notifier):
    channel = "pushplus"

    def send(self, n: Notification) -> None:
        token = self.credentials.get("token", "")
        if not token:
            raise ValueError("PushPlus 需要 token")
        with httpx.Client(timeout=30, trust_env=False) as c:
            r = c.post("https://www.pushplus.plus/send", json={
                "token": token,
                "title": f"[{EVENT_LABELS.get(n.event, n.event)}] {n.title}",
                "content": f"{n.title}<br/>{n.message}",
                "template": "html",
            })
            r.raise_for_status()
