/**
 * Public Comment Section - comments.js
 * Handles comment form submission and comment list loading
 */

document.addEventListener('DOMContentLoaded', function () {
    loadComments();

    const form = document.getElementById('comment-form');
    if (form) {
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            submitComment();
        });
    }
});

async function loadComments() {
    const listEl = document.getElementById('comments-list');
    const loadingEl = document.getElementById('comments-loading');
    const countEl = document.getElementById('comments-count');

    try {
        const res = await fetch(COMMENT_LIST_URL);
        const data = await res.json();

        if (loadingEl) loadingEl.remove();

        if (data.success && data.comments && data.comments.length > 0) {
            if (countEl) countEl.textContent = `(${data.comments.length})`;
            listEl.innerHTML = data.comments.map(renderComment).join('');
        } else {
            if (countEl) countEl.textContent = '(0)';
            listEl.innerHTML = renderEmptyState();
        }
    } catch (err) {
        console.error('Error loading comments:', err);
        if (loadingEl) loadingEl.remove();
        listEl.innerHTML = renderEmptyState();
    }
}

async function submitComment() {
    const btn = document.getElementById('comment-submit-btn');
    const nameInput = document.getElementById('comment-name');
    const emailInput = document.getElementById('comment-email');
    const textInput = document.getElementById('comment-text');
    const msgEl = document.getElementById('comment-form-message');

    const name = nameInput.value.trim();
    const email = emailInput.value.trim();
    const comment = textInput.value.trim();

    if (!name || !email || !comment) {
        showFormMessage(msgEl, 'Please fill in all required fields.', 'error');
        return;
    }

    // Show thank-you modal immediately
    showThankYouModal();

    // Clear form right away
    nameInput.value = '';
    emailInput.value = '';
    textInput.value = '';

    // Disable button while request is in flight
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Posting...';

    try {
        const res = await fetch(COMMENT_POST_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, email, comment })
        });

        const data = await res.json();

        if (data.success) {
            // If published, prepend to list
            if (data.status === 'published' && data.comment) {
                const listEl = document.getElementById('comments-list');
                const emptyState = listEl.querySelector('.comments-empty');
                if (emptyState) emptyState.remove();

                const newHtml = renderComment(data.comment);
                listEl.insertAdjacentHTML('afterbegin', newHtml);

                // Update count
                const countEl = document.getElementById('comments-count');
                const currentCount = listEl.querySelectorAll('.comment-card').length;
                if (countEl) countEl.textContent = `(${currentCount})`;

                // Highlight new comment
                const newCard = listEl.querySelector('.comment-card');
                if (newCard) {
                    newCard.classList.add('comment-new');
                    setTimeout(() => newCard.classList.remove('comment-new'), 3000);
                }
            }
            // If moderated (removed by AI), user already saw the thank-you modal — nothing else to show
        } else {
            // Only show error if something actually failed
            showFormMessage(msgEl, data.error || 'Failed to post comment.', 'error');
        }
    } catch (err) {
        console.error('Comment submit error:', err);
        showFormMessage(msgEl, 'Something went wrong. Please try again.', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">send</i> Post Comment';
    }
}

// ==================== THANK YOU MODAL ====================

function showThankYouModal() {
    const modal = document.getElementById('comment-thankyou-modal');
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeThankYouModal() {
    const modal = document.getElementById('comment-thankyou-modal');
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

// ==================== RENDERERS ====================

function renderComment(comment) {
    const date = comment.created_at ? formatRelativeDate(comment.created_at) : '';
    const initial = (comment.commenter_name || 'A').charAt(0).toUpperCase();
    const name = escapeHtml(comment.commenter_name || 'Anonymous');
    const text = escapeHtml(comment.display_text || '');

    return `
    <div class="comment-card">
        <div class="comment-card-header">
            <div class="comment-avatar">${initial}</div>
            <div class="comment-meta">
                <span class="comment-author">${name}</span>
                <span class="comment-date">${date}</span>
            </div>
        </div>
        <div class="comment-card-body">
            <p>${text}</p>
        </div>
    </div>`;
}

function renderEmptyState() {
    return `
    <div class="comments-empty">
        <i class="material-symbols-outlined icon-inline" aria-hidden="true">chat</i>
        <p>No comments yet. Be the first to share your thoughts!</p>
    </div>`;
}

function showFormMessage(el, message, type) {
    if (!el) return;
    el.className = `comment-form-message comment-msg-${type}`;
    el.textContent = message;
    el.classList.remove('d-none');
    setTimeout(() => el.classList.add('d-none'), 5000);
}

function formatRelativeDate(dateStr) {
    try {
        const date = new Date(dateStr);
        const now = new Date();
        const diff = Math.floor((now - date) / 1000);

        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;

        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch {
        return '';
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
