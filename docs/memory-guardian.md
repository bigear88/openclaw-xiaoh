# 小虹多Agent系統 — 記憶體保護與 VM 穩定性

> 來源：Notion「🛡️ 小虹多Agent系統 — 記憶體保護與 VM 穩定性」

## 問題背景

之前安裝 OpenClaw 時，過多 agents 同時運行導致記憶體耗盡，Linux OOM Killer 觸發後 VirtualBox VM 完全 hang 住不能動。根本原因：
- 沒有 swap，記憶體用完就直接觸發 OOM
- OOM Killer 隨機殺 process，可能殺到關鍵服務
- 沒有應用層的記憶體監控

## 五層防護機制

### 第 1 層：Swap File（VM 必備）
- 建立 4GB swap file
- swappiness=60（適中）
- swap 是 VM hang 住前的最後緩衝

### 第 2 層：earlyoom
- Linux 工具，在 RAM < 5% 或 Swap < 10% 時提前殺進程
- 優先殺 chromium/playwright（最吃記憶體）
- 保護 sshd/nginx/systemd
- 比 kernel OOM Killer 更早、更可控

### 第 3 層：systemd 資源限制
- 主服務 MemoryMax=70%，CPUQuota=300%，TasksMax=50
- Dashboard MemoryMax=512MB
- OOMScoreAdjust 保護關鍵服務

### 第 4 層：Memory Guardian（應用層）

每 10 秒檢查一次 RAM 使用率：

| 等級 | 觸發 | 動作 |
|------|------|------|
| Level 0 | < 70% | 正常運作 |
| Level 1 | 70-85% | 暫停閒置 agents，新任務排隊 |
| Level 2 | 85-92% | 強制終止非關鍵 agents，殺 Playwright |
| Level 3 | > 92% | 殺光所有 agents，Telegram 緊急警報 |

### 第 5 層：Agent 優先級

記憶體不足時按優先級從低到高終止：

| 優先級 | Agent | 說明 |
|--------|-------|------|
| 1 (最高) | briefing | 每日晨晚報，最後被殺 |
| 2 | general | 回覆使用者 |
| 3 | accounting | 記帳 |
| 4 | investment | 投資分析 |
| 5 | health | 健康管理 |
| 6 | bible | 靈修 |
| 7 | learning | 學習 |
| 8 (最低) | newspaper | 截圖最吃記憶體，最先犧牲 |

## Playwright 特別處理

Playwright + Chromium 是最大的記憶體殺手（每個實例 ~200-400MB）：
- 同時最多 1 個 Playwright 實例
- Level 1 就禁止新的 Playwright
- Level 2 直接殺所有 Chromium 進程
- 單一 agent 記憶體上限 800MB

## VirtualBox 特別設定

- RAM 建議：12-16 GB
- 一定要有 swap（沒有 swap 的 VM 最容易 hang）
- VirtualBox Settings → System → 不要勾 "Enable EFI"（減少額外開銷）

## 監控指令

```bash
# 查看記憶體
free -h

# 查看 swap
swapon --show

# 查看 earlyoom 狀態
sudo systemctl status earlyoom

# 查看各服務記憶體用量
systemctl status xiaohong --no-pager

# 查看記憶體前 10 大進程
ps aux --sort=-%mem | head -10
```
