# 小虹多Agent系統 — 目錄結構與設定

> 來源：Notion「📁 小虹多Agent系統 — 目錄結構與設定」

## 專案目錄結構

```
xiaohong/
├── orchestrator/           # 主調度器
│   ├── main.py             # 入口：Telegram + 排程 + 分派
│   ├── router.py           # 意圖分類（關鍵字 + Claude fallback）
│   └── process_manager.py  # Agent 生命週期管理
├── agents/                 # Sub-Agents
│   ├── accounting/         # 💰 記帳
│   ├── investment/         # 📈 投資分析
│   ├── health/             # 🥗 飲食健康
│   ├── learning/           # 📚 學習進度
│   ├── newspaper/          # 📰 報紙截圖
│   ├── briefing/           # 🌅 晨報晚報
│   ├── bible/              # ✝️ 靈修
│   └── general/            # ✨ 通用
├── dashboard/              # 監控網頁
│   ├── server/app.py       # FastAPI + WebSocket
│   └── frontend/index.html # React Dashboard
├── shared/                 # 共用模組
│   ├── config.py           # 設定載入器（dataclass）
│   ├── database.py         # SQLite 操作層
│   └── base_agent.py       # Agent 基底類別
├── data/                   # 資料目錄
│   ├── xiaohong.db         # SQLite 資料庫
│   └── logs/               # 每日日誌檔
├── config.yaml             # 設定檔（API keys、排程）
└── scripts/
    └── setup_vm.sh         # 一鍵安裝腳本
```

## config.yaml 設定項目

| 區塊 | 說明 |
|------|------|
| anthropic | Claude API key |
| telegram | Bot token + admin chat ID |
| notion | Token + 各資料庫 ID |
| gmail | OAuth credentials 路徑 |
| groq | Groq API key（Whisper 用） |
| schedule | 晨報 08:00、晚報 20:00、報紙 07:30 |
| health | 卡路里目標 1800、蛋白質 140g、脂肪 60g、碳水 180g、體重目標 65kg |
| agents | 最大並行數 3、心跳間隔 30s、任務逾時 300s |

## SQLite 資料表

### tasks 表
追蹤所有任務的生命週期
- `id`, `created_at`, `source`（telegram/scheduler）
- `intent`, `agent_name`, `status`（pending/running/completed/error）
- `input_data`, `output_data`, `error_msg`
- `started_at`, `completed_at`

### agent_status 表
各 Agent 即時狀態
- `agent_name`（PK）, `status`, `pid`
- `last_heartbeat`, `current_task_id`
- `total_tasks_completed`, `total_errors`

### logs 表
系統日誌
- `timestamp`, `level`, `agent_name`, `message`

### daily_metrics 表
每日統計（健康+記帳）
- `date`, `calories_total`, `spending_cash`, `spending_credit`
- `tasks_completed`, `weight_kg`

## 意圖路由機制

兩層分類策略：
1. **關鍵字快速匹配**（信心度 ≥ 0.6 直接分派）
2. **Claude 分類 fallback**（低信心度時用 Claude 判斷意圖）

路由示例：
- 「今天午餐吃了便當 85 元」→ health + accounting
- 「幫我分析 TSLA」→ investment
- 「查經文約翰福音 3:16」→ bible
