document.addEventListener('DOMContentLoaded', () => {
    const configElement = document.getElementById('firebase-config');
    if (!configElement) return;

    const firebaseConfig = JSON.parse(configElement.textContent);
    if (!firebase.apps.length) firebase.initializeApp(firebaseConfig);
    const auth = firebase.auth();

    // Validation functions
    function validateEmail(email) {
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        return emailRegex.test(email);
    }

    // Only Gmail (and its googlemail.com alias) may sign up. Mirrors the
    // server-side rule in app/utils/validators.py — the server is the real
    // gate; this is just for immediate feedback.
    function isValidGmail(email) {
        if (!email || /\s/.test(email)) return false;
        const parts = email.trim().split('@');
        if (parts.length !== 2) return false;
        const [local, domain] = parts;
        if (!/^[A-Za-z0-9](?:[A-Za-z0-9.+_-]*[A-Za-z0-9])?$/.test(local)) return false;
        if (local.includes('..')) return false;
        return domain.toLowerCase() === 'gmail.com' || domain.toLowerCase() === 'googlemail.com';
    }

    // Firebase error codes are developer-facing ("FirebaseError: ... (auth/
    // invalid-credential)"). Surface something a person can act on instead,
    // through the app's toast system rather than a browser alert.
    const AUTH_MESSAGES = {
        'auth/invalid-credential': 'That email and password combination doesn\'t match an account.',
        'auth/wrong-password': 'That password is incorrect. Try again or reset it.',
        'auth/user-not-found': 'No account found with that email address.',
        'auth/invalid-email': 'That email address doesn\'t look right.',
        'auth/user-disabled': 'This account has been disabled. Contact your administrator.',
        'auth/email-already-in-use': 'An account with that email already exists. Try signing in.',
        'auth/weak-password': 'That password is too weak. Use at least 8 characters.',
        'auth/too-many-requests': 'Too many attempts. Please wait a moment and try again.',
        'auth/network-request-failed': 'Network problem — check your connection and try again.',
        'auth/popup-closed-by-user': 'The Google sign-in window was closed before finishing.',
        'auth/cancelled-popup-request': 'Another sign-in window is already open.',
        'auth/popup-blocked': 'Your browser blocked the sign-in popup. Allow popups and retry.'
    };

    function notifyAuthError(error, title) {
        const code = (error && error.code) || '';
        const message = AUTH_MESSAGES[code] || (error && error.message) || 'Something went wrong. Please try again.';

        // A closed popup is the user changing their mind, not a failure.
        if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') return;

        if (window.showToast) {
            window.showToast({ type: 'error', title: title || 'Sign-in failed', message: message, duration: 6000 });
        } else {
            alert(message);
        }
    }

    function validatePassword(password) {
        const errors = [];
        if (password.length < 8) errors.push('At least 8 characters');
        if (/\s/.test(password)) errors.push('No spaces allowed');
        if (!/[A-Z]/.test(password)) errors.push('At least one uppercase letter');
        if (!/[!@#$%^&*(),.?":{}|<>_\-+=\[\]\\\/`~;']/.test(password)) errors.push('At least one special character');
        return errors;
    }

    function showError(inputId, message) {
        const errorEl = document.getElementById(inputId + 'Error');
        const wrapperEl = document.getElementById(inputId)?.closest('.input-wrapper');
        if (errorEl) {
            errorEl.textContent = message;
            errorEl.classList.add('show');
        }
        if (wrapperEl) {
            wrapperEl.classList.add('error');
            wrapperEl.classList.remove('success');
        }
    }

    function clearError(inputId) {
        const errorEl = document.getElementById(inputId + 'Error');
        const wrapperEl = document.getElementById(inputId)?.closest('.input-wrapper');
        if (errorEl) {
            errorEl.textContent = '';
            errorEl.classList.remove('show');
        }
        if (wrapperEl) {
            wrapperEl.classList.remove('error');
            wrapperEl.classList.add('success');
        }
    }

    function clearAllErrors() {
        ['username', 'email', 'password'].forEach(id => {
            const errorEl = document.getElementById(id + 'Error');
            const wrapperEl = document.getElementById(id)?.closest('.input-wrapper');
            if (errorEl) {
                errorEl.textContent = '';
                errorEl.classList.remove('show');
            }
            if (wrapperEl) {
                wrapperEl.classList.remove('error', 'success');
            }
        });
    }

    function validateSignupForm(username, email, password) {
        let isValid = true;
        clearAllErrors();

        // Username validation
        if (!username || username.trim() === '') {
            showError('username', 'Full name is required');
            isValid = false;
        } else {
            clearError('username');
        }

        // Email validation
        if (!email || email.trim() === '') {
            showError('email', 'Email is required');
            isValid = false;
        } else if (!validateEmail(email)) {
            showError('email', 'Please enter a valid email address');
            isValid = false;
        } else if (!isValidGmail(email)) {
            showError('email', 'Only Gmail addresses (@gmail.com) are allowed');
            isValid = false;
        } else {
            clearError('email');
        }

        // Password validation
        if (!password || password === '') {
            showError('password', 'Password is required');
            isValid = false;
        } else {
            const passwordErrors = validatePassword(password);
            if (passwordErrors.length > 0) {
                showError('password', passwordErrors.join(', '));
                isValid = false;
            } else {
                clearError('password');
            }
        }

        return isValid;
    }

    async function sendTokenToBackend(user) {
        const idToken = await user.getIdToken();
        const response = await fetch('/api/auth/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ idToken: idToken })
        });
        const data = await response.json();
        if (data.success) {
            window.location.href = data.redirect;
            return true;
        }

        // Returning false (rather than throwing) lets the caller put its button
        // back — previously a rejected verify left the button spinning forever.
        notifyAuthError({ message: data.error || 'We could not complete sign-in.' }, 'Sign-in failed');
        return false;
    }

    // Helper functions for loading state
    function setButtonLoading(btn, isLoading) {
        if (isLoading) {
            btn.disabled = true;
            btn.classList.add('loading');
        } else {
            btn.disabled = false;
            btn.classList.remove('loading');
        }
    }

    // Handle Buttons (Google)
    document.addEventListener('click', async (e) => {
        const btn = e.target.closest('#googleSignIn, #googleSignUp');
        if (!btn) return;
        e.preventDefault();
        const provider = new firebase.auth.GoogleAuthProvider();
        setButtonLoading(btn, true);
        try {
            const result = await auth.signInWithPopup(provider);
            // On the signup page, block non-Gmail Google accounts up front so we
            // don't leave an orphaned Firebase user for the server to clean up.
            const isSignup = btn.id === 'googleSignUp';
            if (isSignup && !isValidGmail(result.user.email || '')) {
                await auth.signOut();
                setButtonLoading(btn, false);
                notifyAuthError(
                    { message: 'Only Gmail addresses (@gmail.com) can be used to sign up.' },
                    'Address not accepted'
                );
                return;
            }
            const ok = await sendTokenToBackend(result.user);
            if (!ok) setButtonLoading(btn, false);
        } catch (error) {
            setButtonLoading(btn, false);
            notifyAuthError(error, btn.id === 'googleSignUp' ? 'Sign-up failed' : 'Sign-in failed');
        }
    });

    // Handle Manual Forms (Login & Signup)
    const authForm = document.querySelector('form');
    if (authForm) {
        const submitBtn = authForm.querySelector('button[type="submit"]');

        authForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = authForm.email.value;
            const password = authForm.password.value;
            const isSignup = !!document.getElementById('googleSignUp');

            try {
                let userCred;
                if (isSignup) {
                    const username = authForm.username.value;

                    // Validate signup form
                    if (!validateSignupForm(username, email, password)) {
                        return;
                    }

                    setButtonLoading(submitBtn, true);
                    userCred = await auth.createUserWithEmailAndPassword(email, password);
                    await userCred.user.updateProfile({ displayName: username });
                } else {
                    setButtonLoading(submitBtn, true);
                    userCred = await auth.signInWithEmailAndPassword(email, password);
                }
                const ok = await sendTokenToBackend(userCred.user);
                if (!ok) setButtonLoading(submitBtn, false);
            } catch (error) {
                setButtonLoading(submitBtn, false);
                notifyAuthError(error, isSignup ? 'Sign-up failed' : 'Sign-in failed');
            }
        });

        // Real-time validation on blur for signup
        const isSignup = !!document.getElementById('googleSignUp');
        if (isSignup) {
            const usernameInput = document.getElementById('username');
            const emailInput = document.getElementById('email');
            const passwordInput = document.getElementById('password');

            if (usernameInput) {
                usernameInput.addEventListener('blur', () => {
                    if (!usernameInput.value.trim()) {
                        showError('username', 'Full name is required');
                    } else {
                        clearError('username');
                    }
                });
            }

            if (emailInput) {
                emailInput.addEventListener('blur', () => {
                    if (!emailInput.value.trim()) {
                        showError('email', 'Email is required');
                    } else if (!validateEmail(emailInput.value)) {
                        showError('email', 'Please enter a valid email address');
                    } else if (!isValidGmail(emailInput.value)) {
                        showError('email', 'Only Gmail addresses (@gmail.com) are allowed');
                    } else {
                        clearError('email');
                    }
                });
            }

            if (passwordInput) {
                passwordInput.addEventListener('blur', () => {
                    if (!passwordInput.value) {
                        showError('password', 'Password is required');
                    } else {
                        const errors = validatePassword(passwordInput.value);
                        if (errors.length > 0) {
                            showError('password', errors.join(', '));
                        } else {
                            clearError('password');
                        }
                    }
                });

                // Live feedback while typing. Where the requirements checklist
                // is on screen (auth.js paints it) it carries the feedback, so
                // we only clear stale errors here instead of flashing red on
                // every keystroke.
                const hasChecklist = !!document.getElementById('passwordRules');
                passwordInput.addEventListener('input', () => {
                    const errors = validatePassword(passwordInput.value);

                    if (hasChecklist) {
                        if (errors.length === 0 && passwordInput.value) {
                            clearError('password');
                        } else {
                            const errorEl = document.getElementById('passwordError');
                            const wrapperEl = passwordInput.closest('.input-wrapper');
                            if (errorEl) {
                                errorEl.textContent = '';
                                errorEl.classList.remove('show');
                            }
                            if (wrapperEl) wrapperEl.classList.remove('error', 'success');
                        }
                        return;
                    }

                    if (passwordInput.value && errors.length > 0) {
                        showError('password', errors.join(', '));
                    } else if (passwordInput.value) {
                        clearError('password');
                    }
                });
            }
        }
    }
});

document.addEventListener('click', async (e) => {
    if (e.target.closest('.logout')) return;

    const btn = e.target.closest('#googleSignIn, #googleSignUp');
    if (!btn) return;

    e.preventDefault();
});