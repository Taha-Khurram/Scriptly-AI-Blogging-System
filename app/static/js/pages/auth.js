/**
 * Auth screen enhancements — presentation only.
 *
 * Credential handling, validation rules and Firebase calls all stay in
 * firebase_google.js / forgot_password.js. This file adds the interaction
 * polish on top: password reveal, the live requirements checklist, and the
 * invitation banner.
 */

(function () {
    'use strict';

    // ----------------------------------------------------------------------
    // Password reveal
    // ----------------------------------------------------------------------

    document.addEventListener('click', function (e) {
        const btn = e.target.closest('[data-reveal]');
        if (!btn) return;

        const input = document.getElementById(btn.getAttribute('data-reveal'));
        if (!input) return;

        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';

        btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');

        const icon = btn.querySelector('.material-symbols-outlined');
        if (icon) icon.textContent = show ? 'visibility_off' : 'visibility';

        // Keep the caret where the user left it.
        const pos = input.value.length;
        input.focus();
        try { input.setSelectionRange(pos, pos); } catch (err) { }
    });

    // ----------------------------------------------------------------------
    // Password requirements checklist
    //
    // Mirrors validatePassword() in firebase_google.js. Showing the rules
    // turning green as they're met reads far better than a red error string
    // that fires on every keystroke.
    // ----------------------------------------------------------------------

    const RULES = {
        length: (v) => v.length >= 8,
        upper: (v) => /[A-Z]/.test(v),
        special: (v) => /[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/`~;']/.test(v),
        nospace: (v) => v.length > 0 && !/\s/.test(v)
    };

    function initPasswordRules() {
        const list = document.getElementById('passwordRules');
        const input = document.getElementById('password');
        if (!list || !input) return;

        const items = list.querySelectorAll('[data-rule]');

        function paint() {
            const value = input.value;
            list.classList.toggle('is-active', value.length > 0);

            items.forEach(function (item) {
                const rule = RULES[item.getAttribute('data-rule')];
                item.classList.toggle('met', !!rule && rule(value));
            });
        }

        input.addEventListener('input', paint);
        input.addEventListener('focus', paint);
        paint();
    }

    // ----------------------------------------------------------------------
    // Invitation flow — signup?invite=<email>
    // ----------------------------------------------------------------------

    function initInvite() {
        const notice = document.getElementById('inviteNotice');
        const emailField = document.getElementById('email');
        if (!notice || !emailField) return;

        const invited = new URLSearchParams(window.location.search).get('invite');
        if (!invited) return;

        emailField.value = decodeURIComponent(invited);
        emailField.readOnly = true;
        notice.hidden = false;

        // The invited address is fixed, so send focus to the field they can act on.
        const username = document.getElementById('username');
        if (username) username.focus();
    }

    function init() {
        initPasswordRules();
        initInvite();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
