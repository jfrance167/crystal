'use strict';
const $ = id => document.getElementById(id);
let token = null;
let status = null;
let receivedAt = 0;
let busy = false;
let polling = false;
const labels = {
  session_started: 'Fresh demo session', creating: 'Rotation started',
  candidate_created: 'Candidate created', candidate_verified: 'Candidate verified',
  consumer_switched: 'Consumer switched', consumer_verified: 'Consumer verified',
  old_revoked: 'Old key revoked', rotation_finished: 'Rotation finished',
  rotation_failed: 'Rotation failed', schedule_updated: 'Schedule updated',
  attempt_interrupted: 'Previous attempt interrupted'
};
function message(text, error = false) {
  $('message').textContent = text;
  $('message').className = error ? 'message error' : 'message';
}
async function api(path, body) {
  const headers = {'X-Crystal-Client': 'demo'};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['X-Crystal-Token'] = token;
    headers['Idempotency-Key'] = crypto.randomUUID();
  }
  const response = await fetch(path, {headers, method: body === undefined ? 'GET' : 'POST',
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(5000)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.reason || 'The operation is unavailable.');
  return data;
}
function controls() {
  $('rotate').disabled = busy || !token || !status || status.needs_attention;
  $('save-schedule').disabled = busy || !token || !status;
  $('toggle-schedule').disabled = busy || !token || !status;
}
function countdown() {
  if (!status) return;
  let text = 'Paused';
  if (status.needs_attention) text = 'On hold';
  else if (status.schedule.enabled) {
    const remaining = Math.max(0, status.schedule.due_in - Math.floor((Date.now() - receivedAt) / 1000));
    const minutes = Math.floor(remaining / 60);
    text = `${String(minutes).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
  }
  $('countdown').replaceChildren(document.createTextNode(text));
  const label = document.createElement('span');
  label.textContent = status.needs_attention ? 'RESTART DEMO TO CLEAR' : 'UNTIL NEXT ROTATION';
  $('countdown').append(label);
}
function renderStatus(data) {
  status = data;
  receivedAt = Date.now();
  $('fingerprint').textContent = data.fingerprint;
  $('health').textContent = data.needs_attention ? 'CLEANUP REQUIRED' : data.consumer_healthy ? 'HEALTHY' : 'UNAVAILABLE';
  $('health').className = data.needs_attention || !data.consumer_healthy ? 'badge attention' : 'badge';
  $('schedule-state').textContent = data.needs_attention ? 'ON HOLD' : data.schedule.enabled ? 'RUNNING' : 'PAUSED';
  $('toggle-schedule').textContent = data.schedule.enabled ? 'Pause schedule' : 'Resume schedule';
  $('success-count').textContent = data.successful_rotations;
  $('active-count').textContent = data.active_keys;
  const result = data.last_result;
  $('revocation-proof').textContent = !result ? '—' : result.old_key_rejected ? 'Rejected' : 'Accepted';
  $('last-rotation').textContent = !result ? 'Ready for its first rotation.' : result.outcome === 'success' ? 'New key verified. Old key revoked.' : result.outcome === 'cleanup_required' ? 'Consumer works. Cleanup needs attention.' : 'Check failed. Working key preserved.';
  if (document.activeElement !== $('interval')) {
    if (![...$('interval').options].some(option => Number(option.value) === data.schedule.interval)) {
      const option = document.createElement('option');
      option.value = data.schedule.interval;
      option.textContent = `${data.schedule.interval} seconds`;
      $('interval').append(option);
    }
    $('interval').value = String(data.schedule.interval);
  }
  controls(); countdown();
}
function renderAudit(events) {
  const fragment = document.createDocumentFragment();
  for (const event of events) {
    const row = document.createElement('tr');
    const time = document.createElement('td');
    time.textContent = new Date(event.timestamp).toLocaleTimeString();
    time.title = new Date(event.timestamp).toLocaleString();
    const name = document.createElement('td');
    name.textContent = labels[event.event] || event.event;
    const outcome = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = `outcome ${event.outcome}`;
    badge.textContent = event.outcome.replaceAll('_', ' ');
    outcome.append(badge);
    const fp = document.createElement('td'); fp.textContent = event.fingerprint || '—';
    row.append(time, name, outcome, fp); fragment.append(row);
  }
  $('audit-body').replaceChildren(fragment);
}
async function refresh() {
  if (polling) return;
  polling = true;
  try {
    const [data, events] = await Promise.all([api('/api/status'), api('/api/audit')]);
    renderStatus(data); renderAudit(events);
  } finally { polling = false; }
}
async function act(action) {
  if (busy) return;
  busy = true; controls();
  try { await action(); await refresh(); }
  catch (error) { message(error.message || 'The local demo is unavailable.', true); }
  finally { busy = false; controls(); }
}
$('rotate').addEventListener('click', () => act(async () => {
  message('Creating and checking a replacement…');
  const result = await api('/api/rotate', {failure: $('failure').value});
  $('failure').value = 'none';
  message(result.outcome === 'success' ? 'Rotation complete. The new key works and the old key is rejected.' :
    result.outcome === 'cleanup_required' ? 'Cleanup failed. Inspect the trail, then restart the disposable demo.' :
    'Verification failed. The working key was preserved.', result.outcome !== 'success');
}));
$('schedule-form').addEventListener('submit', event => {
  event.preventDefault();
  act(async () => { await api('/api/schedule', {interval: Number($('interval').value), enabled: status.schedule.enabled}); message('Rotation interval updated.'); });
});
$('toggle-schedule').addEventListener('click', () => act(async () => {
  const enabled = !status.schedule.enabled;
  await api('/api/schedule', {interval: status.schedule.interval, enabled});
  message(enabled ? 'Foreground schedule resumed.' : 'Schedule paused. Manual rotation is available.');
}));
$('refresh').addEventListener('click', () => act(async () => { message('Audit trail refreshed.'); }));
async function connect() {
  try {
    token = (await api('/api/session')).token;
    await refresh();
    message('Local demo ready. Rotate a key or let the schedule run.');
  } catch (_) { token = null; controls(); message('Cannot reach the local demo. Start the service, then reload this page.', true); }
}
connect();
setInterval(countdown, 1000);
setInterval(async () => {
  if (busy || document.hidden) return;
  try {
    const reconnecting = !token;
    // Refresh authorization after a full server restart; never display it.
    const latest = await api('/api/session'); token = latest.token;
    await refresh();
    if (reconnecting) message('Reconnected to the local demo.');
  } catch (_) { token = null; controls(); message('Connection lost. Start the local demo to reconnect.', true); }
}, 3000);
