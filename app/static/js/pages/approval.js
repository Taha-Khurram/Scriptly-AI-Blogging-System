/**
 * Approval Queue Page JavaScript
 */

let currentBlogId = null;
let currentBlogTitle = '';

// Check if there are remaining approvals and show empty state if needed
function checkEmptyState() {
  const container = document.querySelector('.approvals-container');
  const remainingRows = container.querySelectorAll('.draft-row');

  if (remainingRows.length === 0) {
    // Remove the header too
    const header = container.querySelector('.drafts-header');
    if (header) header.remove();

    // Remove pagination if exists
    const pagination = container.querySelector('.p-4.border-top');
    if (pagination) pagination.remove();

    // Add empty state
    container.innerHTML = `
      <div class="text-center py-5">
        <div class="mb-3 text-muted opacity-50"><i class="bi bi-check-all fs-1"></i></div>
        <p class="text-secondary fw-bold">All caught up! No pending approvals.</p>
      </div>
    `;
  }
}

// Setup schedule modal confirm button
(function() {
  var scheduleBtn = document.getElementById('confirmScheduleBtn');
  if (scheduleBtn) scheduleBtn.addEventListener('click', async () => {
    const dateTime = document.getElementById('scheduleDateTime').value;
    if (!dateTime) {
      showToast({ type: 'error', title: 'Error', message: 'Please select a date and time.', duration: 3000 });
      return;
    }

    const btn = document.getElementById('confirmScheduleBtn');
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div> Scheduling...';

    try {
      const isoDate = new Date(dateTime).toISOString();
      const res = await fetch(`/api/schedule/${currentBlogId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scheduled_at: isoDate })
      });
      const data = await res.json();

      if (data.success) {
        showToast({ type: 'success', title: 'Scheduled!', message: data.message || 'Blog scheduled successfully.', duration: 4000 });
        bootstrap.Modal.getInstance(document.getElementById('scheduleModal')).hide();
        const row = document.getElementById(`row-${currentBlogId}`);
        if (row) {
          row.style.transition = 'all 0.3s ease';
          row.style.opacity = '0';
          row.style.transform = 'translateX(20px)';
          setTimeout(() => { row.remove(); checkEmptyState(); }, 300);
        }
      } else {
        showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to schedule.', duration: 5000 });
      }
    } catch (err) {
      showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 5000 });
    } finally {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-calendar-check"></i> Schedule';
    }
  });
})();

function openScheduleModal(blogId) {
  currentBlogId = blogId;

  // Set min to now
  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  document.getElementById('scheduleDateTime').min = now.toISOString().slice(0, 16);
  document.getElementById('scheduleDateTime').value = '';

  // Try to get the title from the row
  const row = document.getElementById(`row-${blogId}`);
  const title = row ? row.querySelector('.col-title').textContent.trim() : 'Blog';
  document.getElementById('schedule-modal-title').textContent = title;

  const modal = new bootstrap.Modal(document.getElementById('scheduleModal'));
  modal.show();
  loadBestTimeSuggestions();
}

async function openViewModal(id) {
  currentBlogId = id;

  const modalEl = document.getElementById('viewModal');
  if (!modalEl) {
    showToast({ type: 'error', title: 'Error', message: 'Modal not found. Please refresh the page.', duration: 5000 });
    return;
  }

  try {
    const res = await fetch(`/api/get_blog/${id}`);
    const data = await res.json();

    if (!data.success) {
      showToast({ type: 'error', title: 'Error', message: data.message || 'Failed to load blog.', duration: 5000 });
      return;
    }

    const blog = data.blog;
    const content = blog.content;

    const el = (elId) => document.getElementById(elId);

    // Set title
    const titleEl = el('view-modal-title');
    if (titleEl) titleEl.innerText = (blog.title || 'Untitled').replace(/\*\*/g, '');

    // Set category
    const catEl = el('view-modal-category');
    if (catEl) catEl.innerText = blog.category || 'General';

    // Set author info
    const authorName = blog.author || blog.created_by || 'Unknown Author';
    const nameEl = el('view-author-name');
    if (nameEl) nameEl.innerText = authorName;
    const avatarEl = el('view-author-avatar');
    if (avatarEl) avatarEl.innerText = authorName.substring(0, 2).toUpperCase();

    // Set date
    if (blog.updated_at) {
      const dateEl = el('view-submit-date');
      if (dateEl) dateEl.innerText = 'Submitted on ' + new Date(blog.updated_at).toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric'
      });
    }

    // Get content - use html field for formatted content
    let contentHtml = '';
    if (content && typeof content === 'object') {
      contentHtml = content.html || content.body || '';
    } else {
      contentHtml = content || '';
    }

    // Set content with proper HTML rendering
    const contentEl = el('view-modal-content');
    if (contentEl) contentEl.innerHTML = contentHtml || '<p class="text-muted">No content available</p>';

    // Display TOC if available
    const tocContainer = el('view-modal-toc');
    const tocContent = el('view-modal-toc-content');
    if (tocContainer && tocContent) {
      if (content && typeof content === 'object' && content.toc && content.toc.length > 0) {
        let tocHtml = '<ul>';
        content.toc.forEach(item => {
          tocHtml += `<li class="toc-level-${item.level}">
            <a href="#${item.slug}">${item.text}</a>
          </li>`;
        });
        tocHtml += '</ul>';
        tocContent.innerHTML = tocHtml;
        tocContainer.classList.remove('d-none');
      } else if (content && typeof content === 'object' && content.toc_html) {
        tocContent.innerHTML = content.toc_html;
        tocContainer.classList.remove('d-none');
      } else {
        tocContainer.classList.add('d-none');
      }
    }

    // Calculate reading time and word count
    const textContent = contentHtml.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    const wordCount = textContent.split(' ').filter(w => w.length > 0).length;
    const readingTime = Math.ceil(wordCount / 200);

    const readEl = el('view-modal-reading-time');
    if (readEl) readEl.innerText = readingTime + ' min read';
    const wordEl = el('view-modal-word-count');
    if (wordEl) wordEl.innerText = wordCount + ' words';

    // Set button actions
    const approveBtn = el('view-approve-btn');
    if (approveBtn) approveBtn.onclick = function() {
      bootstrap.Modal.getInstance(modalEl).hide();
      approveBlog(id);
    };

    const rejectBtn = el('view-reject-btn');
    if (rejectBtn) rejectBtn.onclick = function() {
      bootstrap.Modal.getInstance(modalEl).hide();
      rejectToDraft(id);
    };

    const scheduleBtn = el('view-schedule-btn');
    if (scheduleBtn) scheduleBtn.onclick = function() {
      bootstrap.Modal.getInstance(modalEl).hide();
      openScheduleModal(id);
    };

    // Show modal
    const viewModal = new bootstrap.Modal(modalEl);
    viewModal.show();
  } catch (err) {
    console.error('openViewModal error:', err);
    showToast({
      type: 'error',
      title: 'Error',
      message: err.message || 'Failed to load blog content.',
      duration: 5000
    });
  }
}

async function openReviewModal(id) {
  currentBlogId = id;

  const modalEl = document.getElementById('reviewModal');
  if (!modalEl) {
    showToast({ type: 'error', title: 'Error', message: 'Modal not found. Please refresh the page.', duration: 5000 });
    return;
  }

  try {
    const res = await fetch(`/api/get_blog/${id}`);
    const data = await res.json();

    if (!data.success) {
      showToast({ type: 'error', title: 'Error', message: data.message || 'Failed to load blog.', duration: 5000 });
      return;
    }

    const blog = data.blog;
    const content = blog.content;
    const el = (elId) => document.getElementById(elId);

    const titleEl = el('viewTitle');
    if (titleEl) titleEl.innerText = (blog.title || 'Untitled').replace(/\*\*/g, '');

    const catEl = el('review-category');
    if (catEl) catEl.innerText = blog.category || 'General';

    // Get content - use html field for formatted content
    let contentHtml = '';
    if (content && typeof content === 'object') {
      contentHtml = content.html || content.body || '';
    } else {
      contentHtml = content || '';
    }

    const contentEl = el('viewContent');
    if (contentEl) contentEl.innerHTML = contentHtml || '<p class="text-muted">No content</p>';

    // Display TOC if available
    const tocContainer = el('review-modal-toc');
    const tocContent = el('review-modal-toc-content');
    if (tocContainer && tocContent) {
      if (content && typeof content === 'object' && content.toc && content.toc.length > 0) {
        let tocHtml = '<ul>';
        content.toc.forEach(item => {
          tocHtml += `<li class="toc-level-${item.level}">
            <a href="#${item.slug}">${item.text}</a>
          </li>`;
        });
        tocHtml += '</ul>';
        tocContent.innerHTML = tocHtml;
        tocContainer.classList.remove('d-none');
      } else if (content && typeof content === 'object' && content.toc_html) {
        tocContent.innerHTML = content.toc_html;
        tocContainer.classList.remove('d-none');
      } else {
        tocContainer.classList.add('d-none');
      }
    }

    // Calculate reading time and word count
    const textContent = contentHtml.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
    const wordCount = textContent.split(' ').filter(w => w.length > 0).length;
    const readingTime = Math.ceil(wordCount / 200);

    const readEl = el('review-reading-time');
    if (readEl) readEl.innerText = readingTime + ' min read';
    const wordEl = el('review-word-count');
    if (wordEl) wordEl.innerText = wordCount + ' words';

    const modalApproveBtn = el('modalApproveBtn');
    if (modalApproveBtn) modalApproveBtn.onclick = function () {
      const modalInstance = bootstrap.Modal.getInstance(modalEl);
      if (modalInstance) modalInstance.hide();
      approveBlog(id);
    };

    const modalScheduleBtn = el('modalScheduleBtn');
    if (modalScheduleBtn) modalScheduleBtn.onclick = function () {
      const modalInstance = bootstrap.Modal.getInstance(modalEl);
      if (modalInstance) modalInstance.hide();
      openScheduleModal(id);
    };

    const modalRejectBtn = el('modalRejectBtn');
    if (modalRejectBtn) modalRejectBtn.onclick = function () {
      const modalInstance = bootstrap.Modal.getInstance(modalEl);
      if (modalInstance) modalInstance.hide();
      rejectToDraft(id);
    };

    const reviewModal = new bootstrap.Modal(modalEl);
    reviewModal.show();
  } catch (err) {
    console.error('openReviewModal error:', err);
    showToast({
      type: 'error',
      title: 'Error',
      message: err.message || 'Failed to load blog content.',
      duration: 5000
    });
  }
}

async function approveBlog(id) {
  // Find and disable the dropdown button for this row
  const row = document.getElementById(`row-${id}`);
  const dropdownBtn = row ? row.querySelector('.btn-dropdown-trigger') : null;
  if (dropdownBtn) {
    dropdownBtn.disabled = true;
    dropdownBtn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
  }

  try {
    const res = await fetch(`/api/update_status/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'PUBLISHED' })
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'success',
        title: 'Blog Published!',
        message: 'The blog has been approved and is now live on the site.',
        duration: 4000
      });
      if (row) {
        row.style.transition = 'all 0.3s ease';
        row.style.opacity = '0';
        row.style.transform = 'translateX(20px)';
        setTimeout(() => {
          row.remove();
          checkEmptyState();
        }, 300);
      }
    } else {
      if (dropdownBtn) {
        dropdownBtn.disabled = false;
        dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
      }
      showToast({
        type: 'error',
        title: 'Approval Failed',
        message: data.error || 'Could not approve the blog.',
        duration: 5000
      });
    }
  } catch (err) {
    if (dropdownBtn) {
      dropdownBtn.disabled = false;
      dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
    }
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to approve blog. Please try again.',
      duration: 5000
    });
  }
}

async function rejectToDraft(id) {
  // Find and disable the dropdown button for this row
  const row = document.getElementById(`row-${id}`);
  const dropdownBtn = row ? row.querySelector('.btn-dropdown-trigger') : null;
  if (dropdownBtn) {
    dropdownBtn.disabled = true;
    dropdownBtn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
  }

  try {
    const res = await fetch(`/api/update_status/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'DRAFT' })
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'warning',
        title: 'Moved to Drafts',
        message: 'The blog has been rejected and moved back to drafts.',
        duration: 4000
      });
      if (row) {
        row.style.transition = 'all 0.3s ease';
        row.style.opacity = '0';
        row.style.transform = 'translateX(20px)';
        setTimeout(() => {
          row.remove();
          checkEmptyState();
        }, 300);
      }
    } else {
      if (dropdownBtn) {
        dropdownBtn.disabled = false;
        dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
      }
      showToast({
        type: 'error',
        title: 'Rejection Failed',
        message: data.error || 'Could not reject the blog.',
        duration: 5000
      });
    }
  } catch (err) {
    if (dropdownBtn) {
      dropdownBtn.disabled = false;
      dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
    }
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to reject blog. Please try again.',
      duration: 5000
    });
  }
}

// ==================== BEST TIME SUGGESTIONS ====================

const FALLBACK_SUGGESTIONS = [
  { day: "Tuesday", day_index: 2, hour: 10, display_time: "Tuesday, 10:00 AM", reasoning: "Tuesdays mid-morning have high engagement across most blogs" },
  { day: "Thursday", day_index: 4, hour: 14, display_time: "Thursday, 2:00 PM", reasoning: "Thursday afternoons are peak reading time for most audiences" },
  { day: "Wednesday", day_index: 3, hour: 9, display_time: "Wednesday, 9:00 AM", reasoning: "Mid-week mornings capture early readers checking content" }
];

async function loadBestTimeSuggestions() {
  const container = document.getElementById('bestTimeSuggestions');
  const list = document.getElementById('bestTimeSuggestionsList');

  list.innerHTML = `
    <div class="best-time-loading">
      <div class="spinner-border spinner-border-sm text-primary"></div>
      <span>Analyzing your traffic data...</span>
    </div>`;

  try {
    const res = await fetch('/api/schedule/best-time?t=' + Date.now());
    const data = await res.json();

    if (data.success && data.suggestions && data.suggestions.length > 0) {
      renderSuggestionChips(list, data.suggestions, 'scheduleDateTime', true);
    } else {
      renderSuggestionChips(list, FALLBACK_SUGGESTIONS, 'scheduleDateTime', false, data.message || null);
    }
  } catch (err) {
    renderSuggestionChips(list, FALLBACK_SUGGESTIONS, 'scheduleDateTime', false, null);
  }
}

function renderSuggestionChips(listEl, suggestions, inputId, fromAnalytics, apiMessage) {
  let sourceLabel;
  if (fromAnalytics) {
    sourceLabel = '<span class="best-time-source best-time-source-analytics"><i class="bi bi-check-circle-fill"></i> Based on your Google Analytics data (last 28 days)</span>';
  } else if (apiMessage) {
    sourceLabel = `<span class="best-time-source best-time-source-warning"><i class="bi bi-exclamation-triangle-fill"></i> ${apiMessage}</span>`;
  } else {
    sourceLabel = '<span class="best-time-source"><i class="bi bi-lightbulb-fill"></i> General best practices</span>';
  }

  listEl.innerHTML = sourceLabel + suggestions.map(s =>
    `<button type="button" class="best-time-chip" onclick="applyBestTime(${s.day_index}, ${s.hour}, '${inputId}')" title="${s.reasoning}">
      <i class="bi bi-clock"></i> ${s.display_time}
      <span class="best-time-score">${s.reasoning}</span>
    </button>`
  ).join('');
}

function applyBestTime(dayIndex, hour, inputId) {
  const now = new Date();
  const currentDay = now.getDay();
  let daysUntil = dayIndex - currentDay;
  if (daysUntil < 0) daysUntil += 7;
  if (daysUntil === 0 && hour <= now.getHours()) daysUntil = 7;

  const target = new Date(now);
  target.setDate(target.getDate() + daysUntil);
  target.setHours(hour, 0, 0, 0);

  const year = target.getFullYear();
  const month = String(target.getMonth() + 1).padStart(2, '0');
  const day = String(target.getDate()).padStart(2, '0');
  const hrs = String(target.getHours()).padStart(2, '0');
  const mins = '00';

  document.getElementById(inputId).value = `${year}-${month}-${day}T${hrs}:${mins}`;
}
