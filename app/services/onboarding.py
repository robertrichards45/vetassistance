from app.extensions import SessionLocal
from app.models import ClientTask, Template, Letter
from app.services.template_engine import render as render_template_text

DEFAULT_TASKS = [
  "Upload ALL VA decision letters (all periods)",
  "Upload ALL C&P exams (all periods)",
  "Upload treatment records for relevant conditions",
  "Upload DBQs (if available)",
  "Send a message with your top 3 concerns + any deadlines",
  "Upload buddy statements (symptoms + functional impact)",
  "Upload employer statement (work impact) if applicable",
]

def ensure_tasks(org_id: int, client_id: int):
    db = SessionLocal()
    existing = db.query(ClientTask).filter_by(org_id=org_id, client_id=client_id).count()
    if existing:
        return
    for t in DEFAULT_TASKS:
        db.add(ClientTask(org_id=org_id, client_id=client_id, title=t, is_required=True))
    db.commit()

def post_welcome_letter(org_id: int, client_id: int, rep_name: str, created_by_user_id: int | None):
    db = SessionLocal()
    # find welcome template
    tmpl = db.query(Template).filter_by(org_id=org_id, name="Welcome Letter").first()
    if not tmpl:
        return
    body = render_template_text(tmpl.body, {"client_name": "", "rep_name": rep_name})
    # prefer client name later in portal render; store with placeholders resolved safely
    letter = Letter(org_id=org_id, client_id=client_id, title="Welcome to Veteran Benefits Assistance", body=body, created_by_user_id=created_by_user_id, is_visible_to_client=True)
    db.add(letter); db.commit()


