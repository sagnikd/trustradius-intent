# TrustRadius → HCL Unica intent dashboard

## Weekly refresh

Drop the new week's TrustRadius export anywhere and run:

```bash
python3 build_dashboard.py /path/to/new_export.csv
```

This merges it into `master_activity.csv` (deduped by `Activity ID` — safe to
re-run with an overlapping file, duplicates are dropped automatically), then
regenerates `dashboard.xlsx`.

You can pass more than one file at once if you're catching up on several
weeks.

## What's in dashboard.xlsx

- **Priority Accounts** — ICP-fit accounts only (industry + size/revenue hard
  filter), ranked by weighted intent score over the trailing 30 days. Shows
  raw activity count, Unica-specific activity count, trend vs the prior 30
  days, and which competitors were researched.
- **Competitor x Industry x Size** — rollup of competitor research activity
  by industry and size band, ICP accounts only.
- **Unmapped Products** — products seen under a tracked vendor (Adobe,
  Salesforce, Oracle, etc.) that aren't in the competitor allow-list in
  `config.py` — mostly deliberately-excluded products (e.g. Adobe Photoshop,
  Salesforce Sales Cloud) so you can eyeball that nothing relevant is being
  silently dropped.
- **Run Info** — window dates and row counts for this run.

## Adjusting targeting or scoring

Edit `config.py`:
- `ICP_INDUSTRIES`, `MIN_EMPLOYEES`, `MIN_REVENUE` — the ICP hard filter.
- `RULES` — which vendor/product pairs count as which competitor bucket.
- `LABEL_WEIGHTS`, `OWN_PRODUCT_MULTIPLIER`, `COMPETITOR_MULTIPLIER` — scoring.

No code changes needed for any of that — just re-run `build_dashboard.py`
(no new file argument needed) to regenerate the dashboard with new settings.

## Known limitation right now

Trend vs prior 30 days will show "New" for most accounts until you've fed in
enough weekly files to cover a full 60-day span — there's currently only
~5 weeks of history in `master_activity.csv`, so the "prior window" is mostly
empty. This resolves itself automatically as you keep uploading weekly.
