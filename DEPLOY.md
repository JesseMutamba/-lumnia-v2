# Deploying Lumnia to Fly.io

One small always-on-capable container + a persistent disk for the SQLite
database and stored uploads. Access is gated by a single shared password.

## One-time setup

1. **Install the CLI and sign in** (on your Mac):
   ```bash
   brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
   fly auth signup            # or `fly auth login` if you already have an account
   ```

2. **Pick a unique app name** — edit the `app = "lumnia"` line in `fly.toml`
   to something free, e.g. `lumnia-jesse`. Adjust `primary_region` if you want
   (list them with `fly platform regions`; `iad` = US East).

3. **Create the app and its volume** (from the repo root):
   ```bash
   fly apps create lumnia-jesse          # must match fly.toml
   fly volume create lumnia_data --region iad --size 1   # 1 GB disk
   ```

4. **Set the access password** (this is what gates the site):
   ```bash
   fly secrets set LUMNIA_PASSWORD='choose-a-strong-password'
   ```

   **Optional — AI narrative.** To enable the one-click executive narrative
   (Claude phrases the pipeline's verified numbers; it never computes), add an
   Anthropic API key from https://console.anthropic.com:
   ```bash
   fly secrets set ANTHROPIC_API_KEY='sk-ant-...'
   ```
   Without it the feature simply doesn't appear — everything else works.
   Each narrative is one small API call (fractions of a cent), cached with
   the analysis until you re-run it.

5. **Deploy:**
   ```bash
   fly deploy
   ```
   Fly builds the Docker image, mounts the volume at `/data`, and gives you a
   URL like `https://lumnia-jesse.fly.dev`. Open it → password prompt →
   dashboard.

## Everyday updates

After merging changes to `main`:
```bash
git pull origin main
fly deploy
```

## Notes

- **Data safety.** The SQLite file and every uploaded workbook live on the
  `lumnia_data` volume, which survives deploys and restarts. `fly deploy` never
  touches it. Take a snapshot before risky changes: `fly volume snapshots list`.
- **Password change.** `fly secrets set LUMNIA_PASSWORD='new'` redeploys and
  instantly logs everyone out (sessions are keyed by the password).
- **Always-on vs scale-to-zero.** Default `min_machines_running = 0` in
  `fly.toml` is cheapest; the site cold-starts in a few seconds on the first
  request after idle. For instant loads (e.g. a live client demo) set it to `1`
  and `fly deploy`.
- **Cost.** One `shared-cpu-1x` / 512 MB machine + a 1 GB volume is roughly a
  couple of USD per month at scale-to-zero.
- **Bigger files.** The app rejects uploads over 25 MB (`MAX_UPLOAD_BYTES` in
  `app/main.py`). If a large workbook OOMs, raise `memory` in `fly.toml` to
  `1024mb` and redeploy.
- **Logs / status:** `fly logs`, `fly status`, `fly ssh console`.
