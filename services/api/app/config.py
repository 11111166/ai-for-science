from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    auth_provider: str = "dev"
    proxy_auth_secret: str = ""
    portal_auth_url: str = "http://127.0.0.1:8790"
    portal_auth_timeout: float = 5.0
    auth_session_cookie: str = "ai4science_session"
    auth_session_ttl: int = 12 * 60 * 60
    database_url: str = "sqlite:///./drug_discovery.db"
    redis_url: str = "redis://localhost:6379/0"
    cache_database_url: str = ""
    cache_enabled: bool = True
    cache_contract_version: str = "target-cache-v2"
    cache_shared_scope: bool = True
    cache_default_ttl: int = 6 * 60 * 60
    cache_public_ttl: int = 24 * 60 * 60
    cache_static_ttl: int = 7 * 24 * 60 * 60
    cache_max_body_bytes: int = 16 * 1024 * 1024
    job_execution_mode: str = "inline"
    recover_interrupted_jobs: bool = True
    object_storage_mode: str = "local"
    upload_dir: str = str(Path(__file__).resolve().parents[3] / "data" / "uploads")
    max_upload_bytes: int = 50 * 1024 * 1024
    omics_rscript: str = ""
    omics_advanced_timeout: int = 600
    s3_endpoint: str = "localhost:9000"
    s3_access_key: str = "drug-discovery"
    s3_secret_key: str = "change-me"
    s3_bucket: str = "drug-discovery"
    s3_secure: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    open_targets_url: str = "https://api.platform.opentargets.org/api/v4/graphql"
    open_targets_connect_timeout: float = 10.0
    open_targets_read_timeout: float = 60.0
    open_targets_retries: int = 3
    open_targets_evidence_page_size: int = 1000
    open_targets_max_evidence_records: int = 100000
    target_discovery_snapshot_path: str = ""
    novel_target_discovery_snapshot_path: str = ""
    novel_target_catalog_path: str = str(Path(__file__).resolve().parents[1] / "data/novel-catalog-v1")
    perturbation_knockout_snapshot_path: str = ""
    virtual_cell_snapshot_path: str = ""
    compound_evaluation_snapshot_path: str = ""

    @model_validator(mode="after")
    def enforce_production_auth(self):
        if self.app_env == "production" and self.auth_provider == "dev":
            raise ValueError("DevAuthProvider is forbidden in production")
        if self.app_env == "production" and self.auth_provider in {"proxy", "portal"} and len(self.proxy_auth_secret) < 32:
            raise ValueError("A trusted proxy secret of at least 32 characters is required in production")
        if self.auth_provider == "portal" and not self.portal_auth_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("PortalAuthProvider must use a loopback-only portal endpoint")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
