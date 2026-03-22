#!/usr/bin/env python3
"""Telethon 首次登入腳本 — 互動式輸入驗證碼"""
import asyncio
import os
from telethon import TelegramClient

API_ID = 34771850
API_HASH = '9bb71c5f76124a7bab906d9333d77b51'
SESSION_DIR = '/home/curtis/openclaw-xiaoh/data'
SESSION_NAME = 'xiaohong_user'

async def main():
    session_path = os.path.join(SESSION_DIR, SESSION_NAME)
    client = TelegramClient(session_path, API_ID, API_HASH)
    
    print('正在連線到 Telegram...')
    await client.start()
    
    me = await client.get_me()
    print(f'登入成功！')
    print(f'  名稱: {me.first_name} {me.last_name or ""}')
    print(f'  帳號: @{me.username}')
    print(f'  ID: {me.id}')
    print(f'  Session 已保存: {session_path}.session')
    
    # 測試讀取頻道
    dialogs = await client.get_dialogs()
    channels = [d for d in dialogs if d.is_channel]
    unread = [d for d in channels if d.unread_count > 0]
    print(f'  總頻道數: {len(channels)}')
    print(f'  有未讀的頻道: {len(unread)}')
    
    if unread:
        total_unread = sum(d.unread_count for d in unread)
        print(f'  總未讀數: {total_unread}')
        print('  未讀頻道前 10:')
        for d in sorted(unread, key=lambda x: x.unread_count, reverse=True)[:10]:
            print(f'    {d.name}: {d.unread_count} 則')
    
    await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
