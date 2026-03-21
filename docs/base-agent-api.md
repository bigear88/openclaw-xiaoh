# 小虹多Agent系統 — BaseAgent 基底類別 API

> 來源：Notion「🧩 小虹多Agent系統 — BaseAgent 基底類別 API」

## BaseAgent 類別說明

所有 Sub-Agent 繼承此基底類別，提供統一的任務執行框架。

## 子類必須覆寫的屬性

- `name: str` — Agent 唯一名稱（如 "accounting"）
- `description: str` — Agent 描述
- `system_prompt: str` — Claude Code 系統提示（可選覆寫，預設小虹人格）

## 子類必須實作的方法

### `async handle(self, intent: str, data: Dict) -> Any`

- `intent`: 意圖字串（如 "record_expense"、"morning_briefing"）
- `data`: 輸入資料字典（通常包含 text, chat_id）
- 回傳值存入 task.output_data

## 內建工具方法

### `async call_claude(prompt, work_dir=None) -> str`

呼叫 Claude Code CLI 執行任務
- 獨立 process，使用 `--print --output-format text`
- 逾時由 `config.agents.task_timeout` 控制（預設 300s）
- work_dir 預設為 `agents/{agent_name}/`

### `async take_screenshot(url, output_path, **kwargs) -> str`

Playwright 截圖
- `width`: 視窗寬度（預設 1280）
- `height`: 視窗高度（預設 720）
- `wait_ms`: 等待毫秒數（預設 3000）
- `selector`: CSS 選擇器（只截取特定元素）

### `async send_telegram(text, photo_path=None)`

發送訊息到 Telegram
- 支援純文字和圖片
- 自動使用 config 中的 bot_token 和 admin_chat_id

### MCP 模式修改

在 MCP 模式下（`XIAOHONG_MODE=mcp`），`send_telegram()` 會直接 return，
不自己發送 Telegram，由 OpenClaw 統一處理輸出。

```python
async def send_telegram(self, text, photo_path=None):
    if os.environ.get("XIAOHONG_MODE") == "mcp":
        return
    # ...原始碼不變
```

## 任務執行生命週期

`execute(task)` 方法自動管理：

1. 更新 agent 狀態為 running
2. 更新 task 狀態為 running
3. 啟動心跳 loop
4. 呼叫 `handle()` 執行實際邏輯
5. 成功 → task 狀態 completed
6. 失敗 → task 狀態 error + 記錄錯誤訊息
7. 恢復 agent 狀態為 idle

## 新增 Agent 範例

```python
from shared.base_agent import BaseAgent

class MyAgent(BaseAgent):
    name = "my_agent"
    description = "我的自訂 Agent"

    async def handle(self, intent, data):
        text = data.get("text", "")
        result = await self.call_claude(f"處理: {text}")
        await self.send_telegram(result)
        return result
```
