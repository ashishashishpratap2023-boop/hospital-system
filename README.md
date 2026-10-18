# Multi-client Hospital Appointment System

## Backend (FastAPI)
```
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # set DATABASE_URL and a long random SECRET_KEY
python -m app.cli init-db     # dev only
python -m app.cli create-superadmin
uvicorn app.main:app --reload   # docs at http://localhost:8000/docs
python -m pytest -q
```
See CHECKLIST.md for what is and is not implemented yet.
