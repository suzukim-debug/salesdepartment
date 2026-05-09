from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "営業部"
    base_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///./salesdepartment.db"
    dashboard_secret_key: str = "change-me"
    dashboard_user: str = "admin"
    dashboard_password: str = "admin123"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
