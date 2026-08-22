## Requirements
- **Python 3.12.x (recommended)** on Windows (Pillow does not ship wheels for Python 3.14)

# Veteran Benefits Assistance — Ecosystem (Production-Grade Scaffold)

This is a complete, **business-first** VA claims case-management ecosystem:
- Public homepage that converts + builds trust
- Director console (users, forced resets, audit log, template seeding)
- Employee workflow (clients list + tools)
- Client Portal (login, secure document upload, messaging)
- Evidence/Document pipeline hooks (ready for AI extraction + scoring)

## Quick Start (Windows)
```powershell
py -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -m flask --app manage.py init-db
python -m flask --app manage.py create-director --email director@local --password ChangeMe123! --full-name "Director"
python manage.py run
```

Open: http://127.0.0.1:5000

## First steps inside app
1. Login as Director
2. Create employee user(s) (forced reset on first login)
3. Create a client record and optionally create a client portal login invite
4. Use the Client Portal to upload documents + message your team

## Production notes (high-level)
- Replace SQLite with Postgres (`DATABASE_URL=postgresql+psycopg://...`)
- Put behind a reverse proxy (Caddy/Nginx) with HTTPS
- Use Redis + RQ for background document extraction + AI runs
- Store uploads on disk or S3-compatible storage



## Background worker (recommended)
In a second terminal:
```powershell
.env\Scriptsctivate
python -m flask --app manage.py worker --queues default,ai,docs
```

## Enable AI
Put your key in `.env`:
```
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.5
```
Then restart the app + worker.  
Run AI per client: open a client â†’ **AI Guidance** â†’ **Run Analysis**.


## Drag & Drop + Multi-file uploads
- Client Portal: drag & drop multiple files at once
- Staff Documents page: drag & drop multiple files, search, and download

## Employee AI access
Employees and Director can run AI per client:
Client â†’ **AI Guidance** â†’ **Run Analysis**


## New: Client Checklist + Portal Letters
- Portal enablement auto-seeds an onboarding checklist and posts a welcome letter into the client portal.
- Staff can manage checklist progress: Client â†’ Checklist.
- Staff can post letters into portal: Client â†’ Letters.


## New: One-click letter generation (DOCX + Web Copy + Email Draft)
- Generate any template: Client â†’ Templates â†’ Generate
- Output page provides:
  - **Download DOCX** (Word document saved under client exports)
  - **Copy Web Copy** (clipboard)
  - **Email Draft** (subject + body ready to paste; mailto convenience)


## New: Nexus Builder (Nexus Packet Generator)
Client â†’ **Nexus Builder**
- Generates a provider-facing nexus request letter (DOCX + web copy)
- Generates a client instructions email draft
- Auto-injects latest AI summary into the provider request for fast context

## New: Generated Docs Library
Client â†’ **Generated Docs**
- Search every generated artifact
- Download DOCX
- Open Email Draft


## New: Public “Become a Client” onboarding
Public homepage CTA â†’ **Become a Client**
- Creates Client + Portal account immediately
- Displays a one-time temporary password
- Forces password reset on first login
- Seeds checklist + welcome letter automatically

## New: Editable homepage copy (Director)
Director â†’ **Site Settings**
- Edit hero title/subtitle, CTA buttons, and feature copy without touching code.


## New: Invite Links (Director)
Director â†’ **Invites**
- Generate one-time secure invite links for Clients or Employees
- Tokens are stored hashed (not plaintext)
- Invite accept page lets the user set their own password


## New: Staff Inbox + Rep-based message access
- Staff â†’ **Inbox** shows assigned client conversations with unread counts.
- Employees can only open threads for clients assigned to them (Director can see all).
- Viewing a thread updates last-seen timestamps to power unread badges.


