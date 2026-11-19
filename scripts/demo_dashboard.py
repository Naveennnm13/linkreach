"""Preview the dashboard with fake sample data. Never touches your real database.

    python scripts/demo_dashboard.py
    then open http://127.0.0.1:8765/#token=demo-token-not-secret
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

demo_home = Path(tempfile.mkdtemp(prefix="linkreach-demo-"))
os.environ["LINKREACH_HOME"] = str(demo_home)
os.environ["LINKREACH_INPUTS"] = str(demo_home)
for src, dst in (("config.example.yaml", "config.yaml"), ("companies.example.csv", "companies.csv"), ("jobs.example.csv", "jobs.csv")):
    shutil.copyfile(ROOT / "linkreach" / "examples" / src, demo_home / dst)

import uvicorn  # noqa: E402

from linkreach import clock  # noqa: E402
from linkreach.config import load_inputs  # noqa: E402
from linkreach.dashboard.server import HOST, PORT, create_app  # noqa: E402
from linkreach.db import DB  # noqa: E402

TOKEN = "demo-token-not-secret"
inputs = load_inputs(demo_home)
db = DB.open(demo_home / "linkreach.db")

NOTE = ("Hi {f}, your post on onboarding new grads made a 40-step checklist sound like a heist plan. "
        "I'm a PM with 3+ years in AI SaaS, finishing my MS at Northeastern (Dec 2026). "
        "I rolled out AI tooling to 15+ enterprise clients. Would like to connect.")
FOLLOW = ("Dear {f},\n\nThanks for connecting. Your team's Copilot rollout is the rare launch that made "
          "spreadsheets feel exciting.\n\nMy name is Naveen Murali...\n\nBest,\nNaveen")
people = [
    ("Priya Raman", "Senior Technical Recruiter", "Microsoft", "recruiter", 1, "followed_up", 20),
    ("Marcus Chen", "Director of Product, Azure AI", "Microsoft", "leader", 0, "accepted", 12),
    ("Dana Ortiz", "Group Product Manager", "Microsoft", "ic", 1, "accepted", 9),
    ("<script>alert('xss')</script>", "Head of Product", "Salesforce", "leader", 0, "sent", 3),
    ("Sam Lee", "AI Platform Product Lead", "Salesforce", "ai_lead", 0, "sent", 8),
    ("Taylor Brooks", "University Recruiter", "Salesforce", "recruiter", 0, "sent", 10),
    ("Jordan Kim", "Talent Acquisition Partner", "Salesforce", "recruiter", 0, "sent", 15),
    ("Alex Novak", "Senior Product Manager", "Salesforce", "ic", 0, "dead", 18),
    ("Riley Patel", "Early Career Recruiter", "Salesforce", "recruiter", 0, "sent", 9),
    ("Chris Wu", "Principal Product Manager", "Microsoft", "ic", 0, "approved", None),
    ("Morgan Diaz", "Technical Recruiter", "Microsoft", "recruiter", 1, "needs_detail", None),
    ("Jamie Fox", "Head of Product", "Microsoft", "leader", 0, "drafted", None),
]
for i, (name, title, company, bucket, alumni, status, sent_days) in enumerate(people):
    first = name.split()[0] if not name.startswith("<") else "there"
    pid = db.add_person(name=name, title=title, company=company, profile_url=f"https://www.linkedin.com/in/demo-{i}/",
                        bucket=bucket, is_alumni=alumni, context_1="Posted about new-grad hiring", context_2=title)
    db.update_fields(pid, note_draft=NOTE.format(f=first), followup_draft=FOLLOW.format(f=first))
    path = {"followed_up": ["drafted", "approved", "sent", "accepted", "followed_up"],
            "accepted": ["drafted", "approved", "sent", "accepted"],
            "sent": ["drafted", "approved", "sent"], "dead": ["drafted", "approved", "sent", "dead"],
            "approved": ["drafted", "approved"], "drafted": ["drafted"], "needs_detail": ["needs_detail"]}[status]
    for st in path:
        extra = {}
        if st == "sent":
            extra = {"sent_at": clock.iso(clock.now() - timedelta(days=sent_days)), "note_sent": NOTE.format(f=first)}
        db.set_status(pid, st, **extra)
    if status == "accepted":
        db.update_fields(pid, follow_up_date=(clock.local_now().date() - timedelta(days=1)).isoformat())
    db.log("send", "send", pid, "clicked Send (manual)") if sent_days else None
db.update_fields(1, my_notes="Replied fast, wants a call next week")
db.log("collect", "run", None, "start")
db.log("collect", "view", None, "/company/microsoft/people/")
db.log("collect", "captcha", None, "captcha: page text matched 'quick security check'")
db.log("collect", "run", None, "end:stopped:captcha")
db.close()

app = create_app(inputs, demo_home / "linkreach.db", token=TOKEN)
print(f"demo data in {demo_home}")
print(f"open http://{HOST}:{PORT}/#token={TOKEN}", flush=True)
uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
