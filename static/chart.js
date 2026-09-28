'use strict';
// "News per day" — stacked column chart of stored articles by publication date (vanilla SVG).
// Follows the page filters: 24 hours -> one column per hour; 7/15/30 days -> one per day;
// All collected -> one per day (one per week beyond 120 days). All text is set with textContent.
(function () {
  const SVG = 'http://www.w3.org/2000/svg';
  // Stack order from the baseline up. Chosen with the dataviz palette validator so that no two
  // touching colours are confusable (worst adjacent ΔE 19.5 dark / 22.7 light, colour-blind safe).
  const STACK = [
    ['Vulnerability', 'vuln'], ['AI Security', 'ai'], ['Threat Intel', 'threat'],
    ['SecOps', 'secops'], ['Supply Chain', 'supply'],
  ];
  const HOUR = 3600000;
  const HEIGHT = 250;
  const M = { top: 24, right: 8, bottom: 30, left: 40 };
  const GAP = 2;          // surface gap between stacked segments
  const MAX_BAR = 24;     // bars never thicker than 24px
  let last = null;        // { bins, opts, layout } of the current render
  let active = -1;

  const $ = id => document.getElementById(id);

  function svg(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  }

  function html(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of children.flat()) {
      if (child != null && child !== false) node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }

  const fmt = n => Number(n).toLocaleString();

  // ----- binning -------------------------------------------------------------
  function floorTo(date, unit) {
    const d = new Date(date);
    if (unit === 'hour') { d.setMinutes(0, 0, 0); return d; }
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    if (unit === 'week') day.setDate(day.getDate() - ((day.getDay() + 6) % 7));  // Monday
    return day;
  }

  function step(date, unit) {
    if (unit === 'hour') return new Date(date.getTime() + HOUR);
    const next = new Date(date);
    next.setDate(next.getDate() + (unit === 'week' ? 7 : 1));
    return next;
  }

  function buildBins(histogram, range, rangeStart) {
    const now = new Date();
    const hours = histogram.map(h => ({ at: new Date(`${h.hour}:00:00Z`), topic: h.topic, count: h.count }));
    let start = rangeStart ? new Date(rangeStart) : null;
    if (!start) start = hours.length ? new Date(Math.min(...hours.map(h => h.at))) : now;
    let unit = range === '24h' ? 'hour' : 'day';
    if (range === 'all' && (now - start) / (24 * HOUR) > 120) unit = 'week';

    const bins = [];
    const index = new Map();
    for (let t = floorTo(start, unit); t <= now; t = step(t, unit)) {
      index.set(t.getTime(), bins.length);
      bins.push({ start: t, total: 0, counts: {} });
    }
    for (const h of hours) {
      const i = index.get(floorTo(h.at, unit).getTime());
      if (i === undefined) continue;
      bins[i].counts[h.topic] = (bins[i].counts[h.topic] || 0) + h.count;
      bins[i].total += h.count;
    }
    return { bins, unit };
  }

  // ----- labels --------------------------------------------------------------
  const isToday = d => d.toDateString() === new Date().toDateString();

  function tickLabel(bin, unit, dense) {
    const d = bin.start;
    if (unit === 'hour') {
      return d.getHours() === 0 ? d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' })
        : `${String(d.getHours()).padStart(2, '0')}h`;
    }
    if (unit === 'day' && !dense) return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric' });
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
  }

  function longLabel(bin, unit) {
    const d = bin.start;
    if (unit === 'hour') {
      const end = new Date(d.getTime() + HOUR);
      const t = x => x.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
      return `${d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })}, ${t(d)}–${t(end)}`;
    }
    if (unit === 'week') return `Week of ${d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`;
    return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
      + (isToday(d) ? ' (today, so far)' : '');
  }

  function niceScale(max) {
    if (max <= 0) return { top: 4, ticks: [0, 1, 2, 3, 4] };
    const rough = max / 4;
    const mag = 10 ** Math.floor(Math.log10(rough));
    const unitStep = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= rough && Number.isInteger(s)) || Math.ceil(rough);
    const top = Math.ceil(max / unitStep) * unitStep;
    const ticks = [];
    for (let v = 0; v <= top; v += unitStep) ticks.push(v);
    return { top, ticks };
  }

  // ----- drawing -------------------------------------------------------------
  function roundedTop(x, y, w, h, r) {
    r = Math.min(r, h, w / 2);
    return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
  }

  function draw() {
    const plot = $('chartPlot');
    const { bins, unit } = last;
    const width = Math.max(280, plot.clientWidth);
    const plotW = width - M.left - M.right;
    const plotH = HEIGHT - M.top - M.bottom;
    const band = plotW / bins.length;
    const barW = Math.max(2, Math.min(MAX_BAR, band * 0.64));
    const maxTotal = Math.max(0, ...bins.map(b => b.total));
    const { top, ticks } = niceScale(maxTotal);
    const y = v => M.top + plotH - (v / top) * plotH;

    const root = svg('svg', {
      viewBox: `0 0 ${width} ${HEIGHT}`, role: 'img', tabindex: '0',
      'aria-labelledby': 'timelineTitle chartCaption',
    });
    const grid = svg('g', { class: 'grid' });
    for (const t of ticks) {
      grid.append(svg('line', { x1: M.left, x2: width - M.right, y1: y(t), y2: y(t) }));
      root.append(svg('text', { class: 'tick', x: M.left - 8, y: y(t) + 4, 'text-anchor': 'end' }, fmt(t)));
    }
    root.prepend(grid);

    // Label every column when they fit, otherwise every k-th (always the latest one).
    const dense = bins.length > 16;
    const every = Math.max(1, Math.ceil(bins.length / Math.max(1, Math.floor(plotW / (unit === 'hour' ? 44 : dense ? 52 : 64)))));
    const showCaps = bins.length <= 31 && band >= 16;
    const peak = bins.reduce((best, b, i) => (b.total > (bins[best]?.total ?? -1) ? i : best), 0);

    bins.forEach((bin, i) => {
      const cx = M.left + band * i + band / 2;
      const col = svg('g', { class: 'col', 'data-i': i });
      col.append(svg('rect', { class: 'hit', x: M.left + band * i, y: M.top, width: band, height: plotH, rx: 4 }));
      let cursor = y(0);
      const present = STACK.filter(([name]) => bin.counts[name]);
      present.forEach(([name, cls], k) => {
        const h = (bin.counts[name] / top) * plotH;
        const isTop = k === present.length - 1;
        const drawn = isTop || h <= GAP + 1 ? h : h - GAP;  // leave a surface gap above lower segments
        const yTop = cursor - h;
        const attrs = { class: `seg seg-${cls}` };
        if (isTop) col.append(svg('path', { ...attrs, d: roundedTop(cx - barW / 2, yTop, barW, h, 4) }));
        else col.append(svg('rect', { ...attrs, x: cx - barW / 2, y: cursor - drawn, width: barW, height: drawn }));
        cursor = yTop;
      });
      if (bin.total && (showCaps || i === peak)) {
        col.append(svg('text', { class: 'cap', x: cx, y: y(bin.total) - 6, 'text-anchor': 'middle' }, fmt(bin.total)));
      }
      const labelled = (bins.length - 1 - i) % every === 0;
      if (labelled) {
        const today = unit !== 'hour' && isToday(bin.start);
        root.append(svg('text', { class: `tick${today ? ' today' : ''}`, x: cx, y: HEIGHT - 10, 'text-anchor': 'middle' },
          today && unit === 'day' ? 'Today' : tickLabel(bin, unit, dense)));
      }
      col.addEventListener('pointerenter', () => setActive(i));
      col.addEventListener('pointerleave', () => setActive(-1));
      root.append(col);
    });
    root.append(svg('line', { class: 'baseline', x1: M.left, x2: width - M.right, y1: y(0), y2: y(0) }));

    root.addEventListener('keydown', event => {
      if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
        event.preventDefault();
        const next = active < 0 ? bins.length - 1 : active + (event.key === 'ArrowRight' ? 1 : -1);
        setActive(Math.max(0, Math.min(bins.length - 1, next)));
      } else if (event.key === 'Escape') setActive(-1);
    });
    root.addEventListener('focus', () => { if (active < 0) setActive(bins.length - 1); });
    root.addEventListener('blur', () => setActive(-1));
    plot.replaceChildren(root);
    last.layout = { band, barW, y, width };
    if (active >= bins.length) active = -1;
  }

  function setActive(i) {
    active = i;
    const plot = $('chartPlot');
    plot.querySelectorAll('.col').forEach(c => c.classList.toggle('active', Number(c.dataset.i) === i));
    const tip = $('chartTip');
    if (i < 0 || !last) { tip.hidden = true; return; }
    const bin = last.bins[i];
    const rows = STACK.slice().reverse().filter(([name]) => bin.counts[name])  // top of the stack first
      .map(([name, cls]) => html('li', { class: `t-${cls}` }, html('span', { class: 'key' }), name, html('b', {}, fmt(bin.counts[name]))));
    tip.replaceChildren(
      html('div', { class: 'when' }, longLabel(bin, last.unit)),
      html('div', { class: 'total' }, fmt(bin.total), ' ', html('small', {}, bin.total === 1 ? 'article' : 'articles')),
      rows.length ? html('ul', {}, rows) : null);
    tip.hidden = false;
    // Beside the column (right of it, or left near the edge) and inside the plot, so it never
    // hides the bar it describes or slides under the sticky filter bar.
    const { band } = last.layout;
    const plotBox = plot.getBoundingClientRect();
    const cardBox = $('chartCard').getBoundingClientRect();
    const colLeft = plotBox.left - cardBox.left + M.left + band * i;
    const tipW = tip.offsetWidth;
    let left = colLeft + band + 8;
    if (left + tipW > cardBox.width - 8) left = colLeft - tipW - 8;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${plotBox.top - cardBox.top + M.top}px`;
  }

  // ----- legend, subtitle, table ---------------------------------------------
  function renderLegend(opts) {
    const counts = (opts.facets && opts.facets.topics) || {};
    $('chartLegend').replaceChildren(...STACK.map(([name, cls]) => html('li', {},
      html('button', {
        type: 'button', class: `t-${cls}${counts[name] ? '' : ' zero'}`, 'aria-pressed': String(opts.topic === name),
        title: opts.topic === name ? 'Show all topics' : `Show only ${name}`,
        onclick: () => opts.onTopic(opts.topic === name ? '' : name),
      }, html('span', { class: 'sw', 'aria-hidden': 'true' }), name, html('span', { class: 'n' }, fmt(counts[name] || 0))))));
  }

  function renderSubtitle(opts, bins, unit) {
    const total = bins.reduce((sum, b) => sum + b.total, 0);
    const per = unit === 'hour' ? 'per hour' : unit === 'week' ? 'per week' : 'per day';
    const from = bins.length ? bins[0].start : new Date();
    const span = opts.range === 'all'
      ? `since ${from.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}`
      : `in the last ${{ '24h': '24 hours', '7d': '7 days', '15d': '15 days', '30d': '30 days' }[opts.range]}`;
    const sub = $('chartSub');
    sub.replaceChildren(html('strong', {}, fmt(total)), ` article${total === 1 ? '' : 's'} published ${span}, ${per}, by topic`,
      opts.filtered ? ' · matching the current filters' : '');
    const peak = bins.reduce((best, b) => (b.total > best.total ? b : best), { total: 0 });
    $('chartCaption').textContent = `Stacked columns of articles ${per} ${span}. Total ${total}.`
      + (peak.total ? ` Busiest: ${longLabel(peak, unit)} with ${peak.total}.` : '');
  }

  function renderTable(bins, unit) {
    const head = html('tr', {}, html('th', { scope: 'col' }, unit === 'hour' ? 'Hour' : unit === 'week' ? 'Week' : 'Day'),
      html('th', { scope: 'col' }, 'Total'), STACK.map(([name]) => html('th', { scope: 'col' }, name)));
    const rows = bins.slice().reverse().map(b => html('tr', {},
      html('th', { scope: 'row' }, longLabel(b, unit)), html('td', {}, fmt(b.total)),
      STACK.map(([name]) => html('td', {}, fmt(b.counts[name] || 0)))));
    $('chartTable').replaceChildren(html('table', {}, html('thead', {}, head), html('tbody', {}, rows)));
  }

  // ----- public API ----------------------------------------------------------
  function render(opts) {
    const { bins, unit } = buildBins(opts.histogram || [], opts.range, opts.rangeStart);
    last = { bins, unit, opts };
    $('chartCard').classList.remove('loading');
    renderLegend(opts);
    renderSubtitle(opts, bins, unit);
    renderTable(bins, unit);
    if (!bins.some(b => b.total)) {
      $('chartTip').hidden = true;
      $('chartPlot').replaceChildren(html('div', { class: 'chart-empty' }, 'No articles were published in this period.'));
      return;
    }
    draw();
  }

  function loading() { $('chartCard').classList.add('loading'); }

  function init() {
    const toggle = $('chartTableToggle');
    toggle.addEventListener('click', () => {
      const show = $('chartTable').hidden;
      $('chartTable').hidden = !show;
      $('chartPlot').hidden = show;
      toggle.setAttribute('aria-pressed', String(show));
      toggle.textContent = show ? 'Show as chart' : 'Show as table';
      $('chartTip').hidden = true;
    });
    let frame;
    new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => { if (last && last.bins.some(b => b.total) && !$('chartPlot').hidden) draw(); });
    }).observe($('chartPlot'));
  }

  window.NewsChart = { render, loading, init };
})();
