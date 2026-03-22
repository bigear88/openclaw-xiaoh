"""小虹秘書 — Dashboard Server v2

FastAPI 監控儀表板（OpenClaw 架構版）：
- 系統資源 (CPU / Memory / Disk / Swap)
- Systemd 服務狀態
- 定時任務排程
- 最近日誌
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

import psutil
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="小虹秘書 Dashboard", version="2.0.0")

# ── 常數 ──────────────────────────────────────────────────────────────

SERVICES = [
    ("openclaw-gateway", "OpenClaw Gateway"),
    ("xiaohong-monitor", "系統監控 Daemon"),
    ("xiaohong-dashboard", "Dashboard"),
    ("xiaohong-browser-monitor", "瀏覽器監控"),
    ("xvfb", "虛擬顯示 (Xvfb)"),
]

TIMERS = [
    ("token-refresh", "Anthropic Token 刷新"),
    ("xiaohong-token-refresh", "Token 刷新 (legacy)"),
    ("auth-health-check", "授權健康檢查"),
    ("response-monitor", "回應品質監控"),
]

LOG_DIR = Path.home() / "xiaohong" / "logs"
OPENCLAW_LOG = Path.home() / ".openclaw" / "logs" / "openclaw.log"


# ── 資料收集 ──────────────────────────────────────────────────────────

def get_system_metrics() -> dict:
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    swap = psutil.swap_memory()
    load1, load5, load15 = psutil.getloadavg()
    uptime_sec = int(datetime.now().timestamp() - psutil.boot_time())
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"
    return {
        "cpu_percent": cpu,
        "load_avg": f"{load1:.1f} / {load5:.1f} / {load15:.1f}",
        "mem_percent": mem.percent,
        "mem_used_gb": round(mem.used / (1024**3), 1),
        "mem_total_gb": round(mem.total / (1024**3), 1),
        "disk_percent": disk.percent,
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "swap_percent": swap.percent,
        "swap_used_gb": round(swap.used / (1024**3), 1),
        "swap_total_gb": round(swap.total / (1024**3), 1),
        "uptime": uptime_str,
    }


def get_service_status() -> list[dict]:
    results = []
    for svc_name, label in SERVICES:
        try:
            r = subprocess.run(
                ["systemctl", "show", svc_name, "--property=ActiveState,SubState,MainPID,MemoryCurrent,ActiveEnterTimestamp"],
                capture_output=True, text=True, timeout=5,
            )
            props = {}
            for line in r.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v

            active = props.get("ActiveState", "unknown")
            sub = props.get("SubState", "")
            pid = props.get("MainPID", "0")
            mem_bytes = props.get("MemoryCurrent", "[not set]")
            entered = props.get("ActiveEnterTimestamp", "")

            mem_mb = ""
            if mem_bytes.isdigit():
                mem_mb = f"{int(mem_bytes) / (1024*1024):.1f} MB"

            since = ""
            if entered and entered != "n/a":
                # format: "Sun 2026-03-22 14:11:53 UTC"
                try:
                    parts = entered.strip().split()
                    if len(parts) >= 4:
                        since = f"{parts[1]} {parts[2]}"
                except Exception:
                    since = entered

            results.append({
                "name": svc_name,
                "label": label,
                "active": active,
                "sub": sub,
                "pid": pid if pid != "0" else "-",
                "memory": mem_mb or "-",
                "since": since,
            })
        except Exception as e:
            results.append({
                "name": svc_name, "label": label,
                "active": "error", "sub": str(e),
                "pid": "-", "memory": "-", "since": "-",
            })
    return results


def get_timer_status() -> list[dict]:
    results = []
    for timer_name, label in TIMERS:
        try:
            r = subprocess.run(
                ["systemctl", "show", f"{timer_name}.timer",
                 "--property=ActiveState,LastTriggerUSec,NextElapseUSecRealtime"],
                capture_output=True, text=True, timeout=5,
            )
            props = {}
            for line in r.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    props[k] = v

            active = props.get("ActiveState", "unknown")

            last_raw = props.get("LastTriggerUSec", "n/a")
            next_raw = props.get("NextElapseUSecRealtime", "n/a")

            def _fmt_time(raw: str) -> str:
                if not raw or raw in ("n/a", "0"):
                    return "-"
                try:
                    parts = raw.strip().split()
                    if len(parts) >= 4:
                        return f"{parts[1]} {parts[2]}"
                except Exception:
                    pass
                return raw[:19]

            results.append({
                "name": timer_name,
                "label": label,
                "active": active,
                "last": _fmt_time(last_raw),
                "next": _fmt_time(next_raw),
            })
        except Exception as e:
            results.append({
                "name": timer_name, "label": label,
                "active": "error", "last": "-", "next": str(e),
            })
    return results


def get_recent_logs(max_lines: int = 25) -> list[dict]:
    """從各日誌檔讀取最近的記錄"""
    log_files = [
        ("token_refresh", LOG_DIR / "token_refresh.log"),
        ("auth_health", LOG_DIR / "auth_health.log"),
        ("response_monitor", LOG_DIR / "response_monitor.log"),
    ]
    entries = []
    for source, path in log_files:
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            for line in lines[-15:]:
                # format: "2026-03-22 14:11:54 [INFO] message"
                entries.append({"source": source, "line": line})
        except Exception:
            continue

    # 也讀取 openclaw gateway 最近日誌
    if OPENCLAW_LOG.exists():
        try:
            lines = OPENCLAW_LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            for line in lines[-10:]:
                entries.append({"source": "gateway", "line": line[:200]})
        except Exception:
            pass

    # 按時間倒序（假設行首是 timestamp）
    entries.sort(key=lambda e: e["line"][:19] if len(e["line"]) >= 19 else "", reverse=True)
    return entries[:max_lines]


def get_auth_summary() -> list[dict]:
    """讀取最近一次 auth_health_check 的結果"""
    log_path = LOG_DIR / "auth_health.log"
    if not log_path.exists():
        return []

    lines = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    # 找最後一次 "=== 授權健康檢查開始 ===" 之後的行
    last_start = -1
    for i, line in enumerate(lines):
        if "授權健康檢查開始" in line:
            last_start = i

    if last_start < 0:
        return []

    results = []
    for line in lines[last_start:]:
        if "[PASS]" in line or "[FAIL]" in line:
            passed = "[PASS]" in line
            # extract service name and detail
            marker = "[PASS] " if passed else "[FAIL] "
            idx = line.find(marker)
            if idx >= 0:
                detail = line[idx + len(marker):]
                name, _, msg = detail.partition(": ")
                results.append({"name": name, "ok": passed, "detail": msg})

    return results


# ── HTML 生成 ─────────────────────────────────────────────────────────

def _gauge(label: str, percent: float, used: str, total: str, unit: str = "GB") -> str:
    if percent >= 80:
        color = "#f44336"
    elif percent >= 60:
        color = "#ff9800"
    else:
        color = "#4CAF50"
    dash = percent * 3.14
    gap = 314 - dash
    return f"""<div class="gauge-card">
        <div class="gauge-label">{label}</div>
        <div class="gauge-ring">
            <svg viewBox="0 0 120 120">
                <circle cx="60" cy="60" r="50" fill="none" stroke="#333" stroke-width="10"/>
                <circle cx="60" cy="60" r="50" fill="none" stroke="{color}" stroke-width="10"
                    stroke-dasharray="{dash} {gap}"
                    stroke-dashoffset="78.5" stroke-linecap="round" transform="rotate(-90 60 60)"/>
            </svg>
            <div class="gauge-value">{percent:.0f}%</div>
        </div>
        <div class="gauge-detail">{used} / {total} {unit}</div>
    </div>"""


def _status_badge(state: str) -> str:
    colors = {
        "active": "#4CAF50", "running": "#4CAF50",
        "inactive": "#9E9E9E", "dead": "#9E9E9E",
        "failed": "#f44336", "error": "#f44336",
        "activating": "#ff9800", "waiting": "#2196F3",
    }
    c = colors.get(state, "#9E9E9E")
    return f'<span class="badge" style="background:{c}">{state}</span>'


@app.get("/", response_class=HTMLResponse)
async def index():
    m = get_system_metrics()
    services = get_service_status()
    timers = get_timer_status()
    logs = get_recent_logs()
    auth = get_auth_summary()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Gauges
    gauges = (
        _gauge("CPU", m["cpu_percent"], f'{m["cpu_percent"]:.0f}', "100", "%")
        + _gauge("Memory", m["mem_percent"], str(m["mem_used_gb"]), str(m["mem_total_gb"]))
        + _gauge("Disk", m["disk_percent"], str(m["disk_used_gb"]), str(m["disk_total_gb"]))
        + _gauge("Swap", m["swap_percent"], str(m["swap_used_gb"]), str(m["swap_total_gb"]))
    )

    # Services table
    svc_rows = ""
    for s in services:
        svc_rows += f"""<tr>
            <td><strong>{s['label']}</strong><br><span class="mono">{s['name']}</span></td>
            <td>{_status_badge(s['active'])}</td>
            <td class="mono">{s['pid']}</td>
            <td>{s['memory']}</td>
            <td>{s['since']}</td>
        </tr>"""

    # Timers table
    tmr_rows = ""
    for t in timers:
        tmr_rows += f"""<tr>
            <td><strong>{t['label']}</strong><br><span class="mono">{t['name']}.timer</span></td>
            <td>{_status_badge(t['active'])}</td>
            <td>{t['last']}</td>
            <td>{t['next']}</td>
        </tr>"""

    # Auth summary
    auth_html = ""
    if auth:
        auth_items = ""
        for a in auth:
            icon = "✅" if a["ok"] else "❌"
            auth_items += f'<span class="auth-item">{icon} {a["name"]}</span>'
        auth_html = f'<div class="auth-bar">{auth_items}</div>'

    # Logs
    log_rows = ""
    for entry in logs:
        src = entry["source"]
        line = entry["line"]
        level_cls = ""
        if "[ERROR]" in line or "[FAIL]" in line:
            level_cls = "log-error"
        elif "[WARNING]" in line:
            level_cls = "log-warn"
        log_rows += f"""<tr class="{level_cls}">
            <td class="mono log-src">{src}</td>
            <td class="mono log-line">{line}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <title>小虹秘書 Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="15">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0; padding: 20px; }}
        .header {{ display: flex; align-items: center; gap: 15px; margin-bottom: 20px; flex-wrap: wrap; }}
        .header h1 {{ color: #e94560; font-size: 1.5em; }}
        .header .meta {{ color: #888; font-size: 0.85em; margin-left: auto; text-align: right; }}
        .section {{ margin-top: 20px; }}
        .section-title {{
            color: #e94560; font-size: 1.05em; font-weight: 600;
            border-bottom: 1px solid #2a2a4a; padding-bottom: 5px; margin-bottom: 10px;
        }}
        .gauges {{ display: flex; gap: 15px; flex-wrap: wrap; }}
        .gauge-card {{
            background: #111128; border-radius: 12px; padding: 15px;
            text-align: center; min-width: 130px; flex: 1;
            border: 1px solid #1e1e3e;
        }}
        .gauge-label {{ font-size: 0.8em; color: #aaa; margin-bottom: 6px; font-weight: 600; }}
        .gauge-ring {{ position: relative; width: 90px; height: 90px; margin: 0 auto; }}
        .gauge-ring svg {{ width: 100%; height: 100%; }}
        .gauge-value {{
            position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
            font-size: 1.2em; font-weight: bold; color: #fff;
        }}
        .gauge-detail {{ font-size: 0.72em; color: #666; margin-top: 6px; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 0.85em; }}
        th, td {{ border: 1px solid #1e1e3e; padding: 6px 10px; text-align: left; }}
        th {{ background: #111128; color: #999; font-weight: 600; font-size: 0.8em; text-transform: uppercase; }}
        tr:hover {{ background: #15153a; }}
        .mono {{ font-family: 'Cascadia Code', 'Fira Code', monospace; font-size: 0.85em; color: #888; }}
        .badge {{
            display: inline-block; padding: 2px 8px; border-radius: 10px;
            font-size: 0.75em; font-weight: 600; color: #fff;
        }}
        .auth-bar {{ display: flex; gap: 15px; flex-wrap: wrap; padding: 10px 0; }}
        .auth-item {{ font-size: 0.9em; }}
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
        .log-src {{ white-space: nowrap; width: 120px; color: #666; }}
        .log-line {{ word-break: break-all; font-size: 0.8em; color: #bbb; }}
        .log-error {{ background: #2a0a0a !important; }}
        .log-error .log-line {{ color: #f88; }}
        .log-warn {{ background: #2a200a !important; }}
        .log-warn .log-line {{ color: #fb8; }}
        .log-table {{ max-height: 400px; overflow-y: auto; display: block; }}
        .log-table table {{ display: table; }}
        @media (max-width: 900px) {{
            .two-col {{ grid-template-columns: 1fr; }}
            .gauges {{ flex-direction: column; align-items: center; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>小虹秘書 Dashboard</h1>
        <div class="meta">
            {now}<br>
            <span style="color:#666">Uptime: {m['uptime']} | Load: {m['load_avg']}</span>
        </div>
    </div>

    <div class="section">
        <div class="section-title">System Resources</div>
        <div class="gauges">{gauges}</div>
    </div>

    {f'<div class="section"><div class="section-title">Auth Status (最近一次檢查)</div>{auth_html}</div>' if auth_html else ''}

    <div class="two-col">
        <div class="section">
            <div class="section-title">Services</div>
            <table>
                <tr><th>Service</th><th>Status</th><th>PID</th><th>Memory</th><th>Since</th></tr>
                {svc_rows}
            </table>
        </div>
        <div class="section">
            <div class="section-title">Scheduled Timers</div>
            <table>
                <tr><th>Timer</th><th>Status</th><th>Last Run</th><th>Next Run</th></tr>
                {tmr_rows}
            </table>
        </div>
    </div>

    <div class="section">
        <div class="section-title">Recent Logs</div>
        <div class="log-table">
            <table>
                <tr><th>Source</th><th>Log</th></tr>
                {log_rows}
            </table>
        </div>
    </div>
</body>
</html>"""


@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/system")
async def system():
    return get_system_metrics()


@app.get("/api/services")
async def services():
    return get_service_status()


@app.get("/api/timers")
async def timers():
    return get_timer_status()


@app.get("/api/auth")
async def auth():
    return get_auth_summary()
