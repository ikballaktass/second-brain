"""Deploy: secrets-off-vault guard, vault-sync.sh against real temp git repos, systemd units."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from second_brain import config as config_module
from second_brain import main as main_module
from second_brain.config import assert_outside_vault

REPO = Path(__file__).resolve().parents[1]
SYNC = REPO / "deploy" / "vault-sync.sh"


# --- assert_outside_vault -----------------------------------------------------------------

@pytest.fixture
def paths(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "app"
    outside.mkdir()
    monkeypatch.setattr(config_module, "ENV_FILE", str(outside / ".env"))
    monkeypatch.setenv("STATE_DB_PATH", str(outside / "state.db"))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(outside / "secrets" / "cred.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(outside / "secrets" / "token.json"))
    return vault, outside


def test_all_outside_passes(paths):
    vault, _ = paths
    assert_outside_vault(str(vault))


@pytest.mark.parametrize("var", ["STATE_DB_PATH", "GOOGLE_CREDENTIALS_PATH", "GOOGLE_TOKEN_PATH"])
def test_each_path_inside_vault_is_rejected(paths, monkeypatch, var):
    vault, _ = paths
    monkeypatch.setenv(var, str(vault / "sub" / "file"))
    with pytest.raises(RuntimeError, match=var):
        assert_outside_vault(str(vault))


def test_env_file_inside_vault_is_rejected(paths, monkeypatch):
    vault, _ = paths
    monkeypatch.setattr(config_module, "ENV_FILE", str(vault / ".env"))
    with pytest.raises(RuntimeError, match=r"\.env"):
        assert_outside_vault(str(vault))


def test_dotdot_escape_is_resolved(paths, monkeypatch):
    vault, outside = paths
    monkeypatch.setenv("STATE_DB_PATH", str(outside / ".." / "vault" / "state.db"))
    with pytest.raises(RuntimeError, match="STATE_DB_PATH"):
        assert_outside_vault(str(vault))


def test_symlink_into_vault_is_caught(paths, monkeypatch):
    vault, outside = paths
    (outside / "link").symlink_to(vault, target_is_directory=True)
    monkeypatch.setenv("STATE_DB_PATH", str(outside / "link" / "state.db"))
    with pytest.raises(RuntimeError, match="STATE_DB_PATH"):
        assert_outside_vault(str(vault))


def test_defaults_resolve_against_working_directory(paths, monkeypatch):
    vault, _ = paths
    monkeypatch.delenv("STATE_DB_PATH")  # default "state.db" in the cwd
    monkeypatch.chdir(vault)
    with pytest.raises(RuntimeError, match="STATE_DB_PATH"):
        assert_outside_vault(str(vault))


def test_sibling_with_common_prefix_is_not_inside(paths, monkeypatch, tmp_path):
    vault, _ = paths
    monkeypatch.setenv("STATE_DB_PATH", str(tmp_path / "vault-backup" / "state.db"))
    assert_outside_vault(str(vault))


def test_no_env_file_is_fine(paths, monkeypatch):
    vault, _ = paths
    monkeypatch.setattr(config_module, "ENV_FILE", "")
    assert_outside_vault(str(vault))


def test_build_refuses_state_db_in_vault(monkeypatch, tmp_path):
    vault = tmp_path / "vault"
    monkeypatch.setenv("VAULT_PATH", str(vault))
    monkeypatch.setenv("STATE_DB_PATH", str(vault / "state.db"))
    monkeypatch.setattr(main_module, "LLMClient", lambda: pytest.fail("must stop before LLM"))
    with pytest.raises(RuntimeError, match="inside the vault"):
        main_module.build()
    assert not (vault / "state.db").exists()


# --- vault-sync.sh ------------------------------------------------------------------------

def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def clone(remote, dest):
    subprocess.run(["git", "clone", "-q", str(remote), str(dest)], check=True)
    git(dest, "config", "user.email", "t@example.com")
    git(dest, "config", "user.name", "Test")
    return dest


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    seed = clone(remote, tmp_path / "seed")
    git(seed, "checkout", "-q", "-b", "main")
    (seed / "note.md").write_text("v1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "main")
    server = clone(remote, tmp_path / "server")
    laptop = clone(remote, tmp_path / "laptop")
    return remote, server, laptop


def sync(vault, env_vault=True):
    env = {**os.environ}
    env.pop("VAULT_PATH", None)
    args = [str(SYNC)] if env_vault else [str(SYNC), str(vault)]
    if env_vault:
        env["VAULT_PATH"] = str(vault)
    return subprocess.run(args, env=env, capture_output=True, text=True)


def test_script_syntax():
    subprocess.run(["bash", "-n", str(SYNC)], check=True)
    assert os.access(SYNC, os.X_OK)


def test_sync_commits_and_pushes_local_changes(repos):
    remote, server, laptop = repos
    (server / "00-Gelen").mkdir()
    (server / "00-Gelen" / "yeni.md").write_text("bot yazdı\n")

    result = sync(server)

    assert result.returncode == 0, result.stderr
    assert "vault-sync: ok" in result.stdout
    git(laptop, "pull", "-q")
    assert (laptop / "00-Gelen" / "yeni.md").read_text() == "bot yazdı\n"
    assert git(laptop, "log", "-1", "--format=%s").stdout.startswith("vault: sync from ")


def test_sync_without_changes_makes_no_commit(repos):
    _, server, _ = repos
    before = git(server, "rev-parse", "HEAD").stdout
    assert sync(server, env_vault=False).returncode == 0
    assert git(server, "rev-parse", "HEAD").stdout == before


def test_sync_rebases_onto_remote_changes(repos):
    _, server, laptop = repos
    (laptop / "laptop.md").write_text("laptoptan\n")
    git(laptop, "add", "-A")
    git(laptop, "commit", "-q", "-m", "laptop edit")
    git(laptop, "push", "-q")
    (server / "server.md").write_text("sunucudan\n")

    assert sync(server).returncode == 0
    assert (server / "laptop.md").exists()
    git(laptop, "pull", "-q")
    assert (laptop / "server.md").exists()
    assert git(laptop, "rev-list", "--merges", "HEAD").stdout == ""  # linear history


def test_conflict_aborts_keeps_local_commit_and_fails(repos):
    remote, server, laptop = repos
    (laptop / "note.md").write_text("laptop version\n")
    git(laptop, "commit", "-qam", "laptop")
    git(laptop, "push", "-q")
    (server / "note.md").write_text("server version\n")

    result = sync(server)

    assert result.returncode == 1
    assert "conflict" in result.stderr
    assert not (server / ".git" / "rebase-merge").exists()
    assert (server / "note.md").read_text() == "server version\n"  # nothing dropped
    assert git(server, "log", "-1", "--format=%s").stdout.startswith("vault: sync from ")
    # Remote untouched: no force push.
    assert git(laptop, "rev-parse", "HEAD").stdout == git(
        laptop, "ls-remote", str(remote), "refs/heads/main").stdout.split()[0] + "\n"


def test_refuses_while_rebase_in_progress_and_bad_input(repos, tmp_path):
    _, server, _ = repos
    (server / ".git" / "rebase-merge").mkdir()
    assert sync(server).returncode == 2

    plain = tmp_path / "plain"
    plain.mkdir()
    assert sync(plain).returncode == 2

    env = {k: v for k, v in os.environ.items() if k != "VAULT_PATH"}
    assert subprocess.run([str(SYNC)], env=env, capture_output=True).returncode == 2


# --- systemd units ------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_units_verify(tmp_path):
    python = shutil.which("python3")
    for name in ("second-brain.service", "vault-sync.service", "vault-sync.timer"):
        text = (REPO / "deploy" / name).read_text()
        text = text.replace("/opt/second-brain/.venv/bin/python", python)
        text = text.replace("/opt/second-brain/deploy/vault-sync.sh", str(SYNC))
        (tmp_path / name).write_text(text)
    result = subprocess.run(
        ["systemd-analyze", "verify", *(str(tmp_path / n) for n in
         ("second-brain.service", "vault-sync.service", "vault-sync.timer"))],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
