#!/usr/bin/env python3
"""
Telegram 頻道整理 Agent

功能：
1. 用 Telethon（User Account API）讀取所有訂閱頻道的未讀訊息
2. 頻道主的訊息完整列出，其他人的訊息用摘要顯示
3. 全部標記為已讀
4. 匯出摘要至 Notion「📊 telegram資訊中心」
5. t.me/CruelsHistoryofFinancial 的 PDF 自動下載到 Google Drive epaper 資料夾
6. 透過 Telegram Bot 發送整理結果 + Google Drive 連結

排程：每日 08:00 / 20:00

需要設定的環境變數：
- TELEGRAM_API_ID: Telegram API ID (從 my.telegram.org 取得)
- TELEGRAM_API_HASH: Telegram API Hash
- TELEGRAM_SESSION_NAME: Telethon session 名稱（預設 xiaohong_user）
- TELEGRAM_BOT_TOKEN: Bot token（用來發送摘要訊息）
- TELEGRAM_CHAT_ID: 你的 chat ID（接收摘要的對象）
- NOTION_TOKEN: Notion Integration Token
- GOOGLE_DRIVE_EPAPER_FOLDER_ID: Google Drive 中 epaper 資料夾 ID
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

# ============================================================
# 設定
# ============================================================

TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION = os.environ.get("TELEGRAM_SESSION_NAME", "xiaohong_user")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
GDRIVE_EPAPER_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_EPAPER_FOLDER_ID", "")

# Notion 資料庫
NOTION_DB_ID = "32a17e8c-8578-81fa-9a88-000b07df42d3"  # collection:// ID

# 特殊頻道處理
CRUELS_CHANNEL = "CruelsHistoryofFinancial"  # PDF 電子報頻道

# PDF 下載路徑
PDF_DOWNLOAD_DIR = os.environ.get("PDF_DOWNLOAD_DIR", "/tmp/xiaohong-epaper")

# 時區
TZ_TAIPEI = timezone(timedelta(hours=8))


class TelegramChannelAgent:
    """Telegram 頻道未讀整理 Agent"""

    def __init__(self):
        self.client = None  # Telethon client
        self._summaries: list[dict] = []
        self._pdf_downloads: list[dict] = []

    # ============================================================
    # 主要流程
    # ============================================================

    async def process(self, message: str = "") -> str:
        """MCP 工具入口"""
        if "摘要" in message or "頻道" in message or "整理" in message or not message:
            return await self.run_full_digest()
        return f"Telegram 頻道 Agent：請說「整理頻道」或「頻道摘要」觸發。"

    async def run_full_digest(self) -> str:
        """
        完整流程：
        1. 連接 Telethon
        2. 讀取所有頻道未讀
        3. 整理摘要（頻道主完整列出，其他人摘要）
        4. CruelsHistoryofFinancial PDF → Google Drive
        5. 全部標記已讀
        6. 匯出 Notion
        7. 發送 Telegram 摘要
        """
        from telethon import TelegramClient
        from telethon.tl.functions.messages import ReadHistoryRequest
        from telethon.tl.functions.channels import ReadHistoryRequest as ChannelReadHistoryRequest

        results = []
        self._summaries = []
        self._pdf_downloads = []

        # 1. 連接 Telethon（User Account）
        self.client = TelegramClient(TELEGRAM_SESSION, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await self.client.start()
        me = await self.client.get_me()
        results.append(f"✅ 已連線：{me.first_name} (@{me.username})")

        # 2. 取得所有有未讀訊息的對話
        dialogs = await self.client.get_dialogs()
        unread_channels = [
            d for d in dialogs
            if d.is_channel and d.unread_count > 0
        ]
        results.append(f"📊 共 {len(unread_channels)} 個頻道有未讀訊息")

        # 3. 逐一處理每個頻道
        for dialog in unread_channels:
            try:
                summary = await self._process_channel(dialog)
                if summary:
                    self._summaries.append(summary)
            except Exception as e:
                results.append(f"⚠️ {dialog.name}: {type(e).__name__}: {e}")

        # 4. 全部標記已讀
        for dialog in unread_channels:
            try:
                await self.client.send_read_acknowledge(dialog.entity)
            except Exception:
                pass  # 部分頻道可能無法標記
        results.append(f"✅ 已標記 {len(unread_channels)} 個頻道為已讀")

        # 5. 匯出 Notion
        notion_url = await self._export_to_notion()
        if notion_url:
            results.append(f"📝 Notion 頁面：{notion_url}")

        # 6. 發送 Telegram 摘要
        await self._send_telegram_digest()
        results.append("📨 Telegram 摘要已發送")

        # 7. 處理 PDF 下載結果
        if self._pdf_downloads:
            pdf_msg = await self._send_pdf_notification()
            results.append(pdf_msg)

        await self.client.disconnect()

        return "\n".join(results)

    # ============================================================
    # 頻道處理
    # ============================================================

    async def _process_channel(self, dialog) -> Optional[dict]:
        """處理單一頻道的未讀訊息"""
        channel_name = dialog.name
        channel_username = getattr(dialog.entity, 'username', '') or ''
        unread_count = dialog.unread_count
        is_cruels = (channel_username == CRUELS_CHANNEL)

        # 取得未讀訊息（最多 100 條）
        messages = await self.client.get_messages(
            dialog.entity,
            limit=min(unread_count, 100),
        )

        if not messages:
            return None

        # 取得頻道擁有者/管理員
        admins = set()
        try:
            async for admin in self.client.iter_participants(
                dialog.entity,
                filter=None,  # 取得所有
            ):
                if getattr(admin, 'participant', None):
                    from telethon.tl.types import ChannelParticipantCreator, ChannelParticipantAdmin
                    p = admin.participant
                    if isinstance(p, (ChannelParticipantCreator, ChannelParticipantAdmin)):
                        admins.add(admin.id)
        except Exception:
            # 部分頻道無法取得管理員列表，把所有訊息都當重要
            admins = None

        # 分類訊息
        owner_messages = []
        other_messages = []
        pdf_files = []

        for msg in reversed(messages):  # 舊到新
            if not msg.text and not msg.media:
                continue

            sender_id = msg.sender_id
            msg_text = msg.text or ""
            msg_time = msg.date.astimezone(TZ_TAIPEI).strftime("%H:%M")

            # 檢查 PDF 附件
            if msg.media and is_cruels:
                pdf_info = await self._check_and_download_pdf(msg, channel_name)
                if pdf_info:
                    pdf_files.append(pdf_info)

            # 分類：頻道主/管理員 vs 其他
            msg_entry = {
                "time": msg_time,
                "text": msg_text[:500],  # 限制長度
                "has_media": bool(msg.media),
                "sender_id": sender_id,
            }

            if admins is None or sender_id in admins or msg.post:
                # 頻道貼文（msg.post=True）都是頻道主發的
                owner_messages.append(msg_entry)
            else:
                other_messages.append(msg_entry)

        # 其他人訊息的摘要
        others_summary = ""
        if other_messages:
            # 簡單摘要：取前 3 條 + 總數
            preview = other_messages[:3]
            preview_texts = [m["text"][:80] for m in preview if m["text"]]
            others_summary = (
                f"其他 {len(other_messages)} 則訊息摘要：\n"
                + "\n".join(f"  • {t}..." for t in preview_texts)
            )
            if len(other_messages) > 3:
                others_summary += f"\n  ...及其他 {len(other_messages) - 3} 則"

        return {
            "channel_name": channel_name,
            "channel_username": channel_username,
            "unread_count": unread_count,
            "owner_messages": owner_messages,
            "others_summary": others_summary,
            "other_count": len(other_messages),
            "pdf_files": pdf_files,
            "is_cruels": is_cruels,
        }

    # ============================================================
    # PDF 下載（CruelsHistoryofFinancial）
    # ============================================================

    async def _check_and_download_pdf(self, msg, channel_name: str) -> Optional[dict]:
        """檢查訊息是否有 PDF，若有則下載"""
        from telethon.tl.types import MessageMediaDocument

        if not isinstance(msg.media, MessageMediaDocument):
            return None

        doc = msg.media.document
        if not doc:
            return None

        # 檢查是否為 PDF
        is_pdf = False
        filename = "unknown.pdf"
        for attr in doc.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                filename = attr.file_name
                if filename.lower().endswith('.pdf'):
                    is_pdf = True
                break

        if not is_pdf:
            # 也檢查 MIME type
            if doc.mime_type == 'application/pdf':
                is_pdf = True
                if not filename.endswith('.pdf'):
                    date_str = msg.date.strftime("%Y%m%d")
                    filename = f"{channel_name}_{date_str}.pdf"

        if not is_pdf:
            return None

        # 下載 PDF
        Path(PDF_DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
        local_path = os.path.join(PDF_DOWNLOAD_DIR, filename)

        try:
            await self.client.download_media(msg, file=local_path)

            file_size = os.path.getsize(local_path)
            pdf_info = {
                "filename": filename,
                "local_path": local_path,
                "size_kb": round(file_size / 1024, 1),
                "date": msg.date.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M"),
                "text_preview": (msg.text or "")[:200],
            }

            # 上傳到 Google Drive
            gdrive_link = await self._upload_to_gdrive(local_path, filename)
            if gdrive_link:
                pdf_info["gdrive_link"] = gdrive_link

            self._pdf_downloads.append(pdf_info)
            return pdf_info

        except Exception as e:
            return {"filename": filename, "error": str(e)}

    async def _upload_to_gdrive(self, local_path: str, filename: str) -> Optional[str]:
        """
        上傳 PDF 到 Google Drive epaper 資料夾。
        使用 Google Drive API v3。
        """
        if not GDRIVE_EPAPER_FOLDER_ID:
            return None

        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            # 使用已存在的 credentials
            creds_path = os.environ.get(
                "GOOGLE_CREDENTIALS_PATH",
                os.path.expanduser("~/.config/google/credentials.json")
            )

            if not os.path.exists(creds_path):
                return None

            creds = Credentials.from_authorized_user_file(creds_path)
            service = build('drive', 'v3', credentials=creds)

            file_metadata = {
                'name': filename,
                'parents': [GDRIVE_EPAPER_FOLDER_ID],
            }
            media = MediaFileUpload(local_path, mimetype='application/pdf')

            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink'
            ).execute()

            return file.get('webViewLink', f"https://drive.google.com/file/d/{file['id']}")

        except Exception as e:
            print(f"[TelegramChannel] Google Drive upload error: {e}")
            return None

    # ============================================================
    # Notion 匯出
    # ============================================================

    async def _export_to_notion(self) -> Optional[str]:
        """匯出摘要到 Notion telegram資訊中心"""
        if not NOTION_TOKEN or not self._summaries:
            return None

        now = datetime.now(TZ_TAIPEI)
        period = "晨報" if now.hour < 14 else "晚報"
        title = f"Telegram 頻道{period} — {now.strftime('%Y-%m-%d %H:%M')}"

        # 組裝頁面內容
        content_blocks = []
        total_unread = sum(s["unread_count"] for s in self._summaries)
        content_blocks.append(
            f"## 📊 總覽\n"
            f"- 頻道數：{len(self._summaries)}\n"
            f"- 總未讀：{total_unread}\n"
            f"- 時間：{now.strftime('%Y-%m-%d %H:%M')}\n"
        )

        for s in self._summaries:
            icon = "📄" if s.get("is_cruels") else "📢"
            channel_link = f"https://t.me/{s['channel_username']}" if s['channel_username'] else ""

            content_blocks.append(f"---\n## {icon} {s['channel_name']}")
            if channel_link:
                content_blocks.append(f"[{channel_link}]({channel_link})")
            content_blocks.append(f"未讀：{s['unread_count']} 則\n")

            # 頻道主訊息
            if s["owner_messages"]:
                content_blocks.append("### 📌 頻道主訊息")
                for msg in s["owner_messages"]:
                    text = msg["text"].replace("\n", "\n> ")
                    content_blocks.append(f"**[{msg['time']}]**\n> {text}\n")

            # 其他人摘要
            if s["others_summary"]:
                content_blocks.append(f"### 💬 其他討論\n{s['others_summary']}\n")

            # PDF 下載
            if s.get("pdf_files"):
                content_blocks.append("### 📎 PDF 電子報")
                for pdf in s["pdf_files"]:
                    if "error" in pdf:
                        content_blocks.append(f"- ❌ {pdf['filename']}: {pdf['error']}")
                    else:
                        gdrive = pdf.get("gdrive_link", "")
                        link_text = f" | [Google Drive]({gdrive})" if gdrive else ""
                        content_blocks.append(
                            f"- ✅ **{pdf['filename']}** ({pdf['size_kb']} KB){link_text}"
                        )

        full_content = "\n".join(content_blocks)

        # 建立 Notion 頁面
        try:
            notion_url = f"https://api.notion.com/v1/pages"
            headers = {
                "Authorization": f"Bearer {NOTION_TOKEN}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            }

            # 將 markdown 內容轉成 Notion blocks
            blocks = self._markdown_to_notion_blocks(full_content)

            total_unread_count = sum(s["unread_count"] for s in self._summaries)
            total_pdf_count = sum(len(s.get("pdf_files", [])) for s in self._summaries)

            payload = {
                "parent": {"database_id": "32a17e8c85788097b5bcf51c6a80e5c4"},
                "properties": {
                    "標題": {
                        "title": [{"text": {"content": title}}]
                    },
                    "日期": {
                        "date": {"start": now.strftime("%Y-%m-%d")}
                    },
                    "類型": {
                        "select": {"name": "頻道摘要"}
                    },
                    "頻道數": {"number": len(self._summaries)},
                    "未讀數": {"number": total_unread_count},
                    "PDF數": {"number": total_pdf_count},
                },
                "children": blocks[:100],  # Notion API 一次最多 100 blocks
            }

            async with httpx.AsyncClient() as client:
                resp = await client.post(notion_url, json=payload, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("url", "")
                else:
                    print(f"[Notion] Error: {resp.status_code} {resp.text[:200]}")
                    return None

        except Exception as e:
            print(f"[Notion] Export error: {e}")
            return None

    def _markdown_to_notion_blocks(self, content: str) -> list[dict]:
        """簡易 markdown → Notion blocks 轉換"""
        blocks = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            if line.startswith("## "):
                blocks.append({
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {
                        "rich_text": [{"type": "text", "text": {"content": line[3:].strip()}}]
                    }
                })
            elif line.startswith("### "):
                blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": line[4:].strip()}}]
                    }
                })
            elif line.startswith("---"):
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            elif line.startswith("- "):
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:].strip()}}]
                    }
                })
            elif line.startswith("> "):
                blocks.append({
                    "object": "block",
                    "type": "quote",
                    "quote": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:].strip()}}]
                    }
                })
            elif line.strip():
                # 一般段落（合併連續非空行）
                para_lines = [line]
                while i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].startswith(("#", "-", ">", "---")):
                    i += 1
                    para_lines.append(lines[i])
                text = "\n".join(para_lines)
                # 限制 Notion block 字數（最多 2000）
                if len(text) > 2000:
                    text = text[:1997] + "..."
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": text}}]
                    }
                })

            i += 1

        return blocks

    # ============================================================
    # Telegram Bot 發送
    # ============================================================

    async def _send_telegram_digest(self):
        """透過 Bot API 發送頻道摘要"""
        if not BOT_TOKEN or not CHAT_ID:
            return

        now = datetime.now(TZ_TAIPEI)
        period = "晨報" if now.hour < 14 else "晚報"

        # 組裝訊息
        parts = [f"📊 <b>Telegram 頻道{period}</b>"]
        parts.append(f"🕐 {now.strftime('%Y-%m-%d %H:%M')}")
        parts.append(f"📡 {len(self._summaries)} 個頻道\n")

        for s in self._summaries:
            icon = "📄" if s.get("is_cruels") else "📢"
            parts.append(f"{icon} <b>{s['channel_name']}</b> ({s['unread_count']}則)")

            # 頻道主訊息（前 3 條）
            for msg in s["owner_messages"][:3]:
                text = msg["text"][:150].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f"  [{msg['time']}] {text}")

            if len(s["owner_messages"]) > 3:
                parts.append(f"  ...及其他 {len(s['owner_messages']) - 3} 則")

            # 其他人
            if s["other_count"] > 0:
                parts.append(f"  💬 其他 {s['other_count']} 則討論")

            parts.append("")

        full_message = "\n".join(parts)

        # Telegram 有 4096 字元限制，分段發送
        await self._send_bot_message(full_message)

    async def _send_pdf_notification(self) -> str:
        """PDF 下載完成後另發通知"""
        if not self._pdf_downloads or not BOT_TOKEN or not CHAT_ID:
            return "無 PDF 下載"

        parts = ["📎 <b>電子報 PDF 已下載</b>\n"]
        for pdf in self._pdf_downloads:
            if "error" in pdf:
                parts.append(f"❌ {pdf['filename']}: {pdf['error']}")
            else:
                parts.append(f"✅ <b>{pdf['filename']}</b>")
                parts.append(f"   📏 {pdf['size_kb']} KB | {pdf['date']}")
                if pdf.get("gdrive_link"):
                    parts.append(f"   📁 <a href=\"{pdf['gdrive_link']}\">Google Drive 連結</a>")
                if pdf.get("text_preview"):
                    preview = pdf["text_preview"][:200].replace("<", "&lt;").replace(">", "&gt;")
                    parts.append(f"   📝 {preview}")
                parts.append("")

        msg = "\n".join(parts)
        await self._send_bot_message(msg)
        return f"📎 已下載 {len(self._pdf_downloads)} 個 PDF 並通知"

    async def _send_bot_message(self, text: str):
        """透過 Bot API 發送訊息（自動分段）"""
        MAX_LEN = 4000

        chunks = []
        while len(text) > MAX_LEN:
            # 在換行處切割
            cut = text.rfind("\n", 0, MAX_LEN)
            if cut == -1:
                cut = MAX_LEN
            chunks.append(text[:cut])
            text = text[cut:].lstrip("\n")
        if text.strip():
            chunks.append(text)

        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient() as client:
            for chunk in chunks:
                payload = {
                    "chat_id": CHAT_ID,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                try:
                    await client.post(url, json=payload, timeout=15)
                    await asyncio.sleep(0.5)  # 避免 rate limit
                except Exception as e:
                    print(f"[TelegramBot] Send error: {e}")


# ============================================================
# 獨立執行入口
# ============================================================

async def main():
    """可直接執行：python -m agents.telegram_channel.agent"""
    agent = TelegramChannelAgent()
    result = await agent.run_full_digest()
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
