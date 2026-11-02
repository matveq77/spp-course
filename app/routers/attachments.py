"""Вложения карточек: загрузка, скачивание (только с правом на проект), удаление."""
import mimetypes
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import config
from ..db import get_db
from ..deps import Principal, get_principal
from ..models import Attachment, Card
from ..permissions import P
from ..serializers import attachment_out
from ..services import log_activity, queue_event

router = APIRouter(prefix="/api", tags=["attachments"])

CHUNK = 1024 * 1024

# Превью показываем только для растровых картинок, тип определяем по содержимому файла.
# SVG намеренно не считается картинкой: он может содержать скрипты.
IMAGE_SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
]


def sniff_image(head: bytes) -> str | None:
    for sig, mime in IMAGE_SIGNATURES:
        if head.startswith(sig):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def file_path(att: Attachment) -> Path:
    return config.UPLOAD_DIR / att.stored_name


def _card(db: Session, card_id: int) -> Card:
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "Карточка не найдена")
    return card


def _human(size: int) -> str:
    return f"{size / 1024:.0f} КБ" if size < CHUNK else f"{size / CHUNK:.1f} МБ"


@router.post("/cards/{card_id}/attachments")
def upload_attachment(card_id: int, request: Request, file: UploadFile = File(...),
                      me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    card = _card(db, card_id)
    project = card.column.board.project
    me.require_project(project.id, P.CARD_UPDATE)
    limit = config.MAX_UPLOAD_MB * CHUNK
    declared = int(request.headers.get("content-length") or 0)
    if declared > limit + 64 * 1024:
        raise HTTPException(413, f"Файл больше {config.MAX_UPLOAD_MB} МБ")
    if len(card.attachments) >= config.MAX_ATTACHMENTS_PER_CARD:
        raise HTTPException(409, f"У карточки не может быть больше {config.MAX_ATTACHMENTS_PER_CARD} вложений")

    filename = Path(file.filename or "file").name.strip()[:200] or "file"
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = secrets.token_hex(16)
    dest = config.UPLOAD_DIR / stored
    size, head = 0, b""
    try:
        with open(dest, "wb") as out:
            while chunk := file.file.read(CHUNK):
                if not head:
                    head = chunk[:16]
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"Файл больше {config.MAX_UPLOAD_MB} МБ")
                out.write(chunk)
        if size == 0:
            raise HTTPException(422, "Пустой файл")
    except BaseException:
        dest.unlink(missing_ok=True)
        raise

    image_type = sniff_image(head)
    content_type = image_type or file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    att = Attachment(card_id=card.id, uploader_id=me.id, filename=filename, stored_name=stored,
                     content_type=content_type[:100], size=size, is_image=bool(image_type))
    db.add(att)
    db.flush()
    log_activity(db, project_id=project.id, user_id=me.id, action="attachment.added",
                 message=f"прикрепил(а) файл «{filename}» ({_human(size)}) к {project.key}-{card.number}",
                 board_id=card.board_id, card_id=card.id)
    queue_event(db, "board.changed", {"board_id": card.board_id, "card_id": card.id},
                project_id=project.id, actor_id=me.id)
    db.commit()
    db.refresh(att)
    return attachment_out(att)


@router.get("/attachments/{att_id}")
def download_attachment(att_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    att = db.get(Attachment, att_id)
    if not att:
        raise HTTPException(404, "Файл не найден")
    card = _card(db, att.card_id)
    me.require_project(card.column.board.project_id, P.PROJECT_READ)
    path = file_path(att)
    if not path.exists():
        raise HTTPException(410, "Файл отсутствует на сервере")
    return FileResponse(
        path, filename=att.filename,
        # не-картинки отдаём как бинарные: браузер не будет пытаться их исполнять/рендерить
        media_type=att.content_type if att.is_image else "application/octet-stream",
        content_disposition_type="inline" if att.is_image else "attachment",
        # no-store: файл не остаётся в кэше браузера после выхода из аккаунта
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.delete("/attachments/{att_id}")
def delete_attachment(att_id: int, me: Principal = Depends(get_principal), db: Session = Depends(get_db)):
    att = db.get(Attachment, att_id)
    if not att:
        raise HTTPException(404, "Файл не найден")
    card = _card(db, att.card_id)
    project = card.column.board.project
    # своё вложение удаляет тот, кто может редактировать карточку; чужое — только с правом удаления
    if att.uploader_id == me.id:
        me.require_project(project.id, P.CARD_UPDATE)
    else:
        me.require_project(project.id, P.CARD_DELETE)
    log_activity(db, project_id=project.id, user_id=me.id, action="attachment.deleted",
                 message=f"удалил(а) файл «{att.filename}» из {project.key}-{card.number}",
                 board_id=card.board_id, card_id=card.id)
    queue_event(db, "board.changed", {"board_id": card.board_id, "card_id": card.id},
                project_id=project.id, actor_id=me.id)
    db.delete(att)
    db.commit()
    return {"ok": True}
