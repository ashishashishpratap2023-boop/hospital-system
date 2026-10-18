from fastapi import FastAPI
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
