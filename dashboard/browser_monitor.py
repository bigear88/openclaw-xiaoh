#!/usr/bin/env python3
"""
小虹瀏覽器自動化監控面板 (agent-browser 版)

以 agent-browser CLI 取代 Playwright，提供：
- 即時截圖串流（每秒自動更新）
- 操作日誌即時顯示
- 手動控制面板（導航、截圖、點擊、snapshot）
- 瀏覽器狀態監控

啟動方式：
  python dashboard/browser_monitor.py
  然後打開 http://localhost:8765
"""

import asyncio
import base64
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="小虹統合監控", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:9174"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clawalytics 設定
CLAWALYTICS_PORT = 9174
CLAWALYTICS_BASE = f"http://localhost:{CLAWALYTICS_PORT}"

# 截圖目錄
SCREENSHOTS_DIR = Path("/tmp/xiaohong-screenshots")
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# 操作日誌
operation_logs: list[dict] = []
MAX_LOGS = 200

# WebSocket 連線池
ws_connections: set[WebSocket] = set()

# agent-browser 環境變數
AB_ENV = {**os.environ, "DISPLAY": ":99"}


# ============================================================
# agent-browser CLI 封裝
# ============================================================

def _run_ab(*args: str, timeout: int = 15) -> tuple[bool, str]:
    """執行 agent-browser 指令，回傳 (成功, 輸出)"""
    try:
        r = subprocess.run(
            ["agent-browser", *args],
            capture_output=True, text=True, timeout=timeout, env=AB_ENV,
        )
        output = (r.stdout or "").strip()
        if r.returncode != 0:
            err = (r.stderr or "").strip()
            return False, err or output or f"exit code {r.returncode}"
        return True, output
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


async def _run_ab_async(*args: str, timeout: int = 15) -> tuple[bool, str]:
    """非同步執行 agent-browser"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _run_ab(*args, timeout=timeout))


async def ab_navigate(url: str) -> dict:
    ok, out = await _run_ab_async("open", url)
    return {"success": ok, "message": out}


async def ab_screenshot(name: str = "") -> dict:
    fname = name or f"shot_{int(time.time())}"
    path = str(SCREENSHOTS_DIR / f"{fname}.png")
    ok, out = await _run_ab_async("screenshot", path)
    return {"success": ok, "path": path if ok else "", "message": out}


async def ab_click(selector: str) -> dict:
    ok, out = await _run_ab_async("click", selector)
    return {"success": ok, "message": out}


async def ab_snapshot(interactive: bool = True, compact: bool = True) -> dict:
    args = ["snapshot"]
    if interactive:
        args.append("-i")
    if compact:
        args.append("-c")
    ok, out = await _run_ab_async(*args)
    return {"success": ok, "tree": out if ok else "", "message": out}


async def ab_fill(selector: str, text: str) -> dict:
    ok, out = await _run_ab_async("fill", selector, text)
    return {"success": ok, "message": out}


async def ab_get_status() -> dict:
    """取得瀏覽器狀態"""
    url_ok, url = await _run_ab_async("get", "url")
    title_ok, title = await _run_ab_async("get", "title")

    if url_ok:
        return {
            "browser_running": True,
            "current_url": url,
            "current_title": title if title_ok else "",
        }
    return {"browser_running": False, "current_url": "", "current_title": ""}


async def ab_close() -> dict:
    ok, out = await _run_ab_async("close")
    return {"success": ok, "message": out}


async def ab_eval(js: str) -> dict:
    ok, out = await _run_ab_async("eval", js)
    return {"success": ok, "result": out if ok else "", "message": out}


# ============================================================
# 日誌 & 廣播
# ============================================================

def add_log(action: str, detail: str = "", status: str = "info"):
    log_entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "detail": detail[:200],
        "status": status,
    }
    operation_logs.append(log_entry)
    if len(operation_logs) > MAX_LOGS:
        operation_logs.pop(0)
    asyncio.create_task(broadcast_log(log_entry))


async def broadcast_log(log_entry: dict):
    dead = set()
    for ws in ws_connections:
        try:
            await ws.send_json({"type": "log", "data": log_entry})
        except Exception:
            dead.add(ws)
    ws_connections -= dead


async def broadcast_screenshot(b64_image: str):
    dead = set()
    for ws in ws_connections:
        try:
            await ws.send_json({"type": "screenshot", "data": b64_image})
        except Exception:
            dead.add(ws)
    ws_connections -= dead


# ============================================================
# 即時截圖串流（背景任務）
# ============================================================

_stream_screenshot_path = SCREENSHOTS_DIR / "_stream.jpg"


async def screenshot_stream_loop():
    """每秒截圖一次，推送到所有 WebSocket 連線"""
    while True:
        try:
            if ws_connections:
                path = str(_stream_screenshot_path)
                ok, _ = await _run_ab_async(
                    "screenshot", path,
                    "--screenshot-format", "jpeg",
                    "--screenshot-quality", "60",
                    timeout=5,
                )
                if ok and _stream_screenshot_path.exists():
                    b64 = base64.b64encode(_stream_screenshot_path.read_bytes()).decode()
                    await broadcast_screenshot(b64)
        except Exception:
            pass
        await asyncio.sleep(1.5)  # ~0.7 FPS to reduce overhead


# ============================================================
# API Routes
# ============================================================

@app.on_event("startup")
async def startup():
    asyncio.create_task(screenshot_stream_loop())


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    ws_connections.add(ws)
    add_log("監控連線", "新的監控畫面已連線", "info")
    try:
        while True:
            data = await ws.receive_json()
            await handle_ws_command(data, ws)
    except WebSocketDisconnect:
        ws_connections.discard(ws)
        add_log("監控斷線", "監控畫面已斷線", "warning")


async def handle_ws_command(data: dict, ws: WebSocket):
    cmd = data.get("command")

    if cmd == "navigate":
        url = data.get("url", "")
        add_log("導航", url, "info")
        result = await ab_navigate(url)
        add_log("導航結果", result["message"],
                "success" if result["success"] else "error")

    elif cmd == "screenshot":
        add_log("手動截圖", "", "info")
        result = await ab_screenshot(f"manual_{int(time.time())}")
        add_log("截圖完成", result.get("path", ""),
                "success" if result["success"] else "error")

    elif cmd == "click":
        selector = data.get("selector", "")
        add_log("點擊", selector, "info")
        result = await ab_click(selector)
        add_log("點擊結果", result["message"],
                "success" if result["success"] else "error")

    elif cmd == "fill":
        selector = data.get("selector", "")
        text = data.get("text", "")
        add_log("填入", f"{selector} = {text}", "info")
        result = await ab_fill(selector, text)
        add_log("填入結果", result["message"],
                "success" if result["success"] else "error")

    elif cmd == "snapshot":
        add_log("Snapshot", "取得 Accessibility Tree", "info")
        result = await ab_snapshot()
        await ws.send_json({"type": "snapshot", "data": result})
        add_log("Snapshot", f"{'成功' if result['success'] else '失敗'}",
                "success" if result["success"] else "error")

    elif cmd == "eval":
        js = data.get("js", "")
        add_log("JS 執行", js[:80], "info")
        result = await ab_eval(js)
        await ws.send_json({"type": "eval_result", "data": result})

    elif cmd == "status":
        status = await ab_get_status()
        await ws.send_json({"type": "status", "data": status})

    elif cmd == "close":
        add_log("關閉瀏覽器", "", "warning")
        await ab_close()

    elif cmd == "get_logs":
        await ws.send_json({"type": "logs", "data": operation_logs[-50:]})


@app.get("/api/status")
async def get_status():
    return await ab_get_status()


@app.get("/api/logs")
async def get_logs():
    return {"logs": operation_logs[-50:]}


@app.get("/api/screenshots")
async def list_screenshots():
    screenshots = sorted(SCREENSHOTS_DIR.glob("*.png"), key=os.path.getmtime, reverse=True)
    return {
        "screenshots": [
            {"name": s.name, "size_kb": round(s.stat().st_size / 1024, 1),
             "time": datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
            for s in screenshots[:20]
        ]
    }


@app.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    path = SCREENSHOTS_DIR / filename
    if path.exists():
        return FileResponse(str(path))
    return JSONResponse({"error": "not found"}, status_code=404)


# ============================================================
# Clawalytics Proxy
# ============================================================

@app.get("/api/analytics/{path:path}")
async def proxy_analytics(path: str, request: Request):
    import urllib.request
    import urllib.error
    target = f"{CLAWALYTICS_BASE}/api/{path}"
    if request.query_params:
        target += f"?{request.query_params}"
    try:
        with urllib.request.urlopen(target, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            return JSONResponse(content=data)
    except (urllib.error.URLError, Exception) as e:
        return JSONResponse(
            content={"error": "clawalytics_unavailable", "detail": str(e)},
            status_code=502,
        )


@app.get("/api/health")
async def health_check():
    import urllib.request
    import urllib.error

    status = await ab_get_status()
    health = {
        "monitor": "ok",
        "browser": "running" if status["browser_running"] else "stopped",
        "clawalytics": "unknown",
        "clawalytics_url": CLAWALYTICS_BASE,
    }
    try:
        with urllib.request.urlopen(f"{CLAWALYTICS_BASE}/api/stats", timeout=3) as resp:
            if resp.status == 200:
                health["clawalytics"] = "ok"
                health["clawalytics_stats"] = json.loads(resp.read().decode())
    except Exception:
        health["clawalytics"] = "offline"
    return health


# ============================================================
# 監控網頁 HTML
# ============================================================

MONITOR_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小虹 — 統合監控面板</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f1419; color: #e7e9ea;
        }

        /* ========== Header + Tabs ========== */
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 0 20px;
            display: flex; align-items: stretch; justify-content: space-between;
            border-bottom: 1px solid #333; height: 48px;
        }
        .header-left { display: flex; align-items: center; gap: 24px; }
        .header h1 { font-size: 16px; white-space: nowrap; }
        .tabs { display: flex; gap: 2px; align-self: stretch; }
        .tab {
            padding: 0 18px; display: flex; align-items: center; gap: 6px;
            font-size: 13px; font-weight: 500; cursor: pointer;
            border: none; background: transparent; color: #888;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }
        .tab:hover { color: #ccc; background: rgba(255,255,255,0.04); }
        .tab.active { color: #fff; border-bottom-color: #0077ff; }
        .tab .badge {
            font-size: 10px; padding: 1px 6px; border-radius: 8px;
            background: #333; color: #888;
        }
        .tab.active .badge { background: #0077ff33; color: #5599ff; }

        .header-right {
            display: flex; align-items: center; gap: 12px; font-size: 12px;
        }
        .status-indicator { display: flex; align-items: center; gap: 6px; }
        .status-dot {
            width: 8px; height: 8px; border-radius: 50%;
            animation: pulse 2s infinite;
        }
        .status-dot.active { background: #00ff88; }
        .status-dot.inactive { background: #ff4444; animation: none; }
        .status-dot.unknown { background: #666; animation: none; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

        /* ========== Tab Content ========== */
        .tab-content { display: none; height: calc(100vh - 48px); }
        .tab-content.active { display: block; }

        /* -- Browser Monitor Tab -- */
        .monitor-container { display: grid; grid-template-columns: 1fr 420px; height: 100%; }
        .preview-panel {
            background: #000; display: flex; flex-direction: column;
            align-items: center; justify-content: center; position: relative;
        }
        .preview-panel img {
            max-width: 100%; max-height: calc(100vh - 120px);
            border: 1px solid #333; border-radius: 4px;
        }
        .preview-label {
            position: absolute; top: 10px; left: 10px;
            background: rgba(0,0,0,0.7); padding: 4px 10px;
            border-radius: 4px; font-size: 12px; color: #aaa;
        }
        .no-preview { color: #666; font-size: 16px; text-align: center; }
        .no-preview p { margin-top: 8px; font-size: 13px; }

        .side-panel {
            background: #1a1a2e; border-left: 1px solid #333;
            display: flex; flex-direction: column;
        }
        .controls { padding: 12px; border-bottom: 1px solid #333; }
        .controls h3 { font-size: 14px; margin-bottom: 8px; color: #aaa; }
        .url-bar { display: flex; gap: 6px; margin-bottom: 8px; }
        .url-bar input {
            flex: 1; padding: 8px 10px; background: #0f1419;
            border: 1px solid #333; border-radius: 4px; color: #fff; font-size: 13px;
        }
        .url-bar input:focus { border-color: #0077ff; outline: none; }
        .btn {
            padding: 8px 14px; border: none; border-radius: 4px;
            cursor: pointer; font-size: 12px; font-weight: 600; transition: all 0.2s;
        }
        .btn-primary { background: #0077ff; color: #fff; }
        .btn-primary:hover { background: #0066dd; }
        .btn-secondary { background: #333; color: #ddd; }
        .btn-secondary:hover { background: #444; }
        .btn-danger { background: #cc3333; color: #fff; }
        .btn-danger:hover { background: #aa2222; }
        .btn-green { background: #2a7a3a; color: #fff; }
        .btn-green:hover { background: #1e6a2e; }
        .btn-group { display: flex; gap: 6px; flex-wrap: wrap; }

        /* Snapshot panel */
        .snapshot-panel {
            padding: 8px; border-bottom: 1px solid #333;
            max-height: 200px; overflow-y: auto;
            font-family: 'Cascadia Code', 'Fira Code', monospace;
            font-size: 11px; line-height: 1.5; color: #aaa;
            background: #0a0e14; display: none;
        }
        .snapshot-panel.visible { display: block; }
        .snapshot-panel .ref { color: #5599ff; cursor: pointer; }
        .snapshot-panel .ref:hover { text-decoration: underline; }

        .logs {
            flex: 1; overflow-y: auto; padding: 8px;
            font-family: 'Cascadia Code', 'Fira Code', monospace;
            font-size: 12px; line-height: 1.6;
        }
        .log-entry {
            padding: 3px 6px; border-radius: 3px; margin-bottom: 2px;
            display: flex; gap: 8px;
        }
        .log-entry:hover { background: rgba(255,255,255,0.05); }
        .log-time { color: #666; min-width: 60px; }
        .log-action { color: #0077ff; min-width: 70px; }
        .log-detail { color: #aaa; word-break: break-all; }
        .log-entry.error .log-action { color: #ff4444; }
        .log-entry.success .log-action { color: #00ff88; }
        .log-entry.warning .log-action { color: #ffaa00; }

        .status-bar {
            padding: 8px 12px; background: #111; border-top: 1px solid #333;
            font-size: 11px; color: #666;
            display: flex; justify-content: space-between;
        }

        /* -- Analytics Tab -- */
        .analytics-container { height: 100%; position: relative; }
        .analytics-container iframe { width: 100%; height: 100%; border: none; }
        .analytics-offline {
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; height: 100%;
            color: #666; text-align: center; gap: 16px;
        }
        .analytics-offline .icon { font-size: 48px; }
        .analytics-offline h2 { color: #aaa; font-size: 20px; }
        .analytics-offline p { font-size: 14px; max-width: 500px; line-height: 1.6; }
        .analytics-offline .cmd-box {
            background: #1a1a2e; border: 1px solid #333; border-radius: 8px;
            padding: 16px 24px; margin-top: 8px; text-align: left;
            font-family: 'Cascadia Code', monospace; font-size: 13px;
            color: #ccc; line-height: 2;
        }
        .analytics-offline .cmd-box .comment { color: #555; }
        .analytics-offline .retry-btn {
            margin-top: 12px; padding: 10px 24px; background: #0077ff;
            color: #fff; border: none; border-radius: 6px; cursor: pointer;
            font-size: 14px; font-weight: 500;
        }
        .analytics-offline .retry-btn:hover { background: #0066dd; }

        /* -- Service Status Tab -- */
        .api-status-container { padding: 24px; overflow-y: auto; height: 100%; }
        .api-grid {
            display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
            gap: 16px;
        }
        .api-card {
            background: #1a1a2e; border: 1px solid #333; border-radius: 8px;
            padding: 16px; transition: border-color 0.2s;
        }
        .api-card:hover { border-color: #444; }
        .api-card-header {
            display: flex; justify-content: space-between; align-items: center;
            margin-bottom: 12px;
        }
        .api-card-title { font-size: 14px; font-weight: 600; }
        .api-card-badge {
            font-size: 11px; padding: 2px 8px; border-radius: 10px; font-weight: 500;
        }
        .api-card-badge.online { background: #00ff8822; color: #00ff88; }
        .api-card-badge.offline { background: #ff444422; color: #ff4444; }
        .api-card-badge.checking { background: #ffaa0022; color: #ffaa00; }
        .api-card-detail { font-size: 12px; color: #888; margin-top: 4px; }
        .api-card-url { font-size: 11px; color: #555; font-family: monospace; margin-top: 8px; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <h1>小虹統合監控</h1>
            <div class="tabs">
                <button class="tab active" onclick="switchTab('monitor')">
                    <span>瀏覽器監控</span>
                    <span class="badge" id="engineBadge">agent-browser</span>
                </button>
                <button class="tab" onclick="switchTab('analytics')">
                    <span>成本分析</span>
                    <span class="badge" id="analyticsBadge">--</span>
                </button>
                <button class="tab" onclick="switchTab('api-status')">
                    <span>服務狀態</span>
                </button>
            </div>
        </div>
        <div class="header-right">
            <div class="status-indicator">
                <span class="status-dot" id="statusDot"></span>
                <span id="statusText">連線中...</span>
            </div>
            <div class="status-indicator">
                <span class="status-dot" id="analyticsDot" class="unknown"></span>
                <span id="analyticsStatus">Analytics</span>
            </div>
        </div>
    </div>

    <!-- Tab 1: Browser Monitor -->
    <div class="tab-content active" id="tab-monitor">
        <div class="monitor-container">
            <div class="preview-panel">
                <div class="preview-label" id="previewLabel">等待瀏覽器啟動...</div>
                <img id="previewImg" style="display:none;" alt="Browser Preview">
                <div class="no-preview" id="noPreview">
                    <span style="font-size:36px">&#127760;</span><br><br>
                    等待瀏覽器啟動
                    <p>使用右側控制面板輸入 URL 開始<br>
                    <span style="color:#555;font-size:12px">Powered by agent-browser (Rust CLI)</span></p>
                </div>
            </div>
            <div class="side-panel">
                <div class="controls">
                    <h3>控制面板</h3>
                    <div class="url-bar">
                        <input type="text" id="urlInput" placeholder="輸入 URL..."
                               value="https://www.google.com">
                        <button class="btn btn-primary" onclick="navigate()">Go</button>
                    </div>
                    <div class="btn-group">
                        <button class="btn btn-secondary" onclick="takeScreenshot()">截圖</button>
                        <button class="btn btn-green" onclick="takeSnapshot()">Snapshot</button>
                        <button class="btn btn-secondary" onclick="getStatus()">狀態</button>
                        <button class="btn btn-danger" onclick="closeBrowser()">關閉</button>
                    </div>
                </div>
                <div class="snapshot-panel" id="snapshotPanel"></div>
                <div class="logs" id="logsContainer">
                    <div class="log-entry info">
                        <span class="log-time">--:--:--</span>
                        <span class="log-action">系統</span>
                        <span class="log-detail">監控面板已啟動 (agent-browser v3.0)</span>
                    </div>
                </div>
                <div class="status-bar">
                    <span id="pageInfo">未載入頁面</span>
                    <span id="fps">0 FPS</span>
                </div>
            </div>
        </div>
    </div>

    <!-- Tab 2: Analytics -->
    <div class="tab-content" id="tab-analytics">
        <div class="analytics-container" id="analyticsContainer">
            <div class="analytics-offline" id="analyticsOffline">
                <div class="icon">&#128202;</div>
                <h2>Clawalytics 成本分析面板</h2>
                <p>追蹤 Claude Code 和 OpenClaw 的 AI 花費、Agent 效能、頻道使用量與安全監控。</p>
                <div class="cmd-box">
                    <div><span class="comment"># 啟動 Clawalytics</span></div>
                    <div>npx clawalytics start --port 9174</div>
                </div>
                <button class="retry-btn" onclick="checkAnalytics()">檢查連線</button>
            </div>
            <iframe id="analyticsFrame" style="display:none;" src="about:blank"></iframe>
        </div>
    </div>

    <!-- Tab 3: Service Status -->
    <div class="tab-content" id="tab-api-status">
        <div class="api-status-container">
            <h2 style="margin-bottom:16px; font-size:18px;">服務健康狀態</h2>
            <div class="api-grid" id="apiGrid"></div>
        </div>
    </div>

    <script>
        /* ========== Tab Switching ========== */
        function switchTab(tabId) {
            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            event.currentTarget.classList.add('active');
            document.getElementById('tab-' + tabId).classList.add('active');
            if (tabId === 'analytics') checkAnalytics();
            if (tabId === 'api-status') refreshApiStatus();
        }

        /* ========== Browser Monitor (WebSocket) ========== */
        let ws;
        let frameCount = 0;
        let lastFpsUpdate = Date.now();

        function connect() {
            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            ws = new WebSocket(`${protocol}//${location.host}/ws`);

            ws.onopen = () => {
                document.getElementById('statusDot').className = 'status-dot active';
                document.getElementById('statusText').textContent = '已連線';
                addLogUI('系統', 'WebSocket 已連線', 'success');
                ws.send(JSON.stringify({command: 'get_logs'}));
            };

            ws.onmessage = (event) => {
                const msg = JSON.parse(event.data);

                if (msg.type === 'screenshot') {
                    const img = document.getElementById('previewImg');
                    img.src = 'data:image/jpeg;base64,' + msg.data;
                    img.style.display = 'block';
                    document.getElementById('noPreview').style.display = 'none';
                    frameCount++;
                    const now = Date.now();
                    if (now - lastFpsUpdate > 1000) {
                        document.getElementById('fps').textContent = frameCount + ' FPS';
                        frameCount = 0;
                        lastFpsUpdate = now;
                    }
                }
                else if (msg.type === 'log') {
                    addLogUI(msg.data.action, msg.data.detail, msg.data.status);
                }
                else if (msg.type === 'status') {
                    const d = msg.data;
                    if (d.current_url) {
                        document.getElementById('pageInfo').textContent = d.current_title || d.current_url;
                        document.getElementById('previewLabel').textContent =
                            'agent-browser | ' + d.current_url;
                    } else {
                        document.getElementById('pageInfo').textContent = '瀏覽器未啟動';
                        document.getElementById('previewLabel').textContent = '等待瀏覽器啟動...';
                    }
                    addLogUI('狀態', JSON.stringify(d), 'info');
                }
                else if (msg.type === 'snapshot') {
                    const panel = document.getElementById('snapshotPanel');
                    if (msg.data.success) {
                        // Parse refs and make clickable
                        let html = msg.data.tree
                            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
                            .replace(/\\[ref=(e\\d+)\\]/g, '<span class="ref" onclick="clickRef(\'@$1\')">[ref=$1]</span>');
                        panel.innerHTML = '<pre>' + html + '</pre>';
                        panel.classList.add('visible');
                    } else {
                        panel.innerHTML = '<span style="color:#f66">' + msg.data.message + '</span>';
                        panel.classList.add('visible');
                    }
                }
                else if (msg.type === 'eval_result') {
                    addLogUI('JS 結果', msg.data.result || msg.data.message,
                             msg.data.success ? 'success' : 'error');
                }
                else if (msg.type === 'logs') {
                    msg.data.forEach(l => addLogUI(l.action, l.detail, l.status));
                }
            };

            ws.onclose = () => {
                document.getElementById('statusDot').className = 'status-dot inactive';
                document.getElementById('statusText').textContent = '已斷線，重連中...';
                setTimeout(connect, 3000);
            };
        }

        function addLogUI(action, detail, status) {
            const container = document.getElementById('logsContainer');
            const now = new Date().toTimeString().slice(0, 8);
            const div = document.createElement('div');
            div.className = 'log-entry ' + status;
            div.innerHTML =
                '<span class="log-time">' + now + '</span>' +
                '<span class="log-action">' + action + '</span>' +
                '<span class="log-detail">' + (detail||'').replace(/</g,'&lt;') + '</span>';
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
            while (container.children.length > 200) container.removeChild(container.firstChild);
        }

        function navigate() {
            const url = document.getElementById('urlInput').value;
            if (url && ws) ws.send(JSON.stringify({command: 'navigate', url: url}));
        }
        function takeScreenshot() {
            if (ws) ws.send(JSON.stringify({command: 'screenshot'}));
        }
        function takeSnapshot() {
            if (ws) ws.send(JSON.stringify({command: 'snapshot'}));
        }
        function getStatus() {
            if (ws) ws.send(JSON.stringify({command: 'status'}));
        }
        function closeBrowser() {
            if (confirm('確定要關閉瀏覽器？')) {
                if (ws) ws.send(JSON.stringify({command: 'close'}));
                document.getElementById('snapshotPanel').classList.remove('visible');
            }
        }
        function clickRef(ref) {
            addLogUI('點擊 Ref', ref, 'info');
            if (ws) ws.send(JSON.stringify({command: 'click', selector: ref}));
        }

        /* ========== Analytics ========== */
        const CLAWALYTICS_URL = 'http://localhost:9174';

        async function checkAnalytics() {
            const dot = document.getElementById('analyticsDot');
            const statusEl = document.getElementById('analyticsStatus');
            const badge = document.getElementById('analyticsBadge');
            dot.className = 'status-dot unknown';
            statusEl.textContent = '檢查中...';
            try {
                const res = await fetch(CLAWALYTICS_URL + '/api/stats', {
                    signal: AbortSignal.timeout(3000)
                });
                if (res.ok) {
                    const data = await res.json();
                    dot.className = 'status-dot active';
                    statusEl.textContent = 'Analytics';
                    document.getElementById('analyticsOffline').style.display = 'none';
                    const frame = document.getElementById('analyticsFrame');
                    frame.style.display = 'block';
                    if (frame.src === 'about:blank' || !frame.src.includes('9174')) {
                        frame.src = CLAWALYTICS_URL;
                    }
                    badge.textContent = data.daily_cost !== undefined
                        ? '$' + Number(data.daily_cost).toFixed(2) : 'ON';
                } else { throw new Error('not ok'); }
            } catch (e) {
                dot.className = 'status-dot inactive';
                statusEl.textContent = 'Analytics OFF';
                badge.textContent = 'OFF';
                document.getElementById('analyticsOffline').style.display = 'flex';
                document.getElementById('analyticsFrame').style.display = 'none';
            }
        }

        /* ========== Service Status ========== */
        const SERVICES = [
            { name: '瀏覽器監控 API', url: '/api/status', type: 'local' },
            { name: 'Clawalytics', url: CLAWALYTICS_URL + '/api/stats', type: 'external',
              detail: '成本追蹤 / Agent 效能 / 安全監控' },
        ];

        async function refreshApiStatus() {
            const grid = document.getElementById('apiGrid');
            grid.innerHTML = '';
            for (const svc of SERVICES) {
                const card = document.createElement('div');
                card.className = 'api-card';
                card.innerHTML =
                    '<div class="api-card-header">' +
                    '  <span class="api-card-title">' + svc.name + '</span>' +
                    '  <span class="api-card-badge checking">檢查中...</span>' +
                    '</div>' +
                    (svc.detail ? '<div class="api-card-detail">' + svc.detail + '</div>' : '') +
                    '<div class="api-card-url">' + svc.url + '</div>';
                grid.appendChild(card);
                (async (cardEl, service) => {
                    try {
                        const res = await fetch(service.url, { signal: AbortSignal.timeout(3000) });
                        const badge = cardEl.querySelector('.api-card-badge');
                        badge.className = 'api-card-badge ' + (res.ok ? 'online' : 'offline');
                        badge.textContent = res.ok ? res.status + ' OK' : res.status;
                    } catch (e) {
                        const badge = cardEl.querySelector('.api-card-badge');
                        badge.className = 'api-card-badge offline';
                        badge.textContent = '離線';
                    }
                })(card, svc);
            }
        }

        /* ========== Init ========== */
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('urlInput').addEventListener('keydown', (e) => {
                if (e.key === 'Enter') navigate();
            });
            connect();
            checkAnalytics();
        });
    </script>
</body>
</html>"""


@app.get("/")
async def monitor_page():
    return HTMLResponse(MONITOR_HTML)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
