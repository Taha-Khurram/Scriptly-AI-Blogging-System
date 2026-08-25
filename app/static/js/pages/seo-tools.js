/**
 * SEO Tools — research keywords · score a draft · rewrite it for search.
 *
 * The previous version declared nine globals (loadDrafts, analyzeDraft,
 * applyOptimizationToDraft, resetAnalysis, copyToClipboard…) so the template
 * could reach them from inline onclick attributes, bound its listeners at
 * module scope, and reported every failure through alert() — a modal browser
 * dialog that says nothing about which request failed and leaves the panel
 * looking like a run you never started.
 *
 * Everything here is one IIFE, every listener goes through an AbortController
 * the next run aborts, and every control is reached by delegation off
 * .dashboard-main. PJAX re-injects this file on each visit to /seo-tools, so a
 * listener bound to document at module scope would accumulate one copy per
 * visit and nothing would ever remove it.
 *
 * It also drops ~230 lines that were unreachable: displayResults(),
 * displayChecklist() and updateScoreBreakdown() were only ever called from each
 * other, and the URL-SEO half (analyzeUrlSeo, displayUrlResults) drove markup
 * that had been commented out of the template. The score breakdown those dead
 * functions rendered is now on the analysis panel, where it is reachable.
 */

(function seoToolsPage() {
    'use strict';

    if (window.__seoToolsAbort) {
        try { window.__seoToolsAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__seoToolsAbort = controller;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const $ = (sel, scope) => (scope || root).querySelector(sel);
    const $$ = (sel, scope) => Array.from((scope || root).querySelectorAll(sel));

    const body = $('[data-body="seo"]');
    const draftSelect = $('[data-draft-select]');
    const regionSelect = $('[data-region-select]');
    if (!body || !draftSelect || !regionSelect) return;

    // The seven categories the scorer returns, in the order it weights them.
    // Labels are ours; the weights come from the API so the two cannot drift.
    const CATEGORIES = [
        ['keywords', 'Keywords'],
        ['content_length', 'Content length'],
        ['headings', 'Headings'],
        ['readability', 'Readability'],
        ['title', 'Title'],
        ['links', 'Links'],
        ['images', 'Images']
    ];

    const state = {
        drafts: [],
        draftsLoaded: false,
        retry: null          // the runner the error state's "Try again" repeats
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does. NOT the
    // `div.textContent -> div.innerHTML` trick: that encodes &, < and > and
    // leaves both quote characters alone, which is safe for text and unsafe the
    // moment the value lands in data-copy="…" or title="…" — and the keyword
    // rows below build both.
    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&#34;')
            .replace(/'/g, '&#39;');
    }

    function num(value, fallback) {
        const n = Number(value);
        return Number.isFinite(n) ? n : (fallback === undefined ? 0 : fallback);
    }

    function clamp(n) {
        return Math.max(0, Math.min(100, n));
    }

    // Compact for display, exact on the element's title — the compact form is
    // unparseable, so anything reading the figure back reads the attribute.
    function compact(value) {
        if (value === null || value === undefined || value === '') return null;
        const n = Number(value);
        if (!Number.isFinite(n)) return null;
        if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
        if (Math.abs(n) >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
        return n.toLocaleString();
    }

    function money(value) {
        const n = Number(value);
        if (!Number.isFinite(n) || n <= 0) return null;
        return '$' + n.toFixed(2);
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    function applyModal() {
        const el = document.getElementById('seoApplyModal');
        if (!el || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(el);
    }

    // Reads the error the API actually sent rather than reporting every failure
    // as a connection problem — a 429 and a dropped socket are different things
    // and only one of them is worth retrying immediately.
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

    function postJson(url, payload) {
        return requestJson(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        });
    }

    function setState(next, message) {
        body.dataset.state = next;
        if (next === 'error') {
            const text = $('[data-error-text]', body);
            if (text) text.textContent = message || 'Something went wrong.';
        }
    }

    function setHead(title, note) {
        const t = $('[data-result-title]');
        const n = $('[data-result-note]');
        if (t) t.textContent = title;
        if (n) n.textContent = note || '';
    }

    function working(label, note) {
        const l = $('[data-working-label]', body);
        const n = $('[data-working-note]', body);
        if (l) l.textContent = label;
        if (n) n.textContent = note;
    }

    // Concurrent runs would race each other into the same panel, so the verbs
    // go down together for the duration of any one of them.
    function lockVerbs(on) {
        $$('[data-action="analyze"], [data-action="keywords"], [data-action="apply"]')
            .forEach((btn) => { btn.disabled = on; });
    }

    function busy(btn, on, label) {
        if (!btn) return;
        if (on) {
            if (!btn.dataset.label) btn.dataset.label = btn.innerHTML;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> ' + esc(label || 'Working…');
        } else if (btn.dataset.label) {
            btn.innerHTML = btn.dataset.label;
            delete btn.dataset.label;
        }
    }

    function gradeClass(grade) {
        const letter = String(grade || '').trim().charAt(0).toLowerCase();
        return 'abcdf'.includes(letter) ? 'grade-' + letter : '';
    }

    function setGrade(pill, letterEl, grade) {
        if (letterEl) letterEl.textContent = grade || '—';
        if (pill) pill.className = 'seo-grade ' + gradeClass(grade);
    }

    function difficultyBand(score) {
        if (score <= 30) return { key: 'easy', word: 'Easy' };
        if (score <= 60) return { key: 'medium', word: 'Medium' };
        return { key: 'hard', word: 'Hard' };
    }

    // A competition level the provider did not send must not be drawn as
    // "medium" — an invented value looks exactly like a measured one.
    function competitionBand(value) {
        const word = String(value || '').trim().toLowerCase();
        if (word === 'low' || word === 'medium' || word === 'high') return word;
        return '';
    }

    async function copyText(text) {
        if (!text) return false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (e) { /* falls through to the textarea */ }

        // Safari without permission, and any page served over plain http.
        try {
            const scratch = document.createElement('textarea');
            scratch.value = text;
            scratch.setAttribute('readonly', '');
            scratch.style.position = 'fixed';
            scratch.style.top = '-1000px';
            document.body.appendChild(scratch);
            scratch.select();
            const ok = document.execCommand('copy');
            scratch.remove();
            return ok;
        } catch (e) {
            return false;
        }
    }

    // ----------------------------------------------------------------------
    // Pickers
    // ----------------------------------------------------------------------

    function draftId() { return draftSelect.value; }
    function regionCode() { return regionSelect.value; }

    function selectedText(select) {
        const opt = select.options[select.selectedIndex];
        return opt ? opt.textContent : '';
    }

    function regionName() { return selectedText(regionSelect); }

    function draftTitle() {
        const id = draftId();
        const found = state.drafts.find((d) => String(d.id) === String(id));
        return (found && found.title) || selectedText(draftSelect) || 'this draft';
    }

    // The SelectPill module in app.js writes through to the <select> and fires
    // `change`; the visible label on the trigger is the page's to keep in step.
    function syncPillLabel(select) {
        const pill = select.closest('[data-select-pill]');
        if (!pill) return;
        const label = $('[data-pill-text]', pill);
        if (label) label.textContent = selectedText(select);
    }

    function fillDraftPicker(drafts, placeholder) {
        const pill = draftSelect.closest('[data-select-pill]');
        const menu = pill ? $('.menu', pill) : null;
        const current = draftSelect.value;

        draftSelect.innerHTML = '<option value="">' + esc(placeholder) + '</option>' +
            drafts.map((d) => '<option value="' + esc(d.id) + '">' +
                esc(d.title || 'Untitled') + '</option>').join('');

        if (menu) {
            menu.innerHTML = '<button type="button" class="menu-item" role="option" data-value="">' +
                '<i class="material-symbols-outlined icon-inline menu-check" aria-hidden="true">check</i>' +
                '<span class="menu-label">' + esc(placeholder) + '</span></button>' +
                drafts.map((d) => '<button type="button" class="menu-item" role="option" ' +
                    'data-value="' + esc(d.id) + '">' +
                    '<i class="material-symbols-outlined icon-inline menu-check" aria-hidden="true">check</i>' +
                    '<span class="menu-label">' + esc(d.title || 'Untitled') + '</span></button>').join('');
        }

        // A draft that vanished between loads must not leave a stale id behind.
        draftSelect.value = drafts.some((d) => String(d.id) === String(current)) ? current : '';
        syncPillLabel(draftSelect);
    }

    async function loadDrafts(force) {
        if (state.draftsLoaded && !force) return;
        state.draftsLoaded = true;

        try {
            const data = await requestJson('/api/seo/drafts');
            state.drafts = Array.isArray(data.drafts) ? data.drafts : [];
            fillDraftPicker(state.drafts, state.drafts.length ? 'Select a draft' : 'No drafts yet');
        } catch (err) {
            state.draftsLoaded = false;
            const message = describeError(err);
            if (!message) return;                 // navigated away mid-request
            fillDraftPicker([], 'Could not load drafts');
            toast('error', 'Drafts unavailable', message);
        }
    }

    // A verb with no draft chosen is a no-op, and it used to be a browser
    // alert() saying so.
    function requireDraft() {
        if (draftId()) return true;
        toast('warning', 'Pick a draft', 'Choose which draft you want to work on first.');
        const trigger = $('[data-select-trigger]', draftSelect.closest('[data-select-pill]'));
        if (trigger) trigger.focus();
        return false;
    }

    // ----------------------------------------------------------------------
    // Render — analysis
    // ----------------------------------------------------------------------

    function metric(el, value, missingWhen) {
        if (!el) return;
        const tile = el.closest('.seo-metric');
        const absent = missingWhen === undefined ? (value === null || value === '') : missingWhen;
        el.textContent = absent ? '—' : value;
        if (tile) tile.classList.toggle('is-missing', !!absent);
    }

    function analysisMeters(breakdown) {
        return CATEGORIES.map(([key, label]) => {
            const row = breakdown[key];
            if (!row) return '';
            const score = clamp(Math.round(num(row.score)));
            const weight = row.weight ? esc(row.weight) + ' of the score' : '';
            return '<div class="seo-meter">' +
                '<span class="seo-meter-label">' + esc(label) +
                (weight ? '<span class="seo-meter-weight">' + weight + '</span>' : '') +
                '</span>' +
                '<span class="seo-meter-track">' +
                '<span class="seo-meter-fill" style="width: ' + score + '%"></span>' +
                '</span>' +
                '<span class="seo-meter-figures"><strong>' + score + '</strong> / 100</span>' +
                '</div>';
        }).join('');
    }

    function issueItems(issues) {
        return issues.map((issue) => {
            const severity = String((issue && issue.severity) || 'low').toLowerCase();
            const known = ['high', 'medium', 'low'].includes(severity) ? severity : 'low';
            const glyph = known === 'high' ? 'report'
                : known === 'medium' ? 'warning' : 'info';
            const text = typeof issue === 'string' ? issue : (issue.message || issue.description || '');
            return '<li class="is-' + known + '">' +
                '<i class="material-symbols-outlined icon-inline" aria-hidden="true">' + glyph + '</i>' +
                '<span><span class="seo-issue-severity">' + known + ' priority</span>' +
                esc(text) + '</span></li>';
        }).join('');
    }

    function textItems(items, glyph) {
        return items.map((item) => {
            const text = typeof item === 'string'
                ? item
                : (item && (item.message || item.description || item.text)) || '';
            if (!text) return '';
            return '<li><i class="material-symbols-outlined icon-inline" aria-hidden="true">' + glyph + '</i><span>' +
                esc(text) + '</span></li>';
        }).join('');
    }

    function renderAnalysis(analysis, title, region) {
        const score = clamp(Math.round(num(analysis.seo_score && analysis.seo_score.total)));
        const grade = (analysis.seo_score && analysis.seo_score.grade) || 'N/A';

        const source = $('[data-analysis-source]', body);
        if (source) source.textContent = title + ' · ' + region;

        const scoreEl = $('[data-analysis-score]', body);
        if (scoreEl) { scoreEl.textContent = String(score); scoreEl.title = String(score); }
        setGrade($('[data-analysis-grade-pill]', body), $('[data-analysis-grade]', body), grade);

        const words = num(analysis.word_count);
        metric($('[data-metric-words]', body), compact(words), !words);

        const flesch = analysis.readability && analysis.readability.flesch_score;
        metric($('[data-metric-readability]', body),
            Number.isFinite(Number(flesch)) ? String(Math.round(num(flesch))) : null,
            !Number.isFinite(Number(flesch)));

        const headings = analysis.headings && analysis.headings.total;
        metric($('[data-metric-headings]', body),
            headings === undefined || headings === null ? null : String(num(headings)),
            headings === undefined || headings === null);

        const links = analysis.links && analysis.links.total;
        metric($('[data-metric-links]', body),
            links === undefined || links === null ? null : String(num(links)),
            links === undefined || links === null);

        const breakdown = (analysis.seo_score && analysis.seo_score.breakdown) || {};
        const meters = analysisMeters(breakdown);
        const breakdownCard = $('[data-analysis-breakdown]', body);
        const metersHost = $('[data-analysis-meters]', body);
        if (metersHost) metersHost.innerHTML = meters;
        if (breakdownCard) breakdownCard.hidden = !meters;

        const issues = Array.isArray(analysis.issues) ? analysis.issues : [];
        const issuesCard = $('[data-issues-card]', body);
        const issuesList = $('[data-issues-list]', body);
        const issuesCount = $('[data-issues-count]', body);
        if (issuesList) issuesList.innerHTML = issueItems(issues);
        if (issuesCount) issuesCount.textContent = String(issues.length);
        if (issuesCard) issuesCard.hidden = issues.length === 0;

        const recs = Array.isArray(analysis.recommendations) ? analysis.recommendations : [];
        const recsCard = $('[data-recs-card]', body);
        const recsList = $('[data-recs-list]', body);
        if (recsList) recsList.innerHTML = textItems(recs, 'arrow_right_alt');
        if (recsCard) recsCard.hidden = recs.length === 0;

        setHead('Analysis', 'Nothing written — this is the draft as it stands');
        setState('analysis');
    }

    // ----------------------------------------------------------------------
    // Render — keyword research
    // ----------------------------------------------------------------------

    function keywordRow(kw) {
        const keyword = String(kw.keyword || '').trim();
        if (!keyword) return '';

        const score = clamp(Math.round(num(kw.difficulty_score, 50)));
        const band = difficultyBand(score);
        const volume = compact(kw.search_volume);
        const cpc = money(kw.cpc);
        const comp = competitionBand(kw.competition);

        return '<tr>' +
            '<td class="seo-table-keyword">' + esc(keyword) + '</td>' +
            '<td><span class="seo-difficulty is-' + band.key + '">' +
            '<span class="seo-difficulty-word">' + band.word + '</span>' +
            '<span class="seo-difficulty-track">' +
            '<span class="seo-difficulty-fill" style="width: ' + score + '%"></span>' +
            '</span>' + score + '</span></td>' +
            '<td class="is-num"' + (volume ? ' title="' + esc(kw.search_volume) + '"' : '') + '>' +
            (volume || '—') + '</td>' +
            '<td class="is-num">' + (cpc || '—') + '</td>' +
            '<td>' + (comp ? '<span class="seo-comp is-' + comp + '">' + comp + '</span>' : '—') + '</td>' +
            '<td><button type="button" class="seo-copy" data-copy="' + esc(keyword) + '">' +
            '<i class="material-symbols-outlined icon-inline" aria-hidden="true">content_copy</i> Copy</button></td>' +
            '</tr>';
    }

    function renderKeywords(payload, topic, region) {
        const keywords = (Array.isArray(payload.related_keywords) ? payload.related_keywords : [])
            .filter((kw) => kw && kw.keyword);

        const source = $('[data-keywords-source]', body);
        if (source) source.textContent = '“' + topic + '” · ' + region;

        const primary = keywords[0];
        const primaryCard = $('[data-primary]', body);
        if (primaryCard) primaryCard.hidden = !primary;

        if (primary) {
            const kwEl = $('[data-primary-kw]', body);
            if (kwEl) kwEl.textContent = primary.keyword;

            const score = clamp(Math.round(num(primary.difficulty_score, 50)));
            const band = difficultyBand(score);
            const comp = competitionBand(primary.competition);
            const note = $('[data-primary-note]', body);
            if (note) {
                note.textContent = comp
                    ? band.word.toLowerCase() + ' to rank for, ' + comp + ' competition'
                    : band.word.toLowerCase() + ' to rank for';
            }

            const volume = compact(primary.search_volume);
            metric($('[data-primary-volume]', body), volume, !volume);
            const cpc = money(primary.cpc);
            metric($('[data-primary-cpc]', body), cpc, !cpc);
            metric($('[data-primary-difficulty]', body), score + ' / 100', false);

            const fill = $('[data-primary-difficulty-fill]', body);
            if (fill) fill.style.width = score + '%';
        }

        const rows = $('[data-kw-rows]', body);
        if (rows) {
            rows.innerHTML = keywords.length
                ? keywords.map(keywordRow).join('')
                : '<tr class="seo-table-empty"><td colspan="6">No keyword data came back for this ' +
                  'topic and region.</td></tr>';
        }
        const count = $('[data-kw-count]', body);
        if (count) count.textContent = String(keywords.length);

        setHead('Keyword research', 'Research only — nothing written back');
        setState('keywords');
    }

    // ----------------------------------------------------------------------
    // Render — comparison
    // ----------------------------------------------------------------------

    function comparisonMeters(breakdown) {
        return CATEGORIES.map(([key, label]) => {
            const row = breakdown[key];
            if (!row) return '';
            const before = clamp(Math.round(num(row.before)));
            const after = clamp(Math.round(num(row.after)));
            const gain = after - before;
            const dir = gain > 0 ? 'is-up' : gain < 0 ? 'is-down' : '';
            const sign = gain > 0 ? '+' : '';

            return '<div class="seo-meter">' +
                '<span class="seo-meter-label">' + esc(label) + '</span>' +
                '<span class="seo-meter-track">' +
                '<span class="seo-meter-fill" style="width: ' + after + '%"></span>' +
                '<span class="seo-meter-tick" style="left: ' + before + '%"></span>' +
                '</span>' +
                '<span class="seo-meter-figures">was ' + before + ' <strong>' + after + '</strong>' +
                (gain ? ' <span class="seo-meter-gain ' + dir + '">' + sign + gain + '</span>' : '') +
                '</span></div>';
        }).join('');
    }

    function changeItems(changes) {
        return changes.map((change) => {
            if (typeof change === 'string') {
                return '<li><i class="material-symbols-outlined icon-inline" aria-hidden="true">check</i><span>' +
                    esc(change) + '</span></li>';
            }
            const description = (change && change.description) || 'Content updated';
            const before = change && change.before;
            const after = change && change.after;
            const detail = (before && after)
                ? '<span class="seo-change-detail"><strong>was</strong> ' + esc(before) + '</span>' +
                  '<span class="seo-change-detail"><strong>now</strong> ' + esc(after) + '</span>'
                : '';
            return '<li><i class="material-symbols-outlined icon-inline" aria-hidden="true">check</i>' +
                '<span>' + esc(description) + detail + '</span></li>';
        }).join('');
    }

    function beforeAfterNote(before, after) {
        if (!Number.isFinite(before) || !Number.isFinite(after)) return '';
        const gain = Math.round(after - before);
        if (!gain) return 'unchanged at ' + Math.round(before);
        return 'was ' + Math.round(before) + ', ' + (gain > 0 ? '+' : '') + gain;
    }

    function renderComparison(data, originalTitle, region) {
        const comparison = data.comparison || {};
        const after = clamp(Math.round(num(data.seo_score)));
        const before = clamp(Math.round(num(data.original_score)));
        const gain = Math.round(num(data.score_improvement, after - before));

        const note = $('[data-outcome-note]', body);
        if (note) {
            note.textContent = 'Scriptly rewrote “' + originalTitle + '” for ' + region +
                ' and saved the result over the draft.';
        }

        const afterEl = $('[data-compare-after]', body);
        if (afterEl) { afterEl.textContent = String(after); afterEl.title = String(after); }
        const beforeEl = $('[data-compare-before]', body);
        if (beforeEl) beforeEl.textContent = String(before);

        const delta = $('[data-compare-delta]', body);
        if (delta) {
            const dir = gain > 0 ? 'is-up' : gain < 0 ? 'is-down' : 'is-flat';
            const arrow = gain > 0 ? '↑' : gain < 0 ? '↓' : '→';
            delta.className = 'seo-score-delta ' + dir;
            delta.textContent = arrow + ' ' + (gain > 0 ? '+' : '') + gain +
                (Math.abs(gain) === 1 ? ' point' : ' points');
        }

        setGrade($('[data-compare-grade-pill]', body), $('[data-compare-grade]', body),
            data.seo_grade || (comparison.grades && comparison.grades.after));

        // Word count and readability are not scores out of 100, so they state
        // the new figure with the old one under it rather than taking a meter.
        const metrics = $('[data-compare-metrics]', body);
        const wc = comparison.word_count || {};
        const rd = comparison.readability || {};
        const kw = data.primary_keyword || {};
        const haveFacts = wc.after !== undefined || rd.after !== undefined || kw.keyword;

        if (metrics) metrics.hidden = !haveFacts;
        if (haveFacts) {
            metric($('[data-compare-words]', body), compact(wc.after), wc.after === undefined);
            const wordsNote = $('[data-compare-words-note]', body);
            if (wordsNote) wordsNote.textContent = beforeAfterNote(Number(wc.before), Number(wc.after));

            const readAfter = Number(rd.after);
            metric($('[data-compare-readability]', body),
                Number.isFinite(readAfter) ? String(Math.round(readAfter)) : null,
                !Number.isFinite(readAfter));
            const readNote = $('[data-compare-readability-note]', body);
            if (readNote) readNote.textContent = beforeAfterNote(Number(rd.before), readAfter);

            metric($('[data-compare-kw]', body), kw.keyword || null, !kw.keyword);
        }

        const meters = comparisonMeters(comparison.breakdown_comparison || {});
        const breakdownCard = $('[data-compare-breakdown]', body);
        const metersHost = $('[data-compare-meters]', body);
        if (metersHost) metersHost.innerHTML = meters;
        if (breakdownCard) breakdownCard.hidden = !meters;

        const titleBefore = $('[data-title-before]', body);
        const titleAfter = $('[data-title-after]', body);
        if (titleBefore) titleBefore.value = (comparison.title && comparison.title.before) || originalTitle || '';
        if (titleAfter) titleAfter.value = data.new_title || '';

        const changes = Array.isArray(data.changes_made) ? data.changes_made : [];
        const changesCard = $('[data-changes-card]', body);
        const changesList = $('[data-changes-list]', body);
        const changesCount = $('[data-changes-count]', body);
        if (changesList) changesList.innerHTML = changeItems(changes);
        if (changesCount) changesCount.textContent = String(changes.length);
        if (changesCard) changesCard.hidden = changes.length === 0;

        // The optimize response carries recommendations the old comparison view
        // threw away — what to do next, after the rewrite it just made.
        const next = Array.isArray(data.recommendations) ? data.recommendations : [];
        const nextCard = $('[data-next-card]', body);
        const nextList = $('[data-next-list]', body);
        if (nextList) nextList.innerHTML = textItems(next, 'arrow_right_alt');
        if (nextCard) nextCard.hidden = next.length === 0;

        setHead('Optimization result', 'Saved over the draft');
        setState('comparison');
    }

    // ----------------------------------------------------------------------
    // Actions
    // ----------------------------------------------------------------------

    async function run(options) {
        const { btn, label, note, head, request, render } = options;

        state.retry = () => run(options);
        lockVerbs(true);
        busy(btn, true, label);
        working(label, note);
        setHead(head, '');
        setState('loading');

        try {
            const data = await request();
            render(data);
        } catch (err) {
            const message = describeError(err);
            if (message === null) return;         // navigated away, panel is gone
            setHead('Results', 'The last run failed');
            setState('error', message);
            toast('error', 'That did not work', message);
        } finally {
            lockVerbs(false);
            busy(btn, false);
        }
    }

    function runAnalyze(btn) {
        if (!requireDraft()) return;
        const id = draftId();
        const title = draftTitle();
        const region = regionName();

        run({
            btn,
            label: 'Scoring the draft…',
            note: 'Reading the draft and scoring it against the seven categories. ' +
                'Nothing is written.',
            head: 'Analyzing',
            request: () => postJson('/api/seo/analyze-draft/' + encodeURIComponent(id),
                { region: regionCode() }),
            render: (data) => renderAnalysis(data.original_analysis || {}, data.blog_title || title, region)
        });
    }

    function runKeywords(btn) {
        if (!requireDraft()) return;
        const topic = draftTitle();
        const region = regionName();

        run({
            btn,
            label: 'Researching keywords…',
            note: 'Looking up volume, cost per click and competition for the terms this draft ' +
                'could rank for. Nothing is written.',
            head: 'Researching',
            // The draft's title is the topic, and the picker already knows it —
            // the old version fetched the whole blog document to read it back.
            request: () => postJson('/api/seo/keywords', { topic, region: regionCode() }),
            render: (data) => renderKeywords(data, topic, region)
        });
    }

    function runApply() {
        const id = draftId();
        if (!id) return;
        const title = draftTitle();
        const region = regionName();

        run({
            btn: null,
            label: 'Rewriting for search…',
            note: 'This usually takes 15–30 seconds. Leaving the tab is fine — the draft is saved ' +
                'server-side.',
            head: 'Optimizing',
            request: () => postJson('/api/seo/optimize-blog/' + encodeURIComponent(id),
                { region: regionCode() }),
            render: (data) => {
                renderComparison(data, title, region);
                toast('success', 'Draft optimized', 'Saved over “' + title + '”.');
                // The title has just changed, so the picker's labels are stale.
                loadDrafts(true);
            }
        });
    }

    function askApply() {
        if (!requireDraft()) return;
        const draft = $('[data-confirm-draft]');
        const region = $('[data-confirm-region]');
        if (draft) draft.textContent = draftTitle();
        if (region) region.textContent = regionName();

        const modal = applyModal();
        if (modal) { modal.show(); return; }

        // Bootstrap absent (a CDN that did not answer) — the action still needs
        // a gate, and it must not be a silent write.
        if (window.confirm('Rewrite and save over “' + draftTitle() + '”? The current version is not kept.')) {
            runApply();
        }
    }

    function reset() {
        state.retry = null;
        setHead('Results', 'Nothing analyzed yet');
        setState('empty');
        body.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    // ----------------------------------------------------------------------
    // Events — one delegated listener, all of it aborted on the next visit
    // ----------------------------------------------------------------------

    root.addEventListener('click', async (e) => {
        const copyBtn = e.target.closest('.seo-copy');
        if (copyBtn) {
            const ok = await copyText(copyBtn.dataset.copy || '');
            toast(ok ? 'success' : 'error', ok ? 'Copied' : 'Could not copy',
                ok ? '“' + (copyBtn.dataset.copy || '') + '” is on your clipboard.'
                   : 'Your browser blocked clipboard access — select the text and copy it.');
            return;
        }

        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        switch (btn.dataset.action) {
            case 'analyze': runAnalyze(btn); break;
            case 'keywords': runKeywords(btn); break;
            case 'apply': askApply(); break;
            case 'reload-drafts':
                loadDrafts(true);
                toast('info', 'Reloading drafts', 'Fetching your latest drafts.');
                break;
            case 'retry':
                if (state.retry) state.retry(); else reset();
                break;
            case 'reset': reset(); break;
            case 'copy-title': {
                const field = $('[data-title-after]', body);
                const ok = await copyText(field ? field.value : '');
                toast(ok ? 'success' : 'error', ok ? 'Copied' : 'Could not copy',
                    ok ? 'The optimized title is on your clipboard.'
                       : 'Your browser blocked clipboard access.');
                break;
            }
        }
    }, { signal });

    // The modal's own button, which lives outside any verb row.
    const confirmBtn = $('[data-confirm-apply]');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', () => {
            const modal = applyModal();
            if (modal) modal.hide();
            runApply();
        }, { signal });
    }

    // The pill label is the page's to maintain; the region also appears in the
    // confirmation, which must never name a region other than the one selected.
    draftSelect.addEventListener('change', () => syncPillLabel(draftSelect), { signal });
    regionSelect.addEventListener('change', () => syncPillLabel(regionSelect), { signal });

    loadDrafts();
})();
