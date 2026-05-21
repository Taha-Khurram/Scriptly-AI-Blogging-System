/**
 * Activity Log Dashboard - activity.js
 * Handles activity listing, filtering, pagination, and date range modal
 */

let currentFilter = 'all';
let currentUser = 'all';
let currentSearch = '';
let currentDateFrom = '';
let currentDateTo = '';
let currentPage = 1;
const perPage = 10;
let searchTimeout = null;

document.addEventListener('DOMContentLoaded', initActivity);
document.addEventListener('pjax:complete', initActivity);

function initActivity() {
    setupFilterTabs();
    setupControls();
    const listEl = document.getElementById('activityList');
    if (listEl && listEl.querySelector('.activity-row')) {
        // Data pre-rendered server-side, user filter already populated in HTML
    } else {
        loadUserFilter();
        loadActivities();
    }
}

// ==================== FILTER TABS ====================

function setupFilterTabs() {
    document.querySelectorAll('.activity-filter-tabs .filter-tab').forEach(tab => {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.activity-filter-tabs .filter-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentFilter = this.dataset.filter;
            currentPage = 1;
            loadActivities();
        });
    });
}

// ==================== CONTROLS ====================

function setupControls() {
    const userSelect = document.getElementById('userFilter');
    userSelect.addEventListener('change', function () {
        currentUser = this.value;
        currentPage = 1;
        loadActivities();
    });

    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentSearch = this.value.trim();
            currentPage = 1;
            loadActivities();
        }, 300);
    });
}

// ==================== LOAD USER FILTER ====================

async function loadUserFilter() {
    try {
        const res = await fetch('/api/activity/users');
        const data = await res.json();
        if (data.success && data.users) {
            const select = document.getElementById('userFilter');
            data.users.forEach(u => {
                const opt = document.createElement('option');
                opt.value = u.uid;
                opt.textContent = u.name;
                select.appendChild(opt);
            });
        }
    } catch (err) {
        console.error('Error loading users:', err);
    }
}

// ==================== LOAD ACTIVITIES ====================

async function loadActivities() {
    const listEl = document.getElementById('activityList');
    listEl.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border spinner-border-sm text-primary opacity-50"></div>
            <p class="text-secondary fw-medium mt-2 mb-0">Loading activity...</p>
        </div>`;

    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage
        });
        if (currentFilter !== 'all') params.set('type', currentFilter);
        if (currentUser !== 'all') params.set('user', currentUser);
        if (currentSearch) params.set('search', currentSearch);
        if (currentDateFrom) params.set('date_from', currentDateFrom);
        if (currentDateTo) params.set('date_to', currentDateTo);

        const res = await fetch('/api/activity?' + params.toString());
        const data = await res.json();

        if (data.success && data.activities && data.activities.length > 0) {
            listEl.innerHTML = data.activities.map(renderActivityRow).join('');
            renderPagination(data.total, data.page, data.per_page);
        } else {
            listEl.innerHTML = `
                <div class="activity-empty">
                    <i class="bi bi-clock-history"></i>
                    <p>No activity found.</p>
                </div>`;
            document.getElementById('activityPagination').innerHTML = '';
        }
    } catch (err) {
        console.error('Error loading activities:', err);
        listEl.innerHTML = `
            <div class="text-center py-4 text-danger">
                <i class="bi bi-exclamation-circle"></i> Failed to load activities.
            </div>`;
    }
}

// ==================== RENDER ROW ====================

function renderActivityRow(activity) {
    const userName = escapeHtml(activity.user_name || 'Unknown');
    const initial = userName.charAt(0).toUpperCase();
    const actionText = escapeHtml(activity.action_text || '');
    const targetName = escapeHtml(activity.target_name || activity.blog_title || '');
    const typeInfo = getTypeInfo(activity);
    const timeStr = formatRelativeTime(activity.timestamp);

    return `
    <div class="activity-row">
        <div class="col-activity-user">
            <div class="activity-user-cell">
                <div class="activity-avatar">${initial}</div>
                <span class="activity-user-name">${userName}</span>
            </div>
        </div>
        <div class="col-activity-action">
            <span class="activity-action-text">${actionText}</span>
        </div>
        <div class="col-activity-target">
            <span class="activity-target-text" title="${targetName}">${truncate(targetName, 25)}</span>
        </div>
        <div class="col-activity-type">
            <span class="activity-type-badge ${typeInfo.badgeClass}">
                <i class="bi ${typeInfo.icon}"></i> ${typeInfo.label}
            </span>
        </div>
        <div class="col-activity-time">
            <span class="activity-time-text">${timeStr}</span>
        </div>
    </div>`;
}

// ==================== TYPE INFO ====================

function getTypeInfo(activity) {
    const targetType = activity.target_type || '';
    const type = activity.type || '';
    const blogTypes = ['generated', 'edited', 'published', 'deleted', 'status_change', 'seo_optimized'];

    if (targetType === 'blog' || blogTypes.includes(type)) {
        return { icon: 'bi-file-post', label: 'Blog', badgeClass: 'badge-type-blog' };
    } else if (targetType === 'user' || type === 'user') {
        return { icon: 'bi-person', label: 'User', badgeClass: 'badge-type-user' };
    } else if (targetType === 'comment' || type === 'comment') {
        return { icon: 'bi-chat-dots', label: 'Comment', badgeClass: 'badge-type-comment' };
    } else if (targetType === 'settings' || type === 'settings') {
        return { icon: 'bi-gear', label: 'Settings', badgeClass: 'badge-type-settings' };
    } else if (targetType === 'newsletter' || type === 'newsletter') {
        return { icon: 'bi-envelope', label: 'Newsletter', badgeClass: 'badge-type-newsletter' };
    } else if (targetType === 'category' || type === 'category') {
        return { icon: 'bi-tags', label: 'Category', badgeClass: 'badge-type-category' };
    }
    return { icon: 'bi-activity', label: 'Other', badgeClass: 'badge-type-settings' };
}

// ==================== PAGINATION ====================

function renderPagination(total, page, perPage) {
    const container = document.getElementById('activityPagination');
    const totalPages = Math.ceil(total / perPage);
    if (totalPages <= 1) { container.innerHTML = ''; return; }

    let html = '';
    html += `<button class="page-btn ${page <= 1 ? 'disabled' : ''}" onclick="goToPage(${page - 1})" ${page <= 1 ? 'disabled' : ''}>
        <i class="bi bi-chevron-left"></i>
    </button>`;

    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
            html += `<button class="page-btn ${i === page ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
        } else if (i === page - 2 || i === page + 2) {
            html += `<span class="page-dots">...</span>`;
        }
    }

    html += `<button class="page-btn ${page >= totalPages ? 'disabled' : ''}" onclick="goToPage(${page + 1})" ${page >= totalPages ? 'disabled' : ''}>
        <i class="bi bi-chevron-right"></i>
    </button>`;

    container.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    loadActivities();
    document.querySelector('.activity-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ==================== DATE RANGE MODAL ====================

function openDateModal() {
    document.getElementById('dateFrom').value = currentDateFrom;
    document.getElementById('dateTo').value = currentDateTo;
    const modal = new bootstrap.Modal(document.getElementById('dateRangeModal'));
    modal.show();
}

function setDatePreset(preset) {
    const today = new Date();
    const toStr = today.toISOString().split('T')[0];
    let fromStr = '';

    switch (preset) {
        case 'today':
            fromStr = toStr;
            break;
        case 'week':
            const week = new Date(today);
            week.setDate(week.getDate() - 7);
            fromStr = week.toISOString().split('T')[0];
            break;
        case 'month':
            const month = new Date(today);
            month.setDate(month.getDate() - 30);
            fromStr = month.toISOString().split('T')[0];
            break;
        case 'all':
            fromStr = '';
            document.getElementById('dateFrom').value = '';
            document.getElementById('dateTo').value = '';
            return;
    }

    document.getElementById('dateFrom').value = fromStr;
    document.getElementById('dateTo').value = toStr;
}

function applyDateFilter() {
    currentDateFrom = document.getElementById('dateFrom').value;
    currentDateTo = document.getElementById('dateTo').value;
    currentPage = 1;

    const btn = document.getElementById('dateFilterBtn');
    const label = document.getElementById('dateFilterLabel');

    if (currentDateFrom || currentDateTo) {
        btn.classList.add('active');
        const from = currentDateFrom ? formatShortDate(currentDateFrom) : '...';
        const to = currentDateTo ? formatShortDate(currentDateTo) : '...';
        label.textContent = `${from} - ${to}`;
    } else {
        btn.classList.remove('active');
        label.textContent = 'Date Range';
    }

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
    loadActivities();
}

function clearDateFilter() {
    document.getElementById('dateFrom').value = '';
    document.getElementById('dateTo').value = '';
    currentDateFrom = '';
    currentDateTo = '';
    currentPage = 1;

    document.getElementById('dateFilterBtn').classList.remove('active');
    document.getElementById('dateFilterLabel').textContent = 'Date Range';

    bootstrap.Modal.getInstance(document.getElementById('dateRangeModal')).hide();
    loadActivities();
}

// ==================== HELPERS ====================

function formatRelativeTime(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffSec = Math.floor(diffMs / 1000);
        const diffMin = Math.floor(diffSec / 60);
        const diffHr = Math.floor(diffMin / 60);
        const diffDays = Math.floor(diffHr / 24);

        if (diffSec < 60) return 'Just now';
        if (diffMin < 60) return `${diffMin}m ago`;
        if (diffHr < 24) return `${diffHr}h ago`;
        if (diffDays < 7) return `${diffDays}d ago`;
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return '';
    }
}

function formatShortDate(dateStr) {
    try {
        const d = new Date(dateStr + 'T00:00:00');
        return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    } catch {
        return dateStr;
    }
}

function truncate(str, max) {
    if (!str) return '';
    return str.length > max ? str.substring(0, max) + '...' : str;
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
