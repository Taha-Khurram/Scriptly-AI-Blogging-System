/**
 * Newsletter — compose, audience, archive.
 *
 * Was 415 lines of inline <script> in the template, declaring a dozen globals
 * (loadStats, sendNewsletter, closeDeleteModal…). `closeDeleteModal` and
 * `showDeleteConfirm` are also declared by leads.js, so the two only avoided
 * colliding by never being on screen together.
 *
 * Everything here is scoped to one IIFE and delegated off the page root.
 * PJAX re-injects this file on every visit to /newsletter, so nothing may hold
 * a reference across navigations and every `document`-level listener goes
 * through an AbortController the next run aborts.
 */

(function newsletterPage() {
    'use strict';

    if (window.__newsletterAbort) {
        try { window.__newsletterAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__newsletterAbort = controller;

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    const root = $('.dashboard-main');
    if (!root) return;

    const state = {
        tab: 'compose',
        generated: null,      // the agent's payload
        html: null,           // last rendered email
        subscribers: [],
        history: [],
        subscriberQuery: '',
        device: 'desktop',
        pendingDeleteId: null,
        renderSeq: 0
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does. NOT the
    // `div.textContent -> div.innerHTML` trick the rest of this codebase uses:
    // that leaves quotes alone, and every value below lands in an attribute.
    // Subscriber emails and blog titles are user-supplied.
    function esc(str) {
        return String(str == null ? '' : str)
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

    function plural(n, one, many) {
        return n === 1 ? one : many;
    }

    function formatDate(value) {
        if (!value) return '—';
        const d = new Date(value);
        if (isNaN(d.getTime())) return '—';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    }

    function initial(text) {
        const s = String(text || '').trim();
        return s ? s[0].toUpperCase() : '?';
    }

    function val(id) {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    }

    function busy(btn, on, label) {
        if (!btn) return;
        if (on) {
            btn.dataset.label = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> ' + esc(label || 'Working…');
        } else {
            btn.disabled = false;
            if (btn.dataset.label) { btn.innerHTML = btn.dataset.label; delete btn.dataset.label; }
        }
    }

    function subscriberCount() {
        const el = $('[data-stat-subscribers]');
        // The compact form ("1.2K") is unparseable, so the true figure is kept
        // on the element's title and read from there.
        if (!el) return 0;
        return parseInt(el.getAttribute('title') || el.textContent || '0', 10) || 0;
    }

    // ----------------------------------------------------------------------
    // Stat tiles — delta, sparkline, meter
    //
    // Everything below is derived in the browser from the subscriber and
    // archive payloads the page already fetches, so the tiles cost no extra
    // request. The server-rendered figure stays authoritative until the data
    // arrives, and is never animated over — an animation that can be
    // interrupted is an animation that can strand a wrong number.
    // ----------------------------------------------------------------------

    const DAY = 86400000;

    // 1,284 / 12.9K / 4.2M — a stat-tile value should not wrap.
    function compact(n) {
        const v = Number(n) || 0;
        if (v < 1000) return String(v);
        if (v < 1e6) return (v / 1000).toFixed(v < 10000 ? 1 : 0).replace(/\.0$/, '') + 'K';
        return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
    }

    function relativeDays(iso) {
        const d = new Date(iso);
        if (!iso || isNaN(d.getTime())) return null;
        const days = Math.floor((Date.now() - d.getTime()) / DAY);
        if (days <= 0) return 'today';
        if (days === 1) return 'yesterday';
        if (days < 30) return days + ' days ago';
        const months = Math.round(days / 30);
        if (months < 12) return months + ' ' + plural(months, 'month', 'months') + ' ago';
        const years = Math.round(days / 365);
        return years + ' ' + plural(years, 'year', 'years') + ' ago';
    }

    /**
     * A single-series sparkline.
     *
     * One hue throughout — the current period is marked by *form* (a dot with
     * a surface-coloured ring), never by a second colour, so nothing depends
     * on telling two similar blues apart. role="img" + aria-label carries the
     * same information as text, since a micro-chart has no room for axes and
     * a tooltip must never be the only way to reach a value.
     */
    function sparkline(values, label) {
        // PAD has to clear the endpoint marker, not just the line: the dot is
        // r3.5 with a 2px ring, so it reaches 4.5px past its centre. At PAD 4
        // the last dot was clipped by the viewBox edge.
        const W = 116, H = 40, PAD = 6;
        const n = values.length;
        if (!n) return '';

        const max = Math.max.apply(null, values);
        const min = Math.min.apply(null, values);
        const span = max - min || 1;
        const stepX = n > 1 ? (W - PAD * 2) / (n - 1) : 0;

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

        const total = values.reduce((a, b) => a + b, 0);
        const summary = label + ': ' + total + ' over ' + n + ' periods, ' +
            'from ' + min + ' to ' + max + ', most recent ' + values[n - 1] + '.';

        return '<svg class="stat-trend" viewBox="0 0 ' + W + ' ' + H + '" role="img" ' +
            'aria-label="' + esc(summary) + '">' +
            '<title>' + esc(summary) + '</title>' +
            '<path class="stat-trend-area" d="' + area + '"/>' +
            '<path class="stat-trend-line" d="' + line + '"/>' +
            '<circle class="stat-trend-dot" cx="' + last[0].toFixed(1) + '" cy="' + last[1].toFixed(1) +
            '" r="3.5"/>' +
            '</svg>';
    }

    // Counts per bucket, oldest first, for the last `count` buckets of `size` ms.
    function bucket(stamps, count, size) {
        const now = Date.now();
        const out = new Array(count).fill(0);
        stamps.forEach((iso) => {
            const t = new Date(iso).getTime();
            if (isNaN(t)) return;
            const age = now - t;
            if (age < 0 || age >= size * count) return;
            out[count - 1 - Math.floor(age / size)]++;
        });
        return out;
    }

    function setDelta(sel, text, direction) {
        const el = $(sel);
        if (!el) return;
        el.classList.remove('is-up', 'is-down');
        if (!text) { el.textContent = ''; return; }
        const icon = direction === 'up' ? 'north_east'
            : direction === 'down' ? 'south_east' : 'remove';
        if (direction === 'up') el.classList.add('is-up');
        if (direction === 'down') el.classList.add('is-down');
        el.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">' + icon + '</i> ' + esc(text);
    }

    function paintSubscriberTile() {
        const list = state.subscribers;
        const figure = $('[data-stat-subscribers]');
        if (figure && list.length) {
            // The server count is authoritative; the list is capped at 500.
            const total = Math.max(subscriberCount(), list.length);
            figure.setAttribute('title', String(total));
            figure.textContent = compact(total);
        }

        const stamps = list.map((s) => s.subscribed_at).filter(Boolean);
        const recent = stamps.filter((iso) => {
            const t = new Date(iso).getTime();
            return !isNaN(t) && Date.now() - t < 30 * DAY;
        }).length;

        setDelta('[data-delta-subscribers]',
            recent ? '+' + recent + ' in the last 30 days' : 'No new signups in 30 days',
            recent ? 'up' : 'flat');

        const slot = $('[data-trend-subscribers]');
        if (slot) {
            slot.innerHTML = stamps.length
                ? sparkline(bucket(stamps, 12, 7 * DAY), 'Signups per week')
                : '';
        }
    }

    function paintSentTile() {
        const list = state.history;
        const figure = $('[data-stat-sent]');
        if (figure) {
            figure.setAttribute('title', String(list.length));
            figure.textContent = compact(list.length);
        }

        const stamps = list.map((h) => h.sent_at).filter(Boolean);
        const newest = stamps.slice().sort().pop();
        const when = newest ? relativeDays(newest) : null;
        setDelta('[data-delta-sent]', when ? 'Last sent ' + when : 'Nothing sent yet', 'flat');

        const slot = $('[data-trend-sent]');
        if (slot) {
            slot.innerHTML = stamps.length
                ? sparkline(bucket(stamps, 12, 30 * DAY), 'Issues per month')
                : '';
        }
    }

    // Tracks the composer's Posts select, so the tile says what the next issue
    // will actually draw on rather than repeating a number already above it.
    function paintPostsTile() {
        const figure = $('[data-stat-posts]');
        const total = figure ? (parseInt(figure.getAttribute('title'), 10) || 0) : 0;
        if (!total) {
            setDelta('[data-delta-posts]', 'Publish a post to get started', 'flat');
            return;
        }

        const wanted = parseInt(val('composeLimit'), 10) || 5;
        const used = Math.min(wanted, total);
        setDelta('[data-delta-posts]',
            used + ' ' + plural(used, 'post goes', 'posts go') + ' into your next issue', 'flat');

        const meter = $('[data-meter-posts]');
        if (meter) meter.hidden = false;
        const fill = $('[data-meter-fill]');
        if (fill) fill.style.width = Math.round((used / total) * 100) + '%';
        const note = $('[data-meter-note]');
        if (note) note.textContent = used + ' of ' + total;
    }

    // ----------------------------------------------------------------------
    // Tabs
    // ----------------------------------------------------------------------

    function showTab(name) {
        state.tab = name;
        $$('.newsletter-tabs .seg-tab').forEach((tab) => {
            const on = tab.dataset.tab === name;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        $$('[data-tab-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.tabPanel !== name;
        });
    }

    // ----------------------------------------------------------------------
    // Setup / status
    // ----------------------------------------------------------------------

    async function loadStatus() {
        try {
            const res = await fetch('/api/newsletter/status', { credentials: 'same-origin' });
            const data = await res.json();
            const banner = $('[data-setup-banner]');
            const configured = !!(data.email_service && data.email_service.configured);
            if (banner) banner.hidden = configured;

            // Every send control is disabled while there is no mail transport —
            // the old screen left them live and let the request fail instead.
            $$('[data-requires-email]').forEach((el) => {
                el.disabled = !configured;
                el.title = configured ? '' : 'Configure the email service first';
            });

            if (typeof data.subscriber_count === 'number') {
                const el = $('[data-stat-subscribers]');
                if (el) el.textContent = data.subscriber_count;
            }
        } catch (err) {
            console.error('Newsletter status failed:', err);
        }
    }

    // ----------------------------------------------------------------------
    // Subscribers
    // ----------------------------------------------------------------------

    async function loadSubscribers() {
        const box = $('[data-subscriber-rows]');
        if (!box) return;
        try {
            const res = await fetch('/api/newsletter/subscribers', { credentials: 'same-origin' });
            const data = await res.json();
            state.subscribers = data.subscribers || [];
            renderSubscribers();
            paintSubscriberTile();
        } catch (err) {
            console.error('Subscribers failed:', err);
            box.innerHTML = emptyState('error', 'Could not load subscribers.');
        }
    }

    function emptyState(icon, text, extra) {
        return '<div class="list-empty">' +
            '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">' + icon + '</i></span>' +
            '<p>' + esc(text) + '</p>' + (extra || '') + '</div>';
    }

    function renderSubscribers() {
        const box = $('[data-subscriber-rows]');
        if (!box) return;

        const q = state.subscriberQuery.toLowerCase();
        const rows = q
            ? state.subscribers.filter((s) => String(s.email || '').toLowerCase().includes(q))
            : state.subscribers;

        const countEl = $('[data-subscriber-shown]');
        if (countEl) countEl.textContent = rows.length;

        const exportBtn = $('[data-export-subscribers]');
        if (exportBtn) exportBtn.disabled = rows.length === 0;

        if (!state.subscribers.length) {
            box.innerHTML = emptyState('group',
                'No subscribers yet. The signup form on your public site feeds this list.');
            return;
        }
        if (!rows.length) {
            box.innerHTML = emptyState('search', 'No subscriber matches “' + state.subscriberQuery + '”.');
            return;
        }

        box.innerHTML = '<div class="data-rows">' + rows.map((sub) => {
            const email = esc(sub.email || 'unknown');
            return '<div class="data-row">' +
                '<span class="row-mark" aria-hidden="true">' + esc(initial(sub.email)) + '</span>' +
                '<span class="row-main">' +
                '<span class="row-title">' + email + '</span>' +
                '<span class="row-meta"><span>Subscribed ' + esc(formatDate(sub.subscribed_at)) + '</span></span>' +
                '</span>' +
                '<time class="row-time">' + esc(formatDate(sub.subscribed_at)) + '</time>' +
                '</div>';
        }).join('') + '</div>';
    }

    // A CSV built in the browser from data already fetched — no endpoint, and
    // nothing leaves the page that was not already on it.
    function exportSubscribers() {
        const q = state.subscriberQuery.toLowerCase();
        const rows = q
            ? state.subscribers.filter((s) => String(s.email || '').toLowerCase().includes(q))
            : state.subscribers;
        if (!rows.length) return;

        // A leading =, +, - or @ makes a spreadsheet treat the cell as a
        // formula, so those are prefixed with a quote. Quotes are doubled per
        // RFC 4180.
        const cell = (v) => {
            let s = String(v == null ? '' : v);
            if (/^[=+\-@]/.test(s)) s = "'" + s;
            return '"' + s.replace(/"/g, '""') + '"';
        };

        const csv = ['Email,Subscribed']
            .concat(rows.map((s) => cell(s.email) + ',' + cell(s.subscribed_at || '')))
            .join('\r\n');

        // A BOM, so Excel opens UTF-8 addresses correctly rather than as mojibake.
        const blob = new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'subscribers.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('success', 'Exported', rows.length + ' ' + plural(rows.length, 'address', 'addresses') + ' downloaded.');
    }

    // ----------------------------------------------------------------------
    // History
    // ----------------------------------------------------------------------

    async function loadHistory() {
        const box = $('[data-history-rows]');
        if (!box) return;
        try {
            const res = await fetch('/api/newsletter/history', { credentials: 'same-origin' });
            const data = await res.json();
            state.history = data.history || [];
            renderHistory();
            paintSentTile();
        } catch (err) {
            console.error('History failed:', err);
            box.innerHTML = emptyState('error', 'Could not load the archive.');
        }
    }

    function renderHistory() {
        const box = $('[data-history-rows]');
        if (!box) return;

        const countEl = $('[data-history-count]');
        if (countEl) countEl.textContent = state.history.length;

        if (!state.history.length) {
            box.innerHTML = emptyState('send',
                'Nothing sent yet. Issues you send appear here with their recipient count.');
            return;
        }

        box.innerHTML = '<div class="data-rows">' + state.history.map((item) => {
            const id = esc(item.id);
            const subject = esc(item.subject || 'No subject');
            const n = parseInt(item.recipient_count, 10) || 0;
            return '<div class="data-row" data-history-row="' + id + '">' +
                '<span class="row-mark" aria-hidden="true"><i class="material-symbols-outlined icon-inline" aria-hidden="true">mail</i></span>' +
                '<button type="button" class="row-open" data-view="' + id + '" title="Open ' + subject + '">' +
                '<span class="row-title">' + subject + '</span>' +
                '<span class="row-meta"><span>Sent ' + esc(formatDate(item.sent_at)) + '</span></span>' +
                '</button>' +
                '<span class="row-recipients"><i class="material-symbols-outlined icon-inline" aria-hidden="true">group</i> ' + n + '</span>' +
                '<time class="row-time">' + esc(formatDate(item.sent_at)) + '</time>' +
                '<div class="row-trail">' +
                '<div class="dropdown">' +
                '<button class="btn-dropdown-trigger" type="button" data-bs-toggle="dropdown" aria-expanded="false"' +
                ' aria-label="More actions for ' + subject + '"><i class="material-symbols-outlined icon-inline" aria-hidden="true">more_vert</i></button>' +
                '<ul class="dropdown-menu dropdown-menu-end">' +
                '<li><button class="dropdown-item" data-view="' + id + '">' +
                '<i class="material-symbols-outlined icon-inline" style="color: var(--info);" aria-hidden="true">visibility</i> View</button></li>' +
                '<li><hr class="dropdown-divider"></li>' +
                '<li><button class="dropdown-item text-danger" data-delete="' + id + '">' +
                '<i class="material-symbols-outlined icon-inline" aria-hidden="true">delete</i> Delete</button></li>' +
                '</ul></div></div></div>';
        }).join('') + '</div>';
    }

    // ----------------------------------------------------------------------
    // Compose
    // ----------------------------------------------------------------------

    async function generate(btn) {
        busy(btn, true, 'Writing…');
        if (typeof window.showActionLoader === 'function') {
            window.showActionLoader('Drafting your newsletter…');
        }
        try {
            const res = await fetch('/api/newsletter/generate', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    topic: val('composeTopic'),
                    custom_intro: val('composeIntro'),
                    blog_limit: parseInt(val('composeLimit'), 10) || 5
                })
            });
            const data = await res.json();
            if (!data.success) {
                toast('error', 'Could not generate', data.error || 'Please try again.');
                return;
            }

            state.generated = data;
            fillEditor(data);
            $('[data-compose-setup]').hidden = true;
            $('[data-compose-editor]').hidden = false;
            toast('success', 'Draft ready', 'Edit anything below — the preview updates as you type.');
            renderPreview();
        } catch (err) {
            console.error('Generate failed:', err);
            toast('error', 'Could not generate', 'Check your connection and try again.');
        } finally {
            busy(btn, false);
            if (typeof window.hideActionLoader === 'function') window.hideActionLoader();
        }
    }

    function fillEditor(data) {
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ''; };
        set('finalSubject', data.subject);
        set('finalIntro', data.intro);
        set('finalCta', data.cta_text || 'Visit our blog');
        set('finalClosing', data.closing || 'Thanks for reading!');

        const list = $('[data-post-list]');
        if (!list) return;
        const posts = data.posts || [];
        list.innerHTML = posts.map((post, i) =>
            '<div class="compose-post">' +
            '<div class="compose-post-head">' +
            '<span class="compose-post-index">' + (i + 1) + '</span>' +
            '<span class="compose-post-title" title="' + esc(post.title) + '">' + esc(post.title) + '</span>' +
            '</div>' +
            // The value goes in as a text node, never interpolated into the
            // markup: a summary containing "</textarea>" would otherwise close
            // the field and inject whatever followed.
            '<textarea class="post-summary" data-index="' + i + '" rows="3"></textarea>' +
            '</div>'
        ).join('');

        $$('.post-summary', list).forEach((area, i) => {
            area.value = (posts[i] && posts[i].summary) || '';
        });
    }

    function collectPosts() {
        const source = (state.generated && state.generated.posts) || [];
        return $$('.post-summary').map((area, i) => ({
            title: (source[i] && source[i].title) || '',
            summary: area.value,
            id: (source[i] && source[i].id) || '',
            category: (source[i] && source[i].category) || ''
        }));
    }

    function payload() {
        return {
            subject: val('finalSubject'),
            intro: val('finalIntro'),
            posts: collectPosts(),
            cta_text: val('finalCta'),
            closing: val('finalClosing')
        };
    }

    // Rendering is a round trip to the server (it owns the email template), so
    // typing is debounced and each response carries a sequence number — a slow
    // earlier render must never overwrite a newer one.
    let renderTimer = null;

    function schedulePreview() {
        clearTimeout(renderTimer);
        renderTimer = setTimeout(renderPreview, 400);
    }

    async function renderPreview() {
        if (!state.generated) return;
        const seq = ++state.renderSeq;
        try {
            const res = await fetch('/api/newsletter/render', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload())
            });
            const data = await res.json();
            if (seq !== state.renderSeq) return;
            if (!data.success || !data.html) return;

            state.html = data.html;
            const frame = $('[data-preview-frame]');
            const stage = $('[data-preview-stage]');
            if (stage) {
                const placeholder = $('.preview-placeholder', stage);
                if (placeholder) placeholder.remove();
                const device = $('.preview-device', stage);
                if (device) device.hidden = false;
            }
            if (frame) frame.srcdoc = data.html;
        } catch (err) {
            if (seq === state.renderSeq) console.error('Preview render failed:', err);
        }
    }

    function resetComposer() {
        state.generated = null;
        state.html = null;
        $('[data-compose-editor]').hidden = true;
        $('[data-compose-setup]').hidden = false;
    }

    // ----------------------------------------------------------------------
    // Sending
    // ----------------------------------------------------------------------

    function openTestModal() {
        const modal = bsModal('testSendModal');
        if (modal) modal.show();
    }

    async function sendTest(btn) {
        const email = val('testEmail');
        if (!email || email.indexOf('@') === -1) {
            toast('error', 'Enter an email', 'A test needs somewhere to go.');
            return;
        }
        busy(btn, true, 'Sending…');
        try {
            await renderPreview();
            const res = await fetch('/api/newsletter/send', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subject: val('finalSubject'),
                    html_content: state.html,
                    test_mode: true,
                    test_email: email
                })
            });
            const data = await res.json();
            if (data.success) {
                const modal = bsModal('testSendModal');
                if (modal) modal.hide();
                toast('success', 'Test sent', 'Check ' + email + '.');
            } else {
                toast('error', 'Test failed', data.message || data.error || 'Please try again.');
            }
        } catch (err) {
            console.error('Test send failed:', err);
            toast('error', 'Test failed', 'Check your connection and try again.');
        } finally {
            busy(btn, false);
        }
    }

    // The confirm names its own blast radius. The old screen sent to every
    // subscriber on a single click of a button labelled just "Send", with no
    // confirmation and no undo.
    function openSendConfirm() {
        const subject = val('finalSubject');
        if (!subject) {
            toast('error', 'Subject required', 'An issue needs a subject line before it can go out.');
            const el = document.getElementById('finalSubject');
            if (el) el.focus();
            return;
        }

        const n = subscriberCount();
        if (!n) {
            toast('error', 'No subscribers', 'There is nobody to send this to yet.');
            return;
        }

        const countEl = $('[data-confirm-count]');
        if (countEl) countEl.textContent = n;
        const unitEl = $('[data-confirm-unit]');
        if (unitEl) unitEl.textContent = plural(n, 'subscriber', 'subscribers');
        const subjEl = $('[data-confirm-subject]');
        if (subjEl) subjEl.textContent = subject;
        const goEl = $('[data-confirm-send]');
        if (goEl) goEl.textContent = 'Send to ' + n + ' ' + plural(n, 'subscriber', 'subscribers');

        const modal = bsModal('sendConfirmModal');
        if (modal) modal.show();
    }

    async function doSend(btn) {
        busy(btn, true, 'Sending…');
        try {
            await renderPreview();
            const res = await fetch('/api/newsletter/send', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subject: val('finalSubject'),
                    html_content: state.html
                })
            });
            const data = await res.json();

            const modal = bsModal('sendConfirmModal');
            if (modal) modal.hide();

            if (data.success) {
                toast('success', 'Newsletter sent',
                    'Delivered to ' + data.sent + ' ' + plural(data.sent, 'subscriber', 'subscribers') + '.');
                if (data.failed) {
                    toast('warning', 'Some did not arrive',
                        data.failed + ' ' + plural(data.failed, 'address', 'addresses') + ' could not be reached.');
                }
                const sentEl = $('[data-stat-sent]');
                if (sentEl) sentEl.textContent = (parseInt(sentEl.textContent, 10) || 0) + 1;
                resetComposer();
                await loadHistory();
                showTab('history');
            } else {
                toast('error', 'Send failed', data.error || 'Please try again.');
            }
        } catch (err) {
            console.error('Send failed:', err);
            toast('error', 'Send failed', 'Check your connection and try again.');
        } finally {
            busy(btn, false);
        }
    }

    // ----------------------------------------------------------------------
    // Archive view / delete
    // ----------------------------------------------------------------------

    async function viewNewsletter(id) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        const frame = $('[data-archive-frame]');
        const title = $('[data-archive-title]');
        if (title) title.textContent = 'Loading…';
        if (frame) frame.srcdoc = '';

        const modal = bsModal('archiveModal');
        if (modal) modal.show();

        try {
            const res = await fetch('/api/newsletter/history/' + encodeURIComponent(id), { credentials: 'same-origin' });
            const data = await res.json();
            if (!data.success || !data.newsletter) {
                toast('error', 'Could not open', data.error || 'Newsletter not found.');
                if (modal) modal.hide();
                return;
            }

            const item = data.newsletter;
            if (title) title.textContent = item.subject || 'Newsletter';

            let html = item.html_content;
            if (!html) {
                // Sent before html_content was stored: show the preview text as
                // a plain document rather than an empty frame, and say why.
                html = '<!doctype html><meta charset="utf-8">' +
                    '<body style="font-family:system-ui,sans-serif;padding:24px;color:#202124;line-height:1.6">' +
                    '<h2 style="margin:0 0 12px">' + esc(item.subject || 'Newsletter') + '</h2>' +
                    '<p>' + esc(item.content_preview || 'No content stored.') + '</p>' +
                    '<p style="color:#80868B;font-size:13px;margin-top:24px">' +
                    'This issue predates full-content archiving, so only the preview text was kept.</p></body>';
            }
            if (frame) frame.srcdoc = html;
        } catch (err) {
            console.error('View failed:', err);
            toast('error', 'Could not open', 'Check your connection and try again.');
            if (modal) modal.hide();
        }
    }

    function askDelete(id) {
        if (typeof window.closeAllDropdowns === 'function') window.closeAllDropdowns();
        state.pendingDeleteId = id;
        const item = state.history.filter((h) => h.id === id)[0];
        const label = $('[data-delete-subject]');
        if (label) label.textContent = (item && item.subject) || 'this newsletter';
        const btn = $('[data-delete-confirm]');
        if (btn) { btn.disabled = false; btn.textContent = 'Delete'; }
        const modal = bsModal('deleteNewsletterModal');
        if (modal) modal.show();
    }

    async function doDelete(btn) {
        const id = state.pendingDeleteId;
        if (!id) return;
        busy(btn, true, 'Deleting…');
        try {
            const res = await fetch('/api/newsletter/history/' + encodeURIComponent(id), {
                method: 'DELETE',
                credentials: 'same-origin'
            });
            const data = await res.json();
            if (!data.success) {
                toast('error', 'Delete failed', data.error || 'Please try again.');
                return;
            }
            const modal = bsModal('deleteNewsletterModal');
            if (modal) modal.hide();
            state.pendingDeleteId = null;
            state.history = state.history.filter((h) => h.id !== id);
            renderHistory();
            paintSentTile();
            toast('success', 'Deleted', 'Removed from the archive.');
        } catch (err) {
            console.error('Delete failed:', err);
            toast('error', 'Delete failed', 'Check your connection and try again.');
        } finally {
            busy(btn, false);
        }
    }

    // ----------------------------------------------------------------------
    // Wiring
    // ----------------------------------------------------------------------

    root.addEventListener('click', (e) => {
        const t = e.target;

        const tab = t.closest('.newsletter-tabs .seg-tab');
        if (tab) { showTab(tab.dataset.tab); return; }

        const device = t.closest('.device-btn');
        if (device) {
            state.device = device.dataset.device;
            $$('.device-btn').forEach((b) => {
                const on = b.dataset.device === state.device;
                b.classList.toggle('is-active', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
            const frame = $('.compose-preview .preview-device');
            if (frame) frame.classList.toggle('is-mobile', state.device === 'mobile');
            return;
        }

        if (t.closest('[data-generate]')) { generate(t.closest('[data-generate]')); return; }
        if (t.closest('[data-discard]')) { resetComposer(); return; }
        if (t.closest('[data-open-test]')) { openTestModal(); return; }
        if (t.closest('[data-send-test]')) { sendTest(t.closest('[data-send-test]')); return; }
        if (t.closest('[data-open-send]')) { openSendConfirm(); return; }
        if (t.closest('[data-confirm-send]')) { doSend(t.closest('[data-confirm-send]')); return; }
        if (t.closest('[data-export-subscribers]')) { exportSubscribers(); return; }
        if (t.closest('[data-delete-confirm]')) { doDelete(t.closest('[data-delete-confirm]')); return; }

        const view = t.closest('[data-view]');
        if (view) { viewNewsletter(view.dataset.view); return; }

        const del = t.closest('[data-delete]');
        if (del) { askDelete(del.dataset.delete); return; }
    }, { signal });

    // Any edit repaints the preview.
    root.addEventListener('input', (e) => {
        const t = e.target;
        if (t.matches('#finalSubject, #finalIntro, #finalCta, #finalClosing, .post-summary')) {
            schedulePreview();
            return;
        }
        if (t.matches('[data-subscriber-search]')) {
            state.subscriberQuery = t.value.trim();
            renderSubscribers();
        }
    }, { signal });

    // The select-pill module writes the chosen value through to the real
    // <select> and fires a genuine `change`, so this stays a plain change
    // listener. It owns only the trigger's visible caption — the module
    // deliberately does not touch it, because what a trigger should read once
    // a value is applied differs per screen.
    function syncLimitLabel() {
        const select = document.getElementById('composeLimit');
        const label = document.getElementById('composeLimitValue');
        if (!select || !label) return;
        const opt = select.options[select.selectedIndex];
        if (opt) label.textContent = opt.textContent.trim();
    }

    root.addEventListener('change', (e) => {
        if (e.target.id === 'composeLimit') {
            syncLimitLabel();
            paintPostsTile();
        }
    }, { signal });

    // Enter in the setup fields generates, rather than doing nothing.
    root.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter') return;
        if (e.target.matches('#composeTopic')) {
            e.preventDefault();
            const btn = $('[data-generate]');
            if (btn && !btn.disabled) generate(btn);
        }
        if (e.target.matches('#testEmail')) {
            e.preventDefault();
            const btn = $('[data-send-test]');
            if (btn && !btn.disabled) sendTest(btn);
        }
    }, { signal });

    // ----------------------------------------------------------------------
    // Boot
    // ----------------------------------------------------------------------

    showTab('compose');
    syncLimitLabel();
    paintPostsTile();
    loadStatus();
    loadSubscribers();
    loadHistory();

    // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
    if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();
