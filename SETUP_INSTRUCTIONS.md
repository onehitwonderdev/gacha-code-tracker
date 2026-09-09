# Phase 2 — Supabase Setup

## 1. Create the project
1. Go to https://supabase.com/dashboard and create a new project (free tier covers NFR-2).
2. Wait for provisioning, then open **Project Settings > API**.
3. Copy two values, you'll need both:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key (NOT the `anon` key) → `SUPABASE_KEY`, used by the scraper/notifier only.
   - The `anon` key is what the *frontend* dashboard uses (public reads).

## 2. Run the schema
1. Open **SQL Editor** in the Supabase dashboard.
2. Paste in the full contents of `supabase_setup.sql` and run it.
3. This creates `game_codes`, `notification_subscribers`, `notification_logs`, seeds the three
   permanent starter codes, adds indexes, enables RLS, and adds a `pending_notifications` view.
4. Verify in **Table Editor** that `game_codes` has 3 rows (the seeded permanent codes).

## 3. Set environment variables (scraper + notifier host)
Wherever `scraper.py` / `dedup_check.py` run (locally, GitHub Actions, or a serverless function):

```bash
export SUPABASE_URL="https://xxxxxxxx.supabase.co"
export SUPABASE_KEY="your-service-role-key"   # keep this secret, never ship to frontend
```

For GitHub Actions (Phase 2 roadmap item), add these as **repo secrets** and reference them in
your workflow's `env:` block — never hardcode them in the workflow YAML.

## 4. Run the ingestion pipeline
```bash
pip install requests beautifulsoup4
python scraper.py
```
This fetches Reddit + wiki candidates, classifies and dedupes them in memory, then upserts into
`game_codes` using `on_conflict=game,code` — so re-running on a schedule is safe and idempotent.

## 5. Run the dedup / notification check
```bash
python dedup_check.py
```
This reads the `pending_notifications` view, which already excludes:
- `PERMANENT` codes (FR-10 — no spam for starter codes)
- subscribers who've opted out of that game
- (code, subscriber) pairs already present in `notification_logs`

Wire your actual email dispatch (Resend/Brevo/SMTP, per FR-9) to iterate this list and, on
successful send, insert a row into `notification_logs` so the same alert never fires twice.

## 6. Schedule it (Phase 2 roadmap)
Simplest free-tier option: a GitHub Actions workflow on a cron trigger.

```yaml
# .github/workflows/scrape.yml
on:
  schedule:
    - cron: '*/15 * * * *'   # Standard Mode, FR-3
  workflow_dispatch: {}
jobs:
  scrape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install requests beautifulsoup4
      - run: python scraper.py
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
```

For **Stream Radar Mode** (FR-3, 2-minute polling during known livestream windows), add a second
workflow with a tighter cron that you manually enable/disable (or gate with a date check) around
announced livestream dates — GitHub Actions' minimum cron granularity is 1 minute but scheduling
is best-effort, so treat 2 minutes as a target, not a guarantee.
