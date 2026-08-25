/**
 * Formatting Tools — markdown in, HTML · table of contents · statistics out.
 *
 * The previous version declared eleven globals (loadDrafts, refreshDrafts,
 * loadDraftContent, viewSelectedDraft, useThisDraft, formatContent, clearAll,
 * copyTOC, copyHTML, copyRawHTML, displayResults) so the template could call
 * them from inline onclick attributes, and reported every failure — a dropped
 * request, a rejected clipboard, an empty textarea — through alert().
 *
 * Everything here is one IIFE, every listener goes through an AbortController
 * the next run aborts, and every control is reached by delegation off
 * .dashboard-main. PJAX re-injects this file on each visit to /formatting-tools,
 * so a listener bound at module scope would accumulate one copy per visit.
 *
 * The three feature flags are no longer re-derived here: /api/format returns
 * has_code_blocks / has_images / has_tables, and the server counts them against
 * the *cleaned* content while this file's regexes ran against the raw textarea
 * — so a fenced block inside a comment could be counted here and not there.
 */

(function formattingToolsPage() {
    'use strict';

    if (window.__formattingAbort) {
        try { window.__formattingAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__formattingAbort = controller;

    const root = document.querySelector('.dashboard-main');
    if (!root) return;

    const $ = (sel, scope) => (scope || root).querySelector(sel);
    const $$ = (sel, scope) => Array.from((scope || root).querySelectorAll(sel));

    const body = $('[data-body="format"]');
    const draftSelect = $('[data-draft-select]');
    const titleField = $('[data-title]');
    const contentField = $('[data-content]');
    if (!body || !draftSelect || !titleField || !contentField) return;

    const state = {
        drafts: [],
        draftsLoaded: false,
        previewed: null,     // the blog behind the open preview modal
        result: null,        // the last successful /api/format payload
        retry: null
    };

    // ----------------------------------------------------------------------
    // Helpers
    // ----------------------------------------------------------------------

    // Escapes all five characters Jinja's autoescape does — the TOC rows build
    // an href attribute, so encoding only &, < and > would leave a heading
    // containing a quote able to close it.
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

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    function draftModal() {
        const el = document.getElementById('fmtDraftModal');
        if (!el || typeof bootstrap === 'undefined') return null;
        return bootstrap.Modal.getOrCreateInstance(el);
    }

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

    async function copyText(text) {
        if (!text) return false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
                return true;
            }
        } catch (e) { /* falls through to the textarea */ }

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

    async function copyAndSay(text, what) {
        const ok = await copyText(text);
        toast(ok ? 'success' : 'error', ok ? 'Copied' : 'Could not copy',
            ok ? what + ' is on your clipboard.'
               : 'Your browser blocked clipboard access — select the text and copy it.');
    }

    function fileSize(chars) {
        // Characters, not bytes: the figure is a rough weight for a reader
        // deciding whether to open the dump, and it says which unit it is in.
        if (chars < 1024) return chars + ' characters';
        return (chars / 1024).toFixed(1) + 'K characters';
    }

    // The stored blog may be a plain string or the structured content dict the
    // rest of the app uses. Markdown is preferred: the whole point of this
    // screen is to run the formatter over the source, and feeding it the
    // already-rendered HTML makes it a no-op.
    function markdownOf(blog) {
        if (!blog) return '';
        const content = blog.content;
        if (content && typeof content === 'object') {
            return content.markdown || content.original_markdown || content.body || '';
        }
        return content || '';
    }

    function htmlOf(blog) {
        if (!blog) return '';
        const content = blog.content;
        if (content && typeof content === 'object') {
            return content.html || content.body || '';
        }
        return content || '';
    }

    // Blog titles arrive with markdown emphasis baked into them.
    function plainTitle(blog) {
        return String((blog && blog.title) || '').replace(/\*\*/g, '').trim();
    }

    // ----------------------------------------------------------------------
    // Draft picker
    // ----------------------------------------------------------------------

    function selectedText(select) {
        const opt = select.options[select.selectedIndex];
        return opt ? opt.textContent : '';
    }

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

        // Titles are not truncated to 60 characters any more — the pill
        // ellipsises what does not fit, and the menu is as wide as its trigger,
        // so the reader sees the real title instead of a hard-cut one.
        draftSelect.innerHTML = '<option value="">' + esc(placeholder) + '</option>' +
            drafts.map((d) => '<option value="' + esc(d.id) + '">' +
                esc(String(d.title || 'Untitled').replace(/\*\*/g, '')) + '</option>').join('');

        if (menu) {
            menu.innerHTML = '<button type="button" class="menu-item" role="option" data-value="">' +
                '<i class="material-symbols-outlined icon-inline menu-check" aria-hidden="true">check</i>' +
                '<span class="menu-label">' + esc(placeholder) + '</span></button>' +
                drafts.map((d) => '<button type="button" class="menu-item" role="option" ' +
                    'data-value="' + esc(d.id) + '">' +
                    '<i class="material-symbols-outlined icon-inline menu-check" aria-hidden="true">check</i>' +
                    '<span class="menu-label">' +
                    esc(String(d.title || 'Untitled').replace(/\*\*/g, '')) +
                    '</span></button>').join('');
        }

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
            if (!message) return;
            fillDraftPicker([], 'Could not load drafts');
            toast('error', 'Drafts unavailable', message);
        }
    }

    function fillForm(blog) {
        titleField.value = plainTitle(blog);
        contentField.value = markdownOf(blog);
    }

    // Choosing a draft loads it straight into the fields — the same behaviour
    // the old onchange had, but it says so when it fails instead of logging to
    // a console nobody has open.
    async function loadDraftIntoForm(id) {
        if (!id) return;
        try {
            const data = await requestJson('/api/get_blog/' + encodeURIComponent(id));
            if (!data.blog) throw Object.assign(new Error('That draft came back empty.'), { handled: true });
            fillForm(data.blog);
            toast('info', 'Draft loaded', 'Edit it here if you like — nothing is saved back.');
        } catch (err) {
            const message = describeError(err);
            if (message) toast('error', 'Could not load that draft', message);
        }
    }

    // ----------------------------------------------------------------------
    // Draft preview modal
    // ----------------------------------------------------------------------

    function readingTimeOf(text) {
        const words = text.split(/\s+/).filter(Boolean).length;
        return { words, minutes: Math.max(1, Math.ceil(words / 200)) };
    }

    async function previewDraft(btn) {
        const id = draftSelect.value;
        if (!id) {
            toast('warning', 'Pick a draft', 'Choose which draft you want to look at first.');
            const trigger = $('[data-select-trigger]', draftSelect.closest('[data-select-pill]'));
            if (trigger) trigger.focus();
            return;
        }

        busy(btn, true, 'Loading…');
        try {
            const data = await requestJson('/api/get_blog/' + encodeURIComponent(id));
            const blog = data.blog;
            if (!blog) throw Object.assign(new Error('That draft came back empty.'), { handled: true });
            state.previewed = blog;

            const title = $('[data-draft-title]');
            if (title) title.textContent = plainTitle(blog) || 'Untitled';

            const category = $('[data-draft-category]');
            if (category) category.textContent = blog.category || 'General';

            const date = $('[data-draft-date]');
            if (date) {
                const when = blog.updated_at ? new Date(blog.updated_at) : null;
                date.textContent = when && !isNaN(when.getTime())
                    ? when.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
                    : '—';
            }

            const html = htmlOf(blog);
            const host = $('[data-draft-content]');
            if (host) {
                // The reader's own stored article, rendered as the site renders
                // it. The empty case is a sentence, not a blank panel.
                host.innerHTML = html || '<p class="fmt-toc-empty">This draft has no content yet.</p>';
            }

            const plain = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
            const read = readingTimeOf(plain);
            const reading = $('[data-draft-reading]');
            if (reading) reading.textContent = read.minutes + ' min read';
            const words = $('[data-draft-words]');
            if (words) words.textContent = read.words.toLocaleString() + ' words';

            const modal = draftModal();
            if (modal) modal.show();
            else toast('info', 'Preview unavailable', 'The dialog script did not load — reload the page.');
        } catch (err) {
            const message = describeError(err);
            if (message) toast('error', 'Could not open that draft', message);
        } finally {
            busy(btn, false);
        }
    }

    function useDraft() {
        if (!state.previewed) return;
        fillForm(state.previewed);
        const modal = draftModal();
        if (modal) modal.hide();
        contentField.focus();
        toast('info', 'Loaded into the workbench', 'Format it whenever you are ready.');
    }

    // ----------------------------------------------------------------------
    // Format
    // ----------------------------------------------------------------------

    function tocList(toc) {
        if (!toc.length) {
            return '<p class="fmt-toc-empty">No headings found. Add a line starting with # and the ' +
                'contents list builds itself.</p>';
        }
        return '<ul class="fmt-toc-list">' + toc.map((item) => {
            const level = Math.min(6, Math.max(1, num(item.level, 1)));
            return '<li class="fmt-toc-item is-l' + level + '">' +
                '<a href="#' + esc(item.slug) + '">' + esc(item.text) + '</a></li>';
        }).join('') + '</ul>';
    }

    function headingsLine(counts) {
        const parts = [];
        for (let i = 1; i <= 6; i += 1) {
            const n = counts && counts['h' + i];
            if (n) parts.push('<span>H' + i + ' ×' + num(n) + '</span>');
        }
        return parts.join('');
    }

    function setFlag(key, value) {
        const el = $('[data-flag="' + key + '"]', body);
        if (!el) return;
        const word = $('[data-flag-state]', el);

        // An absent field is not the same answer as `false`: a response from
        // before the API returned these must not claim "no".
        if (value === undefined || value === null) {
            el.className = 'fmt-flag';
            if (word) word.textContent = '—';
            return;
        }
        el.className = 'fmt-flag ' + (value ? 'is-present' : 'is-absent');
        if (word) word.textContent = value ? 'yes' : 'no';
    }

    function stat(sel, value) {
        const el = $(sel, body);
        if (!el) return;
        el.textContent = value === null || value === undefined ? '—' : value;
    }

    function renderResult(data) {
        state.result = data;

        const reading = $('[data-reading-time]', body);
        if (reading) reading.textContent = data.reading_time || '— min read';

        setFlag('code', data.has_code_blocks);
        setFlag('images', data.has_images);
        setFlag('tables', data.has_tables);

        const stats = data.statistics || {};
        stat('[data-stat-words]', num(stats.word_count).toLocaleString());
        stat('[data-stat-sentences]', num(stats.sentence_count).toLocaleString());
        stat('[data-stat-paragraphs]', num(stats.paragraph_count).toLocaleString());
        stat('[data-stat-characters]', num(stats.character_count).toLocaleString());
        stat('[data-stat-avg]', stats.avg_words_per_sentence === undefined
            ? null : String(stats.avg_words_per_sentence));

        const toc = Array.isArray(data.toc) ? data.toc : [];
        const tocHost = $('[data-toc]', body);
        if (tocHost) tocHost.innerHTML = tocList(toc);
        const tocCount = $('[data-toc-count]', body);
        if (tocCount) tocCount.textContent = String(toc.length);

        const headings = $('[data-headings]', body);
        if (headings) {
            const line = headingsLine(stats.headings_count);
            headings.innerHTML = line;
            headings.hidden = !line;
        }

        const html = data.html || '';
        const preview = $('[data-preview]', body);
        if (preview) {
            preview.innerHTML = html ||
                '<p class="fmt-toc-empty">The formatter returned no markup for this content.</p>';
        }

        const raw = $('[data-raw]', body);
        if (raw) raw.value = html;
        const rawSize = $('[data-raw-size]', body);
        if (rawSize) rawSize.textContent = fileSize(html.length);

        setHead('Result', (titleField.value.trim() || 'Untitled') + ' · ' + (data.reading_time || ''));
        setState('results');
    }

    async function format(btn) {
        const content = contentField.value.trim();
        if (!content) {
            toast('warning', 'Nothing to format', 'Paste some markdown, or load one of your drafts.');
            contentField.focus();
            return;
        }

        state.retry = () => format(btn);
        busy(btn, true, 'Formatting…');
        setHead('Formatting', '');
        setState('loading');

        try {
            const data = await requestJson('/api/format', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: titleField.value, content: contentField.value })
            });
            renderResult(data.formatted || {});
        } catch (err) {
            const message = describeError(err);
            if (message === null) return;             // navigated away
            setHead('Result', 'The last run failed');
            setState('error', message);
            toast('error', 'That did not work', message);
        } finally {
            busy(btn, false);
        }
    }

    function clearAll() {
        titleField.value = '';
        contentField.value = '';
        state.result = null;
        state.retry = null;
        setHead('Result', 'Nothing formatted yet');
        setState('empty');
        titleField.focus();
    }

    function tocMarkdown() {
        const toc = (state.result && Array.isArray(state.result.toc)) ? state.result.toc : [];
        if (!toc.length) return '';
        return '## Table of Contents\n\n' + toc.map((item) => {
            const indent = '  '.repeat(Math.max(0, num(item.level, 1) - 1));
            return indent + '- [' + item.text + '](#' + item.slug + ')';
        }).join('\n') + '\n';
    }

    // ----------------------------------------------------------------------
    // Events — one delegated listener, all of it aborted on the next visit
    // ----------------------------------------------------------------------

    root.addEventListener('click', async (e) => {
        // A contents entry scrolls the preview panel rather than navigating the
        // dashboard to a fragment: the anchors it points at live inside a
        // scrolling box, and a real hash jump would move the page under it.
        const tocLink = e.target.closest('.fmt-toc-item a');
        if (tocLink) {
            e.preventDefault();
            const id = decodeURIComponent((tocLink.getAttribute('href') || '').slice(1));
            const preview = $('[data-preview]', body);
            const target = id && preview
                ? preview.querySelector('[id="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]')
                : null;
            if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            else toast('info', 'Not in the preview', 'That heading has no anchor in the rendered HTML.');
            return;
        }

        const btn = e.target.closest('[data-action]');
        if (!btn) return;

        switch (btn.dataset.action) {
            case 'format': format(btn); break;
            case 'clear': clearAll(); break;
            case 'retry':
                if (state.retry) state.retry(); else clearAll();
                break;
            case 'reload-drafts':
                loadDrafts(true);
                toast('info', 'Reloading drafts', 'Fetching your latest drafts.');
                break;
            case 'preview-draft': previewDraft(btn); break;
            case 'use-draft': useDraft(); break;
            case 'copy-toc': {
                const markdown = tocMarkdown();
                if (!markdown) {
                    toast('warning', 'No contents list', 'This content has no headings to copy.');
                    break;
                }
                await copyAndSay(markdown, 'The contents list');
                break;
            }
            case 'copy-html': {
                const preview = $('[data-preview]', body);
                await copyAndSay(preview ? preview.innerHTML : '', 'The rendered HTML');
                break;
            }
            case 'copy-raw': {
                const raw = $('[data-raw]', body);
                await copyAndSay(raw ? raw.value : '', 'The raw HTML');
                break;
            }
        }
    }, { signal });

    draftSelect.addEventListener('change', () => {
        syncPillLabel(draftSelect);
        loadDraftIntoForm(draftSelect.value);
    }, { signal });

    loadDrafts();
})();
