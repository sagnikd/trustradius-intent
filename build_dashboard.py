#!/usr/bin/env python3
"""
TrustRadius intent → HCL Unica priority-account dashboard.

Usage:
    python3 build_dashboard.py [new_export1.csv new_export2.csv ...]

- Any CSV paths passed in are merged into master_activity.csv, deduped by
  Activity ID (TrustRadius exports are rolling ~30-day windows and overlap
  week to week, so the same activity shows up in multiple files).
- With no arguments, it just regenerates dashboard.xlsx from the existing
  master_activity.csv.
"""
import sys
import re
import json
from pathlib import Path

import pandas as pd

import config

BASE_DIR = Path(__file__).parent
MASTER_PATH = BASE_DIR / "master_activity.csv"
OUTPUT_PATH = BASE_DIR / "dashboard.xlsx"
JSON_OUTPUT_PATH = BASE_DIR / "dashboard_data.json"
# Same payload minus individual-level lead PII (names/emails/phones) — safe for
# hosts that aren't access-controlled (e.g. the Vercel deploy).
JSON_PUBLIC_OUTPUT_PATH = BASE_DIR / "dashboard_data_public.json"
LEADS_MASTER_PATH = BASE_DIR / "master_leads.csv"

OBJECT_SLOTS = ["1", "2", "3"]
LEAD_COLUMNS = [
    "Delivery Date", "First Name", "Last Name", "Job Title", "Company", "Industry", "Country",
    "Email Address", "Phone", "Direct Number", "Buying Timeframe", "Asset Downloaded", "LinkedIn",
]
LEAD_KEY = ["Email Address", "Delivery Date", "Asset Downloaded"]


def merge_new_files(new_paths):
    frames = []
    if MASTER_PATH.exists():
        frames.append(pd.read_csv(MASTER_PATH, dtype=str, keep_default_na=False))
    before_rows = sum(len(f) for f in frames)

    for p in new_paths:
        df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        frames.append(df)

    if not frames:
        raise SystemExit("No master_activity.csv yet and no new files given — nothing to build.")

    merged = pd.concat(frames, ignore_index=True)
    before_dedup = len(merged)
    merged = merged.drop_duplicates(subset=["Activity ID"], keep="first")
    after_dedup = len(merged)

    merged.to_csv(MASTER_PATH, index=False)
    added = after_dedup - before_rows
    print(f"master_activity.csv: {before_rows} -> {after_dedup} rows "
          f"({added} new, {before_dedup - after_dedup} duplicate rows discarded this run)")
    return merged


# Raw delivery workbooks (one sheet per delivery, TrustRadius-style headers)
# differ from the master list's schema; map them onto it.
LEAD_ALT_COLUMNS = {
    "Business Card Title": "Job Title",
    "Firm Description": "Industry",
    "asset_downloaded": "Asset Downloaded",
    "What is your buying timeframe?": "Buying Timeframe",
    "LinkedIn Profile": "LinkedIn",
}


def _read_lead_workbook(path):
    xl = pd.ExcelFile(path)
    if "All Leads" in xl.sheet_names:
        return xl.parse("All Leads")
    df = xl.parse(xl.sheet_names[0]).rename(columns=LEAD_ALT_COLUMNS)
    if "Delivery Date" not in df.columns:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", Path(path).name)
        if not m:
            raise SystemExit(f"{Path(path).name}: no Delivery Date column and no YYYY-MM-DD in the filename.")
        df["Delivery Date"] = m.group(1)
    if "Phone" not in df.columns:
        df["Phone"] = ""
    return df


def _clean_lead_frame(df):
    """Normalize one 'All Leads' sheet to LEAD_COLUMNS as clean strings."""
    missing = [c for c in LEAD_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"Lead file is missing expected columns: {missing}")
    df = df[LEAD_COLUMNS].copy()

    dates = pd.to_datetime(df["Delivery Date"], errors="coerce")
    df["Delivery Date"] = dates.dt.strftime("%Y-%m-%d").fillna(df["Delivery Date"].astype(str))

    # Excel stores phone numbers as floats (1.6e10) — render as plain digits.
    def digits(v):
        if pd.isna(v):
            return ""
        if isinstance(v, float):
            return str(int(round(v)))
        return str(v).strip()

    df["Phone"] = df["Phone"].map(digits)
    for col in LEAD_COLUMNS:
        if col != "Phone":
            df[col] = df[col].map(lambda v: "" if pd.isna(v) else str(v).strip())
    df["Email Address"] = df["Email Address"].str.lower()
    return df


def merge_new_leads(xlsx_paths):
    """Merge lead workbooks ('All Leads' sheet) into master_leads.csv.

    Later rows win on key collisions: a re-delivered master list is
    authoritative over what was stored earlier (e.g. corrected phone numbers).
    """
    frames = []
    if LEADS_MASTER_PATH.exists():
        frames.append(pd.read_csv(LEADS_MASTER_PATH, dtype=str, keep_default_na=False))
    before_rows = sum(len(f) for f in frames)

    for p in xlsx_paths:
        frames.append(_clean_lead_frame(_read_lead_workbook(p)))

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=LEAD_KEY, keep="last")
    merged = merged.sort_values(["Delivery Date", "Company", "Last Name"]).reset_index(drop=True)
    merged.to_csv(LEADS_MASTER_PATH, index=False)
    print(f"master_leads.csv: {before_rows} -> {len(merged)} leads ({len(merged) - before_rows} net new)")
    return merged


def load_master_leads():
    if not LEADS_MASTER_PATH.exists():
        return pd.DataFrame(columns=LEAD_COLUMNS)
    return pd.read_csv(LEADS_MASTER_PATH, dtype=str, keep_default_na=False)


_NAME_NOISE = re.compile(
    r"\b(inc|incorporated|llc|ltd|limited|corp|corporation|co|company|group|holdings|plc|sa|ag|gmbh|the|of|and)\b"
)


def normalize_company(name):
    n = re.sub(r"[^\w\s]", " ", (name or "").lower())
    n = _NAME_NOISE.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def attach_leads(account_records, leads_df):
    """Return ({account index: [lead dicts]}, summary).

    Match on email domain == account domain first (high precision); fall back
    to an exact normalized company-name match only when it is unambiguous.
    Unmatched leads stay in master_leads.csv but aren't shown on any account.
    """
    by_domain, by_name = {}, {}
    for i, a in enumerate(account_records):
        dom = (a.get("domain") or "").lower().strip()
        if dom:
            by_domain.setdefault(dom, []).append(i)
        by_name.setdefault(normalize_company(a["account"]), []).append(i)

    attached, matched, unmatched = {}, 0, 0
    for r in leads_df.to_dict("records"):
        domain = r["Email Address"].split("@")[-1].lower().strip() if "@" in r["Email Address"] else ""
        targets = by_domain.get(domain, [])
        if not targets:
            cands = by_name.get(normalize_company(r["Company"]), [])
            targets = cands if len(cands) == 1 else []
        if not targets:
            unmatched += 1
            continue
        matched += 1
        lead = {
            "date": r["Delivery Date"], "first": r["First Name"], "last": r["Last Name"],
            "title": r["Job Title"], "email": r["Email Address"], "phone": r["Phone"],
            "direct": r["Direct Number"], "timeframe": r["Buying Timeframe"],
            "asset": r["Asset Downloaded"], "linkedin": r["LinkedIn"], "company": r["Company"],
        }
        for t in targets:
            attached.setdefault(t, []).append(lead)

    for lst in attached.values():
        lst.sort(key=lambda l: l["date"], reverse=True)
    return attached, {"total": len(leads_df), "matched": matched, "unmatched": unmatched}


def parse_size_lower_bound(size_str):
    if not size_str:
        return None
    nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", size_str)]
    return min(nums) if nums else None


def parse_revenue(rev_str):
    if not rev_str:
        return None
    try:
        return float(rev_str.replace(",", ""))
    except ValueError:
        return None


REVENUE_BANDS = [
    (100_000_000, "< $100M"),
    (500_000_000, "$100M - $500M"),
    (1_000_000_000, "$500M - $1B"),
    (5_000_000_000, "$1B - $5B"),
    (10_000_000_000, "$5B - $10B"),
    (float("inf"), "$10B+"),
]


def revenue_band(revenue):
    if revenue is None:
        return "Unknown"
    for ceiling, label in REVENUE_BANDS:
        if revenue < ceiling:
            return label
    return "$10B+"


def is_icp(row):
    industry = row["Account Industry"].strip()
    if industry not in config.ICP_INDUSTRIES:
        return False
    size_lb = parse_size_lower_bound(row["Account Size"])
    revenue = parse_revenue(row["Account Annual Revenue"])
    size_ok = size_lb is not None and size_lb >= config.MIN_EMPLOYEES
    revenue_ok = revenue is not None and revenue >= config.MIN_REVENUE
    return size_ok or revenue_ok


def build_vendor_index():
    """vendor -> list of rule dicts, for fast lookup."""
    idx = {}
    for rule in config.RULES:
        idx.setdefault(rule["vendor"], []).append(rule)
    return idx


def classify(vendor, product):
    """Return (bucket, is_own) or (None, False) if not a tracked vendor at all
    (caller decides whether it's 'unmapped' vs 'irrelevant')."""
    rules = VENDOR_INDEX.get(vendor)
    if not rules:
        return None, False
    for rule in rules:
        if rule["products"] is None or product in rule["products"]:
            return rule["bucket"], rule.get("own", False)
    return "UNMAPPED", False


VENDOR_INDEX = build_vendor_index()
TRACKED_VENDORS = set(VENDOR_INDEX.keys())


def explode_objects(df):
    """One row per (activity, object slot) for slots with a non-empty vendor."""
    records = []
    account_cols = [
        "Account Name", "Account Domain", "Account Industry", "Account Size",
        "Account Annual Revenue", "Account Country",
    ]
    for _, row in df.iterrows():
        for slot in OBJECT_SLOTS:
            vendor = row.get(f"Object {slot} Vendor", "").strip()
            if not vendor:
                continue
            product = row.get(f"Object {slot} Name", "").strip()
            category = row.get(f"Object {slot} Category", "").strip()
            records.append({
                **{c: row[c] for c in account_cols},
                "Activity ID": row["Activity ID"],
                "Activity Date": row["Activity Date"],
                "Activity Label": row["Activity Label"].strip(),
                "Vendor": vendor,
                "Product": product,
                "Category": category,
            })
    return pd.DataFrame.from_records(records)


def recency_weight(activity_date, window_start, window_end):
    span = (window_end - window_start).days
    if span <= 0:
        return 1.0
    day_index = (activity_date - window_start).days
    return 0.5 + 0.5 * (day_index / span)


def score_window(exploded, window_start, window_end):
    """Per-account: intent_score, activity_count, unica_activity_count,
    competitor breakdown dict."""
    mask = (exploded["Activity Date"] >= window_start) & (exploded["Activity Date"] <= window_end)
    win = exploded.loc[mask & (exploded["bucket"] != "UNMAPPED") & exploded["bucket"].notna()]

    results = {}
    for account, g in win.groupby("Account Name"):
        score = 0.0
        count = 0
        unica_count = 0
        competitor_counts = {}
        labels_seen = set()
        for _, r in g.iterrows():
            label_weight = config.LABEL_WEIGHTS.get(r["Activity Label"], config.DEFAULT_LABEL_WEIGHT)
            mult = config.OWN_PRODUCT_MULTIPLIER if r["is_own"] else config.COMPETITOR_MULTIPLIER
            rw = recency_weight(r["Activity Date"], window_start, window_end)
            score += label_weight * mult * rw
            count += 1
            labels_seen.add(r["Activity Label"])
            if r["is_own"]:
                unica_count += 1
            else:
                competitor_counts[r["bucket"]] = competitor_counts.get(r["bucket"], 0) + 1
        results[account] = {
            "intent_score": round(score, 1),
            "activity_count": count,
            "unica_activity_count": unica_count,
            "competitor_counts": competitor_counts,
            "activity_labels": sorted(labels_seen),
        }
    return results


def category_counts_window(exploded, window_start, window_end):
    """Per-account category counts across ALL vendors (tracked or not) in the
    window — this is the broader 'who else is this account researching'
    market-landscape view, independent of our named-competitor allow-list."""
    mask = (
        (exploded["Activity Date"] >= window_start) & (exploded["Activity Date"] <= window_end)
        & (exploded["Category"] != "")
    )
    win = exploded.loc[mask]
    results = {}
    for account, g in win.groupby("Account Name"):
        counts = {}
        for cat in g["Category"]:
            counts[cat] = counts.get(cat, 0) + 1
        results[account] = counts
    return results


def main():
    csv_paths = [p for p in sys.argv[1:] if p.lower().endswith(".csv")]
    lead_paths = [p for p in sys.argv[1:] if p.lower().endswith((".xlsx", ".xlsm"))]
    other = [p for p in sys.argv[1:] if p not in csv_paths and p not in lead_paths]
    if other:
        raise SystemExit(f"Unsupported file type (expected .csv activity export or .xlsx lead list): {other}")

    merged = merge_new_files(csv_paths)
    if lead_paths:
        merge_new_leads(lead_paths)
    leads_df = load_master_leads()

    merged["Activity Date"] = pd.to_datetime(merged["Activity Date"], errors="coerce")
    merged = merged.dropna(subset=["Activity Date"])

    window_end = merged["Activity Date"].max()
    window_start = window_end - pd.Timedelta(days=config.ROLLING_WINDOW_DAYS - 1)
    prior_end = window_start - pd.Timedelta(days=1)
    prior_start = prior_end - pd.Timedelta(days=config.ROLLING_WINDOW_DAYS - 1)

    exploded = explode_objects(merged)
    exploded["Activity Date"] = pd.to_datetime(exploded["Activity Date"], errors="coerce")
    exploded = exploded.dropna(subset=["Activity Date"])

    classified = exploded["Vendor"].combine(exploded["Product"], classify)
    exploded["bucket"] = classified.map(lambda t: t[0])
    exploded["is_own"] = classified.map(lambda t: t[1])

    # Unmapped known-vendor products, for visibility (not scored).
    unmapped = (
        exploded.loc[exploded["bucket"] == "UNMAPPED", ["Vendor", "Product", "Activity Label"]]
        .value_counts().reset_index(name="Occurrences")
        .rename(columns={"Activity Label": "Seen With Activity Label"})
    )

    current = score_window(exploded, window_start, window_end)
    prior = score_window(exploded, prior_start, prior_end)
    category_counts = category_counts_window(exploded, window_start, window_end)

    # First/last tracked activity per account, across the entire history in
    # master_activity.csv (not just the current window) — mapped activity only.
    mapped_all = exploded.loc[(exploded["bucket"] != "UNMAPPED") & exploded["bucket"].notna()]
    activity_span = mapped_all.groupby("Account Name")["Activity Date"].agg(["min", "max"])

    # ICP fit per account (dedup account attribute rows from the raw merged df)
    # "last" must mean chronologically latest activity, not last-in-upload-order —
    # backfills are routinely uploaded out of chronological order, and account
    # attributes (industry/size/revenue) can shift between TrustRadius snapshots.
    acct_attrs = (
        merged.sort_values("Activity Date")
        .drop_duplicates(subset=["Account Name"], keep="last")
        .set_index("Account Name")
    )

    rows = []
    for account, cur in current.items():
        if account not in acct_attrs.index:
            continue
        attrs = acct_attrs.loc[account]
        icp_fit = is_icp(attrs)
        prev = prior.get(account)
        prev_score = prev["intent_score"] if prev else 0.0
        if prev is None:
            trend = "New"
        elif prev_score == 0:
            trend = "New" if cur["intent_score"] > 0 else "Flat"
        else:
            delta_pct = round((cur["intent_score"] - prev_score) / prev_score * 100)
            trend = f"{'+' if delta_pct >= 0 else ''}{delta_pct}%"

        competitors_str = "; ".join(
            f"{b} ({c})" for b, c in sorted(cur["competitor_counts"].items(), key=lambda x: -x[1])
        )
        acct_categories = category_counts.get(account, {})
        categories_str = "; ".join(
            f"{c} ({n})" for c, n in sorted(acct_categories.items(), key=lambda x: -x[1])
        )
        revenue_val = parse_revenue(attrs["Account Annual Revenue"])
        span = activity_span.loc[account] if account in activity_span.index else None
        first_activity = span["min"].date().isoformat() if span is not None else None
        last_activity = span["max"].date().isoformat() if span is not None else None
        linkedin_id = attrs.get("Account LinkedIn ID", "")

        record = {
            "First Activity Date": first_activity,
            "Last Activity Date": last_activity,
            "Account Name": account,
            "Account Domain": attrs["Account Domain"],
            "Account Annual Revenue": attrs["Account Annual Revenue"],
            "Account Size": attrs["Account Size"],
            "Account Industry": attrs["Account Industry"],
            "Account LinkedIn ID": linkedin_id,
            "Revenue Band": revenue_band(revenue_val),
            "Country": attrs["Account Country"],
            "Intent Score": cur["intent_score"],
            "Activity Count": cur["activity_count"],
            "Unica Activity Count": cur["unica_activity_count"],
            "Trend vs Prior {}d".format(config.ROLLING_WINDOW_DAYS): trend,
            "Activity Labels": ", ".join(cur["activity_labels"]),
            "Competitors Researched": competitors_str,
            "Categories Researched": categories_str,
        }
        if icp_fit:
            rows.append(record)

    priority = pd.DataFrame(rows).sort_values("Intent Score", ascending=False)

    # Competitor x Industry x Size rollup (current window, ICP accounts only)
    icp_accounts = set(priority["Account Name"])
    roll_mask = (
        (exploded["Activity Date"] >= window_start) & (exploded["Activity Date"] <= window_end)
        & exploded["Account Name"].isin(icp_accounts)
        & (exploded["bucket"] != "UNMAPPED") & exploded["bucket"].notna() & (~exploded["is_own"])
    )
    rollup_df = exploded.loc[roll_mask]
    rollup = (
        rollup_df.groupby(["bucket", "Account Industry", "Account Size"])
        .size().reset_index(name="Activity Count")
        .rename(columns={"bucket": "Competitor", "Account Industry": "Industry", "Account Size": "Size"})
        .sort_values(["Competitor", "Activity Count"], ascending=[True, False])
    )

    run_info = pd.DataFrame([{
        "Master rows (deduped)": len(merged),
        "Window start": window_start.date(),
        "Window end": window_end.date(),
        "Prior window start": prior_start.date(),
        "Prior window end": prior_end.date(),
        "ICP-fit accounts": len(priority),
        "Unmapped product rows": len(unmapped),
    }])

    with pd.ExcelWriter(OUTPUT_PATH, engine="openpyxl") as writer:
        priority.to_excel(writer, sheet_name="Priority Accounts", index=False)
        rollup.to_excel(writer, sheet_name="Competitor x Industry x Size", index=False)
        unmapped.to_excel(writer, sheet_name="Unmapped Products", index=False)
        run_info.to_excel(writer, sheet_name="Run Info", index=False)

    # ---------- Event-level export for the interactive dashboard ----------
    # The dashboard lets the user pick an arbitrary date range client-side,
    # so instead of shipping one precomputed window we ship compact raw
    # events and per-account static attributes; the page recomputes
    # scores/counts/trends itself for whatever range is selected.
    tracked_mask = (exploded["bucket"] != "UNMAPPED") & exploded["bucket"].notna()
    accounts_all_time = sorted(set(exploded.loc[tracked_mask, "Account Name"]))
    account_index = {a: i for i, a in enumerate(accounts_all_time)}

    account_records = []
    for account in accounts_all_time:
        attrs = acct_attrs.loc[account]
        revenue_val = parse_revenue(attrs["Account Annual Revenue"])
        account_records.append({
            "account": account,
            "domain": attrs["Account Domain"],
            "industry": attrs["Account Industry"],
            "size": attrs["Account Size"],
            "revenue": revenue_val,
            "revenueBand": revenue_band(revenue_val),
            "linkedinId": attrs.get("Account LinkedIn ID", ""),
            "country": attrs["Account Country"],
            "icpFit": is_icp(attrs),
        })

    # All activity (any vendor, tracked or not) for these accounts — needed
    # so "researching category" can see the full competitive landscape, not
    # just our named-competitor allow-list. Vendor/product are shipped raw
    # (not pre-classified into buckets) so the dashboard's Settings tab can
    # redefine competitor rules and reclassify entirely client-side.
    event_exploded = exploded[exploded["Account Name"].isin(account_index)]

    vendor_list = sorted(set(event_exploded["Vendor"]))
    vendor_index = {v: i for i, v in enumerate(vendor_list)}
    product_list = sorted(set(event_exploded["Product"]))
    product_index = {p: i for i, p in enumerate(product_list)}
    category_list = sorted({c for c in event_exploded["Category"] if c})
    category_index = {c: i for i, c in enumerate(category_list)}
    label_list = sorted(set(event_exploded["Activity Label"]))
    label_index = {l: i for i, l in enumerate(label_list)}

    events = []
    for _, r in event_exploded.iterrows():
        category = r["Category"]
        events.append([
            account_index[r["Account Name"]],
            r["Activity Date"].date().isoformat(),
            vendor_index[r["Vendor"]],
            product_index[r["Product"]],
            category_index.get(category, -1) if category else -1,
            label_index[r["Activity Label"]],
        ])

    # Default competitor rules, serialized for the client (same shape as
    # config.RULES — the Settings tab edits a copy of this structure).
    attached_leads, leads_summary = attach_leads(account_records, leads_df)

    default_rules = [
        {
            "bucket": rule["bucket"],
            "vendor": rule["vendor"],
            "products": sorted(rule["products"]) if rule["products"] is not None else None,
            "own": rule.get("own", False),
        }
        for rule in config.RULES
    ]

    payload = {
        "dataStart": str(merged["Activity Date"].min().date()),
        "dataEnd": str(merged["Activity Date"].max().date()),
        "icpCriteria": {
            "industries": sorted(config.ICP_INDUSTRIES),
            "minEmployees": config.MIN_EMPLOYEES,
            "minRevenue": config.MIN_REVENUE,
        },
        "labelWeights": config.LABEL_WEIGHTS,
        "defaultLabelWeight": config.DEFAULT_LABEL_WEIGHT,
        "ownMultiplier": config.OWN_PRODUCT_MULTIPLIER,
        "competitorMultiplier": config.COMPETITOR_MULTIPLIER,
        "defaultRules": default_rules,
        "vendorList": vendor_list,
        "productList": product_list,
        "categoryList": category_list,
        "labelList": label_list,
        "accounts": account_records,
        "events": events,
        "leadsIncluded": False,
        "leadsSummary": leads_summary,
    }
    # Lead counts are not PII, so both variants carry them (powers the 'Leads sourced' filter).
    for idx, lead_list in attached_leads.items():
        account_records[idx]["leadCount"] = len(lead_list)
    # Public variant: no individual-level lead data at all.
    JSON_PUBLIC_OUTPUT_PATH.write_text(json.dumps(payload, default=str))

    # Private variant: leads embedded per account (names/emails/phones — PII).
    for idx, lead_list in attached_leads.items():
        account_records[idx] = {**account_records[idx], "leads": lead_list}
    payload["accounts"] = account_records
    payload["leadsIncluded"] = True
    JSON_OUTPUT_PATH.write_text(json.dumps(payload, default=str))

    print(f"Wrote {OUTPUT_PATH.name}: {len(priority)} ICP-fit accounts, "
          f"window {window_start.date()}..{window_end.date()}, "
          f"{len(unmapped)} unmapped product rows to review")
    print(f"Wrote {JSON_OUTPUT_PATH.name}: {len(account_records)} accounts, {len(events)} events, "
          f"data span {payload['dataStart']}..{payload['dataEnd']}")
    print(f"Wrote {JSON_PUBLIC_OUTPUT_PATH.name} (no lead PII). Leads: {leads_summary['total']} total, "
          f"{leads_summary['matched']} linked to a tracked account, {leads_summary['unmatched']} unmatched")


if __name__ == "__main__":
    main()
