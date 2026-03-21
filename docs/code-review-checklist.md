# 小虹多Agent系統 — OpenClaw 遷移：程式碼審閱清單

> 來源：Notion「📝 小虹多Agent系統 — OpenClaw 遷移：程式碼審閱清單」

## GitHub 程式碼審閱清單

以下是需要審閱的所有程式碼，按重要性排序。

---

### ❶ MCP Bridge Server（最重要）

**檔案：** `mcp_servers/xiaohong_agents_server.py`

**審閱重點：**
- Memory Guardian 整合是否正確（每次呼叫前檢查）
- Agent 延遲載入邏輯
- MCP 工具描述是否涵蓋所有場景
- `XIAOHONG_MODE=mcp` 環境變數設定
- 錯誤處理是否完善

**關鍵段落：**
- `AgentLoader.get_agent()` — 延遲載入，避免啟動時全部載入
- `call_tool()` — 呼叫前做 `guardian.check_and_protect()` + `guardian.can_start_agent()`
- `memory_control` 工具 — pause_all / resume_all / gc / status

---

### ❷ base_agent.py 修改

**檔案：** `shared/base_agent.py`（現有檔案）

**修改範圍極小：** 只修改 `send_telegram()` 方法

```python
# 加在方法最前面
if os.environ.get("XIAOHONG_MODE") == "mcp":
    return
```

**審閱重點：**
- 確認不會影響非 MCP 模式的運作
- 確認 `import os` 已存在

---

### ❸ OpenClaw 設定檔

**檔案：** `openclaw-config/openclaw.json`

**審閱重點：**
- 3 個 MCP server 路徑是否正確
- Python venv 路徑
- customInstructions 小虹人格是否符合期望
- permissions 白名單/黑名單

---

### ❹ 系統監控 Daemon

**檔案：** `mcp_servers/system_monitor_daemon.py`

**審閱重點：**
- 30 秒檢查間隔是否合適
- 80% 警報閾值
- 5 分鐘 cooldown 避免重複警報
- Telegram 警報格式

---

### ❺ 部署腳本

**檔案：** `openclaw-config/deploy-openclaw.sh`

**審閱重點：**
- systemd service 的 MemoryMax 限制
- cron 排程時間（07:30 / 08:00 / 20:00）
- OpenManus git clone URL 是否正確
- Node.js 22 vs 20 的選擇
