"""
Seed a realistic HR-domain dataset into Postgres without declared FK constraints.

Schema layout (default schema: ``hr``):

  Reference / lookup       countries, regions, currencies, languages,
                           employment_types, pay_grades, leave_types,
                           competencies, skill_taxonomy, skills,
                           benefit_plans, training_programs, certifications,
                           shifts, public_holidays, review_cycles,
                           pay_components, document_types
  Org structure            cost_centers, locations, departments, jobs
  People                   employees, candidates
  Recruiting               job_postings, applications, interviews,
                           interview_feedback, offers, background_checks
  Compensation             salaries, payroll_runs, payroll_entries,
                           bonuses, compensation_changes
  Time / attendance        timesheets, time_entries, leave_requests,
                           leave_balances, shift_assignments
  Performance              performance_reviews, goals, competency_assessments,
                           promotion_history
  Learning                 training_enrollments, certification_holders
  Benefits                 benefit_enrollments, dependents, emergency_contacts
  Onboarding/offboarding   onboarding_tasks, onboarding_progress,
                           exit_interviews, termination_records
  HR ops                   documents, document_acknowledgements,
                           visa_statuses, incidents, disciplinary_actions,
                           grievances, employee_skills, hr_tickets,
                           hr_ticket_messages, announcements

Plus ~400 generated tables across the standard exclusion families
(*_log, *_events, *_bak, *_archive, temp_*, tmp_*, etl_*, migrations_*),
KPI/dim/fact reporting, junctions, wide-denormalised, and empty edge cases.

Total: ~480 tables, ~3M rows, **0 declared FK constraints**.

PII is scattered through `employees` (ssn, dob, work/personal email, phone),
`candidates` (email, phone, resume_text with embedded PII), `payroll_entries`
(iban, routing_number, bank_account), `dependents` (ssn, dob),
`emergency_contacts` (phone, email).

Usage:
    python3 seed_postgres_hr.py                  # populate hr schema
    python3 seed_postgres_hr.py --schema hr      # explicit
    python3 seed_postgres_hr.py --reset          # DROP first

Connection: localhost:5432 db=test user=adsuser pass=Ads@3421
"""
from __future__ import annotations

import argparse
import random
import secrets
import string
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Reuse helpers from the e-commerce seeder
from seed_postgres_500 import (
    DSN,
    FIRST_NAMES,
    LAST_NAMES,
    CITIES,
    COUNTRIES,
    CURRENCIES,
    SEED,
    Table,
    _gen_archive_table,
    _gen_empty_table,
    _gen_etl_table,
    _gen_event_table,
    _gen_junction_table,
    _gen_kpi_table,
    _gen_log_table,
    _gen_simple_dim,
    _gen_tmp_table,
    _gen_wide_table,
    conn_cur,
    copy_rows,
    gen_email,
    gen_iban,
    gen_phone_e164,
    gen_ssn,
    gen_uuid,
    luhn_card,
)

random.seed(SEED)


# ----------------------------------------------------------- HR shared context

CTX: dict[str, list] = {}

JOB_TITLES = [
    "Software Engineer", "Senior Engineer", "Staff Engineer", "Tech Lead",
    "Engineering Manager", "Director of Engineering", "VP Engineering",
    "Product Manager", "Senior PM", "Group PM", "Director of Product",
    "Designer", "Senior Designer", "Design Lead", "Head of Design",
    "Data Scientist", "ML Engineer", "Data Engineer", "Analytics Lead",
    "DevOps Engineer", "SRE", "Security Engineer", "QA Engineer",
    "Sales Rep", "Account Executive", "Sales Manager", "Sales Director",
    "Customer Success", "Support Specialist", "Support Lead",
    "Marketing Specialist", "Content Strategist", "Marketing Manager",
    "Recruiter", "Senior Recruiter", "HR Business Partner", "HR Director",
    "Finance Analyst", "Senior Finance Analyst", "Controller", "CFO",
    "Operations Specialist", "Operations Manager", "COO", "CEO",
    "Legal Counsel", "Paralegal", "Compliance Officer",
    "Office Manager", "Executive Assistant", "Receptionist",
    "Intern - Engineering", "Intern - Product", "Intern - Marketing",
    "Contractor - Engineering", "Contractor - Design",
]
DEPT_NAMES = [
    "Engineering", "Product", "Design", "Data", "Infrastructure", "Security",
    "Sales", "Marketing", "Customer Success", "Customer Support",
    "Finance", "Accounting", "Legal", "Compliance", "HR", "Recruiting",
    "Operations", "Strategy", "Communications", "Training",
    "Research", "Quality Assurance", "DevOps", "BI / Analytics",
    "Logistics", "Procurement", "Risk", "Audit", "Treasury", "Tax",
]
LOCATION_CITIES = CITIES + ["Austin", "Boston", "Seattle", "San Francisco",
                            "Chicago", "Denver", "Bangalore", "Hyderabad",
                            "Pune", "Bristol", "Edinburgh", "Munich",
                            "Hamburg", "Lyon", "Barcelona", "Milan", "Tel Aviv"]
LEAVE_TYPES = ["Annual", "Sick", "Personal", "Bereavement", "Maternity",
               "Paternity", "Sabbatical", "Jury Duty", "Military", "Unpaid"]
PERF_RATINGS = ["Below", "Meets", "Exceeds", "Outstanding"]
EMP_TYPES = ["Full-Time", "Part-Time", "Contractor", "Intern", "Temporary"]
COMPETENCIES = ["Communication", "Leadership", "Technical Excellence",
                "Customer Focus", "Strategic Thinking", "Collaboration",
                "Problem Solving", "Ownership", "Innovation", "Mentorship",
                "Project Management", "Domain Expertise", "Business Acumen",
                "Decision Making", "Coaching"]
DOC_TYPES = ["Offer Letter", "NDA", "Employee Handbook", "PIP",
             "Performance Review", "Termination Letter", "Promotion Letter",
             "Compensation Letter", "Benefits Enrollment", "I-9 / Right-to-Work"]
LEAVE_STATUSES = ["pending", "approved", "rejected", "cancelled"]
APP_STATUSES = ["applied", "screening", "interviewing", "offer", "hired", "rejected", "withdrawn"]
SHIFTS_DEF = [("Morning", "06:00", "14:00"), ("Day", "09:00", "17:00"),
              ("Evening", "14:00", "22:00"), ("Night", "22:00", "06:00"),
              ("Split", "10:00", "19:00"), ("Weekend", "10:00", "18:00")]


# ----------------------------------------------------------- generators

def t_regions(rng: random.Random):
    rows = [(i, n) for i, n in enumerate(
        ["Americas", "EMEA", "APAC", "LATAM", "MEA", "Oceania"], start=1)]
    CTX["region_ids"] = [r[0] for r in rows]
    return rows


def t_countries(rng: random.Random):
    region_ids = CTX["region_ids"]
    name_by_code = {
        "US": ("United States", 1), "CA": ("Canada", 1), "MX": ("Mexico", 4),
        "BR": ("Brazil", 4), "AR": ("Argentina", 4),
        "GB": ("United Kingdom", 2), "DE": ("Germany", 2), "FR": ("France", 2),
        "ES": ("Spain", 2), "IT": ("Italy", 2), "NL": ("Netherlands", 2),
        "IE": ("Ireland", 2), "PL": ("Poland", 2), "SE": ("Sweden", 2),
        "AE": ("United Arab Emirates", 5), "EG": ("Egypt", 5),
        "ZA": ("South Africa", 5),
        "IN": ("India", 3), "JP": ("Japan", 3), "SG": ("Singapore", 3),
        "AU": ("Australia", 6), "NZ": ("New Zealand", 6),
        "TR": ("Türkiye", 2), "IL": ("Israel", 5),
    }
    rows = []
    for i, (code, (name, region)) in enumerate(name_by_code.items(), start=1):
        rows.append((i, code, name, region))
    CTX["country_codes"] = [r[1] for r in rows]
    CTX["country_ids"] = [r[0] for r in rows]
    return rows


def t_currencies(rng: random.Random):
    items = list(zip(range(1, len(CURRENCIES) + 1), CURRENCIES))
    return [(i, code, f"{code} currency") for i, code in items]


def t_languages(rng: random.Random):
    langs = ["en", "es", "de", "fr", "it", "pt", "ja", "zh", "ar", "hi",
             "tr", "nl", "pl", "ru", "ko", "he", "sv"]
    return [(i + 1, c, c.upper()) for i, c in enumerate(langs)]


def t_employment_types(rng):
    return [(i + 1, n) for i, n in enumerate(EMP_TYPES)]


def t_pay_grades(rng: random.Random):
    rows = []
    for i in range(1, 21):
        floor = 30000 + (i - 1) * 12000
        ceil = floor + 22000
        rows.append((i, f"L{i:02d}", floor, ceil))
    CTX["pay_grade_ids"] = [r[0] for r in rows]
    return rows


def t_cost_centers(rng: random.Random):
    n = 30
    rows = []
    for i in range(1, n + 1):
        parent = None if i <= 6 else rng.randint(1, 6)
        rows.append((i, f"CC-{i:04d}", f"Cost Center {i}", parent))
    CTX["cost_center_ids"] = [r[0] for r in rows]
    return rows


def t_locations(rng: random.Random):
    n = 50
    cc = CTX["country_codes"]
    rows = []
    for i in range(1, n + 1):
        city = rng.choice(LOCATION_CITIES)
        country = rng.choice(cc)
        rows.append((
            i, f"{city} Office",
            f"{rng.randint(100, 9999)} {rng.choice(LAST_NAMES).title()} St",
            city, country, f"{rng.randint(10000, 99999)}",
        ))
    CTX["location_ids"] = [r[0] for r in rows]
    return rows


def t_jobs(rng: random.Random):
    pgs = CTX["pay_grade_ids"]
    rows = []
    for i, title in enumerate(JOB_TITLES, start=1):
        if any(s in title for s in ("VP", "Director", "CEO", "CFO", "COO")):
            grade = rng.choice([15, 16, 17, 18, 19, 20])
        elif "Senior" in title or "Lead" in title or "Manager" in title:
            grade = rng.choice([10, 11, 12, 13, 14])
        elif "Intern" in title:
            grade = rng.choice([1, 2, 3])
        elif "Contractor" in title:
            grade = rng.choice([7, 8, 9, 10])
        else:
            grade = rng.choice([5, 6, 7, 8, 9, 10])
        rows.append((i, title, grade))
    CTX["job_ids"] = [r[0] for r in rows]
    CTX["job_titles_by_id"] = {r[0]: r[1] for r in rows}
    return rows


def t_leave_types(rng):
    rows = [(i + 1, n) for i, n in enumerate(LEAVE_TYPES)]
    CTX["leave_type_ids"] = [r[0] for r in rows]
    return rows


def t_competencies(rng):
    rows = []
    cats = ["Behavioral", "Technical", "Leadership"]
    for i, n in enumerate(COMPETENCIES, start=1):
        rows.append((i, n, rng.choice(cats)))
    CTX["competency_ids"] = [r[0] for r in rows]
    return rows


def t_skill_taxonomy(rng):
    parts = ["Engineering", "Design", "Sales", "Operations", "Finance",
             "HR", "Marketing", "Product", "Data", "Customer", "Legal",
             "Security", "Infra", "Compliance", "Strategy"]
    rows = []
    for i, p in enumerate(parts, start=1):
        rows.append((i, p, None if i <= 4 else rng.choice([1, 2, 3, 4])))
    CTX["skill_taxonomy_ids"] = [r[0] for r in rows]
    return rows


def t_skills(rng):
    skills = []
    base = ["Python", "Java", "Go", "Rust", "TypeScript", "JavaScript",
            "SQL", "PostgreSQL", "MySQL", "Kafka", "Spark", "Airflow",
            "AWS", "GCP", "Azure", "Kubernetes", "Docker", "Terraform",
            "React", "Vue", "Figma", "Sketch", "Adobe XD",
            "Salesforce", "HubSpot", "Tableau", "Looker", "PowerBI",
            "Negotiation", "Public Speaking", "Project Management",
            "Agile / Scrum", "Hiring", "Mentoring", "Strategy",
            "Forecasting", "Modeling", "Tax", "Audit", "GAAP",
            "GDPR", "SOC2", "HIPAA", "PCI", "ISO 27001",
            "Customer Discovery", "User Research", "A/B Testing",
            "Machine Learning", "Deep Learning", "NLP", "Computer Vision",
            "ETL", "Data Warehousing", "DBT", "Snowflake", "Redshift",
            "Linux", "Networking", "Cryptography", "Pentesting",
            "Spanish", "French", "German", "Mandarin", "Hindi", "Japanese",
    ]
    rows = []
    txn = CTX["skill_taxonomy_ids"]
    for i, s in enumerate(base, start=1):
        rows.append((i, s, rng.choice(txn)))
    CTX["skill_ids"] = [r[0] for r in rows]
    return rows


def t_benefit_plans(rng):
    plans = ["Medical PPO", "Medical HMO", "Dental Standard", "Dental Plus",
             "Vision", "Life Insurance Basic", "Life Insurance Plus",
             "Disability Short-term", "Disability Long-term",
             "401k Basic", "401k Plus", "ESPP", "RSU Vest",
             "Wellness Stipend", "Education Stipend",
             "Commuter Benefits", "Pet Insurance"]
    providers = ["Aetna", "Cigna", "UHC", "Kaiser", "BlueCross",
                 "MetLife", "Prudential", "Fidelity", "Vanguard"]
    rows = []
    for i, name in enumerate(plans, start=1):
        rows.append((i, name, rng.choice(providers),
                     rng.choice(["Health", "Retirement", "Insurance", "Wellness"])))
    CTX["benefit_plan_ids"] = [r[0] for r in rows]
    return rows


def t_training_programs(rng):
    titles = [
        "Onboarding 101", "Manager Essentials", "Inclusion & Belonging",
        "Cybersecurity Awareness", "GDPR & Data Privacy", "Leadership Lab",
        "Public Speaking", "Negotiation Skills", "Conflict Resolution",
        "Performance Management", "Coaching for Managers", "Time Management",
        "Sales Mastery", "Product Discovery", "Customer Empathy",
        "SQL Fundamentals", "Cloud Architecture", "DevOps Foundations",
        "Machine Learning Bootcamp", "Design Thinking",
        "Project Management Pro", "Agile Mastery", "OKRs Workshop",
        "Cross-cultural Communication", "Mental Health First Aid",
        "Anti-bribery & Corruption", "Code of Conduct",
        "Travel & Expense Policy", "Vendor Management",
        "Risk & Compliance Basics",
    ]
    rows = []
    for i, name in enumerate(titles, start=1):
        rows.append((i, name, rng.randint(2, 40),
                     rng.choice(["instructor-led", "online", "hybrid"])))
    CTX["training_program_ids"] = [r[0] for r in rows]
    return rows


def t_certifications(rng):
    certs = ["PMP", "CSM", "CSPO", "AWS Solutions Architect", "AWS Developer",
             "GCP Architect", "Azure Admin", "CISSP", "CEH", "CompTIA Sec+",
             "ITIL Foundations", "Six Sigma Green", "Six Sigma Black",
             "CFA Level 1", "CFA Level 2", "CPA", "CMA", "CIPP/E", "CIPM",
             "ScrumMaster", "SAFe Agilist", "Kubernetes CKA", "Kubernetes CKAD",
             "Salesforce Admin", "Tableau Specialist",
             "Google Analytics", "HubSpot Inbound", "PRINCE2"]
    issuers = ["PMI", "Scrum Alliance", "AWS", "Google", "Microsoft",
               "ISC2", "EC-Council", "CompTIA", "ACAMS", "AICPA"]
    rows = []
    for i, name in enumerate(certs, start=1):
        rows.append((i, name, rng.choice(issuers)))
    CTX["certification_ids"] = [r[0] for r in rows]
    return rows


def t_shifts(rng):
    rows = []
    for i, (name, s, e) in enumerate(SHIFTS_DEF, start=1):
        rows.append((i, name, s, e))
    CTX["shift_ids"] = [r[0] for r in rows]
    return rows


def t_public_holidays(rng):
    cc = CTX["country_codes"]
    rows = []
    next_id = 1
    for code in cc:
        for _ in range(rng.randint(8, 14)):
            d = date(rng.choice([2024, 2025, 2026]),
                     rng.randint(1, 12),
                     rng.randint(1, 28))
            rows.append((next_id, code, d, f"Holiday in {code}"))
            next_id += 1
    return rows


def t_review_cycles(rng):
    rows = []
    for i, year in enumerate([2023, 2024, 2025, 2026], start=1):
        for half in (1, 2):
            cid = (i - 1) * 2 + half
            start = date(year, 1 if half == 1 else 7, 1)
            end = date(year, 6 if half == 1 else 12, 30)
            rows.append((cid, f"{year} H{half}", start, end))
    CTX["review_cycle_ids"] = [r[0] for r in rows]
    return rows


def t_pay_components(rng):
    components = [("Base Salary", "base"), ("Annual Bonus", "bonus"),
                  ("RSU Vest", "equity"), ("ESPP Discount", "equity"),
                  ("Cell Allowance", "allowance"),
                  ("Commuter Allowance", "allowance"),
                  ("Wellness Stipend", "allowance"),
                  ("Health Premium", "deduction"),
                  ("Dental Premium", "deduction"),
                  ("Vision Premium", "deduction"),
                  ("401k Contribution", "deduction"),
                  ("Federal Tax", "tax"), ("State Tax", "tax"),
                  ("FICA", "tax"), ("Medicare", "tax"),
                  ("Garnishment", "deduction"),
                  ("Sign-on Bonus", "bonus"),
                  ("Retention Bonus", "bonus"),
                  ("Severance", "bonus"),
                  ("Reimbursement", "allowance")]
    rows = []
    for i, (name, kind) in enumerate(components, start=1):
        rows.append((i, name, kind))
    CTX["pay_component_ids"] = [r[0] for r in rows]
    return rows


def t_document_types(rng):
    rows = [(i + 1, n) for i, n in enumerate(DOC_TYPES)]
    CTX["document_type_ids"] = [r[0] for r in rows]
    return rows


def t_departments(rng):
    locs = CTX["location_ids"]
    rows = []
    for i, n in enumerate(DEPT_NAMES, start=1):
        parent = None if i <= 6 else rng.choice([1, 2, 3, 4, 5, 6])
        rows.append((i, n, rng.choice(locs), parent))
    CTX["department_ids"] = [r[0] for r in rows]
    return rows


def t_employees(rng):
    """5,000 employees with rich PII."""
    n = 5000
    pgs = CTX["pay_grade_ids"]
    deps = CTX["department_ids"]
    locs = CTX["location_ids"]
    jobs = CTX["job_ids"]
    ccs = CTX["cost_center_ids"]
    et_ids = list(range(1, len(EMP_TYPES) + 1))

    rows = []
    # First 80 are managers (no manager_id) so the self-ref pool exists.
    manager_pool = list(range(1, 81))
    for i in range(1, n + 1):
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        manager = None if i <= 80 else rng.choice(manager_pool)
        # Senior/director-level employees become managers themselves
        if 80 < i <= 800 and rng.random() < 0.25:
            manager_pool.append(i)
        rows.append((
            i, first, last,
            f"EMP-{i:06d}",
            gen_ssn(rng),
            (date.today() - timedelta(days=rng.randint(22 * 365, 65 * 365))),
            gen_email(rng, first, last),                    # work_email
            gen_email(rng, first, last) if rng.random() > 0.30 else None,  # personal_email
            gen_phone_e164(rng),                             # work phone
            gen_phone_e164(rng) if rng.random() > 0.20 else None,  # personal phone
            (date.today() - timedelta(days=rng.randint(30, 365 * 25))),  # hire date
            rng.choice(deps), rng.choice(locs), manager,
            rng.choice(jobs), rng.choice(pgs), rng.choice(ccs),
            rng.choice(et_ids),
            rng.choice(["active", "active", "active", "active", "on_leave",
                        "terminated", "active"]),
        ))
    CTX["employee_ids"] = [r[0] for r in rows]
    CTX["manager_ids"] = manager_pool
    return rows


def t_candidates(rng):
    n = 3000
    rows = []
    for i in range(1, n + 1):
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        # 5% of candidates have an embedded PII in their resume_text
        resume = (f"Experienced professional with {rng.randint(1, 25)} years. "
                  f"Skills: {rng.choice(['Python','Java','SQL','Sales','PM','Design'])}.")
        if rng.random() < 0.05:
            resume += (f" Reach me at {gen_email(rng, first, last)} "
                       f"or call {gen_phone_e164(rng)}.")
        referrer = (rng.choice(CTX["employee_ids"][:1500])
                    if rng.random() < 0.30 else None)
        rows.append((
            i, first, last,
            gen_email(rng, first, last),
            gen_phone_e164(rng),
            (date.today() - timedelta(days=rng.randint(20 * 365, 60 * 365))),
            resume,
            referrer,
            rng.choice(["new", "screening", "interviewing", "offer",
                        "hired", "rejected"]),
        ))
    CTX["candidate_ids"] = [r[0] for r in rows]
    return rows


def t_job_postings(rng):
    n = 500
    jobs = CTX["job_ids"]
    emps = CTX["employee_ids"][:500]
    rows = []
    for i in range(1, n + 1):
        rows.append((
            i, rng.choice(jobs), rng.choice(emps),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
            rng.choice(["open", "closed", "filled", "draft"]),
        ))
    CTX["posting_ids"] = [r[0] for r in rows]
    return rows


def t_applications(rng):
    n = 12000
    cands = CTX["candidate_ids"]
    posts = CTX["posting_ids"]
    rows = []
    for i in range(1, n + 1):
        rows.append((
            i, rng.choice(cands), rng.choice(posts),
            rng.choice(APP_STATUSES),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    CTX["application_ids"] = [r[0] for r in rows]
    return rows


def t_interviews(rng):
    n = 18000
    apps = CTX["application_ids"]
    emps = CTX["employee_ids"]
    rows = []
    for i in range(1, n + 1):
        rows.append((
            i, rng.choice(apps), rng.choice(emps),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
            rng.choice(["technical", "behavioral", "panel", "system_design",
                        "culture_fit", "hiring_manager"]),
        ))
    CTX["interview_ids"] = [r[0] for r in rows]
    return rows


def t_interview_feedback(rng):
    ints = CTX["interview_ids"]
    rows = []
    for i, iid in enumerate(ints, start=1):
        rows.append((
            i, iid, rng.randint(1, 5),
            f"Candidate showed {rng.choice(['strong', 'good', 'mixed', 'weak'])}"
            f" {rng.choice(['communication', 'problem solving', 'technical depth', 'culture alignment'])}.",
            rng.choice(["strong_hire", "hire", "lean_hire", "no_hire"]),
        ))
    return rows


def t_offers(rng):
    n = 2200
    apps = CTX["application_ids"][:n]
    cands = CTX["candidate_ids"]
    rows = []
    for i, aid in enumerate(apps, start=1):
        rows.append((
            i, aid, rng.choice(cands),
            round(rng.uniform(50000, 350000), 2),
            rng.choice(CURRENCIES),
            rng.choice(["pending", "accepted", "rejected", "expired"]),
        ))
    return rows


def t_background_checks(rng):
    cands = CTX["candidate_ids"]
    rows = []
    for i, cid in enumerate(cands[:2500], start=1):
        rows.append((
            i, cid,
            rng.choice(["pending", "passed", "flagged", "failed"]),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def t_job_history(rng):
    emps = CTX["employee_ids"]
    deps = CTX["department_ids"]
    jobs = CTX["job_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        n_changes = rng.choice([1, 1, 1, 2, 2, 3])
        for _ in range(n_changes):
            start = date.today() - timedelta(days=rng.randint(30, 365 * 12))
            end = (start + timedelta(days=rng.randint(180, 365 * 4))
                   if rng.random() < 0.6 else None)
            rows.append((next_id, eid, rng.choice(jobs), rng.choice(deps),
                         start, end))
            next_id += 1
    return rows


def t_payroll_runs(rng):
    rows = []
    for i in range(1, 51):
        run_date = date.today() - timedelta(days=i * 14)
        rows.append((i, run_date,
                     rng.choice(["finalised", "finalised", "finalised",
                                 "draft", "voided"])))
    CTX["payroll_run_ids"] = [r[0] for r in rows]
    return rows


def t_payroll_entries(rng):
    """5,000 employees * 50 runs = 250,000 entries. Big, but fast via COPY."""
    emps = CTX["employee_ids"]
    runs = CTX["payroll_run_ids"]
    rows = []
    next_id = 1
    for run in runs:
        for eid in emps:
            base = round(rng.uniform(2500, 18000), 2)
            net = round(base * rng.uniform(0.62, 0.78), 2)
            rows.append((
                next_id, eid, run, base, net,
                f"****{rng.randint(1000, 9999)}",  # bank_account_last4
                gen_iban(rng) if rng.random() > 0.3 else None,
                f"{rng.randint(100000000, 999999999)}",  # routing number (US)
                rng.choice(CURRENCIES),
            ))
            next_id += 1
    return rows


def t_salaries(rng):
    emps = CTX["employee_ids"]
    pcs = CTX["pay_component_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        n_components = rng.randint(2, 4)
        for _ in range(n_components):
            rows.append((
                next_id, eid, rng.choice(pcs),
                round(rng.uniform(30000, 250000), 2),
                rng.choice(CURRENCIES),
                date.today() - timedelta(days=rng.randint(30, 365 * 5)),
            ))
            next_id += 1
    return rows


def t_compensation_changes(rng):
    emps = CTX["employee_ids"]
    rows = []
    for i in range(1, 8001):
        eid = rng.choice(emps)
        prev = round(rng.uniform(50000, 200000), 2)
        new = round(prev * rng.uniform(1.0, 1.20), 2)
        approved_by = rng.choice(CTX["manager_ids"])
        rows.append((
            i, eid, prev, new, approved_by,
            date.today() - timedelta(days=rng.randint(30, 365 * 4)),
        ))
    return rows


def t_bonuses(rng):
    emps = CTX["employee_ids"]
    rows = []
    for i in range(1, 5001):
        rows.append((
            i, rng.choice(emps),
            round(rng.uniform(500, 50000), 2),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 4)),
            rng.choice(["sign-on", "annual", "spot", "retention", "referral"]),
        ))
    return rows


def t_timesheets(rng):
    emps = CTX["employee_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        for w in range(10):
            ps = date.today() - timedelta(days=(w + 1) * 7)
            pe = ps + timedelta(days=6)
            rows.append((next_id, eid, ps, pe,
                         rng.choice(["draft", "submitted", "approved", "rejected"])))
            next_id += 1
    CTX["timesheet_ids"] = [r[0] for r in rows]
    return rows


def t_time_entries(rng):
    """5,000 emps * 10 timesheets * ~10 entries each = 500K entries."""
    rows = []
    next_id = 1
    ts_ids = CTX["timesheet_ids"]
    # we need a mapping ts_id -> employee_id to keep the FK structure tight.
    # Walking ts_ids directly: every consecutive 10 belong to same employee
    emps = CTX["employee_ids"]
    for idx, tid in enumerate(ts_ids):
        eid = emps[idx // 10]
        for d in range(rng.randint(5, 7)):
            ci = datetime.now(timezone.utc) - timedelta(days=rng.randint(7, 90))
            co = ci + timedelta(hours=rng.uniform(7, 10))
            rows.append((next_id, tid, eid, ci, co))
            next_id += 1
    return rows


def t_leave_requests(rng):
    emps = CTX["employee_ids"]
    lts = CTX["leave_type_ids"]
    mgrs = CTX["manager_ids"]
    rows = []
    for i in range(1, 30001):
        start = date.today() - timedelta(days=rng.randint(0, 365 * 2))
        end = start + timedelta(days=rng.randint(1, 14))
        approved_by = rng.choice(mgrs) if rng.random() > 0.15 else None
        rows.append((
            i, rng.choice(emps), rng.choice(lts), start, end,
            approved_by, rng.choice(LEAVE_STATUSES),
        ))
    return rows


def t_leave_balances(rng):
    emps = CTX["employee_ids"]
    lts = CTX["leave_type_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        for lt in lts:
            rows.append((next_id, eid, lt, round(rng.uniform(0, 30), 1)))
            next_id += 1
    return rows


def t_shift_assignments(rng):
    emps = CTX["employee_ids"]
    sids = CTX["shift_ids"]
    rows = []
    for i in range(1, 100001):
        rows.append((
            i, rng.choice(sids), rng.choice(emps),
            date.today() - timedelta(days=rng.randint(0, 365)),
        ))
    return rows


def t_performance_reviews(rng):
    emps = CTX["employee_ids"]
    cycles = CTX["review_cycle_ids"]
    mgrs = CTX["manager_ids"]
    rows = []
    next_id = 1
    for cid in cycles:
        for eid in rng.sample(emps, k=min(2500, len(emps))):
            rows.append((next_id, eid, rng.choice(mgrs), cid,
                         rng.choice(PERF_RATINGS),
                         f"Review summary for emp {eid} in cycle {cid}."))
            next_id += 1
    CTX["review_ids"] = [r[0] for r in rows]
    return rows


def t_goals(rng):
    emps = CTX["employee_ids"]
    rids = CTX["review_ids"]
    rows = []
    next_id = 1
    for rid in rids:
        for _ in range(rng.randint(1, 4)):
            rows.append((next_id,
                         rng.choice(emps), rid,
                         f"Goal {next_id}: deliver {rng.choice(['feature','project','initiative'])}",
                         rng.choice(["draft", "in_progress", "achieved", "missed"])))
            next_id += 1
    return rows


def t_competency_assessments(rng):
    emps = CTX["employee_ids"]
    cids = CTX["competency_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        for cid in rng.sample(cids, k=rng.randint(3, 6)):
            rows.append((
                next_id, eid, cid, rng.randint(1, 5),
                date.today() - timedelta(days=rng.randint(0, 365)),
            ))
            next_id += 1
    return rows


def t_promotion_history(rng):
    emps = rng.sample(CTX["employee_ids"], k=2500)
    jobs = CTX["job_ids"]
    rows = []
    for i, eid in enumerate(emps, start=1):
        rows.append((
            i, eid, rng.choice(jobs), rng.choice(jobs),
            date.today() - timedelta(days=rng.randint(60, 365 * 5)),
        ))
    return rows


def t_training_enrollments(rng):
    emps = CTX["employee_ids"]
    progs = CTX["training_program_ids"]
    rows = []
    for i in range(1, 12001):
        rows.append((
            i, rng.choice(emps), rng.choice(progs),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 3)),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 30))
            if rng.random() > 0.4 else None,
        ))
    return rows


def t_certification_holders(rng):
    emps = CTX["employee_ids"]
    certs = CTX["certification_ids"]
    rows = []
    for i in range(1, 3001):
        issued = date.today() - timedelta(days=rng.randint(30, 365 * 5))
        expires = issued + timedelta(days=365 * rng.randint(2, 5))
        rows.append((i, rng.choice(emps), rng.choice(certs), issued, expires))
    return rows


def t_benefit_enrollments(rng):
    emps = CTX["employee_ids"]
    plans = CTX["benefit_plan_ids"]
    rows = []
    next_id = 1
    for eid in rng.sample(emps, k=4500):
        for pid in rng.sample(plans, k=rng.randint(1, 4)):
            rows.append((
                next_id, eid, pid,
                date.today() - timedelta(days=rng.randint(30, 365 * 5)),
            ))
            next_id += 1
    return rows


def t_dependents(rng):
    emps = CTX["employee_ids"]
    rels = ["spouse", "child", "parent", "sibling", "domestic_partner"]
    rows = []
    for i, eid in enumerate(rng.sample(emps, k=2500), start=1):
        first = rng.choice(FIRST_NAMES).title()
        last = rng.choice(LAST_NAMES).title()
        rows.append((
            i, eid, f"{first} {last}",
            date.today() - timedelta(days=rng.randint(180, 365 * 80)),
            gen_ssn(rng) if rng.random() > 0.20 else None,
            rng.choice(rels),
        ))
    return rows


def t_emergency_contacts(rng):
    emps = CTX["employee_ids"]
    rels = ["spouse", "parent", "sibling", "friend", "neighbour"]
    rows = []
    next_id = 1
    for eid in emps:
        for _ in range(rng.choice([1, 1, 1, 2])):
            first = rng.choice(FIRST_NAMES).title()
            last = rng.choice(LAST_NAMES).title()
            rows.append((next_id, eid, f"{first} {last}",
                         gen_phone_e164(rng),
                         gen_email(rng, first, last) if rng.random() > 0.4 else None,
                         rng.choice(rels)))
            next_id += 1
    return rows


def t_onboarding_tasks(rng):
    tasks = ["Sign offer", "Background check", "Provision laptop",
             "Day-1 orientation", "Setup payroll", "Setup benefits",
             "Mandatory trainings", "Buddy assignment",
             "First-week check-in", "First-month review",
             "IT onboarding", "Security onboarding",
             "Tooling access", "Office tour",
             "30-60-90 plan"]
    rows = []
    for i, n in enumerate(tasks, start=1):
        rows.append((i, n, i, "all"))
    CTX["onb_task_ids"] = [r[0] for r in rows]
    return rows


def t_onboarding_progress(rng):
    emps = CTX["employee_ids"]
    tids = CTX["onb_task_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        for tid in rng.sample(tids, k=rng.randint(5, len(tids))):
            rows.append((
                next_id, eid, tid,
                rng.choice(["pending", "in_progress", "complete"]),
                datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 3))
                if rng.random() > 0.3 else None,
            ))
            next_id += 1
    return rows


def t_exit_interviews(rng):
    emps = rng.sample(CTX["employee_ids"], k=400)
    feelings = ["positive", "neutral", "negative"]
    rows = []
    for i, eid in enumerate(emps, start=1):
        rows.append((
            i, eid,
            datetime.now(timezone.utc) - timedelta(days=rng.randint(30, 365 * 3)),
            f"Departing employee felt {rng.choice(feelings)}. Final notes here.",
        ))
    return rows


def t_termination_records(rng):
    emps = rng.sample(CTX["employee_ids"], k=450)
    mgrs = CTX["manager_ids"]
    rows = []
    for i, eid in enumerate(emps, start=1):
        rows.append((
            i, eid, rng.choice(mgrs),
            date.today() - timedelta(days=rng.randint(0, 365 * 3)),
            rng.choice(["voluntary", "involuntary", "layoff",
                        "retirement", "end-of-contract"]),
        ))
    return rows


def t_documents(rng):
    emps = CTX["employee_ids"]
    dtypes = CTX["document_type_ids"]
    rows = []
    for i in range(1, 8001):
        body = (f"Document body for employee #{rng.choice(emps)}. "
                "Confidential — do not share externally.")
        # 5% have embedded PII
        if rng.random() < 0.05:
            body += f" Contact: {gen_email(rng, 'doc', 'team')}, {gen_phone_e164(rng)}."
        rows.append((
            i, rng.choice(emps), rng.choice(dtypes),
            f"Document #{i} title", body,
        ))
    CTX["document_ids"] = [r[0] for r in rows]
    return rows


def t_document_acks(rng):
    emps = CTX["employee_ids"]
    dids = CTX["document_ids"]
    rows = []
    for i in range(1, 25001):
        rows.append((
            i, rng.choice(dids), rng.choice(emps),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 4)),
        ))
    return rows


def t_visa_statuses(rng):
    emps = rng.sample(CTX["employee_ids"], k=400)
    types = ["H-1B", "L-1", "E-3", "TN", "Green Card", "F-1 OPT", "Blue Card", "ICT"]
    rows = []
    for i, eid in enumerate(emps, start=1):
        rows.append((
            i, eid, rng.choice(types),
            date.today() + timedelta(days=rng.randint(30, 365 * 5)),
        ))
    return rows


def t_incidents(rng):
    emps = CTX["employee_ids"]
    rows = []
    for i in range(1, 401):
        rows.append((
            i, rng.choice(emps), rng.choice(emps),
            rng.choice(["safety", "ethics", "harassment", "policy_violation",
                        "data_breach", "other"]),
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 3)),
            f"Incident description {i}.",
        ))
    CTX["incident_ids"] = [r[0] for r in rows]
    return rows


def t_disciplinary_actions(rng):
    iids = CTX["incident_ids"]
    emps = CTX["employee_ids"]
    rows = []
    for i, iid in enumerate(rng.sample(iids, k=250), start=1):
        rows.append((
            i, iid, rng.choice(emps),
            rng.choice(["verbal_warning", "written_warning", "PIP",
                        "suspension", "termination"]),
            date.today() - timedelta(days=rng.randint(0, 365 * 3)),
        ))
    return rows


def t_grievances(rng):
    emps = CTX["employee_ids"]
    hr = CTX["employee_ids"][:100]
    rows = []
    for i in range(1, 401):
        rows.append((
            i, rng.choice(emps), rng.choice(hr),
            f"Grievance description {i}.",
            rng.choice(["open", "investigating", "resolved", "escalated"]),
        ))
    return rows


def t_employee_skills(rng):
    emps = CTX["employee_ids"]
    skills = CTX["skill_ids"]
    rows = []
    next_id = 1
    for eid in emps:
        for sid in rng.sample(skills, k=rng.randint(2, 8)):
            rows.append((
                next_id, eid, sid,
                rng.randint(1, 5),
                rng.random() < 0.30,
            ))
            next_id += 1
    return rows


def t_hr_tickets(rng):
    emps = CTX["employee_ids"]
    hr = CTX["employee_ids"][:100]
    rows = []
    for i in range(1, 8001):
        rows.append((
            i, rng.choice(emps), rng.choice(hr),
            f"HR ticket #{i}: {rng.choice(['payroll question','benefits question','leave inquiry','onboarding'])}",
            rng.choice(["open", "in_progress", "resolved", "closed"]),
        ))
    CTX["hr_ticket_ids"] = [r[0] for r in rows]
    return rows


def t_hr_ticket_messages(rng):
    tids = CTX["hr_ticket_ids"]
    emps = CTX["employee_ids"]
    rows = []
    for i in range(1, 30001):
        body = (f"Reply for ticket #{rng.choice(tids)}.")
        if rng.random() < 0.05:
            body += f" Reach me at {gen_email(rng, 'a', 'b')} or {gen_phone_e164(rng)}."
        rows.append((
            i, rng.choice(tids), rng.choice(emps), body,
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 2)),
        ))
    return rows


def t_announcements(rng):
    emps = CTX["employee_ids"][:200]
    rows = []
    for i in range(1, 401):
        rows.append((
            i, rng.choice(emps),
            f"Announcement {i}: {rng.choice(['policy update','event','reminder','launch'])}",
            datetime.now(timezone.utc) - timedelta(days=rng.randint(0, 365 * 2)),
        ))
    return rows


# ----------------------------------------------------------- thematic table specs

THEMATIC: list[Table] = [
    Table("regions",
          'CREATE TABLE IF NOT EXISTS "regions" (id INTEGER, name TEXT)',
          t_regions, ["id", "name"]),
    Table("countries",
          '''CREATE TABLE IF NOT EXISTS "countries" (
              id INTEGER, code VARCHAR(2), name TEXT, region_id INTEGER)''',
          t_countries, ["id", "code", "name", "region_id"]),
    Table("currencies",
          '''CREATE TABLE IF NOT EXISTS "currencies" (
              id INTEGER, code VARCHAR(8), name TEXT)''',
          t_currencies, ["id", "code", "name"]),
    Table("languages",
          '''CREATE TABLE IF NOT EXISTS "languages" (
              id INTEGER, code VARCHAR(8), name TEXT)''',
          t_languages, ["id", "code", "name"]),
    Table("employment_types",
          '''CREATE TABLE IF NOT EXISTS "employment_types" (
              id INTEGER, name TEXT)''',
          t_employment_types, ["id", "name"]),
    Table("pay_grades",
          '''CREATE TABLE IF NOT EXISTS "pay_grades" (
              id INTEGER, level VARCHAR(8), min_salary NUMERIC(12,2),
              max_salary NUMERIC(12,2))''',
          t_pay_grades, ["id", "level", "min_salary", "max_salary"]),
    Table("cost_centers",
          '''CREATE TABLE IF NOT EXISTS "cost_centers" (
              id INTEGER, code VARCHAR(16), name TEXT,
              parent_cost_center_id INTEGER)''',
          t_cost_centers, ["id", "code", "name", "parent_cost_center_id"]),
    Table("locations",
          '''CREATE TABLE IF NOT EXISTS "locations" (
              id INTEGER, name TEXT, address TEXT, city TEXT,
              country_code VARCHAR(2), postal_code VARCHAR(16))''',
          t_locations, ["id", "name", "address", "city", "country_code", "postal_code"]),
    Table("departments",
          '''CREATE TABLE IF NOT EXISTS "departments" (
              id INTEGER, name TEXT, location_id INTEGER,
              parent_department_id INTEGER)''',
          t_departments, ["id", "name", "location_id", "parent_department_id"]),
    Table("jobs",
          '''CREATE TABLE IF NOT EXISTS "jobs" (
              id INTEGER, title TEXT, pay_grade_id INTEGER)''',
          t_jobs, ["id", "title", "pay_grade_id"]),
    Table("leave_types",
          '''CREATE TABLE IF NOT EXISTS "leave_types" (
              id INTEGER, name TEXT)''',
          t_leave_types, ["id", "name"]),
    Table("competencies",
          '''CREATE TABLE IF NOT EXISTS "competencies" (
              id INTEGER, name TEXT, category VARCHAR(32))''',
          t_competencies, ["id", "name", "category"]),
    Table("skill_taxonomy",
          '''CREATE TABLE IF NOT EXISTS "skill_taxonomy" (
              id INTEGER, name TEXT, parent_id INTEGER)''',
          t_skill_taxonomy, ["id", "name", "parent_id"]),
    Table("skills",
          '''CREATE TABLE IF NOT EXISTS "skills" (
              id INTEGER, name TEXT, taxonomy_id INTEGER)''',
          t_skills, ["id", "name", "taxonomy_id"]),
    Table("benefit_plans",
          '''CREATE TABLE IF NOT EXISTS "benefit_plans" (
              id INTEGER, name TEXT, provider TEXT, kind VARCHAR(32))''',
          t_benefit_plans, ["id", "name", "provider", "kind"]),
    Table("training_programs",
          '''CREATE TABLE IF NOT EXISTS "training_programs" (
              id INTEGER, name TEXT, duration_hours INTEGER, mode VARCHAR(32))''',
          t_training_programs, ["id", "name", "duration_hours", "mode"]),
    Table("certifications",
          '''CREATE TABLE IF NOT EXISTS "certifications" (
              id INTEGER, name TEXT, issuer TEXT)''',
          t_certifications, ["id", "name", "issuer"]),
    Table("shifts",
          '''CREATE TABLE IF NOT EXISTS "shifts" (
              id INTEGER, name TEXT, starts_at VARCHAR(8), ends_at VARCHAR(8))''',
          t_shifts, ["id", "name", "starts_at", "ends_at"]),
    Table("public_holidays",
          '''CREATE TABLE IF NOT EXISTS "public_holidays" (
              id BIGINT, country_code VARCHAR(2), holiday_date DATE,
              name TEXT)''',
          t_public_holidays, ["id", "country_code", "holiday_date", "name"]),
    Table("review_cycles",
          '''CREATE TABLE IF NOT EXISTS "review_cycles" (
              id INTEGER, name TEXT, start_date DATE, end_date DATE)''',
          t_review_cycles, ["id", "name", "start_date", "end_date"]),
    Table("pay_components",
          '''CREATE TABLE IF NOT EXISTS "pay_components" (
              id INTEGER, name TEXT, kind VARCHAR(32))''',
          t_pay_components, ["id", "name", "kind"]),
    Table("document_types",
          '''CREATE TABLE IF NOT EXISTS "document_types" (
              id INTEGER, name TEXT)''',
          t_document_types, ["id", "name"]),
    Table("employees",
          '''CREATE TABLE IF NOT EXISTS "employees" (
              id INTEGER, first_name TEXT, last_name TEXT,
              employee_code VARCHAR(20), ssn VARCHAR(11), dob DATE,
              work_email VARCHAR(128), personal_email VARCHAR(128),
              work_phone VARCHAR(32), personal_phone VARCHAR(32),
              hire_date DATE, department_id INTEGER, location_id INTEGER,
              manager_id INTEGER, job_id INTEGER, pay_grade_id INTEGER,
              cost_center_id INTEGER, employment_type_id INTEGER,
              status VARCHAR(16))''',
          t_employees,
          ["id", "first_name", "last_name", "employee_code", "ssn", "dob",
           "work_email", "personal_email", "work_phone", "personal_phone",
           "hire_date", "department_id", "location_id", "manager_id",
           "job_id", "pay_grade_id", "cost_center_id",
           "employment_type_id", "status"]),
    Table("candidates",
          '''CREATE TABLE IF NOT EXISTS "candidates" (
              id INTEGER, first_name TEXT, last_name TEXT, email VARCHAR(128),
              phone VARCHAR(32), dob DATE, resume_text TEXT,
              referrer_id INTEGER, status VARCHAR(32))''',
          t_candidates,
          ["id", "first_name", "last_name", "email", "phone", "dob",
           "resume_text", "referrer_id", "status"]),
    Table("job_postings",
          '''CREATE TABLE IF NOT EXISTS "job_postings" (
              id INTEGER, job_id INTEGER, posted_by INTEGER,
              opened_at TIMESTAMPTZ, status VARCHAR(16))''',
          t_job_postings, ["id", "job_id", "posted_by", "opened_at", "status"]),
    Table("applications",
          '''CREATE TABLE IF NOT EXISTS "applications" (
              id INTEGER, candidate_id INTEGER, posting_id INTEGER,
              status VARCHAR(32), applied_at TIMESTAMPTZ)''',
          t_applications,
          ["id", "candidate_id", "posting_id", "status", "applied_at"]),
    Table("interviews",
          '''CREATE TABLE IF NOT EXISTS "interviews" (
              id INTEGER, application_id INTEGER, interviewer_id INTEGER,
              scheduled_at TIMESTAMPTZ, kind VARCHAR(32))''',
          t_interviews,
          ["id", "application_id", "interviewer_id", "scheduled_at", "kind"]),
    Table("interview_feedback",
          '''CREATE TABLE IF NOT EXISTS "interview_feedback" (
              id INTEGER, interview_id INTEGER, score INTEGER,
              notes TEXT, recommendation VARCHAR(32))''',
          t_interview_feedback,
          ["id", "interview_id", "score", "notes", "recommendation"]),
    Table("offers",
          '''CREATE TABLE IF NOT EXISTS "offers" (
              id INTEGER, application_id INTEGER, signed_by_candidate_id INTEGER,
              amount NUMERIC(12,2), currency VARCHAR(8), status VARCHAR(16))''',
          t_offers,
          ["id", "application_id", "signed_by_candidate_id",
           "amount", "currency", "status"]),
    Table("background_checks",
          '''CREATE TABLE IF NOT EXISTS "background_checks" (
              id INTEGER, candidate_id INTEGER, status VARCHAR(16),
              completed_at TIMESTAMPTZ)''',
          t_background_checks,
          ["id", "candidate_id", "status", "completed_at"]),
    Table("job_history",
          '''CREATE TABLE IF NOT EXISTS "job_history" (
              id BIGINT, employee_id INTEGER, job_id INTEGER,
              department_id INTEGER, started_at DATE, ended_at DATE)''',
          t_job_history,
          ["id", "employee_id", "job_id", "department_id",
           "started_at", "ended_at"]),
    Table("payroll_runs",
          '''CREATE TABLE IF NOT EXISTS "payroll_runs" (
              id INTEGER, run_date DATE, status VARCHAR(16))''',
          t_payroll_runs, ["id", "run_date", "status"]),
    Table("payroll_entries",
          '''CREATE TABLE IF NOT EXISTS "payroll_entries" (
              id BIGINT, employee_id INTEGER, payroll_run_id INTEGER,
              gross_amount NUMERIC(12,2), net_amount NUMERIC(12,2),
              bank_account_last4 VARCHAR(8), iban VARCHAR(34),
              routing_number VARCHAR(16), currency VARCHAR(8))''',
          t_payroll_entries,
          ["id", "employee_id", "payroll_run_id", "gross_amount", "net_amount",
           "bank_account_last4", "iban", "routing_number", "currency"]),
    Table("salaries",
          '''CREATE TABLE IF NOT EXISTS "salaries" (
              id BIGINT, employee_id INTEGER, pay_component_id INTEGER,
              amount NUMERIC(12,2), currency VARCHAR(8),
              effective_from DATE)''',
          t_salaries,
          ["id", "employee_id", "pay_component_id", "amount",
           "currency", "effective_from"]),
    Table("compensation_changes",
          '''CREATE TABLE IF NOT EXISTS "compensation_changes" (
              id INTEGER, employee_id INTEGER, previous_amount NUMERIC(12,2),
              new_amount NUMERIC(12,2), approved_by INTEGER,
              effective_date DATE)''',
          t_compensation_changes,
          ["id", "employee_id", "previous_amount", "new_amount",
           "approved_by", "effective_date"]),
    Table("bonuses",
          '''CREATE TABLE IF NOT EXISTS "bonuses" (
              id INTEGER, employee_id INTEGER, amount NUMERIC(12,2),
              granted_at TIMESTAMPTZ, reason VARCHAR(32))''',
          t_bonuses, ["id", "employee_id", "amount", "granted_at", "reason"]),
    Table("timesheets",
          '''CREATE TABLE IF NOT EXISTS "timesheets" (
              id BIGINT, employee_id INTEGER, period_start DATE,
              period_end DATE, status VARCHAR(16))''',
          t_timesheets,
          ["id", "employee_id", "period_start", "period_end", "status"]),
    Table("time_entries",
          '''CREATE TABLE IF NOT EXISTS "time_entries" (
              id BIGINT, timesheet_id BIGINT, employee_id INTEGER,
              clock_in TIMESTAMPTZ, clock_out TIMESTAMPTZ)''',
          t_time_entries,
          ["id", "timesheet_id", "employee_id", "clock_in", "clock_out"]),
    Table("leave_requests",
          '''CREATE TABLE IF NOT EXISTS "leave_requests" (
              id INTEGER, employee_id INTEGER, leave_type_id INTEGER,
              start_date DATE, end_date DATE, approved_by INTEGER,
              status VARCHAR(16))''',
          t_leave_requests,
          ["id", "employee_id", "leave_type_id", "start_date",
           "end_date", "approved_by", "status"]),
    Table("leave_balances",
          '''CREATE TABLE IF NOT EXISTS "leave_balances" (
              id BIGINT, employee_id INTEGER, leave_type_id INTEGER,
              balance_days NUMERIC(6,1))''',
          t_leave_balances,
          ["id", "employee_id", "leave_type_id", "balance_days"]),
    Table("shift_assignments",
          '''CREATE TABLE IF NOT EXISTS "shift_assignments" (
              id BIGINT, shift_id INTEGER, employee_id INTEGER,
              work_date DATE)''',
          t_shift_assignments,
          ["id", "shift_id", "employee_id", "work_date"]),
    Table("performance_reviews",
          '''CREATE TABLE IF NOT EXISTS "performance_reviews" (
              id BIGINT, employee_id INTEGER, reviewer_id INTEGER,
              cycle_id INTEGER, rating VARCHAR(32), summary TEXT)''',
          t_performance_reviews,
          ["id", "employee_id", "reviewer_id", "cycle_id", "rating", "summary"]),
    Table("goals",
          '''CREATE TABLE IF NOT EXISTS "goals" (
              id BIGINT, employee_id INTEGER, review_id BIGINT,
              title TEXT, status VARCHAR(16))''',
          t_goals,
          ["id", "employee_id", "review_id", "title", "status"]),
    Table("competency_assessments",
          '''CREATE TABLE IF NOT EXISTS "competency_assessments" (
              id BIGINT, employee_id INTEGER, competency_id INTEGER,
              level INTEGER, assessed_at DATE)''',
          t_competency_assessments,
          ["id", "employee_id", "competency_id", "level", "assessed_at"]),
    Table("promotion_history",
          '''CREATE TABLE IF NOT EXISTS "promotion_history" (
              id INTEGER, employee_id INTEGER, from_job_id INTEGER,
              to_job_id INTEGER, effective_date DATE)''',
          t_promotion_history,
          ["id", "employee_id", "from_job_id", "to_job_id", "effective_date"]),
    Table("training_enrollments",
          '''CREATE TABLE IF NOT EXISTS "training_enrollments" (
              id INTEGER, employee_id INTEGER, program_id INTEGER,
              enrolled_at TIMESTAMPTZ, completed_at TIMESTAMPTZ)''',
          t_training_enrollments,
          ["id", "employee_id", "program_id", "enrolled_at", "completed_at"]),
    Table("certification_holders",
          '''CREATE TABLE IF NOT EXISTS "certification_holders" (
              id INTEGER, employee_id INTEGER, certification_id INTEGER,
              issued_at DATE, expires_at DATE)''',
          t_certification_holders,
          ["id", "employee_id", "certification_id", "issued_at", "expires_at"]),
    Table("benefit_enrollments",
          '''CREATE TABLE IF NOT EXISTS "benefit_enrollments" (
              id BIGINT, employee_id INTEGER, plan_id INTEGER,
              enrolled_at DATE)''',
          t_benefit_enrollments,
          ["id", "employee_id", "plan_id", "enrolled_at"]),
    Table("dependents",
          '''CREATE TABLE IF NOT EXISTS "dependents" (
              id INTEGER, employee_id INTEGER, full_name TEXT,
              dob DATE, ssn VARCHAR(11), relationship VARCHAR(32))''',
          t_dependents,
          ["id", "employee_id", "full_name", "dob", "ssn", "relationship"]),
    Table("emergency_contacts",
          '''CREATE TABLE IF NOT EXISTS "emergency_contacts" (
              id BIGINT, employee_id INTEGER, full_name TEXT,
              phone VARCHAR(32), email VARCHAR(128), relationship VARCHAR(32))''',
          t_emergency_contacts,
          ["id", "employee_id", "full_name", "phone", "email", "relationship"]),
    Table("onboarding_tasks",
          '''CREATE TABLE IF NOT EXISTS "onboarding_tasks" (
              id INTEGER, name TEXT, sequence INTEGER, audience TEXT)''',
          t_onboarding_tasks, ["id", "name", "sequence", "audience"]),
    Table("onboarding_progress",
          '''CREATE TABLE IF NOT EXISTS "onboarding_progress" (
              id BIGINT, employee_id INTEGER, task_id INTEGER,
              status VARCHAR(16), completed_at TIMESTAMPTZ)''',
          t_onboarding_progress,
          ["id", "employee_id", "task_id", "status", "completed_at"]),
    Table("exit_interviews",
          '''CREATE TABLE IF NOT EXISTS "exit_interviews" (
              id INTEGER, employee_id INTEGER, conducted_at TIMESTAMPTZ,
              feedback_text TEXT)''',
          t_exit_interviews,
          ["id", "employee_id", "conducted_at", "feedback_text"]),
    Table("termination_records",
          '''CREATE TABLE IF NOT EXISTS "termination_records" (
              id INTEGER, employee_id INTEGER, approved_by INTEGER,
              termination_date DATE, reason VARCHAR(64))''',
          t_termination_records,
          ["id", "employee_id", "approved_by", "termination_date", "reason"]),
    Table("documents",
          '''CREATE TABLE IF NOT EXISTS "documents" (
              id INTEGER, owner_employee_id INTEGER,
              document_type_id INTEGER, title TEXT, body TEXT)''',
          t_documents,
          ["id", "owner_employee_id", "document_type_id", "title", "body"]),
    Table("document_acknowledgements",
          '''CREATE TABLE IF NOT EXISTS "document_acknowledgements" (
              id BIGINT, document_id INTEGER, employee_id INTEGER,
              acknowledged_at TIMESTAMPTZ)''',
          t_document_acks,
          ["id", "document_id", "employee_id", "acknowledged_at"]),
    Table("visa_statuses",
          '''CREATE TABLE IF NOT EXISTS "visa_statuses" (
              id INTEGER, employee_id INTEGER, visa_type VARCHAR(32),
              expires_on DATE)''',
          t_visa_statuses,
          ["id", "employee_id", "visa_type", "expires_on"]),
    Table("incidents",
          '''CREATE TABLE IF NOT EXISTS "incidents" (
              id INTEGER, employee_id INTEGER, reported_by INTEGER,
              kind VARCHAR(32), occurred_at TIMESTAMPTZ, description TEXT)''',
          t_incidents,
          ["id", "employee_id", "reported_by", "kind",
           "occurred_at", "description"]),
    Table("disciplinary_actions",
          '''CREATE TABLE IF NOT EXISTS "disciplinary_actions" (
              id INTEGER, incident_id INTEGER, employee_id INTEGER,
              action VARCHAR(32), taken_on DATE)''',
          t_disciplinary_actions,
          ["id", "incident_id", "employee_id", "action", "taken_on"]),
    Table("grievances",
          '''CREATE TABLE IF NOT EXISTS "grievances" (
              id INTEGER, employee_id INTEGER, assigned_hr_id INTEGER,
              description TEXT, status VARCHAR(16))''',
          t_grievances,
          ["id", "employee_id", "assigned_hr_id", "description", "status"]),
    Table("employee_skills",
          '''CREATE TABLE IF NOT EXISTS "employee_skills" (
              id BIGINT, employee_id INTEGER, skill_id INTEGER,
              level INTEGER, certified BOOLEAN)''',
          t_employee_skills,
          ["id", "employee_id", "skill_id", "level", "certified"]),
    Table("hr_tickets",
          '''CREATE TABLE IF NOT EXISTS "hr_tickets" (
              id INTEGER, employee_id INTEGER, assigned_hr_id INTEGER,
              subject TEXT, status VARCHAR(16))''',
          t_hr_tickets,
          ["id", "employee_id", "assigned_hr_id", "subject", "status"]),
    Table("hr_ticket_messages",
          '''CREATE TABLE IF NOT EXISTS "hr_ticket_messages" (
              id INTEGER, ticket_id INTEGER, author_employee_id INTEGER,
              body TEXT, posted_at TIMESTAMPTZ)''',
          t_hr_ticket_messages,
          ["id", "ticket_id", "author_employee_id", "body", "posted_at"]),
    Table("announcements",
          '''CREATE TABLE IF NOT EXISTS "announcements" (
              id INTEGER, posted_by INTEGER, body TEXT,
              posted_at TIMESTAMPTZ)''',
          t_announcements, ["id", "posted_by", "body", "posted_at"]),
]


# ----------------------------------------------------------- noise / filler

def build_all_hr_tables() -> list[Table]:
    tables = list(THEMATIC)

    # Filler reference dims (small lookups)
    refs = [
        "skill_levels", "performance_ratings", "leave_statuses",
        "application_statuses", "job_status_codes", "interview_kinds",
        "review_statuses", "promotion_reasons", "termination_reasons",
        "document_categories", "compliance_tags", "policy_types",
        "incident_kinds", "grievance_kinds", "training_modes",
        "certification_levels", "shift_codes", "calendar_codes",
        "country_groups", "tax_jurisdictions", "fiscal_periods",
        "labor_categories", "salary_bands", "vesting_schedules",
        "stock_grant_types", "expense_categories", "travel_classes",
        "approval_levels", "currency_codes", "language_levels",
        "skill_certs", "training_vendors", "benefit_kinds",
        "leave_codes", "shift_swap_codes", "ticket_priorities",
        "ticket_categories", "doc_classifications", "policy_acks",
        "audit_categories", "incident_severities", "alert_levels",
        "data_classifications", "regulatory_frameworks", "iso_standards",
        "kpi_definitions", "report_kinds", "channel_codes",
        "platform_codes", "device_codes",
    ]
    for r in refs:
        tables.append(_gen_simple_dim(
            r, random.Random(SEED + hash(r)).randint(15, 150)))

    # 60 dim_*
    for i in range(1, 61):
        tables.append(_gen_simple_dim(
            f"dim_employee_{i:03d}",
            random.Random(SEED + 100 + i).randint(500, 5000)))

    # 60 fact_*
    for i in range(1, 61):
        name = f"fact_payroll_{i:03d}"
        ddl = f'''CREATE TABLE IF NOT EXISTS "{name}" (
            id BIGINT, dim_id INTEGER, period DATE, value NUMERIC(18,4))'''

        def mk(ii):
            def populate(rng):
                size = random.Random(SEED + 200 + ii).randint(500, 3000)
                return [(j, rng.randint(1, 1000),
                         date.today() - timedelta(days=rng.randint(0, 365)),
                         round(rng.uniform(0, 200000), 4))
                        for j in range(1, size + 1)]
            return populate
        tables.append(Table(name, ddl, mk(i),
                            ["id", "dim_id", "period", "value"]))

    # 60 kpi_*
    kpi_names = ["headcount", "attrition", "engagement", "retention",
                 "absenteeism", "training_hours", "diversity"]
    for i in range(1, 61):
        kind = kpi_names[(i - 1) % len(kpi_names)]
        tables.append(_gen_kpi_table(
            f"kpi_{kind}_{i:03d}",
            random.Random(SEED + 300 + i).randint(500, 3000)))

    # 70 *_log tables (excluded by *_log)
    log_prefixes = ["system", "hr_audit", "payroll_audit", "login",
                    "hire", "access", "error", "api", "security", "request"]
    for prefix in log_prefixes:
        for i in range(1, 8):
            tables.append(_gen_log_table(
                f"{prefix}_log_{i:02d}",
                random.Random(SEED + hash(prefix) + i).randint(2000, 15000),
                reason="log_pattern"))

    # 30 *_events
    event_prefixes = ["employee", "recruiting", "performance", "payroll",
                      "training", "compliance"]
    for prefix in event_prefixes:
        for i in range(1, 6):
            tables.append(_gen_event_table(
                f"{prefix}_events_{i:02d}", reason="events_pattern"))

    # 20 *_bak (excluded)
    for i in range(1, 11):
        tables.append(_gen_archive_table(
            f"employees_bak_{i:03d}", reason="backup_pattern"))
    for i in range(1, 11):
        tables.append(_gen_archive_table(
            f"payroll_bak_{i:03d}", reason="backup_pattern"))

    # 15 *_archive (excluded)
    for i in range(1, 8):
        tables.append(_gen_archive_table(
            f"employees_archive_{2020 + i}", reason="archive_pattern"))
    for i in range(1, 9):
        tables.append(_gen_archive_table(
            f"terminated_archive_{2020 + i}", reason="archive_pattern"))

    # 15 temp_*
    for i in range(1, 16):
        tables.append(_gen_tmp_table(
            f"temp_payroll_{i:03d}", reason="temp_pattern"))
    # 15 tmp_*
    for i in range(1, 16):
        tables.append(_gen_tmp_table(
            f"tmp_recruiting_{i:03d}", reason="tmp_pattern"))

    # 20 etl_*
    for i in range(1, 21):
        tables.append(_gen_etl_table(
            f"etl_pipeline_{i:03d}", reason="etl_pattern"))

    # 5 migrations
    for i in range(1, 6):
        tables.append(Table(
            f"migrations_v{i}",
            f'''CREATE TABLE IF NOT EXISTS "migrations_v{i}" (
                id INTEGER, name TEXT, applied_at TIMESTAMPTZ,
                checksum VARCHAR(64))''',
            (lambda rng, ii=i: [(j, f"migration_{ii}_{j}",
                                  datetime.now(timezone.utc)
                                  - timedelta(days=rng.randint(0, 1500)),
                                  secrets.token_hex(32))
                                 for j in range(1, 51)]),
            ["id", "name", "applied_at", "checksum"],
            excluded_reason="migrations_pattern"))

    # Junction (M:N) tables — referencing CTX ids so undeclared FKs exist
    junction_specs = [
        ("employee_skill_endorsements", "employee_ids", "skill_ids", 12000),
        ("employee_certifications_index", "employee_ids", "certification_ids", 6000),
        ("training_attendance", "employee_ids", "training_program_ids", 12000),
        ("project_assignments", "employee_ids", None, 15000),
        ("skill_demand", "skill_ids", "department_ids", 4000),
        ("competency_required", "competency_ids", "job_ids", 4000),
        ("employee_languages", "employee_ids", None, 8000),
        ("employee_locations_history", "employee_ids", "location_ids", 8000),
        ("manager_circle", "employee_ids", None, 5000),
        ("policy_acknowledgements", "employee_ids", "document_ids", 20000),
        ("review_calibrations", "review_ids", "employee_ids", 5000),
        ("training_certificates", "training_program_ids",
         "certification_ids", 3000),
    ]
    for name, lk, rk, n in junction_specs:
        left = CTX.get(lk, list(range(1, 1000)))
        right = CTX.get(rk, list(range(1, 1000))) if rk else None
        tables.append(_gen_junction_table(name, left, right, n))
    # filler junctions
    for i in range(1, 25):
        tables.append(_gen_junction_table(
            f"link_table_{i:03d}", None, None,
            random.Random(SEED + 700 + i).randint(2000, 10000)))

    # 10 wide tables (denormalised reports)
    for i in range(1, 11):
        tables.append(_gen_wide_table(
            f"wide_employee_report_{i:03d}",
            n_cols=random.Random(SEED + 800 + i).randint(40, 80),
            n_rows=random.Random(SEED + 800 + i).randint(100, 1500)))

    # 5 empty edge cases
    for i in range(1, 6):
        tables.append(_gen_empty_table(f"placeholder_{i:03d}"))

    return tables


# ----------------------------------------------------------- driver

def already_populated(cur, name: str) -> bool:
    cur.execute(f'SELECT EXISTS(SELECT 1 FROM "{name}" LIMIT 1)')
    return cur.fetchone()[0]


def run(reset: bool = False, schema: str = "hr") -> None:
    t0 = time.time()
    tables = build_all_hr_tables()
    print(f"Planned tables: {len(tables)}  (schema: {schema})")

    if reset:
        print(f"--reset: dropping all planned tables in {schema!r}")
        with conn_cur(schema) as (conn, cur):
            for t in tables:
                cur.execute(f'DROP TABLE IF EXISTS "{t.name}" CASCADE')
            conn.commit()

    print(f"Phase 1/2: creating tables in schema {schema!r}...")
    with conn_cur(schema) as (conn, cur):
        for i, t in enumerate(tables, 1):
            cur.execute(t.ddl)
            if i % 50 == 0:
                conn.commit()
                print(f"  ddl progress: {i}/{len(tables)}", flush=True)
        conn.commit()
    print(f"  DDL done in {time.time()-t0:.1f}s")

    print(f"Phase 2/2: populating tables in schema {schema!r}...")
    total_rows = 0
    for idx, t in enumerate(tables, 1):
        if t.populate is None:
            continue
        with conn_cur(schema) as (conn, cur):
            if already_populated(cur, t.name):
                continue
            rng = random.Random(SEED ^ hash(t.name))
            try:
                rows = t.populate(rng)
            except Exception as e:
                print(f"  generator error in {t.name}: {e!r}")
                continue
            if not rows:
                continue
            n = copy_rows(cur, t.name, t.column_names, rows)
            conn.commit()
            total_rows += n
            if idx % 25 == 0 or idx == len(tables):
                print(f"  [{idx:>3}/{len(tables)}] {t.name:<32s} +{n:>8} rows  total={total_rows:,}",
                      flush=True)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    with conn_cur(schema) as (conn, cur):
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema=%s",
                    (schema,))
        actual = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM information_schema.table_constraints "
                    "WHERE table_schema=%s AND constraint_type='FOREIGN KEY'",
                    (schema,))
        n_fks = cur.fetchone()[0]
    print(f"Tables in {schema}: {actual}")
    print(f"Total rows inserted (approx): {total_rows:,}")
    print(f"Foreign-key constraints in {schema}: {n_fks}  (must be 0)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="DROP all planned tables first (destructive)")
    ap.add_argument("--schema", default="hr",
                    help="Target schema (default: hr). Created if absent.")
    args = ap.parse_args()
    run(reset=args.reset, schema=args.schema)
