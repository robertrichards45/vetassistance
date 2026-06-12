from string import Template
from datetime import date

DEFAULT_TEMPLATES = [
  {"name":"Welcome Letter","category":"Onboarding","body":
"""Welcome to Veteran Benefits Assistance, and thank you for trusting us to support you. We are honored to work with you and to help you navigate your benefits with clarity, organization, and purpose.

Our mission is simple: to help veterans understand, organize, and act on the benefits they have earned - without confusion, unnecessary delays, or guesswork.

What Happens Next

Your account has been created, and you now have access to your Client Portal, which will be your primary hub throughout this process. Inside the portal, you can:

Upload documents securely
Send and receive messages with our team
View your task checklist and next steps
Download letters, summaries, and shared documents

You will see a list of requested tasks in your portal. These items help us understand your history, identify evidence gaps, and determine the best path forward.

If you are missing any records, do not worry - upload what you have and let us know. Missing records are common, and we can help you work through them.

Documents to Upload (If You Have Them)

Please upload all applicable documents from the list below. If something does not apply to you or you do not have it, simply skip it or send us a message.

Military & VA Records

DD214(s) or separation documents (all periods of service)
All VA Rating Decision Letters (every decision, any year)
VA Award Letters and benefit summaries
VA Codesheets (if available)

VA Medical & Exam Records

C&P Exam Reports (all exams, all periods)
VA treatment records related to your claimed conditions
DBQs (Disability Benefits Questionnaires), if completed

Civilian Medical Records

Private doctor or hospital records related to your conditions
Mental health treatment records (VA or civilian)
Specialist evaluations or diagnoses
Prescription or treatment summaries (if relevant)

Supporting Statements & Evidence

Buddy statements (symptoms, events, functional impact)
Personal statements describing:

When symptoms started
How they affect daily life and work

Employer statements describing work limitations or accommodations (if applicable)
Line of Duty (LOD) documents, incident reports, or service records (if applicable)

Additional Helpful Items (If Available)

Social Security Disability determinations (if any)
Prior appeal or denial paperwork
Any correspondence from the VA you believe is important
Timelines, notes, or summaries you've created yourself

Upload Tips

Accepted file types: PDF, JPG, PNG, DOCX
Large files may be uploaded in parts
Clear photos or scans are preferred
Do not worry about perfect organization - we will sort and review everything

What We Do (and What We Don't)

We focus on education, organization, and evidence readiness. Our role is to help you:

Understand your benefits (federal, state, and local)
Organize your records and documentation
Identify missing or weak evidence
Prepare clear, structured statements and supporting materials
Avoid common mistakes that delay or harm claims

We do not guarantee outcomes, make medical determinations, or replace the Department of Veterans Affairs. Every case is unique, and results depend on evidence, eligibility, and VA review.

Communication & Support

You can message us at any time through the Client Portal. For security reasons, sensitive case details are handled inside the portal, not through public email.

If you are unsure whether a document applies, upload it anyway or send us a message - we will advise you.

A Veteran-Focused Approach

This platform was built with veterans in mind - straightforward, organized, and respectful of your time. Our goal is to remove confusion and help you move forward with confidence and a clear plan.

You served your country.
You earned these benefits.
We're here to help you pursue them the right way.

Respectfully,

Veteran Benefits Assistance
Veterans Helping Veterans
www.vetassistance.org
Email: veteranclaimsassistance@gmail.com
Phone: 229-848-1633
"""},
  {"name":"Evidence Request Checklist","category":"Evidence","body":
"""EVIDENCE CHECKLIST - $client_name
Date: $today

Please upload any items you have available:
- VA decision letters (all periods)
- C&P exams (all)
- Service treatment records (STRs)
- Private treatment records (primary care + specialists)
- DBQs (if available)
- Imaging reports (MRI/X-ray/CT)
- Buddy statements (symptoms + functional impact)
- Employer statements (attendance/performance impacts)

Reply in the portal if anything is missing or you need help locating records.

$rep_name
"""},
  {"name":"Buddy Statement Guide","category":"Evidence","body":
"""BUDDY STATEMENT GUIDE - $client_name
Date: $today

Purpose: a clear, credible statement describing what was observed.

Please include:
1) Relationship to the veteran (coworker, spouse, battle buddy, friend)
2) What was observed (symptoms, behaviors, limitations)
3) When it started and how it has changed over time
4) Real-world impact (work, daily life, relationships)

Keep it factual, specific, and based on personal observation. Dates and examples are helpful.

$rep_name
"""},
  {"name":"Employer Statement Request","category":"Evidence","body":
"""EMPLOYER STATEMENT REQUEST - $client_name
Date: $today

If possible, please provide a brief statement from a supervisor or HR confirming:
- Job title and dates of employment
- Attendance issues or accommodations
- Performance impact related to medical conditions
- Any reduced duties or schedule changes

Thank you for your assistance.

$rep_name
"""},
  {"name":"Nexus Request to Provider","category":"Medical","body":
"""NEXUS REQUEST - $client_name
Date: $today

Provider name: ____________________
Facility: _________________________

We are requesting a medical opinion addressing whether the veteran's condition is at least as likely as not related to military service.

Please consider:
- Current diagnosis and symptoms
- In-service event or exposure (if documented)
- Continuity of symptoms after service
- Functional impact

Thank you for your time and assistance.

$rep_name
"""},
  {"name":"C&P Exam Preparation","category":"Exams","body":
"""C&P EXAM PREP - $client_name
Date: $today

Purpose: describe symptoms and functional impact clearly and consistently.
- Frequency, severity, duration
- Triggers
- Work and daily-life effects
- Flare-ups and limitations
- Missed work / accommodations

Please bring a list of medications, dates of treatment, and any key changes in severity.

$rep_name
"""},
  {"name":"Missing Records Reconstruction","category":"Evidence","body":
"""MISSING RECORDS PLAN - $client_name
Date: $today

We are missing records from one or more time periods. Please help us reconstruct the timeline.

Please provide:
- Approximate dates of care
- Facility names and locations
- Provider names if known
- Any alternate proof (prescriptions, appointment notes, letters)

We will use this information to request records or locate alternate documentation.

$rep_name
"""},
  {"name":"Decision Review Summary","category":"Claims","body":
"""DECISION REVIEW SUMMARY - $client_name
Date: $today

We reviewed your VA decision letter. Summary:
- Granted: __________________________
- Denied: ___________________________
- Deferred: _________________________

Next steps will be posted in the portal after we confirm any missing evidence and timelines.

$rep_name
"""},
  {"name":"Supplemental Claim Cover Letter","category":"Claims","body":
"""SUPPLEMENTAL CLAIM COVER LETTER - $client_name
Date: $today

This packet includes new and relevant evidence for a supplemental claim review.

Included:
- New evidence list
- Updated statements
- Supporting documents

Please associate all documents with the veteran's claim.

$rep_name
"""},
  {"name":"Higher-Level Review Cover Letter","category":"Claims","body":
"""HIGHER-LEVEL REVIEW COVER LETTER - $client_name
Date: $today

This request seeks Higher-Level Review of the prior decision based on the evidence of record.

Included:
- Summary of the contested issues
- Clarifying notes for reviewer

$rep_name
"""},
  {"name":"Dependency Update Request","category":"Claims","body":
"""DEPENDENCY UPDATE REQUEST - $client_name
Date: $today

Please update the veteran's dependents based on the attached documentation.

Included:
- Marriage certificate / divorce decree (if applicable)
- Birth certificates (if applicable)
- School status verification (if applicable)

$rep_name
"""},
  {"name":"VR&E Request","category":"Education","body":
"""VR&E REQUEST - $client_name
Date: $today

We are requesting consideration for Veteran Readiness and Employment (Chapter 31).

Included:
- Service-connected disability information
- Employment limitations summary
- Requested training or vocational path

$rep_name
"""},
  {"name":"GI Bill Education Benefits Request","category":"Education","body":
"""EDUCATION BENEFITS REQUEST - $client_name
Date: $today

Please provide guidance on GI Bill or education benefit eligibility and application steps.

Included:
- Service history summary
- School or program information (if available)

$rep_name
"""},
  {"name":"Hardship / Expedited Processing Request","category":"Claims","body":
"""HARDSHIP / EXPEDITED PROCESSING REQUEST - $client_name
Date: $today

The veteran is requesting expedited processing due to hardship.

Included:
- Hardship explanation
- Supporting documentation

$rep_name
"""},
  {"name":"Records Release Authorization Cover Letter","category":"Records","body":
"""RECORDS RELEASE COVER LETTER - $client_name
Date: $today

Attached is the authorization to release records for the veteran.

Please provide all records for the requested time period.

$rep_name
"""},
  {"name":"Service Agreement Summary","category":"Onboarding","body":
"""SERVICE AGREEMENT SUMMARY - $client_name
Date: $today

This is a summary of services provided:
1) Evidence organization and intake guidance
2) Packet formatting and document preparation
3) Claim support tools and checklists

No medical or legal advice is provided.

$rep_name
"""},
  {"name":"Buddy/Lay Statement","category":"Evidence","body":
"""BUDDY/LAY STATEMENT - $client_name
Date: $today

Please include:
- Your relationship to the veteran
- What you personally observed
- When it started and how it changed
- Specific examples of impact on daily life or work

Keep it factual and based on your direct observations.

$rep_name
"""},
  {"name":"Spouse Statement","category":"Evidence","body":
"""SPOUSE STATEMENT - $client_name
Date: $today

Please include:
- Your relationship to the veteran
- What you personally observed
- Changes over time
- Specific examples of impact on daily life

Keep it factual and based on your direct observations.

$rep_name
"""},
  {"name":"Employer Statement","category":"Evidence","body":
"""EMPLOYER STATEMENT - $client_name
Date: $today

If possible, please confirm:
- Job title and dates of employment
- Attendance or performance impacts
- Any accommodations or duty changes

Thank you for your assistance.

$rep_name
"""},
  {"name":"Nexus Request","category":"Medical","body":
"""NEXUS REQUEST - $client_name
Date: $today

We are requesting a medical opinion addressing whether the veteran's condition is at least as likely as not related to military service.

Please consider:
- Current diagnosis
- In-service event or exposure
- Continuity of symptoms
- Functional impact

Thank you for your time.

$rep_name
"""},
  {"name":"Hardship Request","category":"Claims","body":
"""HARDSHIP / EXPEDITED PROCESSING REQUEST - $client_name
Date: $today

The veteran is requesting expedited processing due to hardship.

Included:
- Hardship explanation
- Supporting documentation

$rep_name
"""},
]

LETTER_WIZARDS = {
  "Decision Review Summary": {
    "questions": [
      {"id": "decision_date", "label": "Decision date on the VA letter", "type": "date", "placeholder": "YYYY-MM-DD", "required": True, "help": "Use the exact date shown on the VA decision."},
      {"id": "granted", "label": "Granted issues and ratings", "type": "textarea", "placeholder": "List granted conditions and ratings", "required": False},
      {"id": "denied", "label": "Denied issues and reasons (if known)", "type": "textarea", "placeholder": "List denied conditions and brief reasons", "required": False},
      {"id": "deferred", "label": "Deferred items", "type": "textarea", "placeholder": "List deferred issues", "required": False},
      {"id": "next_steps", "label": "Next steps discussed", "type": "textarea", "placeholder": "Evidence needed, deadlines, actions", "required": False},
    ],
    "template": """DECISION REVIEW SUMMARY
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4
Decision date: $decision_date

Summary
Granted:
$granted

Denied:
$denied

Deferred:
$deferred

Next steps
$next_steps

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Dependency Update Request": {
    "questions": [
      {"id": "dependents", "label": "Dependents to add/update", "type": "textarea", "placeholder": "Name, relationship, DOB", "required": True},
      {"id": "documents", "label": "Documents provided", "type": "textarea", "placeholder": "Marriage cert, birth certs, school verification", "required": False},
      {"id": "effective_date", "label": "Requested effective date (if any)", "type": "date", "placeholder": "YYYY-MM-DD", "required": False},
      {"id": "notes", "label": "Notes", "type": "textarea", "placeholder": "Special circumstances", "required": False},
    ],
    "template": """DEPENDENCY UPDATE REQUEST
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Dependents to update
$dependents

Documents provided
$documents

Requested effective date
$effective_date

Notes
$notes

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Hardship / Expedited Processing Request": {
    "questions": [
      {"id": "hardship_reason", "label": "Hardship reason", "type": "textarea", "placeholder": "Financial, housing, medical, safety", "required": True},
      {"id": "deadline", "label": "Urgent deadlines", "placeholder": "Dates/time-sensitive details", "required": False},
      {"id": "supporting_docs", "label": "Supporting documents", "type": "textarea", "placeholder": "Eviction notice, shutoff notice, bills, medical letters", "required": False, "show_if": {"id": "hardship_reason", "includes_any": ["eviction", "homeless", "financial", "medical", "shutoff", "utility"]}},
      {"id": "contact_method", "label": "Best contact method", "placeholder": "Phone, email", "required": False},
    ],
    "template": """HARDSHIP / EXPEDITED PROCESSING REQUEST
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Hardship reason
$hardship_reason

Urgent deadlines
$deadline

Supporting documents
$supporting_docs

Best contact method
$contact_method

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Higher-Level Review Cover Letter": {
    "questions": [
      {"id": "decision_date", "label": "Decision date", "type": "date", "placeholder": "YYYY-MM-DD", "required": True},
      {"id": "issues", "label": "Issues for review", "type": "textarea", "placeholder": "List contested issues", "required": True},
      {"id": "error_summary", "label": "Summary of error", "type": "textarea", "placeholder": "Concise explanation of the error", "required": False},
      {"id": "conference", "label": "Informal conference requested?", "placeholder": "Yes or No", "required": False},
      {"id": "conference_phone", "label": "Conference phone", "placeholder": "Preferred phone number", "required": False, "show_if": {"id": "conference", "includes_any": ["yes", "y"]}},
      {"id": "notes", "label": "Notes for reviewer", "type": "textarea", "placeholder": "Clarifications, dates, evidence", "required": False},
    ],
    "template": """HIGHER-LEVEL REVIEW COVER LETTER
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4
Decision date: $decision_date

Issues for review
$issues

Summary of error
$error_summary

Informal conference requested
$conference
Conference phone
$conference_phone

Notes for reviewer
$notes

$rep_name
$org_name
$org_phone | $org_email
"""
  },

  "Supplemental Claim Cover Letter": {
    "questions": [
      {"id": "issues", "label": "Issues being supplemented", "type": "textarea", "placeholder": "List conditions/issues", "required": True},
      {"id": "new_evidence", "label": "New and relevant evidence", "type": "textarea", "placeholder": "Records, statements, exams", "required": True},
      {"id": "evidence_dates", "label": "Evidence date ranges", "type": "textarea", "placeholder": "Dates of treatment or records", "required": False},
      {"id": "notes", "label": "Notes", "type": "textarea", "placeholder": "Anything to emphasize", "required": False},
    ],
    "template": """SUPPLEMENTAL CLAIM COVER LETTER
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Issues being supplemented
$issues

New and relevant evidence included
$new_evidence

Evidence date ranges
$evidence_dates

Notes
$notes

$rep_name
$org_name
$org_phone | $org_email
"""
  },

  "GI Bill Education Benefits Request": {
    "questions": [
      {"id": "school_program", "label": "School or program", "type": "textarea", "placeholder": "School name, program, start date", "required": True},
      {"id": "service_summary", "label": "Service history summary", "type": "textarea", "placeholder": "Branch, dates, discharge", "required": False},
      {"id": "questions", "label": "Questions to VA", "type": "textarea", "placeholder": "Eligibility, remaining months, transferability", "required": False},
    ],
    "template": """EDUCATION BENEFITS REQUEST (GI BILL)
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

School/program
$school_program

Service history summary
$service_summary

Questions
$questions

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "VR&E Request": {
    "questions": [
      {"id": "employment_limits", "label": "Employment limitations", "type": "textarea", "placeholder": "How conditions limit work", "required": True},
      {"id": "training_goal", "label": "Requested training or goal", "type": "textarea", "placeholder": "Target role or program", "required": True},
      {"id": "service_connected", "label": "Service-connected info", "type": "textarea", "placeholder": "Conditions/ratings", "required": False},
    ],
    "template": """VR&E REQUEST
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Employment limitations
$employment_limits

Requested training/goal
$training_goal

Service-connected info
$service_connected

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Buddy Statement Guide": {
    "questions": [
      {"id": "buddy_name", "label": "Buddy name", "placeholder": "Full name", "required": False},
      {"id": "relationship", "label": "Relationship to the veteran", "placeholder": "Battle buddy, friend, coworker", "required": True},
      {"id": "observations", "label": "Key observations", "type": "textarea", "placeholder": "Symptoms, behaviors, limitations", "required": True},
      {"id": "timeline", "label": "Timeline", "type": "textarea", "placeholder": "When it started and changes over time", "required": False},
      {"id": "impact", "label": "Impact", "type": "textarea", "placeholder": "Work, daily life, relationships", "required": False},
    ],
    "template": """BUDDY STATEMENT GUIDE
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Buddy name
$buddy_name

Relationship
$relationship

Key observations
$observations

Timeline
$timeline

Impact
$impact

$rep_name
$org_name
$org_phone | $org_email
"""
  },

  "Employer Statement Request": {
    "questions": [
      {"id": "employer_name", "label": "Employer or supervisor name", "placeholder": "Company or supervisor", "required": True},
      {"id": "job_title", "label": "Job title and dates", "placeholder": "Role and employment dates", "required": False},
      {"id": "impact", "label": "Work impact", "type": "textarea", "placeholder": "Attendance, accommodations, performance", "required": True},
      {"id": "contact_email", "label": "Employer contact email", "placeholder": "name@company.com", "required": False},
    ],
    "template": """EMPLOYER STATEMENT REQUEST
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Employer
$employer_name

Job title/dates
$job_title

Work impact to address
$impact

Contact email
$contact_email

$rep_name
$org_name
$org_phone | $org_email
"""
  },

  "Evidence Request Checklist": {
    "questions": [
      {"id": "missing_items", "label": "Missing evidence", "type": "textarea", "placeholder": "List what we still need", "required": True},
      {"id": "deadlines", "label": "Deadlines", "type": "textarea", "placeholder": "Any time-sensitive items", "required": False},
      {"id": "upload_tips", "label": "Upload tips", "type": "textarea", "placeholder": "Preferred file types, naming tips", "required": False},
    ],
    "template": """EVIDENCE REQUEST CHECKLIST
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Missing items
$missing_items

Deadlines
$deadlines

Upload tips
$upload_tips

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Missing Records Reconstruction": {
    "questions": [
      {"id": "care_dates", "label": "Approximate dates of care", "type": "textarea", "placeholder": "YYYY-MM-DD to YYYY-MM-DD", "required": True},
      {"id": "facilities", "label": "Facilities/locations", "type": "textarea", "placeholder": "VA, private clinics, hospitals", "required": True},
      {"id": "providers", "label": "Provider names", "type": "textarea", "placeholder": "If known", "required": False},
      {"id": "alternate_proof", "label": "Alternate proof", "type": "textarea", "placeholder": "Prescriptions, bills, notes", "required": False},
    ],
    "template": """MISSING RECORDS RECONSTRUCTION
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Approximate dates of care
$care_dates

Facilities/locations
$facilities

Providers
$providers

Alternate proof
$alternate_proof

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "C&P Exam Preparation": {
    "questions": [
      {"id": "conditions", "label": "Conditions being evaluated", "type": "textarea", "placeholder": "List conditions", "required": True},
      {"id": "symptoms", "label": "Key symptoms", "type": "textarea", "placeholder": "Frequency, severity, duration", "required": True},
      {"id": "impact", "label": "Functional impact", "type": "textarea", "placeholder": "Work, daily life, flare-ups", "required": False},
      {"id": "meds", "label": "Medications/treatments", "type": "textarea", "placeholder": "Current meds, therapies", "required": False},
    ],
    "template": """C&P EXAM PREPARATION
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Conditions being evaluated
$conditions

Key symptoms
$symptoms

Functional impact
$impact

Medications/treatments
$meds

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Nexus Request to Provider": {
    "questions": [
      {"id": "provider_name", "label": "Provider name", "placeholder": "Doctor/clinic", "required": True},
      {"id": "facility", "label": "Facility name", "placeholder": "Clinic/hospital", "required": False},
      {"id": "diagnosis", "label": "Current diagnosis", "type": "textarea", "placeholder": "Condition and summary", "required": True},
      {"id": "in_service_event", "label": "In-service event/exposure", "type": "textarea", "placeholder": "Details", "required": False},
      {"id": "continuity", "label": "Continuity of symptoms", "type": "textarea", "placeholder": "After service to present", "required": False},
      {"id": "records_reviewed", "label": "Records to consider", "type": "textarea", "placeholder": "STRs, exams, imaging, treatment notes", "required": False},
    ],
    "template": """NEXUS REQUEST TO PROVIDER
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Provider: $provider_name
Facility: $facility

Current diagnosis
$diagnosis

In-service event/exposure
$in_service_event

Continuity of symptoms
$continuity

Records to consider
$records_reviewed

$rep_name
$org_name
$org_phone | $org_email
"""
  },

  "Service Agreement Summary": {
    "questions": [
      {"id": "services", "label": "Services included", "type": "textarea", "placeholder": "Evidence intake, letters, packet prep", "required": True},
      {"id": "expectations", "label": "Client expectations", "type": "textarea", "placeholder": "Upload documents, respond to tasks", "required": False},
      {"id": "fees", "label": "Fees or billing notes", "type": "textarea", "placeholder": "If applicable", "required": False},
    ],
    "template": """SERVICE AGREEMENT SUMMARY
Date: $today

Client: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Thank you for choosing $org_name. This summary confirms what we covered and how we will support your case with clarity and care.

Services included
$services

What we will deliver
- Evidence organization and intake guidance
- Clear checklists and document requests
- Professional draft letters and summaries
- Secure portal for uploads and communication

What we need from you
$expectations

Fees or billing notes
$fees

If anything in this summary needs to be updated, let us know right away so we can correct it.

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Welcome Letter": {
    "questions": [
      {"id": "next_steps", "label": "Next steps discussed", "type": "textarea", "placeholder": "Portal access, key tasks", "required": True},
      {"id": "documents", "label": "Documents requested", "type": "textarea", "placeholder": "Decision letters, C&P, STRs", "required": False},
      {"id": "notes", "label": "Notes", "type": "textarea", "placeholder": "Special instructions", "required": False},
    ],
    "template": """WELCOME LETTER
Date: $today

Welcome, $client_name.

Next steps
$next_steps

Documents requested
$documents

Notes
$notes

$rep_name
$org_name
$org_phone | $org_email
"""
  },
  "Records Release Authorization Cover Letter": {
    "questions": [
      {"id": "facility", "label": "Facility or records holder", "type": "textarea", "placeholder": "VA, hospital, clinic", "required": True},
      {"id": "date_range", "label": "Date range requested", "placeholder": "YYYY-MM-DD to YYYY-MM-DD", "required": True},
      {"id": "records_type", "label": "Type of records requested", "type": "textarea", "placeholder": "Treatment notes, imaging, labs", "required": False},
      {"id": "notes", "label": "Notes", "type": "textarea", "placeholder": "Any special requests", "required": False},
    ],
    "template": """RECORDS RELEASE AUTHORIZATION COVER LETTER
Date: $today

Veteran: $client_name
Claim #: $client_claim_number
DOB: $client_dob
SSN (last 4): $client_ssn_last4

Facility/records holder
$facility

Date range requested
$date_range

Records requested
$records_type

Notes
$notes

$rep_name
$org_name
$org_phone | $org_email
"""
  },
}

def render(template_body: str, context: dict) -> str:
    ctx = dict(context); ctx.setdefault("today", date.today().isoformat())
    return Template(template_body).safe_substitute(ctx)


