"""One-off read-only diagnostic: how much retail attribution lives in the lead dumps vs the
retail sheet?  Prints AGGREGATE counts only (no lead ids).  Run via the diag-retail workflow."""
import os, sys, time, json, collections, requests
import pandas as pd

URL = "https://script.google.com/macros/s/AKfycbwdTKif3l3gYJKMwZBO6PjmYgNbWulkQ9TMEIsN-6xMdG2efbndnSoHE4tC63Oe6AKmlQ/exec"
SECRET = os.environ.get("TVS_PUSH_SECRET", "")
SHEETS = {
    "Jul'26 (static)":  "1gaRoPLebv7jaBgWEET-XSQuhqE_XgQlGru39TA-FoSo",
    "T-2 (Aug)":        "19n028-nRZqPo5wcv9pN737GvUlrcrUusXv9-F9lJsLo",
    "T-1 (Sep)":        "1Wp26qCv3d6oEq1h2wGamlHmCb9YuYrNDa8x8i653W3M",
    "Current (Oct)":    "1iSw5zXF67q5Wkoz2mSPFqql9OPAcqmd0um5BEHUGf4o",
}
COLS = "opty_id,Lead_Month,Medium,Retail By,DMS_Retail_Month,Ops_Retail_Month,Billing Month,Retail Date"


def get(action, extra, timeout=90):
    last = None
    for attempt in range(8):
        try:
            p = {"action": action, "secret": SECRET, **extra}
            r = requests.get(URL, params=p, timeout=timeout)
            r.raise_for_status()
            d = r.json()
            if "error" in d:
                raise RuntimeError(d["error"])
            return d
        except Exception as e:
            last = e
            time.sleep(min(5 * (attempt + 1), 30))
    raise last


def to_id(v):
    s = str(v if v is not None else "").strip()
    return s[:-2] if s.endswith(".0") else s


def fetch_all(action, extra, page_size):
    rows, headers, page = [], None, 0
    while True:
        d = get(action, {**extra, "page": page, "pageSize": page_size})
        headers = headers or d["headers"]
        rows.extend(d.get("rows", []))
        if d.get("done", True):
            break
        page += 1
    return pd.DataFrame(rows, columns=headers)


import threading
results, errors = {}, {}

def job(key, fn):
    try:
        results[key] = fn()
    except Exception as e:
        errors[key] = e

jobs = [("__retail__", lambda: fetch_all("getCurrentRetails", {}, 2000))]
for label, fid in SHEETS.items():
    jobs.append((label, (lambda fid=fid: fetch_all("getSheetData", {"fileId": fid, "tabName": "TVS", "cols": COLS}, 3000))))
ths = [threading.Thread(target=job, args=j, daemon=True) for j in jobs]
for t in ths: t.start()
for t in ths: t.join()
if errors:
    print("FETCH ERRORS:", {k: repr(v) for k, v in errors.items()}); sys.exit(1)

print("== retail sheet (getCurrentRetails) ==", flush=True)
ret = results["__retail__"]
print("columns:", list(ret.columns))
ret["lid"] = ret["sourceLeadId"].map(to_id)
ret = ret[ret["lid"] != ""]
ret["pm"] = ret["performanceMonth"].astype(str).str.strip()
print(f"retail data rows (non-blank sourceLeadId): {len(ret):,}   unique lids: {ret['lid'].nunique():,}")
ret["pm_mon"] = ret["pm"].str[:7]
print("performanceMonth (YYYY-MM) distribution:", dict(sorted(collections.Counter(ret["pm_mon"]).items())[-14:]))
if "Call Type" in ret.columns:
    print("Call Type:", dict(collections.Counter(ret["Call Type"].astype(str).str.strip())))
retail_lids = set(ret["lid"])
retail_pm = dict(zip(ret["lid"], ret["pm_mon"]))

for label, fid in SHEETS.items():
    print(f"\n== {label}  ({fid[:8]}…) ==", flush=True)
    df = results[label]
    df["lid"] = df["opty_id"].map(to_id)
    df = df[df["lid"] != ""]
    print(f"lead rows: {len(df):,}   cols: {list(df.columns)}")
    nonempty = {}
    for c in ["Retail By", "DMS_Retail_Month", "Ops_Retail_Month", "Billing Month", "Retail Date"]:
        if c in df.columns:
            v = df[c].astype(str).str.strip()
            ok = v[~v.isin(["", "-", "nan", "None", "0"])]
            nonempty[c] = len(ok)
            top = collections.Counter(ok).most_common(12)
            print(f"  {c:18s} non-empty: {len(ok):>8,}   top: {top}")
    has_dms = df["DMS_Retail_Month"].astype(str).str.strip().replace({"-": "", "nan": ""}).ne("") if "DMS_Retail_Month" in df else pd.Series(False, index=df.index)
    has_ops = df["Ops_Retail_Month"].astype(str).str.strip().replace({"-": "", "nan": ""}).ne("") if "Ops_Retail_Month" in df else pd.Series(False, index=df.index)
    any_r = has_dms | has_ops
    print(f"  leads flagged retail in the dump: DMS={int(has_dms.sum()):,}  OPS={int(has_ops.sum()):,}  either={int(any_r.sum()):,}  both={int((has_dms & has_ops).sum()):,}")
    flagged = set(df.loc[any_r, "lid"])
    in_sheet = flagged & retail_lids
    print(f"  of those, present in the retail sheet: {len(in_sheet):,}   MISSING from retail sheet: {len(flagged - retail_lids):,}")
    # month agreement for those present
    agree = tot = 0
    for _, r in df.loc[any_r & df["lid"].isin(retail_lids)].iterrows():
        tot += 1
    print(f"  dump leads whose lid IS in retail sheet (any marker or not): {int(df['lid'].isin(retail_lids).sum()):,}")
    print("  Lead_Month of MISSING-retail leads:", dict(collections.Counter(df.loc[any_r & ~df['lid'].isin(retail_lids), 'Lead_Month'].astype(str).str.strip()).most_common(6)))
    print("  DMS_Retail_Month of MISSING:", dict(collections.Counter(df.loc[has_dms & ~df['lid'].isin(retail_lids), 'DMS_Retail_Month'].astype(str).str.strip()).most_common(8)))
    print("  Ops_Retail_Month of MISSING:", dict(collections.Counter(df.loc[has_ops & ~df['lid'].isin(retail_lids), 'Ops_Retail_Month'].astype(str).str.strip()).most_common(8)))
print("\nDONE")
