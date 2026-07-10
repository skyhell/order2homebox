"""Application settings, loaded from environment variables / .env (prefix O2H_)."""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="O2H_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Homebox
    homebox_url: str = "http://localhost:7745"
    # URL encoded into the QR codes (e.g. the reverse-proxy URL users scan);
    # falls back to homebox_url.
    homebox_public_url: str = ""
    homebox_username: str = ""
    homebox_password: str = ""

    # Print agent on the Raspberry Pi
    print_agent_url: str = "http://raspberrypi.local:8010"
    print_agent_api_key: str = ""

    # Web UI auth
    web_user: str = "admin"
    web_password_hash: str = ""  # bcrypt hash; generate with: python -m app.hashpw
    secret_key: str = "change-me"
    session_max_age: int = 60 * 60 * 24 * 30  # 30 days

    # Storage & UI
    data_dir: Path = Path("data")
    default_language: str = "de"

    # Label layout
    label_show_asset_id: bool = True
    label_qr_per_row: int = 2

    # Scraping
    scraper_headless: bool = True
    scraper_timeout_ms: int = 30000
    amazon_domain: str = "www.amazon.de"

    @model_validator(mode="after")
    def _decrypt_secrets(self) -> "Settings":
        """Decrypt any ``enc:``-prefixed secrets. Plain values pass through, so
        existing installs keep working. Done here (not in field validators) so
        data_dir is already set when the key file is resolved."""
        from .secrets import decrypt_maybe

        key_path = self.data_dir / "secret.key"
        self.homebox_password = decrypt_maybe(self.homebox_password, key_path)
        self.print_agent_api_key = decrypt_maybe(self.print_agent_api_key, key_path)
        return self

    @property
    def qr_base_url(self) -> str:
        return (self.homebox_public_url or self.homebox_url).rstrip("/")


settings = Settings()
