/**
 * Studio — the conversational blog agent.
 *
 * One IIFE with an AbortController, the pattern every page script here uses:
 * listeners are bound with `{ signal }`, timers and connections are closed on
 * abort, and a fresh visit aborts the previous instance. PJAX swaps only
 * `.dashboard-main`'s innerHTML, so anything left running would be operating on
 * a DOM that has been thrown away — which is how create-blog.js used to leave a
 * poller hammering an endpoint until a hard reload.
 *
 * ---------------------------------------------------------------------------
 * How a turn is watched
 *
 * Sending a message does not return the reply. It returns a `turn_id`, because
 * a turn can research a topic, plan a post, write eleven hundred words and file
 * it — minutes of work that outlives the request and often outlives the tab.
 * So the browser *attaches* to the turn:
 *
 *   1. `EventSource` on /api/chat/turns/<id>/stream. Every frame carries an
 *      `id:`, which is the server's event cursor, so the browser's own
 *      `Last-Event-ID` handling resumes an interrupted stream for free.
 *   2. The server closes the stream after ~90s with a `reconnect` event rather
 *      than holding a worker thread for a whole turn. We reopen from the cursor.
 *      This is the normal case on a long turn, not an error.
 *   3. If EventSource fails outright (a proxy that buffers, a browser without
 *      it), the same event log is read by cursor poll. One source of truth, two
 *      transports — they cannot show different conversations.
 *
 * On load, if the server says a turn is still running in this conversation, we
 * attach to it with no cursor and replay it from the start. A user who closed
 * the tab mid-post comes back to the post being written, not to a dead screen.
 *
 * ---------------------------------------------------------------------------
 * Cards
 *
 * Structured attachments — an outline awaiting approval, a finished draft, a
 * delete confirmation — are rendered by `renderCard`, and by nothing else. The
 * same function draws a card arriving live over SSE and a card replayed from a
 * conversation opened three days later, because two renderers would eventually
 * disagree about what an outline looks like and only one of them would be
 * getting fixed.
 *
 * Everything a card renders is model-authored text, so it goes through
 * `DraftMarkdown.escape` (or `render`, which escapes first) on the way in.
 */

(function chatPage() {
    'use strict';

    if (window.__chatAbort) {
        try { window.__chatAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__chatAbort = controller;

    const root = document.querySelector('.dashboard-main');
    const shell = root && root.querySelector('[data-chat]');
    if (!shell) return;

    const $ = (sel) => shell.querySelector(sel);
    const $$ = (sel) => Array.from(shell.querySelectorAll(sel));

    const el = {
        rail: $('[data-list]'),
        more: $('[data-load-more]'),
        pane: $('.chat-pane'),
        blank: $('[data-blank]'),
        thread: $('[data-thread]'),
        form: $('[data-composer]'),
        input: $('[data-input]'),
        send: $('[data-send]'),
        stop: $('[data-stop]'),
        count: $('[data-count]'),
        hint: $('[data-hint]'),
        modal: root.querySelector('[data-confirm-modal]'),
        modalBody: root.querySelector('[data-confirm-body]'),
        modalWarn: root.querySelector('[data-confirm-warn]'),
        modalGo: root.querySelector('[data-confirm-go]')
    };
    if (!el.thread || !el.form || !el.input) return;

    const MD = window.DraftMarkdown || {
        escape: (t) => String(t),
        render: (t) => String(t)
    };

    // ------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------

    // Poll interval for the fallback transport. Slower than the SSE tick
    // because a poll is a whole request; fast enough that streamed text still
    // reads as writing rather than as paragraphs appearing.
    const POLL_MS = 900;

    // Consecutive transport failures tolerated before giving up on watching.
    // The turn itself continues server-side either way — what is lost is the
    // live view, and the reply is in the conversation on the next load.
    const MAX_FAILS = 4;

    // How close to the bottom still counts as "following along". Generous
    // enough to survive the pixel of drift a growing document introduces
    // between a scroll event and the next repaint, which would otherwise
    // unpin the view on its own.
    const PIN_SLACK_PX = 64;

    const COUNT_FROM = 400;      // below this a character counter is just noise
    const SOFT_LIMIT = 6000;     // where the counter turns amber

    const STAGE_LABEL = {
        thinking: 'Thinking',
        searching: 'Researching',
        outlining: 'Planning the post',
        writing: 'Writing the post',
        formatting: 'Formatting and filing',
        editing: 'Editing',
        listing: 'Looking through your posts'
    };

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------

    const state = {
        sessionId: shell.dataset.session || '',
        turnId: null,
        cursor: -1,
        es: null,
        pollTimer: null,
        fails: 0,
        watching: false,
        pinned: true,
        // The live agent turn's nodes, so an event does not have to re-query
        // the DOM for every token.
        turn: null,
        // Accumulated streamed text, and the rAF handle that flushes it.
        buffer: { text: '', draft: '', frame: null },
        pendingConfirm: null
    };

    // ------------------------------------------------------------------
    // Small helpers
    // ------------------------------------------------------------------

    function node(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    function icon(name) {
        const i = node('i', 'material-symbols-outlined icon-inline', name);
        i.setAttribute('aria-hidden', 'true');
        return i;
    }

    function toast(title, message, type) {
        if (window.showToast) {
            window.showToast({ title: title, message: message, type: type || 'info' });
        }
    }

    async function api(url, options) {
        const res = await fetch(url, Object.assign({ signal: signal }, options || {}));
        let data = null;
        try { data = await res.json(); } catch (e) { /* empty body */ }
        if (!res.ok) {
            const message = (data && (data.message || data.error))
                || `Request failed (${res.status})`;
            const error = new Error(message);
            error.status = res.status;
            error.data = data;
            throw error;
        }
        return data || {};
    }

    function json(body) {
        return {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        };
    }

    // The page scrolls, not an inner box — a message does not scroll
    // independently of the conversation it is in. Following is opt-out: once
    // the reader scrolls up to reread something, the view stops chasing.
    function atBottom() {
        const doc = document.documentElement;
        return (window.innerHeight + window.scrollY)
            >= (doc.scrollHeight - PIN_SLACK_PX);
    }

    function follow() {
        if (!state.pinned) return;
        window.scrollTo({ top: document.documentElement.scrollHeight });
    }

    window.addEventListener('scroll', () => {
        state.pinned = atBottom();
    }, { passive: true, signal: signal });

    // ------------------------------------------------------------------
    // Composer
    // ------------------------------------------------------------------

    function autosize() {
        el.input.style.height = 'auto';
        el.input.style.height = Math.min(el.input.scrollHeight, 320) + 'px';
    }

    function syncComposer() {
        const value = el.input.value.trim();
        el.send.disabled = !value || state.watching;

        const length = el.input.value.length;
        if (length < COUNT_FROM) {
            el.count.hidden = true;
        } else {
            el.count.hidden = false;
            el.count.textContent = `${length} / 8000`;
            el.count.classList.toggle('is-warn', length > SOFT_LIMIT);
        }
        autosize();
    }

    el.input.addEventListener('input', syncComposer, { signal: signal });

    el.input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
            event.preventDefault();
            el.form.requestSubmit();
        }
    }, { signal: signal });

    el.form.addEventListener('submit', (event) => {
        event.preventDefault();
        const text = el.input.value.trim();
        if (!text || state.watching) return;
        send(text);
    }, { signal: signal });

    $$('[data-starter]').forEach((button) => {
        button.addEventListener('click', () => {
            el.input.value = button.dataset.text || '';
            el.input.focus();
            // Select the first [bracketed] slot so the reader types over the
            // part that is theirs.
            const match = /\[[^\]]+\]/.exec(el.input.value);
            if (match) {
                el.input.setSelectionRange(match.index, match.index + match[0].length);
            }
            syncComposer();
        }, { signal: signal });
    });

    // ------------------------------------------------------------------
    // Rendering: messages
    // ------------------------------------------------------------------

    function showThread() {
        shell.dataset.state = 'open';
    }

    function addUserMessage(text) {
        const turn = node('article', 'turn is-you');
        turn.appendChild(node('p', 'turn-who', 'You'));
        turn.appendChild(node('div', 'turn-bubble', text));
        el.thread.appendChild(turn);
        return turn;
    }

    function addSystemMessage(text) {
        const line = node('p', 'turn-note is-system');
        line.appendChild(icon('check_circle'));
        line.appendChild(document.createTextNode(' ' + text));
        el.thread.appendChild(line);
    }

    /**
     * Build the live agent turn and remember its nodes.
     *
     * The status foot and the step list are created up front but hidden until
     * something fills them: an empty "Thinking" block that promises reasoning
     * and shows none is worse than no block at all.
     */
    function openAgentTurn() {
        const turn = node('article', 'turn is-agent is-live');

        const main = node('div', 'turn-main');
        main.appendChild(node('p', 'turn-who', 'Scriptly'));

        const live = node('p', 'visually-hidden');
        live.setAttribute('role', 'status');
        live.setAttribute('aria-live', 'polite');
        main.appendChild(live);

        // Steps — what it actually did, as it does it. The same disclosure the
        // create screen uses for the model's plan.
        const reason = node('div', 'reason');
        reason.hidden = true;
        const head = node('button', 'reason-head');
        head.type = 'button';
        head.setAttribute('aria-expanded', 'true');
        const dots = node('span', 'reason-dots');
        dots.setAttribute('aria-hidden', 'true');
        dots.innerHTML = '<i></i><i></i><i></i>';
        head.appendChild(dots);
        const title = node('span', 'reason-title', 'Working');
        head.appendChild(title);
        const caret = icon('expand_more');
        caret.classList.add('reason-caret');
        head.appendChild(caret);
        reason.appendChild(head);
        const body = node('div', 'reason-body');
        const list = node('ol', 'reason-list');
        body.appendChild(list);
        reason.appendChild(body);
        main.appendChild(reason);

        head.addEventListener('click', () => {
            const open = head.getAttribute('aria-expanded') === 'true';
            head.setAttribute('aria-expanded', String(!open));
        }, { signal: signal });

        const text = node('div', 'turn-text');
        main.appendChild(text);

        const cards = node('div', 'turn-cards');
        main.appendChild(cards);

        const foot = node('div', 'turn-foot');
        const status = node('p', 'turn-status');
        const dot = node('span', 'status-dot');
        dot.setAttribute('aria-hidden', 'true');
        status.appendChild(dot);
        const stage = node('span', 'status-stage', 'Thinking');
        status.appendChild(stage);
        foot.appendChild(status);
        main.appendChild(foot);

        turn.appendChild(main);
        el.thread.appendChild(turn);

        state.turn = {
            root: turn, main: main, reason: reason, title: title, list: list,
            text: text, cards: cards, foot: foot, stage: stage, live: live,
            draft: null, draftBody: null, steps: 0
        };
        state.buffer.text = '';
        state.buffer.draft = '';
        follow();
        return state.turn;
    }

    function closeAgentTurn(failed) {
        if (!state.turn) return;
        flushBuffers();
        state.turn.root.classList.remove('is-live');
        state.turn.root.classList.add(failed ? 'is-failed' : 'is-settled');
        // The steps stay, collapsed: they are the record of what happened, and
        // a reader debugging a surprising answer wants them one click away.
        if (state.turn.steps) {
            state.turn.title.textContent =
                `${state.turn.steps} step${state.turn.steps === 1 ? '' : 's'}`;
            state.turn.reason.querySelector('.reason-head')
                .setAttribute('aria-expanded', 'false');
        }
        state.turn.foot.remove();
        state.turn = null;
    }

    /**
     * Flush accumulated streamed text on an animation frame.
     *
     * Markdown is re-rendered from the whole buffer rather than appended to,
     * because a chunk boundary lands mid-syntax constantly — `**bo` + `ld**`
     * only becomes bold when both halves are present. Re-rendering is cheap for
     * a chat reply and acceptable for a draft; doing it once per frame instead
     * of once per event is what keeps a fast model from causing a repaint storm.
     */
    function flushBuffers() {
        state.buffer.frame = null;
        if (!state.turn) return;
        if (state.buffer.text) {
            state.turn.text.innerHTML = MD.render(state.buffer.text);
        }
        if (state.buffer.draft && state.turn.draftBody) {
            state.turn.draftBody.innerHTML = MD.render(state.buffer.draft);
        }
        follow();
    }

    function scheduleFlush() {
        if (state.buffer.frame) return;
        state.buffer.frame = window.requestAnimationFrame(flushBuffers);
    }

    function ensureDraftCard() {
        if (state.turn.draft) return state.turn.draft;
        const card = node('div', 'chat-card is-draft');
        const head = node('div', 'chat-card-head');
        head.appendChild(icon('draft'));
        head.appendChild(node('span', 'chat-card-kind', 'Writing'));
        card.appendChild(head);
        const draft = node('div', 'draft');
        const body = node('div', 'draft-body');
        draft.appendChild(body);
        card.appendChild(draft);
        state.turn.cards.appendChild(card);
        state.turn.draft = card;
        state.turn.draftBody = body;
        return card;
    }

    function addStep(label) {
        if (!state.turn) return null;
        state.turn.reason.hidden = false;
        state.turn.steps += 1;
        const item = node('li', 'reason-item is-pending', label);
        state.turn.list.appendChild(item);
        follow();
        return item;
    }

    // ------------------------------------------------------------------
    // Rendering: cards
    // ------------------------------------------------------------------

    /**
     * One renderer for every card kind, live or replayed.
     *
     * Returns an element or null. An unknown kind returns null rather than
     * throwing: a card added server-side before this script is redeployed
     * should be invisible, not fatal.
     */
    function renderCard(kind, data) {
        data = data || {};
        switch (kind) {
            case 'outline': return outlineCard(data);
            case 'outline_approved': return noteCard('check_circle',
                `Outline approved${data.title ? ': ' + data.title : ''}.`);
            case 'approval_required': return noteCard('pending',
                'That outline still needs your approval before it can be written.');
            case 'blog': return blogCard(data, 'Draft saved');
            case 'blog_edited': return editedCard(data);
            case 'blog_preview': return previewCard(data);
            case 'blog_list': return listCard(data);
            case 'sources': return sourcesCard(data);
            case 'confirm_delete': return confirmCard(data);
            case 'deleted': return noteCard('delete',
                `"${data.title || 'That post'}" was deleted.`);
            default: return null;
        }
    }

    function cardShell(cls, iconName, kind) {
        const card = node('div', 'chat-card ' + cls);
        const head = node('div', 'chat-card-head');
        head.appendChild(icon(iconName));
        head.appendChild(node('span', 'chat-card-kind', kind));
        card.appendChild(head);
        return card;
    }

    function noteCard(iconName, text) {
        const card = node('p', 'chat-note');
        card.appendChild(icon(iconName));
        card.appendChild(document.createTextNode(' ' + text));
        return card;
    }

    function outlineCard(data) {
        const card = cardShell('is-outline', 'checklist', 'Outline');
        if (data.revision > 1) {
            card.querySelector('.chat-card-head')
                .appendChild(node('span', 'chat-chip', `revision ${data.revision}`));
        }

        card.appendChild(node('h3', 'chat-card-title', data.title || 'Untitled'));
        if (data.angle) card.appendChild(node('p', 'chat-card-angle', data.angle));
        if (data.audience) {
            card.appendChild(node('p', 'chat-card-meta', 'For: ' + data.audience));
        }

        const list = node('ol', 'chat-outline');
        (data.sections || []).forEach((section) => {
            const item = node('li', 'chat-outline-item');
            item.appendChild(node('p', 'chat-outline-head', section.heading || ''));
            const points = section.points || [];
            if (points.length) {
                const ul = node('ul', 'chat-outline-points');
                points.forEach((point) => ul.appendChild(node('li', null, point)));
                item.appendChild(ul);
            }
            list.appendChild(item);
        });
        card.appendChild(list);

        if ((data.sources || []).length) {
            const details = node('details', 'chat-sources');
            details.appendChild(node('summary', null,
                `${data.sources.length} source${data.sources.length === 1 ? '' : 's'}`));
            const ul = node('ul', 'chat-source-list');
            data.sources.forEach((source) => {
                const li = node('li');
                if (source.url) {
                    const a = node('a', null, source.title || source.url);
                    a.href = source.url;
                    a.target = '_blank';
                    a.rel = 'noopener noreferrer';
                    li.appendChild(a);
                } else {
                    li.textContent = source.title || '';
                }
                ul.appendChild(li);
            });
            details.appendChild(ul);
            card.appendChild(details);
        }

        const meta = node('p', 'chat-card-foot',
            `${(data.sections || []).length} sections · ${data.target_words || 'medium'} · ${data.tone || 'professional'} tone`);
        card.appendChild(meta);

        // The approval gate, as two buttons. Approving is a request from this
        // browser — the agent has no way to do it — so this is the actual
        // human-in-the-loop control and not a restatement of one.
        if (data.status !== 'approved' && data.outline_id) {
            const actions = node('div', 'chat-card-actions');

            const approve = node('button', 'app-btn is-primary');
            approve.type = 'button';
            approve.appendChild(icon('check'));
            approve.appendChild(node('span', null, 'Approve and write it'));
            approve.addEventListener('click', () => {
                approveOutline(data.outline_id, actions);
            }, { signal: signal });

            const change = node('button', 'app-btn is-ghost');
            change.type = 'button';
            change.appendChild(icon('edit'));
            change.appendChild(node('span', null, 'Change something'));
            change.addEventListener('click', () => {
                el.input.value = '';
                el.input.placeholder = 'What should change about the outline?';
                el.input.focus();
                syncComposer();
            }, { signal: signal });

            actions.appendChild(approve);
            actions.appendChild(change);
            card.appendChild(actions);
        }

        return card;
    }

    function blogCard(data, kind) {
        const card = cardShell('is-blog', 'article', kind);
        card.appendChild(node('h3', 'chat-card-title', data.title || 'Untitled'));

        const meta = [];
        if (data.word_count) meta.push(`${data.word_count} words`);
        if (data.reading_time) meta.push(data.reading_time);
        if (data.section_count) meta.push(`${data.section_count} sections`);
        if (data.category) meta.push(data.category);
        if (data.status) meta.push(data.status.toLowerCase().replace('_', ' '));
        card.appendChild(node('p', 'chat-card-meta', meta.join(' · ')));

        if (data.partial) {
            card.appendChild(noteCard('warning',
                'The stream was cut short, so this draft may be unfinished.'));
        }

        if (data.excerpt) {
            const details = node('details', 'chat-excerpt');
            details.appendChild(node('summary', null, 'Read the opening'));
            const body = node('div', 'draft-body');
            body.innerHTML = MD.render(data.excerpt);
            details.appendChild(body);
            card.appendChild(details);
        }

        card.appendChild(blogActions(data));
        return card;
    }

    function editedCard(data) {
        const card = cardShell('is-edited', 'edit_note', 'Edit applied');
        card.appendChild(node('h3', 'chat-card-title', data.title || 'Untitled'));
        if (data.instruction) {
            card.appendChild(node('p', 'chat-card-angle', '“' + data.instruction + '”'));
        }
        card.appendChild(node('p', 'chat-card-meta', data.summary || ''));
        if (data.structure_unchanged === false) {
            card.appendChild(noteCard('warning',
                'The section structure changed — worth a look.'));
        }
        if (data.excerpt) {
            const details = node('details', 'chat-excerpt');
            details.appendChild(node('summary', null, 'Read the opening'));
            const body = node('div', 'draft-body');
            body.innerHTML = MD.render(data.excerpt);
            details.appendChild(body);
            card.appendChild(details);
        }
        card.appendChild(blogActions(data));
        return card;
    }

    function previewCard(data) {
        const card = cardShell('is-preview', 'visibility', 'Post');
        card.appendChild(node('h3', 'chat-card-title', data.title || 'Untitled'));
        const meta = [];
        if (data.status) meta.push(data.status.toLowerCase().replace('_', ' '));
        if (data.word_count) meta.push(`${data.word_count} words`);
        if (data.reading_time) meta.push(data.reading_time);
        if (data.category) meta.push(data.category);
        card.appendChild(node('p', 'chat-card-meta', meta.join(' · ')));

        if (data.markdown) {
            const details = node('details', 'chat-excerpt');
            details.appendChild(node('summary', null, 'Read the whole post'));
            const body = node('div', 'draft-body');
            body.innerHTML = MD.render(data.markdown);
            details.appendChild(body);
            card.appendChild(details);
        }
        card.appendChild(blogActions(data));
        return card;
    }

    function blogActions(data) {
        const actions = node('div', 'chat-card-actions');
        if (data.blog_id) {
            const open = node('a', 'app-btn is-ghost');
            open.href = '/drafts?blog=' + encodeURIComponent(data.blog_id);
            open.appendChild(icon('open_in_new'));
            open.appendChild(node('span', null, 'Open in Drafts'));
            actions.appendChild(open);
        }
        return actions;
    }

    function listCard(data) {
        const card = cardShell('is-list', 'list', 'Your posts');
        const total = data.total || (data.items || []).length;
        card.appendChild(node('p', 'chat-card-meta',
            `${total} post${total === 1 ? '' : 's'}`));

        const list = node('ul', 'chat-blog-list');
        (data.items || []).forEach((item) => {
            const li = node('li', 'chat-blog-row');
            const link = node('a', 'chat-blog-title',
                item.title || 'Untitled');
            link.href = '/drafts?blog=' + encodeURIComponent(item.blog_id || '');
            li.appendChild(link);
            const meta = [item.status, item.category, item.updated]
                .filter(Boolean).join(' · ');
            li.appendChild(node('span', 'chat-blog-meta', meta));
            list.appendChild(li);
        });
        card.appendChild(list);
        return card;
    }

    function sourcesCard(data) {
        const card = cardShell('is-sources', 'travel_explore', 'Research');
        card.appendChild(node('p', 'chat-card-meta',
            `“${data.query || ''}” · ${(data.items || []).length} results`
            + (data.cached ? ' · cached' : '')));

        const list = node('ul', 'chat-source-list');
        (data.items || []).forEach((item) => {
            const li = node('li');
            const a = node('a', null, item.title || item.url || '');
            a.href = item.url || '#';
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            li.appendChild(a);
            if (item.snippet) li.appendChild(node('p', 'chat-source-snip', item.snippet));
            list.appendChild(li);
        });
        card.appendChild(list);
        return card;
    }

    /**
     * The delete confirmation, in the thread.
     *
     * The button opens a modal rather than deleting on click. Two steps for a
     * permanent action, and the modal is where the consequence is spelled out —
     * a card in a scrolling conversation is too easy to click past.
     */
    function confirmCard(data) {
        const card = cardShell('is-confirm', 'delete_forever', 'Confirm deletion');
        card.appendChild(node('h3', 'chat-card-title', data.title || 'Untitled'));
        card.appendChild(node('p', 'chat-card-meta',
            (data.status || 'DRAFT').toLowerCase().replace('_', ' ')
            + (data.word_count ? ` · ${data.word_count} words` : '')));

        if (data.published) {
            card.appendChild(noteCard('public_off',
                'This is published — it will disappear from your live site.'));
        }

        const actions = node('div', 'chat-card-actions');
        const go = node('button', 'app-btn is-danger');
        go.type = 'button';
        go.appendChild(icon('delete'));
        go.appendChild(node('span', null, 'Delete permanently'));
        go.addEventListener('click', () => openConfirm(data, card), { signal: signal });

        const keep = node('button', 'app-btn is-ghost');
        keep.type = 'button';
        keep.textContent = 'Keep it';
        keep.addEventListener('click', () => {
            actions.remove();
            card.appendChild(noteCard('block', 'Left alone.'));
        }, { signal: signal });

        actions.appendChild(go);
        actions.appendChild(keep);
        card.appendChild(actions);
        return card;
    }

    // ------------------------------------------------------------------
    // Hydration of server-rendered messages
    // ------------------------------------------------------------------

    /**
     * Render markdown and cards into the messages the server already painted.
     *
     * The text arrives as plain text in the bubble (correct without JS); this
     * upgrades the agent's side to rendered markdown and appends its cards. The
     * user's own messages are left as plain text on purpose — their asterisks
     * are asterisks.
     */
    function hydrate() {
        const script = root.querySelector('[data-chat-bootstrap]');
        if (!script) return;

        let messages = [];
        try { messages = JSON.parse(script.textContent || '[]'); } catch (e) { return; }

        const byId = {};
        messages.forEach((message) => { byId[message.id] = message; });

        $$('[data-message]').forEach((turnEl) => {
            const message = byId[turnEl.dataset.id];
            if (!message) return;

            const textEl = turnEl.querySelector('[data-text]');
            if (textEl && message.text) {
                textEl.innerHTML = MD.render(message.text);
            }

            const cardsEl = turnEl.querySelector('[data-cards]');
            if (!cardsEl) return;
            (message.cards || []).forEach((card) => {
                const rendered = renderCard(card.kind, card.data);
                if (rendered) cardsEl.appendChild(rendered);
            });
        });

        if (messages.length) showThread();
        window.scrollTo({ top: document.documentElement.scrollHeight });
    }

    // ------------------------------------------------------------------
    // Sending and watching
    // ------------------------------------------------------------------

    async function ensureSession() {
        if (state.sessionId) return state.sessionId;
        const data = await api('/api/chat/sessions', json({}));
        state.sessionId = data.session.id;
        shell.dataset.session = state.sessionId;
        prependRailRow(data.session);
        // The URL carries the conversation so a reload stays in it, without a
        // navigation that would tear down this script mid-turn.
        window.history.replaceState({}, '',
            '/chat?s=' + encodeURIComponent(state.sessionId));
        return state.sessionId;
    }

    async function send(text) {
        el.input.value = '';
        el.input.placeholder = 'Describe a topic, or ask for a change…';
        syncComposer();
        showThread();
        state.pinned = true;

        addUserMessage(text);
        follow();

        try {
            const sessionId = await ensureSession();
            const data = await api(
                `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
                json({ message: text })
            );
            attach(data.turn_id, -1);
        } catch (error) {
            if (error.name === 'AbortError') return;
            if (error.status === 409 && error.data && error.data.turn_id) {
                // Already working. Attach rather than complain: the user's
                // message did not land, but showing them the turn in progress
                // explains why better than an error would.
                attach(error.data.turn_id, -1);
                toast('Still working', 'Finishing the previous message first.', 'info');
                return;
            }
            failTurn(error.message || 'Your message could not be sent.');
        }
    }

    /**
     * Attach to a turn: open the agent bubble and start streaming into it.
     *
     * `cursor` of -1 replays the turn from its first event, which is what a
     * reattaching browser wants — it sees the whole turn rather than joining
     * halfway with no idea what happened.
     */
    function attach(turnId, cursor) {
        if (!turnId) return;
        detach();

        state.turnId = turnId;
        state.cursor = (cursor == null) ? -1 : cursor;
        state.fails = 0;
        state.watching = true;
        el.stop.hidden = false;
        syncComposer();
        openAgentTurn();
        openStream();
    }

    function closeStream() {
        if (!state.es) return;
        try { state.es.close(); } catch (e) { /* already closed */ }
        state.es = null;
    }

    function detach() {
        state.watching = false;
        closeStream();
        if (state.pollTimer) {
            window.clearTimeout(state.pollTimer);
            state.pollTimer = null;
        }
        el.stop.hidden = true;
        syncComposer();
    }

    function openStream() {
        if (!window.EventSource) {
            startPolling();
            return;
        }

        const url = `/api/chat/turns/${encodeURIComponent(state.turnId)}/stream`
            + `?cursor=${encodeURIComponent(state.cursor)}`;

        let source;
        try {
            source = new EventSource(url, { withCredentials: true });
        } catch (e) {
            startPolling();
            return;
        }
        state.es = source;

        // One handler per event type, because the server names them — a single
        // onmessage would only receive frames with no `event:` line.
        ['status', 'thought', 'tool_start', 'tool_end', 'token', 'draft',
            'card', 'message'].forEach((type) => {
                source.addEventListener(type, (event) => {
                    onFrame(type, event);
                });
            });

        source.addEventListener('done', (event) => {
            onFrame('done', event);
            detach();
            finishTurn(false);
        });

        source.addEventListener('end', () => {
            detach();
            finishTurn(false);
        });

        source.addEventListener('reconnect', (event) => {
            // The server closed a healthy stream to free its worker thread.
            // Reopening from the cursor is the expected continuation, not a
            // recovery — see the module comment.
            const frame = parse(event);
            if (frame && frame.data && typeof frame.data.cursor === 'number') {
                state.cursor = frame.data.cursor;
            }
            closeStream();
            if (state.watching) openStream();
        });

        // Both the server's `error` frame and the transport's own failure
        // arrive here. `parse` tells them apart: only the former has a body.
        source.addEventListener('error', (event) => {
            const frame = parse(event);

            if (frame) {
                onFrame('error', event);
                detach();
                failTurn((frame.data && frame.data.message) || 'The turn failed.');
                return;
            }

            // Transport fault. EventSource retries on its own, which is usually
            // right; counting the failures is what lets a genuinely broken
            // transport — a proxy that buffers the whole response, say — fall
            // back to polling instead of retrying behind it forever.
            state.fails += 1;
            if (state.fails < MAX_FAILS) return;
            closeStream();
            if (state.watching) startPolling();
        });
    }

    /**
     * The event object out of an SSE frame, or null.
     *
     * Null means "this is not a server frame", which is the load-bearing case:
     * `addEventListener('error')` on an EventSource receives BOTH a frame the
     * server named `error` and the transport's own failure notification, and
     * those need opposite responses — report the turn as failed, versus let the
     * browser retry. A transport event carries no `data`, so the absence of a
     * parseable body is the discriminator.
     */
    function parse(event) {
        if (!event || typeof event.data !== 'string') return null;
        try { return JSON.parse(event.data); } catch (e) { return null; }
    }

    function startPolling() {
        if (!state.watching) return;

        const tick = async () => {
            if (!state.watching) return;
            try {
                const data = await api(
                    `/api/chat/turns/${encodeURIComponent(state.turnId)}`
                    + `?cursor=${encodeURIComponent(state.cursor)}`
                );
                state.fails = 0;
                (data.events || []).forEach((event) => {
                    state.cursor = event.i;
                    handleEvent(event.type, event.data || {});
                });
                if (data.status !== 'running') {
                    detach();
                    finishTurn(data.status === 'failed');
                    return;
                }
            } catch (error) {
                if (error.name === 'AbortError') return;
                state.fails += 1;
                if (error.status === 404 || state.fails >= MAX_FAILS) {
                    detach();
                    failTurn('Lost track of that turn. Reload to see the reply.');
                    return;
                }
            }
            state.pollTimer = window.setTimeout(tick, POLL_MS);
        };

        state.pollTimer = window.setTimeout(tick, 0);
    }

    function onFrame(type, event) {
        const parsed = parse(event);
        if (!parsed) return;
        const frame = parsed.data || {};
        if (typeof frame.i === 'number') state.cursor = frame.i;
        handleEvent(type, frame.data || {});
    }

    // ------------------------------------------------------------------
    // The event handlers
    // ------------------------------------------------------------------

    function handleEvent(type, data) {
        if (!state.turn && type !== 'done' && type !== 'error') return;

        switch (type) {
            case 'status': {
                const label = data.label || STAGE_LABEL[data.stage] || 'Working';
                state.turn.stage.textContent = label;
                state.turn.live.textContent = label;
                break;
            }
            case 'thought':
                addStep(data.text || '');
                break;
            case 'tool_start': {
                const item = addStep(data.label || (data.name || '').replace(/_/g, ' '));
                if (item) item.dataset.tool = data.name || '';
                state.turn.stage.textContent = data.label || 'Working';
                break;
            }
            case 'tool_end': {
                // Settle the newest pending step for this tool, rather than the
                // newest step overall: tools can overlap in one turn and ticking
                // the wrong line is worse than ticking none.
                const items = Array.from(
                    state.turn.list.querySelectorAll('.reason-item.is-pending')
                ).filter((n) => !data.name || n.dataset.tool === data.name);
                const item = items[items.length - 1];
                if (item) {
                    item.classList.remove('is-pending');
                    item.classList.add(data.ok ? 'is-done' : 'is-failed');
                }
                break;
            }
            case 'token':
                state.buffer.text += (data.text || '');
                scheduleFlush();
                break;
            case 'draft':
                ensureDraftCard();
                state.buffer.draft += (data.text || '');
                scheduleFlush();
                break;
            case 'card': {
                const rendered = renderCard(data.kind, data.data);
                if (rendered) {
                    state.turn.cards.appendChild(rendered);
                    follow();
                }
                // A finished draft card supersedes the live one: the same text,
                // but with the word count and the actions attached.
                if (data.kind === 'blog' && state.turn.draft) {
                    state.turn.draft.remove();
                    state.turn.draft = null;
                    state.turn.draftBody = null;
                }
                break;
            }
            case 'message':
                if (data.message_id) state.turn.root.dataset.id = data.message_id;
                break;
            case 'done':
                finishTurn(false);
                break;
            case 'error':
                failTurn(data.message || 'The turn failed.');
                break;
            default:
                break;
        }
    }

    function finishTurn(failed) {
        if (!state.turn) return;
        closeAgentTurn(failed);
        state.turnId = null;
        detach();
        follow();
        refreshRailRow();
    }

    function failTurn(message) {
        if (state.turn) {
            const fail = node('p', 'chat-fail');
            fail.appendChild(icon('warning'));
            fail.appendChild(document.createTextNode(' ' + message));
            state.turn.main.appendChild(fail);
            closeAgentTurn(true);
        } else {
            toast('Something went wrong', message, 'error');
        }
        state.turnId = null;
        detach();
        follow();
    }

    el.stop.addEventListener('click', () => {
        // Stops *watching*, not the turn. The turn is server-side work that a
        // browser cannot cancel, and pretending otherwise would leave a user
        // thinking a post was not written when it was.
        detach();
        if (state.turn) closeAgentTurn(false);
        toast('Stopped watching',
            'The agent is still working. Reload to see the reply.', 'info');
    }, { signal: signal });

    // ------------------------------------------------------------------
    // Approval and confirmation
    // ------------------------------------------------------------------

    async function approveOutline(outlineId, actions) {
        actions.querySelectorAll('button').forEach((b) => { b.disabled = true; });
        try {
            const data = await api(
                `/api/chat/outlines/${encodeURIComponent(outlineId)}/approve`,
                json({})
            );
            actions.replaceWith(noteCard('check_circle', 'Approved — writing it now.'));
            addUserMessage('Approved — write the post from that outline.');
            state.pinned = true;
            if (data.turn_id) attach(data.turn_id, -1);
        } catch (error) {
            if (error.name === 'AbortError') return;
            actions.querySelectorAll('button').forEach((b) => { b.disabled = false; });
            toast('Could not approve', error.message, 'error');
        }
    }

    function openConfirm(data, card) {
        state.pendingConfirm = { token: data.token, card: card };
        el.modalBody.textContent =
            `“${data.title || 'Untitled'}” will be permanently deleted. This cannot be undone.`;
        el.modalWarn.hidden = !data.published;
        el.modal.hidden = false;
        el.modalGo.focus();
    }

    function closeConfirm() {
        el.modal.hidden = true;
        state.pendingConfirm = null;
    }

    root.querySelectorAll('[data-confirm-cancel]').forEach((n) => {
        n.addEventListener('click', closeConfirm, { signal: signal });
    });

    el.modalGo.addEventListener('click', async () => {
        const pending = state.pendingConfirm;
        if (!pending) return;
        el.modalGo.disabled = true;
        try {
            const data = await api('/api/chat/confirm', json({
                token: pending.token,
                session_id: state.sessionId
            }));
            closeConfirm();
            const actions = pending.card.querySelector('.chat-card-actions');
            if (actions) actions.remove();
            pending.card.appendChild(noteCard('delete', data.message || 'Deleted.'));
            addSystemMessage(data.message || 'Deleted.');
            follow();
        } catch (error) {
            if (error.name !== 'AbortError') {
                toast('Could not delete', error.message, 'error');
            }
        } finally {
            el.modalGo.disabled = false;
        }
    }, { signal: signal });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && !el.modal.hidden) closeConfirm();
    }, { signal: signal });

    // ------------------------------------------------------------------
    // The rail
    // ------------------------------------------------------------------

    function prependRailRow(session) {
        if (!el.rail) return;
        const row = node('div', 'chat-row is-active');
        row.dataset.id = session.id;
        row.setAttribute('role', 'listitem');

        const main = node('a', 'chat-row-main');
        main.href = '/chat?s=' + encodeURIComponent(session.id);
        main.appendChild(node('span', 'chat-row-title',
            session.title || 'New conversation'));
        const meta = node('span', 'chat-row-meta');
        meta.appendChild(node('span', 'chat-row-when', 'now'));
        main.appendChild(meta);
        row.appendChild(main);

        $$('.chat-row').forEach((n) => n.classList.remove('is-active'));
        el.rail.prepend(row);
    }

    function refreshRailRow() {
        // The title is derived server-side from the first message, so after the
        // first turn the rail row still says "New conversation". Rather than
        // refetch the list, take the title from the first user message on
        // screen — the same string the server derived it from.
        if (!state.sessionId || !el.rail) return;
        const row = el.rail.querySelector(`[data-id="${state.sessionId}"]`);
        if (!row) return;
        const title = row.querySelector('.chat-row-title');
        if (!title || title.textContent !== 'New conversation') return;
        const first = el.thread.querySelector('.turn.is-you .turn-bubble');
        if (first) title.textContent = first.textContent.slice(0, 80);
    }

    $$('[data-new-chat]').forEach((button) => {
        button.addEventListener('click', () => {
            // A navigation, not an in-place reset: a fresh page is the simplest
            // way to be certain no state from the previous conversation — a
            // focus pointer, a half-watched turn — survives into the new one.
            window.location.href = '/chat';
        }, { signal: signal });
    });

    $$('[data-delete-session]').forEach(bindDeleteSession);

    function bindDeleteSession(button) {
        button.addEventListener('click', async (event) => {
            event.preventDefault();
            event.stopPropagation();
            const id = button.dataset.deleteSession;
            if (!id) return;
            if (!window.confirm(
                'Delete this conversation? The posts it produced are untouched.')) {
                return;
            }
            try {
                const data = await api(
                    '/api/chat/sessions/' + encodeURIComponent(id),
                    { method: 'DELETE' }
                );
                const row = button.closest('.chat-row');
                if (row) row.remove();
                toast('Conversation deleted', data.message || '', 'success');
                if (id === state.sessionId) window.location.href = '/chat';
            } catch (error) {
                if (error.name !== 'AbortError') {
                    toast('Could not delete', error.message, 'error');
                }
            }
        }, { signal: signal });
    }

    if (el.more) {
        el.more.addEventListener('click', async () => {
            el.more.disabled = true;
            try {
                const data = await api('/api/chat/sessions?before='
                    + encodeURIComponent(el.more.dataset.cursor || ''));
                (data.items || []).forEach((session) => {
                    const row = node('div', 'chat-row');
                    row.dataset.id = session.id;
                    row.setAttribute('role', 'listitem');
                    const main = node('a', 'chat-row-main');
                    main.href = '/chat?s=' + encodeURIComponent(session.id);
                    main.appendChild(node('span', 'chat-row-title',
                        session.title || 'New conversation'));
                    const meta = node('span', 'chat-row-meta');
                    meta.appendChild(node('span', 'chat-row-when',
                        (session.updated_at || '').slice(0, 10)));
                    main.appendChild(meta);
                    row.appendChild(main);
                    el.rail.appendChild(row);
                });
                if (data.has_more) {
                    el.more.dataset.cursor = data.next_cursor;
                    el.more.disabled = false;
                } else {
                    el.more.remove();
                }
            } catch (error) {
                el.more.disabled = false;
                if (error.name !== 'AbortError') {
                    toast('Could not load more', error.message, 'error');
                }
            }
        }, { signal: signal });
    }

    // ------------------------------------------------------------------
    // Start
    // ------------------------------------------------------------------

    hydrate();
    syncComposer();

    // Reattach to a turn that is still running in this conversation. The check
    // is one request and it is what makes closing the tab mid-post survivable:
    // reopening the page rejoins the turn instead of showing a conversation
    // whose last message is the user's.
    if (state.sessionId) {
        api('/api/chat/sessions/' + encodeURIComponent(state.sessionId))
            .then((data) => {
                if (data.active_turn) {
                    showThread();
                    attach(data.active_turn, -1);
                }
            })
            .catch(() => { /* nothing to rejoin */ });
    }

    if (!el.thread.children.length) el.input.focus();
})();
