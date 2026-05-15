/**
 * Scriptly AI - Main Application Logic
 * Handles: Toast notifications, page transitions (pjax), session management
 */

// --------------------------------------------------------------------------
// DOM Elements
// --------------------------------------------------------------------------

const pageLoader = document.getElementById("page-loader");
const navProgress = document.getElementById("nav-progress");
const toastContainer = document.getElementById("toast-container");

// --------------------------------------------------------------------------
// Toast Notification System
// --------------------------------------------------------------------------

const showToast = (options) => {
    const { type = 'success', title, message, duration = 4000 } = options;

    const icons = {
        success: 'bi-check-circle-fill',
        error: 'bi-x-circle-fill',
        warning: 'bi-exclamation-triangle-fill',
        info: 'bi-info-circle-fill'
    };

    const toast = document.createElement('div');
    toast.className = 'custom-toast';
    toast.innerHTML = `
        <div class="toast-icon ${type}">
            <i class="bi ${icons[type]}"></i>
        </div>
        <div class="toast-content">
            <div class="toast-title">${title}</div>
            <div class="toast-message">${message}</div>
        </div>
        <button class="toast-close" onclick="this.closest('.custom-toast').remove()">
            <i class="bi bi-x"></i>
        </button>
        <div class="toast-progress" style="animation-duration: ${duration}ms;"></div>
    `;

    if (toastContainer) {
        toastContainer.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add('show');
        });

        setTimeout(() => {
            toast.classList.add('hiding');
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 400);
        }, duration);
    }

    return toast;
};

window.showToast = showToast;

// --------------------------------------------------------------------------
// Page Loader Functions
// --------------------------------------------------------------------------

const showLoader = () => {
    if (pageLoader) pageLoader.classList.remove("hidden");
    if (navProgress) navProgress.classList.add("loading");
};

const hideLoader = () => {
    if (pageLoader) pageLoader.classList.add("hidden");
    if (navProgress) {
        navProgress.classList.remove("loading");
        navProgress.style.width = "100%";
        setTimeout(() => { navProgress.style.width = "0%"; }, 300);
    }
};

window.showLoader = showLoader;
window.hideLoader = hideLoader;

// --------------------------------------------------------------------------
// Pjax Navigation System (SPA-like transitions)
// --------------------------------------------------------------------------

// Patch DOMContentLoaded to support dynamically loaded scripts (pjax)
// When scripts are loaded after initial page load, their DOMContentLoaded
// listeners would never fire. This makes them fire immediately instead.
const _origAddEventListener = Document.prototype.addEventListener;
Document.prototype.addEventListener = function(type, fn, options) {
    if (type === 'DOMContentLoaded' && document.readyState !== 'loading') {
        setTimeout(fn, 0);
    } else {
        _origAddEventListener.call(this, type, fn, options);
    }
};

const Pjax = (() => {
    let currentAbortController = null;
    let isNavigating = false;
    let currentPageStyles = [];

    function isDashboardLink(link) {
        if (!link || !link.href) return false;
        if (link.target === '_blank') return false;
        if (link.hasAttribute('data-bs-toggle')) return false;
        if (link.hasAttribute('download')) return false;
        if (link.classList.contains('no-pjax')) return false;
        if (link.classList.contains('user-card-logout')) return false;
        if (link.href.includes('#')) return false;
        if (link.href.startsWith('javascript:')) return false;
        if (link.href.startsWith('mailto:')) return false;

        const url = new URL(link.href);
        if (url.host !== window.location.host) return false;

        // Only intercept dashboard navigation (sidebar links)
        const dashboardPaths = [
            '/dashboard', '/create', '/drafts', '/all-blogs',
            '/categories', '/gallery', '/seo-tools', '/newsletter',
            '/formatting-tools', '/site-settings', '/approval',
            '/comments', '/schedule', '/leads', '/activity',
            '/analytics', '/app-settings', '/users/manage-users'
        ];
        return dashboardPaths.some(p => url.pathname === p || url.pathname.startsWith(p));
    }

    function showProgress() {
        if (navProgress) {
            navProgress.style.width = "0%";
            navProgress.classList.add("loading");
        }
    }

    function hideProgress() {
        if (navProgress) {
            navProgress.classList.remove("loading");
            navProgress.style.width = "100%";
            setTimeout(() => { navProgress.style.width = "0%"; }, 300);
        }
    }

    function updateActiveLink(url) {
        const currentPath = new URL(url).pathname;
        document.querySelectorAll('.sidebar-menu a, .nav-link').forEach(link => {
            const href = link.getAttribute('href');
            if (href === currentPath) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }

    function extractPageAssets(doc) {
        const styles = [];
        const scripts = [];

        // Base assets that should NOT be reloaded on navigation
        const baseScripts = ['bootstrap.bundle', 'app.js'];
        const baseStyles = ['bootstrap', 'dashboard.css', 'fonts.googleapis'];

        // Get page-specific CSS (anything not from the base template)
        doc.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
            const href = link.getAttribute('href');
            if (href && !baseStyles.some(b => href.includes(b))) {
                styles.push(href);
            }
        });

        // Get page-specific external JS (anything not from the base template)
        doc.querySelectorAll('script[src]').forEach(script => {
            const src = script.getAttribute('src');
            if (src && !baseScripts.some(b => src.includes(b))) {
                scripts.push(src);
            }
        });

        // Get inline scripts (page-specific initialization)
        const bodyScripts = [];
        doc.querySelectorAll('script:not([src])').forEach(script => {
            const text = script.textContent.trim();
            // Skip the session timeout and pjax scripts (they're in app.js)
            if (text && !text.includes('resetTimers') && !text.includes('_origAddEventListener')) {
                bodyScripts.push(text);
            }
        });

        return { styles, scripts, bodyScripts };
    }

    function loadStyles(newStyles) {
        // Remove old page-specific styles
        currentPageStyles.forEach(href => {
            const existing = document.querySelector(`link[href="${href}"]`);
            if (existing && !newStyles.includes(href)) {
                existing.remove();
            }
        });

        // Add new page-specific styles
        newStyles.forEach(href => {
            if (!document.querySelector(`link[href="${href}"]`)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = href;
                document.head.appendChild(link);
            }
        });

        currentPageStyles = newStyles;
    }

    function executeScripts(scripts, inlineScripts) {
        // Load external scripts sequentially
        const loadScript = (src) => {
            return new Promise((resolve) => {
                // Remove old version if exists
                const old = document.querySelector(`script[data-pjax][src="${src}"]`);
                if (old) old.remove();

                const script = document.createElement('script');
                script.src = src;
                script.setAttribute('data-pjax', 'true');
                script.onload = resolve;
                script.onerror = resolve;
                document.body.appendChild(script);
            });
        };

        // Chain script loading
        let chain = Promise.resolve();
        scripts.forEach(src => {
            chain = chain.then(() => loadScript(src));
        });

        // Execute inline scripts after external ones load
        chain.then(() => {
            inlineScripts.forEach(code => {
                try {
                    const script = document.createElement('script');
                    script.setAttribute('data-pjax', 'true');
                    script.textContent = code;
                    document.body.appendChild(script);
                } catch (e) {
                    console.warn('Pjax: inline script error', e);
                }
            });

            // Dispatch DOMContentLoaded-like event for scripts expecting it
            document.dispatchEvent(new Event('pjax:complete'));
            window.dispatchEvent(new Event('load'));
        });
    }

    function cleanupOldScripts() {
        document.querySelectorAll('script[data-pjax]').forEach(s => s.remove());
    }

    async function navigate(url, pushState = true) {
        if (isNavigating) {
            if (currentAbortController) {
                currentAbortController.abort();
            }
        }

        isNavigating = true;
        currentAbortController = new AbortController();
        showProgress();

        const mainContent = document.querySelector('.dashboard-main');
        if (!mainContent) {
            // Not on a dashboard page, do full navigation
            window.location.href = url;
            return;
        }

        try {
            // Fade out current content
            mainContent.classList.add('pjax-leaving');

            const response = await fetch(url, {
                signal: currentAbortController.signal,
                headers: { 'X-Pjax': 'true' }
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const html = await response.text();
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, 'text/html');

            // Extract new main content
            const newMain = doc.querySelector('.dashboard-main');
            if (!newMain) {
                throw new Error('No .dashboard-main found in response');
            }

            // Extract page title
            const newTitle = doc.querySelector('title');
            if (newTitle) {
                document.title = newTitle.textContent;
            }

            // Extract and manage assets
            const assets = extractPageAssets(doc);

            // Wait for fade out to complete
            await new Promise(resolve => setTimeout(resolve, 150));

            // Cleanup old scripts
            cleanupOldScripts();

            // Swap content
            mainContent.innerHTML = newMain.innerHTML;
            mainContent.classList.remove('pjax-leaving');
            mainContent.classList.add('pjax-entering');

            // Load new styles
            loadStyles(assets.styles);

            // Update URL and history
            if (pushState) {
                history.pushState({ pjax: true, url: url }, '', url);
            }

            // Update active sidebar link
            updateActiveLink(url);

            // Hide progress
            hideProgress();

            // Execute new page scripts
            executeScripts(assets.scripts, assets.bodyScripts);

            // Remove entering class after animation
            setTimeout(() => {
                mainContent.classList.remove('pjax-entering');
            }, 250);

            // Scroll to top of content
            mainContent.scrollTop = 0;
            window.scrollTo(0, 0);

        } catch (error) {
            if (error.name === 'AbortError') {
                return; // Cancelled by newer navigation
            }
            console.warn('Pjax navigation failed, falling back:', error.message);
            // Fallback to full page navigation
            window.location.href = url;
        } finally {
            isNavigating = false;
            currentAbortController = null;
        }
    }

    function init() {
        // Track initial page-specific styles
        const baseStyles = ['bootstrap', 'dashboard.css'];
        document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
            const href = link.getAttribute('href');
            if (href && !baseStyles.some(b => href.includes(b))) {
                currentPageStyles.push(href);
            }
        });

        // Intercept sidebar link clicks
        document.addEventListener('click', (e) => {
            const link = e.target.closest('a');
            if (!link) return;
            if (e.ctrlKey || e.metaKey || e.shiftKey) return; // Allow open in new tab

            if (isDashboardLink(link)) {
                e.preventDefault();
                navigate(link.href);
            }
        });

        // Handle browser back/forward
        window.addEventListener('popstate', (e) => {
            if (e.state && e.state.pjax) {
                navigate(e.state.url, false);
            } else if (document.querySelector('.dashboard-main')) {
                navigate(window.location.href, false);
            }
        });

        // Store initial state
        history.replaceState({ pjax: true, url: window.location.href }, '');
    }

    return { init, navigate };
})();

// --------------------------------------------------------------------------
// Page Load & Non-Dashboard Navigation Handling
// --------------------------------------------------------------------------

// Hide loader on page load
window.addEventListener("load", () => setTimeout(hideLoader, 200));

// Safety fallback: Force hide after 2.5 seconds if load event fails
setTimeout(hideLoader, 2500);

// Show loader only for non-dashboard (non-pjax) navigation
document.addEventListener("click", (e) => {
    const link = e.target.closest("a");
    if (link && link.href &&
        !link.href.includes("#") &&
        !link.href.startsWith("javascript:") &&
        !link.href.startsWith("mailto:") &&
        link.target !== "_blank" &&
        !e.ctrlKey && !e.metaKey &&
        !link.hasAttribute('data-bs-toggle') &&
        !link.hasAttribute('download') &&
        !link.classList.contains('logout') &&
        !link.classList.contains('no-loader')) {

        const currentHost = window.location.host;
        const linkHost = new URL(link.href).host;

        // Only show full-page loader for non-dashboard external/internal links
        // Dashboard links are handled by Pjax
        if (currentHost === linkHost) {
            const dashboardPaths = [
                '/dashboard', '/create', '/drafts', '/all-blogs',
                '/categories', '/gallery', '/seo-tools', '/newsletter',
                '/formatting-tools', '/site-settings', '/approval',
                '/comments', '/schedule', '/leads', '/activity',
                '/analytics', '/app-settings', '/users/manage-users'
            ];
            const url = new URL(link.href);
            const isPjaxLink = dashboardPaths.some(p => url.pathname === p || url.pathname.startsWith(p));
            if (!isPjaxLink) {
                showLoader();
            }
        }
    }
});

// Show loader on form submit (except AJAX forms)
document.addEventListener("submit", (e) => {
    if (!e.target.classList.contains('no-loader') &&
        !e.target.classList.contains('ajax-form')) {
        showLoader();
    }
});

// Hide loader on back/forward navigation
window.addEventListener("pageshow", (event) => {
    if (event.persisted) hideLoader();
});

// --------------------------------------------------------------------------
// Sidebar Active Link Handler (initial page load)
// --------------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.sidebar-menu a, .nav-link').forEach(link => {
        const href = link.getAttribute('href');
        if (href === currentPath) {
            link.classList.add('active');
        } else {
            link.classList.remove('active');
        }
    });

    // Initialize Pjax navigation
    Pjax.init();
});

// --------------------------------------------------------------------------
// Session Inactivity Timeout
// --------------------------------------------------------------------------

(function() {
    const TIMEOUT_MS = 15 * 60 * 1000;
    const WARN_MS = 2 * 60 * 1000;
    let timeoutTimer, warningTimer;

    function resetTimers() {
        clearTimeout(timeoutTimer);
        clearTimeout(warningTimer);

        warningTimer = setTimeout(() => {
            if (window.showToast) {
                showToast({
                    type: 'warning',
                    title: 'Session Expiring',
                    message: 'Your session will expire in 2 minutes due to inactivity.',
                    duration: 10000
                });
            }
        }, TIMEOUT_MS - WARN_MS);

        timeoutTimer = setTimeout(() => {
            window.location.href = '/login?expired=1';
        }, TIMEOUT_MS);
    }

    ['click', 'keydown', 'scroll', 'mousemove'].forEach(evt =>
        document.addEventListener(evt, resetTimers, { passive: true })
    );
    resetTimers();

    // Intercept fetch to handle 401 session_expired responses
    const _fetch = window.fetch;
    window.fetch = async function(...args) {
        const res = await _fetch.apply(this, args);
        if (res.status === 401) {
            try {
                const data = await res.clone().json();
                if (data.error === 'session_expired') {
                    window.location.href = data.redirect || '/login?expired=1';
                }
            } catch(e) {}
        }
        return res;
    };
})();
