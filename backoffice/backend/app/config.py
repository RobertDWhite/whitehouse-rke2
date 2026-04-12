from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://backoffice:backoffice@backoffice-postgres:5432/backoffice"
    k8s_in_cluster: bool = True

    model_config = {"env_prefix": "BACKOFFICE_"}


settings = Settings()
