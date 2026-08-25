/**
 * Create — the agent start state and the run that replaces it.
 *
 * The version this replaces declared four globals so three inline `onclick` /
 * `oninput` / `onkeydown` attributes could reach them, and bound nothing it
 * could later unbind. Two faults followed from that:
 *
 *   - Its poller was a bare setInterval with no owner. Navigating away by PJAX
 *     left it running against a DOM that had been thrown out, fetching
 *     /api/generate/status every three seconds until a hard reload — and if the
 *     task had expired (the manager drops tasks after 600s and answers 404) the
 *     response had no `status` field, so nothing ever cleared the interval.
 *   - A single network blip killed the poll outright and reset the form, while
 *     the generation carried on server-side and landed in Drafts unannounced.
 *
 * Everything below is one IIFE with an AbortController, the pattern the rest of
 * the page scripts use: listeners are bound with `{ signal }`, timers are
 * cleared on abort, and a fresh visit aborts the previous instance.
 *
 * The run is also survivable now. The task id goes into sessionStorage, so
 * leaving the page and coming back re-attaches to the run in progress instead
 * of showing an empty composer while the agent is still writing.
 *
 * ---------------------------------------------------------------------------
 * The run shows its work, as a conversation
 *
 * The run is a thread, not a card: the prompt as the reader's message, the
 * agent's turn beneath it, and the page itself as the scroller. The elevated,
 * centred panel this replaced read as a modal — a progress dialog wrapped
 * around an exchange — and its five-step checklist described work still to
 * come, which is a dialog's job rather than a conversation's.
 *
 * The pipeline reports three things while it runs, and this file renders them:
 *
 *   - The agent's plan for the piece, as it is formed. It comes out of the same
 *     model call that writes the post, ahead of it, so the reasoning panel is
 *     the reasoning the draft was written against — not a caption invented to
 *     fill the wait.
 *   - The draft itself, chunk by chunk. It is revealed with a drain loop rather
 *     than pasted in on each poll: the server's chunks arrive in bursts of
 *     hundreds of characters, and a preview that jumps a paragraph at a time
 *     reads as four repaints, not as writing.
 *   - Which stage is actually running, so "Writing the draft" is no longer on
 *     screen while the formatter works. It shows as a live status line at the
 *     foot of the turn, and each finished step leaves a ticked line in the
 *     trail — the past tense a conversation can carry.
 *
 * The poll carries cursors (`tc`, `cc`) and gets back only what it has not seen.
 * Sending no cursors means "everything from the start", which is exactly what a
 * reattaching browser wants — it replays the run instead of joining it blind.
 */

(function createBlogPage() {
    'use strict';

    if (window.__createBlogAbort) {
        try { window.__createBlogAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__createBlogAbort = controller;

    const root = document.querySelector('.dashboard-main');
    const stage = root && root.querySelector('[data-create-stage]');
    if (!stage) return;

    const $ = (sel) => stage.querySelector(sel);

    const el = {
        form: $('[data-create-form]'),
        box: $('[data-prompt-box]'),
        input: $('[data-prompt]'),
        submit: $('[data-submit]'),
        count: $('[data-prompt-count]'),
        dest: $('#promptDest'),
        destValue: $('#destValue'),
        destNote: $('[data-dest-note]'),
        turn: $('[data-agent-turn]'),
        runTitle: $('[data-run-title]'),
        runSub: $('[data-run-sub]'),
        runPct: $('[data-run-pct]'),
        runElapsed: $('[data-run-elapsed]'),
        runBar: $('[data-run-bar]'),
        runLive: $('[data-run-live]'),
        runPrompt: $('[data-run-prompt]'),
        runFail: $('[data-run-fail]'),
        runFailText: $('[data-run-fail-text]'),

        reason: $('[data-reason]'),
        reasonToggle: $('[data-reason-toggle]'),
        reasonTitle: $('[data-reason-title]'),
        reasonList: $('[data-reason-list]'),
        trail: $('[data-trail]'),
        streamWrap: $('[data-stream]'),
        streamText: $('[data-stream-text]'),
        streamCount: $('[data-stream-count]')
    };
    if (!el.form || !el.input || !el.submit) return;

    // ------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------

    // The stages _run_generation_task actually reports. 'outline' and
    // 'humanizing' were in the old message table and never fired: the pipeline
    // derives the outline from the generated headings with no LLM round-trip,
    // and humanization is a separate on-demand action from the drafts screen.
    const STAGE_TITLE = {
        starting: 'Warming up',
        content: 'Writing the draft',
        formatting: 'Formatting and styling',
        categorizing: 'Assigning a category',
        saving: 'Saving to your library',
        completed: 'Done'
    };

    // The writing stage has two halves that used to look identical: the model
    // composing before a single character exists, and the text arriving. They
    // are different waits and now say so.
    const THINKING_TITLE = 'Thinking it through';

    const POLL_MS = 2000;
    // While the draft is streaming the poll is the frame rate of the preview,
    // so it tightens. Only during that stage: the other four are single steps
    // with nothing to report between their start and end.
    const POLL_STREAM_MS = 900;
    // The first one is sooner than either. A worker picks the task up in
    // milliseconds and the model's plan lands about a second later, so waiting
    // out a full interval spent the whole thinking phase showing "Warming up".
    const POLL_FIRST_MS = 400;

    // Typewriter drain. The reveal chases the buffer instead of running at a
    // fixed speed — a fixed speed either falls minutes behind a fast model or
    // stutters on a slow one. Revealing 1/DRAIN_DIVISOR of what is outstanding
    // per tick keeps it about half a second behind the server whatever the
    // rate, and MIN_CHARS_PER_TICK stops the last few characters crawling.
    const REVEAL_TICK_MS = 33;
    const DRAIN_DIVISOR = 18;
    const MIN_CHARS_PER_TICK = 2;
    // A ceiling on how far behind the reveal may fall. The drain loop keeps
    // itself well inside this on its own (~600ms at a real generation rate);
    // the ceiling is for a backgrounded tab, where the timer is throttled to
    // roughly once a second. Coming back to a preview crawling through six
    // paragraphs it should already have shown would be worse than a jump.
    const MAX_BACKLOG_CHARS = 1200;
    const BACKLOG_KEEP_CHARS = 240;

    // How close to the bottom of the page still counts as "following along".
    // Generous enough to survive the pixel or two of drift a growing document
    // introduces between a scroll event and the next repaint, which would
    // otherwise unpin the view on its own and stop the page ever following.
    const PIN_SLACK_PX = 48;

    // Someone who asked for less motion gets the text, not the typing.
    const REDUCED_MOTION = window.matchMedia
        && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const SOFT_LIMIT = 1200;          // where the counter turns amber
    const COUNT_FROM = 200;           // below this a counter is just noise
    const RUN_TTL_MS = 10 * 60 * 1000; // task_manager.cleanup_expired's 600s
    const RUN_KEY = 'scriptly-create-run';
    const DRAFT_KEY = 'scriptly-create-draft';
    const MAX_NET_FAILS = 3;          // consecutive blips tolerated before giving up

    // ------------------------------------------------------------------
    // State
    // ------------------------------------------------------------------

    const run = {
        taskId: null,
        prompt: '',
        // Captured at submit time, not read back off the select when the run
        // finishes: a resumed run repaints a composer whose pill has reset to
        // its default, and the completion notice would name the wrong place.
        destLabel: '',
        startedAt: 0,
        stage: 'starting',
        progress: 5,
        stopped: false,
        pollTimer: null,
        clockTimer: null,
        netFails: 0
    };

    // Live output. `text` is everything the server has sent; `shown` is how much
    // of it has been revealed. The gap between them is the drain loop's job, and
    // keeping them as two numbers over one string is what lets a poll append
    // without disturbing what is already on screen.
    const stream = {
        text: '',
        shown: 0,
        tc: 0,              // thought cursor, sent back as ?tc=
        cc: 0,              // character cursor, sent back as ?cc=
        thoughts: 0,
        thinking: false,
        thoughtAt: 0,       // seconds from start to the first draft character
        drain: null,        // the reveal timer, running only while behind
        pinned: true        // following the text, until the reader scrolls up
    };

    // One shape, one writer — the resume path reads exactly these keys back.
    function parkRun() {
        store(RUN_KEY, {
            taskId: run.taskId,
            prompt: run.prompt,
            destLabel: run.destLabel,
            startedAt: run.startedAt,
            stage: run.stage,
            progress: run.progress
        });
    }

    function toast(type, title, message) {
        if (typeof window.showToast === 'function') {
            window.showToast({ type, title, message, duration: type === 'error' ? 5000 : 3000 });
        }
    }

    function store(key, value) {
        try {
            if (value === null) sessionStorage.removeItem(key);
            else sessionStorage.setItem(key, JSON.stringify(value));
        } catch (e) { /* private mode, quota — the page works without it */ }
    }

    function read(key) {
        try { return JSON.parse(sessionStorage.getItem(key)); } catch (e) { return null; }
    }

    // ------------------------------------------------------------------
    // Composer
    // ------------------------------------------------------------------

    function autoResize() {
        el.input.style.height = 'auto';
        el.input.style.height = el.input.scrollHeight + 'px';
    }

    function syncComposer() {
        const len = el.input.value.length;
        el.submit.disabled = el.input.value.trim().length === 0;

        if (el.count) {
            const show = len >= COUNT_FROM;
            el.count.hidden = !show;
            if (show) {
                el.count.textContent = len + ' / ' + el.input.getAttribute('maxlength');
                el.count.classList.toggle('is-long', len > SOFT_LIMIT);
            }
        }
    }

    function syncDest() {
        if (!el.dest) return;
        const opt = el.dest.selectedOptions[0];
        if (!opt) return;
        // The pill and the note both read off the option, so the two never
        // disagree and neither restates the copy the template already owns.
        if (el.destValue) el.destValue.textContent = opt.textContent.trim();
        if (el.destNote) el.destNote.textContent = opt.dataset.note || '';
    }

    // Drops a starter in and selects its first [bracketed] slot, so typing
    // replaces the part that belongs to the reader rather than landing at the
    // end of a sentence they then have to edit backwards into.
    function applyStarter(text) {
        el.input.value = text;
        autoResize();
        syncComposer();
        el.input.focus();

        const slot = /\[[^\]]+\]/.exec(text);
        if (slot) el.input.setSelectionRange(slot.index, slot.index + slot[0].length);
        else el.input.setSelectionRange(text.length, text.length);
    }

    // ------------------------------------------------------------------
    // Live output: the reasoning panel and the streamed draft
    // ------------------------------------------------------------------

    // The markdown preview renderer is shared with History, which paints the
    // opening of a finished draft with exactly these rules — see
    // js/components/draft-markdown.js. Read once into a local so a missing
    // component fails here, loudly and at boot, rather than on the first chunk
    // of text a reader is watching arrive.
    const renderMd = window.DraftMarkdown.render;

    const CARET = '<span class="stream-caret" aria-hidden="true"></span>';

    // Inside the last block rather than after everything, so the caret sits at
    // the end of the sentence being written instead of on a line of its own.
    function withCaret(html) {
        let at = -1;
        ['</p>', '</li>', '</h3>', '</h4>'].forEach((closer) => {
            const found = html.lastIndexOf(closer);
            if (found > at) at = found;
        });
        return at < 0 ? html + CARET : html.slice(0, at) + CARET + html.slice(at);
    }

    function renderStream(caret) {
        if (!el.streamText) return;

        const text = stream.text.slice(0, stream.shown);
        const html = renderMd(text);
        el.streamText.innerHTML = caret === false ? html : withCaret(html);

        if (el.streamCount) {
            const words = (text.match(/\S+/g) || []).length;
            el.streamCount.textContent = words ? words + (words === 1 ? ' word' : ' words') : '';
        }

        // The page follows the text, not an inner box — the draft is a message
        // in a thread, and a message does not scroll on its own. Following stops
        // the moment the reader scrolls up to read something: yanking the view
        // back down mid-sentence is the worst thing a live preview can do.
        if (stream.pinned) scrollToLatest();
    }

    function atPageBottom() {
        const doc = document.documentElement;
        return (doc.scrollHeight - window.scrollY - window.innerHeight) < PIN_SLACK_PX;
    }

    function scrollToLatest() {
        // 'instant', not smooth: a smooth scroll retargeted every 33ms never
        // arrives, and the caret drifts off the bottom of the screen.
        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
    }

    // A timer, not an animation frame, and deliberately so: a frame callback
    // stops firing altogether in a background tab, and this loop is the only
    // thing that moves text onto the screen. A reader who switched tabs for
    // twenty seconds would come back to a run that had written a whole post and
    // shown none of it, with a frame callback pending that never ran. A timer is
    // throttled when hidden, never abandoned. It runs only while there is a
    // backlog and stops itself the moment there is not.
    function scheduleDrain() {
        if (stream.drain != null || signal.aborted) return;
        stream.drain = setInterval(drainTick, REVEAL_TICK_MS);
    }

    function stopDrain() {
        if (stream.drain == null) return;
        clearInterval(stream.drain);
        stream.drain = null;
    }

    function drainTick() {
        const remaining = stream.text.length - stream.shown;
        if (remaining <= 0 || signal.aborted) {
            stopDrain();
            return;
        }

        stream.shown = Math.min(
            stream.text.length,
            stream.shown + Math.max(MIN_CHARS_PER_TICK, Math.ceil(remaining / DRAIN_DIVISOR))
        );
        renderStream(!run.stopped);
    }

    function revealAll() {
        stopDrain();
        stream.shown = stream.text.length;
        if (stream.text) renderStream(false);
    }

    function showStreamPanel() {
        if (el.streamWrap) el.streamWrap.hidden = false;
    }

    // `instant` is the reattach path: a browser coming back to a run in flight
    // is handed the whole draft so far, and typing out 6,000 characters it has
    // already missed would be theatre.
    function pushContent(chunk, instant) {
        if (!chunk) return;

        const first = !stream.text;
        stream.text += chunk;

        if (first) {
            showStreamPanel();
            stream.thoughtAt = Math.max(1, Math.round((Date.now() - run.startedAt) / 1000));
            setThinking(false);
        }

        if (instant || REDUCED_MOTION) {
            revealAll();
            if (!run.stopped) renderStream(true);
            return;
        }

        // Catch up in one step if the loop has not been running. Leaves a short
        // tail so the caret still has something to type out afterwards.
        if (stream.text.length - stream.shown > MAX_BACKLOG_CHARS) {
            stream.shown = stream.text.length - BACKLOG_KEEP_CHARS;
            renderStream(!run.stopped);
        }

        scheduleDrain();
    }

    function setThinking(on) {
        stream.thinking = !!on;
        if (el.reason) el.reason.dataset.thinking = on ? 'true' : 'false';
        if (!el.reasonTitle) return;
        el.reasonTitle.textContent = on
            ? 'Thinking'
            : (stream.thoughtAt ? 'Thought for ' + stream.thoughtAt + 's' : 'Its plan for this post');
    }

    // Opened as soon as the writing stage starts, with a placeholder, so the
    // first seconds of a run -- model composing, nothing emitted yet -- have an
    // indicator of their own instead of a title that says text is being written
    // when none is.
    function openThinking() {
        if (!el.reason || stream.text || stream.thoughts) return;
        el.reason.hidden = false;
        setThinking(true);

        if (el.reasonList && !el.reasonList.firstChild) {
            const pending = document.createElement('li');
            pending.className = 'reason-item is-pending';
            pending.dataset.pending = '';
            pending.textContent = 'Working out the angle for this piece…';
            el.reasonList.appendChild(pending);
        }
    }

    // Two kinds, two places, because they are two different things. A 'plan'
    // line is the model reasoning about a piece it has not written yet, and
    // belongs in the disclosure above the draft. A 'note' is the pipeline
    // reporting something it has just done — the section count, the word count,
    // the category — and belongs in the trail below it, ticked, in the past
    // tense. Putting both in one list made the reasoning panel drift from
    // "what it is thinking" to "a log", which is how it stopped being worth
    // reading.
    function addThought(entry) {
        if (!entry || !entry.text) return;
        if (entry.kind === 'note') {
            addTrailItem(entry.text);
            return;
        }

        if (!el.reasonList) return;

        if (el.reason && el.reason.hidden) {
            el.reason.hidden = false;
            // Only a claim of "thinking" while nothing has been written yet.
            // After that these are observations about work already done.
            if (!stream.text) setThinking(true);
        }

        const pending = el.reasonList.querySelector('[data-pending]');
        if (pending) pending.remove();

        const item = document.createElement('li');
        item.className = 'reason-item is-plan';
        item.textContent = entry.text;
        el.reasonList.appendChild(item);
        stream.thoughts += 1;
    }

    function addTrailItem(text) {
        if (!el.trail) return;
        const item = document.createElement('li');
        item.className = 'trail-item';

        const tick = document.createElement('i');
        tick.className = 'material-symbols-outlined icon-inline trail-tick';
        tick.textContent = 'check';
        tick.setAttribute('aria-hidden', 'true');

        const label = document.createElement('span');
        label.textContent = text;

        item.append(tick, label);
        el.trail.appendChild(item);
        if (stream.pinned) scrollToLatest();
    }
    // Deliberately not scrolled to the newest line the way the draft is. The
    // plan arrives in the first two seconds and then sits still, so the useful
    // view is the top of it — the angle, which is the line worth reading —
    // rather than the bottom. Following the newest entry here scrolled that
    // line out of a box that never moved again.

    function resetStream() {
        stopDrain();
        stream.text = '';
        stream.shown = 0;
        stream.tc = 0;
        stream.cc = 0;
        stream.thoughts = 0;
        stream.thoughtAt = 0;
        stream.pinned = true;

        if (el.reasonList) el.reasonList.textContent = '';
        if (el.reason) el.reason.hidden = true;
        if (el.streamText) el.streamText.textContent = '';
        if (el.streamCount) el.streamCount.textContent = '';
        if (el.streamWrap) el.streamWrap.hidden = true;
        if (el.trail) el.trail.textContent = '';
        if (el.reasonToggle) el.reasonToggle.setAttribute('aria-expanded', 'true');
        setThinking(false);
    }

    // ------------------------------------------------------------------
    // Run rendering
    // ------------------------------------------------------------------

    function fmtElapsed(ms) {
        const total = Math.max(0, Math.floor(ms / 1000));
        return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }

    function tickClock() {
        if (el.runElapsed) el.runElapsed.textContent = fmtElapsed(Date.now() - run.startedAt);
    }

    function paintRun(stageKey, progress, mode) {
        run.stage = stageKey;
        run.progress = progress;

        const pct = Math.max(0, Math.min(100, Number(progress) || 0));
        if (el.runBar) el.runBar.style.width = pct + '%';
        if (el.runPct) el.runPct.textContent = pct + '%';

        // An unmapped stage still gets a readable title rather than a blank
        // one, in case the pipeline gains a step before this file hears of it.
        let title = STAGE_TITLE[stageKey]
            || (stageKey ? stageKey.charAt(0).toUpperCase() + stageKey.slice(1) : 'Working');

        // "Writing the draft" is a lie until there is a draft. The model spends
        // the first seconds of that stage composing, which is a wait of its own
        // and now reads as one.
        if (stageKey === 'content' && !stream.text && mode !== 'done') {
            title = THINKING_TITLE;
            if (!run.stopped) openThinking();
        }
        if (el.runTitle) el.runTitle.textContent = title;
        if (el.runLive) el.runLive.textContent = title + ', ' + pct + ' percent';
    }

    function enterRun() {
        stage.dataset.state = 'working';
        run.stopped = false;
        if (el.turn) el.turn.classList.remove('is-settled', 'is-failed');
        if (el.runFail) el.runFail.hidden = true;
        if (el.runSub) {
            el.runSub.textContent = 'This runs on the server — you can move around the app and come back.';
        }
        if (el.runPrompt) el.runPrompt.textContent = run.prompt;

        // Called by both a fresh submit and a resume, and the resume repopulates
        // from the server's buffer, so both want the panels empty first.
        resetStream();

        paintRun(run.stage, run.progress);
        tickClock();

        // The composer sat mid-page; the thread starts at the top of the canvas.
        // Without this the reader is dropped into the middle of an empty column.
        window.scrollTo({ top: 0, behavior: 'instant' });
        stream.pinned = true;

        clearInterval(run.clockTimer);
        run.clockTimer = setInterval(tickClock, 1000);
    }

    function leaveRun(keepPrompt) {
        stopTimers();
        store(RUN_KEY, null);
        run.taskId = null;
        stage.dataset.state = 'idle';

        if (keepPrompt) {
            el.input.value = run.prompt;
            autoResize();
            syncComposer();
            el.input.focus();
            el.input.setSelectionRange(run.prompt.length, run.prompt.length);
        }
    }

    function stopTimers() {
        clearTimeout(run.pollTimer);
        clearInterval(run.clockTimer);
        run.pollTimer = null;
        run.clockTimer = null;
        stopDrain();
    }

    function failRun(message) {
        run.stopped = true;
        stopTimers();
        store(RUN_KEY, null);

        // Whatever was written stays on screen without a caret. It is the most
        // useful thing left after a failure — the reader can see how far it got,
        // and whether the prompt was the problem.
        setThinking(false);
        revealAll();
        if (el.reasonList) {
            const pending = el.reasonList.querySelector('[data-pending]');
            if (pending) pending.remove();
            if (el.reason && !el.reasonList.firstChild) el.reason.hidden = true;
        }

        if (el.turn) el.turn.classList.add('is-settled', 'is-failed');
        if (el.runTitle) el.runTitle.textContent = 'Generation stopped';
        if (el.runSub) el.runSub.textContent = 'Nothing was saved. Your prompt is below, unchanged.';
        if (el.runLive) el.runLive.textContent = 'Generation stopped. ' + message;
        if (el.runFailText) el.runFailText.textContent = message;
        if (el.runFail) el.runFail.hidden = false;

        toast('error', 'Generation stopped', message);
    }

    function completeRun(result) {
        run.stopped = true;
        stopTimers();
        store(RUN_KEY, null);
        store(DRAFT_KEY, null);

        const where = run.destLabel ? run.destLabel.toLowerCase() : 'your library';

        // The drain loop is mid-way through the last few hundred characters when
        // the server says done. Finish the text before leaving: a preview cut off
        // three words from the end is the one frame the reader remembers.
        setThinking(false);
        revealAll();

        if (el.turn) el.turn.classList.add('is-settled');
        paintRun('completed', 100, 'done');
        if (el.runSub) el.runSub.textContent = 'Taking you there now.';
        if (el.runLive) el.runLive.textContent = 'Blog generated. Opening it now.';

        toast('success', 'Blog generated', 'Written and filed — ' + where + '.');

        const target = (result && result.redirect) || '/drafts';
        // A beat longer than the old 900ms, because there is now a finished
        // draft on screen worth seeing land.
        setTimeout(() => { window.location.href = target; }, 1300);
    }

    // ------------------------------------------------------------------
    // Polling
    // ------------------------------------------------------------------

    // `slow` forces the calm interval after a network blip, so a hiccup does not
    // turn into four retries a second.
    function schedulePoll(slow) {
        clearTimeout(run.pollTimer);
        const streaming = !slow && run.stage === 'content';
        run.pollTimer = setTimeout(poll, streaming ? POLL_STREAM_MS : POLL_MS);
    }

    // Deltas from one status response. `instant` skips the typewriter, which is
    // what a reattaching browser wants for the text it already missed.
    function consumeStream(data, instant) {
        if (Array.isArray(data.thoughts)) data.thoughts.forEach(addThought);
        if (typeof data.thought_cursor === 'number') stream.tc = data.thought_cursor;

        if (data.content) pushContent(data.content, instant);
        if (typeof data.char_cursor === 'number') stream.cc = data.char_cursor;

        // The server capped the slice; come back for the rest without waiting
        // out a full poll interval, or the preview falls steadily behind.
        return typeof data.total_chars === 'number' && data.total_chars > stream.cc;
    }

    async function poll(instant) {
        if (!run.taskId) return;

        // A run older than the server keeps its tasks for cannot be recovered,
        // and polling it forever is exactly what the old interval did.
        if (Date.now() - run.startedAt > RUN_TTL_MS) {
            failRun('This run took too long to report back. Check Drafts before starting again — it may have finished.');
            return;
        }

        let res;
        try {
            // The cursors are what keep this cheap enough to poll under a
            // second: the response carries the new characters, not the draft.
            const url = '/api/generate/status/' + encodeURIComponent(run.taskId)
                + '?tc=' + stream.tc + '&cc=' + stream.cc;
            res = await fetch(url, { signal });
        } catch (err) {
            if (signal.aborted) return;
            // A blip is not a failure. The generation is server-side and does
            // not care that one poll missed.
            run.netFails += 1;
            if (run.netFails >= MAX_NET_FAILS) {
                failRun('Lost connection to the server. The blog may still be generating — check Drafts in a minute.');
                return;
            }
            if (el.runSub) el.runSub.textContent = 'Reconnecting…';
            schedulePoll(true);
            return;
        }

        if (res.status === 401) {
            window.location.href = '/login';
            return;
        }

        // 404 = the task expired or never existed; 403 = it belongs to someone
        // else. Both are terminal, and both used to fall through the old
        // handler's `data.status` check and poll forever.
        if (res.status === 404 || res.status === 403) {
            failRun('This run is no longer available. Check Drafts — it may have finished — then try again.');
            return;
        }

        let data;
        try {
            data = await res.json();
        } catch (err) {
            run.netFails += 1;
            if (run.netFails >= MAX_NET_FAILS) {
                failRun('The server sent something unreadable. Check Drafts before starting again.');
                return;
            }
            schedulePoll(true);
            return;
        }

        run.netFails = 0;
        if (el.runSub && stage.dataset.state === 'working' && !run.stopped) {
            el.runSub.textContent = 'This runs on the server — you can move around the app and come back.';
        }

        // Before the status branches, not inside them: a run that completed or
        // failed between two polls still has its last characters in this
        // response, and dropping them would leave the draft ending mid-word.
        const behind = consumeStream(data, instant);

        if (data.status === 'completed') {
            if (behind) {
                // The server capped the last slice and the run is already over.
                // Collect the rest before settling, or the draft on screen ends
                // 8,000 characters short of the one being saved. Terminates:
                // every pass advances the cursor, and `behind` is false once it
                // reaches the total.
                clearTimeout(run.pollTimer);
                run.pollTimer = setTimeout(() => poll(true), 0);
                return;
            }
            completeRun(data.result);
            return;
        }

        if (data.status === 'failed') {
            paintRun(data.stage || run.stage, data.progress || run.progress);
            failRun(data.error || 'The writing agent could not finish this one. Try a more specific prompt.');
            return;
        }

        paintRun(data.stage || run.stage, data.progress != null ? data.progress : run.progress);
        parkRun();

        if (behind) {
            // Still holding text we have not been given. Ask again immediately
            // rather than letting the preview drift a poll further behind.
            clearTimeout(run.pollTimer);
            run.pollTimer = setTimeout(poll, 0);
            return;
        }
        schedulePoll();
    }

    // ------------------------------------------------------------------
    // Submit
    // ------------------------------------------------------------------

    async function startRun(prompt) {
        const chosen = el.dest && el.dest.selectedOptions[0];

        // "Try again" reuses this path, so a poll left pending by the previous
        // attempt has to go before the state it would paint into is replaced.
        stopTimers();

        run.prompt = prompt;
        run.destLabel = chosen ? chosen.textContent.trim() : '';
        run.startedAt = Date.now();
        run.stage = 'starting';
        run.progress = 5;
        run.stopped = false;
        run.netFails = 0;
        run.taskId = null;

        enterRun();

        let data;
        try {
            const res = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    prompt: prompt,
                    auto_submit: chosen ? chosen.value === 'submit' : false
                }),
                signal
            });

            if (res.status === 401) {
                window.location.href = '/login';
                return;
            }
            data = await res.json();
        } catch (err) {
            if (signal.aborted) return;
            failRun('Could not reach the server. Check your connection and try again.');
            return;
        }

        if (!data || !data.success || !data.task_id) {
            failRun((data && data.error) || 'The server would not start this run. Try again.');
            return;
        }

        run.taskId = data.task_id;
        parkRun();
        clearTimeout(run.pollTimer);
        run.pollTimer = setTimeout(poll, POLL_FIRST_MS);
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    el.input.addEventListener('input', () => {
        autoResize();
        syncComposer();
        // Survives a PJAX hop away and back — .dashboard-main is rebuilt from
        // scratch, so a half-typed prompt is otherwise gone.
        store(DRAFT_KEY, { text: el.input.value, at: Date.now() });
    }, { signal });

    el.input.addEventListener('keydown', (e) => {
        const submitCombo = e.key === 'Enter' && (!e.shiftKey || e.metaKey || e.ctrlKey);
        if (submitCombo) {
            e.preventDefault();
            el.form.requestSubmit();
        }
    }, { signal });

    el.form.addEventListener('submit', (e) => {
        e.preventDefault();
        const prompt = el.input.value.trim();
        if (!prompt || run.taskId) return;
        store(DRAFT_KEY, null);
        startRun(prompt);
    }, { signal });

    if (el.dest) el.dest.addEventListener('change', syncDest, { signal });

    // The panel collapses on demand rather than on its own: the plan is the part
    // a writer wants to read, and a panel that folds itself away the moment the
    // draft starts is a panel nobody gets to finish reading.
    if (el.reasonToggle) {
        el.reasonToggle.addEventListener('click', () => {
            const open = el.reasonToggle.getAttribute('aria-expanded') === 'true';
            el.reasonToggle.setAttribute('aria-expanded', open ? 'false' : 'true');
        }, { signal });
    }

    // Stop following the text the moment the reader scrolls up, and start again
    // when they come back to the bottom. Without this, reading anything above
    // the last line is impossible while the draft is still arriving.
    window.addEventListener('scroll', () => {
        if (stage.dataset.state === 'working') stream.pinned = atPageBottom();
    }, { signal, passive: true });

    stage.addEventListener('click', (e) => {
        const starter = e.target.closest('[data-starter]');
        if (starter) {
            applyStarter(starter.dataset.promptText || '');
            return;
        }

        const action = e.target.closest('[data-action]');
        if (!action) return;

        if (action.dataset.action === 'retry') {
            const prompt = run.prompt;
            if (prompt) startRun(prompt); else leaveRun(false);
        } else if (action.dataset.action === 'edit-prompt') {
            leaveRun(true);
        }
    }, { signal });

    // A poller whose page has been swapped out by PJAX has nothing left to
    // paint. This is what the previous version had no way to notice.
    document.addEventListener('pjax:complete', () => {
        if (!document.contains(stage)) controller.abort();
    }, { signal });

    signal.addEventListener('abort', stopTimers);
    window.addEventListener('beforeunload', stopTimers, { signal });

    // ------------------------------------------------------------------
    // Boot
    // ------------------------------------------------------------------

    syncDest();

    const parked = read(RUN_KEY);
    if (parked && parked.taskId && (Date.now() - (parked.startedAt || 0)) < RUN_TTL_MS) {
        // Re-attach to a run already in flight. Painting the stored stage first
        // means the card is correct immediately rather than sitting at 5% for
        // the two seconds until the first poll answers.
        run.taskId = parked.taskId;
        run.prompt = parked.prompt || '';
        run.destLabel = parked.destLabel || '';
        run.startedAt = parked.startedAt;
        run.stage = parked.stage || 'starting';
        run.progress = parked.progress || 5;
        enterRun();
        // `true`: this poll returns everything the run has produced so far, and
        // typing out text the reader already missed would be theatre.
        poll(true);
    } else {
        store(RUN_KEY, null);

        const draft = read(DRAFT_KEY);
        if (draft && draft.text) {
            el.input.value = draft.text;
            autoResize();
        }
        syncComposer();

        // Not the `autofocus` attribute: PJAX injects this markup after load,
        // where autofocus does nothing. Skipped on narrow screens, where it
        // would throw up the on-screen keyboard over the whole page.
        if (window.matchMedia('(min-width: 768px)').matches) {
            el.input.focus();
            el.input.setSelectionRange(el.input.value.length, el.input.value.length);
        }
    }
})();
