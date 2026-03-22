#!/usr/bin/env python3
"""自動刷新 Anthropic OAuth token 並同步至 gateway service + auth-profiles.json

流程：
  1. 執行 `claude auth status` 觸發 token refresh
  2. 讀取 ~/.claude/.credentials.json 的最新 access token
  3. 比對 gateway service 的 ANTHROPIC_API_KEY
  4. 若不同 → 更新 service file + auth-profiles.json → 重啟 gateway
  5. 寫入日誌
"""

import json
import logging
import subprocess
import sys
from pathlib import Path

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
SERVICE_FILE = Path("/etc/systemd/system/openclaw-gateway.service")
AUTH_PROFILES = Path.home() / ".openclaw" / "agents" / "xiaohong" / "agent" / "auth-profiles.json"
LOG_DIR = Path.home() / "xiaohong" / "logs"
LOG_FILE = LOG_DIR / "token_refresh.log"

LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("token_refresh")
logger.setLevel(logging.INFO)
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(_fmt)
logger.addHandler(_fh)
_sh = logging.StreamHandler()
_sh.setFormatter(_fmt)
logger.addHandler(_sh)


def get_current_token() -> str:
    """從 credentials.json 讀取 access token"""
    with open(CREDENTIALS) as f:
        return json.load(f)["claudeAiOauth"]["accessToken"]


def get_service_key() -> str:
    """從 gateway service 讀取 ANTHROPIC_API_KEY"""
    with open(SERVICE_FILE) as f:
        for line in f:
            if "ANTHROPIC_API_KEY=" in line:
                return line.strip().split("=", 2)[2]
    return ""


def trigger_refresh():
    """執行 claude auth status 觸發自動 refresh"""
    try:
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("claude auth status 成功")
        else:
            logger.warning(f"claude auth status 失敗: {result.stderr[:200]}")
    except Exception as e:
        logger.error(f"執行 claude auth status 錯誤: {e}")


def update_service_file(new_key: str):
    """更新 gateway service 的 ANTHROPIC_API_KEY"""
    lines = SERVICE_FILE.read_text().splitlines()
    updated = []
    for line in lines:
        if line.strip().startswith("Environment=ANTHROPIC_API_KEY="):
            updated.append(f"Environment=ANTHROPIC_API_KEY={new_key}")
        else:
            updated.append(line)
    SERVICE_FILE.write_text("\n".join(updated) + "\n")


def update_auth_profiles(new_key: str):
    """更新 auth-profiles.json 的 anthropic key"""
    if not AUTH_PROFILES.exists():
        return
    with open(AUTH_PROFILES) as f:
        data = json.load(f)
    if "anthropic" in data.get("profiles", {}):
        data["profiles"]["anthropic"]["key"] = new_key
        with open(AUTH_PROFILES, "w") as f:
            json.dump(data, f, indent=2)


def restart_gateway():
    """重啟 openclaw-gateway"""
    try:
        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            timeout=10, check=True, capture_output=True,
        )
        subprocess.run(
            ["sudo", "systemctl", "restart", "openclaw-gateway"],
            timeout=30, check=True, capture_output=True,
        )
        logger.info("Gateway 已重啟")
    except Exception as e:
        logger.error(f"Gateway 重啟失敗: {e}")


def main():
    logger.info("=== Token refresh 開始 ===")

    # 1. 觸發 refresh
    trigger_refresh()

    # 2. 讀取最新 token
    try:
        new_token = get_current_token()
    except Exception as e:
        logger.error(f"無法讀取 credentials: {e}")
        sys.exit(1)

    # 3. 比對 service key
    try:
        current_key = get_service_key()
    except Exception as e:
        logger.error(f"無法讀取 service file: {e}")
        sys.exit(1)

    if new_token == current_key:
        logger.info("Token 未變更，無需更新")
        sys.exit(0)

    logger.info(f"Token 已變更: {current_key[:20]}... → {new_token[:20]}...")

    # 4. 更新
    try:
        update_service_file(new_token)
        logger.info("Service file 已更新")
    except Exception as e:
        logger.error(f"更新 service file 失敗: {e}")
        sys.exit(1)

    try:
        update_auth_profiles(new_token)
        logger.info("auth-profiles.json 已更新")
    except Exception as e:
        logger.warning(f"更新 auth-profiles 失敗: {e}")

    # 5. 重啟
    restart_gateway()
    logger.info("=== Token refresh 完成 ===")


if __name__ == "__main__":
    main()
