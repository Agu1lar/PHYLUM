from pathlib import Path

# Protected roots that must not be deleted or moved by the agent
PROTECTED_PATHS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("C:/Users/Default"),
]

# Allowed workspace roots for safe operations (agent quarantine, temp)
AGENT_WORKSPACE = Path("C:/Users/User/Documents/AgenteDesktop/agent_workspace")
QUARANTINE_DIR = AGENT_WORKSPACE / "quarantine"
OP_HISTORY_PREFIX = "fs:history"

# File categories (can be extended)
DEFAULT_CATEGORIES = {
    "images": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"],
    "documents": [".pdf", ".doc", ".docx", ".txt", ".odt"],
    "archives": [".zip", ".tar", ".gz", ".7z", ".rar"],
    "videos": [".mp4", ".mkv", ".avi", ".mov"],
    "audio": [".mp3", ".wav", ".flac"],
    "executables": [".exe", ".msi"],
}

# Directories to consider by default
from pathlib import Path
USER_HOME = Path.home()
DESKTOP_DIR = USER_HOME / "Desktop"
DOWNLOADS_DIR = USER_HOME / "Downloads"
TEMP_DIRS = [Path("C:/Windows/Temp"), Path("C:/Users") / USER_HOME.name / "AppData/Local/Temp"]
