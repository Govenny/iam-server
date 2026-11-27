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

    # 现有门户用户信息反查接口（单步Bearer模式，留空则不启用）
    PORTAL_USERINFO_URL: str = ""

    # 门户统一身份认证体系接口配置（《门户-身份体系接口文档》两步换票及全量拉取，留空则启用本地Mock解析）
    PORTAL_SSO_BASE_URL: str = ""
    PORTAL_SSO_CLIENT_ID: str = "test-client"
    PORTAL_AUTH_SALT: str = "4c4a1c7d52"
    PORTAL_LOGIN_URL: str = "http://10.213.10.228:65/user/nlogin"

    # 安全密钥
    JUMP_TICKET_AES_KEY: str = "0123456789abcdef0123456789abcdef"
    GATEWAY_HMAC_SECRET: str = "shuzidizuo_gw_secret_2026"
    INTERNAL_JWT_SECRET: str = "shuzidizuo_iam_jwt_secret_2026"

    # 国家中试基地统一接口规范鉴权凭证
    PILOT_APP_ID: str = "APP_SZ_PILOT_001"
    PILOT_APP_SECRET: str = "B2732427"
    PILOT_VERIFY_TOKEN: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
