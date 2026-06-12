import json
from datetime import datetime
from flask import current_app
from app.extensions import SessionLocal
from app.models import AIRun, Document, Client, FormData

SYSTEM_PROMPT = """You are the internal Claims Intelligence assistant for Veteran Benefits Assistance.
You must produce:
1) A concise client case summary
2) A per-condition evidence score (0-100) with reasoning
3) Missing evidence / gaps
4) Conflicts / inconsistencies
5) Next best actions (prioritized)
6) A presumptive-conditions analysis for Gulf War (Aug 2, 1990) through present-day rules.
Return STRICT JSON with keys: summary, conditions[], gaps[], conflicts[], next_actions[], presumptive.
Each conditions[] item: name, score, rationale, best_evidence[], missing_evidence[].
presumptive must be an object with:
- eligible (true/false)
- conditions[]: name, category, authority, confirmation, evidence_minimum, nexus_required, claim_summary, claim_language
- secondary_conditions[] (strings)
- evidence_needed[] (strings)
- strategy[] (strings)
- logic_log[] (strings)
- disclaimer (string)
Do NOT provide medical diagnoses or guarantees. Be operational, VA-compliant, and specific.
"""

DOC_QA_PROMPT = """You answer questions using the provided context (documents + client profile + intake + prior AI summary).
If the answer is not supported by the context, you may provide general VA guidance, but clearly label it as general guidance.
When applicable, reference VA rules/regulations (38 CFR, PACT Act) but only if they are relevant to the question.
Be concise and cite short snippets when helpful.
Do NOT provide medical diagnosis or guarantees.

Return response in this structure:
Scope: Client-specific | General guidance
Answer: <your answer>
Evidence needed: <bullet list if key evidence is missing, otherwise say "None">
"""

CLAIM_REVIEW_PROMPT = """You are the internal Claims Quality reviewer for Veteran Benefits Assistance.
You must return STRICT JSON with this schema:
{
  "score": 0-100,
  "summary": "string",
  "issues": [
    {
      "level": "red|yellow|green",
      "title": "string",
      "detail": "string",
      "missing_evidence": ["..."],
      "fix": "string"
    }
  ],
  "missing_evidence": ["..."],
  "strengths": ["..."],
  "notes": ["..."],
  "disclaimer": "string"
}
Rules:
- Use only provided context.
- Do not diagnose or guarantee outcomes.
- Be operational, specific, and concise.
 - If CRSC_HINTS suggest a retiree/medical retiree with combat indicators, add a note starting with "CRSC:" that recommends CRSC review and list missing evidence in missing_evidence (retirement orders, retired pay statement, combat nexus proof).
"""


def run_claim_review(prompt: str) -> str:
    if not current_app.config.get("OPENAI_API_KEY"):
        return json.dumps({
            "score": 0,
            "summary": "AI is not configured yet (OPENAI_API_KEY missing).",
            "issues": [
                {
                    "level": "yellow",
                    "title": "AI not configured",
                    "detail": "Add OPENAI_API_KEY in .env to enable AI review.",
                    "missing_evidence": [],
                    "fix": "Configure OPENAI_API_KEY and restart the app."
                }
            ],
            "missing_evidence": [],
            "strengths": [],
            "notes": [],
            "disclaimer": "This tool does not provide medical or legal advice."
        }, indent=2)

    from openai import OpenAI
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": CLAIM_REVIEW_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""

def _call_openai(prompt: str) -> str:
    # Only runs if OPENAI_API_KEY exists; otherwise returns a deterministic stub.
    if not current_app.config.get("OPENAI_API_KEY"):
        return json.dumps({
            "summary": "AI is not configured yet (OPENAI_API_KEY missing). Uploads are stored and ready; add OPENAI_API_KEY to enable analysis.",
            "conditions": [],
            "gaps": ["Configure OPENAI_API_KEY to enable evidence intelligence."],
            "conflicts": [],
            "next_actions": [
                "Add OPENAI_API_KEY in .env and restart the app.",
                "Upload decision letters, C&P exams, and treatment records for analysis."
            ],
            "presumptive": {
                "eligible": False,
                "conditions": [],
                "secondary_conditions": [],
                "evidence_needed": ["AI is not configured yet (OPENAI_API_KEY missing)."],
                "strategy": [],
                "logic_log": ["No AI run; presumptive logic not evaluated."],
                "disclaimer": "This tool does not guarantee outcomes and does not provide medical diagnosis."
            }
        }, indent=2)

    from openai import OpenAI
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role":"system","content": SYSTEM_PROMPT},
            {"role":"user","content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content or ""


def answer_doc_question(doc_text: str, question: str, history: str = "", extra_context: str = "") -> str:
    if not question.strip():
        return "Please enter a question."
    if not current_app.config.get("OPENAI_API_KEY"):
        return "AI is not configured yet (OPENAI_API_KEY missing)."
    if not doc_text.strip() and not extra_context.strip():
        return "No readable text was found in the documents or client record."

    from openai import OpenAI
    client = OpenAI(api_key=current_app.config["OPENAI_API_KEY"])
    model = current_app.config.get("OPENAI_MODEL", "gpt-4.1-mini")
    prompt = (
        "DOCUMENT_TEXT:\\n"
        f"{doc_text[:12000]}\\n\\n"
        f"CHAT_HISTORY:\\n{history}\\n\\n"
        f"QUESTION:\\n{question.strip()}\\n"
    )
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": DOC_QA_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return (resp.choices[0].message.content or "").strip()

def run_client_analysis(ai_run_id: int) -> None:
    db = SessionLocal()
    run = db.get(AIRun, ai_run_id)
    if not run:
        return
    try:
        run.status = "RUNNING"
        db.commit()

        client = db.get(Client, run.client_id)
        intake_data = {}
        intake = db.query(FormData).filter_by(org_id=run.org_id, client_id=run.client_id, form_key="INTAKE_V1").first()
        if intake and intake.data_json:
            try:
                intake_data = json.loads(intake.data_json)
            except Exception:
                intake_data = {}

        docs = db.query(Document).filter_by(org_id=run.org_id, client_id=run.client_id).order_by(Document.created_at.asc()).all()
        # lightweight prompt: metadata + extracted text snippets (if present)
        doc_blocks = []
        for d in docs:
            block = [
                f"FILE: {d.filename}",
                f"MIME: {d.mime_type}",
                f"CATEGORY: {d.category or ''}",
                f"STATUS: {d.status or ''}",
            ]
            if d.extracted_text_path and d.extracted_text_path.strip():
                try:
                    with open(d.extracted_text_path, "r", encoding="utf-8", errors="ignore") as f:
                        block.append(f.read()[:8000])
                except Exception:
                    pass
            doc_blocks.append("\n".join(block))

        profile = {
            "client": {
                "name": client.display_name() if client else "",
                "branch": getattr(client, "service_branch", "") if client else "",
                "service_entry_date": getattr(client, "service_entry_date", "") if client else "",
                "service_discharge_date": getattr(client, "service_discharge_date", "") if client else "",
                "dob": getattr(client, "dob", "") if client else "",
            },
            "intake": intake_data,
        }

        rules = (
            "Presumptive engine rules:\n"
            "- Gulf War era starts Aug 2, 1990.\n"
            "- Apply Southwest Asia theater and PACT Act regions.\n"
            "- Identify undiagnosed illnesses (6+ months), MUCMI, infectious disease presumptives, "
            "respiratory presumptives, and cancer presumptives.\n"
            "- For each presumptive condition, confirm qualifying service location/period and "
            "diagnosis or symptom criteria.\n"
            "- If confirmed, mark presumptive and state 'No nexus letter required'. Cite 38 CFR 3.317 "
            "or PACT Act 2022 as applicable.\n"
            "- Flag secondary conditions and missed issues.\n"
            "- Provide first-person VA-ready claim language.\n"
            "- Do not diagnose. Do not guarantee outcomes."
        )

        prompt = (
            "Client profile and evidence (best-effort).\n\n"
            f"PROFILE_JSON:\n{json.dumps(profile, ensure_ascii=True)}\n\n"
            f"EVIDENCE_TEXT:\n{chr(10).join(doc_blocks)}\n\n"
            f"{rules}\n"
        )[:120000]

        out = _call_openai(prompt)
        # validate JSON best-effort
        try:
            parsed = json.loads(out)
            run.result_json = json.dumps(parsed, indent=2)
        except Exception:
            run.result_json = json.dumps({"raw": out}, indent=2)
        run.status = "COMPLETED"
        run.completed_at = datetime.utcnow()
        db.commit()
    except Exception as e:
        run.status = "FAILED"
        run.error = str(e)
        run.completed_at = datetime.utcnow()
        db.commit()


