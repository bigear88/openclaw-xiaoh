# 小虹多Agent系統 — OpenClaw + MCP 遷移計畫

## 架構變更：純 Python Orchestrator → OpenClaw + MCP 橋接

| | 現有架構 | OpenClaw 架構 |
|---|---|---|
| 閘道器 | Python orchestrator/main.py | OpenClaw (TypeScript, :18789) |
| 訊息頻道 | 自己寫 Telegram 長輪詢 | OpenClaw 內建 Telegram 頻道 |
| Agent 呼叫 | 直接 Python import | MCP 協議（標準化） |
| 瀏覽器自動化 | 自己寫 playwright_tool.py | OpenManus via MCP |
| 記憶體保護 | Memory Guardian | Memory Guardian + systemd 限制 |
| 排程 | Python APScheduler | Linux cron |

## 風險評估

| 風險 | 嚴重性 | 緩解方式 |
|------|--------|----------|
| OpenClaw 更新頻繁，fork 維護成本 | 中 | 定期 merge upstream，只改設定不改核心 |
| TypeScript debug 困難 | 低 | 主要邏輯在 Python MCP server |
| MCP 多一層抽象，延遲增加 | 低 | stdio 通訊延遲 < 10ms |
| 記憶體增加 ~500 MB | 低 | Memory Guardian + systemd 雙重保護 |
| OpenManus alpha 穩定性 | 中 | 按需啟動，失敗時 fallback |

## 參考專案
- OpenClaw: Claude Code CLI 閘道器
- OpenManus-RL: https://github.com/OpenManus/OpenManus-RL
