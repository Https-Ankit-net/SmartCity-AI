from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "change-this-secret-key-before-production"


class Settings(BaseSettings):
    environment: str = Field(default="development", validation_alias=AliasChoices("ENVIRONMENT", "APP_ENV"))
    database_url: str = "sqlite:///./smartcity_ai.db"
    # .env uses the JWT_* names; SECRET_KEY etc. are accepted too.
    secret_key: str = Field(default=DEFAULT_SECRET, validation_alias=AliasChoices("JWT_SECRET", "SECRET_KEY"))
    algorithm: str = Field(default="HS256", validation_alias=AliasChoices("JWT_ALGORITHM", "ALGORITHM"))
    access_token_expire_minutes: int = Field(
        default=60, validation_alias=AliasChoices("JWT_EXPIRE_MINUTES", "ACCESS_TOKEN_EXPIRE_MINUTES")
    )
    cors_origins: str = Field(default="*", description="Comma-separated origins, or *")
    redis_url: str | None = Field(default=None, description="Enables cross-worker WebSocket broadcasting")

    # Outgoing email (see app/services/mailer.py).
    email_backend: str = Field(default="console", description="console | smtp | memory | disabled")
    email_from: str = "no-reply@smartcity.local"
    email_from_name: str = "SmartCity AI"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_security: str = Field(default="starttls", description="starttls (587) | ssl (465) | none")
    rate_limit_enabled: bool = True
    # Where the pages are served; used for links in emails. Empty = no links.
    frontend_url: str | None = Field(default=None, validation_alias=AliasChoices("FRONTEND_URL", "APP_URL"))

    # env_ignore_empty: a blank "SMTP_PORT=" line in .env means "use the default", not an error.
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore", env_ignore_empty=True)

    @field_validator("smtp_port", "smtp_host", "smtp_username", "smtp_password", "frontend_url", "redis_url", mode="before")
    @classmethod
    def blank_means_default(cls, value, info):
        # Older pydantic-settings ignore env_ignore_empty; treat "" the same way explicitly.
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @model_validator(mode="after")
    def production_needs_real_secret(self) -> "Settings":
        if self.environment.lower() == "production" and (self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32):
            raise ValueError("JWT_SECRET must be set to a random value of at least 32 characters in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()] or ["*"]


settings = Settings()
