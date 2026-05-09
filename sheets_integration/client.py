"""
Google Sheets API クライアント
gspread を使って読み書きする
"""
import logging
import gspread
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceCredentials
from config import settings

logger = logging.getLogger(__name__)

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gc = None


def get_sheets_client() -> gspread.Client:
    """
    Google Sheets クライアントを返す
    OAuth2 token.json または service account credentials.json を使用する
    """
    global _gc
    if _gc is not None:
        return _gc

    import os

    # サービスアカウントJSONがある場合 (本番推奨)
    service_account_file = "service_account.json"
    if os.path.exists(service_account_file):
        creds = ServiceCredentials.from_service_account_file(
            service_account_file, scopes=SHEETS_SCOPES
        )
        _gc = gspread.authorize(creds)
        return _gc

    # OAuth2 token.json (開発用・Gmail認証と共用)
    if os.path.exists(settings.gmail_token_file):
        creds = Credentials.from_authorized_user_file(
            settings.gmail_token_file, SHEETS_SCOPES
        )
        _gc = gspread.authorize(creds)
        return _gc

    raise FileNotFoundError(
        "Google Sheets の認証ファイルが見つかりません。\n"
        "service_account.json または token.json を配置してください。"
    )


def get_or_create_spreadsheet(title: str = "Sales Outreach 管理表") -> gspread.Spreadsheet:
    """スプレッドシートを取得または新規作成する"""
    gc = get_sheets_client()

    if settings.sheets_spreadsheet_id:
        return gc.open_by_key(settings.sheets_spreadsheet_id)

    # 既存の同名シートを探す
    try:
        return gc.open(title)
    except gspread.SpreadsheetNotFound:
        pass

    # 新規作成
    ss = gc.create(title)
    logger.info(f"スプレッドシートを作成しました: {ss.url}")
    return ss


def get_or_create_worksheet(
    ss: gspread.Spreadsheet, tab_name: str, headers: list[str]
) -> gspread.Worksheet:
    """ワークシートを取得または作成する"""
    try:
        ws = ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
        ws.append_row(headers)
        logger.info(f"ワークシートを作成しました: {tab_name}")
    return ws


def is_sheets_configured() -> bool:
    import os
    return (
        os.path.exists("service_account.json")
        or os.path.exists(settings.gmail_token_file)
    )
