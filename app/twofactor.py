"""Проверка второго фактора: TOTP-код или одноразовый резервный код."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, totp
from .models import BackupCode, User, utcnow


def check_second_factor(db: Session, user: User, code: str) -> bool:
    """Проверяет код и сразу «сжигает» его (TOTP-шаг или резервный код). Нужен commit после."""
    if not user.totp_secret:
        return False
    code = (code or "").strip()
    step = totp.verify(user.totp_secret, code, user.totp_last_step)
    if step is not None:
        user.totp_last_step = step
        return True
    h = totp.hash_backup(code)
    bc = db.scalar(select(BackupCode).where(
        BackupCode.user_id == user.id, BackupCode.code_hash == h, BackupCode.used_at.is_(None)))
    if bc:
        bc.used_at = utcnow()
        return True
    return False


def replace_backup_codes(db: Session, user: User) -> list[str]:
    db.query(BackupCode).filter_by(user_id=user.id).delete()
    codes = totp.generate_backup_codes(config.BACKUP_CODES_COUNT)
    for c in codes:
        db.add(BackupCode(user_id=user.id, code_hash=totp.hash_backup(c)))
    return codes


def backup_codes_left(db: Session, user_id: int) -> int:
    return db.query(BackupCode).filter_by(user_id=user_id, used_at=None).count()


def disable_2fa(db: Session, user: User):
    user.totp_enabled = False
    user.totp_secret = None
    user.totp_last_step = None
    db.query(BackupCode).filter_by(user_id=user.id).delete()
