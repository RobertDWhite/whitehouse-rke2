import os

from pydantic_settings import BaseSettings


def _build_database_url() -> str:
    user = os.environ.get("POSTGRES_USER", "backoffice")
    password = os.environ.get("POSTGRES_PASSWORD", "backoffice")
    db = os.environ.get("POSTGRES_DB", "backoffice")
    host = os.environ.get("POSTGRES_HOST", "backoffice-postgres")
    return f"postgresql+asyncpg://{user}:{password}@{host}:5432/{db}"


class Settings(BaseSettings):
    database_url: str = ""
    k8s_in_cluster: bool = True

    model_config = {"env_prefix": "BACKOFFICE_"}

    def model_post_init(self, __context) -> None:
        if not self.database_url:
            self.database_url = _build_database_url()


settings = Settings()
