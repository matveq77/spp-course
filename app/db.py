from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def migrate():
    """Создаёт новые таблицы и добавляет недостающие колонки в существующие.

    Мини-замена Alembic для курсового: позволяет обновить уже заполненную базу без пересоздания.
    """
    from sqlalchemy import inspect, text

    from . import models  # noqa: F401 — регистрирует все таблицы в Base.metadata

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f"ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}"
                default = col.default.arg if col.default is not None and col.default.is_scalar else None
                if isinstance(default, bool):
                    ddl += " DEFAULT " + ("TRUE" if default else "FALSE")
                elif isinstance(default, (int, float)):
                    ddl += f" DEFAULT {default}"
                elif isinstance(default, str):
                    ddl += " DEFAULT '" + default.replace("'", "''") + "'"
                conn.execute(text(ddl))
        # refresh-токены, выданные до появления сессий
        conn.execute(text(
            "UPDATE refresh_tokens SET session_id = 'legacy-' || id, session_started_at = created_at "
            "WHERE session_id IS NULL"
        ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
