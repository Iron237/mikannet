"""P3 验收:连接 /ws/progress 听 8 秒,确认 tracker 在广播。"""
import asyncio
import json

import websockets


async def main():
    uri = "ws://127.0.0.1:8008/ws/progress"
    async with websockets.connect(uri) as ws:
        print("WS 已连接")
        try:
            for _ in range(3):
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=12))
                tasks = msg.get("tasks", [])
                print(f"收到广播: type={msg['type']} tasks={len(tasks)}")
                for t in tasks[:3]:
                    print(f"  #{t['id']} {t['status']} {t['progress']:.1%} {t['title'][:40]}")
        except asyncio.TimeoutError:
            print("12 秒内无广播(可能无活动任务)")


asyncio.run(main())
