/**
 * Optimization — optimize · reports · draft keywords · URL metrics · domain
 * keywords.
 *
 * The previous version declared eight globals (analyzeUrl, runSiteAudit,
 * deleteReport, toggleReportMenu…) so the template could call them from inline
 * onclick attributes, and registered its `document`-level listeners at module
 * scope. PJAX re-injects this file on every visit to /optimization, so those
 * listeners accumulated: the fifth visit had five copies of the "close the
 * report menu" handler bound, and nothing ever removed them.
 *
 * Everything here is one IIFE, every listener goes through an AbortController
 * the next run aborts, and every control is reached by delegation off
 * .dashboard-main rather than by name.
 *
 * It also drops ~115 lines that re-implemented a <select> as click-only divs
 * over a `display: none` native control — which meant the draft and country
 * pickers could not be operated by keyboard at all. They are .select-pill now,
 * driven by the SelectPill module in app.js.
 */

(function optimizationPage() {
    'use strict';

    if (window.__optimizationAbort) {
        try { window.__optimizationAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__optimizationAbort = controller;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const $ = (sel, scope) => (scope || root).querySelector(sel);
    const $$ = (sel, scope) => Array.from((scope || root).querySelectorAll(sel));

    const TABS = ['optimize', 'reports', 'draft-keywords', 'url-metrics', 'domain-keywords'];

    const state = {
        tab: 'optimize',
        draftsLoaded: false,
        reportsLoaded: false,
        reports: [],
        lastRun: null,        // the payload of the optimize run on screen
        pendingDeleteId: null,
        pendingExportId: null
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does. NOT the
    // `div.textContent -> div.innerHTML` trick this file used to use: that
    // encodes &, < and > and leaves both quote characters alone, which is safe
    // for text and unsafe the moment the value lands in title="…" or
    // aria-label="…" — and every row below builds attributes.
    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    function bsModal(id) {
        const el = document.getElementById(id);
        if (!el || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(el);
    }

    function num(value, fallback) {
        const n = Number(value);
        return Number.isFinite(n) ? n : (fallback === undefined ? 0 : fallback);
    }

    // Compact for display, exact on the element's title — the compact form is
    // unparseable, so anything that needs to read the figure back reads the
    // attribute.
    function compact(value) {
        if (value === null || value === undefined || value === '') return null;
        const n = Number(value);
        if (!Number.isFinite(n)) return null;
        if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return n.toLocaleString();
    }

    function formatDate(value) {
        if (!value) return '';
        const d = new Date(value);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    // snake_case / camelCase -> "Sentence case"
    function humanise(key) {
        return String(key)
            .replace(/_/g, ' ')
            .replace(/([a-z])([A-Z])/g, '$1 $2')
            .replace(/^./, (s) => s.toUpperCase());
    }

    function gradeClass(grade) {
        const letter = String(grade || '').trim().charAt(0).toLowerCase();
        return 'abcdf'.includes(letter) ? 'grade-' + letter : '';
    }

    function setState(body, next, message) {
        const el = typeof body === 'string' ? $('[data-body="' + body + '"]') : body;
        if (!el) return;
        el.dataset.state = next;
        if (next === 'error') {
            const text = $('[data-error-text]', el);
            if (text) text.textContent = message || 'Something went wrong.';
        }
    }

    function busy(btn, on, label) {
        if (!btn) return;
        if (on) {
            if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> ' + esc(label || 'Working…');
        } else {
            btn.disabled = false;
            if (btn.dataset.label) { btn.innerHTML = btn.dataset.label; delete btn.dataset.label; }
        }
    }

    // Reads the error the API actually sent rather than reporting every failure
    // as a connection problem — a 429 and a dropped socket are different
    // things and only one of them is worth retrying immediately.
    async function requestJson(url, options) {
        const res = await fetch(url, Object.assign({ signal }, options || {}));
        let payload = null;
        try { payload = await res.json(); } catch (e) { payload = null; }
        if (!res.ok || !payload || payload.success === false) {
            const err = new Error((payload && payload.error) || 'The request failed (' + res.status + ').');
            err.handled = true;
            throw err;
        }
        return payload;
    }

    function describeError(err) {
        if (err && err.name === 'AbortError') return null;   // navigated away
        if (err && err.handled) return err.message;
        return 'Could not reach the server. Check your connection and try again.';
    }

    // ----------------------------------------------------------------------
    // Tabs
    //
    // The tab is written to the hash, so a reload, a bookmark or the back
    // button returns to the panel you were on instead of always landing on the
    // first one.
    // ----------------------------------------------------------------------

    function showTab(name, pushHash) {
        if (TABS.indexOf(name) === -1) name = TABS[0];
        state.tab = name;

        $$('.opt-tabs .seg-tab').forEach((tab) => {
            const on = tab.dataset.tab === name;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        $$('[data-tab-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.tabPanel !== name;
        });

        if (pushHash !== false) {
            try { history.replaceState(null, '', '#' + name); } catch (e) { /* file:// etc. */ }
        }

        if (name === 'optimize' || name === 'draft-keywords') loadDrafts();
        if (name === 'reports') loadReports();
    }

    // ----------------------------------------------------------------------
    // Stat tiles
    //
    // Re-derived in the browser from the report list once it has been fetched,
    // and only then: the server rendered these figures from the same data, and
    // an empty cache before the first fetch would zero out numbers that were
    // already correct.
    // ----------------------------------------------------------------------

    function sparkline(values, label) {
        // PAD clears the endpoint marker, not just the line: the dot is r3.5
        // with a 2px ring, so it reaches 4.5px past its centre.
        const W = 116, H = 40, PAD = 6;
        const n = values.length;
        if (n < 2) return '';

        const max = Math.max.apply(null, values);
        const min = Math.min.apply(null, values);
        const span = max - min || 1;
        const stepX = (W - PAD * 2) / (n - 1);

        const pts = values.map((v, i) => {
            const x = PAD + i * stepX;
            // A flat series sits on the baseline rather than halfway up, so
            // "nothing happened" cannot read as "steady at some level".
            const y = max === min
                ? (max === 0 ? H - PAD : H / 2)
                : H - PAD - ((v - min) / span) * (H - PAD * 2);
            return [x, y];
        });

        const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
        const area = line + ' L' + pts[n - 1][0].toFixed(1) + ' ' + (H - PAD) +
            ' L' + pts[0][0].toFixed(1) + ' ' + (H - PAD) + ' Z';
        const last = pts[n - 1];

        // A micro-chart has no room for axes, so the whole series gets a text
        // equivalent rather than depending on a tooltip nobody may reach.
        const summary = label + ': ' + n + ' runs, from ' + min + ' to ' + max +
            ' points, most recent ' + values[n - 1] + '.';

        return '<svg class="stat-trend" viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
            'aria-label="' + esc(summary) + '">' +
            '<title>' + esc(summary) + '</title>' +
            '<path class="stat-trend-area" d="' + area + '"/>' +
            '<path class="stat-trend-line" d="' + line + '"/>' +
            '<circle class="stat-trend-dot" cx="' + last[0].toFixed(1) + '" cy="' +
            last[1].toFixed(1) + '" r="3.5"/>' +
            '</svg>';
    }

    function gainOf(report) {
        const after = num(report.seo_score);
        const before = num(report.original_score);
        return Math.round(num(report.score_improvement, after - before));
    }

    function refreshStats() {
        if (!state.reportsLoaded) return;

        const reports = state.reports;
        const count = reports.length;
        const gains = reports.map(gainOf);
        const avg = count ? Math.round(gains.reduce((a, b) => a + b, 0) / count) : 0;
        const best = count ? Math.round(Math.max.apply(null, reports.map((r) => num(r.seo_score)))) : 0;

        const runs = $('[data-stat-runs]');
        if (runs) { runs.textContent = String(count); runs.title = String(count); }

        const gain = $('[data-stat-gain]');
        if (gain) { gain.textContent = (avg > 0 ? '+' : '') + avg; gain.title = String(avg); }

        const bestEl = $('[data-stat-best]');
        if (bestEl) { bestEl.textContent = String(best); bestEl.title = String(best); }

        $$('[data-report-count], [data-report-total]').forEach((el) => { el.textContent = String(count); });

        // "How many" plus "when was the last one" — the delta answers
        // "compared to what" as well as a run counter can.
        const runsDelta = $('[data-delta-runs]');
        if (runsDelta) {
            const latest = count ? formatDate(reports[0].timestamp || reports[0].created_at) : '';
            runsDelta.textContent = latest ? 'last run ' + latest : 'none yet';
        }

        const meter = $('[data-meter-best]');
        if (meter) {
            meter.hidden = count === 0;
            const fill = $('[data-meter-fill]', meter);
            const note = $('[data-meter-note]', meter);
            if (fill) fill.style.width = Math.max(0, Math.min(100, best)) + '%';
            if (note) note.textContent = best + ' / 100';
        }

        // Reports arrive newest-first; a trend line reads left to right.
        const trend = $('[data-trend-gain]');
        if (trend) trend.innerHTML = sparkline(gains.slice(0, 12).reverse(), 'Points gained per run');
    }

    // ----------------------------------------------------------------------
    // Drafts — shared by the optimize and keyword pickers
    // ----------------------------------------------------------------------

    function fillPicker(select, drafts, placeholder) {
        const pill = select.closest('[data-select-pill]');
        const menu = pill ? $('.menu', pill) : null;
        const current = select.value;

        select.innerHTML = '<option value="">' + esc(placeholder) + '</option>' +
            drafts.map((d) => '<option value="' + esc(d.id) + '">' +
                esc(d.title || 'Untitled') + '</option>').join('');

        if (menu) {
            menu.innerHTML = '<button type="button" class="menu-item" role="option" data-value="">' +
                '<i class="bi bi-check2 menu-check" aria-hidden="true"></i>' +
                '<span class="menu-label">' + esc(placeholder) + '</span></button>' +
                drafts.map((d) => '<button type="button" class="menu-item" role="option" ' +
                    'data-value="' + esc(d.id) + '">' +
                    '<i class="bi bi-check2 menu-check" aria-hidden="true"></i>' +
                    '<span class="menu-label">' + esc(d.title || 'Untitled') + '</span></button>').join('');
        }

        // A draft that vanished between loads must not leave a stale id behind.
        select.value = drafts.some((d) => d.id === current) ? current : '';
        syncPillLabel(select);
    }

    // The SelectPill module writes through to the <select> and fires `change`;
    // the visible label on the trigger is the page's to keep in step.
    function syncPillLabel(select) {
        const pill = select.closest('[data-select-pill]');
        if (!pill) return;
        const label = $('[data-pill-text]', pill);
        if (!label) return;
        const opt = select.options[select.selectedIndex];
        label.textContent = opt ? opt.textContent : '';
    }

    async function loadDrafts() {
        if (state.draftsLoaded) return;
        state.draftsLoaded = true;   // set first: two tabs can ask at once

        const selects = $$('[data-drafts-for]');
        try {
            const result = await requestJson('/api/seo/drafts');
            const drafts = (result.drafts || []).filter((d) => d && d.id);
            selects.forEach((s) => fillPicker(s, drafts, drafts.length ? 'Select a draft' : 'No drafts yet'));
        } catch (err) {
            state.draftsLoaded = false;   // let the next tab visit retry
            const message = describeError(err);
            if (!message) return;
            selects.forEach((s) => fillPicker(s, [], 'Could not load drafts'));
            toast('error', 'Drafts unavailable', message);
        }
    }

    // ----------------------------------------------------------------------
    // Optimize
    // ----------------------------------------------------------------------

    function selectedDraft() {
        const select = $('#optimizeDraftSelect');
        if (!select || !select.value) return null;
        const opt = select.options[select.selectedIndex];
        return { id: select.value, title: opt ? opt.textContent : 'this draft' };
    }

    function askToOptimize() {
        const draft = selectedDraft();
        if (!draft) {
            toast('warning', 'No draft selected', 'Pick the draft you want optimized first.');
            const trigger = $('#optimizeDraftSelect');
            const pill = trigger && trigger.closest('[data-select-pill]');
            if (pill) { const t = $('[data-select-trigger]', pill); if (t) t.focus(); }
            return;
        }

        const region = $('#optimizeRegionSelect');
        const regionName = region && region.options[region.selectedIndex]
            ? region.options[region.selectedIndex].textContent : 'your region';

        const target = $('[data-confirm-draft]');
        if (target) target.textContent = draft.title;
        const regionEl = $('[data-confirm-region]');
        if (regionEl) regionEl.textContent = regionName;

        const modal = bsModal('optimizeConfirmModal');
        if (modal) modal.show();
        else runOptimize();   // no Bootstrap: do not strand the only path to the action
    }

    async function runOptimize() {
        const draft = selectedDraft();
        if (!draft) return;

        const region = $('#optimizeRegionSelect');
        const btn = $('.opt-run[data-action="optimize"]');

        busy(btn, true, 'Optimizing…');
        setState('optimize', 'loading');

        try {
            const result = await requestJson('/api/seo/optimize-blog/' + encodeURIComponent(draft.id), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ region: region ? region.value : 'US' })
            });

            result.blog_title = result.blog_title || draft.title;
            renderOptimizeResult(result);
            toast('success', 'Optimization complete', 'The draft has been rewritten and saved.');

            // The run just wrote a report; the cache and every figure derived
            // from it are now stale.
            state.reportsLoaded = false;
            loadReports();

        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            setState('optimize', 'error', message);
            toast('error', 'Optimization failed', message);
        } finally {
            busy(btn, false);
        }
    }

    const BREAKDOWN_LABELS = {
        content: 'Content', headings: 'Headings', keywords: 'Keywords', meta: 'Meta tags',
        readability: 'Readability', structure: 'Structure', links: 'Links', images: 'Images'
    };

    // Direction is carried by the arrow glyph, the sign and the title text as
    // well as by colour — the reserved status hues never mean anything on
    // their own.
    function deltaParts(delta) {
        return {
            cls: delta > 0 ? 'is-up' : delta < 0 ? 'is-down' : 'is-flat',
            arrow: delta > 0 ? '↑' : delta < 0 ? '↓' : '→',
            word: delta > 0 ? 'up' : delta < 0 ? 'down' : 'no change',
            text: delta === 0 ? 'no change' : (delta > 0 ? '+' : '') + delta
        };
    }

    function deltaMarkup(delta, className) {
        const p = deltaParts(delta);
        return '<span class="' + className + ' ' + p.cls + '" title="' + esc(p.word) + '">' +
            '<span aria-hidden="true">' + p.arrow + '</span>' + esc(p.text) + '</span>';
    }

    // Paints an existing element rather than replacing it, so the node the rest
    // of the render holds a handle to survives.
    function paintDelta(el, delta, className) {
        if (!el) return;
        const p = deltaParts(delta);
        el.className = className + ' ' + p.cls;
        el.title = p.word;
        el.innerHTML = '<span aria-hidden="true">' + p.arrow + '</span>' + esc(p.text);
    }

    function renderBreakdown(comparison) {
        const card = $('[data-breakdown]');
        const rows = $('[data-breakdown-rows]');
        if (!card || !rows) return;

        const data = (comparison && comparison.breakdown_comparison) || {};
        const keys = Object.keys(data).filter((k) => data[k] && typeof data[k] === 'object');

        if (!keys.length) { card.hidden = true; rows.innerHTML = ''; return; }

        rows.innerHTML = keys.map((key) => {
            const before = Math.round(num(data[key].before));
            const after = Math.round(num(data[key].after));
            const delta = after - before;
            const pct = (v) => Math.max(0, Math.min(100, v));

            return '<div class="opt-meter">' +
                '<span class="opt-meter-label">' + esc(BREAKDOWN_LABELS[key] || humanise(key)) + '</span>' +
                '<span class="opt-meter-track" role="img" aria-label="' +
                esc(humanise(key) + ': ' + before + ' before, ' + after + ' after') + '">' +
                '<span class="opt-meter-fill" style="width:' + pct(after) + '%"></span>' +
                '<span class="opt-meter-tick" style="left:' + pct(before) + '%"></span>' +
                '</span>' +
                '<span class="opt-meter-figures">' + before + ' → <strong>' + after + '</strong>' +
                deltaMarkup(delta, 'opt-score-delta') + '</span>' +
                '</div>';
        }).join('');

        card.hidden = false;
    }

    function primaryKeywordText(value) {
        if (!value) return '—';
        if (typeof value === 'object') return value.keyword || value.term || '—';
        return String(value);
    }

    function changeText(change) {
        if (change && typeof change === 'object') {
            return change.description || change.type || JSON.stringify(change);
        }
        return String(change);
    }

    function renderOptimizeResult(data) {
        state.lastRun = data;

        const after = Math.round(num(data.seo_score));
        const before = Math.round(num(data.original_score));
        const delta = Math.round(num(data.score_improvement, after - before));

        $('[data-score-after]').textContent = after;
        $('[data-score-before]').textContent = before;
        paintDelta($('[data-score-delta]'), delta, 'opt-score-delta');

        const grade = data.seo_grade || '—';
        const pill = $('[data-grade-pill]');
        pill.className = 'opt-grade ' + gradeClass(grade);
        $('[data-grade]').textContent = grade;

        renderBreakdown(data.comparison);

        $('[data-new-title]').textContent = data.new_title || data.blog_title || '—';
        $('[data-primary-kw]').textContent = primaryKeywordText(data.primary_keyword);

        const changes = Array.isArray(data.changes_made) ? data.changes_made : [];
        const changesCard = $('[data-changes-card]');
        $('[data-changes-list]').innerHTML = changes.map((c) => '<li>' + esc(changeText(c)) + '</li>').join('');
        changesCard.hidden = changes.length === 0;

        const recs = Array.isArray(data.recommendations) ? data.recommendations : [];
        const recsCard = $('[data-recs-card]');
        $('[data-recs-list]').innerHTML = recs.map((r) => '<li>' + esc(String(r)) + '</li>').join('');
        recsCard.hidden = recs.length === 0;

        setState('optimize', 'results');
    }

    // ----------------------------------------------------------------------
    // Reports
    // ----------------------------------------------------------------------

    async function loadReports() {
        if (state.reportsLoaded) return;

        const body = $('[data-body="reports"]');
        if (body && body.dataset.state !== 'results') setState(body, 'loading');

        try {
            const result = await requestJson('/api/optimization/reports');
            state.reports = Array.isArray(result.reports) ? result.reports : [];
            state.reportsLoaded = true;
            renderReports();
            refreshStats();
        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            setState('reports', 'error', message);
        }
    }

    function renderReports() {
        const rows = $('[data-report-rows]');
        if (!rows) return;

        if (!state.reports.length) {
            rows.innerHTML = '';
            setState('reports', 'empty');
            return;
        }

        rows.innerHTML = state.reports.map((report) => {
            const title = report.new_title || report.blog_title || 'Untitled blog';
            const grade = report.seo_grade || '?';
            const after = Math.round(num(report.seo_score));
            const before = Math.round(num(report.original_score));
            const delta = gainOf(report);
            const when = formatDate(report.timestamp || report.created_at);
            const keyword = primaryKeywordText(report.primary_keyword);
            const id = esc(report.id || '');

            const meta = [when, keyword !== '—' ? keyword : '']
                .filter(Boolean)
                .map((part) => '<span>' + esc(part) + '</span>')
                .join('<span class="row-sep">·</span>');

            return '<div class="data-row report-row" data-report-id="' + id + '">' +
                '<span class="row-mark ' + gradeClass(grade) + '" aria-hidden="true">' +
                esc(String(grade).charAt(0)) + '</span>' +

                '<button type="button" class="row-open" data-action="report-detail" data-id="' + id + '">' +
                '<span class="row-title">' + esc(title) + '</span>' +
                '<span class="row-meta">' + meta + '</span>' +
                '</button>' +

                '<div class="report-trail">' +
                '<span class="report-score" title="' +
                esc('SEO score ' + before + ' before, ' + after + ' after — grade ' + grade) + '">' +
                before + ' <span aria-hidden="true">→</span> ' +
                '<span class="report-score-after">' + after + '</span>' +
                deltaMarkup(delta, 'report-score-delta') +
                '</span>' +
                '<button type="button" class="opt-row-btn" data-action="report-export" data-id="' + id + '" ' +
                'aria-label="' + esc('Export the report for ' + title) + '" title="Export report">' +
                '<i class="bi bi-download" aria-hidden="true"></i></button>' +
                '<button type="button" class="opt-row-btn is-danger" data-action="report-delete" data-id="' + id + '" ' +
                'aria-label="' + esc('Delete the report for ' + title) + '" title="Delete report">' +
                '<i class="bi bi-trash3" aria-hidden="true"></i></button>' +
                '</div>' +
                '</div>';
        }).join('');

        setState('reports', 'results');
    }

    function reportById(id) {
        return state.reports.find((r) => String(r.id) === String(id)) || null;
    }

    function openReportDetail(id) {
        const report = reportById(id);
        if (!report) return;

        state.pendingExportId = id;

        const after = Math.round(num(report.seo_score));
        const before = Math.round(num(report.original_score));
        const delta = gainOf(report);
        const grade = report.seo_grade || '—';
        const kw = report.primary_keyword || {};
        const when = formatDate(report.timestamp || report.created_at);

        $('[data-detail-title]').textContent = report.new_title || report.blog_title || 'Report';

        const facts = [
            ['Primary keyword', primaryKeywordText(report.primary_keyword)],
            ['Search volume', (compact(kw.search_volume) || '—')],
            ['Difficulty', kw.difficulty_score !== undefined && kw.difficulty_score !== null
                ? Math.round(num(kw.difficulty_score)) + ' / 100' : '—'],
            ['CPC', kw.cpc !== undefined && kw.cpc !== null ? '$' + num(kw.cpc).toFixed(2) : '—']
        ];

        const changes = Array.isArray(report.changes_made) ? report.changes_made : [];
        const recs = Array.isArray(report.recommendations) ? report.recommendations : [];

        let html = '<p class="opt-detail-meta">' +
            (when ? '<span>' + esc(when) + '</span>' : '') +
            '<span class="report-score">' + before + ' <span aria-hidden="true">→</span> ' +
            '<span class="report-score-after">' + after + '</span>' +
            deltaMarkup(delta, 'report-score-delta') + '</span>' +
            '<span class="report-score">Grade ' + esc(grade) + '</span>' +
            '</p>';

        html += '<div class="opt-detail-section"><div class="opt-facts">' +
            facts.map((f) => '<div class="opt-fact"><span class="opt-fact-label">' + esc(f[0]) +
                '</span><span class="opt-fact-value">' + esc(f[1]) + '</span></div>').join('') +
            '</div></div>';

        if (changes.length) {
            html += '<div class="opt-detail-section"><h3 class="opt-subhead">Changes made</h3>' +
                '<ul class="opt-list opt-list-done">' +
                changes.map((c) => '<li>' + esc(changeText(c)) + '</li>').join('') +
                '</ul></div>';
        }

        if (recs.length) {
            html += '<div class="opt-detail-section"><h3 class="opt-subhead">Go further</h3>' +
                '<ul class="opt-list opt-list-todo">' +
                recs.map((r) => '<li>' + esc(String(r)) + '</li>').join('') +
                '</ul></div>';
        }

        $('[data-detail-body]').innerHTML = html;

        const modal = bsModal('reportDetailModal');
        if (modal) modal.show();
    }

    function askToDelete(id) {
        const report = reportById(id);
        if (!report) return;
        state.pendingDeleteId = id;
        const label = $('[data-delete-title]');
        if (label) label.textContent = report.new_title || report.blog_title || 'Untitled blog';
        const modal = bsModal('reportDeleteModal');
        if (modal) modal.show();
    }

    async function deleteReport() {
        const id = state.pendingDeleteId;
        if (!id) return;
        state.pendingDeleteId = null;

        const modal = bsModal('reportDeleteModal');
        if (modal) modal.hide();

        try {
            await requestJson('/api/optimization/reports/' + encodeURIComponent(id), { method: 'DELETE' });
            state.reports = state.reports.filter((r) => String(r.id) !== String(id));
            renderReports();
            refreshStats();
            toast('success', 'Report deleted', 'The record of that run has been removed.');
        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            toast('error', 'Could not delete', message);
        }
    }

    // ----------------------------------------------------------------------
    // Draft keywords
    // ----------------------------------------------------------------------

    function difficultyMarkup(value) {
        if (value === null || value === undefined || value === '') return '—';
        const n = Math.round(num(value));
        const band = n >= 70 ? ['is-hard', 'Hard'] : n >= 40 ? ['is-medium', 'Medium'] : ['is-easy', 'Easy'];
        // The word is the label; the hue and the bar only reinforce it.
        return '<span class="kw-difficulty ' + band[0] + '">' +
            '<span class="kw-difficulty-track">' +
            '<span class="kw-difficulty-fill" style="width:' + Math.max(0, Math.min(100, n)) + '%"></span>' +
            '</span>' +
            '<span class="kw-difficulty-word">' + band[1] + '</span>' +
            '<span>' + n + '</span>' +
            '</span>';
    }

    // Compact in the cell, exact on its title — "1.2K" is unreadable as a
    // precise figure and unparseable as a value.
    function cell(value, extraClass) {
        const cls = 'is-num' + (extraClass ? ' ' + extraClass : '');
        const shown = compact(value);
        if (shown === null) return '<td class="' + cls + '">—</td>';
        return '<td class="' + cls + '" title="' + esc(Number(value).toLocaleString()) + '">' +
            esc(shown) + '</td>';
    }

    async function runKeywords() {
        const select = $('#draftSelect');
        const country = $('#countrySelect');
        const btn = $('.opt-run[data-action="keywords"]');

        if (!select || !select.value) {
            toast('warning', 'No draft selected', 'Pick the draft you want researched first.');
            return;
        }

        busy(btn, true, 'Researching…');
        setState('keywords', 'loading');

        try {
            const result = await requestJson('/api/optimization/draft-keywords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ blog_id: select.value, country: country ? country.value : 'us' })
            });

            const data = result.data || {};
            const keywords = Array.isArray(data.keywords) ? data.keywords : [];

            const source = $('[data-kw-source]');
            const countryName = country && country.options[country.selectedIndex]
                ? country.options[country.selectedIndex].textContent : '';
            source.innerHTML = '<i class="bi bi-file-earmark-text" aria-hidden="true"></i> ' +
                'Keywords found in <strong>' + esc(data.blog_title || 'this draft') + '</strong>' +
                (countryName ? ', measured for ' + esc(countryName) : '');

            if (!keywords.length) {
                $('[data-kw-rows]').innerHTML =
                    '<tr class="opt-table-empty"><td colspan="6">No keywords could be extracted from this draft.</td></tr>';
            } else {
                $('[data-kw-rows]').innerHTML = keywords.map((kw) => {
                    if (kw.error) {
                        return '<tr class="opt-table-error"><td class="opt-table-keyword">' +
                            esc(kw.keyword || '') + '</td><td colspan="5">Metrics unavailable</td></tr>';
                    }
                    return '<tr>' +
                        '<td class="opt-table-keyword">' + esc(kw.keyword || '') + '</td>' +
                        cell(kw.searchVolume) +
                        '<td>' + difficultyMarkup(kw.difficulty) + '</td>' +
                        '<td class="is-num opt-col-wide">' +
                        (kw.cpc === null || kw.cpc === undefined ? '—' : '$' + num(kw.cpc).toFixed(2)) + '</td>' +
                        cell(kw.clicks, 'opt-col-wide') +
                        cell(kw.trafficPotential, 'opt-col-wide') +
                        '</tr>';
                }).join('');
            }

            setState('keywords', 'results');

        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            setState('keywords', 'error', message);
            toast('error', 'Research failed', message);
        } finally {
            busy(btn, false);
        }
    }

    // ----------------------------------------------------------------------
    // URL metrics
    // ----------------------------------------------------------------------

    const URL_METRICS = [
        { key: 'domainRating', alts: ['domain_rating', 'dr'], label: 'Domain rating', ratio: true },
        { key: 'urlRating', alts: ['url_rating', 'ur'], label: 'URL rating', ratio: true },
        { key: 'backlinks', alts: ['total_backlinks', 'backlink'], label: 'Backlinks', note: 'links pointing here' },
        { key: 'refDomains', alts: ['referring_domains', 'ref_domains'], label: 'Referring domains', note: 'unique sites' },
        { key: 'traffic', alts: ['organic_traffic', 'org_traffic'], label: 'Organic traffic', note: 'visits per month' },
        { key: 'organicKeywords', alts: ['organic_keywords', 'keywords'], label: 'Organic keywords', note: 'terms ranked for' }
    ];

    function pick(data, def) {
        const keys = [def.key].concat(def.alts || []);
        for (let i = 0; i < keys.length; i++) {
            const v = data[keys[i]];
            if (v !== undefined && v !== null && v !== '') return v;
        }
        return null;
    }

    async function runUrlMetrics() {
        const input = $('#urlInput');
        const btn = $('.opt-run[data-action="url"]');
        const url = input ? input.value.trim() : '';

        if (!url) {
            toast('warning', 'Missing URL', 'Enter the page you want analyzed.');
            if (input) input.focus();
            return;
        }

        busy(btn, true, 'Analyzing…');
        setState('url', 'loading');

        try {
            const result = await requestJson('/api/optimization/url-metrics?url=' + encodeURIComponent(url));
            const data = result.data || {};

            $('[data-url-source]').innerHTML = '<i class="bi bi-link-45deg" aria-hidden="true"></i> ' +
                'Metrics for <strong>' + esc(url) + '</strong>' +
                (result.cached ? ' <span class="opt-raw-count">(cached)</span>' : '');

            const consumed = {};
            $('[data-url-metrics]').innerHTML = URL_METRICS.map((def) => {
                [def.key].concat(def.alts || []).forEach((k) => { consumed[k] = true; });

                const raw = pick(data, def);
                const shown = compact(raw);
                const missing = shown === null;
                const value = missing ? 'Not reported' : shown;
                // A rating out of 100 is a ratio, so it gets a track. A count
                // has no ceiling to draw one against.
                const meter = (!missing && def.ratio)
                    ? '<span class="opt-metric-track"><span class="opt-metric-fill" style="width:' +
                    Math.max(0, Math.min(100, num(raw))) + '%"></span></span>'
                    : '';

                return '<div class="opt-metric' + (missing ? ' is-missing' : '') + '">' +
                    '<span class="opt-metric-label">' + esc(def.label) + '</span>' +
                    '<span class="opt-metric-value"' +
                    (missing ? '' : ' title="' + esc(Number(raw).toLocaleString()) + '"') + '>' +
                    esc(value) + '</span>' +
                    '<span class="opt-metric-note">' + esc(def.ratio ? 'out of 100' : (def.note || '')) + '</span>' +
                    meter + '</div>';
            }).join('');

            // Everything the tiles did not claim. This used to be printed as a
            // flat grid directly under them — a debug view shipped as UI.
            const extras = Object.keys(data).filter((key) => {
                if (consumed[key]) return false;
                const v = data[key];
                return v !== null && v !== undefined && v !== '' && typeof v !== 'object';
            });

            const raw = $('[data-url-raw]');
            raw.hidden = extras.length === 0;
            raw.open = false;
            $('[data-url-raw-count]').textContent = extras.length ? '(' + extras.length + ')' : '';
            $('[data-url-raw-grid]').innerHTML = extras.map((key) =>
                '<div class="opt-raw-row"><span class="opt-raw-key">' + esc(humanise(key)) + '</span>' +
                '<span class="opt-raw-value">' + esc(String(data[key])) + '</span></div>').join('');

            setState('url', 'results');

        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            setState('url', 'error', message);
            toast('error', 'Analysis failed', message);
        } finally {
            busy(btn, false);
        }
    }

    // ----------------------------------------------------------------------
    // Domain keywords
    //
    // The provider's response shape varies — sometimes an array of rows,
    // sometimes an object wrapping one, sometimes neither — so the table is
    // built from whatever keys come back and the scalar leftovers are shown
    // beside it rather than dropped.
    // ----------------------------------------------------------------------

    function renderDomainTable(rows) {
        const head = $('[data-domain-head]');
        const body = $('[data-domain-rows]');

        if (!rows || !rows.length) {
            head.innerHTML = '';
            body.innerHTML = '<tr class="opt-table-empty"><td>No keywords found for this domain.</td></tr>';
            return;
        }

        // Union of every row's keys, not just the first row's — a provider that
        // omits an empty field on row 1 used to drop that column entirely.
        const keys = [];
        rows.forEach((row) => {
            Object.keys(row || {}).forEach((k) => {
                if (keys.indexOf(k) === -1 && typeof row[k] !== 'object') keys.push(k);
            });
        });

        head.innerHTML = '<tr>' + keys.map((k) =>
            '<th scope="col">' + esc(humanise(k)) + '</th>').join('') + '</tr>';

        body.innerHTML = rows.map((row) => '<tr>' + keys.map((k, i) => {
            const v = row ? row[k] : null;
            const text = (v === null || v === undefined || v === '') ? '—' : String(v);
            return '<td' + (i === 0 ? ' class="opt-table-keyword"' : '') + '>' + esc(text) + '</td>';
        }).join('') + '</tr>').join('');
    }

    function renderDomainExtras(fields) {
        const el = $('[data-domain-extra]');
        const keys = Object.keys(fields);
        el.hidden = keys.length === 0;
        el.innerHTML = keys.map((k) =>
            '<div class="opt-raw-row"><span class="opt-raw-key">' + esc(humanise(k)) + '</span>' +
            '<span class="opt-raw-value">' + esc(String(fields[k])) + '</span></div>').join('');
    }

    async function runDomain() {
        const input = $('#auditDomainInput');
        const btn = $('.opt-run[data-action="domain"]');
        const domain = input ? input.value.trim() : '';

        if (!domain) {
            toast('warning', 'Missing domain', 'Enter the domain you want to look up.');
            if (input) input.focus();
            return;
        }

        busy(btn, true, 'Looking up…');
        setState('domain', 'loading');

        try {
            const result = await requestJson('/api/optimization/site-audit?domain=' + encodeURIComponent(domain));
            const data = result.data;

            $('[data-domain-source]').innerHTML = '<i class="bi bi-globe2" aria-hidden="true"></i> ' +
                'Keywords <strong>' + esc(domain) + '</strong> ranks for' +
                (result.cached ? ' <span class="opt-raw-count">(cached)</span>' : '');

            if (Array.isArray(data)) {
                renderDomainTable(data);
                renderDomainExtras({});
            } else if (data && typeof data === 'object') {
                let rows = null;
                const extras = {};
                Object.keys(data).forEach((key) => {
                    const v = data[key];
                    if (Array.isArray(v) && v.length && typeof v[0] === 'object') rows = v;
                    else if (v !== null && v !== undefined && v !== '' && typeof v !== 'object') extras[key] = v;
                });
                renderDomainTable(rows || []);
                renderDomainExtras(extras);
            } else {
                setState('domain', 'error', 'The provider returned a response this screen cannot read.');
                return;
            }

            setState('domain', 'results');

        } catch (err) {
            const message = describeError(err);
            if (!message) return;
            setState('domain', 'error', message);
            toast('error', 'Lookup failed', message);
        } finally {
            busy(btn, false);
        }
    }

    // ----------------------------------------------------------------------
    // Export — a standalone HTML document
    //
    // Detached from the app, so it carries its own literal palette rather than
    // the token layer: it is opened from the filesystem, where no stylesheet
    // of ours is loaded. The values are the light-theme tokens.
    // ----------------------------------------------------------------------

    const REPORT_CSS = [
        '*{margin:0;padding:0;box-sizing:border-box}',
        'body{font-family:"Google Sans",Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;',
        'background:#F0F4F9;color:#202124;line-height:1.6;padding:24px}',
        '.report{max-width:820px;margin:0 auto;background:#fff;border-radius:20px;',
        'box-shadow:0 1px 3px rgba(60,64,67,.12);overflow:hidden}',
        '.header{padding:32px;border-bottom:1px solid #E8EAED}',
        '.header .eyebrow{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#5F6368}',
        '.header h1{font-size:28px;font-weight:500;margin:4px 0 8px;letter-spacing:-.01em}',
        '.header .date{font-size:13px;color:#5F6368}',
        '.scores{display:flex;gap:16px;padding:24px 32px;flex-wrap:wrap}',
        '.score{flex:1;min-width:150px;padding:16px 20px;border-radius:16px;background:#F0F4F9}',
        '.score .label{display:block;font-size:12px;color:#5F6368}',
        '.score .value{display:block;font-size:34px;font-weight:400;line-height:1.1;color:#1F1F1F}',
        '.score .sub{display:block;font-size:12px;color:#80868B}',
        '.score.up .value{color:#1E8E3E}.score.down .value{color:#D93025}',
        '.section{padding:24px 32px;border-top:1px solid #E8EAED}',
        '.section h2{font-size:16px;font-weight:500;margin-bottom:16px;color:#1F1F1F}',
        'table{width:100%;border-collapse:collapse;font-size:14px}',
        'th{background:#F0F4F9;padding:10px 14px;text-align:left;font-size:11px;font-weight:500;',
        'text-transform:uppercase;letter-spacing:.04em;color:#5F6368}',
        'td{padding:10px 14px;border-top:1px solid #E8EAED;vertical-align:top}',
        '.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}',
        '.fact{background:#F0F4F9;border-radius:12px;padding:14px 16px}',
        '.fact .label{font-size:12px;color:#5F6368}',
        '.fact .value{font-size:15px;font-weight:500;color:#1F1F1F}',
        'ul{list-style:none}li{padding:10px 0 10px 22px;position:relative;border-bottom:1px solid #E8EAED;font-size:14px}',
        'li:last-child{border-bottom:none}li::before{content:"\\2022";position:absolute;left:4px;color:#0B57D0}',
        '.pos{color:#1E8E3E;font-weight:500}.neg{color:#D93025;font-weight:500}.flat{color:#5F6368}',
        '.footer{padding:20px 32px;border-top:1px solid #E8EAED;font-size:12px;color:#80868B;text-align:center}',
        '@media print{body{background:#fff;padding:0}.report{box-shadow:none;border-radius:0}}'
    ].join('');

    function exportReport(report) {
        if (!report) return;

        const after = Math.round(num(report.seo_score));
        const before = Math.round(num(report.original_score));
        const delta = Math.round(num(report.score_improvement, after - before));
        const title = report.new_title || report.blog_title || 'Optimized blog';
        const grade = report.seo_grade || 'N/A';
        const kw = report.primary_keyword || {};
        const when = formatDate(report.timestamp || report.created_at) ||
            new Date().toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });

        const deltaClass = delta > 0 ? 'up' : delta < 0 ? 'down' : '';
        const deltaText = (delta > 0 ? '+' : '') + delta;

        const facts = [
            ['Primary keyword', primaryKeywordText(report.primary_keyword)],
            ['Search volume', Number(num(kw.search_volume)).toLocaleString()],
            ['Difficulty', Math.round(num(kw.difficulty_score)) + ' / 100'],
            ['CPC', '$' + num(kw.cpc).toFixed(2)],
            ['SEO grade', grade]
        ];

        const changes = Array.isArray(report.changes_made) ? report.changes_made : [];
        const changeRows = changes.map((c) => {
            if (c && typeof c === 'object') {
                return '<tr><td>' + esc(c.type || '—') + '</td><td>' + esc(c.description || '') + '</td>' +
                    '<td>' + esc(c.before || '—') + '</td><td>' + esc(c.after || '—') + '</td></tr>';
            }
            return '<tr><td colspan="4">' + esc(String(c)) + '</td></tr>';
        }).join('');

        const breakdown = (report.comparison && report.comparison.breakdown_comparison) || {};
        const breakdownRows = Object.keys(breakdown).map((key) => {
            const b = Math.round(num(breakdown[key].before));
            const a = Math.round(num(breakdown[key].after));
            const d = a - b;
            const cls = d > 0 ? 'pos' : d < 0 ? 'neg' : 'flat';
            const arrow = d > 0 ? '↑ ' : d < 0 ? '↓ ' : '→ ';
            return '<tr><td>' + esc(BREAKDOWN_LABELS[key] || humanise(key)) + '</td><td>' + b +
                '</td><td>' + a + '</td><td class="' + cls + '">' + arrow +
                (d === 0 ? 'no change' : (d > 0 ? '+' : '') + d) + '</td></tr>';
        }).join('');

        const recs = Array.isArray(report.recommendations) ? report.recommendations : [];

        const html = '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">' +
            '<meta name="viewport" content="width=device-width,initial-scale=1">' +
            '<title>SEO report — ' + esc(title) + '</title><style>' + REPORT_CSS + '</style></head><body>' +
            '<div class="report">' +
            '<div class="header"><p class="eyebrow">SEO optimization report</p><h1>' + esc(title) + '</h1>' +
            '<p class="date">' + esc(when) + '</p></div>' +
            '<div class="scores">' +
            '<div class="score"><span class="label">Before</span><span class="value">' + before +
            '</span><span class="sub">SEO score</span></div>' +
            '<div class="score"><span class="label">After</span><span class="value">' + after +
            '</span><span class="sub">SEO score</span></div>' +
            '<div class="score ' + deltaClass + '"><span class="label">Change</span><span class="value">' +
            deltaText + '</span><span class="sub">points · grade ' + esc(grade) + '</span></div>' +
            '</div>' +
            '<div class="section"><h2>Keyword</h2><div class="facts">' +
            facts.map((f) => '<div class="fact"><div class="label">' + esc(f[0]) + '</div>' +
                '<div class="value">' + esc(f[1]) + '</div></div>').join('') +
            '</div></div>' +
            (changeRows ? '<div class="section"><h2>Changes made</h2><table><thead><tr>' +
                '<th>Type</th><th>Description</th><th>Before</th><th>After</th></tr></thead>' +
                '<tbody>' + changeRows + '</tbody></table></div>' : '') +
            (breakdownRows ? '<div class="section"><h2>Score breakdown</h2><table><thead><tr>' +
                '<th>Category</th><th>Before</th><th>After</th><th>Change</th></tr></thead>' +
                '<tbody>' + breakdownRows + '</tbody></table></div>' : '') +
            (recs.length ? '<div class="section"><h2>Go further</h2><ul>' +
                recs.map((r) => '<li>' + esc(String(r)) + '</li>').join('') + '</ul></div>' : '') +
            '<div class="footer">Generated by ScriptlyAI · ' + esc(when) + '</div>' +
            '</div></body></html>';

        const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'report';
        const stamp = new Date().toISOString().slice(0, 10);
        const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'seo-report-' + slug + '-' + stamp + '.html';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // ----------------------------------------------------------------------
    // Wiring
    // ----------------------------------------------------------------------

    const ACTIONS = {
        optimize: askToOptimize,
        keywords: runKeywords,
        url: runUrlMetrics,
        domain: runDomain,
        'reload-reports': () => { state.reportsLoaded = false; loadReports(); },
        'export-current': () => exportReport(state.lastRun)
    };

    root.addEventListener('click', (e) => {
        const tab = e.target.closest('.opt-tabs .seg-tab');
        if (tab) { showTab(tab.dataset.tab); return; }

        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        const action = btn.dataset.action;

        if (action === 'report-detail') { openReportDetail(btn.dataset.id); return; }
        if (action === 'report-export') { exportReport(reportById(btn.dataset.id)); return; }
        if (action === 'report-delete') { askToDelete(btn.dataset.id); return; }

        const handler = ACTIONS[action];
        if (handler) handler();
    }, { signal });

    // Enter submits the field it was pressed in.
    root.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        const input = e.target.closest('[data-submit]');
        if (!input) return;
        e.preventDefault();
        if (input.dataset.submit === 'url') runUrlMetrics();
        if (input.dataset.submit === 'domain') runDomain();
    }, { signal });

    // The SelectPill module owns the menu and writes through to the <select>;
    // the trigger's visible label is ours to keep in step.
    root.addEventListener('change', (e) => {
        const select = e.target.closest('[data-select-pill] select');
        if (select) syncPillLabel(select);
    }, { signal });

    const confirmOptimize = $('[data-confirm-optimize]');
    if (confirmOptimize) {
        confirmOptimize.addEventListener('click', () => {
            const modal = bsModal('optimizeConfirmModal');
            if (modal) modal.hide();
            runOptimize();
        }, { signal });
    }

    const confirmDelete = $('[data-confirm-delete]');
    if (confirmDelete) confirmDelete.addEventListener('click', deleteReport, { signal });

    const detailExport = $('[data-detail-export]');
    if (detailExport) {
        detailExport.addEventListener('click', () => exportReport(reportById(state.pendingExportId)), { signal });
    }

    // Only a hash this page owns moves the tab — an unrelated in-page anchor
    // must not yank the reader back to the first panel.
    window.addEventListener('hashchange', () => {
        const name = (location.hash || '').replace('#', '');
        if (TABS.indexOf(name) !== -1 && name !== state.tab) showTab(name, false);
    }, { signal });

    // ----------------------------------------------------------------------
    // Start
    // ----------------------------------------------------------------------

    $$('[data-select-pill] select').forEach(syncPillLabel);

    const initial = (location.hash || '').replace('#', '');
    showTab(TABS.indexOf(initial) === -1 ? 'optimize' : initial, false);
})();
