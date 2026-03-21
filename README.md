# openclaw-xiaoh

小虹多Agent系統 — OpenClaw + MCP 架構

## 架構總覽

整合 7 個 Python AI agents，透過 MCP (Model Context Protocol) 協議統一管理，
支援 Telegram 頻道、系統監控、Memory Guardian 記憶體保護。

```
OpenClaw Gateway (TypeScript, :18789)
  ├── Telegram Channel（bot token）
  ├── MCP: xiaohong-agents (Python, stdio)
  │     ├── general_agent      — 一般對話
  │     ├── investment_agent   — 投資分析
  │     ├── health_agent       — 健康管理
  │     ├── accounting_agent   — 記帳
  │     ├── learning_agent     — 學習助手
  │     ├── briefing_agent     — 晨晚報
  │     ├── bible_agent        — 聖經靈修
  │     ├── system_status      — 系統狀態
  │     └── memory_control     — 記憶體管理
  ├── MCP: openmanus (Python, stdio)
  │     ├── browser_navigate
  │     ├── browser_screenshot
  │     ├── bash_execute
  │     └── editor_*
  ├── MCP: system-monitor (Python, stdio)
  └── Cron: 07:30 報紙 / 08:00 晨報 / 20:00 晚報
```

## 參考專案

- [OpenClaw](https://github.com/anthropics/claude-code) — Claude Code CLI 閘道器
- [OpenManus / OpenManus-RL](https://github.com/OpenManus/OpenManus-RL) — RL tuning for LLM agents，瀏覽器自動化

## 專案結構

```
openclaw-xiaoh/
├── mcp_servers/
│   ├── xiaohong_agents_server.py   # MCP Bridge：7 agents + memory_control
│   └── system_monitor_daemon.py    # 系統監控 daemon
├── openclaw-config/
│   ├── openclaw.json               # OpenClaw 主設定檔
│   ├── deploy-openclaw.sh          # 一鍵部署腳本
│   └── SKILL.md                    # 小虹人格路由規則
├── shared/
│   └── PATCH-base_agent.md         # base_agent.py 修改說明
├── agents/                         # Agent 實作（從現有系統遷移）
├── docs/
│   └── migration-plan.md           # 遷移計畫文件
└── README.md
```

## 七個執行階段

| Phase | 說明 | 狀態 |
|-------|------|------|
| 1 | 倉庫設定（Node.js 22 + pnpm + Fork） | ✅ |
| 2 | MCP Bridge for Agents | 待執行 |
| 3 | OpenManus 整合 | 待執行 |
| 4 | OpenClaw 設定 | 待執行 |
| 5 | 系統監控 | 待執行 |
| 6 | 排程 | 待執行 |
| 7 | 測試驗證 | 待執行 |

## 記憶體預算

| 元件 | 預估 RAM |
|------|----------|
| OpenClaw (Node.js) | ~500 MB |
| MCP Bridge (Python) | ~300 MB |
| Active Agents (1-2 個) | ~400 MB |
| OpenManus (按需啟動) | ~300 MB |
| System Monitor | ~50 MB |
| Playwright/Chromium (按需) | ~300 MB |
| 系統 + 其他 | ~500 MB |
| **合計** | **~2.35 GB** |

## Memory Guardian 整合

每次 MCP 工具呼叫前：
1. `guardian.check_and_protect()` — 檢查並觸發保護
2. `guardian.can_start_agent(name)` — 判斷是否允許啟動
3. 記憶體不足時回傳友善訊息而非硬錯誤

## License

MIT
