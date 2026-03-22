#!/usr/bin/env python3
"""OpenClaw 回應監控代理 — 每小時掃描 log，慢回應 / 錯誤匯出至 Notion

掃描項目：
  1. lane task error / lane task completed（含 durationMs）
  2. embedded_run_agent_end 事件（含 model、provider、error）
  3. 任何 durationMs > 60000（1 分鐘）的紀錄

輸出：
  - 將慢回應 & 錯誤摘要寫入 Notion「秘書的大腦」
  - 包含原因分析與改進建議
  - 寫入 ~/xiaohong/logs/response_monitor.log
"""

import json
import logging
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 常數 ──────────────────────────────────────────────────────────────
LOG_DIR_PATH = Path("/tmp/openclaw")
SYSTEMD_SERVICE = "/etc/systemd/system/openclaw-gateway.service"
LOCAL_LOG_DIR = Path.home() / "xiaohong" / "logs"
LOCAL_LOG_FILE = LOCAL_LOG_DIR / "response_monitor.log"
SLOW_THRESHOLD_MS = 60_000  # 1 分鐘
NOTION_PARENT_PAGE_ID = "32a17e8c85788171af12dbaa41741095"
STATE_FILE = Path.home() / "xiaohong" / "logs" / ".response_monitor_state.json"

# ── Logging ───────────────────────────────────────────────────────────
LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("response_monitor")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

_fh = logging.FileHandler(LOCAL_LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)


# ── 工具 ──────────────────────────────────────────────────────────────

def read_env_from_service(key: str) -> str:
    with open(SYSTEMD_SERVICE) as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"Environment={key}="):
                return line.split("=", 2)[2]
    raise ValueError(f"{key} not found in {SYSTEMD_SERVICE}")


def load_state() -> dict:
    """載入上次掃描的 checkpoint（避免重複處理）。"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ── Log 解析 ──────────────────────────────────────────────────────────

def get_log_files_for_window(hours: int = 2) -> list[Path]:
    """取得最近 N 小時可能涉及的 log 檔案（跨日邊界時取兩個）。"""
    now = datetime.now(timezone.utc)
    dates = set()
    for h in range(hours + 1):
        dt = now - timedelta(hours=h)
        dates.add(dt.strftime("%Y-%m-%d"))
    files = []
    for d in sorted(dates):
        p = LOG_DIR_PATH / f"openclaw-{d}.log"
        if p.exists():
            files.append(p)
    return files


def parse_log_line(raw: str) -> dict | None:
    """解析一行 JSON log。"""
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None


def extract_events(log_files: list[Path], since: datetime) -> list[dict]:
    """從 log 檔案中提取最近時段的關鍵事件。"""
    events = []
    seen_lines = set()

    for log_file in log_files:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line in seen_lines:
                    continue

                entry = parse_log_line(line)
                if not entry:
                    continue

                # 取得時間
                time_str = entry.get("time", "")
                if not time_str:
                    meta = entry.get("_meta", {})
                    time_str = meta.get("date", "")
                if not time_str:
                    continue

                try:
                    # 處理 ISO 格式時間
                    ts = time_str.replace("Z", "+00:00")
                    if "+" not in ts and "-" not in ts[10:]:
                        ts += "+00:00"
                    event_time = datetime.fromisoformat(ts)
                except (ValueError, IndexError):
                    continue

                if event_time < since:
                    continue

                # 過濾重複的 ANSI-colored 行（openclaw parent logger 重複輸出）
                subsystem = ""
                field0 = entry.get("0", "")
                if isinstance(field0, str) and field0.startswith("{"):
                    try:
                        sub = json.loads(field0)
                        subsystem = sub.get("subsystem", "")
                    except (json.JSONDecodeError, ValueError):
                        pass

                # 只取 subsystem 版本（避免重複計算 parent logger 的行）
                if not subsystem and isinstance(field0, str) and "[" in field0:
                    # 這是 ANSI-colored parent logger 重複行，跳過
                    if entry.get("_meta", {}).get("name") == "openclaw":
                        continue

                field1 = entry.get("1", "")

                # ── 類型 1: lane task error（含 durationMs）
                if isinstance(field1, str) and "lane task error" in field1:
                    m_dur = re.search(r"durationMs=(\d+)", field1)
                    m_err = re.search(r'error="(.+)"$', field1)
                    m_lane = re.search(r"lane=(\S+)", field1)
                    duration_ms = int(m_dur.group(1)) if m_dur else 0
                    error_msg = m_err.group(1) if m_err else field1
                    lane = m_lane.group(1) if m_lane else "unknown"

                    # 去重：同一 lane 的 main 和 session lane 只取一個
                    if "session:" in lane:
                        continue

                    events.append({
                        "type": "lane_task_error",
                        "time": event_time.isoformat(),
                        "duration_ms": duration_ms,
                        "lane": lane,
                        "error": error_msg[:300],
                        "log_level": entry.get("_meta", {}).get("logLevelName", ""),
                    })

                # ── 類型 2: embedded_run_agent_end
                elif isinstance(field1, dict) and field1.get("event") == "embedded_run_agent_end":
                    events.append({
                        "type": "agent_end",
                        "time": event_time.isoformat(),
                        "is_error": field1.get("isError", False),
                        "error": (field1.get("error", "") or "")[:300],
                        "model": field1.get("model", "unknown"),
                        "provider": field1.get("provider", "unknown"),
                        "http_code": field1.get("httpCode", ""),
                        "error_type": field1.get("providerErrorType", ""),
                        "failover_reason": field1.get("failoverReason", ""),
                        "run_id": field1.get("runId", ""),
                    })

                # ── 類型 3: 任何包含 durationMs 的成功事件
                elif isinstance(field1, str) and "durationMs=" in field1 and "error" not in field1.lower():
                    m_dur = re.search(r"durationMs=(\d+)", field1)
                    duration_ms = int(m_dur.group(1)) if m_dur else 0
                    if duration_ms > 0:
                        events.append({
                            "type": "lane_task_ok",
                            "time": event_time.isoformat(),
                            "duration_ms": duration_ms,
                            "detail": field1[:300],
                        })

    return events


# ── 分析 ──────────────────────────────────────────────────────────────

def analyze_events(events: list[dict]) -> dict:
    """分析事件，產生摘要報告。"""
    slow_events = []  # durationMs > threshold
    error_events = []
    all_durations = []

    error_categories = Counter()
    provider_errors = defaultdict(list)
    model_errors = defaultdict(list)

    for ev in events:
        if ev["type"] == "lane_task_error":
            error_events.append(ev)
            dur = ev.get("duration_ms", 0)
            all_durations.append(dur)
            if dur > SLOW_THRESHOLD_MS:
                slow_events.append(ev)
            # 分類錯誤
            err = ev.get("error", "")
            if "No API key" in err:
                m = re.search(r'provider "(\w+)"', err)
                provider = m.group(1) if m else "unknown"
                error_categories[f"missing_api_key:{provider}"] += 1
            elif "rate_limit" in err.lower():
                error_categories["rate_limit"] += 1
            elif "401" in err or "authentication" in err.lower():
                error_categories["auth_failure"] += 1
            else:
                error_categories["other"] += 1

        elif ev["type"] == "agent_end":
            if ev.get("is_error"):
                error_events.append(ev)
                provider = ev.get("provider", "unknown")
                model = ev.get("model", "unknown")
                err_type = ev.get("error_type", ev.get("failover_reason", "unknown"))
                provider_errors[provider].append(err_type)
                model_errors[model].append(err_type)
                error_categories[f"{provider}:{err_type}"] += 1

        elif ev["type"] == "lane_task_ok":
            dur = ev.get("duration_ms", 0)
            all_durations.append(dur)
            if dur > SLOW_THRESHOLD_MS:
                slow_events.append(ev)

    return {
        "total_events": len(events),
        "total_errors": len(error_events),
        "slow_events": slow_events,
        "error_categories": dict(error_categories),
        "provider_errors": dict(provider_errors),
        "model_errors": dict(model_errors),
        "all_durations": sorted(all_durations, reverse=True)[:20],
        "avg_duration_ms": sum(all_durations) / len(all_durations) if all_durations else 0,
        "max_duration_ms": max(all_durations) if all_durations else 0,
    }


def generate_diagnosis(analysis: dict) -> str:
    """根據分析結果，產生原因診斷與改進建議。"""
    lines = []

    # 錯誤分類診斷
    cats = analysis["error_categories"]
    if not cats and not analysis["slow_events"]:
        return "✅ 過去一小時無慢回應或錯誤，運行正常。"

    lines.append("## 問題診斷\n")

    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        if cat.startswith("missing_api_key:"):
            provider = cat.split(":")[1]
            lines.append(f"- **缺少 API Key ({provider})**: {count} 次")
            lines.append(f"  → 建議：檢查 `auth-profiles.json` 是否包含 `{provider}` 的設定")
        elif "rate_limit" in cat:
            provider = cat.split(":")[0] if ":" in cat else "unknown"
            lines.append(f"- **API 速率限制 ({provider})**: {count} 次")
            lines.append(f"  → 建議：升級 API plan 或降低請求頻率；考慮切換備用模型")
        elif "auth_failure" in cat or "401" in cat:
            lines.append(f"- **認證失敗**: {count} 次")
            lines.append(f"  → 建議：檢查 token 是否過期，執行 auth_health_check.py")
        elif cat.startswith("anthropic:"):
            err_type = cat.split(":")[1]
            lines.append(f"- **Anthropic {err_type}**: {count} 次")
            lines.append(f"  → 建議：{'升級 API 方案或等待配額重置' if 'rate' in err_type else '檢查 API key 有效性'}")
        else:
            lines.append(f"- **{cat}**: {count} 次")

    if analysis["slow_events"]:
        lines.append(f"\n## 慢回應 (>{SLOW_THRESHOLD_MS/1000:.0f}s)")
        for ev in analysis["slow_events"][:10]:
            dur_s = ev.get("duration_ms", 0) / 1000
            t = ev.get("time", "")[:19]
            detail = ev.get("error", ev.get("detail", ""))[:100]
            lines.append(f"- `{t}` {dur_s:.1f}s — {detail}")

    # 統計
    if analysis["all_durations"]:
        lines.append(f"\n## 統計")
        lines.append(f"- 平均回應時間: {analysis['avg_duration_ms']/1000:.1f}s")
        lines.append(f"- 最慢回應: {analysis['max_duration_ms']/1000:.1f}s")
        lines.append(f"- 總事件數: {analysis['total_events']}")
        lines.append(f"- 錯誤數: {analysis['total_errors']}")

    # 改進建議
    lines.append(f"\n## 改進建議")
    suggestions = set()
    for cat in cats:
        if "missing_api_key" in cat:
            suggestions.add("重新設定 auth-profiles.json，確保所有 provider 都有有效的 API key")
        if "rate_limit" in cat:
            suggestions.add("考慮使用 model fallback chain，rate limit 時自動切換備用模型")
        if "auth_failure" in cat or "401" in str(cats):
            suggestions.add("設定 OAuth token 自動刷新機制，或改用 API key 認證")

    if not suggestions:
        suggestions.add("持續監控，目前無需額外改進")

    for i, s in enumerate(suggestions, 1):
        lines.append(f"{i}. {s}")

    return "\n".join(lines)


# ── Notion 輸出 ────────────────────────────────────────────────────────

def post_to_notion(title: str, content: str, notion_token: str) -> bool:
    """在 Notion 頁面下建立子頁面，寫入監控報告。"""
    # 將 markdown 內容切分成 Notion blocks
    blocks = []
    for line in content.split("\n"):
        if not line.strip():
            continue
        if line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [{"type": "text", "text": {"content": line[3:]}}]
                }
            })
        elif line.startswith("- "):
            text = line[2:]
            # 簡單處理 bold markdown
            rich_text = []
            parts = re.split(r'(\*\*[^*]+\*\*)', text)
            for part in parts:
                if part.startswith("**") and part.endswith("**"):
                    rich_text.append({
                        "type": "text",
                        "text": {"content": part[2:-2]},
                        "annotations": {"bold": True}
                    })
                else:
                    rich_text.append({"type": "text", "text": {"content": part}})
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": rich_text}
            })
        elif re.match(r'^\d+\. ', line):
            text = re.sub(r'^\d+\. ', '', line)
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {
                    "rich_text": [{"type": "text", "text": {"content": text}}]
                }
            })
        else:
            blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": line}}]
                }
            })

    # 限制 Notion API 一次最多 100 blocks
    blocks = blocks[:100]

    payload = json.dumps({
        "parent": {"page_id": NOTION_PARENT_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "📊"},
        "properties": {
            "title": {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        },
        "children": blocks,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=payload,
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            page_id = result.get("id", "unknown")
            logger.info(f"Notion 頁面已建立: {page_id}")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        logger.error(f"Notion API 錯誤 HTTP {e.code}: {body}")
        return False
    except Exception as e:
        logger.error(f"Notion 寫入失敗: {e}")
        return False


def send_telegram(text: str) -> bool:
    """透過 Telegram 發送告警摘要。"""
    try:
        bot_token = read_env_from_service("TELEGRAM_BOT_TOKEN")
        chat_id = read_env_from_service("TELEGRAM_CHAT_ID")
    except Exception as e:
        logger.error(f"無法讀取 Telegram 設定: {e}")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except Exception as e:
        logger.error(f"Telegram 發送失敗: {e}")
        return False


# ── 主流程 ─────────────────────────────────────────────────────────────

def main():
    logger.info("=== 回應監控開始 ===")

    # 讀取 Notion token
    try:
        notion_token = read_env_from_service("NOTION_TOKEN")
    except Exception as e:
        logger.error(f"無法讀取 NOTION_TOKEN: {e}")
        sys.exit(1)

    # 決定掃描時間範圍（上次掃描到現在，或預設最近 1 小時）
    state = load_state()
    now = datetime.now(timezone.utc)
    last_scan = state.get("last_scan_utc")
    if last_scan:
        try:
            since = datetime.fromisoformat(last_scan)
        except (ValueError, TypeError):
            since = now - timedelta(hours=1)
    else:
        since = now - timedelta(hours=1)

    # 不掃描超過 6 小時前的 log（避免首次啟動掃太多）
    max_lookback = now - timedelta(hours=6)
    if since < max_lookback:
        since = max_lookback

    logger.info(f"掃描範圍: {since.isoformat()} ~ {now.isoformat()}")

    # 取得 log 檔案
    log_files = get_log_files_for_window(hours=2)
    if not log_files:
        logger.warning("找不到 OpenClaw log 檔案")
        save_state({"last_scan_utc": now.isoformat()})
        sys.exit(0)

    logger.info(f"掃描 log 檔案: {[str(f) for f in log_files]}")

    # 提取事件
    events = extract_events(log_files, since)
    logger.info(f"提取到 {len(events)} 個事件")

    if not events:
        logger.info("無新事件，跳過")
        save_state({"last_scan_utc": now.isoformat()})
        sys.exit(0)

    # 分析
    analysis = analyze_events(events)
    diagnosis = generate_diagnosis(analysis)

    logger.info(f"分析結果: {analysis['total_errors']} 錯誤, "
                f"{len(analysis['slow_events'])} 慢回應")

    # 判斷是否需要匯出至 Notion
    has_issues = analysis["total_errors"] > 0 or len(analysis["slow_events"]) > 0

    if has_issues:
        # 建立 Notion 報告
        now_str = now.strftime("%Y-%m-%d %H:%M")
        title = f"📊 回應監控報告 — {now_str} UTC"

        report = f"掃描範圍: {since.strftime('%H:%M')} ~ {now.strftime('%H:%M')} UTC\n"
        report += f"事件數: {analysis['total_events']} | 錯誤: {analysis['total_errors']} | "
        report += f"慢回應: {len(analysis['slow_events'])}\n\n"
        report += diagnosis

        success = post_to_notion(title, report, notion_token)
        if success:
            logger.info("報告已匯出至 Notion")
        else:
            logger.error("Notion 匯出失敗")

        # Telegram 簡報
        tg_msg = f"📊 *OpenClaw 回應監控*\n"
        tg_msg += f"時間: {now_str} UTC\n"
        tg_msg += f"錯誤: {analysis['total_errors']} 次\n"
        tg_msg += f"慢回應: {len(analysis['slow_events'])} 次\n\n"

        # 前三大錯誤類別
        top_errors = sorted(analysis["error_categories"].items(), key=lambda x: -x[1])[:3]
        if top_errors:
            tg_msg += "主要問題:\n"
            for cat, count in top_errors:
                tg_msg += f"• {cat}: {count} 次\n"

        send_telegram(tg_msg)
    else:
        logger.info("無異常，不產生報告")

    # 更新 state
    save_state({"last_scan_utc": now.isoformat()})
    logger.info("=== 回應監控結束 ===")


if __name__ == "__main__":
    main()
