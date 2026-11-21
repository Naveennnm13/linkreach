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
        """What the search will open. The numeric company id is read from the company's People
        tab at run time, so the dry run shows a placeholder for it."""
        return S.PEOPLE_SEARCH_URL.format(keywords=quote_plus(self.keywords), company_id=f"<{self.company.slug} id>")


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
        shown: set[str] = set()
        for s in searches:
            company = s.company.company_name
            q = CompanyQuota(ctx, company)
            if company not in shown:
                shown.add(company)
                shares = ", ".join(f"{b} {q.have.get(b, 0)}/{n}" for b, n in q.target.items())
                cached = "id cached" if ctx.db.get_company_id(s.company.slug) else "+1 view to look up its id"
                console.print(escape(
                    f"  P{s.company.priority}  {company}: {cfg.per_company_limit - q.left}/{cfg.per_company_limit} "
                    f"collected ({shares}); {cached}"
                ), soft_wrap=True)
            if q.left <= 0:
                note = "skipped: company full"
            elif s.bucket_hint != "alumni" and q._share_left(s.bucket_hint) == 0:
                note = "runs only if an earlier bucket leaves spare slots"
            else:
                note = ""
            console.print(escape(f"      [{s.bucket_hint}] {s.keywords}" + (f"   ({note})" if note else "")), soft_wrap=True)
            console.print(escape(f"        {s.url}"), style="dim", soft_wrap=True)
        return 0
    return handle_run_errors(ctx, "collect", lambda: _collect(ctx, searches))


def _company_count(ctx: Ctx, company: str) -> int:
    row = ctx.db.conn.execute("SELECT COUNT(*) FROM people WHERE company = ? COLLATE NOCASE", (company,)).fetchone()
    return int(row[0])


def _bucket_counts(ctx: Ctx, company: str) -> dict[str, int]:
    rows = ctx.db.conn.execute(
        "SELECT bucket, COUNT(*) AS n FROM people WHERE company = ? COLLATE NOCASE GROUP BY bucket", (company,)
    ).fetchall()
    return {r["bucket"]: int(r["n"]) for r in rows if r["bucket"]}


def split_slots(total: int, buckets: list[str]) -> dict[str, int]:
    """per_company_limit shared across buckets in config order, e.g. 6 over 4 -> 2, 2, 1, 1."""
    if not buckets:
        return {}
    base, extra = divmod(max(total, 0), len(buckets))
    return {b: base + (1 if i < extra else 0) for i, b in enumerate(buckets)}


class CompanyQuota:
    """How many more people each bucket may take at one company.

    Each bucket gets its share of per_company_limit (split_slots). A bucket that finds fewer
    people than its share in this run passes the unused slots on to the next bucket, so the
    company still fills up when one kind of role is scarce. The alumni pass draws from the
    same shares, so alumni come first inside each bucket without crowding the others out.
    """

    def __init__(self, ctx: Ctx, company: str):
        cfg = ctx.cfg
        self.left = cfg.per_company_limit - _company_count(ctx, company)
        self.target = split_slots(cfg.per_company_limit, list(cfg.buckets))
        self.have = _bucket_counts(ctx, company)
        self.room: dict[str, int] = {}
        self.spill = 0
        self.current: str | None = None

    def _share_left(self, bucket: str) -> int:
        return max(0, self.target.get(bucket, 0) - self.have.get(bucket, 0))

    def enter(self, bucket_hint: str) -> None:
        """Called before each search; moving to a new bucket hands on the previous one's spare slots."""
        if bucket_hint == "alumni" or bucket_hint == self.current:
            return
        if self.current is not None:
            self.spill = self.room.get(self.current, 0)
        self.current = bucket_hint
        self.room[bucket_hint] = self._share_left(bucket_hint) + self.spill
        self.spill = 0

    def worth_searching(self, bucket_hint: str) -> bool:
        if self.left <= 0:
            return False
        if bucket_hint == "alumni":
            return any(self._share_left(b) > 0 for b in self.target)
        return self.room.get(bucket_hint, 0) > 0

    def can_take(self, bucket_hint: str, bucket: str) -> bool:
        if self.left <= 0:
            return False
        if bucket_hint == "alumni":
            return self._share_left(bucket) > 0
        return bucket == bucket_hint and self.room.get(bucket, 0) > 0

    def took(self, bucket_hint: str, bucket: str) -> None:
        self.left -= 1
        self.have[bucket] = self.have.get(bucket, 0) + 1
        if bucket_hint != "alumni":
            self.room[bucket] -= 1


def _collect(ctx: Ctx, searches: list[PlannedSearch]) -> int:
    cfg = ctx.cfg
    added = 0
    quotas: dict[str, CompanyQuota] = {}
    with linkedin_session(ctx, "collect") as li:
        streak = FailStreak(li, "collect")
        for s in searches:
            company = s.company.company_name
            q = quotas.setdefault(company, CompanyQuota(ctx, company))
            q.enter(s.bucket_hint)
            if not q.worth_searching(s.bucket_hint):
                continue  # no page view spent on a bucket (or company) that is already full
            console.print(f"[cyan]search[/] {escape(company)}: {escape(s.keywords)}")
            candidates = li.search_company_people(s.company.slug, s.keywords)
            for c in candidates:
                if q.left <= 0:
                    break
                if ctx.db.has_profile(c.profile_url):
                    continue  # never contact the same person twice
                bucket = classify(c.title, cfg)
                if bucket is None or not q.can_take(s.bucket_hint, bucket):
                    continue  # checked before the profile view, so skipping costs nothing
                profile = li.read_profile(c.profile_url)
                if not profile.ok:
                    streak.fail(None, profile.missing)
                    continue
                streak.ok()
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
                    q.took(s.bucket_hint, bucket)
                    ctx.db.log("collect", "info", pid, f"collected bucket={bucket} alumni={int(is_alumni)}")
                    console.print(
                        f"  [green]+[/] {escape(profile.name or c.name)} ({bucket}{', alumni' if is_alumni else ''})"
                    )
    console.print(f"[bold]Collected {added} new people.[/]")
    return 0
