"""Dev/ops helpers:  python -m app.cli init-db | create-superadmin"""
import getpass
import sys

from app.core.db import Base, SessionLocal, engine
from app.core.security import hash_password
from app.models import User


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init-db":  # development only; production uses Alembic migrations
        Base.metadata.create_all(engine)
        print("Tables created")
    elif cmd == "create-superadmin":
        email = input("Email: ").strip().lower()
        pw = getpass.getpass("Password (min 12 chars): ")
        if len(pw) < 12:
            sys.exit("Password too short")
        with SessionLocal() as db:
            db.add(User(email=email, full_name="Super Admin", role="super_admin", password_hash=hash_password(pw)))
            db.commit()
        print("Super admin created")
    elif cmd == "run-worker":  # delivers queued/retrying WhatsApp messages
        import time
        from app.services.whatsapp import process_due
        while True:
            with SessionLocal() as db:
                process_due(db)
            time.sleep(30)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
