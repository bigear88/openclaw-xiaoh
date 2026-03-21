#!/usr/bin/env python3
"""
系統監控 Daemon
每 30 秒檢查 CPU/RAM，超過閾值發 Telegram 警報。
5 分鐘 cooldown 避免重複警報。
"""

import asyncio
import os
import time
from datetime import datetime

import httpx
import psutil

# 設定
CHECK_INTERVAL = 30          # 秒
RAM_THRESHOLD = 80           # %
CPU_THRESHOLD = 90           # %
ALERT_COOLDOWN = 300         # 5 分鐘
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID")

last_alert_time: float = 0


async def send_telegram_alert(message: str):
    """發送 Telegram 警報"""
    global last_alert_time
    now = time.time()

    if now - last_alert_time < ALERT_COOLDOWN:
        return  # cooldown 中，不重複發送

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10)
        last_alert_time = now
    except Exception as e:
        print(f"[Monitor] Telegram alert failed: {e}")


def get_system_status() -> dict:
    """取得系統狀態"""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": psutil.cpu_percent(interval=1),
        "ram_percent": mem.percent,
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "ram_available_gb": round(mem.available / (1024**3), 2),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / (1024**3), 2),
        "timestamp": datetime.now().isoformat(),
    }


async def check_and_alert():
    """檢查系統狀態並在超過閾值時發送警報"""
    status = get_system_status()

    alerts = []
    if status["ram_percent"] > RAM_THRESHOLD:
        alerts.append(
            f"🔴 RAM: {status['ram_percent']:.1f}% "
            f"({status['ram_used_gb']}/{status['ram_total_gb']} GB)"
        )
    if status["cpu_percent"] > CPU_THRESHOLD:
        alerts.append(f"🔴 CPU: {status['cpu_percent']:.1f}%")

    if alerts:
        message = (
            "<b>⚠️ 小虹系統警報</b>\n\n"
            + "\n".join(alerts)
            + f"\n\n可用記憶體：{status['ram_available_gb']} GB"
            + f"\n磁碟剩餘：{status['disk_free_gb']} GB"
            + f"\n時間：{status['timestamp']}"
        )
        await send_telegram_alert(message)
        print(f"[Monitor] ALERT sent: RAM={status['ram_percent']}% CPU={status['cpu_percent']}%")
    else:
        print(
            f"[Monitor] OK: RAM={status['ram_percent']:.1f}% "
            f"CPU={status['cpu_percent']:.1f}% "
            f"@ {status['timestamp']}"
        )


async def main():
    print("[Monitor] 系統監控 daemon 啟動")
    print(f"[Monitor] 檢查間隔: {CHECK_INTERVAL}s, RAM閾值: {RAM_THRESHOLD}%, CPU閾值: {CPU_THRESHOLD}%")

    while True:
        try:
            await check_and_alert()
        except Exception as e:
            print(f"[Monitor] Error: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
