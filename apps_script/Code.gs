/**
 * TVS LDR - Access + Data service (single Google Apps Script web app)
 *
 * Replaces the two legacy Apps Scripts that were owned by a departed employee:
 *   1. ACCESS  - who may open the dashboard (checkRole / requestAccess / admin panel actions)
 *   2. DATA    - read-only paged access to the Google Sheets the daily pipeline needs
 *
 * Deploy: Deploy > New deployment > Web app > Execute as: Me > Who has access: Anyone.
 * Re-deploy updates via Deploy > Manage deployments > Edit > Version: New version (the /exec URL is kept).
 *
 * Script Properties (Project Settings > Script Properties):
 *   TVS_PUSH_SECRET   shared secret the pipeline sends (?secret=...). Required for DATA actions.
 *   U:<email>         one record per non-admin user (managed by this script; editable by hand).
 *
 * Roles: admin (only INITIAL_ADMINS below) > full > viewer.  pending / none / restricted are states, not roles.
 * SECURITY NOTE: admin actions are authorised by the supplied admin e-mail only (as in the legacy design;
 * Apps Script cannot verify the caller). To limit the blast radius, admin rights can NEVER be granted or
 * removed through the web interface - only by editing INITIAL_ADMINS here - so a spoofed call can approve
 * or remove viewer/full users but cannot create admins.
 */

var ALLOWED_DOMAINS = ['girnarsoft.com', 'girnarcare.com'];
var INITIAL_ADMINS  = ['aditya.kumar@girnarsoft.com'];     // edit here to add/remove admins, then redeploy a new version
var RETAILS_FILE_ID = '1ZWBlzxX-g2R5iCcrsGUWrqSvxIHcchFHtajDDPcFJgE';
var RETAILS_TAB     = 'Raw';
var USER_PREFIX     = 'U:';

// ------------------------- entry points -------------------------
function doGet(e)  { return _out(_route((e && e.parameter) || {}, null)); }

function doPost(e) {
  var params = Object.assign({}, (e && e.parameter) || {});
  var body = null;
  try { body = JSON.parse((e && e.postData && e.postData.contents) || 'null'); } catch (err) { body = null; }
  // Legacy pipeline pushed the finished payload here to be mirrored to Firebase. The dashboard never read
  // Firebase, so the payload is simply acknowledged (not stored).
  if (body && body.gz !== undefined && params.action === undefined) {
    if (!_secretOk(params.secret)) return _out({ error: 'unauthorized' });
    return _out({ ok: true, t: new Date().toISOString() });
  }
  if (body && typeof body === 'object') Object.keys(body).forEach(function (k) { if (params[k] === undefined) params[k] = body[k]; });
  return _out(_route(params, body));
}

function _out(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function _route(p, body) {
  try {
    switch (p.action) {
      // -- DATA (pipeline) --
      case 'ping':             return _secretOk(p.secret) ? { ok: true, t: new Date().toISOString() } : { error: 'unauthorized' };
      case 'getSheetData':     return _secretOk(p.secret) ? getSheetData_(p) : { error: 'unauthorized' };
      case 'getCurrentRetails':return _secretOk(p.secret) ? getSheetData_({ fileId: RETAILS_FILE_ID, tabName: RETAILS_TAB, page: p.page, pageSize: p.pageSize }) : { error: 'unauthorized' };
      // -- ACCESS (dashboard) --
      case 'checkRole':        return { role: roleOf_(p.email) };
      case 'requestAccess':    return requestAccess_(p.email, p.name);
      case 'getPending':       return isAdmin_(p.email) ? { pending: listPending_() } : { error: 'forbidden' };
      case 'getUsers':         return isAdmin_(p.email) ? { users: listUsers_() } : { error: 'forbidden' };
      case 'reviewRequest':    return isAdmin_(p.adminEmail) ? reviewRequest_(p.adminEmail, p.targetEmail, p.decision, p.role) : { error: 'forbidden' };
      case 'updateRole':       return isAdmin_(p.adminEmail) ? updateRole_(p.targetEmail, p.role) : { error: 'forbidden' };
      default:                 return { error: 'unknown action' };
    }
  } catch (err) {
    return { error: String(err && err.message ? err.message : err) };
  }
}

function _secretOk(s) {
  var want = PropertiesService.getScriptProperties().getProperty('TVS_PUSH_SECRET');
  return !!want && String(s || '') === want;
}

// ------------------------- DATA: paged sheet reader -------------------------
// Returns { headers:[...], rows:[[...]], total:<data rows>, done:<bool> }. Cell values are the *displayed*
// text (so dates/months look exactly as in the sheet). Blank trailing rows are included, as before.
function getSheetData_(p) {
  var ss = SpreadsheetApp.openById(String(p.fileId));
  var sh = p.tabName ? ss.getSheetByName(String(p.tabName)) : ss.getSheets()[0];
  if (!sh) return { error: 'tab not found: ' + p.tabName };
  var lastRow = sh.getLastRow(), lastCol = sh.getLastColumn();
  if (lastRow < 1 || lastCol < 1) return { headers: [], rows: [], total: 0, done: true };
  var pageSize = Math.max(1, Math.min(10000, parseInt(p.pageSize, 10) || 2000));
  var page = Math.max(0, parseInt(p.page, 10) || 0);
  var total = Math.max(0, lastRow - 1);
  var headersAll = sh.getRange(1, 1, 1, lastCol).getDisplayValues()[0].map(function (h) { return String(h).trim(); });
  var idx = pickColumns_(headersAll, p.cols);
  var start = page * pageSize, n = Math.max(0, Math.min(pageSize, total - start));
  var rows = [];
  if (n > 0) {
    var block = sh.getRange(2 + start, 1, n, lastCol).getDisplayValues();
    rows = block.map(function (r) { return idx.map(function (i) { return r[i]; }); });
  }
  return { headers: idx.map(function (i) { return headersAll[i]; }), rows: rows, total: total, done: start + n >= total };
}

function pickColumns_(headersAll, cols) {
  if (!cols) return headersAll.map(function (_, i) { return i; });
  var out = [];
  String(cols).split(',').forEach(function (c) {
    var name = c.trim(); if (!name) return;
    var i = headersAll.indexOf(name);
    if (i < 0) i = headersAll.map(function (h) { return h.toLowerCase(); }).indexOf(name.toLowerCase());
    if (i >= 0 && out.indexOf(i) < 0) out.push(i);
  });
  return out;
}

// ------------------------- ACCESS -------------------------
function normEmail_(e) { return String(e || '').trim().toLowerCase(); }
function domainOk_(email) { var d = email.split('@')[1] || ''; return ALLOWED_DOMAINS.indexOf(d) >= 0; }
function isAdmin_(email) { email = normEmail_(email); return !!email && INITIAL_ADMINS.map(normEmail_).indexOf(email) >= 0; }

function getUser_(email) {
  var raw = PropertiesService.getScriptProperties().getProperty(USER_PREFIX + email);
  if (!raw) return null;
  try { return JSON.parse(raw); } catch (e) { return null; }
}
function putUser_(email, rec) { PropertiesService.getScriptProperties().setProperty(USER_PREFIX + email, JSON.stringify(rec)); }
function delUser_(email) { PropertiesService.getScriptProperties().deleteProperty(USER_PREFIX + email); }

function roleOf_(email) {
  email = normEmail_(email);
  if (!email || !domainOk_(email)) return 'restricted';
  if (isAdmin_(email)) return 'admin';
  var u = getUser_(email);
  if (!u) return 'none';
  if (u.status === 'approved' && (u.role === 'full' || u.role === 'viewer')) return u.role;
  if (u.status === 'pending') return 'pending';
  return 'restricted';                     // rejected or malformed
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try { return fn(); } finally { lock.releaseLock(); }
}

function requestAccess_(email, name) {
  email = normEmail_(email);
  if (!email || !domainOk_(email)) return { error: 'Only ' + ALLOWED_DOMAINS.map(function (d) { return '@' + d; }).join(' / ') + ' e-mail addresses can request access.' };
  var current = roleOf_(email);
  if (current === 'admin' || current === 'full' || current === 'viewer' || current === 'pending') return { role: current };
  return withLock_(function () {
    putUser_(email, { status: 'pending', name: String(name || '').trim().slice(0, 80), requestedAt: new Date().toISOString() });
    return { ok: true };
  });
}

function listPending_() {
  var out = {}, props = PropertiesService.getScriptProperties().getProperties();
  Object.keys(props).forEach(function (k) {
    if (k.indexOf(USER_PREFIX) !== 0) return;
    var u; try { u = JSON.parse(props[k]); } catch (e) { return; }
    if (u && u.status === 'pending') out[k.slice(USER_PREFIX.length)] = { name: u.name || '', requestedAt: u.requestedAt || null };
  });
  return out;
}

function listUsers_() {
  var out = {}, props = PropertiesService.getScriptProperties().getProperties();
  INITIAL_ADMINS.forEach(function (a) { out[normEmail_(a)] = { role: 'admin', name: '' }; });
  Object.keys(props).forEach(function (k) {
    if (k.indexOf(USER_PREFIX) !== 0) return;
    var u; try { u = JSON.parse(props[k]); } catch (e) { return; }
    var em = k.slice(USER_PREFIX.length);
    if (u && u.status === 'approved' && !out[em]) out[em] = { role: u.role, name: u.name || '', approvedAt: u.approvedAt || null };
  });
  return out;
}

function reviewRequest_(adminEmail, targetEmail, decision, role) {
  targetEmail = normEmail_(targetEmail);
  return withLock_(function () {
    var u = getUser_(targetEmail);
    if (!u || u.status !== 'pending') return { error: 'No pending request for ' + targetEmail };
    if (decision === 'approve') {
      var r = (role === 'full') ? 'full' : 'viewer';          // admin can never be granted here
      putUser_(targetEmail, Object.assign({}, u, { status: 'approved', role: r, approvedBy: normEmail_(adminEmail), approvedAt: new Date().toISOString() }));
    } else {
      putUser_(targetEmail, Object.assign({}, u, { status: 'rejected', reviewedBy: normEmail_(adminEmail), reviewedAt: new Date().toISOString() }));
    }
    return { ok: true };
  });
}

function updateRole_(targetEmail, role) {
  targetEmail = normEmail_(targetEmail);
  if (isAdmin_(targetEmail)) return { error: 'Admins can only be changed in the script (INITIAL_ADMINS).' };
  return withLock_(function () {
    var u = getUser_(targetEmail);
    if (role === 'remove') { delUser_(targetEmail); return { ok: true }; }
    if (role !== 'full' && role !== 'viewer') return { error: 'Role must be full, viewer or remove.' };
    if (!u) return { error: 'Unknown user ' + targetEmail };
    putUser_(targetEmail, Object.assign({}, u, { status: 'approved', role: role }));
    return { ok: true };
  });
}

// ------------------------- admin helpers (run from the script editor) -------------------------
/** Bulk-add existing users: edit the lists, select this function, press Run. */
function addUsersManually() {
  var FULL   = [];   // e.g. ['name@girnarsoft.com']
  var VIEWER = [];
  FULL.forEach(function (e) { putUser_(normEmail_(e), { status: 'approved', role: 'full', name: '', approvedAt: new Date().toISOString() }); });
  VIEWER.forEach(function (e) { putUser_(normEmail_(e), { status: 'approved', role: 'viewer', name: '', approvedAt: new Date().toISOString() }); });
  Logger.log('added: ' + FULL.length + ' full, ' + VIEWER.length + ' viewer');
}
