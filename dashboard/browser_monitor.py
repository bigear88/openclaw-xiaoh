#!/usr/bin/env python3
"""
小虹瀏覽器自動化監控面板

提供 Web 介面即時觀看瀏覽器操作過程：
- 即時截圖串流（每秒自動更新）
- 操作日誌即時顯示
- 手動控制面板（導航、截圖、點擊）
- 瀏覽器狀態監控

啟動方式：
  python dashboard/browser_monitor.py
  然後打開 http://localhost:8765
"""

import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# 加入專案根目錄到 path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = FastAPI(title="小虹瀏覽器監控", version="1.0")

# 操作日誌
operation_logs: list[dict] = []
MAX_LOGS = 200

# WebSocket 連線池
ws_connections: set[WebSocket] = set()


def add_log(action: str, detail: str = "", status: str = "info"):
    """新增操作日誌"""
    log_entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "detail": detail[:200],
        "status": status,
    }
    operation_logs.append(log_entry)
    if len(operation_logs) > MAX_LOGS:
        operation_logs.pop(0)
    # 廣播給所有 WebSocket 連線
    asyncio.create_task(broadcast_log(log_entry))


async def broadcast_log(log_entry: dict):
    """廣播日誌到所有 WebSocket 連線"""
    dead = set()
    for ws in ws_connections:
        try:
            await ws.send_json({"type": "log", "data": log_entry})
        except Exception:
            dead.add(ws)
    ws_connections -= dead


async def broadcast_screenshot(b64_image: str):
    """廣播截圖到所有 WebSocket 連線"""
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

async def screenshot_stream_loop():
    """每秒截圖一次，推送到所有 WebSocket 連線"""
    from mcp_servers.browser.browser_manager import browser_manager

    while True:
        try:
            if ws_connections and browser_manager._browser and browser_manager._browser.is_connected():
                page = browser_manager._page
                if page:
                    screenshot_bytes = await page.screenshot(type="jpeg", quality=60)
                    b64 = base64.b64encode(screenshot_bytes).decode()
                    await broadcast_screenshot(b64)
        except Exception:
            pass  # 瀏覽器未啟動或已關閉時忽略
        await asyncio.sleep(1)  # 1 FPS


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
            # 接收控制指令
            data = await ws.receive_json()
            await handle_ws_command(data, ws)
    except WebSocketDisconnect:
        ws_connections.discard(ws)
        add_log("監控斷線", "監控畫面已斷線", "warning")


async def handle_ws_command(data: dict, ws: WebSocket):
    """處理 WebSocket 控制指令"""
    from mcp_servers.browser.browser_manager import browser_manager

    cmd = data.get("command")

    if cmd == "navigate":
        url = data.get("url", "")
        add_log("導航", url, "info")
        result = await browser_manager.navigate(url)
        add_log("導航結果", json.dumps(result, ensure_ascii=False),
                "success" if result["success"] else "error")

    elif cmd == "screenshot":
        add_log("手動截圖", "", "info")
        result = await browser_manager.screenshot(output_name=f"manual_{int(time.time())}")
        add_log("截圖完成", result.get("path", ""), "success" if result["success"] else "error")

    elif cmd == "click":
        selector = data.get("selector", "")
        add_log("點擊", selector, "info")
        result = await browser_manager.click(selector)
        add_log("點擊結果", json.dumps(result, ensure_ascii=False),
                "success" if result["success"] else "error")

    elif cmd == "status":
        status = await browser_manager.get_browser_status()
        await ws.send_json({"type": "status", "data": status})

    elif cmd == "close":
        add_log("關閉瀏覽器", "", "warning")
        await browser_manager.close()

    elif cmd == "get_logs":
        await ws.send_json({"type": "logs", "data": operation_logs[-50:]})


@app.get("/api/status")
async def get_status():
    from mcp_servers.browser.browser_manager import browser_manager
    return await browser_manager.get_browser_status()


@app.get("/api/logs")
async def get_logs():
    return {"logs": operation_logs[-50:]}


@app.get("/api/screenshots")
async def list_screenshots():
    from mcp_servers.browser.browser_manager import SCREENSHOTS_DIR
    screenshots = sorted(Path(SCREENSHOTS_DIR).glob("*.png"), key=os.path.getmtime, reverse=True)
    return {
        "screenshots": [
            {"name": s.name, "size_kb": round(s.stat().st_size / 1024, 1),
             "time": datetime.fromtimestamp(s.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")}
            for s in screenshots[:20]
        ]
    }


@app.get("/screenshots/{filename}")
async def serve_screenshot(filename: str):
    from mcp_servers.browser.browser_manager import SCREENSHOTS_DIR
    path = os.path.join(SCREENSHOTS_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return {"error": "not found"}


# ============================================================
# 監控網頁 HTML
# ============================================================

MONITOR_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>小虹 — 瀏覽器自動化監控</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #0f1419; color: #e7e9ea;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 12px 20px;
            display: flex; align-items: center; justify-content: space-between;
            border-bottom: 1px solid #333;
        }
        .header h1 { font-size: 18px; }
        .header .status {
            display: flex; align-items: center; gap: 8px; font-size: 13px;
        }
        .status-dot {
            width: 10px; height: 10px; border-radius: 50%;
            animation: pulse 2s infinite;
        }
        .status-dot.active { background: #00ff88; }
        .status-dot.inactive { background: #ff4444; animation: none; }
        @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

        .container { display: grid; grid-template-columns: 1fr 380px; height: calc(100vh - 50px); }

        /* 左側：即時畫面 */
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
        .no-preview {
            color: #666; font-size: 16px; text-align: center;
        }
        .no-preview p { margin-top: 8px; font-size: 13px; }

        /* 右側面板 */
        .side-panel {
            background: #1a1a2e; border-left: 1px solid #333;
            display: flex; flex-direction: column;
        }

        /* 控制區 */
        .controls { padding: 12px; border-bottom: 1px solid #333; }
        .controls h3 { font-size: 14px; margin-bottom: 8px; color: #aaa; }
        .url-bar {
            display: flex; gap: 6px; margin-bottom: 8px;
        }
        .url-bar input {
            flex: 1; padding: 8px 10px; background: #0f1419;
            border: 1px solid #333; border-radius: 4px; color: #fff;
            font-size: 13px;
        }
        .url-bar input:focus { border-color: #0077ff; outline: none; }
        .btn {
            padding: 8px 14px; border: none; border-radius: 4px;
            cursor: pointer; font-size: 12px; font-weight: 600;
            transition: all 0.2s;
        }
        .btn-primary { background: #0077ff; color: #fff; }
        .btn-primary:hover { background: #0066dd; }
        .btn-secondary { background: #333; color: #ddd; }
        .btn-secondary:hover { background: #444; }
        .btn-danger { background: #cc3333; color: #fff; }
        .btn-danger:hover { background: #aa2222; }
        .btn-group { display: flex; gap: 6px; flex-wrap: wrap; }

        /* 日誌區 */
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

        /* 狀態列 */
        .status-bar {
            padding: 8px 12px; background: #111; border-top: 1px solid #333;
            font-size: 11px; color: #666;
            display: flex; justify-content: space-between;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ 小虹瀏覽器自動化監控</h1>
        <div class="status">
            <span class="status-dot" id="statusDot"></span>
            <span id="statusText">連線中...</span>
        </div>
    </div>
    <div class="container">
        <!-- 即時畫面 -->
        <div class="preview-panel">
            <div class="preview-label" id="previewLabel">等待瀏覽器啟動...</div>
            <img id="previewImg" style="display:none;" alt="Browser Preview">
            <div class="no-preview" id="noPreview">
                🌐 等待瀏覽器啟動
                <p>使用右側控制面板輸入 URL 開始</p>
            </div>
        </div>

        <!-- 右側面板 -->
        <div class="side-panel">
            <div class="controls">
                <h3>🎮 控制面板</h3>
                <div class="url-bar">
                    <input type="text" id="urlInput" placeholder="輸入 URL..."
                           value="https://www.google.com">
                    <button class="btn btn-primary" onclick="navigate()">Go</button>
                </div>
                <div class="btn-group">
                    <button class="btn btn-secondary" onclick="takeScreenshot()">📸 截圖</button>
                    <button class="btn btn-secondary" onclick="getStatus()">📊 狀態</button>
                    <button class="btn btn-danger" onclick="closeBrowser()">⏹ 關閉</button>
                </div>
            </div>
            <div class="logs" id="logsContainer">
                <div class="log-entry info">
                    <span class="log-time">--:--:--</span>
                    <span class="log-action">系統</span>
                    <span class="log-detail">監控面板已啟動，等待 WebSocket 連線...</span>
                </div>
            </div>
            <div class="status-bar">
                <span id="pageInfo">未載入頁面</span>
                <span id="fps">0 FPS</span>
            </div>
        </div>
    </div>

    <script>
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

                    // FPS 計算
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
                            `🟢 Chrome (headed) | ${d.current_url}`;
                    }
                    addLogUI('狀態', JSON.stringify(d), 'info');
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
            div.className = `log-entry ${status}`;
            div.innerHTML = `
                <span class="log-time">${now}</span>
                <span class="log-action">${action}</span>
                <span class="log-detail">${detail}</span>
            `;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
            // 限制日誌數量
            while (container.children.length > 200) container.removeChild(container.firstChild);
        }

        function navigate() {
            const url = document.getElementById('urlInput').value;
            if (url && ws) ws.send(JSON.stringify({command: 'navigate', url}));
        }

        function takeScreenshot() {
            if (ws) ws.send(JSON.stringify({command: 'screenshot'}));
        }

        function getStatus() {
            if (ws) ws.send(JSON.stringify({command: 'status'}));
        }

        function closeBrowser() {
            if (confirm('確定要關閉瀏覽器？')) {
                if (ws) ws.send(JSON.stringify({command: 'close'}));
            }
        }

        // Enter 鍵觸發導航
        document.addEventListener('DOMContentLoaded', () => {
            document.getElementById('urlInput').addEventListener('keydown', (e) => {
                if (e.key === 'Enter') navigate();
            });
            connect();
        });
    </script>
</body>
</html>"""


@app.get("/")
async def monitor_page():
    return HTMLResponse(MONITOR_HTML)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
