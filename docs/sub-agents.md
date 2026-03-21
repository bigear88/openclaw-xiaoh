# 小虹多Agent系統 — Sub-Agent 清單與技能

> 來源：Notion「🤖 小虹多Agent系統 — Sub-Agent 清單與技能」

## Agent 總覽

每個 Sub-Agent 繼承 `BaseAgent` 基底類別，具備：
- `call_claude(prompt)` — 呼叫 Claude Code CLI
- `take_screenshot(url, path)` — Playwright 截圖
- `send_telegram(text, photo)` — 發送 Telegram 訊息
- 自動心跳回報、任務狀態追蹤

---

## 💰 記帳 Agent (accounting)

**功能：**
- 記錄日常消費，區分現金/信用卡
- 查詢店家地址，附上照片
- 雲端發票查詢
- 每日消費統計（併入晚報）

**意圖關鍵字：** 記帳、花了、消費、買了、付了、刷卡、現金、發票、支出

---

## 📈 投資分析 Agent (investment)

**功能：**
- Mark Minervini SEPA 動量分析
- CANSLIM 方法篩選
- 美股選擇權策略建議
- 加密貨幣追蹤（BTC、MSTR、IBIT ETF）
- SCTR 技術排名
- Finviz 熱力圖截圖
- StockTwits 熱門標的
- Trading Logic 儀表板截圖

**意圖關鍵字：** 股市、美股、台股、加密貨幣、BTC、MSTR、IBIT、技術分析、SCTR、Minervini、CANSLIM、選擇權

---

## 🥗 健康管理 Agent (health)

**功能：**
- 記錄每日飲食（早/午/晚餐+點心）
- 分析卡路里和營養素
  - 目標：蛋白質 140g、脂肪 60g、碳水 180g、總熱量 1,800 大卡
- 照片辨識食物，自動估算熱量
- 追蹤減重目標（88.5kg → 65kg）
- 提供飲食建議和營養師討論重點

**意圖關鍵字：** 吃了、早餐、午餐、晚餐、點心、喝了、卡路里、熱量、體重、減重

---

## 📚 學習 Agent (learning)

**功能：**
- LiveABC 閱讀進度追蹤
- TOEIC 多益準備
- 每兩週一本書閱讀計畫
- 碩士論文進度追蹤

**意圖關鍵字：** LiveABC、閱讀、讀書進度、TOEIC、多益、英文、論文

---

## 📰 新聞截圖 Agent (newspaper)

**功能：**
- 每日 07:30 自動截圖 Hami 書城報紙首頁
- 使用 Playwright 模擬瀏覽器操作
- 截圖發送到 Telegram

**意圖關鍵字：** 報紙、新聞截圖、Hami、頭版

---

## 🌅 晨報/晚報 Agent (briefing)

**每天 08:00 晨報內容：**
- 當天日期農曆、節日節氣
- Gmail 未讀摘要
- 市場概覽（美股/台股/加密貨幣）
- 圖文並茂技術分析圖（Finviz 熱力圖、SCTR 排名、Trading Logic 儀表板、六大指數技術分析）
- 每日任務提醒

**每天 20:00 晚報內容：**
- 今日回顧
- Gmail 摘要
- 市場收盤總結
- 今日消費統計（現金/信用卡分開）
- 今日飲食卡路里統計（vs 1,800 大卡目標）
- 營養素達成率
- 減重進度追蹤
- 明日預告

**意圖關鍵字：** 晨報、早安報告、晚報、晚安報告、今日回顧

---

## ✝️ 靈修 Agent (bible)

**功能：**
- FHL 信望愛聖經經文查詢
- 原文研究（希臘文/希伯來文）
- 每日靈修
- 講道筆記（搭配漢王語音轉寫 + 心智圖生成）

**意圖關鍵字：** 經文、聖經、靈修、FHL、原文、希臘文、希伯來文、講道、主日

---

## ✨ 通用 Agent (general)

**功能：**
- Notion 頁面建立與更新
- Gmail 郵件摘要
- 文件翻譯與撰寫
- YouTube 影片轉 Notion 筆記
- Canva 設計
- 漢王語音轉寫
- 其他雜項任務

**意圖關鍵字：** Notion、Gmail、郵件、翻譯、任何不屬於特定領域的任務
