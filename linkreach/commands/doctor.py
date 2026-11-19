"""linkreach doctor: check selectors, login, API key, caps, and DB, then exit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from rich.markup import escape

from linkreach import credentials
from linkreach.commands.common import EXIT_DOCTOR, Ctx, console, handle_run_errors, linkedin_session
from linkreach.linkedin import LinkedIn
from linkreach.safety import cap_status


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def default_api_check(model: str) -> tuple[bool, str]:
    """Verify the key with a Models API lookup (no tokens billed)."""
    key = credentials.api_key()
    if not key:
        return False, "ANTHROPIC_API_KEY not found in keychain, environment, or .env"
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key, max_retries=2, timeout=20.0)
        client.models.retrieve(model)
        return True, f"key works, model {model} available"
    except anthropic.AuthenticationError:
        return False, "API key rejected (401)"
    except anthropic.NotFoundError:
        return False, f"model {model!r} not found for this key"
    except anthropic.APIConnectionError:
        return False, "could not reach api.anthropic.com"
    except anthropic.APIStatusError as exc:
        return False, f"API error {exc.status_code}"


def offline_checks(ctx: Ctx, api_check: Callable[[str], tuple[bool, str]]) -> list[Check]:
    checks: list[Check] = [Check("config", True, "config.yaml, companies.csv, jobs.csv valid")]
    ok, detail = api_check(ctx.cfg.model)
    checks.append(Check("api_key", ok, detail))
    checks.append(Check("db_integrity", ctx.db.integrity_ok(), "PRAGMA integrity_check"))
    s = cap_status(ctx.db, ctx.cfg)
    checks.append(
        Check(
            "caps",
            s.views_left >= 2,
            f"views {s.views_today}/{s.views_cap} today, requests {s.requests_today}/{s.requests_cap} today, "
            f"{s.requests_week}/{s.week_cap} this week",
        )
    )
    return checks


def browser_checks(ctx: Ctx, li: LinkedIn) -> list[Check]:
    checks: list[Check] = []
    logged_in = li.logged_in()
    checks.append(Check("login", logged_in, "feed loads while signed in" if logged_in else "not signed in: run `linkreach login`"))
    if not logged_in:
        return checks
    p = li.read_profile(ctx.cfg.doctor_profile_url)
    checks.append(
        Check(
            "selectors",
            p.ok and bool(p.name),
            "name, headline, action buttons found" if p.ok else f"missing: {', '.join(p.missing)}",
        )
    )
    optional = [n for n, v in (("headline", p.headline), ("about", p.about)) if not v]
    if p.ok and optional:
        checks.append(Check("selectors_optional", True, f"not found on test profile (may be empty): {', '.join(optional)}"))
    return checks


def print_checks(checks: list[Check]) -> None:
    for c in checks:
        mark = "[green]PASS[/]" if c.ok else "[bold red]FAIL[/]"
        console.print(f"  {mark}  {c.name:<20} {escape(c.detail)}")


def run_with(ctx: Ctx, li: LinkedIn) -> bool:
    """Used by `send --auto` with an already-open session."""
    checks = offline_checks(ctx, ctx.api_check or default_api_check)
    if all(c.ok for c in checks):
        checks += browser_checks(ctx, li)
    print_checks(checks)
    passed = all(c.ok for c in checks)
    ctx.db.log("doctor", "info" if passed else "fail", None, "doctor passed" if passed else "doctor failed: " + ", ".join(c.name for c in checks if not c.ok))
    return passed


def run(ctx: Ctx) -> int:
    console.print("[bold]linkreach doctor[/]")
    checks = offline_checks(ctx, ctx.api_check or default_api_check)
    if not all(c.ok for c in checks):
        print_checks(checks)
        ctx.db.log("doctor", "fail", None, "doctor failed: " + ", ".join(c.name for c in checks if not c.ok))
        return EXIT_DOCTOR

    def _browser() -> int:
        with linkedin_session(ctx, "doctor") as li:
            more = browser_checks(ctx, li)
        all_checks = checks + more
        print_checks(all_checks)
        passed = all(c.ok for c in all_checks)
        ctx.db.log("doctor", "info" if passed else "fail", None, "doctor passed" if passed else "doctor failed")
        return 0 if passed else EXIT_DOCTOR

    return handle_run_errors(ctx, "doctor", _browser)
