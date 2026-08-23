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
        runCard: $('.run-card'),
        runTitle: $('[data-run-title]'),
        runSub: $('[data-run-sub]'),
        runPct: $('[data-run-pct]'),
        runElapsed: $('[data-run-elapsed]'),
        runBar: $('[data-run-bar]'),
        runLive: $('[data-run-live]'),
        runPrompt: $('[data-run-prompt]'),
        runFail: $('[data-run-fail]'),
        runFailText: $('[data-run-fail-text]'),
        steps: Array.from(stage.querySelectorAll('.run-step'))
    };
    if (!el.form || !el.input || !el.submit) return;

    // ------------------------------------------------------------------
    // Constants
    // ------------------------------------------------------------------

    // The stages _run_generation_task actually reports, in order. 'outline' and
    // 'humanizing' were in the old message table and never fired: the pipeline
    // derives the outline from the generated headings with no LLM round-trip,
    // and humanization is a separate on-demand action from the drafts screen.
    const STAGES = ['starting', 'content', 'formatting', 'categorizing', 'saving'];

    const STAGE_TITLE = {
        starting: 'Warming up',
        content: 'Writing the draft',
        formatting: 'Formatting and styling',
        categorizing: 'Assigning a category',
        saving: 'Saving to your library',
        completed: 'Done'
    };

    const POLL_MS = 2000;
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
    // Run rendering
    // ------------------------------------------------------------------

    function fmtElapsed(ms) {
        const total = Math.max(0, Math.floor(ms / 1000));
        return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
    }

    function tickClock() {
        if (el.runElapsed) el.runElapsed.textContent = fmtElapsed(Date.now() - run.startedAt);
    }

    // mode: 'running' — up to stageKey done, stageKey pulsing, the rest pending
    //       'done'    — every step ticked
    //       'failed'  — stageKey marked failed, anything after it never ran
    //
    // The mode is a parameter rather than something inferred from run.stopped,
    // because a failed step must not keep the pulsing "is-current" dot: an
    // animation that says "still working" over a run that has stopped is the
    // one thing this card exists to avoid.
    function paintSteps(stageKey, mode) {
        const at = STAGES.indexOf(stageKey);
        el.steps.forEach((step, i) => {
            const before = at >= 0 && i < at;
            const on = at >= 0 && i === at;
            const after = at >= 0 && i > at;

            step.classList.toggle('is-done', mode === 'done' || before);
            step.classList.toggle('is-current', mode === 'running' && on);
            step.classList.toggle('is-failed', mode === 'failed' && on);
            step.classList.toggle('is-skipped', mode === 'failed' && after);
        });
    }

    function paintRun(stageKey, progress, mode) {
        run.stage = stageKey;
        run.progress = progress;

        const pct = Math.max(0, Math.min(100, Number(progress) || 0));
        if (el.runBar) el.runBar.style.width = pct + '%';
        if (el.runPct) el.runPct.textContent = pct + '%';

        // An unmapped stage still gets a readable title rather than a blank
        // one, in case the pipeline gains a step before this file hears of it.
        const title = STAGE_TITLE[stageKey]
            || (stageKey ? stageKey.charAt(0).toUpperCase() + stageKey.slice(1) : 'Working');
        if (el.runTitle) el.runTitle.textContent = title;
        if (el.runLive) el.runLive.textContent = title + ', ' + pct + ' percent';

        paintSteps(stageKey, mode || 'running');
    }

    function enterRun() {
        stage.dataset.state = 'working';
        run.stopped = false;
        if (el.runCard) el.runCard.classList.remove('is-settled', 'is-failed');
        if (el.runFail) el.runFail.hidden = true;
        if (el.runSub) {
            el.runSub.textContent = 'This runs on the server — you can move around the app and come back.';
        }
        if (el.runPrompt) el.runPrompt.textContent = run.prompt;

        paintRun(run.stage, run.progress);
        tickClock();

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
    }

    function failRun(message) {
        run.stopped = true;
        stopTimers();
        store(RUN_KEY, null);

        if (el.runCard) el.runCard.classList.add('is-settled', 'is-failed');
        if (el.runTitle) el.runTitle.textContent = 'Generation stopped';
        if (el.runSub) el.runSub.textContent = 'Nothing was saved. Your prompt is below, unchanged.';
        if (el.runLive) el.runLive.textContent = 'Generation stopped. ' + message;
        if (el.runFailText) el.runFailText.textContent = message;
        if (el.runFail) el.runFail.hidden = false;

        paintSteps(run.stage, 'failed');
        toast('error', 'Generation stopped', message);
    }

    function completeRun(result) {
        stopTimers();
        store(RUN_KEY, null);
        store(DRAFT_KEY, null);

        const where = run.destLabel ? run.destLabel.toLowerCase() : 'your library';

        if (el.runCard) el.runCard.classList.add('is-settled');
        paintRun('completed', 100, 'done');
        if (el.runSub) el.runSub.textContent = 'Taking you there now.';
        if (el.runLive) el.runLive.textContent = 'Blog generated. Opening it now.';

        toast('success', 'Blog generated', 'Written and filed — ' + where + '.');

        const target = (result && result.redirect) || '/drafts';
        setTimeout(() => { window.location.href = target; }, 900);
    }

    // ------------------------------------------------------------------
    // Polling
    // ------------------------------------------------------------------

    function schedulePoll() {
        clearTimeout(run.pollTimer);
        run.pollTimer = setTimeout(poll, POLL_MS);
    }

    async function poll() {
        if (!run.taskId) return;

        // A run older than the server keeps its tasks for cannot be recovered,
        // and polling it forever is exactly what the old interval did.
        if (Date.now() - run.startedAt > RUN_TTL_MS) {
            failRun('This run took too long to report back. Check Drafts before starting again — it may have finished.');
            return;
        }

        let res;
        try {
            res = await fetch('/api/generate/status/' + encodeURIComponent(run.taskId), { signal });
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
            schedulePoll();
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
            schedulePoll();
            return;
        }

        run.netFails = 0;
        if (el.runSub && stage.dataset.state === 'working' && !run.stopped) {
            el.runSub.textContent = 'This runs on the server — you can move around the app and come back.';
        }

        if (data.status === 'completed') {
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
        schedulePoll();
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
        poll();
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
