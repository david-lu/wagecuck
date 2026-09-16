"""Reproducible synthetic applicant and a real, text-extractable PDF."""

import json
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from .models import Profile


def generate_profile(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    facts = {
        "first_name": "Alex",
        "last_name": "Morgan",
        "email": "alex.morgan@example.com",
        "phone": "+14165550142",
        "website": "https://example.com/alex",
        "linkedin": "https://example.com/alex/linkedin",
        "github": "https://example.com/alex/github",
        "twitter": "https://example.com/alex/twitter",
        "source": "Company careers page",
        "skills": "Python, TypeScript, React, PostgreSQL, Playwright, Docker",
        "project_summary": "Built a fictional test platform for browser workflows using Python and Playwright.",
    }
    profile = {
        "id": "synthetic-alex-morgan-v1",
        "synthetic": True,
        "facts": facts,
        "address": {
            "line1": "100 Example Street",
            "line2": "",
            "city": "Toronto",
            "state": "Ontario",
            "postal_code": "M5V 2T6",
            "country": "Canada",
        },
        "employment": [
            {
                "company": "Example Systems (fictional)",
                "title": "Software Engineer",
                "start_date": "2023-06-01",
                "end_date": None,
                "current": True,
                "location": "Toronto, Ontario, Canada",
                "summary": "Built Python services and automated browser regression workflows.\nMaintained TypeScript interfaces and PostgreSQL data models.",
            }
        ],
        "education": [
            {
                "school": "Example University (fictional)",
                "degree": "Bachelor of Science",
                "field": "Computer Science",
                "start_date": "2019-09-01",
                "end_date": "2023-05-31",
                "current": False,
                "location": "Toronto, Ontario, Canada",
                "summary": "Completed a fictional undergraduate program in computer science.",
                "result": None,
                "expected_graduation_date": None,
            }
        ],
        "application": {
            "randomize_source": True,
            "preferred_name": "Alex",
            "pronouns": None,
            "headline": "Software engineer focused on Python services and browser automation",
            "personal_summary": "Fictional software engineer with experience building Python services, TypeScript interfaces, and browser testing workflows.",
            "available_start_date": None,
            "notice_period_days": 14,
            "availability_summary": None,
            "preferred_location": "Toronto",
            "current_work_country": "Canada",
            "role_interest": "My fictional background focuses on reliable Python services and browser automation.",
            "ai_usage": "For this fictional profile, I use AI to brainstorm test cases and review code, then verify suggestions with tests.",
            "cover_letter_text": None,
            "compensation": {
                "currency": "CAD",
                "annual_target": 100000,
                "expectations": None,
            },
        },
        "experience": {
            "python": {
                "years": 3,
                "has_experience": True,
                "summary": "Built fictional Python services and automated browser tests.",
            },
            "golang": {"years": 0, "has_experience": False, "summary": None},
            "kubernetes": {"years": None, "has_experience": None, "summary": None},
            "terraform": {"years": None, "has_experience": None, "summary": None},
            "microservices_rest": {"years": None, "has_experience": None, "summary": None},
            "genai": {"years": None, "has_experience": None, "summary": None},
            "aws": {"years": None, "has_experience": None, "summary": None},
            "git_platform_apis": {"years": None, "has_experience": None, "summary": None},
        },
        "consents": {"application_processing": True, "future_opportunities": False, "sms": False},
        "resume": "alex-morgan-resume.pdf",
        "cover_letter": None,
        "screening": {
            "default_work_country": "US",
            "work_authorization": {
                "US": {"authorized": True, "visa_type": "TN", "requires_sponsorship": None},
                "CA": {
                    "authorized": True,
                    "visa_type": None,
                    "requires_sponsorship": None,
                    "description": None,
                },
            },
            "veteran_status": "not_a_veteran",
            "disability_status": "no_disability",
            "disability_history": False,
        },
    }
    applicant = Profile.model_validate(profile)
    job = applicant.employment[0]
    school = applicant.education[0]
    target = directory / "profile.json"
    target.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    canvas = Canvas(str(directory / profile["resume"]), pagesize=letter, invariant=True)
    canvas.setTitle("Alex Morgan - Synthetic Test Resume")
    canvas.setAuthor("wagecuck test fixture")
    lines = [
        (18, applicant.get_full_name()),
        (11, "SYNTHETIC TEST PROFILE - NOT A REAL APPLICANT"),
        (10, "Toronto, Ontario | alex.morgan@example.com | +1 416 555 0142"),
        (10, "https://example.com/alex"),
        (14, "Summary"),
        (11, "Fictional software engineer specializing in reliable web applications and testing."),
        (14, "Skills"),
        (11, facts["skills"]),
        (14, "Experience"),
        (11, f"{job.company} | {job.title} | {job.start_date:%B %Y} - Present"),
        *((11, line) for line in job.summary.splitlines()),
        (14, "Education"),
        (11, f"{school.school} | BSc {school.field} | {school.end_date.year}"),
        (14, "Projects"),
        (11, "Browser Workflow Lab: a fictional Playwright form-testing platform."),
        (10, "All credentials, employers, accomplishments and contact details are test data."),
    ]
    y = 745
    for size, line in lines:
        canvas.setFont("Helvetica-Bold" if size >= 14 else "Helvetica", size)
        canvas.drawString(48, y, line)
        y -= size + 14
    canvas.save()
    return target
