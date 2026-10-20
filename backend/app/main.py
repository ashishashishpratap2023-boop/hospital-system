from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import appointments, whatsapp, auth, clinic, patients, public, tenants, users
from app.core.config import settings

app = FastAPI(title="Hospital Appointment System", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Content-Type"])
for r in (auth, tenants, users, clinic, patients, appointments, whatsapp, public):
    app.include_router(r.router, prefix="/api/v1")
app.include_router(whatsapp.hook, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}


# Web app (same origin as the API, so login cookies just work). Must stay LAST so it never shadows API routes.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
