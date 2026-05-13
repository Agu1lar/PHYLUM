# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from pydantic import BaseSettings

class Settings(BaseSettings):
    default_timeout: int = 30
    default_retries: int = 2
    db_path: str = "C:\\Users\\User\\Documents\\AgenteDesktop\\agent_state.db"

settings = Settings()
