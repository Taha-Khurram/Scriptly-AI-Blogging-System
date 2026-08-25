/**
 * Site Settings — eleven sections, 113 controls, one save.
 *
 * The version this replaces lived as 590 lines of <script> inside the template
 * (beside 1028 of <style>), declared nineteen globals so twenty-three inline
 * `onclick` attributes could reach them, bound its listeners at module scope so
 * PJAX stacked a fresh copy on every visit, and interpolated sheet rows and
 * gallery URLs into markup with no escaping at all.
 *
 * It also silently dropped an entire section. `buildPayload` below sends a
 * `legal` object; the old handler did not, so `data.get('legal', {})` on the
 * server always saw nothing and nine fields — the privacy policy, the terms and
 * the whole cookie banner — could be typed, saved, cheerfully confirmed, and
 * never stored.
 */

(function siteSettingsPage() {
    'use strict';

    if (window.__siteSettingsAbort) {
        try { window.__siteSettingsAbort.abort(); } catch (e) { /* already gone */ }
    }
    const controller = new AbortController();
    const signal = controller.signal;
    window.__siteSettingsAbort = controller;

    const root = document.querySelector('.dashboard-main');
    const form = root && root.querySelector('#settingsForm');
    if (!root || !form) return;

    const $ = (sel, scope) => (scope || root).querySelector(sel);
    const $$ = (sel, scope) => Array.from((scope || root).querySelectorAll(sel));

    // Section key -> the label the rail shows, so the save bar can name sections
    // in the reader's words rather than by slug.
    const SECTIONS = {
        general: 'General', appearance: 'Appearance', content: 'Content',
        chrome: 'Header & footer', heroes: 'Hero sections', legal: 'Legal',
        seo: 'SEO', social: 'Social', permalinks: 'Permalinks',
        locale: 'Locale & time', sheets: 'Google Sheets'
    };

    const state = {
        section: 'general',
        baseline: new Map(),   // control id -> value as last saved
        dirty: new Set(),      // control ids differing from baseline
        imageKey: null,        // which image field the picker is filling
        galleryLoaded: false,
        gallery: [],
        sheetsLoaded: false,
        pendingUnpublish: null,
        pendingHref: null,     // where the reader was trying to go
        saving: false
    };

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    // All five characters Jinja's autoescape covers. The sheet rows and gallery
    // filenames below land in attributes, and the two-line
    // textContent/innerHTML trick leaves both quote characters alone.
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

    function show(id) { const m = bsModal(id); if (m) m.show(); }
    function hide(id) { const m = bsModal(id); if (m) m.hide(); }

    function controls() {
        return $$('[data-setting]', form);
    }

    function valueOf(el) {
        if (el.type === 'checkbox') return el.checked ? '1' : '0';
        if (el.type === 'radio') {
            const picked = form.querySelector('input[name="' + el.name + '"]:checked');
            return picked ? picked.value : '';
        }
        return el.value;
    }

    function keyOf(el) {
        return el.type === 'radio' ? 'radio:' + el.name : el.id;
    }

    function sectionOf(el) {
        const panel = el.closest('[data-panel]');
        return panel ? panel.dataset.panel : null;
    }

    function num(value, fallback) {
        const n = parseInt(value, 10);
        return Number.isFinite(n) ? n : fallback;
    }

    function v(id) { const el = document.getElementById(id); return el ? el.value : ''; }
    function c(id) { const el = document.getElementById(id); return el ? el.checked : false; }

    // ------------------------------------------------------------------
    // Dirty tracking
    // ------------------------------------------------------------------

    function snapshot() {
        state.baseline.clear();
        controls().forEach((el) => state.baseline.set(keyOf(el), valueOf(el)));
        state.dirty.clear();
        paintDirty();
    }

    function recompute() {
        state.dirty.clear();
        controls().forEach((el) => {
            const key = keyOf(el);
            if (state.baseline.get(key) !== valueOf(el)) state.dirty.add(key);
        });
        paintDirty();
    }

    function dirtySections() {
        const out = new Set();
        controls().forEach((el) => {
            if (!state.dirty.has(keyOf(el))) return;
            const sec = sectionOf(el);
            if (sec) out.add(sec);
        });
        return out;
    }

    function paintDirty() {
        // Per-field marker
        controls().forEach((el) => {
            const on = state.dirty.has(keyOf(el));
            const field = el.closest('[data-field]');
            const sw = el.closest('[data-switch]');
            if (field) field.classList.toggle('is-dirty', on);
            if (sw) sw.classList.toggle('is-dirty', on);
        });

        // Rail dots — so "3 changes" resolves to somewhere you can look
        const secs = dirtySections();
        Object.keys(SECTIONS).forEach((key) => {
            const dot = $('[data-section-dot="' + key + '"]');
            if (dot) dot.hidden = !secs.has(key);
        });

        // The bar
        const bar = $('[data-savebar]');
        const text = $('[data-savebar-text]');
        const count = state.dirty.size;
        if (bar) bar.classList.toggle('is-idle', count === 0);
        if (text && count) {
            const names = Array.from(secs).map((k) => SECTIONS[k] || k);
            text.innerHTML = '<strong>' + count + (count === 1 ? ' unsaved change' : ' unsaved changes') +
                '</strong>' + (names.length ? ' in ' + esc(listify(names)) : '');
        }
    }

    function listify(items) {
        if (items.length <= 1) return items[0] || '';
        if (items.length === 2) return items[0] + ' and ' + items[1];
        return items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
    }

    function isDirty() { return state.dirty.size > 0; }

    function revert() {
        controls().forEach((el) => {
            const key = keyOf(el);
            if (!state.baseline.has(key)) return;
            const was = state.baseline.get(key);
            if (el.type === 'checkbox') {
                el.checked = was === '1';
            } else if (el.type === 'radio') {
                el.checked = el.value === was;
            } else if (el.value !== was) {
                el.value = was;
            }
        });
        // Anything mirroring a field has to be re-derived from it.
        syncSelectCaptions();
        syncColorSwatches();
        syncImagePreviews();
        syncSiteStrip();
        syncCounters();
        recompute();
    }

    // ------------------------------------------------------------------
    // Mirrors — things that display a field's value elsewhere
    // ------------------------------------------------------------------

    // The SelectPill module writes through to the <select> and fires `change`,
    // but deliberately leaves the trigger's caption alone: what a trigger should
    // read once a value is applied differs per screen. This is that one line.
    function syncSelectCaptions() {
        $$('[data-select-pill]', form).forEach((pill) => {
            const select = pill.querySelector('select');
            const caption = pill.querySelector('[data-pill-text]');
            if (!select || !caption) return;
            const opt = select.options[select.selectedIndex];
            caption.textContent = opt ? opt.textContent.trim() : '';
        });
    }

    function syncColorSwatches() {
        $$('[data-color-text]', form).forEach((text) => {
            const picker = $('[data-color-picker="' + text.id + '"]', form);
            if (picker && /^#([A-Fa-f0-9]{6}|[A-Fa-f0-9]{3})$/.test(text.value)) {
                picker.value = text.value;
            }
        });
    }

    function syncImagePreviews() {
        $$('[data-image-field]', form).forEach((field) => {
            const hidden = field.querySelector('input[type="hidden"]');
            const frame = field.querySelector('[data-image-frame]');
            const img = field.querySelector('[data-image-preview]');
            const empty = field.querySelector('[data-image-empty]');
            const actions = field.querySelector('[data-image-actions]');
            const url = hidden ? hidden.value : '';
            if (frame) frame.hidden = !url;
            if (empty) empty.hidden = !!url;
            if (actions) actions.hidden = !url;
            if (img) img.src = url || '';
        });
    }

    function syncSiteStrip() {
        const name = $('[data-site-name]');
        const desc = $('[data-site-desc]');
        const slug = $('[data-site-slug]');
        const link = $('[data-site-link]');
        const mark = $('[data-site-mark]');

        if (name) name.textContent = v('site_name') || 'Untitled site';
        if (desc) desc.textContent = v('site_description') || 'No description yet';

        const shown = v('site_slug').trim() || (slug ? slug.dataset.fallback : '');
        if (slug && shown) slug.textContent = shown;
        if (link && shown) link.href = window.location.origin + '/site/' + shown;

        const logo = v('logo_url');
        if (mark) {
            const current = mark.querySelector('img');
            if (logo && !current) {
                mark.innerHTML = '<img src="' + esc(logo) + '" alt="">';
            } else if (logo && current) {
                current.src = logo;
            } else if (!logo && current) {
                mark.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">public</i>';
            }
        }
    }

    function syncCounters() {
        $$('[data-counter]', form).forEach((el) => {
            const max = num(el.dataset.counter, 0);
            const out = $('[data-counter-for="' + el.id + '"]', form);
            if (!out || !max) return;
            const len = el.value.length;
            out.textContent = len + '/' + max;
            out.classList.toggle('is-over', len >= max);
            out.classList.toggle('is-near', len < max && len >= max * 0.8);
        });
    }

    // ------------------------------------------------------------------
    // Sections
    // ------------------------------------------------------------------

    function openSection(key, viaHash) {
        if (!SECTIONS[key]) key = 'general';
        state.section = key;

        $$('[data-section]').forEach((tab) => {
            const on = tab.dataset.section === key;
            tab.classList.toggle('is-active', on);
            tab.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        $$('[data-panel]').forEach((panel) => {
            panel.hidden = panel.dataset.panel !== key;
        });

        // The section is in the URL, so a reload — or a link someone shares —
        // comes back to the panel it was on rather than to General.
        if (!viaHash && window.location.hash !== '#' + key) {
            try {
                history.replaceState(null, '', window.location.pathname + '#' + key);
            } catch (e) { /* history unavailable, not worth failing over */ }
        }

        if (key === 'sheets' && !state.sheetsLoaded) loadSheetActivity();
    }

    // ------------------------------------------------------------------
    // Save
    // ------------------------------------------------------------------

    function slugify(text) {
        return String(text || '').toLowerCase()
            .replace(/[^\w\s-]/g, '')
            .replace(/[\s_]+/g, '-')
            .replace(/-+/g, '-')
            .replace(/^-|-$/g, '')
            .substring(0, 50);
    }

    function sheetId(raw) {
        const value = String(raw || '').trim();
        const match = value.match(/\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/);
        return match ? match[1] : value;
    }

    function heroFields(prefix, keys) {
        const out = {};
        keys.forEach((key) => { out[key] = v(prefix + '_' + key); });
        return out;
    }

    function buildPayload() {
        const structure = form.querySelector('input[name="permalink_structure"]:checked');

        return {
            site_name: v('site_name'),
            site_slug: v('site_slug').toLowerCase().replace(/[^a-z0-9-]/g, ''),
            site_description: v('site_description'),
            niche: v('niche'),
            site_visibility: v('site_visibility'),

            logo_url: v('logo_url'),
            favicon_url: v('favicon_url'),
            cover_image_url: v('cover_image_url'),
            primary_color: v('primary_color'),
            secondary_color: v('secondary_color'),

            posts_per_page: num(v('posts_per_page'), 10),
            featured_post_id: v('featured_post_id'),
            show_reading_time: c('show_reading_time'),
            show_author: c('show_author'),

            meta_title: v('meta_title'),
            meta_description: v('meta_description'),
            og_image_url: v('og_image_url'),
            analytics_id: v('analytics_id'),
            custom_domain: v('custom_domain'),

            social_twitter: v('social_twitter'),
            social_linkedin: v('social_linkedin'),
            social_github: v('social_github'),
            contact_email: v('contact_email'),
            about_content: v('about_content'),

            timezone: v('timezone'),
            locale: v('locale'),
            date_format: v('date_format'),
            time_format: v('time_format'),

            header: {
                nav_home: v('header_nav_home'),
                nav_blog: v('header_nav_blog'),
                nav_about: v('header_nav_about'),
                nav_contact: v('header_nav_contact'),
                cta_text: v('header_cta_text'),
                show_search: c('header_show_search')
            },
            footer: {
                copyright: v('footer_copyright'),
                col1_title: v('footer_col1_title'),
                col2_title: v('footer_col2_title'),
                col3_title: v('footer_col3_title'),
                show_newsletter: c('footer_show_newsletter'),
                newsletter_title: v('footer_newsletter_title'),
                newsletter_description: v('footer_newsletter_description')
            },

            hero_home: heroFields('hero_home', [
                'badge', 'cta_secondary', 'stats_label_1', 'stats_label_2', 'stats_label_3',
                'latest_title', 'latest_subtitle', 'view_all_text', 'about_kicker', 'about_title',
                'newsletter_disclaimer', 'newsletter_image'
            ]),
            hero_about: heroFields('hero_about', [
                'subtitle', 'story_title', 'values_title',
                'value_1_title', 'value_1_desc', 'value_2_title', 'value_2_desc',
                'value_3_title', 'value_3_desc',
                'stats_title', 'cta_title', 'cta_subtitle', 'badge_text', 'values_subtitle',
                'stat_1_label', 'stat_2_label', 'stat_3_label', 'stat_4_label', 'stat_4_value',
                'cta_btn_primary', 'cta_btn_secondary'
            ]),
            hero_blog: heroFields('hero_blog', ['title', 'subtitle']),
            hero_contact: heroFields('hero_contact', [
                'title', 'subtitle', 'form_title', 'form_subtitle',
                'faq_1_q', 'faq_1_a', 'faq_2_q', 'faq_2_a',
                'faq_3_q', 'faq_3_a', 'faq_4_q', 'faq_4_a'
            ]),

            permalinks: {
                structure: structure ? structure.value : 'post-name',
                category_base: v('category_base'),
                tag_base: v('tag_base')
            },
            seo: {
                indexing_enabled: c('seo_indexing_enabled'),
                robots_txt_custom: v('seo_robots_txt'),
                og_site_name: v('seo_og_site_name'),
                og_default_image: v('og_image_url'),
                twitter_card: v('seo_twitter_card'),
                twitter_site: v('seo_twitter_site')
            },
            rss: {
                enabled: c('rss_enabled'),
                posts_count: num(v('rss_posts_count'), 20),
                content_type: v('rss_content_type'),
                include_featured_image: c('rss_include_image')
            },

            // The section the old payload left out entirely. Note the key inside
            // is `contact_email`, not `legal_contact_email` — that is the shape
            // site_routes.py reads when it serves the policy pages.
            legal: {
                contact_email: v('legal_contact_email'),
                privacy_policy_enabled: c('privacy_policy_enabled'),
                privacy_policy_content: v('privacy_policy_content'),
                terms_of_service_enabled: c('terms_of_service_enabled'),
                terms_of_service_content: v('terms_of_service_content'),
                cookie_consent_enabled: c('cookie_consent_enabled'),
                cookie_consent_text: v('cookie_consent_text'),
                cookie_consent_button: v('cookie_consent_button'),
                cookie_consent_link_text: v('cookie_consent_link_text')
            },

            google_sheets_id: sheetId(v('google_sheets_id')),
            activity_tracking_enabled: c('activity_tracking_enabled')
        };
    }

    function save(then) {
        if (state.saving) return;

        // The server requires it and answers 400. Saying so here costs nothing
        // and points at the field instead of at a toast.
        const name = document.getElementById('site_name');
        if (name && !name.value.trim()) {
            openSection('general');
            name.focus();
            toast('error', 'Site name is required', 'Your site needs a name before anything can be saved.');
            return;
        }
        const slug = v('site_slug').trim();
        if (slug && slug.length < 3) {
            openSection('general');
            const el = document.getElementById('site_slug');
            if (el) el.focus();
            toast('error', 'Slug too short', 'A site slug needs at least 3 characters, or leave it blank.');
            return;
        }

        state.saving = true;
        const btn = $('[data-save]');
        const original = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Saving…';
        }

        fetch('/api/site-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(buildPayload()),
            signal
        })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'The server would not accept that.');
                snapshot();
                syncSiteStrip();
                toast('success', 'Saved', 'Your site settings are live.');
                if (typeof then === 'function') then();
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                toast('error', 'Not saved', err.message || 'Could not reach the server.');
            })
            .finally(() => {
                state.saving = false;
                if (btn) {
                    btn.disabled = false;
                    btn.innerHTML = original;
                }
            });
    }

    // ------------------------------------------------------------------
    // Image picker
    // ------------------------------------------------------------------

    function openPicker(key) {
        state.imageKey = key;
        show('imagePickerModal');
        if (state.galleryLoaded) { renderGallery(); return; }

        const body = $('[data-picker-body]');
        if (body) {
            body.innerHTML = '<div class="list-empty">' +
                '<span class="list-empty-icon"><span class="spinner-border spinner-border-sm"></span></span>' +
                '<p>Loading your gallery…</p></div>';
        }

        fetch('/api/gallery/images?per_page=50', { signal })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'Could not read your gallery.');
                state.gallery = data.images || [];
                state.galleryLoaded = true;
                renderGallery();
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                if (!body) return;
                body.innerHTML = '<div class="list-empty">' +
                    '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">warning</i></span>' +
                    '<p>' + esc(err.message || 'Could not reach the gallery.') + '</p>' +
                    '<button type="button" class="app-btn is-ghost" data-picker-retry>Try again</button></div>';
            });
    }

    function renderGallery() {
        const body = $('[data-picker-body]');
        if (!body) return;

        if (!state.gallery.length) {
            body.innerHTML = '<div class="list-empty">' +
                '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">photo_library</i></span>' +
                '<p>Your gallery is empty. Upload something on the Gallery page and it will show up here.</p></div>';
            return;
        }

        body.innerHTML = '<div class="set-picker-grid">' + state.gallery.map((img) => {
            const url = esc(img.url);
            const alt = esc(img.filename || '');
            return '<button type="button" class="set-picker-item" data-picker-pick="' + url + '" ' +
                'title="' + alt + '"><img src="' + url + '" alt="' + alt + '" loading="lazy"></button>';
        }).join('') + '</div>';
    }

    function pickImage(url) {
        const key = state.imageKey;
        const field = key && $('[data-image-field="' + key + '"]', form);
        const hidden = field && field.querySelector('input[type="hidden"]');
        if (hidden) {
            hidden.value = url;
            syncImagePreviews();
            syncSiteStrip();
            recompute();
        }
        hide('imagePickerModal');
    }

    function clearImage(key) {
        const field = $('[data-image-field="' + key + '"]', form);
        const hidden = field && field.querySelector('input[type="hidden"]');
        if (!hidden) return;
        hidden.value = '';
        syncImagePreviews();
        syncSiteStrip();
        recompute();
    }

    // ------------------------------------------------------------------
    // Locale preview
    // ------------------------------------------------------------------

    let previewTimer = null;

    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(loadPreview, 250);
    }

    // Every render carries a sequence number and a stale answer is dropped: three
    // rapid changes must not let the first response overwrite the third.
    let previewSeq = 0;

    function loadPreview() {
        const box = $('[data-time-preview]');
        if (!box) return;
        const seq = ++previewSeq;
        box.classList.add('is-loading');

        fetch('/api/time-preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                timezone: v('timezone'),
                date_format: v('date_format'),
                time_format: v('time_format')
            }),
            signal
        })
            .then((res) => res.json())
            .then((data) => {
                if (seq !== previewSeq) return;
                box.classList.remove('is-loading');
                if (!data.success) return;
                const d = $('[data-preview-date]', box);
                const t = $('[data-preview-time]', box);
                const f = $('[data-preview-full]', box);
                if (d) d.textContent = data.date;
                if (t) t.textContent = data.time;
                if (f) f.textContent = data.full;
            })
            .catch((err) => {
                if (seq === previewSeq) box.classList.remove('is-loading');
                if (err.name !== 'AbortError') { /* the server-rendered values stand */ }
            });
    }

    // ------------------------------------------------------------------
    // Google Sheets activity
    // ------------------------------------------------------------------

    function loadSheetActivity() {
        const box = $('[data-sheets-activity]');
        if (!box) return;
        state.sheetsLoaded = true;

        box.innerHTML = '<div class="list-empty">' +
            '<span class="list-empty-icon"><span class="spinner-border spinner-border-sm"></span></span>' +
            '<p>Reading the last rows from your sheet…</p></div>';

        fetch('/api/sheets-recent-activity', { signal })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success || !data.activities || !data.activities.length) {
                    box.innerHTML = '<div class="list-empty">' +
                        '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">inbox</i></span>' +
                        '<p>' + esc(data.error || 'No rows yet. Use the dashboard and entries will appear here.') +
                        '</p></div>';
                    return;
                }

                // Every cell escaped. These strings come out of a spreadsheet
                // anyone with edit access can write to, and the old renderer
                // interpolated user, action and page straight into the markup.
                const rows = data.activities.map((a) => {
                    const stamp = String(a.timestamp || '');
                    const time = stamp.split(' ')[1] || stamp.substring(11, 19) || stamp;
                    return '<tr>' +
                        '<td class="set-td-time">' + esc(time) + '</td>' +
                        '<td class="set-td-clip">' + esc(a.user || '') + '</td>' +
                        '<td><span class="set-kind">' + esc(a.action_type || 'event') + '</span></td>' +
                        '<td class="set-td-clip">' + esc(a.action || '') + '</td>' +
                        '<td class="set-td-clip">' + esc(a.page || '') + '</td>' +
                        '</tr>';
                }).join('');

                box.innerHTML = '<div class="set-table-scroll"><table class="set-table">' +
                    '<thead><tr><th>Time</th><th>User</th><th>Kind</th><th>Action</th><th>Page</th></tr></thead>' +
                    '<tbody>' + rows + '</tbody></table></div>';
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                box.innerHTML = '<div class="list-empty">' +
                    '<span class="list-empty-icon"><i class="material-symbols-outlined icon-inline" aria-hidden="true">warning</i></span>' +
                    '<p>Could not read the sheet. Check the ID above, and that the service account has Editor ' +
                    'access.</p><button type="button" class="app-btn is-ghost" data-sheets-refresh>Try again</button>' +
                    '</div>';
            });
    }

    // ------------------------------------------------------------------
    // Unpublish
    // ------------------------------------------------------------------

    function unpublish(id) {
        const btn = $('[data-unpublish-confirm]');
        const original = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Unpublishing…';
        }

        fetch('/api/unpublish/' + encodeURIComponent(id), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            signal
        })
            .then((res) => res.json())
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'Could not unpublish that post.');
                hide('unpublishModal');

                const row = $('[data-published="' + CSS.escape(id) + '"]');
                if (row) row.remove();

                // Read the figures off their own elements. The old code found them
                // with `[style*="rgba(67, 24, 255"] h2` — an inline style string
                // that only still existed to keep that selector matching.
                const pub = $('[data-stat-published]');
                const pend = $('[data-stat-pending]');
                if (pub) pub.textContent = Math.max(0, num(pub.textContent, 0) - 1);
                if (pend) pend.textContent = num(pend.textContent, 0) + 1;

                const rows = $('[data-published-rows]');
                const empty = $('[data-published-empty]');
                if (empty && rows) empty.hidden = rows.children.length > 0;

                toast('success', 'Unpublished', 'It is back in the approval queue.');
            })
            .catch((err) => {
                if (err.name === 'AbortError') return;
                toast('error', 'Still published', err.message || 'Could not reach the server.');
            })
            .finally(() => {
                if (btn) { btn.disabled = false; btn.innerHTML = original; }
                state.pendingUnpublish = null;
            });
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        save();
    }, { signal });

    root.addEventListener('click', (e) => {
        const section = e.target.closest('[data-section]');
        if (section) { openSection(section.dataset.section); return; }

        const imageOpen = e.target.closest('[data-image-open]');
        if (imageOpen) { openPicker(imageOpen.dataset.imageOpen); return; }

        const imageClear = e.target.closest('[data-image-clear]');
        if (imageClear) { clearImage(imageClear.dataset.imageClear); return; }

        const pick = e.target.closest('[data-picker-pick]');
        if (pick) { pickImage(pick.dataset.pickerPick); return; }

        if (e.target.closest('[data-picker-retry]')) {
            state.galleryLoaded = false;
            openPicker(state.imageKey);
            return;
        }

        if (e.target.closest('[data-slug-generate]')) {
            const slug = document.getElementById('site_slug');
            const name = v('site_name');
            if (slug && name.trim()) {
                slug.value = slugify(name);
                syncSiteStrip();
                recompute();
            }
            return;
        }

        if (e.target.closest('[data-copy-url]')) {
            const link = $('[data-site-link]');
            copy(link ? link.href : '', 'Site URL copied.');
            return;
        }

        if (e.target.closest('[data-copy-service]')) {
            const el = $('[data-service-account]');
            copy(el ? el.textContent.trim() : '', 'Service account address copied.');
            return;
        }

        if (e.target.closest('[data-sheets-refresh]')) { loadSheetActivity(); return; }

        const unpub = e.target.closest('[data-unpublish]');
        if (unpub) {
            state.pendingUnpublish = unpub.dataset.unpublish;
            const title = $('[data-unpublish-title]');
            if (title) title.textContent = unpub.dataset.title || 'This post';
            show('unpublishModal');
            return;
        }

        if (e.target.closest('[data-unpublish-confirm]')) {
            if (state.pendingUnpublish) unpublish(state.pendingUnpublish);
            return;
        }

        if (e.target.closest('[data-discard]')) { revert(); return; }

        if (e.target.closest('[data-discard-leave]')) {
            hide('discardModal');
            const href = state.pendingHref;
            state.dirty.clear();
            paintDirty();
            if (href) window.location.href = href;
            return;
        }

        if (e.target.closest('[data-discard-save]')) {
            const href = state.pendingHref;
            save(() => {
                hide('discardModal');
                if (href) window.location.href = href;
            });
            return;
        }
    }, { signal });

    // One handler for every value change. `input` catches typing, `change`
    // catches checkboxes, radios, the colour swatch and the select pills'
    // write-through.
    ['input', 'change'].forEach((type) => {
        form.addEventListener(type, (e) => {
            const el = e.target;

            if (el.matches && el.matches('[data-color-picker]')) {
                const text = document.getElementById(el.dataset.colorPicker);
                if (text) { text.value = el.value.toUpperCase(); }
                recompute();
                return;
            }

            if (el.id === 'site_slug') {
                // Constrained here as well as on the server, so what you see in
                // the URL strip is what will actually be stored.
                //
                // Spaces become hyphens rather than vanishing. The old filter was
                // a bare /[^a-z0-9-]/g strip, so typing "My Blog" gave "myblog" —
                // a character silently eaten mid-word, and a different answer from
                // the one the generate button beside it produces.
                //
                // A trailing hyphen is left alone while the field has focus: this
                // fires on every keystroke, and trimming it here would delete the
                // separator the moment you typed it. The server trims on save.
                const cleaned = el.value.toLowerCase()
                    .replace(/[\s_]+/g, '-')
                    .replace(/[^a-z0-9-]/g, '')
                    .replace(/-{2,}/g, '-');
                if (cleaned !== el.value) {
                    const at = el.selectionStart;
                    const shrank = el.value.length - cleaned.length;
                    el.value = cleaned;
                    // Keep the caret where the typing was, rather than throwing it
                    // to the end of the field on every corrected character.
                    if (typeof at === 'number' && el.setSelectionRange) {
                        const to = Math.max(0, at - shrank);
                        try { el.setSelectionRange(to, to); } catch (err) { /* not a text input */ }
                    }
                }
            }

            if (el.matches && el.matches('[data-color-text]')) syncColorSwatches();
            if (el.matches && el.matches('[data-counter]')) syncCounters();
            if (el.closest && el.closest('[data-select-pill]')) syncSelectCaptions();

            if (['site_name', 'site_description', 'site_slug', 'logo_url'].indexOf(el.id) !== -1) {
                syncSiteStrip();
            }
            if (['timezone', 'date_format', 'time_format'].indexOf(el.id) !== -1) {
                // The old screen wired this to date_format and time_format only —
                // so changing the timezone, the field the preview exists for,
                // left every figure in it stale.
                schedulePreview();
            }

            recompute();
        }, { signal });
    });

    // A slug is offered from the site name, but only into an empty field — it is
    // part of a public URL and overwriting a chosen one would be rude.
    const siteName = document.getElementById('site_name');
    if (siteName) {
        siteName.addEventListener('blur', () => {
            const slug = document.getElementById('site_slug');
            if (slug && !slug.value.trim()) {
                slug.value = slugify(siteName.value);
                syncSiteStrip();
                recompute();
            }
        }, { signal });
    }

    function copy(text, message) {
        if (!text) return;
        const done = () => toast('success', 'Copied', message);
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(done).catch(() => {
                toast('error', 'Could not copy', 'Your browser blocked clipboard access.');
            });
        } else {
            toast('error', 'Could not copy', 'Your browser does not allow clipboard access here.');
        }
    }

    // --- The unsaved-changes guard ------------------------------------
    // Capture phase, deliberately: app.js binds its PJAX interceptor on
    // `document` in the bubble phase and is registered first, so a bubble-phase
    // guard here would run after the navigation had already been kicked off.
    document.addEventListener('click', (e) => {
        if (!isDirty() || state.saving) return;
        if (e.defaultPrevented) return;
        if (e.ctrlKey || e.metaKey || e.shiftKey) return;

        const link = e.target.closest('a[href]');
        if (!link) return;
        if (link.target === '_blank' || link.hasAttribute('download')) return;
        if (link.closest('.modal')) return;

        let url;
        try { url = new URL(link.href, window.location.href); } catch (err) { return; }
        if (url.origin !== window.location.origin) return;
        if (url.pathname === window.location.pathname) return;   // in-page anchor

        e.preventDefault();
        e.stopPropagation();
        state.pendingHref = link.href;

        const copyEl = $('[data-discard-copy]');
        if (copyEl) {
            const names = Array.from(dirtySections()).map((k) => SECTIONS[k] || k);
            copyEl.textContent = state.dirty.size + (state.dirty.size === 1 ? ' change' : ' changes') +
                (names.length ? ' in ' + listify(names) : '') +
                ' have not been saved. Leaving now discards them.';
        }
        show('discardModal');
    }, { capture: true, signal });

    // The browser's own guard, for a hard reload or a closed tab — the one case
    // no in-page dialog can cover.
    window.addEventListener('beforeunload', (e) => {
        if (!isDirty() || state.saving) return;
        e.preventDefault();
        e.returnValue = '';
        return '';
    }, { signal });

    // ------------------------------------------------------------------
    // Init
    // ------------------------------------------------------------------

    // The fallback the URL strip shows when the slug field is empty, captured
    // before anything can edit it.
    const slugEl = $('[data-site-slug]');
    if (slugEl) slugEl.dataset.fallback = slugEl.textContent.trim();

    syncSelectCaptions();
    syncColorSwatches();
    syncImagePreviews();
    syncCounters();
    snapshot();

    // Counters used to be initialised from a DOMContentLoaded listener, which
    // never fires again after the first load — so on every PJAX arrival at this
    // page they read 0/70 regardless of what was in the field.
    openSection((window.location.hash || '').replace('#', '') || 'general', true);

    window.addEventListener('hashchange', () => {
        openSection((window.location.hash || '').replace('#', '') || 'general', true);
    }, { signal });

    // The header's search field jumps to whichever section mentions the query,
    // which is the only way to find one field among 113 without opening all
    // eleven panels.
    document.addEventListener('page-search', (e) => {
        const q = ((e.detail && e.detail.value) || '').trim().toLowerCase();
        $$('[data-field], [data-switch]', form).forEach((f) => f.classList.remove('is-hit'));
        if (!q) return;

        for (const key of Object.keys(SECTIONS)) {
            const panel = $('[data-panel="' + key + '"]');
            if (!panel) continue;
            const hit = $$('[data-field], [data-switch]', panel).find((f) =>
                f.textContent.toLowerCase().indexOf(q) !== -1);
            if (hit) {
                openSection(key);
                hit.classList.add('is-hit');
                hit.scrollIntoView({ block: 'center', behavior: 'smooth' });
                const control = hit.querySelector('[data-setting]');
                if (control && control.type !== 'hidden') control.focus({ preventScroll: true });
                return;
            }
        }
    }, { signal });

})();
