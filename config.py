from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""

    # Google Gemini (無料代替)
    gemini_api_key: str = ""

    # メール送信 (Gmail API優先、フォールバックにSMTP)
    gmail_credentials_file: str = "credentials.json"   # Google OAuth2クレデンシャル
    gmail_token_file: str = "token.json"               # 認証トークン保存先
    gmail_sender_email: str = ""                       # 送信元Gmailアドレス
    gmail_from_name: str = "営業部"

    # SMTP (Gmailを使わない場合のフォールバック)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "営業部"

    # Google Sheets
    sheets_credentials_file: str = "credentials.json"  # Gmail APIと同じクレデンシャル使用可
    sheets_spreadsheet_id: str = ""                    # スプレッドシートのID (URLから取得)
    sheets_hot_leads_tab: str = "ホットリード"
    sheets_call_log_tab: str = "架電ログ"
    sheets_results_tab: str = "分析"

    # アプリ
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./salesdepartment.db"
    dashboard_secret_key: str = "change-me"

    # スクレイピング
    scrape_delay_seconds: float = 2.0               # リクエスト間隔 (マナー)
    scrape_timeout_seconds: int = 15
    scrape_use_playwright: bool = False             # JS描画が必要なサイト用

    # スケジューラ
    follow_up_days: int = 7                         # 初回メール後のフォローアップ間隔
    gmail_check_interval_minutes: int = 15          # 返信チェック間隔

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
