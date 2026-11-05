"""Vahan gap block: TVS registrations (Vahan) next to our leads and retails, by state and by city group.

Source of the Vahan side: https://bd-contribution-site.vercel.app/tvs (a public page that carries TVS registrations per RTO plus the
RTO -> city and BikeDekho city -> city mapping in one embedded JSON). It is read here, in the pipeline, so the dashboard never
calls it. Our side comes from the tables the pipeline has just built:
  leads   = leads created in the month (same for On Create / On Update)
  retails = On Update basis (retail counted in the month it was retailed), the basis Vahan registrations are on.

Splits: fuel (ICE / EV) follows the ENQUIRED model: EV = iQube and Orbiter (the page counts only the iQube as EV, but the Orbiter is
electric and is in Vahan's electric registrations), ICE = every other model. XL100 = XL100 enquiries against Vahan registrations in the
Moped vehicle class (scraped by the page only for the months in its 'xlm').

City groups follow the page's mapping: several RTO names / BikeDekho city names that are one market (e.g. Bangalore + Bengaluru
Urban + Bengaluru South) share one group. Cities of ours that the mapping does not know fall into "Rest of <state>".
"""
import json
import re
import time
from datetime import datetime, date, timedelta

URL = 'https://bd-contribution-site.vercel.app/tvs'
EV_MODELS = ('iqube', 'orbiter')          # enquired models counted as EV
XL_MODEL = 'xl100'
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# our state name -> the name Vahan uses
STATE_ALIAS = {'Orissa': 'Odisha', 'Pondicherry': 'Puducherry', 'Andaman & Nicobar': 'Andaman & Nicobar Island',
               'Andaman and Nicobar Islands': 'Andaman & Nicobar Island', 'Jammu and Kashmir': 'Jammu & Kashmir',
               'NCT of Delhi': 'Delhi', 'Dadra and Nagar Haveli and Daman and Diu': 'UT of DNH and DD'}


def _norm(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())


def fetch(url=URL, tries=3, wait=20):
    """Return the page html; raises after the last failed try."""
    import requests
    err = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=60, headers={'User-Agent': 'tvs-ldr-pipeline'})
            if r.status_code == 200 and 'id="data"' in r.text:
                return r.text
            err = f'HTTP {r.status_code}'
        except Exception as e:      # network problem: try again
            err = str(e)
        time.sleep(wait)
    raise RuntimeError(f'Vahan page not readable: {err}')


def _d(s):
    return datetime.strptime(s.strip(), '%d %b %Y').date()


def parse(html):
    """-> (embedded json, from date, through date, updated date)"""
    m = re.search(r'<script[^>]*id="data"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError('Vahan page has no data block')
    V = json.loads(m.group(1))
    h = re.search(r'Data\s+(\d{1,2} \w{3} \d{4})\s*[–\-]\s*(\d{1,2} \w{3} \d{4}).{0,12}?Last updated\s+(\d{1,2} \w{3} \d{4})', html, re.S)
    if not h:
        raise RuntimeError('Vahan page header (data range / last updated) not found')
    return V, _d(h.group(1)), _d(h.group(2)), _d(h.group(3))


def _vahan_groups(V):
    """Replicates the page's rule: an RTO whose city has no BikeDekho city in the same state rolls into "Rest of <state>".
    Returns (groups {(state, group): [[ICE per month], [EV per month], [Moped per month]]}, bd map {(state, norm bd city): group})."""
    linked = {(b[0], b[2]) for b in V['bdc'] if not str(b[2]).startswith('Rest of ')}
    nm = len(V['months'])
    g = {}
    eff = []
    for r in V['rtos']:
        c = r[3]
        eff.append(c if (r[0], c) in linked else 'Rest of ' + r[0])

    def cell(k):
        return g.setdefault(k, [[0] * nm, [0] * nm, [0] * nm])
    for i, mo, f, n in V['rv']:
        seg = 0 if V['fuels'][f] == 'ICE' else 1
        cell((V['rtos'][i][0], eff[i]))[seg][mo] += n
    for i, mo, n in (V.get('xv') or []):
        cell((V['rtos'][i][0], eff[i]))[2][mo] += n
    bd = {}
    for b in V['bdc']:
        bd[(b[0], _norm(b[1]))] = b[2]
    return g, bd


def _seg_of_model(maps):
    """model index -> 1 if EV model, 2 if XL100, else 0"""
    out = []
    for n in maps['mdl']:
        t = _norm(n)
        out.append(2 if XL_MODEL in t else 1 if any(e in t for e in EV_MODELS) else 0)
    return out


def build(V, d_from, d_through, d_updated, tables, maps):
    """tables: payload rows (lists) u_cm, u_stm, u_mxst, u_cxsm [+ dd, ddu]; maps: payload maps (lm, st, city, city_state, mdl)."""
    mkeys = list(V['months'])
    nm = len(mkeys)
    labels = [MON[int(k[5:7]) - 1] + "'" + k[2:4] for k in mkeys]
    lm_idx = {l: i for i, l in enumerate(maps['lm'])}
    col = [lm_idx.get(l) for l in labels]            # our month index of each Vahan month
    pos = {c: j for j, c in enumerate(col) if c is not None}
    mseg = _seg_of_model(maps)

    groups, bdmap = _vahan_groups(V)

    def z():
        return [[0] * nm, [0] * nm, [0] * nm]        # ICE, EV, XL100
    vstate = {}
    for (st, grp), regs in groups.items():
        a = vstate.setdefault(st, z())
        for sg in range(3):
            for j in range(nm):
                a[sg][j] += regs[sg][j]

    def our_state(i):
        if i is None or i < 0 or i >= len(maps['st']):
            return None
        s = maps['st'][i]
        return STATE_ALIAS.get(s, s)

    def city_key(ci):
        s = our_state(maps['city_state'][ci]) if ci < len(maps['city_state']) else None
        if s is None:
            return None
        grp = bdmap.get((s, _norm(maps['city'][ci])))
        if grp is None or str(grp).startswith('Rest of '):
            grp = 'Rest of ' + s
        return (s, grp)

    # All models by state (u_stm) and by city (u_cm), then the EV and XL100 enquiries (u_mxst / u_cxsm); ICE = all - EV.
    tot_all = [[0] * nm, [0] * nm]          # leads, retails
    st_all, ct_all = {}, {}
    for r in tables['u_stm']:                # [st, src, lm, L, R, Rd, Rc]
        j = pos.get(r[2])
        if j is None:
            continue
        a = st_all.setdefault(our_state(r[0]), [[0] * nm, [0] * nm])
        tot_all[0][j] += r[3]
        tot_all[1][j] += r[4]
        a[0][j] += r[3]
        a[1][j] += r[4]
    unmapped = [0, 0]
    for r in tables['u_cm']:                 # [city, lm, L, R, Rd, Rc]
        j = pos.get(r[1])
        if j is None:
            continue
        k = city_key(r[0])
        if k is None:
            unmapped[0] += r[2]
            unmapped[1] += r[3]
            continue
        a = ct_all.setdefault(k, [[0] * nm, [0] * nm])
        a[0][j] += r[2]
        a[1][j] += r[3]

    # per segment (EV, XL100): leads, retails
    tot_seg = [[[0] * nm, [0] * nm] for _ in range(3)]
    st_seg, ct_seg = {}, {}
    for r in tables['u_mxst']:               # [mdl, st, lm, L, R, Rd, Rc]
        sg = mseg[r[0]] if r[0] < len(mseg) else 0
        j = pos.get(r[2])
        if sg == 0 or j is None:
            continue
        a = st_seg.setdefault((our_state(r[1]), sg), [[0] * nm, [0] * nm])
        a[0][j] += r[3]
        a[1][j] += r[4]
        tot_seg[sg][0][j] += r[3]
        tot_seg[sg][1][j] += r[4]
    for r in tables['u_cxsm']:               # [city, src, mdl, lm, L, R, Rd, Rc]
        sg = mseg[r[2]] if r[2] < len(mseg) else 0
        j = pos.get(r[3])
        if sg == 0 or j is None:
            continue
        k = city_key(r[0])
        if k is None:
            continue
        a = ct_seg.setdefault((k, sg), [[0] * nm, [0] * nm])
        a[0][j] += r[4]
        a[1][j] += r[5]

    zero = [0] * nm

    def lr(allv, segd, key):
        """-> (L3, R3): [ICE, EV, XL100] arrays. XL100 is inside ICE."""
        ev = segd.get((key, 1)) or [zero, zero]
        xl = segd.get((key, 2)) or [zero, zero]
        al = allv.get(key) or [zero, zero]
        res = []
        for q in (0, 1):
            ice = [max(al[q][j] - ev[q][j], 0) for j in range(nm)]
            res.append([ice, list(ev[q]), list(xl[q])])
        return res[0], res[1]

    tot = {'V': [[sum(v[sg][j] for v in vstate.values()) for j in range(nm)] for sg in range(3)]}
    ev_L, ev_R = tot_seg[1][0], tot_seg[1][1]
    tot['L'] = [[tot_all[0][j] - ev_L[j] for j in range(nm)], list(ev_L), list(tot_seg[2][0])]
    tot['R'] = [[tot_all[1][j] - ev_R[j] for j in range(nm)], list(ev_R), list(tot_seg[2][1])]

    st_rows = []
    for s in sorted(set(vstate) | {k for k in st_all if k}):
        L3, R3 = lr(st_all, st_seg, s)
        st_rows.append([s, vstate.get(s, z()), L3, R3])
    ct_rows = []
    for k in sorted(set(groups) | set(ct_all)):
        L3, R3 = lr(ct_all, ct_seg, k)
        ct_rows.append([k[0], k[1], groups.get(k, z()), L3, R3])

    # a month the Vahan data covers only in part: pro-rate our side to the same cut-off date (all models)
    cap = {}
    last = d_through
    nxt = (last.replace(day=28) + timedelta(days=4)).replace(day=1)
    if (nxt - timedelta(days=1)) != last and tables.get('dd') is not None:
        mk = f'{last.year}-{last.month:02d}'
        ym, cut = last.year * 100 + last.month, last.year * 10000 + last.month * 100 + last.day
        fl = [0, 0]
        fr = [0, 0]
        for r in tables['dd']:
            if r[0] // 100 == ym:
                fl[1] += r[5]
                if r[0] <= cut:
                    fl[0] += r[5]
        for r in (tables.get('ddu') or []):
            if r[0] // 100 == ym:
                fr[1] += r[5]
                if r[0] <= cut:
                    fr[0] += r[5]
        if fl[1] and fr[1]:
            cap[mk] = [round(fl[0] / fl[1], 6), round(fr[0] / fr[1], 6)]

    return {
        'src': URL, 'from': d_from.isoformat(), 'through': d_through.isoformat(), 'updated': d_updated.isoformat(),
        'mkeys': mkeys, 'months': labels, 'cap': cap,
        'xlm': dict(V.get('xlm') or {}),       # months with Moped-class registrations, e.g. {"2026-10": "1-27 Oct"}
        'segs': ['ICE', 'EV', 'XL100'],
        'tot': tot, 'unmapped': unmapped,
        'st': st_rows, 'ct': ct_rows,
    }
