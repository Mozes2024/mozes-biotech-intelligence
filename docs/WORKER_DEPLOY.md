# Hot-lane worker deployment

GitHub Actions keeps research refresh and Pages; it never pushes alerts. The worker is the
single push owner and needs a process that stays up (not serverless).

## What the worker does every 60 s

FDA / wire / Nasdaq-halt / company-IR feeds → priority SEC + news → outbox dispatch →
heartbeat ping. Every `MOZES_BACKUP_HOURS` it writes an online SQLite backup.

On an empty DB the first pass is a silent warm-up: hours of backlog become the baseline
(outbox rows marked `dead` / `warm-up baseline`) instead of a burst of pushes.

## Option A — Linux VM with systemd

```bash
sudo useradd --system --home /opt/mozes mozes
sudo git clone https://github.com/Mozes2024/mozes-biotech-intelligence /opt/mozes
cd /opt/mozes && sudo python3 -m venv .venv && sudo .venv/bin/pip install . tzdata
sudo mkdir -p /etc/mozes /var/lib/mozes && sudo chown mozes:mozes /var/lib/mozes
sudo cp deploy/hot.env.example /etc/mozes/hot.env && sudo chmod 600 /etc/mozes/hot.env
sudo nano /etc/mozes/hot.env          # SEC_USER_AGENT, MOZES_NTFY_URL, MOZES_HEARTBEAT_URL
sudo cp deploy/mozes-hot.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mozes-hot
journalctl -u mozes-hot -f            # one JSON line per pass
```

Update: `cd /opt/mozes && sudo git pull && sudo .venv/bin/pip install . && sudo systemctl restart mozes-hot`.

## Option B — Docker

```bash
docker build -t mozes-hot .
docker run -d --name mozes-hot --restart unless-stopped \
  --env-file deploy/hot.env -v mozes-data:/data mozes-hot
docker logs -f mozes-hot
```

## Optional: start from the Actions monitor DB

With `gh` authenticated and `GITHUB_REPOSITORY=Mozes2024/mozes-biotech-intelligence`:

```bash
MOZES_DB_PATH=/var/lib/mozes/mozes-hot.db python scripts/restore_monitor_state.py
```

A restored DB is not empty, so there is no warm-up pass; only changes that are new to it are pushed.

## Checks after deploy

- `status` in the log is `OK` or `PARTIAL` (a single feed error is `PARTIAL`, not an outage).
- The heartbeat check shows pings every minute; it alerts if the worker stops.
- `ls /var/lib/mozes/backups` shows a new backup every `MOZES_BACKUP_HOURS`.
