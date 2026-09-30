# Deploy

Run the bot 24/7 on a small VPS or a Raspberry Pi, and keep the vault in sync with your
laptop through git. Everything runs as one systemd service plus a sync timer.

```
server (VPS / Pi)                                   laptop
/opt/second-brain   code, .venv, .env, secrets/,    Obsidian + Obsidian Git plugin
                    state.db   (never in the vault)        │
/srv/vault          the vault (a git clone) ◀── vault-sync.timer ──▶ GitHub (private repo)
```

The files referenced below live in [`deploy/`](../deploy).

## 1. Prepare the host

Debian / Ubuntu / Raspberry Pi OS, Python 3.11+.

```bash
sudo apt install -y python3 python3-venv git
sudo useradd --system --home-dir /var/lib/secondbrain --create-home --shell /usr/sbin/nologin secondbrain
sudo git clone https://github.com/<you>/second-brain.git /opt/second-brain
sudo chown -R secondbrain:secondbrain /opt/second-brain
sudo -u secondbrain python3 -m venv /opt/second-brain/.venv
sudo -u secondbrain /opt/second-brain/.venv/bin/pip install -r /opt/second-brain/requirements.txt
```

## 2. Secrets and configuration

```bash
sudo -u secondbrain cp /opt/second-brain/.env.example /opt/second-brain/.env
sudo -u secondbrain chmod 600 /opt/second-brain/.env
sudoedit /opt/second-brain/.env
```

Set at least `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID` and
`VAULT_PATH=/srv/vault`. Keep the default relative `STATE_DB_PATH=state.db` (it lands in
`/opt/second-brain`).

**Secrets never touch the vault.** The vault is pushed to git, so anything inside it should be
treated as published. `.env`, `state.db` and `secrets/` stay in `/opt/second-brain`. The bot
enforces this at startup: if `.env`, `STATE_DB_PATH`, `GOOGLE_CREDENTIALS_PATH` or
`GOOGLE_TOKEN_PATH` resolves to a path inside `VAULT_PATH` (including via `..` or a symlink), it
refuses to start and says which one.

Google Calendar (optional): run `scripts/google_auth.py` on your laptop (see
[DEVELOPMENT.md](DEVELOPMENT.md#google-calendar-optional)), then copy the token:

```bash
scp secrets/google_token.json server:/tmp/ && ssh server \
  'sudo install -o secondbrain -g secondbrain -m 600 -D /tmp/google_token.json \
   /opt/second-brain/secrets/google_token.json && rm /tmp/google_token.json'
```

## 3. The vault on the server

Put the vault in a **private** GitHub repo and give the server a deploy key with write access:

```bash
sudo -u secondbrain ssh-keygen -t ed25519 -N "" -f /var/lib/secondbrain/.ssh/id_ed25519
sudo cat /var/lib/secondbrain/.ssh/id_ed25519.pub   # add as a deploy key, "Allow write access"
sudo mkdir -p /srv/vault && sudo chown secondbrain:secondbrain /srv/vault
sudo -u secondbrain git clone git@github.com:<you>/vault.git /srv/vault
sudo -u secondbrain git -C /srv/vault config user.name "Second Brain"
sudo -u secondbrain git -C /srv/vault config user.email "second-brain@localhost"
```

Recommended vault `.gitignore` (per-device Obsidian state that only causes conflicts):

```
.obsidian/workspace*.json
.obsidian/cache
.trash/
```

## 4. Install and start the services

If your paths differ from `/opt/second-brain` and `/srv/vault`, edit the unit files first
(`WorkingDirectory`, `ExecStart`, `ReadWritePaths`, `VAULT_PATH`).

```bash
sudo cp /opt/second-brain/deploy/second-brain.service \
        /opt/second-brain/deploy/vault-sync.service \
        /opt/second-brain/deploy/vault-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now second-brain.service vault-sync.timer
```

- `second-brain.service` runs `python -m second_brain.main` as `secondbrain`, restarts on
  failure, and is sandboxed (read-only system, no `/home`, private `/tmp`, `UMask=0077`).
  It has no `EnvironmentFile=` on purpose: the app loads `.env` itself, and systemd would
  treat the inline `# comments` in `.env` as part of the values.
- The scheduler (reminders) runs inside this same process; there is no separate cron.

## 5. Vault sync

`vault-sync.timer` runs `deploy/vault-sync.sh` every 10 minutes (and 2 minutes after boot). One
pass: `git add -A` → commit if anything changed → `git pull --rebase` → `git push`.

It never forces and never resets. On a conflict it aborts the rebase, keeps the local commit,
and exits 1, so the unit shows as failed until you fix it:

```bash
sudo -u secondbrain git -C /srv/vault pull --rebase   # resolve the files, then:
sudo -u secondbrain git -C /srv/vault rebase --continue && sudo systemctl start vault-sync
```

Conflicts are rare in practice: the bot only creates new files (`00-Gelen/`, `bookmarks/`) and
you mostly edit on the laptop.

**Laptop side:** install the *Obsidian Git* community plugin and enable auto pull on startup plus
auto commit-and-sync every ~10 minutes. Pull before editing notes the bot might touch.

**Alternative: Syncthing.** If you do not want git history, sync `/srv/vault` with Syncthing
instead and skip the timer. Syncthing copies conflicting edits to `*.sync-conflict-*` files
rather than blocking. Keep the same rule: nothing from `/opt/second-brain` in the synced folder.

## 6. Operate

```bash
journalctl -u second-brain -f                 # bot logs
systemctl status vault-sync.service           # last sync result
journalctl -u vault-sync --since today
systemctl list-timers vault-sync.timer        # next sync
```

Update the code:

```bash
sudo -u secondbrain git -C /opt/second-brain pull
sudo -u secondbrain /opt/second-brain/.venv/bin/pip install -r /opt/second-brain/requirements.txt
sudo systemctl restart second-brain
```

Backups: the vault is already in git. `state.db` holds only operational state (reminder queue,
history); losing it loses no notes, but back it up if you care about pending reminders.
