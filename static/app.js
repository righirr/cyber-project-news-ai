'use strict';
// Cyber Security News Powered by AI — browser client.
// All article content is untrusted: it is only ever inserted with textContent / DOM nodes.

const $ = (sel, root = document) => root.querySelector(sel);

const TOPICS = [
  ['AI Security', 'ai'], ['Vulnerability', 'vuln'], ['Threat Intel', 'threat'],
  ['Supply Chain', 'supply'], ['SecOps', 'secops'],
];
const TOPIC_CLASS = Object.fromEntries(TOPICS);
const RANGES = [['24h', '24 hours'], ['7d', '7 days'], ['15d', '15 days'], ['30d', '30 days'], ['all', 'All collected']];
const KIND_LABEL = { ai: 'AI summary', feed: 'Feed abstract', page: 'Page excerpt', headline: 'Headline only' };
const PAGE_SIZE = 48;

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* private mode */ } },
};

const state = {
  q: '', range: '7d', topic: '', sources: new Set(), sort: '',
  offset: 0, total: 0, facets: { topics: {}, sources: {} }, articles: [],
  status: null, sourceConfig: [], settings: { lookback_days: 7, default_max_items: 10 },
  newRun: null, newCount: 0,  // set after a refresh: show only the articles it added
};
const previousVisit = Number(storage.get('pulse.lastVisit')) || 0;
let requestSeq = 0;

// ----- helpers --------------------------------------------------------------
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'style') Object.entries(value).forEach(([prop, v]) => node.style.setProperty(prop, v));  // CSSOM is CSP-safe
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key in node && typeof value !== 'string') node[key] = value;
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(path, { method = 'GET', body } = {}) {
  const init = { method, headers: { Accept: 'application/json' }, cache: 'no-store' };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const response = await fetch(path, init);
  let data = {};
  try { data = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new Error(data.error || `Request failed (HTTP ${response.status})`);
  return data;
}

function safeUrl(value) {
  try {
    const url = new URL(value, location.href);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : null;
  } catch { return null; }
}

/** Render server highlights (\u0002…\u0003 markers) as <mark> without using innerHTML. */
function highlighted(text) {
  const fragment = document.createDocumentFragment();
  for (const part of String(text || '').split(/(\u0002[^\u0003]*\u0003)/)) {
    if (part.startsWith('\u0002')) fragment.append(el('mark', {}, part.slice(1, -1)));
    else if (part) fragment.append(part);
  }
  return fragment;
}

function relativeTime(iso) {
  const seconds = (Date.now() - Date.parse(iso)) / 1000;
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  if (seconds < 86400 * 14) return `${Math.round(seconds / 86400)}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
}

const fmt = n => Number(n || 0).toLocaleString();
let toastTimer;
function toast(message, kind = '') {
  const node = $('#toast');
  node.textContent = message;
  node.className = 'toast ' + kind;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 7000 : 4500);
}

// ----- URL state ------------------------------------------------------------
function readUrl() {
  const params = new URLSearchParams(location.search);
  state.q = params.get('q') || '';
  state.range = RANGES.some(([r]) => r === params.get('range')) ? params.get('range') : '7d';
  state.topic = TOPIC_CLASS[params.get('topic')] ? params.get('topic') : '';
  state.sources = new Set(params.getAll('source'));
  state.sort = ['newest', 'relevance'].includes(params.get('sort')) ? params.get('sort') : '';
  $('#search').value = state.q;
  $('#sort').value = state.sort;
}

function writeUrl() {
  const params = new URLSearchParams();
  if (state.q) params.set('q', state.q);
  if (state.range !== '7d') params.set('range', state.range);
  if (state.topic) params.set('topic', state.topic);
  for (const s of state.sources) params.append('source', s);
  if (state.sort) params.set('sort', state.sort);
  const query = params.toString();
  history.replaceState(null, '', query ? `?${query}` : location.pathname);
}

// ----- data loading ---------------------------------------------------------
async function loadStatus() {
  state.status = await api('/api/status');
  const s = state.status;
  $('#stat24h').textContent = fmt(s.articles_24h);
  $('#stat7d').textContent = fmt(s.articles_7d);
  $('#statTotal').textContent = fmt(s.articles_total);
  $('#statSources').textContent = `${s.sources_enabled}/${s.sources_total}`;
  $('#versionLabel').textContent = 'v' + s.version;
  const last = s.last_refresh;
  $('#lastRefresh').textContent = last
    ? `Last refresh${last.scope === 'scheduled' ? ' (automatic)' : ''} ${relativeTime(last.at)} · ${fmt(last.added)} new from ${last.sources.length} source${last.sources.length === 1 ? '' : 's'}`
    : 'Nothing collected yet — use Refresh news to start.';
  renderAutoRefresh(s.auto_refresh);
  const badge = $('#aiBadge');
  badge.classList.toggle('on', s.ai.enabled);
  badge.title = s.ai.enabled ? `Claude enrichment on (${s.ai.model})` : `Claude enrichment off: ${s.ai.reason}`;
  renderStorage(s.storage);
  $('#aboutAi').textContent = s.ai.enabled
    ? `Active — ${s.ai.model} writes article summaries, classifies topics, filters off-topic stories and drafts the daily briefing.`
    : `Inactive (${s.ai.reason}). Install the anthropic package and set ANTHROPIC_API_KEY to enable AI summaries and briefings; keyword classification and publisher abstracts are used meanwhile.`;
}

/** 90112 -> "88.0 KB"; uses 1024-based units up to GB. */
function humanSize(bytes) {
  const units = ['bytes', 'KB', 'MB', 'GB'];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return unit ? `${value.toFixed(value < 10 ? 2 : 1)} ${units[unit]}` : `${value} bytes`;
}

function renderStorage(storage) {
  if (!storage) return;
  const bytes = storage.size_bytes || 0;
  $('#aboutArticles').textContent = fmt(storage.articles);
  $('#aboutSince').textContent = storage.first_collected
    ? new Date(storage.first_collected).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
    : 'nothing yet';
  $('#aboutSize').textContent = humanSize(bytes);
  $('#aboutBytes').textContent = `${fmt(bytes)} bytes · ${(bytes / 1048576).toFixed(2)} MB · ${(bytes / 1073741824).toFixed(4)} GB`;
  $('#aboutLocation').textContent = storage.location;
}

async function loadRefreshLog() {
  try {
    const data = await api('/api/refresh-log?lines=10');
    if (data.file) $('#aboutLogFile').textContent = `data/${data.file}`;
    $('#aboutLog').textContent = data.entries.length
      ? data.entries.join('\n')  // newest first
      : 'No refresh has run yet. The first line appears after the next manual or automatic refresh.';
  } catch (error) {
    $('#aboutLog').textContent = `Could not read the log: ${error.message}`;
  }
}

function describeNextRun(iso) {
  const when = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const time = when.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (when.toDateString() === today.toDateString()) return `today at ${time}`;
  if (when.toDateString() === tomorrow.toDateString()) return `tomorrow at ${time}`;
  return when.toLocaleString(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function renderAutoRefresh(auto) {
  const foot = $('#autoRefreshFoot');
  if (!auto) { foot.hidden = true; return; }
  foot.hidden = false;
  if (!auto.enabled) {
    foot.textContent = 'Automatic daily refresh is off — turn it on in Manage sources.';
    $('#aboutAuto').textContent = 'currently turned off (turn it on in Manage sources)';
    return;
  }
  const lastRun = auto.last_run && auto.last_run.status && auto.last_run.status !== 'running'
    ? ` · last automatic run ${relativeTime(auto.last_run.finished_at || auto.last_run.started_at)}: ${auto.last_run.status === 'done' ? `${fmt(auto.last_run.added)} new` : auto.last_run.status}`
    : '';
  foot.textContent = `Automatic refresh daily at ${auto.time} (${auto.timezone}) · next ${describeNextRun(auto.next_run)}${lastRun}`;
  $('#aboutAuto').textContent = `every day at ${auto.time} (server time, ${auto.timezone}), next ${describeNextRun(auto.next_run)}`;
}

async function loadSources() {
  const data = await api('/api/sources');
  state.sourceConfig = data.sources;
  state.settings = data.settings;
}

async function loadBriefing() {
  const data = await api('/api/briefing');
  const section = $('#briefing');
  if (!data || data.kind === 'empty') { section.hidden = true; return; }
  section.hidden = false;
  const kind = $('#briefingKind');
  kind.textContent = data.kind === 'ai' ? `AI briefing · ${data.model || 'Claude'}` : 'Computed from recent reporting';
  kind.className = 'chip-static' + (data.kind === 'ai' ? ' ai' : '');
  const cards = [['Lead signal', data.lead], ['Watch next', data.watch], ['SOC action', data.action]];
  $('#briefingGrid').replaceChildren(...cards.map(([label, item]) => {
    const url = item.url && safeUrl(item.url);
    return el('article', { class: 'brief-card' },
      el('p', { class: 'eyebrow' }, label),
      el('h3', {}, url ? el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, item.title) : item.title),
      el('p', {}, item.text));
  }));
}

async function loadArticles({ append = false } = {}) {
  const seq = ++requestSeq;
  if (!append) state.offset = 0;
  const params = new URLSearchParams({ range: state.range, limit: PAGE_SIZE, offset: state.offset });
  if (state.q) params.set('q', state.q);
  if (state.topic) params.set('topic', state.topic);
  if (state.sort) params.set('sort', state.sort);
  for (const s of state.sources) params.append('source', s);
  if (state.newRun) params.set('run', state.newRun);
  writeUrl();
  const grid = $('#grid');
  grid.setAttribute('aria-busy', 'true');
  if (!append) NewsChart.loading();  // keep the previous chart, dimmed, while data reloads
  if (!append && !grid.children.length) grid.replaceChildren(...Array.from({ length: 6 }, () => el('div', { class: 'card skeleton' })));
  let data;
  try {
    data = await api('/api/articles?' + params);
  } catch (error) {
    if (seq === requestSeq) { grid.replaceChildren(); showEmpty('error', error.message); }
    return;
  } finally {
    if (seq === requestSeq) grid.setAttribute('aria-busy', 'false');
  }
  if (seq !== requestSeq) return;  // a newer request superseded this one
  state.total = data.total;
  state.facets = data.facets;
  state.articles = append ? state.articles.concat(data.articles) : data.articles;
  renderArticles(data.articles, append);
  if (!append) {
    NewsChart.render({
      histogram: data.histogram, range: state.range, rangeStart: data.range_start, facets: data.facets,
      topic: state.topic, filtered: Boolean(state.q || state.topic || state.sources.size || state.newRun),
      onTopic: topic => setTopic(topic),
    });
  }
  renderFacets();
  renderCloud(data.terms);
  renderActiveFilters();
}

// ----- rendering ------------------------------------------------------------
function card(article, index) {
  const topicClass = TOPIC_CLASS[article.topic] || 'threat';
  const url = safeUrl(article.url);
  const isNew = previousVisit > 0 && Date.parse(article.collected_at) > previousVisit;
  const lead = index === 0 && !state.q && state.offset === 0;
  return el('article', { class: `card t-${topicClass}${lead ? ' lead' : ''}` },
    el('div', { class: 'card-meta' },
      el('span', { class: 'topic-pill' }, article.topic),
      isNew && el('span', { class: 'new-badge', title: 'Collected since your last visit' }, 'NEW'),
      el('time', { datetime: article.published_at, title: new Date(article.published_at).toLocaleString() },
        relativeTime(article.published_at))),
    el('h3', {}, url
      ? el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, highlighted(article.title_hl || article.title))
      : highlighted(article.title)),
    el('p', { class: 'card-summary' }, highlighted(article.summary_hl || article.summary)),
    el('div', { class: 'card-foot' },
      el('button', { type: 'button', class: 'source-link', title: `Show only ${article.source_name}`,
        onclick: () => { state.sources = new Set([article.source_name]); loadArticles(); } }, article.source_name),
      el('span', { class: `kind kind-${article.summary_kind}` }, KIND_LABEL[article.summary_kind] || ''),
      el('span', { class: 'read', 'aria-hidden': 'true' }, 'Read ↗')));
}

function renderArticles(articles, append) {
  const grid = $('#grid');
  const start = append ? state.articles.length - articles.length : 0;
  const nodes = articles.map((a, i) => card(a, start + i));
  if (append) grid.append(...nodes); else grid.replaceChildren(...nodes);
  const shown = state.articles.length;
  $('#resultCount').textContent = state.total
    ? `${fmt(shown)} of ${fmt(state.total)} article${state.total === 1 ? '' : 's'}`
    : '';
  $('#loadMore').hidden = shown >= state.total;
  if (!state.total) showEmpty(state.status && !state.status.articles_total ? 'first-run' : 'no-match');
  else $('#empty').hidden = true;
}

function showEmpty(kind, message) {
  const box = $('#empty');
  box.hidden = false;
  if (kind === 'first-run') {
    box.replaceChildren(
      el('h3', {}, 'No news collected yet'),
      el('p', {}, 'Collect the latest articles from your sources. They are summarised, classified and indexed for search.'),
      el('button', { class: 'btn btn-primary', type: 'button', onclick: () => startCollection('enabled') }, 'Refresh all sources'));
  } else if (kind === 'error') {
    box.replaceChildren(el('h3', {}, 'Could not load articles'), el('p', {}, message));
  } else {
    const widen = state.range !== 'all';
    box.replaceChildren(
      el('h3', {}, 'No articles match'),
      el('p', {}, widen ? 'Nothing matches in this time window. Try a longer range or clear the filters.' : 'Try different words or clear the filters.'),
      el('div', { class: 'empty-actions' },
        widen && el('button', { class: 'btn btn-ghost', type: 'button', onclick: () => setRange('all') }, 'Search all collected news'),
        el('button', { class: 'btn btn-ghost', type: 'button', onclick: clearFilters }, 'Clear filters')));
  }
}

function renderRange() {
  $('#rangeFilter').replaceChildren(...RANGES.map(([value, label]) =>
    el('button', { type: 'button', 'aria-pressed': String(state.range === value), onclick: () => setRange(value) }, label)));
}

function renderFacets() {
  const topicCounts = state.facets.topics || {};
  const all = Object.values(topicCounts).reduce((a, b) => a + b, 0);
  $('#topicFilter').replaceChildren(
    el('button', { type: 'button', class: 'chip', 'aria-pressed': String(!state.topic), onclick: () => setTopic('') },
      'All topics', el('span', { class: 'n' }, fmt(all))),
    ...TOPICS.map(([topic, cls]) => el('button', {
      type: 'button', class: `chip t-${cls}`, 'aria-pressed': String(state.topic === topic),
      onclick: () => setTopic(state.topic === topic ? '' : topic),
    }, topic, el('span', { class: 'n' }, fmt(topicCounts[topic] || 0)))));

  const counts = state.facets.sources || {};
  const names = [...new Set([...state.sourceConfig.map(s => s.name), ...Object.keys(counts)])];
  names.sort((a, b) => (counts[b] || 0) - (counts[a] || 0) || a.localeCompare(b));
  $('#sourceFilter').replaceChildren(...names.map(name => el('li', {},
    el('label', {},
      el('input', { type: 'checkbox', checked: state.sources.has(name), onchange: event => {
        if (event.target.checked) state.sources.add(name); else state.sources.delete(name);
        loadArticles();
      } }),
      el('span', {}, name),
      el('span', { class: 'n' }, fmt(counts[name] || 0))))));
  $('#clearSources').hidden = !state.sources.size;
}

function renderCloud(terms) {
  const cloud = $('#cloud');
  if (!terms || !terms.length) {
    cloud.replaceChildren(el('p', { class: 'cloud-empty' }, 'Trends appear once enough articles match.'));
    return;
  }
  const max = Math.max(...terms.map(t => t.score));
  const min = Math.min(...terms.map(t => t.score));
  const shuffled = [...terms].sort((a, b) => a.term.localeCompare(b.term));
  cloud.replaceChildren(...shuffled.map(({ term, score }) => el('button', {
    type: 'button', style: { '--w': (9 * (score - min) / Math.max(1, max - min)).toFixed(1) },
    title: `Search for “${term}”`, onclick: () => setQuery(term.includes(' ') ? `"${term}"` : term),
  }, term)));
}

function renderActiveFilters() {
  const pills = [];
  const pill = (label, onRemove) => el('span', { class: 'pill' }, label,
    el('button', { type: 'button', 'aria-label': `Remove filter ${label}`, onclick: onRemove }, '×'));
  if (state.newRun) {
    const newPill = pill(`Only the ${fmt(state.newCount)} new from the last refresh`, () => { clearNewOnly(); loadArticles(); });
    newPill.classList.add('new');
    pills.push(newPill);
  }
  if (state.q) pills.push(pill(`Search: ${state.q}`, () => setQuery('')));
  if (state.topic) pills.push(pill(`Topic: ${state.topic}`, () => setTopic('')));
  for (const s of state.sources) pills.push(pill(`Source: ${s}`, () => { state.sources.delete(s); loadArticles(); }));
  if (pills.length > 1) pills.push(el('button', { class: 'link-btn', type: 'button', onclick: clearFilters }, 'Clear all'));
  $('#activeFilters').replaceChildren(...pills);
}

// ----- filter actions -------------------------------------------------------
function setRange(range) { state.range = range; renderRange(); loadArticles(); }
function setTopic(topic) { state.topic = topic; loadArticles(); }
function setQuery(q) { state.q = q; $('#search').value = q; loadArticles(); }
function clearFilters() {
  state.q = ''; state.topic = ''; state.sources.clear(); $('#search').value = '';
  clearNewOnly();
  loadArticles();
}

function clearNewOnly() {
  if (!state.newRun) return;
  state.newRun = null;
  state.newCount = 0;
  state.range = '7d';
  renderRange();
}

// ----- collection -----------------------------------------------------------
function closeMenu() {
  $('#collectOptions').hidden = true;
  $('#collectButton').setAttribute('aria-expanded', 'false');
}

function openMenu() {
  const panel = $('#collectOptions');
  const enabled = state.sourceConfig.filter(s => s.enabled);
  const item = (label, detail, onclick, dot) => el('button', { type: 'button', role: 'menuitem', class: 'menu-item', onclick },
    dot !== undefined && el('span', { class: 'dot' + (dot ? ' on' : '') }), label, detail && el('small', {}, detail));
  panel.replaceChildren(
    item('Refresh all enabled sources', `${enabled.length}`, () => startCollection('enabled')),
    item('Choose several sources…', null, openChooseDialog),
    el('div', { class: 'menu-sep', role: 'separator' }),
    el('div', { class: 'menu-label' }, 'Refresh only one source'),
    ...state.sourceConfig.map(s => item(s.name, s.enabled ? '' : 'disabled', () => startCollection([s.id]), s.enabled)));
  panel.hidden = false;
  $('#collectButton').setAttribute('aria-expanded', 'true');
  panel.querySelector('.menu-item')?.focus();
}

function openChooseDialog() {
  closeMenu();
  const list = $('#chooseList');
  list.replaceChildren(...state.sourceConfig.map(s => el('li', {}, el('label', {},
    el('input', { type: 'checkbox', value: String(s.id), checked: s.enabled, onchange: updateChooseCount }),
    el('span', {}, s.name),
    el('span', { class: 'meta' }, s.enabled ? `${s.max_items} max` : 'disabled')))));
  $('#chooseDays').value = state.settings.lookback_days;
  updateChooseCount();
  $('#chooseDialog').showModal();
}

function chosenIds() {
  return [...$('#chooseList').querySelectorAll('input:checked')].map(i => Number(i.value));
}

function updateChooseCount() {
  const n = chosenIds().length;
  const button = $('#chooseSubmit');
  button.disabled = n === 0;
  button.textContent = n ? `Refresh ${n} source${n === 1 ? '' : 's'}` : 'Select a source';
}

async function startCollection(sourceIds, days) {
  closeMenu();
  const body = { source_ids: sourceIds };
  if (days) body.days = days;
  const button = $('#collectButton');
  button.disabled = true;
  try {
    const job = await api('/api/refresh', { method: 'POST', body });
    renderProgress(job);
    pollJob(job.id);
  } catch (error) {
    button.disabled = false;
    toast(error.message, 'error');
  }
}

function renderProgress(job) {
  const box = $('#progress');
  box.hidden = false;
  const finished = job.sources.filter(s => ['done', 'error', 'fetched'].includes(s.state)).length;
  const phaseText = { collecting: 'Collecting', summarizing: 'Summarising with Claude', saving: 'Indexing',
    briefing: 'Writing briefing', done: 'Refresh complete', failed: 'Refresh failed' }[job.phase] || 'Collecting';
  const label = job.sources.length === 1 ? job.sources[0].name : `${job.sources.length} sources`;
  $('#progressTitle').textContent = `${phaseText} · ${label}`;
  const pct = job.status !== 'running' ? 100 : Math.round(90 * finished / job.sources.length) + (job.phase === 'collecting' ? 0 : 5);
  $('#progressBar').style.width = `${pct}%`;
  $('#progressList').replaceChildren(...job.sources.map(s => {
    let icon, detail;
    if (s.state === 'error') { icon = '✕'; detail = s.error; }
    else if (s.state === 'done') { icon = '✓'; detail = `${s.new} new · ${s.found} in window${s.via === 'google' ? ' · via Google News' : ''}`; }
    else if (s.state === 'fetched') { icon = el('span', { class: 'spin' }); detail = `${s.found} in window`; }
    else if (s.state === 'running') { icon = el('span', { class: 'spin' }); detail = 'fetching…'; }
    else { icon = '·'; detail = 'queued'; }
    return el('li', { class: s.state }, el('span', { class: 'st' }, icon), el('span', {}, s.name),
      el('span', { class: 'detail', title: detail }, detail));
  }));
}

async function pollJob(id) {
  let job;
  try {
    job = await api(`/api/refresh/${id}`);
  } catch (error) {
    toast(error.message, 'error');
    $('#collectButton').disabled = false;
    return;
  }
  renderProgress(job);
  if (job.status === 'running') { setTimeout(() => pollJob(id), 700); return; }
  $('#collectButton').disabled = false;
  const previous = state.status && state.status.last_refresh;  // the refresh before this one
  const since = previous ? ` since the last refresh (${previous.scope === 'scheduled' ? 'automatic, ' : ''}${relativeTime(previous.at)})` : '';
  const failed = job.sources.filter(s => s.state === 'error').length;
  const failedNote = failed ? ` · ${failed} source${failed === 1 ? '' : 's'} failed` : '';
  if (job.status === 'failed') toast(`Refresh failed: ${job.error}`, 'error');
  else if (job.added) toast(`${fmt(job.added)} new article${job.added === 1 ? '' : 's'}${since} — showing only these${failedNote}`, failed ? 'error' : '');
  else toast(`No new articles${since}: everything the sources published is already collected${failedNote}`, failed ? 'error' : '');
  if (!failed) setTimeout(() => { $('#progress').hidden = true; }, 6000);
  await Promise.all([loadStatus(), loadSources(), loadBriefing()]);
  if (job.status === 'done' && job.added) {
    // Show exactly what this refresh brought: the articles tagged with its run id.
    state.newRun = job.run;
    state.newCount = job.added;
    state.q = ''; state.topic = ''; state.sources.clear(); $('#search').value = '';
    state.range = 'all';
    renderRange();
  }
  loadArticles();
}

// ----- source management ----------------------------------------------------
function sourceRow(source = {}) {
  const row = el('div', { class: 'source-row', role: 'listitem' });
  row.dataset.id = source.id ?? '';
  const status = source.last_error
    ? el('div', { class: 'status err', title: source.last_error }, `Last check failed: ${source.last_error}`)
    : source.last_checked
      ? el('div', { class: 'status' }, `Checked ${relativeTime(source.last_checked)} · ${source.feed_url ? 'feed: ' + source.feed_url : 'via Google News'}`)
      : el('div', { class: 'status' }, source.feed_url ? `Feed: ${source.feed_url}` : 'Not checked yet — feed will be discovered on first refresh');
  row.append(
    el('input', { type: 'checkbox', class: 'enabled', checked: source.enabled ?? true, 'aria-label': 'Enabled' }),
    el('input', { type: 'text', class: 'name', required: true, maxlength: '80', placeholder: 'Source name', value: source.name || '', 'aria-label': 'Source name' }),
    el('input', { type: 'text', class: 'url', required: true, maxlength: '300', placeholder: 'example.com/blog or a feed URL', value: source.url || '', 'aria-label': 'Website or feed URL', inputmode: 'url' }),
    el('input', { type: 'number', class: 'limit', min: '1', max: '50', required: true, value: String(source.max_items || state.settings.default_max_items), 'aria-label': 'Articles per refresh' }),
    el('label', { class: 'filter-toggle' }, el('input', { type: 'checkbox', class: 'topic-filter', checked: source.topic_filter ?? true }), 'Filter off-topic'),
    el('button', { type: 'button', class: 'remove', 'aria-label': `Remove ${source.name || 'source'}`, onclick: () => row.remove() }, '×'),
    status);
  return row;
}

function openSourcesDialog() {
  $('#setLookback').value = state.settings.lookback_days;
  $('#setDefaultItems').value = state.settings.default_max_items;
  $('#setAuto').checked = state.settings.auto_refresh;
  $('#setAutoTime').value = state.settings.auto_refresh_time;
  $('#setAutoTime').disabled = !state.settings.auto_refresh;
  $('#sourcesError').textContent = '';
  $('#sourceTable').replaceChildren(
    el('div', { class: 'source-row head', 'aria-hidden': 'true' },
      el('span', {}, 'On'), el('span', {}, 'Name'), el('span', {}, 'Website or feed'), el('span', {}, 'Articles'), el('span', {}, 'Relevance'), el('span')),
    ...state.sourceConfig.map(sourceRow));
  $('#sourcesDialog').showModal();
}

async function saveSources(event) {
  event.preventDefault();
  const rows = [...$('#sourceTable').querySelectorAll('.source-row:not(.head)')];
  const body = {
    settings: {
      lookback_days: Number($('#setLookback').value),
      default_max_items: Number($('#setDefaultItems').value),
      auto_refresh: $('#setAuto').checked,
      auto_refresh_time: $('#setAutoTime').value || state.settings.auto_refresh_time,
    },
    sources: rows.map(r => ({
      id: r.dataset.id ? Number(r.dataset.id) : null,
      enabled: r.querySelector('.enabled').checked,
      name: r.querySelector('.name').value.trim(),
      url: r.querySelector('.url').value.trim(),
      max_items: Number(r.querySelector('.limit').value),
      topic_filter: r.querySelector('.topic-filter').checked,
    })),
  };
  try {
    const data = await api('/api/sources', { method: 'PUT', body });
    state.sourceConfig = data.sources;
    state.settings = data.settings;
    $('#sourcesDialog').close();
    toast('Sources saved');
    loadStatus();
    renderFacets();
  } catch (error) {
    $('#sourcesError').textContent = error.message;
  }
}

async function resetSources() {
  if (!confirm('Replace your source list with the six default publications? Collected articles are kept.')) return;
  try {
    const data = await api('/api/sources', { method: 'PUT', body: { reset: true } });
    state.sourceConfig = data.sources;
    state.settings = data.settings;
    openSourcesDialog();
    loadStatus();
  } catch (error) {
    $('#sourcesError').textContent = error.message;
  }
}

// ----- wiring ---------------------------------------------------------------
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  storage.set('pulse.theme', theme);
}

function currentTheme() {
  return document.documentElement.dataset.theme
    || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
}

/** Info "i" buttons on the statistics: hover/focus shows the explanation, click or tap pins it. */
function wireInfoTips() {
  const buttons = [...document.querySelectorAll('.info-btn')];
  const close = except => buttons.forEach(b => {
    if (b === except) return;
    b.setAttribute('aria-expanded', 'false');
    b.nextElementSibling.classList.remove('open');
  });
  for (const button of buttons) {
    const tip = button.nextElementSibling;
    const placeArrow = () => {
      // The tip is positioned against its card, or against the whole stats block on phones.
      const stat = button.closest('.stat');
      const anchor = getComputedStyle(stat).position === 'static' ? stat.parentElement : stat;
      const a = anchor.getBoundingClientRect();
      const inset = parseFloat(getComputedStyle(tip).left) || 0;
      tip.style.setProperty('--tip-top', `${stat.getBoundingClientRect().top - a.top + 44}px`);
      tip.style.setProperty('--arrow-x', `${Math.max(8, button.getBoundingClientRect().left - a.left - inset + 3)}px`);
    };
    button.addEventListener('mouseenter', placeArrow);
    button.addEventListener('focus', placeArrow);
    button.addEventListener('click', event => {
      event.stopPropagation();
      placeArrow();
      const open = !tip.classList.contains('open');
      close(button);
      tip.classList.toggle('open', open);
      button.setAttribute('aria-expanded', String(open));
    });
  }
  document.addEventListener('click', event => { if (!event.target.closest('.info-tip')) close(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
}

function wire() {
  wireInfoTips();
  let debounce;
  $('#search').addEventListener('input', event => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { state.q = event.target.value.trim(); loadArticles(); }, 250);
  });
  $('#searchForm').addEventListener('submit', event => {
    event.preventDefault();
    clearTimeout(debounce);
    state.q = $('#search').value.trim();
    loadArticles();
    $('#feed').scrollIntoView({ block: 'start' });
  });
  $('#search').addEventListener('keydown', event => {
    if (event.key === 'Escape' && event.target.value) { event.preventDefault(); setQuery(''); }
  });
  $('#sort').addEventListener('change', event => { state.sort = event.target.value; loadArticles(); });
  $('#loadMore').addEventListener('click', () => { state.offset = state.articles.length; loadArticles({ append: true }); });
  $('#clearSources').addEventListener('click', event => { event.preventDefault(); state.sources.clear(); loadArticles(); });

  $('#collectButton').addEventListener('click', () => ($('#collectOptions').hidden ? openMenu() : closeMenu()));
  document.addEventListener('click', event => { if (!$('#collectMenu').contains(event.target)) closeMenu(); });
  $('#collectMenu').addEventListener('keydown', event => {
    const items = [...$('#collectOptions').querySelectorAll('.menu-item')];
    const index = items.indexOf(document.activeElement);
    if (event.key === 'Escape') { closeMenu(); $('#collectButton').focus(); }
    else if (event.key === 'ArrowDown' && items.length) { event.preventDefault(); items[(index + 1) % items.length].focus(); }
    else if (event.key === 'ArrowUp' && items.length) { event.preventDefault(); items[(index - 1 + items.length) % items.length].focus(); }
  });
  $('#chooseAll').addEventListener('click', () => { $('#chooseList').querySelectorAll('input').forEach(i => { i.checked = true; }); updateChooseCount(); });
  $('#chooseNone').addEventListener('click', () => { $('#chooseList').querySelectorAll('input').forEach(i => { i.checked = false; }); updateChooseCount(); });
  $('#chooseDialog').addEventListener('close', () => {
    if ($('#chooseDialog').returnValue !== 'go') return;
    const days = Number($('#chooseDays').value);
    startCollection(chosenIds(), days >= 1 && days <= 30 ? days : undefined);
  });
  $('#closeProgress').addEventListener('click', () => { $('#progress').hidden = true; });

  $('#openSources').addEventListener('click', openSourcesDialog);
  $('#sourcesForm').addEventListener('submit', saveSources);
  $('#addSource').addEventListener('click', () => {
    const row = sourceRow({ topic_filter: true });
    $('#sourceTable').append(row);
    row.querySelector('.name').focus();
  });
  $('#resetSources').addEventListener('click', resetSources);
  $('#setAuto').addEventListener('change', event => { $('#setAutoTime').disabled = !event.target.checked; });
  $('#openAbout').addEventListener('click', () => {
    loadStatus().catch(() => {});  // refresh the live storage figures
    loadRefreshLog();
    $('#aboutDialog').showModal();
  });
  for (const dialog of document.querySelectorAll('dialog')) {
    dialog.addEventListener('click', event => {
      if (event.target === dialog || event.target.closest('[data-close]')) dialog.close();
    });
  }
  $('#themeToggle').addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'));

  document.addEventListener('keydown', event => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
    if (event.key === '/' && !typing && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      $('#search').focus();
    }
  });
  const markVisit = () => storage.set('pulse.lastVisit', String(Date.now()));
  addEventListener('pagehide', markVisit);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') markVisit(); });
}

async function init() {
  $('#today').textContent = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  readUrl();
  renderRange();
  wire();
  NewsChart.init();
  try {
    await Promise.all([loadStatus(), loadSources()]);
  } catch (error) {
    toast(location.protocol === 'file:' ? 'Run “python3 server.py” and open http://localhost:8000' : error.message, 'error');
    return;
  }
  loadBriefing().catch(() => { $('#briefing').hidden = true; });
  await loadArticles();
  if (state.status.collecting) toast('A refresh is already running in the background…');
  watchBackgroundRefreshes();
}

/** Every minute, re-read the status; when a refresh made elsewhere (e.g. the automatic one) finishes, reload the news. */
function watchBackgroundRefreshes() {
  let lastSeen = state.status.last_refresh && state.status.last_refresh.at;
  let announced = false;
  setInterval(async () => {
    if (document.hidden || $('#collectButton').disabled) return;  // our own refresh is being tracked already
    try { await loadStatus(); } catch { return; }
    const s = state.status;
    if (s.collecting && !announced) { toast('An automatic refresh is running…'); announced = true; }
    const latest = s.last_refresh && s.last_refresh.at;
    if (latest && latest !== lastSeen) {
      lastSeen = latest;
      announced = false;
      if (s.last_refresh.scope === 'scheduled') toast(`Automatic refresh finished · ${fmt(s.last_refresh.added)} new articles`);
      loadBriefing().catch(() => {});
      loadArticles();
    }
  }, 60000);
}

init();
