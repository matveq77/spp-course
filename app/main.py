import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR, UPLOAD_DIR
from .db import migrate
from .events import broker
from .routers import account, attachments, auth, boards, projects, realtime, sprints, teams, users

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    migrate()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    broker.loop = asyncio.get_running_loop()
    yield


app = FastAPI(title="Kanban Lite", version="1.0", lifespan=lifespan)

for r in (auth, account, users, teams, projects, boards, sprints, attachments, realtime):
    app.include_router(r.router)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")
