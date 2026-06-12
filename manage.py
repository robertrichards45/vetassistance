import sys
import sys
if sys.version_info >= (3, 14):
    raise RuntimeError("Python 3.14 is not supported on Windows for this build (Pillow wheels unavailable). Install Python 3.12.x (recommended) or 3.13.x.")


import click
from app import create_app
from app.extensions import init_db, SessionLocal
from app.models import Base, Organization, User
from app.models.user import Role

app = create_app()

@app.cli.command("init-db")
def init_db_cmd():
    engine = init_db(app)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    default_name = app.config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    org = db.query(Organization).filter(Organization.name == default_name).first()
    if not org:
        org = Organization(name=default_name)
        db.add(org); db.commit()
    click.echo(f"DB initialized. Default org: {org.name} (id={org.id})")

@app.cli.command("create-director")
@click.option("--email", required=True)
@click.option("--password", required=True)
@click.option("--full-name", default="Director")
def create_director(email, password, full_name):
    db = SessionLocal()
    default_name = app.config.get("DEFAULT_TENANT", "Veteran Benefits Assistance")
    org = db.query(Organization).filter(Organization.name == default_name).first()
    if not org:
        org = Organization(name=default_name); db.add(org); db.commit()
    if db.query(User).filter(User.email == email.lower()).first():
        click.echo("User already exists."); return
    u = User(org_id=org.id, email=email.lower(), full_name=full_name, role=Role.DIRECTOR, is_active=True, must_reset_password=False)
    u.set_password(password)
    db.add(u); db.commit()
    click.echo(f"Director created: {u.email} (org={org.name})")

@app.cli.command("worker")
@click.option("--queues", default="default,ai,docs")
def worker(queues: str):
    """Start an RQ worker for background jobs."""
    from rq import Worker
    from app.services.queue import get_redis
    qs = [q.strip() for q in queues.split(",") if q.strip()]
    w = Worker(qs, connection=get_redis())
    w.work()

@app.cli.command("run")
def run():
    app.run(host="127.0.0.1", port=5000)

if __name__ == "__main__":
    app.run()


@app.cli.command("create-admin")
def create_admin():
    """Create a DIRECTOR user interactively (for first-time setup)."""
    import getpass
    from werkzeug.security import generate_password_hash
    from app.extensions import SessionLocal
    from app.models import Organization, User
    from app.models.user import Role

    db = SessionLocal()
    email = input("Admin email: ").strip().lower()
    full_name = input("Full name (optional): ").strip() or email
    pw = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw != pw2:
        print("Passwords do not match.")
        return

    org = db.query(Organization).first()
    if not org:
        org = Organization(name="Veteran Benefits Assistance")
        db.add(org); db.commit()

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        print("User already exists.")
        return

    u = User(
        org_id=org.id,
        email=email,
        full_name=full_name,
        role=Role.DIRECTOR,
        password_hash=generate_password_hash(pw),
        must_reset_password=False,
        is_active=True,
    )
    db.add(u); db.commit()
    print("Admin created.")


