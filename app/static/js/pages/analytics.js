/**
 * Analytics — GA4 read as a dashboard.
 *
 * What this replaces, and why each piece is different:
 *
 *  - Four `window.*` globals existed only so five inline `onclick` attributes
 *    could reach them. Everything here is delegated and bound with an
 *    AbortController, the pattern the other page scripts use.
 *  - The property list interpolated GA4 display names into an `onclick="…"`
 *    attribute and escaped them with `.replace(/'/g, "\\'")`. Rows are built
 *    with createElement/textContent now — a label from an external API is
 *    untrusted data, and the old `escapeHtml` helper (textContent → innerHTML)
 *    leaves both quote characters alone, which is exactly wrong for the
 *    `title="…"` attribute it was also used for.
 *  - A dead token made the poll handler overwrite `.dashboard-content-wrapper`
 *    with a banner — throwing the whole screen away to report one failure. The
 *    banner is markup that already exists now; the data just goes stale.
 *  - The realtime pulse animated unconditionally, so it kept beating over a
 *    connection that had stopped answering.
 *
 * Chart notes: one series at a time (the KPI tiles are the selector), so there
 * is never a second hue to tell apart and never a legend to read. Marks are
 * SVG built at the container's real pixel size — a scaled viewBox would stretch
 * the 2px stroke with the card. Every value the tooltip shows is also in the
 * table view under the chart; the hover only ever enhances.
 */

(function analyticsPage() {
    'use strict';

    if (window.__analyticsAbort) {
        try { window.__analyticsAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__analyticsAbort = controller;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const $ = (sel) => root.querySelector(sel);
    const $$ = (sel) => Array.from(root.querySelectorAll(sel));

    const timers = [];
    function every(ms, fn) { timers.push(setInterval(fn, ms)); }
    function stopTimers() { timers.forEach(clearInterval); timers.length = 0; }

    signal.addEventListener('abort', stopTimers);

    // A poller whose DOM has been swapped out by PJAX has nothing left to
    // paint. The previous version watched for a missing element id; this checks
    // the actual container it holds a reference to.
    document.addEventListener('pjax:complete', () => {
        if (!document.contains(root)) controller.abort();
    }, { signal });

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------

    // 1,284 / 12.9K / 4.2M — a figure in a tile must not wrap.
    function compact(n) {
        const v = Number(n) || 0;
        if (v < 1000) return String(v);
        if (v < 1e6) return (v / 1000).toFixed(v < 10000 ? 1 : 0).replace(/\.0$/, '') + 'K';
        return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    }

    function grouped(n) {
        return (Number(n) || 0).toLocaleString('en-US');
    }

    function duration(seconds) {
        const s = Math.round(Number(seconds) || 0);
        if (s < 1) return '0s';
        const m = Math.floor(s / 60);
        return m > 0 ? m + 'm ' + (s % 60) + 's' : s + 's';
    }

    function pct(part, whole) {
        if (!whole) return 0;
        return (part / whole) * 100;
    }

    // Axis ticks round to clean numbers — they carry the values the chart does
    // not directly label, so 1,000 / 2,000 rather than 1,873 / 3,746.
    //
    // The ladder includes 2.5 deliberately. On a plain 1/2/5/10 ladder a series
    // peaking at 240 gets an axis of 500, so the line never rises past halfway
    // and the chart visually understates its own data. 2.5 closes the widest
    // gap in the ladder; the resulting ticks (0 / 125 / 250) still read as
    // round numbers.
    function niceCeil(v) {
        if (v <= 0) return 1;
        const mag = Math.pow(10, Math.floor(Math.log10(v)));
        const norm = v / mag;
        const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
        return step * mag;
    }

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }

    const SVGNS = 'http://www.w3.org/2000/svg';
    function svg(tag, attrs) {
        const node = document.createElementNS(SVGNS, tag);
        for (const k in attrs) node.setAttribute(k, attrs[k]);
        return node;
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    // ------------------------------------------------------------------
    // GATE — property selection
    // ------------------------------------------------------------------

    const propertyList = $('[data-property-list]');
    if (propertyList) initPropertyPicker();

    function initPropertyPicker() {
        loadProperties();

        propertyList.addEventListener('click', (e) => {
            const row = e.target.closest('[data-property-id]');
            if (!row) return;
            selectProperty(row.dataset.propertyId, row.dataset.propertyName, row);
        }, { signal });
    }

    function listNote(text, kind) {
        propertyList.textContent = '';
        propertyList.setAttribute('aria-busy', 'false');
        const note = el('div', 'an-note');
        note.appendChild(el('i', 'bi bi-' + (kind === 'error' ? 'exclamation-triangle' : 'info-circle')));
        note.appendChild(el('span', null, text));
        propertyList.appendChild(note);
    }

    async function loadProperties() {
        // The Admin API is slow to answer when it is not enabled for the
        // project — it hangs rather than refusing — so this one call keeps its
        // own deadline instead of waiting on the browser's.
        const deadline = new AbortController();
        const timeout = setTimeout(() => deadline.abort(), 20000);
        signal.addEventListener('abort', () => deadline.abort());

        let data;
        try {
            const res = await fetch('/analytics/properties', { signal: deadline.signal });
            clearTimeout(timeout);
            if (res.status === 401) {
                listNote('The Google connection expired. Reconnect and try again.', 'error');
                return;
            }
            data = await res.json();
        } catch (err) {
            clearTimeout(timeout);
            if (signal.aborted) return;
            listNote(
                deadline.signal.aborted
                    ? 'Google did not answer in time. Check that the Analytics Admin API is enabled for your Google Cloud project.'
                    : 'Could not reach Google. Check your connection and reload.',
                'error');
            return;
        }

        if (data.error) { listNote(String(data.error), 'error'); return; }
        if (!data.properties || !data.properties.length) {
            listNote('No GA4 properties found on this Google account. Create one in Analytics, then reload.', 'info');
            return;
        }

        propertyList.textContent = '';
        propertyList.setAttribute('aria-busy', 'false');

        data.properties.forEach((prop) => {
            // Names come from an external API: built as text nodes, never
            // interpolated into markup or into an attribute.
            const row = el('button', 'an-property');
            row.type = 'button';
            row.dataset.propertyId = prop.property_id;
            row.dataset.propertyName = prop.display_name;

            const body = el('span', 'an-property-body');
            body.appendChild(el('span', 'an-property-name', prop.display_name));
            body.appendChild(el('span', 'an-property-account', prop.account_name));
            row.appendChild(body);
            row.appendChild(el('i', 'bi bi-chevron-right'));
            propertyList.appendChild(row);
        });
    }

    async function selectProperty(id, name, row) {
        $$('.an-property').forEach((b) => { b.disabled = true; });
        row.classList.add('is-busy');

        try {
            const res = await fetch('/analytics/select-property', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ property_id: id, property_name: name }),
                signal
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'failed');

            if (data.measurement_id) {
                toast('success', 'Property linked',
                    data.domain
                        ? data.measurement_id + ' · ' + data.domain
                        : 'Tracking ID ' + data.measurement_id + ' saved to your site settings.');
            }
            setTimeout(() => window.location.reload(), 600);
        } catch (err) {
            if (signal.aborted) return;
            $$('.an-property').forEach((b) => { b.disabled = false; });
            row.classList.remove('is-busy');
            toast('error', 'Could not select that property', 'Please try again.');
        }
    }

    // ------------------------------------------------------------------
    // DISCONNECT
    // ------------------------------------------------------------------

    root.addEventListener('click', (e) => {
        const action = e.target.closest('[data-action]');
        if (!action) return;

        if (action.dataset.action === 'disconnect') {
            const node = document.getElementById('disconnectModal');
            if (node && typeof bootstrap !== 'undefined') {
                bootstrap.Modal.getOrCreateInstance(node).show();
            }
        } else if (action.dataset.action === 'confirm-disconnect') {
            confirmDisconnect(action);
        }
    }, { signal });

    async function confirmDisconnect(button) {
        button.disabled = true;
        try {
            const res = await fetch('/analytics/disconnect', { method: 'POST', signal });
            const data = await res.json();
            if (data.success) { window.location.reload(); return; }
            throw new Error('failed');
        } catch (err) {
            if (signal.aborted) return;
            button.disabled = false;
            toast('error', 'Could not disconnect', 'Please try again.');
        }
    }

    // ------------------------------------------------------------------
    // DASHBOARD
    // ------------------------------------------------------------------

    const chartBox = $('[data-chart]');
    if (!chartBox) return;

    const METRICS = {
        page_views: 'Page views',
        sessions: 'Sessions',
        users: 'Users'
    };

    const PERIOD_LABEL = { '1': 'today', '7': 'last 7 days', '30': 'last 30 days' };
    const PERIOD_PREV = { '1': 'yesterday', '7': 'previous 7 days', '30': 'previous 30 days' };

    const state = {
        period: '7',
        metric: 'page_views',
        series: [],          // [{iso,label,page_views,sessions,users}]
        granularity: 'day',
        totals: null,
        previous: null,
        focus: -1,           // keyboard/hover index into series
        dead: false          // the connection is gone; stop claiming to be live
    };

    const ui = {
        pulse: $('[data-live-pulse]'),
        realtime: $('[data-realtime]'),
        realtimeWord: $('[data-realtime-word]'),
        avgDuration: $('[data-avg-duration]'),
        bounce: $('[data-bounce]'),
        bounceFill: $('[data-bounce-fill]'),
        chartTitle: $('[data-chart-title]'),
        chartRange: $('[data-chart-range]'),
        chartLoading: $('[data-chart-loading]'),
        tip: $('[data-chart-tip]'),
        chartTable: $('[data-chart-table]'),
        topPages: $('[data-top-pages]'),
        sources: $('[data-sources]'),
        reconnect: $('[data-reconnect-banner]')
    };

    // --- Reconnect ------------------------------------------------------

    // The old handler replaced the page wrapper's innerHTML with a banner,
    // destroying every figure on screen to report one failure. The banner is
    // already in the markup; the numbers simply stop being current and say so.
    function goDead() {
        if (state.dead) return;
        state.dead = true;
        stopTimers();
        if (ui.reconnect) ui.reconnect.hidden = false;
        if (ui.pulse) { ui.pulse.classList.remove('is-live'); ui.pulse.classList.add('is-stale'); }
        $$('.an-card, .an-kpi').forEach((node) => node.classList.add('an-stale'));
    }

    // Returns true when the caller should stop — the response says the
    // connection is gone, or this instance has been torn down.
    function halted(res, data) {
        if (signal.aborted) return true;
        if (res && (res.status === 401 || res.status === 403)) { goDead(); return true; }
        if (data && data.reconnect) { goDead(); return true; }
        return false;
    }

    // --- Realtime -------------------------------------------------------

    async function fetchRealtime() {
        let res, data;
        try {
            res = await fetch('/api/analytics/realtime', { signal });
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            // A single missed poll is not a disconnection. The heartbeat stops
            // rather than continuing to beat over nothing.
            if (ui.pulse) ui.pulse.classList.remove('is-live');
            return;
        }
        if (halted(res, data)) return;
        if (!data.success) return;

        const n = Number(data.active_users) || 0;
        if (ui.realtime) ui.realtime.textContent = grouped(n);
        if (ui.realtimeWord) {
            const d = ui.realtimeWord.dataset;
            ui.realtimeWord.textContent = n === 1 ? (d.one || '') : (d.many || '');
        }
        if (ui.pulse) { ui.pulse.classList.add('is-live'); ui.pulse.classList.remove('is-stale'); }
    }

    // --- Overview + KPI tiles -------------------------------------------

    async function fetchOverview() {
        let res, data;
        try {
            res = await fetch('/api/analytics/overview?period=' + state.period, { signal });
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            return;
        }
        if (halted(res, data) || !data.success) return;

        state.totals = data.data;
        state.previous = data.previous;
        paintKpis();
        paintEngagement();
        paintTopPagesShares();
    }

    function paintKpis() {
        const t = state.totals || {};
        Object.keys(METRICS).forEach((key) => {
            const figure = $('[data-kpi="' + key + '"]');
            if (figure) {
                const v = Number(t[key]) || 0;
                // The exact figure stays on `title`, because the compact form
                // is unparseable and this is where anything reading it back
                // has to look.
                figure.setAttribute('title', grouped(v));
                figure.textContent = compact(v);
            }
            paintDelta(key);
        });
    }

    function paintDelta(key) {
        const node = $('[data-delta="' + key + '"]');
        if (!node) return;

        node.textContent = '';
        node.classList.remove('is-up', 'is-down');

        const cur = Number((state.totals || {})[key]);
        const prev = state.previous ? Number(state.previous[key]) : null;
        if (!isFinite(cur) || prev == null || !isFinite(prev)) return;

        const against = PERIOD_PREV[state.period];

        // A percentage against zero is not a number. Say what happened instead
        // of printing an infinity or silently hiding the comparison.
        if (prev === 0) {
            node.appendChild(el('i', 'bi bi-dash'));
            node.appendChild(document.createTextNode(
                cur === 0 ? ' no visits either period' : ' first visits vs ' + against));
            return;
        }

        const change = ((cur - prev) / prev) * 100;
        const rounded = Math.abs(change) < 0.05 ? 0 : change;
        const dir = rounded > 0 ? 'up' : rounded < 0 ? 'down' : 'flat';

        // Direction is carried by an arrow glyph and words as well as by
        // colour, so the hue never means anything on its own.
        const icon = dir === 'up' ? 'arrow-up-right' : dir === 'down' ? 'arrow-down-right' : 'dash';
        if (dir !== 'flat') node.classList.add('is-' + dir);
        node.appendChild(el('i', 'bi bi-' + icon));
        node.appendChild(document.createTextNode(
            dir === 'flat'
                ? ' level vs ' + against
                : ' ' + Math.abs(rounded).toFixed(Math.abs(rounded) < 10 ? 1 : 0) + '% vs ' + against));
    }

    function paintEngagement() {
        const t = state.totals || {};
        if (ui.avgDuration) ui.avgDuration.textContent = duration(t.avg_duration);

        const rate = Number(t.bounce_rate);
        if (ui.bounce) ui.bounce.textContent = isFinite(rate) ? rate.toFixed(1) + '%' : '—';
        if (ui.bounceFill) {
            ui.bounceFill.style.width = Math.max(0, Math.min(100, isFinite(rate) ? rate : 0)) + '%';
        }
    }

    // --- Sparklines -----------------------------------------------------

    /**
     * The tile's trend slot. One hue throughout; the current period is marked
     * by *form* — a dot with a surface-coloured ring — never by a second
     * colour. role="img" + aria-label carries the same information as text,
     * because a micro-chart has no room for axes and a tooltip must never be
     * the only way to reach a value.
     */
    function sparkline(values, label) {
        const W = 116, H = 40, PAD = 6;   // PAD clears the r3.5 dot + 2px ring
        const n = values.length;
        if (!n) return null;

        const max = Math.max.apply(null, values);
        const min = Math.min.apply(null, values);
        const span = max - min || 1;
        const stepX = n > 1 ? (W - PAD * 2) / (n - 1) : 0;

        const pts = values.map((v, i) => {
            const x = PAD + i * stepX;
            // A flat series sits on the baseline, not halfway up: "nothing
            // happened" must not read as "steady at some level".
            const y = max === min
                ? (max === 0 ? H - PAD : H / 2)
                : H - PAD - ((v - min) / span) * (H - PAD * 2);
            return [x, y];
        });

        const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
        const area = line + ' L' + pts[n - 1][0].toFixed(1) + ' ' + (H - PAD) +
            ' L' + pts[0][0].toFixed(1) + ' ' + (H - PAD) + ' Z';

        const total = values.reduce((a, b) => a + b, 0);
        const summary = label + ': ' + grouped(total) + ' over ' + n + ' ' +
            (state.granularity === 'hour' ? 'hours' : 'days') +
            ', from ' + grouped(min) + ' to ' + grouped(max) +
            ', most recent ' + grouped(values[n - 1]) + '.';

        const node = svg('svg', {
            class: 'stat-trend', viewBox: '0 0 ' + W + ' ' + H,
            role: 'img', 'aria-label': summary
        });
        node.appendChild(svg('path', { class: 'stat-trend-area', d: area }));
        node.appendChild(svg('path', { class: 'stat-trend-line', d: line }));
        node.appendChild(svg('circle', {
            class: 'stat-trend-dot',
            cx: pts[n - 1][0].toFixed(1), cy: pts[n - 1][1].toFixed(1), r: 3.5
        }));
        return node;
    }

    function paintSparklines() {
        Object.keys(METRICS).forEach((key) => {
            const slot = $('[data-trend="' + key + '"]');
            if (!slot) return;
            slot.textContent = '';
            const node = sparkline(state.series.map((p) => Number(p[key]) || 0), METRICS[key]);
            if (node) slot.appendChild(node);
        });
    }

    // --- Time series ----------------------------------------------------

    async function fetchSeries() {
        let res, data;
        try {
            res = await fetch('/api/analytics/timeseries?period=' + state.period, { signal });
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            chartNote('Could not load the traffic chart. The figures above are unaffected.');
            return;
        }
        if (halted(res, data)) return;

        if (!data.success || !data.points || !data.points.length) {
            chartNote(state.period === '1'
                ? 'No traffic recorded yet today.'
                : 'No traffic recorded in this period.');
            state.series = [];
            paintSparklines();
            paintTable();
            return;
        }

        state.series = data.points;
        state.granularity = data.granularity || 'day';
        state.focus = -1;
        // Only now is the granularity known. loadPeriod() sets the caption up
        // front so the card is never captionless, but it can only guess "by
        // day" from the previous slice — switching to Today would otherwise
        // sit there labelled "by day" over an hourly chart.
        setChartCaption();
        if (ui.chartLoading) ui.chartLoading.hidden = true;
        drawChart();
        paintSparklines();
        paintTable();
    }

    function chartNote(text) {
        if (ui.chartLoading) ui.chartLoading.hidden = true;
        clearChart();
        const note = el('div', 'an-note');
        note.appendChild(el('i', 'bi bi-info-circle'));
        note.appendChild(el('span', null, text));
        chartBox.appendChild(note);
        chartBox.setAttribute('aria-label', text);
    }

    function clearChart() {
        Array.from(chartBox.children).forEach((child) => {
            if (child !== ui.tip && child !== ui.chartLoading) child.remove();
        });
    }

    // Plot geometry. The bottom band is reserved for the x labels rather than
    // letting them fall outside the box — a container sized to the plot alone
    // is what gives a chart card its own little scrollbar.
    const PAD = { top: 14, right: 10, bottom: 26, left: 44 };
    let plot = null;   // {w,h,x0,y0,x1,y1,xOf,yOf,max}

    function drawChart() {
        if (!state.series.length) return;

        const box = chartBox.getBoundingClientRect();
        const W = Math.max(240, Math.round(box.width));
        const H = Math.max(160, Math.round(box.height));
        clearChart();

        const key = state.metric;
        const values = state.series.map((p) => Number(p[key]) || 0);
        const max = niceCeil(Math.max.apply(null, values) || 1);
        const n = values.length;

        const x0 = PAD.left, x1 = W - PAD.right;
        const y0 = H - PAD.bottom, y1 = PAD.top;
        const xOf = (i) => n > 1 ? x0 + (i * (x1 - x0)) / (n - 1) : (x0 + x1) / 2;
        const yOf = (v) => y0 - (v / max) * (y0 - y1);
        plot = { W, H, x0, y0, x1, y1, xOf, yOf, max };

        const node = svg('svg', { viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true' });

        // Gridlines and y ticks: 0, half, max. Three is enough to read a level
        // off; more is chrome competing with the data.
        [0, max / 2, max].forEach((v) => {
            const y = yOf(v);
            node.appendChild(svg('line', { class: 'an-grid', x1: x0, x2: x1, y1: y, y2: y }));
            const t = svg('text', { class: 'an-tick', x: x0 - 8, y: y + 4, 'text-anchor': 'end' });
            t.textContent = compact(v);
            node.appendChild(t);
        });

        const line = values.map((v, i) => (i ? 'L' : 'M') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1)).join(' ');
        node.appendChild(svg('path', {
            class: 'an-area',
            d: line + ' L' + xOf(n - 1).toFixed(1) + ' ' + y0 + ' L' + xOf(0).toFixed(1) + ' ' + y0 + ' Z'
        }));
        node.appendChild(svg('path', { class: 'an-line', d: line }));

        // One direct label: the endpoint. A value on every point is chaos and
        // goes unread — the axis, the tooltip and the table carry the rest.
        node.appendChild(svg('circle', {
            class: 'an-dot', cx: xOf(n - 1).toFixed(1), cy: yOf(values[n - 1]).toFixed(1), r: 4
        }));

        // X labels, thinned so they cannot collide: at most eight across.
        const stride = Math.max(1, Math.ceil(n / 8));
        state.series.forEach((p, i) => {
            if (i % stride !== 0 && i !== n - 1) return;
            const t = svg('text', {
                class: 'an-tick', x: xOf(i).toFixed(1), y: H - 8,
                'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'
            });
            t.textContent = p.label;
            node.appendChild(t);
        });

        // The crosshair layer. One transparent rect over the plot, so the
        // reader aims at a date rather than at a 2px line.
        const cursor = svg('g', { class: 'an-cursor-group', visibility: 'hidden' });
        cursor.appendChild(svg('line', { class: 'an-cursor', y1: y1, y2: y0, x1: 0, x2: 0 }));
        cursor.appendChild(svg('circle', { class: 'an-cursor-dot', r: 4, cx: 0, cy: 0 }));
        node.appendChild(cursor);

        const hit = svg('rect', {
            x: x0, y: y1, width: Math.max(1, x1 - x0), height: Math.max(1, y0 - y1),
            fill: 'transparent'
        });
        node.appendChild(hit);

        chartBox.insertBefore(node, ui.tip);
        chartBox.setAttribute('aria-label', chartSummary());

        hit.addEventListener('pointermove', (e) => {
            const rect = node.getBoundingClientRect();
            const rel = ((e.clientX - rect.left) / rect.width) * W;
            const i = n > 1
                ? Math.round(((rel - x0) / (x1 - x0)) * (n - 1))
                : 0;
            focusPoint(Math.max(0, Math.min(n - 1, i)));
        }, { signal });

        hit.addEventListener('pointerleave', () => blurPoint(), { signal });

        if (state.focus >= 0) focusPoint(state.focus);
    }

    function chartSummary() {
        const key = state.metric;
        const values = state.series.map((p) => Number(p[key]) || 0);
        if (!values.length) return METRICS[key] + ': no data.';
        const total = values.reduce((a, b) => a + b, 0);
        return METRICS[key] + ' by ' + state.granularity + ', ' + PERIOD_LABEL[state.period] +
            ': ' + grouped(total) + ' across ' + values.length + ' points, ' +
            'from ' + grouped(Math.min.apply(null, values)) +
            ' to ' + grouped(Math.max.apply(null, values)) +
            '. Full figures in the table view below.';
    }

    // Hover and keyboard land in the same place, so focus shows exactly what
    // the pointer would.
    function focusPoint(i) {
        if (!plot || !state.series[i]) return;
        state.focus = i;

        const point = state.series[i];
        const value = Number(point[state.metric]) || 0;
        const cx = plot.xOf(i), cy = plot.yOf(value);

        const group = chartBox.querySelector('.an-cursor-group');
        if (group) {
            group.setAttribute('visibility', 'visible');
            const lineEl = group.querySelector('.an-cursor');
            lineEl.setAttribute('x1', cx);
            lineEl.setAttribute('x2', cx);
            const dot = group.querySelector('.an-cursor-dot');
            dot.setAttribute('cx', cx);
            dot.setAttribute('cy', cy);
        }

        if (ui.tip) {
            ui.tip.textContent = '';
            ui.tip.appendChild(el('span', 'an-tip-value', grouped(value) + ' ' + METRICS[state.metric].toLowerCase()));
            ui.tip.appendChild(el('span', 'an-tip-label', point.label));
            ui.tip.hidden = false;
            // Clamp so the bubble cannot hang off either edge of the card.
            const half = ui.tip.offsetWidth / 2;
            const left = Math.max(half + 2, Math.min(plot.W - half - 2, cx));
            ui.tip.style.left = left + 'px';
            ui.tip.style.top = Math.max(24, cy - 10) + 'px';
        }
    }

    function blurPoint() {
        state.focus = -1;
        const group = chartBox.querySelector('.an-cursor-group');
        if (group) group.setAttribute('visibility', 'hidden');
        if (ui.tip) ui.tip.hidden = true;
    }

    chartBox.addEventListener('keydown', (e) => {
        const n = state.series.length;
        if (!n) return;
        let i = state.focus;

        if (e.key === 'ArrowRight') i = i < 0 ? 0 : Math.min(n - 1, i + 1);
        else if (e.key === 'ArrowLeft') i = i < 0 ? n - 1 : Math.max(0, i - 1);
        else if (e.key === 'Home') i = 0;
        else if (e.key === 'End') i = n - 1;
        else if (e.key === 'Escape') { blurPoint(); return; }
        else return;

        e.preventDefault();
        focusPoint(i);
    }, { signal });

    chartBox.addEventListener('blur', () => blurPoint(), { signal });

    // Re-measure rather than scale: stretching the viewBox would stretch the
    // 2px stroke with it. Cheap because the series is already in memory.
    if (typeof ResizeObserver === 'function') {
        let raf = null;
        const ro = new ResizeObserver(() => {
            if (raf) cancelAnimationFrame(raf);
            raf = requestAnimationFrame(() => { raf = null; if (state.series.length) drawChart(); });
        });
        ro.observe(chartBox);
        signal.addEventListener('abort', () => ro.disconnect());
    }

    // --- Table view -----------------------------------------------------

    function paintTable() {
        const table = ui.chartTable;
        if (!table) return;

        Array.from(table.querySelectorAll('thead, tbody')).forEach((n) => n.remove());
        if (!state.series.length) return;

        const head = document.createElement('thead');
        const hr = document.createElement('tr');
        [state.granularity === 'hour' ? 'Hour' : 'Day', 'Page views', 'Sessions', 'Users']
            .forEach((label) => {
                const th = el('th', null, label);
                th.scope = 'col';
                hr.appendChild(th);
            });
        head.appendChild(hr);
        table.appendChild(head);

        const body = document.createElement('tbody');
        state.series.forEach((p) => {
            const tr = document.createElement('tr');
            const th = el('th', null, p.label);
            th.scope = 'row';
            tr.appendChild(th);
            ['page_views', 'sessions', 'users'].forEach((k) => {
                tr.appendChild(el('td', null, grouped(p[k])));
            });
            body.appendChild(tr);
        });
        table.appendChild(body);
    }

    // --- Top pages ------------------------------------------------------

    let topPages = [];

    async function fetchTopPages() {
        let res, data;
        try {
            res = await fetch('/api/analytics/top-pages?period=' + state.period, { signal });
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            cardNote(ui.topPages, 'Could not load page data.');
            return;
        }
        if (halted(res, data)) return;

        if (!data.success || !data.pages || !data.pages.length) {
            cardEmpty(ui.topPages, 'file-earmark-text', 'No page views in this period yet.');
            topPages = [];
            return;
        }

        topPages = data.pages;
        paintTopPages();
    }

    function paintTopPages() {
        const host = ui.topPages;
        if (!host) return;
        host.textContent = '';
        host.setAttribute('aria-busy', 'false');

        // Share is of the period's total page views, which /overview reports
        // directly. Dividing by the sum of these ten rows instead would make
        // every share too big — the top ten are not the whole site.
        const total = state.totals ? Number(state.totals.page_views) || 0 : 0;

        topPages.forEach((page) => {
            const row = el('div', 'an-row');

            // A GA4 page title can be empty; the path is the fallback identity.
            row.appendChild(el('span', 'an-row-title', page.title || page.path || '—'));
            row.appendChild(el('span', 'an-row-path', page.path || ''));

            const figures = el('span', 'an-row-figures');
            const views = el('span', 'an-row-views', compact(page.views));
            views.title = grouped(page.views) + ' views';
            figures.appendChild(views);
            figures.appendChild(el('span', 'an-row-time', duration(page.avg_time)));
            figures.appendChild(el('span', 'an-row-share',
                total ? Math.round(pct(page.views, total)) + '%' : '—'));
            row.appendChild(figures);

            const meter = el('span', 'an-row-meter');
            const fill = el('span', 'an-row-meter-fill');
            // Scaled against the top row, not against the total: at 3% of a
            // site's traffic every bar would be an invisible sliver and the
            // meter would stop comparing anything.
            const top = Number(topPages[0].views) || 1;
            fill.style.width = Math.max(2, pct(page.views, top)) + '%';
            meter.appendChild(fill);
            row.appendChild(meter);

            host.appendChild(row);
        });
    }

    // The share column depends on /overview's total, which can land after the
    // rows do. Repaint rather than leave a row of em-dashes.
    function paintTopPagesShares() {
        if (topPages.length) paintTopPages();
    }

    // --- Traffic sources ------------------------------------------------

    const SOURCE_ROWS = 6;

    async function fetchSources() {
        let res, data;
        try {
            res = await fetch('/api/analytics/traffic-sources?period=' + state.period, { signal });
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            cardNote(ui.sources, 'Could not load channel data.');
            return;
        }
        if (halted(res, data)) return;

        if (!data.success || !data.sources || !data.sources.length) {
            cardEmpty(ui.sources, 'signpost-split', 'No sessions in this period yet.');
            return;
        }
        paintSources(data.sources, Number(data.total_sessions) || 0);
    }

    function paintSources(sources, total) {
        const host = ui.sources;
        if (!host) return;
        host.textContent = '';
        host.setAttribute('aria-busy', 'false');

        // Past six rows the tail folds into one "Other" — never into more
        // hues. Every bar is the same colour anyway; this is about the list
        // staying readable, and about the folded rows still being counted.
        const shown = sources.slice(0, SOURCE_ROWS);
        const tail = sources.slice(SOURCE_ROWS);
        if (tail.length) {
            shown.push({
                channel: tail.length + ' other channels',
                sessions: tail.reduce((a, s) => a + (Number(s.sessions) || 0), 0),
                users: tail.reduce((a, s) => a + (Number(s.users) || 0), 0),
                other: true
            });
        }

        const top = Math.max.apply(null, shown.map((s) => Number(s.sessions) || 0)) || 1;
        const whole = total || shown.reduce((a, s) => a + (Number(s.sessions) || 0), 0);

        shown.forEach((src) => {
            const row = el('div', 'an-bar-row' + (src.other ? ' is-other' : ''));
            row.appendChild(el('span', 'an-bar-label', src.channel));

            const value = el('span', 'an-bar-value', compact(src.sessions));
            value.title = grouped(src.sessions) + ' sessions · ' + grouped(src.users) + ' users';
            row.appendChild(value);

            row.appendChild(el('span', 'an-bar-share',
                whole ? Math.round(pct(src.sessions, whole)) + '%' : '—'));

            const track = el('span', 'an-bar-track');
            const fill = el('span', 'an-bar-fill');
            fill.style.width = pct(src.sessions, top) + '%';
            track.appendChild(fill);
            row.appendChild(track);

            host.appendChild(row);
        });
    }

    // --- Card-level notes ----------------------------------------------

    function cardNote(host, text) {
        if (!host) return;
        host.textContent = '';
        host.setAttribute('aria-busy', 'false');
        const note = el('div', 'an-note');
        note.appendChild(el('i', 'bi bi-exclamation-triangle'));
        note.appendChild(el('span', null, text));
        host.appendChild(note);
    }

    function cardEmpty(host, icon, text) {
        if (!host) return;
        host.textContent = '';
        host.setAttribute('aria-busy', 'false');
        const empty = el('div', 'list-empty');
        const medallion = el('span', 'list-empty-icon');
        medallion.appendChild(el('i', 'bi bi-' + icon));
        empty.appendChild(medallion);
        empty.appendChild(el('p', null, text));
        host.appendChild(empty);
    }

    // --- Period + metric switching --------------------------------------

    function setChartCaption() {
        if (ui.chartTitle) ui.chartTitle.textContent = METRICS[state.metric];
        if (ui.chartRange) {
            ui.chartRange.textContent = state.granularity === 'hour'
                ? '· today, by hour'
                : '· ' + PERIOD_LABEL[state.period] + ', by day';
        }
    }

    $$('.an-kpi').forEach((tile) => {
        tile.addEventListener('click', () => {
            if (tile.dataset.metric === state.metric) return;
            state.metric = tile.dataset.metric;

            $$('.an-kpi').forEach((other) => {
                const on = other === tile;
                other.classList.toggle('is-active', on);
                other.setAttribute('aria-pressed', on ? 'true' : 'false');
            });

            setChartCaption();
            blurPoint();
            // No refetch: every series came down in the same payload, so
            // switching metric is a repaint.
            drawChart();
        }, { signal });
    });

    $$('.an-filters .seg-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
            if (tab.dataset.period === state.period || state.dead) return;
            state.period = tab.dataset.period;

            $$('.an-filters .seg-tab').forEach((other) => {
                const on = other === tab;
                other.classList.toggle('is-active', on);
                other.setAttribute('aria-selected', on ? 'true' : 'false');
            });

            loadPeriod();
        }, { signal });
    });

    function loadPeriod() {
        // Hold the previous render at reduced opacity while the next slice
        // loads: no skeleton, no layout jump, no flash of empty cards.
        const cards = $$('.an-card, .an-kpi');
        cards.forEach((c) => c.classList.add('an-stale'));

        setChartCaption();
        blurPoint();

        Promise.all([fetchOverview(), fetchSeries(), fetchTopPages(), fetchSources()])
            .then(() => {
                if (signal.aborted) return;
                // Not if the connection died during the load. goDead() dims the
                // screen precisely to say "these figures are no longer current",
                // and this ran afterwards and brightened them all back up.
                if (state.dead) return;
                cards.forEach((c) => c.classList.remove('an-stale'));
            });
    }

    // --- Boot -----------------------------------------------------------

    setChartCaption();
    fetchRealtime();
    loadPeriod();
    every(30000, fetchRealtime);
})();
