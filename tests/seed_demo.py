"""Seed a small demo dataset (used by the crawl test and for local screenshots).

Never run this against the production database.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.extensions import SessionLocal
from app.models import Organization, User, Client, MessageThread, Message, ClientTask
from app.models.user import Role

PASSWORD = "Demo-Password-123"
EMAILS = {"director": "director@demo.local", "employee": "employee@demo.local", "client": "client@demo.local"}


def seed() -> dict:
    db = SessionLocal()
    try:
        org = db.query(Organization).filter_by(name="Veteran Benefits Assistance").first() or db.query(Organization).first()
        if not org:
            org = Organization(name="Veteran Benefits Assistance")
            db.add(org)
            db.commit()

        def user(key, role, name):
            u = db.query(User).filter_by(email=EMAILS[key]).first()
            if not u:
                u = User(org_id=org.id, email=EMAILS[key], full_name=name, role=role)
                u.set_password(PASSWORD)
                db.add(u)
                db.commit()
            return u

        director = user("director", Role.DIRECTOR, "Robert Director")
        employee = user("employee", Role.EMPLOYEE, "Dana Ortiz")
        client_user = user("client", Role.CLIENT, "Marcus Reed")

        if not db.query(Client).filter_by(org_id=org.id).count():
            people = [
                ("Marcus", "Reed", "Army", "Gathering evidence", employee.id, client_user.id),
                ("Alicia", "Gomez", "Navy", "New intake", None, None),
                ("Tom", "Becker", "Marine Corps", "Ready for review", employee.id, None),
                ("Renee", "Walsh", "Air Force", "Awaiting VA decision", director.id, None),
                ("James", "Okafor", "Army", "Filed with VA", employee.id, None),
                ("Linda", "Park", "Coast Guard", "Intake Received", None, None),
            ]
            for i, (f, l, br, st, rep, pu) in enumerate(people):
                c = Client(org_id=org.id, first_name=f, last_name=l, email=f"{f.lower()}@example.com",
                           phone=f"229555{1000 + i}", service_branch=br, status=st, assigned_user_id=rep,
                           portal_user_id=pu, portal_enabled=bool(pu),
                           created_at=datetime.utcnow() - timedelta(days=i * 3))
                db.add(c)
            db.commit()
            marcus = db.query(Client).filter_by(org_id=org.id, first_name="Marcus").first()
            tom = db.query(Client).filter_by(org_id=org.id, first_name="Tom").first()
            now = datetime.utcnow()
            db.add_all([
                ClientTask(org_id=org.id, client_id=marcus.id, title="Upload service treatment records",
                           instructions="Request them from the National Personnel Records Center if you don't have them.",
                           due_date=now - timedelta(days=2)),
                ClientTask(org_id=org.id, client_id=marcus.id, title="Ask your doctor about a nexus letter",
                           due_date=now + timedelta(days=5)),
                ClientTask(org_id=org.id, client_id=marcus.id, title="Sign the intake form", is_completed=True,
                           completed_at=now - timedelta(days=6)),
                ClientTask(org_id=org.id, client_id=tom.id, title="Review buddy statement draft",
                           due_date=now + timedelta(days=1)),
            ])
            db.commit()
            t = MessageThread(org_id=org.id, client_id=marcus.id)
            db.add(t)
            db.commit()
            db.add_all([
                Message(org_id=org.id, thread_id=t.id, client_id=marcus.id, sender_role="STAFF", sender_user_id=employee.id,
                        body="Hi Marcus, I added two tasks to your checklist.", created_at=now - timedelta(days=1)),
                Message(org_id=org.id, thread_id=t.id, client_id=marcus.id, sender_role="CLIENT", sender_user_id=client_user.id,
                        body="Thanks. I uploaded my DD-214 this morning. Do you need anything else for the knee?",
                        created_at=now - timedelta(hours=3)),
            ])
            db.commit()
        return {"org_id": org.id, "password": PASSWORD, "emails": EMAILS}
    finally:
        db.close()
