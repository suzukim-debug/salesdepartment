"""
Gmail API OAuth2 認証
初回は browser で認証し token.json に保存する
2回目以降は token.json から自動的に認証する
"""
import os
import logging
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_gmail_credentials() -> Credentials:
    """
    Gmail API用のOAuth2クレデンシャルを取得する
    初回のみブラウザ認証が必要（CLIから python -m gmail_integration.auth で実行）
    """
    creds = None

    if os.path.exists(settings.gmail_token_file):
        creds = Credentials.from_authorized_user_file(settings.gmail_token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(settings.gmail_credentials_file):
                raise FileNotFoundError(
                    f"credentials.json が見つかりません: {settings.gmail_credentials_file}\n"
                    "Google Cloud Console から OAuth2クライアントIDをダウンロードして配置してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                settings.gmail_credentials_file, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(settings.gmail_token_file, "w") as f:
            f.write(creds.to_json())
        logger.info(f"トークンを保存しました: {settings.gmail_token_file}")

    return creds


def get_gmail_service():
    """Gmail APIサービスオブジェクトを返す"""
    creds = get_gmail_credentials()
    return build("gmail", "v1", credentials=creds)


def is_gmail_configured() -> bool:
    """Gmail APIが設定済みか確認する"""
    return (
        os.path.exists(settings.gmail_credentials_file)
        or os.path.exists(settings.gmail_token_file)
    )


if __name__ == "__main__":
    print("Gmail API 認証を開始します...")
    get_gmail_credentials()
    print("認証が完了しました。token.json を確認してください。")
