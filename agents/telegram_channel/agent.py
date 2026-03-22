#!/usr/bin/env python3
"""
Telegram 頻道整理 Agent v1.1

功能：
1. 用 Telethon（User Account API）讀取所有訂閱頻道的未讀訊息
2. 頻道主的訊息完整列出，其他人的訊息用摘要顯示
3. 全部標記為已讀
4. CruelsHistoryofFinancial PDF 下載至 Google Drive（≤90天）
5. 清理超過 3 個月的舊檔案（本地 + Google Drive）
6. 匯出摘要至 Notion「📊 telegram資訊中心」
7. 清理報告匯出至 Notion
8. 透過 Telegram Bot 發送整理結果 + PDF 通知

排程：每日 08:00 / 20:00 (UTC+8)
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
GOOGLE_TOKEN_PATH = os.environ.get("GOOGLE_TOKEN_PATH", os.path.expanduser("~/xiaohong/token.json"))
GOOGLE_CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS_PATH", os.path.expanduser("~/xiaohong/credentials.json"))

# Notion 資料庫 — 📊 telegram資訊中心
NOTION_TELEGRAM_DB_ID = os.environ.get("NOTION_TELEGRAM_DB_ID", "")

# 特殊頻道處理
CRUELS_CHANNEL = "CruelsHistoryofFinancial"

# PDF 下載路徑
PDF_DOWNLOAD_DIR = os.environ.get("PDF_DOWNLOAD_DIR", "/tmp/xiaohong-epaper")

# 檔案清理：保留天數
FILE_RETENTION_DAYS = 90

# 時區
TZ_TAIPEI = timezone(timedelta(hours=8))


class TelegramChannelAgent:
    """Telegram 頻道未讀整理 Agent"""

    def __init__(self):
        self.client = None  # Telethon client
        self._summaries: list[dict] = []
        self._pdf_downloads: list[dict] = []
        self._skipped_pdfs: list[dict] = []
        self._cleanup_local: list[dict] = []
        self._cleanup_gdrive: list[dict] = []

    # ============================================================
    # MCP 入口
    # ============================================================

    async def process(self, message: str = "") -> str:
        """MCP 工具入口"""
        if "摘要" in message or "頻道" in message or "整理" in message or not message:
            return await self.run_full_digest()
        return "Telegram 頻道 Agent：請說「整理頻道」或「頻道摘要」觸發。"

    # ============================================================
    # 完整流程（8 步驟）
    # ============================================================

    async def run_full_digest(self) -> str:
        """
        1. 連接 Telethon
        2. 讀取所有頻道未讀
        3. 逐一處理頻道（分類訊息、偵測 PDF）
        4. 全部標記已讀
        5. 清理超過 3 個月的舊檔案
        6. 匯出至 Notion
        7. Telegram Bot 發送摘要
        8. PDF 下載通知
        """
        from telethon import TelegramClient

        results = []
        self._summaries = []
        self._pdf_downloads = []
        self._skipped_pdfs = []
        self._cleanup_local = []
        self._cleanup_gdrive = []

        # ── 1. 連接 Telethon ──
        session_path = os.path.join(
            os.environ.get("TELETHON_SESSION_DIR", os.path.expanduser("~/openclaw-xiaoh/data")),
            TELEGRAM_SESSION,
        )
        self.client = TelegramClient(session_path, TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await self.client.start()
        me = await self.client.get_me()
        results.append(f"1. 已連線：{me.first_name} (@{me.username})")

        # ── 2. 讀取所有頻道未讀 ──
        dialogs = await self.client.get_dialogs()
        unread_channels = [
            d for d in dialogs
            if d.is_channel and d.unread_count > 0
        ]
        results.append(f"2. 共 {len(unread_channels)} 個頻道有未讀訊息")

        # ── 3. 逐一處理 ──
        for dialog in unread_channels:
            try:
                summary = await self._process_channel(dialog)
                if summary:
                    self._summaries.append(summary)
            except Exception as e:
                results.append(f"   {dialog.name}: {type(e).__name__}: {e}")

        # ── 4. 全部標記已讀 ──
        marked = 0
        for dialog in unread_channels:
            try:
                await self.client.send_read_acknowledge(dialog.entity)
                marked += 1
            except Exception:
                pass
        results.append(f"4. 已標記 {marked}/{len(unread_channels)} 個頻道為已讀")

        # ── 5. 清理舊檔案 ──
        cleanup_msg = await self._cleanup_old_files()
        if cleanup_msg:
            results.append(f"5. {cleanup_msg}")

        # ── 6. 匯出 Notion ──
        notion_url = await self._export_to_notion()
        if notion_url:
            results.append(f"6. Notion 頁面：{notion_url}")

        # 清理報告
        if self._has_cleanup_data():
            cleanup_url = await self._export_cleanup_report_to_notion()
            if cleanup_url:
                results.append(f"   清理報告：{cleanup_url}")

        # ── 7. 發送 Telegram 摘要 ──
        await self._send_telegram_digest()
        results.append("7. Telegram 摘要已發送")

        # ── 8. PDF 下載通知 ──
        if self._pdf_downloads:
            pdf_msg = await self._send_pdf_notification()
            results.append(f"8. {pdf_msg}")

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

        messages = await self.client.get_messages(
            dialog.entity,
            limit=min(unread_count, 100),
        )
        if not messages:
            return None

        # 取得管理員
        admins = set()
        try:
            from telethon.tl.types import (
                ChannelParticipantCreator,
                ChannelParticipantAdmin,
            )
            async for user in self.client.iter_participants(dialog.entity):
                p = getattr(user, 'participant', None)
                if isinstance(p, (ChannelParticipantCreator, ChannelParticipantAdmin)):
                    admins.add(user.id)
        except Exception:
            admins = None  # 無法取得 → 全部視為重要

        owner_messages = []
        other_messages = []
        pdf_files = []
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=FILE_RETENTION_DAYS)

        for msg in reversed(messages):
            if not msg.text and not msg.media:
                continue

            sender_id = msg.sender_id
            msg_text = msg.text or ""
            msg_time = msg.date.astimezone(TZ_TAIPEI).strftime("%H:%M")

            # PDF 偵測（僅 CruelsHistoryofFinancial）
            if msg.media and is_cruels:
                pdf_info = await self._check_and_download_pdf(msg, channel_name, cutoff_date)
                if pdf_info:
                    pdf_files.append(pdf_info)

            msg_entry = {
                "time": msg_time,
                "text": msg_text[:500],
                "has_media": bool(msg.media),
                "sender_id": sender_id,
            }

            if admins is None or sender_id in admins or msg.post:
                owner_messages.append(msg_entry)
            else:
                other_messages.append(msg_entry)

        others_summary = ""
        if other_messages:
            preview = other_messages[:3]
            preview_texts = [m["text"][:80] for m in preview if m["text"]]
            others_summary = (
                f"其他 {len(other_messages)} 則訊息摘要：\n"
                + "\n".join(f"  - {t}..." for t in preview_texts)
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
    # PDF 下載（含日期檢查）
    # ============================================================

    async def _check_and_download_pdf(self, msg, channel_name: str, cutoff_date) -> Optional[dict]:
        """偵測 PDF，≤90天才下載，否則記錄跳過"""
        from telethon.tl.types import MessageMediaDocument

        if not isinstance(msg.media, MessageMediaDocument):
            return None

        doc = msg.media.document
        if not doc:
            return None

        is_pdf = False
        filename = "unknown.pdf"
        for attr in doc.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                filename = attr.file_name
                if filename.lower().endswith('.pdf'):
                    is_pdf = True
                break

        if not is_pdf:
            if doc.mime_type == 'application/pdf':
                is_pdf = True
                if not filename.endswith('.pdf'):
                    date_str = msg.date.strftime("%Y%m%d")
                    filename = f"{channel_name}_{date_str}.pdf"

        if not is_pdf:
            return None

        # 日期檢查：超過 90 天的不下載
        if msg.date < cutoff_date:
            skip_info = {
                "filename": filename,
                "date": msg.date.astimezone(TZ_TAIPEI).strftime("%Y-%m-%d"),
                "reason": f"訊息日期超過 {FILE_RETENTION_DAYS} 天",
            }
            self._skipped_pdfs.append(skip_info)
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

            gdrive_link = await self._upload_to_gdrive(local_path, filename)
            if gdrive_link:
                pdf_info["gdrive_link"] = gdrive_link

            self._pdf_downloads.append(pdf_info)
            return pdf_info

        except Exception as e:
            return {"filename": filename, "error": str(e)}

    # ============================================================
    # Google Drive 上傳
    # ============================================================

    async def _upload_to_gdrive(self, local_path: str, filename: str) -> Optional[str]:
        """上傳 PDF 到 Google Drive epaper 資料夾"""
        if not GDRIVE_EPAPER_FOLDER_ID:
            return None

        try:
            creds = self._get_gdrive_creds()
            if not creds:
                return None

            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload

            service = build('drive', 'v3', credentials=creds)

            file_metadata = {
                'name': filename,
                'parents': [GDRIVE_EPAPER_FOLDER_ID],
            }
            media = MediaFileUpload(local_path, mimetype='application/pdf')

            file = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, webViewLink',
            ).execute()

            return file.get('webViewLink', f"https://drive.google.com/file/d/{file['id']}")

        except Exception as e:
            print(f"[TelegramChannel] Google Drive upload error: {e}")
            return None

    def _get_gdrive_creds(self):
        """取得 Google OAuth credentials，支援自動 refresh"""
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            if not os.path.exists(GOOGLE_TOKEN_PATH):
                return None

            creds = Credentials.from_authorized_user_file(
                GOOGLE_TOKEN_PATH,
                scopes=["https://www.googleapis.com/auth/drive"],
            )

            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # 寫回更新的 token
                with open(GOOGLE_TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())

            return creds

        except Exception as e:
            print(f"[TelegramChannel] Google creds error: {e}")
            return None

    # ============================================================
    # 檔案清理（3 個月保留期）
    # ============================================================

    async def _cleanup_old_files(self) -> str:
        """清理主入口：本地 + Google Drive"""
        cutoff = datetime.now(timezone.utc) - timedelta(days=FILE_RETENTION_DAYS)
        results = []

        local_count = self._cleanup_local_epaper(cutoff)
        if local_count > 0:
            results.append(f"本地刪除 {local_count} 個檔案")

        gdrive_count = await self._cleanup_gdrive_epaper(cutoff)
        if gdrive_count > 0:
            results.append(f"Google Drive 刪除 {gdrive_count} 個檔案")

        if self._skipped_pdfs:
            results.append(f"跳過 {len(self._skipped_pdfs)} 個過期 PDF")

        return "；".join(results) if results else ""

    def _cleanup_local_epaper(self, cutoff) -> int:
        """掃描本地 epaper 資料夾，刪除 mtime > 90 天的檔案"""
        epaper_dir = Path(PDF_DOWNLOAD_DIR)
        if not epaper_dir.exists():
            return 0

        count = 0
        cutoff_ts = cutoff.timestamp()

        for f in epaper_dir.iterdir():
            if not f.is_file():
                continue
            if f.stat().st_mtime < cutoff_ts:
                size_kb = round(f.stat().st_size / 1024, 1)
                mtime_str = datetime.fromtimestamp(f.stat().st_mtime, TZ_TAIPEI).strftime("%Y-%m-%d")
                try:
                    f.unlink()
                    self._cleanup_local.append({
                        "filename": f.name,
                        "size_kb": size_kb,
                        "mtime": mtime_str,
                        "status": "deleted",
                    })
                    count += 1
                except Exception as e:
                    self._cleanup_local.append({
                        "filename": f.name,
                        "size_kb": size_kb,
                        "mtime": mtime_str,
                        "status": f"error: {e}",
                    })
        return count

    async def _cleanup_gdrive_epaper(self, cutoff) -> int:
        """Drive API 查詢 createdTime < cutoff，逐一刪除"""
        if not GDRIVE_EPAPER_FOLDER_ID:
            return 0

        try:
            creds = self._get_gdrive_creds()
            if not creds:
                return 0

            from googleapiclient.discovery import build

            service = build('drive', 'v3', credentials=creds)
            cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")

            count = 0
            page_token = None

            while True:
                query = (
                    f"'{GDRIVE_EPAPER_FOLDER_ID}' in parents "
                    f"and createdTime < '{cutoff_str}' "
                    f"and trashed = false"
                )
                resp = service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name, size, createdTime)",
                    pageSize=100,
                    pageToken=page_token,
                ).execute()

                files = resp.get('files', [])
                for f in files:
                    try:
                        service.files().delete(fileId=f['id']).execute()
                        self._cleanup_gdrive.append({
                            "filename": f['name'],
                            "size_kb": round(int(f.get('size', 0)) / 1024, 1),
                            "created": f.get('createdTime', '')[:10],
                            "status": "deleted",
                        })
                        count += 1
                    except Exception as e:
                        self._cleanup_gdrive.append({
                            "filename": f['name'],
                            "size_kb": round(int(f.get('size', 0)) / 1024, 1),
                            "created": f.get('createdTime', '')[:10],
                            "status": f"error: {e}",
                        })

                page_token = resp.get('nextPageToken')
                if not page_token:
                    break

            return count

        except Exception as e:
            print(f"[TelegramChannel] GDrive cleanup error: {e}")
            return 0

    def _has_cleanup_data(self) -> bool:
        return bool(self._cleanup_local or self._cleanup_gdrive or self._skipped_pdfs)

    # ============================================================
    # Notion 匯出
    # ============================================================

    async def _export_to_notion(self) -> Optional[str]:
        """匯出摘要到 Notion telegram資訊中心"""
        if not NOTION_TOKEN or not NOTION_TELEGRAM_DB_ID or not self._summaries:
            return None

        now = datetime.now(TZ_TAIPEI)
        period = "晨報" if now.hour < 14 else "晚報"
        title = f"Telegram 頻道{period} — {now.strftime('%Y-%m-%d %H:%M')}"

        # 組裝頁面內容
        content_blocks = []
        total_unread = sum(s["unread_count"] for s in self._summaries)
        content_blocks.append(
            f"## 總覽\n"
            f"- 頻道數：{len(self._summaries)}\n"
            f"- 總未讀：{total_unread}\n"
            f"- 時間：{now.strftime('%Y-%m-%d %H:%M')}\n"
        )

        for s in self._summaries:
            icon = "PDF" if s.get("is_cruels") else "頻道"
            channel_link = f"https://t.me/{s['channel_username']}" if s['channel_username'] else ""

            content_blocks.append(f"---\n## [{icon}] {s['channel_name']}")
            if channel_link:
                content_blocks.append(f"[{channel_link}]({channel_link})")
            content_blocks.append(f"未讀：{s['unread_count']} 則\n")

            if s["owner_messages"]:
                content_blocks.append("### 頻道主訊息")
                for msg in s["owner_messages"]:
                    text = msg["text"].replace("\n", "\n> ")
                    content_blocks.append(f"**[{msg['time']}]**\n> {text}\n")

            if s["others_summary"]:
                content_blocks.append(f"### 其他討論\n{s['others_summary']}\n")

            if s.get("pdf_files"):
                content_blocks.append("### PDF 電子報")
                for pdf in s["pdf_files"]:
                    if "error" in pdf:
                        content_blocks.append(f"- {pdf['filename']}: {pdf['error']}")
                    else:
                        gdrive = pdf.get("gdrive_link", "")
                        link_text = f" | [Google Drive]({gdrive})" if gdrive else ""
                        content_blocks.append(
                            f"- **{pdf['filename']}** ({pdf['size_kb']} KB){link_text}"
                        )

        full_content = "\n".join(content_blocks)
        blocks = self._markdown_to_notion_blocks(full_content)

        total_unread_count = sum(s["unread_count"] for s in self._summaries)
        total_pdf_count = sum(len(s.get("pdf_files", [])) for s in self._summaries)

        return await self._create_notion_page(
            title=title,
            page_type="頻道摘要",
            blocks=blocks,
            extra_props={
                "頻道數": {"number": len(self._summaries)},
                "未讀數": {"number": total_unread_count},
                "PDF數": {"number": total_pdf_count},
            },
        )

    async def _export_cleanup_report_to_notion(self) -> Optional[str]:
        """清理報告匯出為 Notion 獨立頁面"""
        if not NOTION_TOKEN or not NOTION_TELEGRAM_DB_ID:
            return None

        now = datetime.now(TZ_TAIPEI)
        title = f"清理報告 — {now.strftime('%Y-%m-%d %H:%M')}"

        content_parts = []

        # 總覽
        local_count = len([x for x in self._cleanup_local if x["status"] == "deleted"])
        gdrive_count = len([x for x in self._cleanup_gdrive if x["status"] == "deleted"])
        local_size = sum(x["size_kb"] for x in self._cleanup_local if x["status"] == "deleted")
        gdrive_size = sum(x["size_kb"] for x in self._cleanup_gdrive if x["status"] == "deleted")

        content_parts.append(
            f"## 清理總覽\n"
            f"- 本地刪除：{local_count} 個檔案（{local_size/1024:.1f} MB）\n"
            f"- Google Drive 刪除：{gdrive_count} 個檔案（{gdrive_size/1024:.1f} MB）\n"
            f"- 跳過下載：{len(self._skipped_pdfs)} 個過期 PDF\n"
            f"- 保留期限：{FILE_RETENTION_DAYS} 天\n"
        )

        if self._cleanup_local:
            content_parts.append("---\n## 本地刪除明細")
            for item in self._cleanup_local:
                status_icon = "OK" if item["status"] == "deleted" else "ERR"
                content_parts.append(
                    f"- [{status_icon}] {item['filename']} ({item['size_kb']} KB, {item['mtime']})"
                )

        if self._cleanup_gdrive:
            content_parts.append("---\n## Google Drive 刪除明細")
            for item in self._cleanup_gdrive:
                status_icon = "OK" if item["status"] == "deleted" else "ERR"
                content_parts.append(
                    f"- [{status_icon}] {item['filename']} ({item['size_kb']} KB, {item['created']})"
                )

        if self._skipped_pdfs:
            content_parts.append("---\n## 跳過下載清單")
            for item in self._skipped_pdfs:
                content_parts.append(
                    f"- {item['filename']}（{item['date']}）— {item['reason']}"
                )

        full_content = "\n".join(content_parts)
        blocks = self._markdown_to_notion_blocks(full_content)
        total_cleaned = local_count + gdrive_count

        return await self._create_notion_page(
            title=title,
            page_type="清理報告",
            blocks=blocks,
            extra_props={
                "PDF數": {"number": total_cleaned},
            },
        )

    async def _create_notion_page(
        self, title: str, page_type: str, blocks: list, extra_props: dict = None,
    ) -> Optional[str]:
        """建立 Notion 頁面到 telegram 資訊中心"""
        now = datetime.now(TZ_TAIPEI)
        headers = {
            "Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

        properties = {
            "標題": {"title": [{"text": {"content": title}}]},
            "日期": {"date": {"start": now.strftime("%Y-%m-%d")}},
            "類型": {"select": {"name": page_type}},
        }
        if extra_props:
            properties.update(extra_props)

        payload = {
            "parent": {"database_id": NOTION_TELEGRAM_DB_ID},
            "properties": properties,
            "children": blocks[:100],
        }

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "https://api.notion.com/v1/pages",
                    json=payload, headers=headers, timeout=30,
                )
                if resp.status_code == 200:
                    return resp.json().get("url", "")
                print(f"[Notion] Error: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            print(f"[Notion] Export error: {e}")
            return None

    def _markdown_to_notion_blocks(self, content: str) -> list[dict]:
        """簡易 markdown -> Notion blocks 轉換"""
        blocks = []
        lines = content.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i]

            if line.startswith("## "):
                blocks.append({
                    "object": "block", "type": "heading_2",
                    "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:].strip()}}]},
                })
            elif line.startswith("### "):
                blocks.append({
                    "object": "block", "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:].strip()}}]},
                })
            elif line.startswith("---"):
                blocks.append({"object": "block", "type": "divider", "divider": {}})
            elif line.startswith("- "):
                blocks.append({
                    "object": "block", "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[2:].strip()}}]},
                })
            elif line.startswith("> "):
                blocks.append({
                    "object": "block", "type": "quote",
                    "quote": {"rich_text": [{"type": "text", "text": {"content": line[2:].strip()}}]},
                })
            elif line.strip():
                para_lines = [line]
                while (i + 1 < len(lines) and lines[i + 1].strip()
                       and not lines[i + 1].startswith(("#", "-", ">", "---"))):
                    i += 1
                    para_lines.append(lines[i])
                text = "\n".join(para_lines)
                if len(text) > 2000:
                    text = text[:1997] + "..."
                blocks.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
                })

            i += 1

        return blocks

    # ============================================================
    # Telegram Bot 發送
    # ============================================================

    async def _send_telegram_digest(self):
        """透過 Bot API 發送頻道摘要"""
        if not BOT_TOKEN or not CHAT_ID or not self._summaries:
            return

        now = datetime.now(TZ_TAIPEI)
        period = "晨報" if now.hour < 14 else "晚報"

        parts = [f"<b>Telegram 頻道{period}</b>"]
        parts.append(f"{now.strftime('%Y-%m-%d %H:%M')}")
        parts.append(f"{len(self._summaries)} 個頻道\n")

        for s in self._summaries:
            icon = "PDF" if s.get("is_cruels") else "CH"
            parts.append(f"[{icon}] <b>{s['channel_name']}</b> ({s['unread_count']}則)")

            for msg in s["owner_messages"][:3]:
                text = msg["text"][:150].replace("<", "&lt;").replace(">", "&gt;")
                parts.append(f"  [{msg['time']}] {text}")

            if len(s["owner_messages"]) > 3:
                parts.append(f"  ...及其他 {len(s['owner_messages']) - 3} 則")

            if s["other_count"] > 0:
                parts.append(f"  其他 {s['other_count']} 則討論")
            parts.append("")

        full_message = "\n".join(parts)
        await self._send_bot_message(full_message)

    async def _send_pdf_notification(self) -> str:
        """PDF 下載完成後另發通知"""
        if not self._pdf_downloads or not BOT_TOKEN or not CHAT_ID:
            return "無 PDF 下載"

        parts = ["<b>電子報 PDF 已下載</b>\n"]
        for pdf in self._pdf_downloads:
            if "error" in pdf:
                parts.append(f"ERR {pdf['filename']}: {pdf['error']}")
            else:
                parts.append(f"OK <b>{pdf['filename']}</b>")
                parts.append(f"   {pdf['size_kb']} KB | {pdf['date']}")
                if pdf.get("gdrive_link"):
                    parts.append(f"   <a href=\"{pdf['gdrive_link']}\">Google Drive</a>")
                if pdf.get("text_preview"):
                    preview = pdf["text_preview"][:200].replace("<", "&lt;").replace(">", "&gt;")
                    parts.append(f"   {preview}")
                parts.append("")

        msg = "\n".join(parts)
        await self._send_bot_message(msg)
        return f"已下載 {len(self._pdf_downloads)} 個 PDF 並通知"

    async def _send_bot_message(self, text: str):
        """透過 Bot API 發送訊息（自動分段 + HTML/plain fallback）"""
        MAX_LEN = 4000
        chunks = []
        while len(text) > MAX_LEN:
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
                for parse_mode in ("HTML", None):
                    payload = {
                        "chat_id": CHAT_ID,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    }
                    if parse_mode:
                        payload["parse_mode"] = parse_mode
                    try:
                        resp = await client.post(url, json=payload, timeout=15)
                        if resp.status_code == 200:
                            break
                        if resp.status_code == 400 and parse_mode == "HTML":
                            continue  # retry without parse_mode
                    except Exception as e:
                        print(f"[TelegramBot] Send error: {e}")
                        break
                await asyncio.sleep(0.5)


# ============================================================
# 獨立執行入口
# ============================================================

async def main():
    agent = TelegramChannelAgent()
    result = await agent.run_full_digest()
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
