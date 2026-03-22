"""
飲食消費記錄 Agent
接收 AI 模型結構化的飲食消費資料，寫入 Notion 飲食記錄 & 消費記錄

輸入格式（JSON）：
{
  "meal_type": "晚餐",
  "restaurant": "臺灣鹹酥雞",
  "items": [
    {"name": "大甲芋頭", "calories": 180, "protein": 2, "carbs": 30, "fat": 7},
    {"name": "洋蔥圈", "calories": 250, "protein": 3, "carbs": 28, "fat": 14},
    {"name": "魷魚", "calories": 200, "protein": 18, "carbs": 12, "fat": 10}
  ],
  "total_amount": 360,
  "currency": "TWD",
  "payment_method": "現金",
  "address": "（由 AI 查詢填入）",
  "map_url": "https://www.google.com/maps/search/...",
  "note": ""
}
"""

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)

TZ_TAIPEI = timezone(timedelta(hours=8))

# Notion DB IDs
DIET_DB_ID = "0acffd95-65c2-4b0d-9d74-bcc114915991"
EXPENSE_DB_ID = "df748ed8-cb27-4567-a6b9-58689ab15473"


def _get_notion_token() -> str:
    """從環境變數或 systemd service 讀取 NOTION_TOKEN"""
    token = os.environ.get("NOTION_TOKEN", "")
    if token:
        return token
    try:
        with open("/etc/systemd/system/openclaw-gateway.service") as f:
            for line in f:
                if "NOTION_TOKEN=" in line:
                    return line.strip().split("=", 2)[2]
    except Exception:
        pass
    return ""


def _notion_request(method: str, endpoint: str, data: dict, token: str) -> dict:
    """發送 Notion API 請求"""
    url = f"https://api.notion.com/v1/{endpoint}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


class MealExpenseAgent(BaseAgent):

    def __init__(self):
        super().__init__("MealExpenseAgent")

    async def process(self, message: str) -> str:
        try:
            return self._handle(message)
        except Exception as e:
            logger.error(f"MealExpenseAgent error: {e}", exc_info=True)
            return self.format_error(e)

    # ── 主邏輯 ────────────────────────────────────────────────────

    def _handle(self, message: str) -> str:
        # 嘗試解析 JSON
        data = self._parse_input(message)
        if not data:
            return (
                "❌ 無法解析輸入。請提供 JSON 格式的飲食消費資料，包含：\n"
                "meal_type, restaurant, items (含 name/calories/protein/carbs/fat), "
                "total_amount 等欄位。"
            )

        token = _get_notion_token()
        if not token:
            return "❌ 找不到 NOTION_TOKEN，無法寫入 Notion"

        now = datetime.now(TZ_TAIPEI)
        date_str = now.strftime("%Y-%m-%d")
        results = []

        # 1. 寫入飲食記錄
        items = data.get("items", [])
        meal_type = data.get("meal_type", "其他")
        restaurant = data.get("restaurant", "")
        diet_ids = []

        for item in items:
            name = item.get("name", "未知品項")
            try:
                page = _notion_request("POST", "pages", {
                    "parent": {"database_id": DIET_DB_ID},
                    "properties": {
                        "品項": {"title": [{"text": {"content": name}}]},
                        "餐別": {"select": {"name": meal_type}},
                        "日期": {"date": {"start": date_str}},
                        "卡路里 (kcal)": {"number": item.get("calories", 0)},
                        "蛋白質 (g)": {"number": item.get("protein", 0)},
                        "碳水化合物 (g)": {"number": item.get("carbs", 0)},
                        "脂肪 (g)": {"number": item.get("fat", 0)},
                        "備註": {"rich_text": [{"text": {"content": f"店家: {restaurant}"}}]},
                    },
                }, token)
                diet_ids.append(page["id"])
            except Exception as e:
                results.append(f"❌ 飲食記錄 [{name}] 失敗: {e}")

        if diet_ids:
            total_cal = sum(i.get("calories", 0) for i in items)
            total_protein = sum(i.get("protein", 0) for i in items)
            total_carbs = sum(i.get("carbs", 0) for i in items)
            total_fat = sum(i.get("fat", 0) for i in items)
            results.append(
                f"🍽️ 飲食記錄已寫入 {len(diet_ids)} 筆\n"
                f"   總熱量: {total_cal} kcal | 蛋白質: {total_protein}g | "
                f"碳水: {total_carbs}g | 脂肪: {total_fat}g"
            )

        # 2. 寫入消費記錄
        amount = data.get("total_amount", 0)
        if amount > 0:
            item_names = "、".join(i.get("name", "") for i in items)
            expense_title = f"{restaurant} — {item_names}" if restaurant else item_names
            address = data.get("address", "")
            map_url = data.get("map_url", "")
            note_parts = []
            if data.get("note"):
                note_parts.append(data["note"])
            if map_url:
                note_parts.append(f"Google Maps: {map_url}")

            properties = {
                "項目": {"title": [{"text": {"content": expense_title[:100]}}]},
                "金額": {"number": amount},
                "類別": {"select": {"name": "餐飲"}},
                "日期": {"date": {"start": date_str}},
            }

            # 選填欄位
            payment = data.get("payment_method", "")
            if payment:
                properties["付款方式"] = {"select": {"name": payment}}
            if address:
                properties["店家地址"] = {"rich_text": [{"text": {"content": address}}]}
            if note_parts:
                properties["備註"] = {"rich_text": [{"text": {"content": "\n".join(note_parts)}}]}

            # 頁面內容 blocks
            children = []
            # 營養摘要
            children.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"text": {"content": "🍽️ 營養成分"}}]},
            })
            for item in items:
                children.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"text": {"content":
                        f"{item.get('name', '?')}: {item.get('calories', 0)} kcal "
                        f"(蛋白質 {item.get('protein', 0)}g / "
                        f"碳水 {item.get('carbs', 0)}g / "
                        f"脂肪 {item.get('fat', 0)}g)"
                    }}]},
                })

            # Google Maps 圖片（如果有 map_url）
            if map_url:
                children.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {"rich_text": [{"text": {"content": "📍 店家位置"}}]},
                })
                children.append({
                    "object": "block",
                    "type": "bookmark",
                    "bookmark": {"url": map_url},
                })

            # 嵌入 Google Maps Static Map（如果有地址）
            if address:
                encoded_addr = urllib.parse.quote(address)
                static_map_url = (
                    f"https://maps.googleapis.com/maps/api/staticmap"
                    f"?center={encoded_addr}&zoom=16&size=600x300"
                    f"&markers=color:red|{encoded_addr}"
                    f"&key={os.environ.get('GEMINI_API_KEY', '')}"
                )
                # 只有有 API key 時才嵌入地圖圖片
                if os.environ.get("GEMINI_API_KEY"):
                    children.append({
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "external",
                            "external": {"url": static_map_url},
                        },
                    })

            try:
                page = _notion_request("POST", "pages", {
                    "parent": {"database_id": EXPENSE_DB_ID},
                    "properties": properties,
                    "children": children[:100],
                }, token)
                results.append(
                    f"💰 消費記錄已寫入: {expense_title[:50]} — ${amount}"
                )
                if map_url:
                    results.append(f"📍 地圖: {map_url}")
            except Exception as e:
                results.append(f"❌ 消費記錄寫入失敗: {e}")

        if not results:
            return "⚠️ 沒有可記錄的資料"

        return "\n".join(results)

    # ── 解析 ──────────────────────────────────────────────────────

    def _parse_input(self, message: str) -> dict | None:
        """嘗試從訊息中解析 JSON 資料"""
        # 直接 JSON
        try:
            return json.loads(message)
        except (json.JSONDecodeError, ValueError):
            pass

        # 從 markdown code block 中提取 JSON
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", message, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # 從訊息中找 { ... }
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", message, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return None
