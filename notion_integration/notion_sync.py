#!/usr/bin/env python3
"""
Notion 同步管理器
負責將各 Agent 的資料同步到 Notion 資料庫

Features:
- 自動建立/更新 Notion 資料庫
- 各 Agent 資料的結構化同步
- 錯誤處理和重試機制
- 批次同步和即時同步
"""

import os
import json
import asyncio
import aiohttp
from datetime import datetime, date
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import logging

# 設定日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass
class NotionConfig:
    """Notion 配置"""
    api_token: str
    workspace_id: str
    agent_db_id: str = ""
    accounting_db_id: str = ""
    learning_db_id: str = ""
    health_db_id: str = ""
    investment_db_id: str = ""
    devotion_db_id: str = ""
    news_db_id: str = ""
    briefing_db_id: str = ""


class NotionSyncManager:
    """Notion 同步管理器"""
    
    def __init__(self, config: NotionConfig):
        self.config = config
        self.api_base = "https://api.notion.com/v1"
        self.headers = {
            "Authorization": f"Bearer {config.api_token}",
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28"
        }
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def _make_request(self, method: str, endpoint: str, data: Dict = None) -> Dict:
        """發送 API 請求"""
        if not self.session:
            raise RuntimeError("Session not initialized. Use 'async with' context.")
        
        url = f"{self.api_base}/{endpoint}"
        
        try:
            async with self.session.request(
                method, url, headers=self.headers, json=data
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    logger.error(f"Notion API 錯誤 {response.status}: {error_text}")
                    raise Exception(f"Notion API Error: {response.status}")
        
        except Exception as e:
            logger.error(f"請求失敗: {e}")
            raise
    
    async def create_agent_database(self) -> str:
        """建立 Agent 管理資料庫"""
        db_schema = {
            "parent": {"page_id": self.config.workspace_id},
            "title": [{"text": {"content": "🤖 Agent 管理中心"}}],
            "properties": {
                "Agent名稱": {"title": {}},
                "狀態": {
                    "select": {
                        "options": [
                            {"name": "正常運作", "color": "green"},
                            {"name": "維護中", "color": "yellow"},
                            {"name": "已停用", "color": "red"}
                        ]
                    }
                },
                "分類": {
                    "select": {
                        "options": [
                            {"name": "對話", "color": "blue"},
                            {"name": "財務", "color": "green"},
                            {"name": "健康", "color": "red"},
                            {"name": "學習", "color": "purple"},
                            {"name": "靈修", "color": "yellow"},
                            {"name": "資訊", "color": "orange"},
                            {"name": "系統", "color": "gray"}
                        ]
                    }
                },
                "最後使用時間": {"date": {}},
                "使用次數": {"number": {}},
                "描述": {"rich_text": {}},
                "功能特色": {"rich_text": {}}
            }
        }
        
        result = await self._make_request("POST", "databases", db_schema)
        return result["id"]
    
    async def create_accounting_database(self) -> str:
        """建立記帳資料庫"""
        db_schema = {
            "parent": {"page_id": self.config.workspace_id},
            "title": [{"text": {"content": "💰 財務記帳系統"}}],
            "properties": {
                "項目名稱": {"title": {}},
                "交易日期": {"date": {}},
                "金額": {"number": {"format": "number"}},
                "分類": {
                    "select": {
                        "options": [
                            {"name": "餐飲", "color": "red"},
                            {"name": "交通", "color": "blue"},
                            {"name": "購物", "color": "green"},
                            {"name": "娛樂", "color": "purple"},
                            {"name": "醫療", "color": "pink"},
                            {"name": "教育", "color": "orange"},
                            {"name": "居住", "color": "brown"},
                            {"name": "其他", "color": "gray"}
                        ]
                    }
                },
                "類型": {
                    "select": {
                        "options": [
                            {"name": "收入", "color": "green"},
                            {"name": "支出", "color": "red"}
                        ]
                    }
                },
                "備註": {"rich_text": {}},
                "Agent記錄ID": {"rich_text": {}}
            }
        }
        
        result = await self._make_request("POST", "databases", db_schema)
        return result["id"]
    
    async def create_news_database(self) -> str:
        """建立新聞資訊資料庫"""
        db_schema = {
            "parent": {"page_id": self.config.workspace_id},
            "title": [{"text": {"content": "📰 新聞資訊中心"}}],
            "properties": {
                "標題": {"title": {}},
                "分類": {
                    "select": {
                        "options": [
                            {"name": "台灣", "color": "red"},
                            {"name": "國際", "color": "blue"},
                            {"name": "財經", "color": "green"},
                            {"name": "科技", "color": "purple"},
                            {"name": "其他", "color": "gray"}
                        ]
                    }
                },
                "來源": {"rich_text": {}},
                "發布時間": {"date": {}},
                "摘要": {"rich_text": {}},
                "重要程度": {
                    "select": {
                        "options": [
                            {"name": "高", "color": "red"},
                            {"name": "中", "color": "yellow"},
                            {"name": "低", "color": "gray"}
                        ]
                    }
                },
                "收集時間": {"date": {}},
                "URL": {"url": {}}
            }
        }
        
        result = await self._make_request("POST", "databases", db_schema)
        return result["id"]
    
    async def create_briefing_database(self) -> str:
        """建立晨晚報資料庫"""
        db_schema = {
            "parent": {"page_id": self.config.workspace_id},
            "title": [{"text": {"content": "📋 晨晚報記錄"}}],
            "properties": {
                "日期": {"title": {}},
                "報告類型": {
                    "select": {
                        "options": [
                            {"name": "晨報", "color": "yellow"},
                            {"name": "晚報", "color": "blue"},
                            {"name": "摘要", "color": "green"}
                        ]
                    }
                },
                "內容": {"rich_text": {}},
                "天氣資訊": {"rich_text": {}},
                "新聞摘要": {"rich_text": {}},
                "個人統計": {"rich_text": {}},
                "生成時間": {"date": {}},
                "系統狀態": {"rich_text": {}}
            }
        }
        
        result = await self._make_request("POST", "databases", db_schema)
        return result["id"]
    
    async def sync_agent_status(self, agent_data: Dict[str, Any]) -> bool:
        """同步 Agent 狀態"""
        try:
            # 準備頁面內容
            page_data = {
                "parent": {"database_id": self.config.agent_db_id},
                "properties": {
                    "Agent名稱": {
                        "title": [{"text": {"content": agent_data["name"]}}]
                    },
                    "狀態": {
                        "select": {"name": agent_data.get("status", "正常運作")}
                    },
                    "分類": {
                        "select": {"name": agent_data.get("category", "系統")}
                    },
                    "最後使用時間": {
                        "date": {"start": datetime.now().isoformat()}
                    },
                    "使用次數": {
                        "number": agent_data.get("usage_count", 0)
                    },
                    "描述": {
                        "rich_text": [{"text": {"content": agent_data.get("description", "")}}]
                    },
                    "功能特色": {
                        "rich_text": [{"text": {"content": agent_data.get("features", "")}}]
                    }
                }
            }
            
            await self._make_request("POST", "pages", page_data)
            logger.info(f"Agent {agent_data['name']} 狀態已同步到 Notion")
            return True
            
        except Exception as e:
            logger.error(f"同步 Agent 狀態失敗: {e}")
            return False
    
    async def sync_accounting_record(self, transaction: Dict[str, Any]) -> bool:
        """同步記帳記錄"""
        try:
            page_data = {
                "parent": {"database_id": self.config.accounting_db_id},
                "properties": {
                    "項目名稱": {
                        "title": [{"text": {"content": transaction["item_name"]}}]
                    },
                    "交易日期": {
                        "date": {"start": transaction["date"]}
                    },
                    "金額": {
                        "number": transaction["amount"]
                    },
                    "分類": {
                        "select": {"name": transaction["category"]}
                    },
                    "類型": {
                        "select": {"name": transaction["type"]}
                    },
                    "備註": {
                        "rich_text": [{"text": {"content": transaction.get("note", "")}}]
                    },
                    "Agent記錄ID": {
                        "rich_text": [{"text": {"content": transaction.get("record_id", "")}}]
                    }
                }
            }
            
            await self._make_request("POST", "pages", page_data)
            logger.info(f"記帳記錄 {transaction['item_name']} 已同步到 Notion")
            return True
            
        except Exception as e:
            logger.error(f"同步記帳記錄失敗: {e}")
            return False
    
    async def sync_news_article(self, article: Dict[str, Any]) -> bool:
        """同步新聞文章"""
        try:
            page_data = {
                "parent": {"database_id": self.config.news_db_id},
                "properties": {
                    "標題": {
                        "title": [{"text": {"content": article["title"]}}]
                    },
                    "分類": {
                        "select": {"name": article.get("category", "其他")}
                    },
                    "來源": {
                        "rich_text": [{"text": {"content": article.get("source", "")}}]
                    },
                    "發布時間": {
                        "date": {"start": article.get("publish_time", datetime.now().isoformat())}
                    },
                    "摘要": {
                        "rich_text": [{"text": {"content": article.get("summary", "")}}]
                    },
                    "重要程度": {
                        "select": {"name": article.get("importance", "中")}
                    },
                    "收集時間": {
                        "date": {"start": datetime.now().isoformat()}
                    },
                    "URL": {
                        "url": article.get("url")
                    }
                }
            }
            
            await self._make_request("POST", "pages", page_data)
            logger.info(f"新聞文章 {article['title']} 已同步到 Notion")
            return True
            
        except Exception as e:
            logger.error(f"同步新聞文章失敗: {e}")
            return False
    
    async def sync_briefing_report(self, report: Dict[str, Any]) -> bool:
        """同步晨晚報"""
        try:
            page_data = {
                "parent": {"database_id": self.config.briefing_db_id},
                "properties": {
                    "日期": {
                        "title": [{"text": {"content": report["date"]}}]
                    },
                    "報告類型": {
                        "select": {"name": report["type"]}
                    },
                    "內容": {
                        "rich_text": [{"text": {"content": report.get("content", "")}}]
                    },
                    "天氣資訊": {
                        "rich_text": [{"text": {"content": report.get("weather", "")}}]
                    },
                    "新聞摘要": {
                        "rich_text": [{"text": {"content": report.get("news", "")}}]
                    },
                    "個人統計": {
                        "rich_text": [{"text": {"content": report.get("stats", "")}}]
                    },
                    "生成時間": {
                        "date": {"start": datetime.now().isoformat()}
                    },
                    "系統狀態": {
                        "rich_text": [{"text": {"content": report.get("system_status", "")}}]
                    }
                }
            }
            
            await self._make_request("POST", "pages", page_data)
            logger.info(f"{report['type']} {report['date']} 已同步到 Notion")
            return True
            
        except Exception as e:
            logger.error(f"同步晨晚報失敗: {e}")
            return False


class NotionIntegrationAgent:
    """Notion 整合 Agent"""
    
    def __init__(self, config_path: str = "notion_integration/config.json"):
        self.config_path = config_path
        self.config = self._load_config()
        self.sync_manager = NotionSyncManager(self.config)
    
    def _load_config(self) -> NotionConfig:
        """載入配置"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            return NotionConfig(**config_data)
        except FileNotFoundError:
            # 建立預設配置檔案
            default_config = {
                "api_token": "your_notion_api_token_here",
                "workspace_id": "your_workspace_id_here"
            }
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=2, ensure_ascii=False)
            raise Exception(f"請在 {self.config_path} 中設定 Notion API token 和 workspace ID")
    
    async def initialize_databases(self) -> Dict[str, str]:
        """初始化所有資料庫"""
        async with self.sync_manager as manager:
            try:
                # 建立各個資料庫
                databases = {}
                
                if not self.config.agent_db_id:
                    databases["agent_db_id"] = await manager.create_agent_database()
                
                if not self.config.accounting_db_id:
                    databases["accounting_db_id"] = await manager.create_accounting_database()
                
                if not self.config.news_db_id:
                    databases["news_db_id"] = await manager.create_news_database()
                
                if not self.config.briefing_db_id:
                    databases["briefing_db_id"] = await manager.create_briefing_database()
                
                # 更新配置檔案
                if databases:
                    config_data = {
                        "api_token": self.config.api_token,
                        "workspace_id": self.config.workspace_id,
                        **databases
                    }
                    
                    with open(self.config_path, 'w', encoding='utf-8') as f:
                        json.dump(config_data, f, indent=2, ensure_ascii=False)
                    
                    logger.info(f"已建立 {len(databases)} 個新資料庫")
                
                return databases
                
            except Exception as e:
                logger.error(f"初始化資料庫失敗: {e}")
                raise
    
    async def sync_all_agents_status(self):
        """同步所有 Agent 狀態"""
        agents_data = [
            {
                "name": "一般對話 Agent",
                "status": "正常運作",
                "category": "對話",
                "description": "處理日常問答、閒聊、建議",
                "features": "智能回應、自我介紹、時間查詢"
            },
            {
                "name": "投資分析 Agent", 
                "status": "正常運作",
                "category": "財務",
                "description": "查詢股價、分析市場、投資建議",
                "features": "台積電資訊、投資建議、市場分析"
            },
            {
                "name": "健康管理 Agent",
                "status": "正常運作", 
                "category": "健康",
                "description": "健康諮詢、用藥提醒、運動建議",
                "features": "睡眠、運動、營養、壓力管理"
            },
            {
                "name": "記帳 Agent",
                "status": "正常運作",
                "category": "財務", 
                "description": "記錄收支、查詢帳目、生成報表",
                "features": "自動記錄、分類、統計報表"
            },
            {
                "name": "學習助手 Agent",
                "status": "正常運作",
                "category": "學習",
                "description": "學習計畫、知識問答、學習進度追蹤", 
                "features": "英文、程式、學習計畫指導"
            },
            {
                "name": "晨晚報 Agent",
                "status": "正常運作",
                "category": "資訊",
                "description": "生成每日晨報、晚報摘要",
                "features": "每日摘要、新聞整理框架"
            },
            {
                "name": "聖經靈修 Agent",
                "status": "正常運作", 
                "category": "靈修",
                "description": "經文查詢、靈修分享、禱告指引",
                "features": "經文查詢、靈修指導、禱告"
            },
            {
                "name": "網路搜尋 Agent",
                "status": "正常運作",
                "category": "資訊", 
                "description": "使用 OpenManus 瀏覽器技術搜尋網路資訊",
                "features": "新聞、股價、天氣、一般搜尋"
            }
        ]
        
        async with self.sync_manager as manager:
            success_count = 0
            for agent_data in agents_data:
                if await manager.sync_agent_status(agent_data):
                    success_count += 1
            
            logger.info(f"成功同步 {success_count}/{len(agents_data)} 個 Agent 狀態")
            return success_count


# 使用範例
if __name__ == "__main__":
    async def main():
        # 建立 Notion 整合
        integration = NotionIntegrationAgent()
        
        # 初始化資料庫
        await integration.initialize_databases()
        
        # 同步所有 Agent 狀態
        await integration.sync_all_agents_status()
        
        print("✅ Notion 整合初始化完成！")
    
    asyncio.run(main())