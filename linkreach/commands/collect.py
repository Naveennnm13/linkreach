"""linkreach collect: find people at each company and save them as `collected`."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus

from rich.markup import escape

from linkreach import logsetup
from linkreach import selectors as S
from linkreach.commands.common import Ctx, FailStreak, console, handle_run_errors, linkedin_session
from linkreach.config import Config, Company
from linkreach.linkedin import Profile

log = logsetup.get("collect")


@dataclass
class PlannedSearch:
    company: Company
    keywords: str
    bucket_hint: str  # bucket name or "alumni"

    @property
    def url(self) -> str:
        return S.COMPANY_PEOPLE_URL.format(slug=quote_plus(self.company.slug), keywords=quote_plus(self.keywords))


def classify(title: str, cfg: Config) -> str | None:
    """Bucket for a title, or None if excluded / unmatched. Bucket order in
    config.yaml is the match priority."""
    t = (title or "").lower()
    if not t:
        return None
    for word in cfg.exclude_title_keywords:
        if _has_word(t, word.lower()):
            return None
    for name, bucket in cfg.buckets.items():
        for kw in bucket.keywords:
            if kw.lower() in t:
                return name
    return None


def _has_word(text: str, word: str) -> bool:
    import re

    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def plan(cfg: Config, companies: list[Company]) -> list[PlannedSearch]:
    """Alumni pass first, then every bucket keyword, companies in priority order."""
    out: list[PlannedSearch] = []
    for c in companies:
        if cfg.alumni_school:
            out.append(PlannedSearch(c, cfg.alumni_school, "alumni"))
        for name, bucket in cfg.buckets.items():
            if cfg.combine_keywords:
                # One page view per bucket instead of one per keyword.
                out.append(PlannedSearch(c, " OR ".join(f'"{k}"' for k in bucket.keywords), name))
            else:
                for kw in bucket.keywords:
                    out.append(PlannedSearch(c, kw, name))
    return out


def pick_contexts(p: Profile) -> tuple[str, str]:
    vals = [v for v in (p.about, p.featured, p.headline) if v]
    return (vals[0] if vals else "", vals[1] if len(vals) > 1 else "")


def run(ctx: Ctx, dry_run: bool = False) -> int:
    cfg = ctx.cfg
    searches = plan(cfg, ctx.inputs.companies)
    if dry_run:
        console.print(f"[bold]Planned searches[/] ({len(searches)}), nothing will be opened:")
        for s in searches:
            existing = _company_count(ctx, s.company.company_name)
            console.print(
                escape(
                    f"  P{s.company.priority}  {s.company.company_name:<24} [{s.bucket_hint}] "
                    f"{s.keywords}\n      {s.url}   (have {existing}/{cfg.per_company_limit})"
                )
            )
        return 0
    return handle_run_errors(ctx, "collect", lambda: _collect(ctx, searches))


def _company_count(ctx: Ctx, company: str) -> int:
    row = ctx.db.conn.execute("SELECT COUNT(*) FROM people WHERE company = ? COLLATE NOCASE", (company,)).fetchone()
    return int(row[0])


def _collect(ctx: Ctx, searches: list[PlannedSearch]) -> int:
    cfg = ctx.cfg
    added = 0
    with linkedin_session(ctx, "collect") as li:
        streak = FailStreak(li, "collect")
        for s in searches:
            company = s.company.company_name
            remaining = cfg.per_company_limit - _company_count(ctx, company)
            if remaining <= 0:
                continue
            console.print(f"[cyan]search[/] {escape(company)}: {escape(s.keywords)}")
            candidates = li.search_company_people(s.company.slug, s.keywords)
            for c in candidates:
                if remaining <= 0:
                    break
                if ctx.db.has_profile(c.profile_url):
                    continue  # never contact the same person twice
                bucket = classify(c.title, cfg)
                if bucket is None:
                    continue
                profile = li.read_profile(c.profile_url)
                if not profile.ok:
                    streak.fail(None, profile.missing)
                    continue
                streak.ok()
                bucket = classify(profile.headline, cfg) or bucket
                is_alumni = bool(cfg.alumni_school) and cfg.alumni_school.lower() in profile.education.lower()
                c1, c2 = pick_contexts(profile)
                job = ctx.inputs.job_for(company)
                pid = ctx.db.add_person(
                    name=profile.name or c.name,
                    title=c.title or profile.headline[:200],
                    company=company,
                    profile_url=c.profile_url,
                    bucket=bucket,
                    is_alumni=1 if is_alumni else 0,
                    context_1=c1,
                    context_2=c2,
                    job_title=job.job_title if job else None,
                )
                if pid is not None:
                    added += 1
                    remaining -= 1
                    ctx.db.log("collect", "info", pid, f"collected bucket={bucket} alumni={int(is_alumni)}")
                    console.print(
                        f"  [green]+[/] {escape(profile.name or c.name)} ({bucket}{', alumni' if is_alumni else ''})"
                    )
    console.print(f"[bold]Collected {added} new people.[/]")
    return 0
