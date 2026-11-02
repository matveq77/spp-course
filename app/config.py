import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'kanban.db'}")

# Секрет для подписи JWT. В проде обязательно задать через переменную окружения.
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me-please-32-bytes!!")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_MIN = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "15"))
REFRESH_TOKEN_TTL_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "14"))

SSE_KEEPALIVE_SEC = 15

# 2FA
TOTP_ISSUER = "Kanban Lite"
TWO_FACTOR_CHALLENGE_TTL_MIN = 5
TWO_FACTOR_MAX_ATTEMPTS = 5
BACKUP_CODES_COUNT = 10

# вложения
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", BASE_DIR / "uploads"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_ATTACHMENTS_PER_CARD = 30
