"""
TOTP (RFC 6238) на стандартной библиотеке — совместим с Google Authenticator, Яндекс Ключом,
Microsoft Authenticator и т.п. Плюс резервные коды и QR-код для подключения.
"""
import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

import qrcode
import qrcode.image.svg

from . import config

PERIOD = 30
DIGITS = 6
WINDOW = 1  # допускаем расхождение часов на ±1 шаг (±30 с)


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return str(code).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((now if now is not None else time.time()) // PERIOD)


def code_at(secret: str, step: int) -> str:
    return _hotp(secret, step)


def verify(secret: str, code: str, last_step: int | None) -> int | None:
    """Возвращает номер принятого шага или None. Коды из уже использованных шагов отклоняются."""
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = current_step()
    for step in range(now - WINDOW, now + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(_hotp(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, username: str) -> str:
    issuer = config.TOTP_ISSUER
    label = quote(f"{issuer}:{username}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}")


def qr_data_uri(text: str) -> str:
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------- резервные коды ----------

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # без похожих символов (l/1, o/0)


def generate_backup_codes(n: int) -> list[str]:
    def one():
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(8))
        return f"{raw[:4]}-{raw[4:]}"
    return [one() for _ in range(n)]


def normalize_backup(code: str) -> str:
    c = (code or "").strip().lower().replace(" ", "").replace("-", "")
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else c


def hash_backup(code: str) -> str:
    return hashlib.sha256(normalize_backup(code).encode()).hexdigest()
