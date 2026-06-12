from __future__ import annotations

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from werkzeug.security import generate_password_hash

from app import create_app
from app.models import Base
from app.models.org import Organization
from app.models.user import User, Role

DEFAULT_EMAIL = os.getenv("DIRECTOR_EMAIL", os.getenv("COMPANY_EMAIL", "veteranclaimsassistance@gmail.com"))
DEFAULT_PASSWORD = os.getenv("DIRECTOR_PASSWORD", "ChangeMeNow123!")
DEFAULT_ORG = os.getenv("ORG_NAME", os.getenv("COMPANY_NAME", "Veterans Benefits"))

def main() -> int:
    app = create_app()
    with app.app_context():
        db_url = app.config.get("SQLALCHEMY_DATABASE_URI")
        if not db_url:
            raise RuntimeError("SQLALCHEMY_DATABASE_URI is not configured.")
        engine = create_engine(db_url, future=True, pool_pre_ping=True)

        # Create all tables (including newly added ones like resources)
        Base.metadata.create_all(bind=engine)

        with Session(engine) as db:
            org = db.query(Organization).filter(Organization.name == DEFAULT_ORG).first()
            if not org:
                org = Organization(name=DEFAULT_ORG)
                db.add(org)
                db.commit()
                db.refresh(org)

            user = db.query(User).filter(User.email == DEFAULT_EMAIL).first()
            if not user:
                user = User(
                    org_id=org.id,
                    email=DEFAULT_EMAIL,
                    full_name=f"{DEFAULT_ORG} (Director)",
                    role=Role.DIRECTOR,
                    password_hash=generate_password_hash(DEFAULT_PASSWORD),
                    is_active=True,
                    must_reset_password=True,
                )
                db.add(user)
                db.commit()
            else:
                user.org_id = org.id
                user.role = Role.DIRECTOR
                user.password_hash = generate_password_hash(DEFAULT_PASSWORD)
                user.is_active = True
                user.must_reset_password = True
                db.commit()

        print("\nDIRECTOR LOGIN CREATED/RESET:")
        print(f"  Email: {DEFAULT_EMAIL}")
        print(f"  Password: {DEFAULT_PASSWORD}")
        print("  (You will be forced to reset the password on first login.)\n")
        print("Next: run the app, then log in and immediately change your password.\n")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
