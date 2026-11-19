"""linkreach review: approve, edit, redraft, or skip each draft in the terminal.

Approval always re-runs validation, so a rejected draft can never reach `approved`.
"""

from __future__ import annotations

import os
import shlex
import subprocess  # nosec B404 - used only to launch your own $EDITOR on a temp file
import tempfile
from pathlib import Path
from typing import Any, Callable

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from linkreach import paths, validate
from linkreach.commands.common import Ctx, console
from linkreach.drafting import Drafter, email_subject, save_outcome

NOTE_MARK = "# ===== CONNECTION NOTE (max {n} characters) ====="
FOLLOW_MARK = "# ===== FOLLOW-UP MESSAGE (max {n} words) ====="

Editor = Callable[[str], str]


def default_editor(text: str) -> str:
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ("notepad" if os.name == "nt" else "nano")
    paths.secure_dir(paths.tmp_dir())
    fd, name = tempfile.mkstemp(prefix="draft_", suffix=".txt", dir=str(paths.tmp_dir()))
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        paths.secure_file(path)
        cmd = shlex.split(editor, posix=(os.name != "nt")) + [str(path)]
        subprocess.run(cmd, check=False)  # nosec B603 - argv list, no shell; editor is your own setting
        return path.read_text(encoding="utf-8")
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def to_edit_text(ctx: Ctx, note: str, followup: str) -> str:
    return (
        "# Edit the text below. Lines starting with # are ignored.\n"
        + NOTE_MARK.format(n=ctx.cfg.note_max_chars) + "\n"
        + note.strip() + "\n"
        + FOLLOW_MARK.format(n=ctx.cfg.followup_max_words) + "\n"
        + followup.strip() + "\n"
    )


def from_edit_text(ctx: Ctx, text: str) -> tuple[str, str]:
    note_mark = NOTE_MARK.format(n=ctx.cfg.note_max_chars)
    follow_mark = FOLLOW_MARK.format(n=ctx.cfg.followup_max_words)
    section = None
    note_lines: list[str] = []
    follow_lines: list[str] = []
    for line in text.splitlines():
        if line.strip() == note_mark:
            section = "note"
            continue
        if line.strip() == follow_mark:
            section = "follow"
            continue
        if line.lstrip().startswith("#"):
            continue
        if section == "note":
            note_lines.append(line)
        elif section == "follow":
            follow_lines.append(line)
    note = " ".join(l.strip() for l in note_lines if l.strip())
    followup = "\n".join(follow_lines).strip()
    return note, followup


def _validate(ctx: Ctx, note: str | None, followup: str | None) -> validate.Result:
    cfg = ctx.cfg
    return validate.validate_draft(
        note,
        followup,
        resume_url=cfg.resume_url,
        banned_phrases=cfg.banned_phrases,
        note_max_chars=cfg.note_max_chars,
        followup_max_words=cfg.followup_max_words,
    )


def show(ctx: Ctx, row: Any) -> None:
    info = Table.grid(padding=(0, 2))
    info.add_row("[bold]Name[/]", escape(row["name"]))
    info.add_row("[bold]Title[/]", escape(row["title"] or ""))
    info.add_row("[bold]Company[/]", escape(row["company"]))
    info.add_row("[bold]Bucket[/]", escape(row["bucket"] or "") + ("  [magenta]ALUMNI[/]" if row["is_alumni"] else ""))
    if row["job_title"]:
        info.add_row("[bold]Role[/]", escape(row["job_title"]))
    info.add_row("[bold]Context 1[/]", escape(row["context_1"] or "—"))
    info.add_row("[bold]Context 2[/]", escape(row["context_2"] or "—"))
    if row["user_detail"]:
        info.add_row("[bold]Your detail[/]", escape(row["user_detail"]))
    console.print(Panel(info, title=f"#{row['id']}  status: {row['status']}", expand=True))
    if row["validation_errors"]:
        console.print(Panel(escape(row["validation_errors"]), title="Validation", border_style="red"))
    if row["note_draft"]:
        n = row["note_draft"]
        console.print(Panel(escape(n), title=f"Connection note — {len(n)}/{ctx.cfg.note_max_chars} chars"))
    if row["followup_draft"]:
        f = row["followup_draft"]
        wc = validate.word_count(f)
        console.print(
            Panel(
                escape(f),
                title=f"Follow-up — {wc}/{ctx.cfg.followup_max_words} words",
                subtitle=escape(f"email subject: {email_subject(ctx.cfg, row['job_title'])}"),
            )
        )


def run(ctx: Ctx, editor: Editor = default_editor) -> int:
    db = ctx.db
    drafter: Drafter = ctx.drafter or Drafter(ctx.cfg)  # type: ignore[assignment]

    def order(r: Any) -> tuple:
        return (0 if r["status"] == "needs_detail" else 1, 0 if r["is_alumni"] else 1, ctx.inputs.priority_of(r["company"]), r["id"])

    rows = sorted(db.people(["needs_detail", "drafted"]), key=order)
    if not rows:
        console.print("Nothing to review. Run `linkreach draft` first.")
        return 0
    approved = 0
    for start in rows:
        pid = int(start["id"])
        while True:
            row = db.get(pid)
            if row is None or row["status"] not in ("needs_detail", "drafted"):
                break
            console.rule()
            show(ctx, row)
            if row["status"] == "needs_detail":
                console.print(
                    "[bold]needs_detail:[/] type one line of context for the opener and press Enter to redraft.\n"
                    "Or: [bold]e[/] edit draft by hand, [bold]s[/] skip person, [bold]n[/] next, [bold]q[/] quit."
                )
                answer = ctx.input_fn("> ").strip()
                key = answer.lower()
                if key == "q":
                    console.print(f"Approved {approved}.")
                    return 0
                if key in ("", "n"):
                    break
                if key == "s":
                    db.set_status(pid, "skipped")
                    break
                if key == "e":
                    _edit(ctx, row, editor)
                    continue
                _redraft(ctx, drafter, pid, answer)
                continue

            answer = ctx.input_fn("[a]pprove  [e]dit  [d]etail+redraft  [s]kip  [q]uit > ").strip().lower()
            if answer == "q":
                console.print(f"Approved {approved}.")
                return 0
            if answer == "a":
                res = _validate(ctx, row["note_draft"], row["followup_draft"])
                if res.ok:
                    db.set_status(pid, "approved")
                    db.update_fields(pid, validation_errors=None)
                    approved += 1
                    console.print("[green]approved[/]")
                    break
                db.update_fields(pid, validation_errors="\n".join(res.errors))
                db.set_status(pid, "needs_detail")
                console.print("[red]Draft failed validation; moved to needs_detail.[/]")
                continue
            if answer == "e":
                _edit(ctx, row, editor)
                continue
            if answer == "d":
                detail = ctx.input_fn("One line of context: ").strip()
                if detail:
                    _redraft(ctx, drafter, pid, detail)
                continue
            if answer == "s":
                db.set_status(pid, "skipped")
                break
    console.print(f"[bold]Review done. Approved {approved}.[/]")
    return 0


def _edit(ctx: Ctx, row: Any, editor: Editor) -> None:
    text = editor(to_edit_text(ctx, row["note_draft"] or "", row["followup_draft"] or ""))
    note, followup = from_edit_text(ctx, text)
    res = _validate(ctx, note, followup)
    pid = int(row["id"])
    ctx.db.update_fields(pid, note_draft=note, followup_draft=followup, validation_errors="\n".join(res.errors) or None)
    if res.ok:
        ctx.db.set_status(pid, "drafted")
        console.print("[green]Edit passes validation.[/] Press a to approve.")
    else:
        if row["status"] != "needs_detail":
            ctx.db.set_status(pid, "needs_detail")
        console.print("[red]Edited draft fails validation.[/]")


def _redraft(ctx: Ctx, drafter: Drafter, pid: int, detail: str) -> None:
    from linkreach import sanitize

    ctx.db.update_fields(pid, user_detail=sanitize.clean_line(detail, 300))
    row = ctx.db.get(pid)
    console.print("[cyan]Redrafting...[/]")
    try:
        outcome = drafter.draft(row, ctx.db.recent_openers(5))
    except Exception as exc:
        console.print(f"[red]Draft failed: {type(exc).__name__}[/]")
        return
    save_outcome(ctx.db, ctx.cfg, outcome)
