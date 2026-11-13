// Starts the "TVS Daily Data Push" GitHub workflow.
//   POST /api/refresh            -> called by the dashboard's Refresh button
//   GET  /api/refresh            -> called by Vercel Cron (Authorization: Bearer CRON_SECRET)
//   GET  /api/refresh?status=1   -> is a rebuild running? (button polls this)
// Env vars (Vercel): GH_DISPATCH_TOKEN (fine-grained PAT, Actions read/write on this repo), CRON_SECRET.
const REPO = 'adityakumar-bikes/TVS-LDR-Dashboard';
const WORKFLOW = 'tvs-data-push.yml';
const GH = 'https://api.github.com/repos/' + REPO;

function gh(path, init) {
  return fetch(GH + path, Object.assign({
    headers: {
      Authorization: 'Bearer ' + process.env.GH_DISPATCH_TOKEN,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'User-Agent': 'tvs-dashboard-refresh',
    },
  }, init));
}

async function activeRun() {
  const r = await gh('/actions/workflows/' + WORKFLOW + '/runs?per_page=10');
  if (!r.ok) throw new Error('GitHub runs lookup failed: ' + r.status);
  const j = await r.json();
  return (j.workflow_runs || []).find(x => x.status === 'in_progress' || x.status === 'queued' || x.status === 'waiting' || x.status === 'pending') || null;
}

module.exports = async (req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  if (!process.env.GH_DISPATCH_TOKEN) return res.status(500).json({ ok: false, error: 'GH_DISPATCH_TOKEN is not configured' });
  try {
    const isStatus = req.query && req.query.status;
    if (isStatus) {
      const run = await activeRun();
      return res.status(200).json({ ok: true, running: !!run, since: run ? run.created_at : null });
    }
    const fromCron = req.method === 'GET';
    if (fromCron) {
      const secret = process.env.CRON_SECRET;
      if (!secret || req.headers.authorization !== 'Bearer ' + secret) return res.status(401).json({ ok: false, error: 'unauthorized' });
    } else if (req.method !== 'POST') {
      return res.status(405).json({ ok: false, error: 'method not allowed' });
    }
    const run = await activeRun();
    if (run) return res.status(200).json({ ok: true, started: false, running: true, since: run.created_at, note: 'A rebuild is already in progress' });
    const d = await gh('/actions/workflows/' + WORKFLOW + '/dispatches', {
      method: 'POST',
      body: JSON.stringify({ ref: 'main' }),
    });
    if (d.status !== 204) return res.status(502).json({ ok: false, error: 'GitHub dispatch failed: ' + d.status });
    return res.status(200).json({ ok: true, started: true, running: true, since: new Date().toISOString() });
  } catch (e) {
    return res.status(500).json({ ok: false, error: String(e && e.message || e) });
  }
};
