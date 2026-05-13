from pydantic import BaseSettings

class Settings(BaseSettings):
    default_timeout: int = 30
    default_retries: int = 2
    db_path: str = "C:\\Users\\User\\Documents\\AgenteDesktop\\agent_state.db"

settings = Settings()
