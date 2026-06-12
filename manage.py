import sys
import sys
if sys.version_info >= (3, 14):
    raise RuntimeError("Python 3.14 is not supported on Windows for this build (Pillow wheels unavailable). Install Python 3.12.x (recommended) or 3.13.x.")


import click
from sqlalchemy import text
from app import create_app
from app.extensions import init_db, SessionLocal
from app.models import Base, Organization, User
from app.models.user import Role
from app.services.db_migration import count_rows, migrate_sqlite_to_postgres

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


@app.cli.command("migrate-sqlite-to-postgres")
@click.option("--sqlite-path", default="instance/app.db", show_default=True, help="Path to the source SQLite database.")
@click.option("--truncate/--no-truncate", default=True, show_default=True, help="Clear destination tables before loading data.")
@click.option("--batch-size", default=500, show_default=True, type=int, help="Rows per insert batch.")
@click.option("--dry-run", is_flag=True, help="Inspect row counts without writing to PostgreSQL.")
def migrate_sqlite_to_postgres_cmd(sqlite_path: str, truncate: bool, batch_size: int, dry_run: bool):
    """Copy application data from the local SQLite database into PostgreSQL."""
    engine = init_db(app)
    results = migrate_sqlite_to_postgres(
        sqlite_path=sqlite_path,
        target_engine=engine,
        truncate=truncate,
        batch_size=batch_size,
        dry_run=dry_run,
    )
    for result in results:
        click.echo(f"{result.table}: {result.rows} row(s)")
    if not dry_run:
        counts = count_rows(engine, [result.table for result in results])
        click.echo("Destination counts:")
        for table_name, row_count in counts.items():
            click.echo(f"{table_name}: {row_count} row(s)")


@app.cli.command("migrate-storage-paths")
@click.option(
    "--windows-root",
    default=r"C:\Users\rober\Desktop\merged website",
    show_default=True,
    help="Legacy Windows project root used in the SQLite database.",
)
@click.option(
    "--railway-root",
    default="/app/storage",
    show_default=True,
    help="Mounted Railway volume root.",
)
@click.option("--dry-run", is_flag=True, help="Show counts without updating PostgreSQL.")
def migrate_storage_paths_cmd(windows_root: str, railway_root: str, dry_run: bool):
    """Rewrite legacy Windows file paths to the Railway volume layout."""
    engine = init_db(app)
    normalized_windows_root = windows_root.rstrip("\\/")
    normalized_railway_root = railway_root.rstrip("/")
    replacements = [
        ("documents", "storage_path"),
        ("documents", "extracted_text_path"),
        ("rendered_artifacts", "docx_path"),
        ("rendered_artifacts", "pdf_path"),
        ("cue_files", "docx_path"),
        ("cue_files", "pdf_path"),
        ("cue_files", "decision_path"),
        ("cue_files", "confirmation_path"),
        ("diy_documents", "storage_path"),
    ]

    def _legacy_prefix(dirname: str) -> str:
        return f"{normalized_windows_root}\\{dirname}\\"

    def _target_prefix(dirname: str) -> str:
        return f"{normalized_railway_root}/{dirname}/"

    with engine.begin() as conn:
        for table_name, column_name in replacements:
            updated_rows = 0
            for dirname in ("uploads", "storage"):
                prefix = _legacy_prefix(dirname)
                count_stmt = text(
                    f"""
                    SELECT COUNT(*)
                    FROM {table_name}
                    WHERE {column_name} LIKE :prefix
                    """
                )
                row_count = conn.execute(count_stmt, {"prefix": f"{prefix}%"}).scalar() or 0
                if not row_count:
                    continue
                click.echo(f"{table_name}.{column_name}: {row_count} row(s) matched {dirname}")
                if dry_run:
                    continue
                update_stmt = text(
                    f"""
                    UPDATE {table_name}
                    SET {column_name} = REPLACE(
                        REPLACE({column_name}, :prefix, :target_prefix),
                        '\\\\',
                        '/'
                    )
                    WHERE {column_name} LIKE :like_prefix
                    """
                )
                conn.execute(
                    update_stmt,
                    {
                        "prefix": prefix,
                        "target_prefix": _target_prefix(dirname),
                        "like_prefix": f"{prefix}%",
                    },
                )
                updated_rows += row_count
            if not dry_run and updated_rows:
                click.echo(f"{table_name}.{column_name}: updated {updated_rows} row(s)")

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


