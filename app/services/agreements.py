from __future__ import annotations

from datetime import datetime


DEFAULT_AGREEMENT_TITLE = "Service Agreement"


def _format_services(services: list[str] | None) -> str:
    if not services:
        return ""
    lines = "\n".join(f"- {s}" for s in services)
    return f"Selected Services:\n{lines}\n\n"


def render_default_agreement(client_name: str, company_name: str, services: list[str] | None = None) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    services_block = _format_services(services)
    return (
        f"SERVICE AGREEMENT\n\n"
        f"This Service Agreement (\"Agreement\") is entered into by and between:\n"
        f"Client: {client_name}\n"
        f"Provider: {company_name}\n"
        f"Effective Date: {today}\n\n"
        "1) Scope of Services\n"
        "- Provider will deliver administrative support, evidence organization, and claim support tools.\n"
        "- Services are limited to non-legal, non-medical support and do not include representation before any agency.\n"
        "- Client understands outcomes are not guaranteed.\n\n"
        f"{services_block}"
        "2) Fees, Payment, and Authorization\n"
        "- Fees are disclosed in the pricing section and confirmed before work begins.\n"
        "- Percentage-based fees, if applicable, apply only to eligible awards as described in the pricing terms.\n"
        "- Client authorizes Provider to prepare administrative documents and summaries based on Client-supplied information.\n\n"
        "3) Client Responsibilities\n"
        "- Provide accurate, complete, and timely information and documents.\n"
        "- Review drafts and forms for accuracy before submission.\n"
        "- Notify Provider of deadlines and time-sensitive matters.\n\n"
        "4) Communications and Records\n"
        "- The secure portal is the primary channel for messages and document exchange.\n"
        "- Client consents to receiving notices and copies through the portal and email.\n"
        "- Client is responsible for maintaining current contact information.\n\n"
        "5) Confidentiality\n"
        "- Provider will handle client information using reasonable administrative safeguards.\n"
        "- Client acknowledges that electronic communications carry inherent risks.\n\n"
        "6) Term and Termination\n"
        "- This Agreement remains in effect until completed or terminated by either party in writing.\n"
        "- Upon termination, Client remains responsible for any fees incurred for work already performed.\n\n"
        "7) Limitation of Scope and Disclaimers\n"
        "- Provider does not offer legal or medical advice and does not guarantee outcomes.\n"
        "- Client is responsible for all submissions and decisions made with any agency.\n\n"
        "8) Independent Contractor\n"
        "- Provider acts as an independent contractor and not as an employee, agent, or fiduciary of Client.\n\n"
        "9) Entire Agreement\n"
        "- This Agreement reflects the entire understanding regarding the services described above.\n"
        "- Any modifications must be in writing and acknowledged by both parties.\n\n"
        "10) Governing Law\n"
        "- This Agreement is governed by the laws of the State of Georgia, without regard to conflict-of-laws rules.\n\n"
        "11) Severability\n"
        "- If any provision of this Agreement is held unenforceable, the remaining provisions will remain in effect.\n\n"
        "12) Electronic Signature Consent\n"
        "- The parties agree that electronic signatures and records are valid and enforceable to the fullest extent permitted by law.\n\n"
        "ACKNOWLEDGMENT AND SIGNATURE\n"
        "By signing electronically, the client acknowledges and agrees to the terms of this Agreement.\n"
    )
