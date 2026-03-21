# base_agent.py 修改說明

## 修改範圍
僅修改 `shared/base_agent.py` 的 `send_telegram()` 方法，加入 MCP 模式判斷。

## 修改內容

在 `send_telegram()` 方法最前面加入：

```python
async def send_telegram(self, text, photo_path=None):
    # MCP 模式：不自己發 Telegram（由 OpenClaw 統一處理）
    if os.environ.get("XIAOHONG_MODE") == "mcp":
        return
    # ...原始碼不變
```

## 為什麼需要這個修改

在 MCP 架構中，agents 的回覆會透過 MCP 協議回傳給 OpenClaw，
由 OpenClaw 統一透過 Telegram 頻道發送。如果 agent 自己又發一次 Telegram，
使用者就會收到重複訊息。

## 確認事項

- `import os` 已存在於檔案頂部（確認）
- 不影響非 MCP 模式的運作（`XIAOHONG_MODE` 未設定時走原始流程）
- 修改行數：2 行
