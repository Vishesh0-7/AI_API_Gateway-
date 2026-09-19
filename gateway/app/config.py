"""
Central configuration for the gateway.

Upstreams are declared here as a static registry (name -> base URL) rather
than discovered dynamically, since this is a portfolio project with a fixed,
small set of mock upstreams. Everything else is tunable via environment
variables so the same image works in docker-compose and in tests.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# docker-compose defaults -- upstreams resolve via service DNS names there.
_DEFAULT_UPSTREAM_URLS: dict[str, str] = {
    "upstream-a": "http://upstream-a:8000",
    "upstream-b": "http://upstream-b:8000",
    "upstream-c": "http://upstream-c:8000",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    redis_url: str = "redis://redis:6379/0"
    postgres_dsn: str = "postgresql://gateway:gateway@postgres:5432/gateway"

    # Per-upstream hostname overrides (no scheme) -- set these in
    # deployments where each upstream is a separate public host (e.g. the
    # Render blueprint wires these from each upstream service's assigned
    # hostname). Unset ones fall back to the docker-compose default below.
    upstream_a_host: str | None = None
    upstream_b_host: str | None = None
    upstream_c_host: str | None = None

    @property
    def upstreams(self) -> dict[str, str]:
        overrides = {
            "upstream-a": self.upstream_a_host,
            "upstream-b": self.upstream_b_host,
            "upstream-c": self.upstream_c_host,
        }
        return {
            name: f"https://{host}" if host else _DEFAULT_UPSTREAM_URLS[name]
            for name, host in overrides.items()
        }

    # Circuit breaker defaults (overridable per-upstream via the API later).
    cb_failure_threshold: int = 5
    cb_recovery_timeout_seconds: float = 10.0
    cb_half_open_max_calls: int = 2

    # Retry defaults.
    retry_max_attempts: int = 3
    retry_base_delay_seconds: float = 0.2
    retry_max_delay_seconds: float = 2.0

    # Rate limiter defaults: token bucket, per (client_id, upstream).
    rate_limit_capacity: int = 20
    rate_limit_refill_per_second: float = 10.0

    upstream_timeout_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
