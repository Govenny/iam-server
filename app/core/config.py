import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "prod"
    APP_PORT: int = 8001
    MYSQL_DSN: str = "mysql+pymysql://root:123456@db:3306/casdoor?charset=utf8mb4"
    REDIS_URL: str = "redis://redis:6379/0"

    # Casdoor 内网调用配置
    CASDOOR_INTERNAL_ENDPOINT: str = "http://casdoor:8000"
    CASDOOR_CLIENT_ID: str = "your_client_id"
    CASDOOR_CLIENT_SECRET: str = "your_client_secret"
    CASDOOR_ORG_NAME: str = "built-in"
    CASDOOR_APP_NAME: str = "app-built-in"

    # 现有门户用户信息反查接口（用于 iframe 打开时验证 portal_token，留空则启用本地Mock解析）
    PORTAL_USERINFO_URL: str = ""

    # 安全密钥
    JUMP_TICKET_AES_KEY: str = "0123456789abcdef0123456789abcdef"
    GATEWAY_HMAC_SECRET: str = "shuzidizuo_gw_secret_2026"
    INTERNAL_JWT_SECRET: str = "shuzidizuo_iam_jwt_secret_2026"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
