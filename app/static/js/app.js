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

    // Page-specific skeleton templates matching actual page structures
    const skeletons = {
        // Dashboard Home: greeting + 3 stat cards + 3-column card grid with blog lists
        dashboard: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:200px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-grid" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr));">
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:100px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:120px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:90px;margin-bottom:1.25rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
            </div>`,

        // All Blogs: header + filter bar (status tabs + category + search) + 5-col table
        allBlogs: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:100px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:65px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:85px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:75px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:140px;height:36px;border-radius:8px;"></div>
                <div class="skeleton" style="width:200px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:34%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div></div>
            </div>`,

        // Drafts: header with "New Draft" button + 4-col table (title, category, date, actions)
        drafts: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
                <div class="skeleton" style="width:110px;height:38px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:45%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:42%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Categories: header with search + table (name, count badge, status, actions)
        categories: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
                <div class="skeleton" style="width:180px;height:36px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton" style="width:40px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Gallery: header + upload zone + image grid
        gallery: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:100px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:150px;"></div></div>
            </header>
            <div class="skeleton" style="width:100%;height:110px;border-radius:12px;margin-bottom:1.5rem;display:flex;align-items:center;justify-content:center;">
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem;">
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
            </div>`,

        // Newsletter: header + 3 stat cards + newsletter creation card + subscribers
        newsletter: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-card" style="margin-bottom:1.5rem;">
                <div class="skeleton skeleton-text" style="height:18px;width:180px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:40px;border-radius:8px;margin-bottom:1rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:80px;border-radius:8px;margin-bottom:1rem;"></div>
                <div class="skeleton" style="width:140px;height:38px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text" style="height:18px;width:130px;margin-bottom:1rem;"></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
            </div>`,

        // Leads: header + 3 stats + filter tabs + table (status, name, email, subject, date, actions)
        leads: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:70px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:100px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:60px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:200px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:26%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-circle" style="width:10px;height:10px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:21%;"></div><div class="skeleton skeleton-text" style="width:23%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Activity: header + 4 stats + filter bar (type tabs + user + search) + table
        activity: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:24px;width:45px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:50px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:55px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:60px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:70px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:65px;height:32px;border-radius:20px;"></div>
                <div style="flex:1"></div>
                <div class="skeleton" style="width:120px;height:36px;border-radius:8px;"></div>
                <div class="skeleton" style="width:180px;height:36px;border-radius:8px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:24%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:21%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
            </div>`,

        // Analytics: header + 4 stat cards + 2-col grid (top pages + traffic sources)
        analytics: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
                <div class="skeleton" style="width:110px;height:36px;border-radius:8px;"></div>
            </header>
            <div class="skeleton" style="width:100%;height:44px;border-radius:10px;margin-bottom:1.25rem;"></div>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(auto-fit,minmax(160px,1fr));">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:42px;height:42px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:26px;width:55px;"></div></div></div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;">
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:100px;margin-bottom:1rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:40px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:35px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:30px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="width:28px;"></div></div></div>
                <div class="skeleton-card"><div class="skeleton skeleton-text" style="height:18px;width:130px;margin-bottom:1rem;"></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:40px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text" style="width:35px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="width:30px;"></div></div><div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text" style="width:28px;"></div></div></div>
            </div>`,

        // Comments: header + 3 stats + filter tabs + table (commenter, text, post, status, actions)
        comments: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:190px;"></div></div>
            </header>
            <div class="skeleton-stat-grid">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:50px;"></div></div></div>
            </div>
            <div class="skeleton-filter-bar">
                <div class="skeleton" style="width:50px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:80px;height:32px;border-radius:20px;"></div>
                <div class="skeleton" style="width:75px;height:32px;border-radius:20px;"></div>
            </div>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:13%;"></div><div class="skeleton skeleton-text" style="width:34%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:20%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // Schedule: header + 2 stats + calendar nav + timeline
        schedule: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-stat-grid" style="grid-template-columns:repeat(2,1fr);">
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:40px;"></div></div></div>
                <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:40px;"></div></div></div>
            </div>
            <div style="display:flex;align-items:center;justify-content:center;gap:1rem;margin-bottom:1.5rem;">
                <div class="skeleton" style="width:36px;height:36px;border-radius:50%;"></div>
                <div class="skeleton skeleton-text" style="width:160px;height:22px;"></div>
                <div class="skeleton" style="width:36px;height:36px;border-radius:50%;"></div>
            </div>
            <div class="skeleton-card" style="margin-bottom:1rem;">
                <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;"><div class="skeleton" style="width:12px;height:12px;border-radius:50%;"></div><div class="skeleton skeleton-text" style="width:100px;height:16px;"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
            </div>
            <div class="skeleton-card">
                <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem;"><div class="skeleton" style="width:12px;height:12px;border-radius:50%;"></div><div class="skeleton skeleton-text" style="width:100px;height:16px;"></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div>
            </div>`,

        // Approval: header + table (title, category, submitted, actions)
        approval: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:120px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:190px;"></div></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:42%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:17%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`,

        // SEO Tools: header + card with dropdowns and button
        seoTools: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:80px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:130px;"></div></div>
            </header>
            <div class="skeleton-card" style="max-width:900px;">
                <div class="skeleton skeleton-text" style="height:18px;width:180px;margin-bottom:1.5rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:0.75rem;">
                    <div class="skeleton" style="width:150px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:170px;height:40px;border-radius:8px;"></div>
                </div>
            </div>`,

        // Formatting Tools: header + input card (dropdown + title + textarea + buttons)
        formattingTools: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:110px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:180px;"></div></div>
            </header>
            <div class="skeleton-card" style="max-width:900px;">
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div style="display:flex;gap:0.75rem;margin-bottom:1.25rem;">
                    <div class="skeleton" style="flex:1;height:42px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:90px;height:42px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:80px;height:42px;border-radius:8px;"></div>
                </div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:200px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:0.75rem;">
                    <div class="skeleton" style="width:140px;height:40px;border-radius:8px;"></div>
                    <div class="skeleton" style="width:80px;height:40px;border-radius:8px;"></div>
                </div>
            </div>`,

        // Site Settings: header + tab navigation + form card
        siteSettings: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:150px;"></div></div>
            </header>
            <div class="skeleton-filter-bar" style="margin-bottom:1.5rem;">
                <div class="skeleton" style="width:100px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:110px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:90px;height:34px;border-radius:20px;"></div>
                <div class="skeleton" style="width:100px;height:34px;border-radius:20px;"></div>
            </div>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:100px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
            </div>`,

        // App Settings: header + identity card with inputs and preview
        appSettings: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:90px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:140px;"></div></div>
            </header>
            <div class="skeleton-card">
                <div class="skeleton skeleton-text" style="height:18px;width:140px;margin-bottom:1.5rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.25rem;"></div>
                <div class="skeleton skeleton-text sm" style="margin-bottom:0.5rem;"></div>
                <div class="skeleton" style="width:100%;height:42px;border-radius:8px;margin-bottom:1.5rem;"></div>
                <div class="skeleton" style="width:100%;height:90px;border-radius:12px;margin-bottom:1.5rem;"></div>
                <div class="skeleton" style="width:130px;height:40px;border-radius:8px;"></div>
            </div>`,

        // Create Blog: centered layout with prompt box
        create: `
            <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:70vh;text-align:center;padding:2rem;">
                <div class="skeleton skeleton-text" style="width:200px;height:14px;margin-bottom:1rem;"></div>
                <div class="skeleton skeleton-text" style="width:340px;height:32px;margin-bottom:2.5rem;"></div>
                <div class="skeleton" style="width:100%;max-width:600px;height:120px;border-radius:16px;margin-bottom:1.5rem;"></div>
                <div style="display:flex;gap:1rem;align-items:center;">
                    <div class="skeleton" style="width:140px;height:36px;border-radius:20px;"></div>
                    <div class="skeleton skeleton-text" style="width:180px;height:14px;"></div>
                </div>
            </div>`,

        // Manage Users: header with invite button + table (user, email, role, status, actions)
        manageUsers: `
            <header class="dashboard-header skeleton-header">
                <div><div class="skeleton skeleton-text" style="height:14px;width:110px;margin-bottom:8px;"></div><div class="skeleton skeleton-title" style="width:180px;"></div></div>
                <div class="skeleton" style="width:120px;height:38px;border-radius:8px;"></div>
            </header>
            <div class="skeleton-table">
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:80%;"></div></div><div class="skeleton skeleton-text" style="width:25%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:70%;"></div></div><div class="skeleton skeleton-text" style="width:28%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:90%;"></div></div><div class="skeleton skeleton-text" style="width:22%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
                <div class="skeleton-table-row"><div style="display:flex;align-items:center;gap:10px;width:25%;"><div class="skeleton skeleton-circle" style="width:36px;height:36px;flex-shrink:0;"></div><div class="skeleton skeleton-text" style="width:75%;"></div></div><div class="skeleton skeleton-text" style="width:26%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton" style="width:60px;height:22px;border-radius:12px;"></div><div class="skeleton skeleton-text" style="width:8%;"></div></div>
            </div>`
    };

    // Map routes to their specific skeleton
    const routeSkeletonMap = {
        '/dashboard': 'dashboard',
        '/drafts': 'drafts',
        '/all-blogs': 'allBlogs',
        '/categories': 'categories',
        '/gallery': 'gallery',
        '/seo-tools': 'seoTools',
        '/newsletter': 'newsletter',
        '/formatting-tools': 'formattingTools',
        '/site-settings': 'siteSettings',
        '/approval': 'approval',
        '/comments': 'comments',
        '/schedule': 'schedule',
        '/leads': 'leads',
        '/activity': 'activity',
        '/analytics': 'analytics',
        '/create': 'create',
        '/app-settings': 'appSettings',
        '/users/manage-users': 'manageUsers'
    };

    function getSkeletonForUrl(url) {
        const pathname = new URL(url).pathname;
        for (const [route, type] of Object.entries(routeSkeletonMap)) {
            if (pathname === route || pathname.startsWith(route)) {
                return skeletons[type] || skeletons.allBlogs;
            }
        }
        return skeletons.allBlogs;
    }

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
        const inlineStyles = [];
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

        // Get page-specific inline <style> blocks
        doc.querySelectorAll('style').forEach(style => {
            const text = style.textContent.trim();
            if (text) {
                inlineStyles.push(text);
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

        return { styles, inlineStyles, scripts, bodyScripts };
    }

    function loadStyles(newStyles, newInlineStyles) {
        // Remove old page-specific external styles
        currentPageStyles.forEach(href => {
            const existing = document.querySelector(`link[href="${href}"]`);
            if (existing && !newStyles.includes(href)) {
                existing.remove();
            }
        });

        // Remove old pjax-injected inline styles
        document.querySelectorAll('style[data-pjax-inline]').forEach(el => el.remove());

        // Inject new inline styles immediately
        if (newInlineStyles && newInlineStyles.length > 0) {
            newInlineStyles.forEach(css => {
                const style = document.createElement('style');
                style.setAttribute('data-pjax-inline', 'true');
                style.textContent = css;
                document.head.appendChild(style);
            });
        }

        // Add new page-specific styles and wait for them to load
        const loadPromises = [];
        newStyles.forEach(href => {
            if (!document.querySelector(`link[href="${href}"]`)) {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = href;
                const promise = new Promise(resolve => {
                    link.onload = resolve;
                    link.onerror = resolve;
                });
                document.head.appendChild(link);
                loadPromises.push(promise);
            }
        });

        currentPageStyles = newStyles;
        return Promise.all(loadPromises);
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
        const timeoutId = setTimeout(() => currentAbortController.abort(), 15000);
        showProgress();

        const mainContent = document.querySelector('.dashboard-main');
        if (!mainContent) {
            window.location.href = url;
            return;
        }

        try {
            // Update active sidebar link immediately for responsiveness
            updateActiveLink(url);

            // Dim current content to signal loading
            mainContent.style.opacity = '0.5';
            mainContent.style.pointerEvents = 'none';
            mainContent.style.transition = 'opacity 0.15s ease';

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

            const newMain = doc.querySelector('.dashboard-main');
            if (!newMain) {
                throw new Error('No .dashboard-main found in response');
            }

            const newTitle = doc.querySelector('title');
            if (newTitle) {
                document.title = newTitle.textContent;
            }

            const assets = extractPageAssets(doc);

            // Cleanup old scripts
            cleanupOldScripts();

            // Load new styles BEFORE swapping content (prevents flash of unstyled content)
            await loadStyles(assets.styles, assets.inlineStyles);

            // Swap content with real page
            mainContent.innerHTML = newMain.innerHTML;
            mainContent.scrollTop = 0;
            window.scrollTo(0, 0);
            mainContent.style.opacity = '1';
            mainContent.style.pointerEvents = '';
            mainContent.classList.add('pjax-entering');

            // Update URL and history
            if (pushState) {
                history.pushState({ pjax: true, url: url }, '', url);
            }

            hideProgress();

            // Execute new page scripts
            executeScripts(assets.scripts, assets.bodyScripts);

            setTimeout(() => {
                mainContent.classList.remove('pjax-entering');
            }, 250);

        } catch (error) {
            if (error.name === 'AbortError') {
                mainContent.style.opacity = '1';
                mainContent.style.pointerEvents = '';
                return;
            }
            console.warn('Pjax navigation failed, falling back:', error.message);
            window.location.href = url;
        } finally {
            clearTimeout(timeoutId);
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

    return { init, navigate, getSkeletonForUrl, skeletons };
})();

// --------------------------------------------------------------------------
// Global Skeleton Utility (for page scripts to use during AJAX loading)
// --------------------------------------------------------------------------

window.Skeleton = {
    // Show skeleton inside a container while data is loading
    show(container, type = 'table') {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) return;
        container.setAttribute('data-skeleton-original', container.innerHTML);
        const templates = {
            table: `
                <div class="skeleton-table">
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:35%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:40%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:13%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:30%;"></div><div class="skeleton skeleton-text" style="width:18%;"></div><div class="skeleton skeleton-text" style="width:10%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:38%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div><div class="skeleton skeleton-text" style="width:12%;"></div><div class="skeleton skeleton-text" style="width:14%;"></div></div>
                    <div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:32%;"></div><div class="skeleton skeleton-text" style="width:16%;"></div><div class="skeleton skeleton-text" style="width:11%;"></div><div class="skeleton skeleton-text" style="width:15%;"></div></div>
                </div>`,
            list: `
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text sm"></div></div></div>
                <div class="skeleton-list-item"><div class="skeleton skeleton-circle" style="width:40px;height:40px;"></div><div style="flex:1"><div class="skeleton skeleton-text md"></div><div class="skeleton skeleton-text sm"></div></div></div>`,
            cards: `
                <div class="skeleton-grid">
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                    <div class="skeleton-card"><div class="skeleton skeleton-text md" style="height:18px;margin-bottom:1rem;"></div><div class="skeleton skeleton-text xl"></div><div class="skeleton skeleton-text lg"></div><div class="skeleton skeleton-text md"></div></div>
                </div>`,
            stats: `
                <div class="skeleton-stat-grid">
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                    <div class="skeleton-stat"><div class="skeleton skeleton-circle" style="width:48px;height:48px;"></div><div style="flex:1"><div class="skeleton skeleton-text sm"></div><div class="skeleton skeleton-text" style="height:28px;width:60px;"></div></div></div>
                </div>`,
            gallery: `
                <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:1rem;">
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                    <div class="skeleton" style="width:100%;height:160px;border-radius:12px;"></div>
                </div>`,
            rows: (count = 5) => {
                let html = '';
                for (let i = 0; i < count; i++) {
                    const w = 25 + Math.random() * 30;
                    html += `<div class="skeleton-table-row"><div class="skeleton skeleton-text" style="width:${w}%;"></div><div class="skeleton skeleton-text" style="width:${w * 0.4}%;"></div><div class="skeleton skeleton-text" style="width:${w * 0.3}%;"></div></div>`;
                }
                return `<div class="skeleton-table">${html}</div>`;
            }
        };

        const html = typeof templates[type] === 'function' ? templates[type]() : templates[type];
        if (html) container.innerHTML = html;
    },

    // Hide skeleton and restore original content (or replace with new content)
    hide(container, newContent) {
        if (typeof container === 'string') {
            container = document.querySelector(container);
        }
        if (!container) return;
        if (newContent !== undefined) {
            container.innerHTML = newContent;
        } else {
            const original = container.getAttribute('data-skeleton-original');
            if (original) container.innerHTML = original;
        }
        container.removeAttribute('data-skeleton-original');
    }
};

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
