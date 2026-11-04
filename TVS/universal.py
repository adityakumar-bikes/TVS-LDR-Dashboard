"""Full-cross table: State x City x Dealer x Source x Model x Lead Type x Month, with Leads / Retails / DMS / Call-Out.

The Pivot picks the smallest pre-built table that holds every field and filter you chose. The older tables each hold only some of
the fields, so some combinations (e.g. City + Model + Lead Type, or Dealer + Source + Model) were not exact. This table holds all of
them together; the Pivot reads it only when no smaller table can answer.

Built from one record per lead (parallel integer arrays) with numpy instead of a Python dict per row, so 2M+ rows stay cheap.
Same counting rules as the other tables: On Create counts the lead and its retail in the lead month; On Update counts the lead in the
lead month and the retail in the month it retailed.
"""
import numpy as np

DIMS = ['st', 'ct', 'dl', 'src', 'mdl', 'lt', 'lm']       # column order of a row; then L, R, R_dms, R_co


def build_rows(rec, update):
    """rec: dict of equal-length integer sequences for DIMS plus 'um' (month of retail) and 'rc'
    (retail class: 0 not retailed, 1 retailed, 2 DMS, 3 Call-Out). Returns [[*dims, L, R, Rd, Rc], ...] sorted by dims."""
    n = len(rec['rc'])
    if n == 0:
        return []
    a = {k: np.asarray(rec[k], dtype=np.int64) for k in DIMS + ['um', 'rc']}
    rad = [int(a[k].max()) + 1 for k in DIMS]

    def comb(month):
        key = np.zeros(n, dtype=np.int64)
        for k, r in zip(DIMS, rad):
            key = key * r + (month if k == 'lm' else a[k])
        return key

    rc = a['rc']
    ret = rc > 0
    if not update:
        keys = comb(a['lm'])
        vals = [np.ones(n), ret, rc == 2, rc == 3]
    else:
        nr = int(ret.sum())
        keys = np.concatenate([comb(a['lm']), comb(a['um'])[ret]])
        zeros = np.zeros(n)
        vals = [np.concatenate([np.ones(n), np.zeros(nr)]),
                np.concatenate([zeros, np.ones(nr)]),
                np.concatenate([zeros, (rc == 2)[ret]]),
                np.concatenate([zeros, (rc == 3)[ret]])]
    uniq, inv = np.unique(keys, return_inverse=True)
    sums = [np.bincount(inv, weights=v.astype(np.float64), minlength=len(uniq)).astype(np.int64) for v in vals]
    cols, rest = [], uniq.copy()
    for r in reversed(rad):
        cols.append(rest % r)
        rest //= r
    cols.reverse()
    return np.column_stack(cols + sums).tolist()
