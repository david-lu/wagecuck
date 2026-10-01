"""Reproducible synthetic applicant and a real, text-extractable PDF."""

import json
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

from .models import Profile

INK = HexColor("#172033")
ACCENT = HexColor("#2457A7")
MUTED = HexColor("#596579")
RULE = HexColor("#D8DFEA")
BANNER = HexColor("#EEF4FF")


def _wrapped_lines(text: str, font: str, size: float, width: float) -> list[str]:
    """Wrap text to a measured width while keeping the PDF text selectable."""
    lines: list[str] = []
    for paragraph in text.splitlines():
        words = paragraph.split()
        if not words:
            continue
        current = words.pop(0)
        for word in words:
            candidate = f"{current} {word}"
            if stringWidth(candidate, font, size) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_wrapped(
    canvas: Canvas,
    text: str,
    *,
    x: float,
    y: float,
    width: float,
    font: str = "Helvetica",
    size: float = 9.5,
    leading: float = 13,
) -> float:
    canvas.setFont(font, size)
    canvas.setFillColor(INK)
    for line in _wrapped_lines(text, font, size, width):
        canvas.drawString(x, y, line)
        y -= leading
    return y


def _draw_bullet(canvas: Canvas, text: str, *, x: float, y: float, width: float) -> float:
    canvas.setFillColor(ACCENT)
    canvas.circle(x + 2, y + 3, 1.6, stroke=0, fill=1)
    return _draw_wrapped(canvas, text, x=x + 12, y=y, width=width - 12)


def _draw_section(canvas: Canvas, title: str, *, y: float) -> float:
    canvas.setFillColor(ACCENT)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(52, y, title.upper())
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.8)
    canvas.line(52, y - 5, 560, y - 5)
    return y - 20


def generate_resume(applicant: Profile, target: Path) -> Path:
    """Write a clean, single-column synthetic resume suitable for ATS testing."""
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas = Canvas(str(target), pagesize=letter, invariant=True)
    canvas.setTitle(f"{applicant.get_full_name()} - Synthetic Test Resume")
    canvas.setAuthor("wagecuck test fixture")

    facts = applicant.facts
    job = applicant.employment[0]
    school = applicant.education[0]
    left = 52
    content_width = 508

    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 25)
    canvas.drawString(left, 744, applicant.get_full_name().upper())
    canvas.setFillColor(ACCENT)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(left, 720, applicant.application.headline or job.title)

    location = job.location or applicant.application.preferred_location or ""
    contact = "  |  ".join(
        str(value) for value in (location, facts.get("email"), facts.get("phone")) if value
    )
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 9)
    canvas.drawString(left, 701, contact)
    links = "  |  ".join(
        str(value)
        for value in (facts.get("website"), facts.get("linkedin"), facts.get("github"))
        if value
    )
    canvas.drawString(left, 687, links)

    canvas.setFillColor(BANNER)
    canvas.roundRect(left, 654, content_width, 21, 4, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.setFont("Helvetica-Bold", 8)
    canvas.drawCentredString(306, 661, "SYNTHETIC TEST PROFILE  |  NOT A REAL APPLICANT")

    y = _draw_section(canvas, "Professional summary", y=636)
    summary = applicant.application.personal_summary or str(facts.get("project_summary", ""))
    y = _draw_wrapped(canvas, summary, x=left, y=y, width=content_width)

    y = _draw_section(canvas, "Technical skills", y=y - 5)
    y = _draw_wrapped(
        canvas,
        str(facts.get("skills", "")),
        x=left,
        y=y,
        width=content_width,
        font="Helvetica-Bold",
    )

    y = _draw_section(canvas, "Experience", y=y - 6)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(left, y, job.title)
    end_date = "Present" if job.current else job.end_date.strftime("%b %Y") if job.end_date else ""
    date_range = f"{job.start_date:%b %Y} - {end_date}".rstrip(" -")
    canvas.setFont("Helvetica-Bold", 9)
    canvas.drawRightString(560, y, date_range)
    y -= 15
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Oblique", 9)
    canvas.drawString(left, y, f"{job.company}  |  {job.location}")
    y -= 17
    for achievement in job.summary.splitlines():
        y = _draw_bullet(canvas, achievement, x=left, y=y, width=content_width)
        y -= 3

    y = _draw_section(canvas, "Selected project", y=y - 4)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 10)
    canvas.drawString(left, y, "Browser Workflow Lab")
    y -= 15
    y = _draw_bullet(
        canvas,
        str(facts.get("project_summary", "")),
        x=left,
        y=y,
        width=content_width,
    )

    y = _draw_section(canvas, "Education", y=y - 7)
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 10.5)
    canvas.drawString(left, y, f"{school.degree}, {school.field}")
    if school.end_date:
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(560, y, str(school.end_date.year))
    y -= 15
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Oblique", 9)
    canvas.drawString(left, y, f"{school.school}  |  {school.location}")

    canvas.setStrokeColor(RULE)
    canvas.line(left, 49, 560, 49)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 7.5)
    canvas.drawCentredString(
        306,
        35,
        "All credentials, employers, accomplishments, and contact details on this document are synthetic test data.",
    )
    canvas.save()
    return target


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
            "pronouns": "they/them",
            "headline": "Software engineer focused on Python services and browser automation",
            "personal_summary": "Fictional software engineer with experience building Python services, TypeScript interfaces, and browser testing workflows.",
            "available_start_date": "2026-10-01",
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
            "demographics": {
                "gender_identity": "non_binary",
                "sexual_orientation": "bisexual",
                "transgender_status": False,
                "race_ethnicity": ["white"],
                "hispanic_latino": False,
            },
        },
    }
    applicant = Profile.model_validate(profile)
    target = directory / "profile.json"
    target.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    generate_resume(applicant, directory / profile["resume"])
    return target
