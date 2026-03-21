# 小虹多Agent系統 — 整體架構與VM規劃

> 來源：Notion「🏗️ 小虹多Agent系統 — 整體架構與VM規劃」

## 系統總覽

小虹秘書大管家是一套運行於 Ubuntu 24.04 LTS (VirtualBox VM) 的多 Agent AI 系統，以 Claude Code CLI 為核心引擎，透過 Telegram Bot 接收指令，自動分派給專屬 Sub-Agent 處理各類任務。

## VM 環境規格

| 項目 | 設定值 |
|------|--------|
| 作業系統 | Ubuntu 24.04 LTS Server（不裝桌面） |
| CPU | 4 核心 |
| RAM | 8 GB（最少 4GB） |
| 磁碟 | 60 GB 動態配置 |
| 網路 | Bridged Adapter（取得區網 IP） |
| Port Forwarding 備用 | Host 2222→Guest 22, Host 8080→Guest 8080 |

**選擇 Ubuntu 24.04 LTS Server 的理由：**
- LTS 支援至 2029 年
- Server 版記憶體佔用低（~512MB vs Desktop 2GB+）
- 所有操作透過 SSH + Web Dashboard，不需 GUI
- 內建 systemd 方便管理服務

## 架構模式：Orchestrator + 獨立 Sub-Agent

```
Telegram / Notion / Cron
        │
        ▼
┌─────────────────────────┐
│   Orchestrator (main)   │ ← 接收訊息 → 意圖分類 → 分派
└─────────┬───────────────┘
          │
┌─────────▼───────────────┐
│  Agent Process Manager  │ ← 管理所有 sub-agent 生命週期
└─────────┬───────────────┘
          │
  ┌───┬───┼───┬───┬───┬───┬───┐
  ▼   ▼   ▼   ▼   ▼   ▼   ▼   ▼
 記帳 投資 健康 學習 新聞 晨晚報 靈修 通用
```

**設計決策：每個 Agent 獨立 Process**
- 優點：完全隔離，一個 agent 崩潰不影響其他
- 每個 agent 有獨立 Claude Code CLI session
- 心跳回報機制確保健康狀態
- 透過 SQLite 任務佇列協調

## 外部介面

1. **Telegram Bot** — 主要互動介面，長輪詢接收訊息
2. **Notion** — 持久化資料層，秘書的大腦
3. **Web Dashboard** — FastAPI + React 即時監控
4. **Cron Scheduler** — 定時觸發晨報/晚報/報紙截圖

## 技術棧

| 元件 | 技術 |
|------|------|
| AI 引擎 | Claude Code CLI |
| 程式語言 | Python 3.12 |
| Web 框架 | FastAPI + WebSocket |
| 前端 | React（CDN 模式） |
| 資料庫 | SQLite（本地狀態） + Notion（持久化） |
| 截圖 | Playwright + Chromium |
| TTS | Edge TTS (zh-TW-HsiaoChenNeural) |
| 排程 | APScheduler / asyncio 排程器 |
| 服務管理 | systemd |
| 日誌 | loguru |
