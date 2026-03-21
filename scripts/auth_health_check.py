#!/usr/bin/env python3
"""授權健康檢查 — 定期驗證所有外部服務的 token/credential 是否有效

檢查項目：
  1. Claude/Anthropic API (API key)
  2. Google Gmail (OAuth token)
  3. Google Drive (rclone)
  4. Notion API (integration token)
  5. GitHub (git credential)

失敗時透過 Telegram 告警，全部通過時只在週日發摘要。
"""

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ── 路徑與常數 ──────────────────────────────────────────────────────
SYSTEMD_SERVICE = "/etc/systemd/system/openclaw-gateway.service"
GMAIL_TOKEN_PATH = Path.home() / "xiaohong" / "token.json"
REFRESH_SCRIPT = Path.home() / "xiaohong" / "scripts" / "refresh_token.py"
OPENCLAW_REPO = Path.home() / "openclaw-xiaoh"
LOG_DIR = Path.home() / "xiaohong" / "logs"
LOG_FILE = LOG_DIR / "auth_health.log"
CHECK_TIMEOUT = 30  # 每項檢查的 timeout (秒)

# ── Logging ─────────────────────────────────────────────────────────
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("auth_health")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)

_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)


# ── 工具函式 ─────────────────────────────────────────────────────────

def read_env_from_service(key: str) -> str:
    """從 systemd service 檔案讀取 Environment= 設定值。"""
    with open(SYSTEMD_SERVICE) as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"Environment={key}="):
                return line.split("=", 2)[2]
    raise ValueError(f"{key} not found in {SYSTEMD_SERVICE}")


def send_telegram(text: str) -> bool:
    """透過 Telegram Bot API 發送訊息。"""
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


# ── 檢查函式 ─────────────────────────────────────────────────────────

def check_anthropic() -> tuple[bool, str]:
    """檢查 Anthropic API Key 是否有效。"""
    try:
        api_key = read_env_from_service("ANTHROPIC_API_KEY")
    except Exception as e:
        return False, f"無法讀取 API key: {e}"

    def _call(key: str) -> tuple[int, str]:
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
                return resp.status, "OK"
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            return e.code, body

    status, detail = _call(api_key)
    if status == 200:
        return True, "API key 有效"

    # 401 → 嘗試 refresh (for OAuth-based keys)
    if status == 401 and REFRESH_SCRIPT.exists():
        logger.info("Anthropic 401, 嘗試 refresh token...")
        try:
            subprocess.run(
                [sys.executable, str(REFRESH_SCRIPT)],
                timeout=60, check=True,
                capture_output=True, text=True,
            )
            # refresh 後重新讀取（refresh_token.py 更新的是 credentials.json，
            # 但 systemd service 用的是固定 API key，所以這裡主要針對 OAuth token 情境）
            status2, detail2 = _call(api_key)
            if status2 == 200:
                return True, "API key 有效 (refresh 後)"
            return False, f"Refresh 後仍失敗: HTTP {status2} - {detail2}"
        except Exception as e:
            return False, f"Refresh 失敗: {e}"

    # 其他錯誤但不是 auth 問題（如 429 rate limit, 529 overloaded）也算通過
    if status in (429, 529, 500, 502, 503):
        return True, f"API key 可能有效 (HTTP {status}, 暫時性錯誤)"

    return False, f"HTTP {status} - {detail}"


def check_gmail() -> tuple[bool, str]:
    """檢查 Gmail OAuth token 是否有效，過期則自動 refresh。"""
    if not GMAIL_TOKEN_PATH.exists():
        return False, f"token.json 不存在: {GMAIL_TOKEN_PATH}"

    try:
        with open(GMAIL_TOKEN_PATH) as f:
            token_data = json.load(f)
    except Exception as e:
        return False, f"無法讀取 token.json: {e}"

    access_token = token_data.get("token", "")
    refresh_token_str = token_data.get("refresh_token", "")
    token_uri = token_data.get("token_uri", "https://oauth2.googleapis.com/token")
    client_id = token_data.get("client_id", "")
    client_secret = token_data.get("client_secret", "")

    def _test_gmail(token: str) -> tuple[int, str]:
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
                return resp.status, "OK"
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")[:200]

    status, detail = _test_gmail(access_token)
    if status == 200:
        return True, "Gmail token 有效"

    # Token 過期 → 用 refresh_token 刷新
    if status == 401 and refresh_token_str:
        logger.info("Gmail token 過期, 自動 refresh...")
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token_str,
        }).encode()
        req = urllib.request.Request(token_uri, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            return False, f"Gmail refresh 失敗: {e}"

        new_token = result.get("access_token", "")
        if not new_token:
            return False, f"Gmail refresh 無 access_token: {result}"

        # 寫回 token.json
        token_data["token"] = new_token
        if "refresh_token" in result:
            token_data["refresh_token"] = result["refresh_token"]
        if "expiry" not in result and "expires_in" in result:
            from datetime import timezone, timedelta
            expiry = datetime.now(timezone.utc) + timedelta(seconds=result["expires_in"])
            token_data["expiry"] = expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        with open(GMAIL_TOKEN_PATH, "w") as f:
            json.dump(token_data, f)
        logger.info("Gmail token 已刷新並寫回 token.json")

        # 驗證新 token
        status2, detail2 = _test_gmail(new_token)
        if status2 == 200:
            return True, "Gmail token 有效 (refresh 後)"
        return False, f"Refresh 後仍失敗: HTTP {status2}"

    return False, f"HTTP {status} - {detail}"


def check_gdrive() -> tuple[bool, str]:
    """透過 rclone 檢查 Google Drive 存取。"""
    try:
        result = subprocess.run(
            ["rclone", "lsd", "gdrive:", "--max-depth", "1"],
            timeout=CHECK_TIMEOUT,
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            return True, "rclone gdrive 正常"
        return False, f"rclone 失敗 (rc={result.returncode}): {result.stderr[:200]}"
    except FileNotFoundError:
        return False, "rclone 未安裝"
    except subprocess.TimeoutExpired:
        return False, "rclone timeout"
    except Exception as e:
        return False, f"rclone 錯誤: {e}"


def check_notion() -> tuple[bool, str]:
    """檢查 Notion integration token。"""
    try:
        token = read_env_from_service("NOTION_TOKEN")
    except Exception as e:
        return False, f"無法讀取 NOTION_TOKEN: {e}"

    req = urllib.request.Request(
        "https://api.notion.com/v1/users/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT) as resp:
            data = json.loads(resp.read())
            name = data.get("name", "unknown")
            return True, f"Notion 正常 (user: {name})"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        return False, f"HTTP {e.code} - {body}"
    except Exception as e:
        return False, f"Notion 錯誤: {e}"


def check_github() -> tuple[bool, str]:
    """透過 git ls-remote 檢查 GitHub 存取。"""
    if not OPENCLAW_REPO.is_dir():
        return False, f"Repo 不存在: {OPENCLAW_REPO}"

    try:
        result = subprocess.run(
            ["git", "ls-remote", "--heads", "origin"],
            cwd=str(OPENCLAW_REPO),
            timeout=CHECK_TIMEOUT,
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            branches = len(result.stdout.strip().splitlines())
            return True, f"GitHub 正常 ({branches} branches)"
        return False, f"git ls-remote 失敗: {result.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return False, "git ls-remote timeout"
    except Exception as e:
        return False, f"git 錯誤: {e}"


# ── 主流程 ───────────────────────────────────────────────────────────

CHECKS = [
    ("Claude/Anthropic", check_anthropic),
    ("Google Gmail", check_gmail),
    ("Google Drive", check_gdrive),
    ("Notion", check_notion),
    ("GitHub", check_github),
]


def main():
    logger.info("=== 授權健康檢查開始 ===")
    results: list[tuple[str, bool, str]] = []

    for name, func in CHECKS:
        try:
            ok, detail = func()
        except Exception as e:
            ok, detail = False, f"未預期錯誤: {e}"
        status_str = "PASS" if ok else "FAIL"
        logger.info(f"  [{status_str}] {name}: {detail}")
        results.append((name, ok, detail))

    # 統計
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = total - passed
    all_pass = failed == 0

    logger.info(f"=== 結果: {passed}/{total} 通過 ===")

    # 組裝訊息
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"🔐 *授權健康檢查* — {now_str}"]
    lines.append(f"結果: {passed}/{total} 通過\n")

    for name, ok, detail in results:
        icon = "✅" if ok else "❌"
        lines.append(f"{icon} *{name}*: {detail}")

    msg = "\n".join(lines)

    # 發送邏輯
    if not all_pass:
        # 有失敗 → 立即告警
        logger.info("有檢查失敗，發送 Telegram 告警")
        send_telegram(msg)
    else:
        # 全部通過 → 只在週日發摘要
        weekday = datetime.now().weekday()  # 0=Mon, 6=Sun
        if weekday == 6:
            logger.info("週日例行摘要，發送 Telegram")
            send_telegram(msg)
        else:
            logger.info("全部通過，非週日不發送通知")

    # 退出碼
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
