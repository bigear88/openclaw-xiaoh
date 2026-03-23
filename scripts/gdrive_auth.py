#!/usr/bin/env python3
"""Google Drive OAuth 授權腳本 — 使用 local server"""
import json, os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive',
]
CREDS_PATH = '/home/curtis/xiaohong/credentials.json'
TOKEN_PATH = '/home/curtis/xiaohong/token.json'

flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
print('啟動本地授權伺服器 port 8090...')
print('請在瀏覽器開啟: http://localhost:8090')
print()
creds = flow.run_local_server(port=8090, open_browser=False)
with open(TOKEN_PATH, 'w') as f:
    f.write(creds.to_json())
print(f'授權成功！Scopes: {creds.scopes}')

from googleapiclient.discovery import build
service = build('drive', 'v3', credentials=creds)
about = service.about().get(fields='user').execute()
print(f'Drive 帳號: {about["user"]["emailAddress"]}')
