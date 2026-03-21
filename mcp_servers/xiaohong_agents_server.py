#!/usr/bin/env python3
"""
小虹 MCP Bridge Server
將 7 個 Python agents 包裝為 MCP 工具，供 OpenClaw 閘道器呼叫。

Features:
- Agent 延遲載入（lazy-load）節省記憶體
- Memory Guardian 整合：每次呼叫前檢查記憶體
- 統一錯誤處理
"""

import asyncio
import gc
import json
import os
import sys
import psutil
from typing import Any, Optional

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# 設定 MCP 模式，讓 agents 不自己發 Telegram
os.environ["XIAOHONG_MODE"] = "mcp"

# ============================================================
# Memory Guardian（簡化版，整合至 MCP Bridge）
# ============================================================

class MemoryGuardian:
    """五層記憶體保護機制"""

    LEVELS = {
        1: {"threshold": 60, "action": "log"},
        2: {"threshold": 70, "action": "gc"},
        3: {"threshold": 80, "action": "pause_low_priority"},
        4: {"threshold": 90, "action": "pause_all"},
        5: {"threshold": 95, "action": "emergency_kill"},
    }

    AGENT_PRIORITY = {
        "general_agent": 5,      # 最高優先
        "briefing_agent": 4,
        "investment_agent": 4,
        "health_agent": 3,
        "bible_agent": 3,
        "accounting_agent": 2,
        "learning_agent": 2,
    }

    def __init__(self):
        self.paused_agents: set[str] = set()
        self.alert_cooldown: dict[str, float] = {}

    def get_memory_percent(self) -> float:
        return psutil.virtual_memory().percent

    def get_current_level(self) -> int:
        pct = self.get_memory_percent()
        level = 0
        for lvl, info in self.LEVELS.items():
            if pct >= info["threshold"]:
                level = lvl
        return level

    async def check_and_protect(self) -> dict[str, Any]:
        """檢查記憶體並觸發對應保護動作"""
        pct = self.get_memory_percent()
        level = self.get_current_level()
        result = {"memory_percent": pct, "level": level, "action": "none"}

        if level >= 2:
            gc.collect()
            result["action"] = "gc"

        if level >= 3:
            # 暫停低優先級 agents
            for name, priority in self.AGENT_PRIORITY.items():
                if priority <= 2:
                    self.paused_agents.add(name)
            result["action"] = "pause_low_priority"
            result["paused"] = list(self.paused_agents)

        if level >= 4:
            # 暫停全部
            self.paused_agents = set(self.AGENT_PRIORITY.keys())
            result["action"] = "pause_all"

        return result

    def can_start_agent(self, agent_name: str) -> bool:
        """判斷是否允許啟動指定 agent"""
        if agent_name in self.paused_agents:
            return False
        pct = self.get_memory_percent()
        if pct > 85:
            priority = self.AGENT_PRIORITY.get(agent_name, 1)
            return priority >= 4  # 只允許高優先級
        return True

    def resume_all(self):
        self.paused_agents.clear()

    def status(self) -> dict[str, Any]:
        mem = psutil.virtual_memory()
        return {
            "memory_percent": mem.percent,
            "memory_used_gb": round(mem.used / (1024**3), 2),
            "memory_total_gb": round(mem.total / (1024**3), 2),
            "memory_available_gb": round(mem.available / (1024**3), 2),
            "guardian_level": self.get_current_level(),
            "paused_agents": list(self.paused_agents),
            "cpu_percent": psutil.cpu_percent(interval=0.5),
        }


# ============================================================
# Agent Lazy Loader
# ============================================================

class AgentLoader:
    """延遲載入 agents，避免啟動時全部載入佔用記憶體"""

    AGENT_MAP = {
        "general_agent": ("agents.general_agent", "GeneralAgent"),
        "investment_agent": ("agents.investment_agent", "InvestmentAgent"),
        "health_agent": ("agents.health_agent", "HealthAgent"),
        "accounting_agent": ("agents.accounting_agent", "AccountingAgent"),
        "learning_agent": ("agents.learning_agent", "LearningAgent"),
        "briefing_agent": ("agents.briefing_agent", "BriefingAgent"),
        "bible_agent": ("agents.bible_agent", "BibleAgent"),
    }

    def __init__(self):
        self._instances: dict[str, Any] = {}

    def get_agent(self, name: str) -> Any:
        """取得 agent 實例，首次呼叫時才載入"""
        if name not in self._instances:
            if name not in self.AGENT_MAP:
                raise ValueError(f"Unknown agent: {name}")
            module_path, class_name = self.AGENT_MAP[name]
            import importlib
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            self._instances[name] = agent_class()
        return self._instances[name]

    def unload_agent(self, name: str):
        """卸載指定 agent 釋放記憶體"""
        if name in self._instances:
            del self._instances[name]
            gc.collect()

    def loaded_agents(self) -> list[str]:
        return list(self._instances.keys())


# ============================================================
# MCP Server 定義
# ============================================================

guardian = MemoryGuardian()
loader = AgentLoader()
server = Server("xiaohong-agents")


# 定義所有 MCP 工具
TOOLS = [
    Tool(
        name="general_agent",
        description="小虹一般對話 agent。處理日常問答、閒聊、建議。輸入：使用者訊息文字。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "使用者的訊息內容"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="investment_agent",
        description="投資分析 agent。查詢股價、分析市場、投資建議。輸入：投資相關問題。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "投資相關問題"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="health_agent",
        description="健康管理 agent。健康諮詢、用藥提醒、運動建議。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "健康相關問題"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="accounting_agent",
        description="記帳 agent。記錄收支、查詢帳目、生成報表。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "記帳相關指令或問題"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="learning_agent",
        description="學習助手 agent。學習計畫、知識問答、學習進度追蹤。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "學習相關問題"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="briefing_agent",
        description="晨晚報 agent。生成每日晨報、晚報摘要。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "報告指令，如「晨報」「晚報」"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="bible_agent",
        description="聖經靈修 agent。經文查詢、靈修分享、禱告指引。",
        inputSchema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "聖經或靈修相關問題"}
            },
            "required": ["message"],
        },
    ),
    Tool(
        name="system_status",
        description="查詢系統狀態：CPU、記憶體、磁碟使用量、已載入 agents。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="memory_control",
        description="記憶體管理控制。可執行：status / gc / pause_all / resume_all / unload <agent>",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "gc", "pause_all", "resume_all", "unload"],
                    "description": "要執行的動作",
                },
                "agent_name": {
                    "type": "string",
                    "description": "unload 時指定 agent 名稱",
                },
            },
            "required": ["action"],
        },
    ),
]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """處理工具呼叫，整合 Memory Guardian"""

    # 系統工具不受 Memory Guardian 限制
    if name == "system_status":
        status = guardian.status()
        status["loaded_agents"] = loader.loaded_agents()
        return [TextContent(type="text", text=json.dumps(status, ensure_ascii=False, indent=2))]

    if name == "memory_control":
        return await _handle_memory_control(arguments)

    # Agent 呼叫前：Memory Guardian 檢查
    protection = await guardian.check_and_protect()
    if protection["level"] >= 4:
        return [TextContent(
            type="text",
            text=f"⚠️ 記憶體使用量過高 ({protection['memory_percent']:.1f}%)，"
                 f"所有 agents 已暫停。請稍後再試或執行 memory_control(action='gc')。"
        )]

    if not guardian.can_start_agent(name):
        return [TextContent(
            type="text",
            text=f"⚠️ {name} 目前被暫停（記憶體保護 Level {protection['level']}）。"
                 f"記憶體使用：{protection['memory_percent']:.1f}%"
        )]

    # 載入並呼叫 agent
    try:
        agent = loader.get_agent(name)
        message = arguments.get("message", "")
        result = await agent.process(message)
        return [TextContent(type="text", text=str(result))]
    except Exception as e:
        return [TextContent(
            type="text",
            text=f"❌ {name} 執行錯誤：{type(e).__name__}: {e}"
        )]


async def _handle_memory_control(arguments: dict[str, Any]) -> list[TextContent]:
    """處理記憶體管理指令"""
    action = arguments.get("action", "status")

    if action == "status":
        status = guardian.status()
        status["loaded_agents"] = loader.loaded_agents()
        return [TextContent(type="text", text=json.dumps(status, ensure_ascii=False, indent=2))]

    elif action == "gc":
        gc.collect()
        status = guardian.status()
        return [TextContent(type="text", text=f"✅ GC 完成。記憶體：{status['memory_percent']:.1f}%")]

    elif action == "pause_all":
        guardian.paused_agents = set(AgentLoader.AGENT_MAP.keys())
        return [TextContent(type="text", text="⏸️ 所有 agents 已暫停")]

    elif action == "resume_all":
        guardian.resume_all()
        return [TextContent(type="text", text="▶️ 所有 agents 已恢復")]

    elif action == "unload":
        agent_name = arguments.get("agent_name")
        if agent_name:
            loader.unload_agent(agent_name)
            return [TextContent(type="text", text=f"🗑️ {agent_name} 已卸載")]
        return [TextContent(type="text", text="❌ 請指定 agent_name")]

    return [TextContent(type="text", text=f"❌ 未知動作：{action}")]


# ============================================================
# Main
# ============================================================

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
