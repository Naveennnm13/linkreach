from __future__ import annotations

import pytest
import yaml

from linkreach.browser import ProfileGuardError, default_chrome_dirs, guard_profile_dir, host_allowed
from linkreach.cli import main
from linkreach.config import ConfigError, load_inputs

from tests.conftest import write_inputs


def test_valid_inputs_load(env):
    inp = env.inputs
    assert [c.company_name for c in inp.companies] == ["Microsoft", "Salesforce"]
    assert inp.job_for("microsoft").job_title == "Product Manager II"
    assert inp.config.caps.daily_requests == 20


def test_all_errors_reported_at_once(env):
    d = env.inputs_dir
    cfg = yaml.safe_load((d / "config.yaml").read_text())
    del cfg["resume_url"]
    cfg["caps"]["daily_requests"] = 50  # above the hard ceiling
    (d / "config.yaml").write_text(yaml.safe_dump(cfg))
    (d / "companies.csv").write_text("company_name,linkedin_company_url\nAcme,https://www.linkedin.com/company/acme/\n")
    (d / "jobs.csv").write_text("company_name,job_title,job_url\nAcme,PM,not-a-url\n")
    with pytest.raises(ConfigError) as exc:
        load_inputs(d)
    text = "\n".join(exc.value.problems)
    assert "resume_url" in text
    assert "daily_requests" in text
    assert "companies.csv: missing required column(s): priority" in text
    assert "job_url" in text
    assert len(exc.value.problems) >= 4


def test_bad_company_row_rejected(env):
    (env.inputs_dir / "companies.csv").write_text(
        "company_name,linkedin_company_url,priority\nAcme,https://acme.com,5\n"
    )
    with pytest.raises(ConfigError) as exc:
        load_inputs(env.inputs_dir)
    text = "\n".join(exc.value.problems)
    assert "linkedin_company_url" in text and "priority" in text


def test_default_chrome_profile_refused(env):
    default = default_chrome_dirs()[0]
    write_inputs(env.inputs_dir, {"chrome_profile_dir": str(default)})
    with pytest.raises(ConfigError) as exc:
        load_inputs(env.inputs_dir)
    assert "default browser profile" in "\n".join(exc.value.problems)
    # a profile folder inside it is refused too
    with pytest.raises(ProfileGuardError):
        guard_profile_dir(default / "Default")


def test_cli_refuses_to_start_on_default_profile(env, capsys):
    write_inputs(env.inputs_dir, {"chrome_profile_dir": str(default_chrome_dirs()[0])})
    code = main(["list"])
    assert code == 2
    assert "refuses to start" in capsys.readouterr().out


def test_dedicated_profile_allowed(env, tmp_path):
    guard_profile_dir(tmp_path / ".linkreach" / "chrome-profile")


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://www.linkedin.com/in/x/", True),
        ("https://static.licdn.com/a.js", True),
        ("https://media.licdn.com/img.png", True),
        ("https://linkedin.com/feed/", True),
        ("https://evil.example/", False),
        ("https://linkedin.com.evil.example/", False),
        ("https://notlinkedin.com/", False),
        ("javascript:alert(1)", False),
        ("about:blank", True),
    ],
)
def test_domain_allowlist(url, ok):
    assert host_allowed(url) is ok
