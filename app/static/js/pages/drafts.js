/**
 * Drafts Page JavaScript
 */

let currentEditingId = null;
let currentViewingId = null;

/**
 * Generate URL-friendly slug from title
 */
function generateSlug(title) {
  if (!title) return '';
  return title.toLowerCase()
    .replace(/[^\w\s-]/g, '')      // Remove non-word chars
    .replace(/[\s_]+/g, '-')        // Replace spaces/underscores with hyphens
    .replace(/-+/g, '-')            // Remove multiple hyphens
    .trim()
    .replace(/^-|-$/g, '')          // Remove leading/trailing hyphens
    .substring(0, 100);             // Max 100 chars
}

// Check if there are remaining drafts and show empty state if needed
function checkEmptyState() {
  const container = document.querySelector('.drafts-container');
  const remainingRows = container.querySelectorAll('.draft-row');

  if (remainingRows.length === 0) {
    // Remove the header too
    const header = container.querySelector('.drafts-header');
    if (header) header.remove();

    // Add empty state
    container.innerHTML = `
      <div class="text-center py-5">
        <div class="mb-3 text-muted opacity-50"><i class="bi bi-file-earmark-text fs-1"></i></div>
        <p class="text-secondary fw-bold">No drafts found.</p>
        <a href="/create" class="btn btn-sm btn-outline-primary mt-2 rounded-pill px-4">Create New</a>
      </div>
    `;
  }
}

const initEditor = (initialContent) => {
  if (tinymce.get('editor-canvas')) {
    tinymce.remove('#editor-canvas');
  }

  tinymce.init({
    selector: '#editor-canvas',
    plugins: 'anchor autolink charmap codesample emoticons image link lists media searchreplace table visualblocks wordcount',
    toolbar: 'undo redo | blocks fontfamily fontsize | bold italic underline strikethrough | link image media table | align lineheight | numlist bullist indent outdent | emoticons charmap | removeformat',
    height: 500,
    menubar: false,
    statusbar: false,
    setup: function (editor) {
      editor.on('init', function () {
        editor.setContent(initialContent || '');
      });
    }
  });
};

async function openViewModal(id) {
  currentViewingId = id;
  try {
    const res = await fetch(`/api/get_blog/${id}`);
    if (!res.ok) throw new Error(`Server error (${res.status})`);
    const data = await res.json();

    if (data.success) {
      const blog = data.blog;

      // Set title
      document.getElementById('view-modal-title').innerText = (blog.title || 'Untitled').replace(/\*\*/g, '');

      // Set status badge
      const statusBadge = document.getElementById('view-modal-status');
      const status = blog.status || 'DRAFT';
      statusBadge.innerText = status;
      statusBadge.className = 'status-badge status-' + status.toLowerCase().replace('_', '-');

      // Set category
      document.getElementById('view-modal-category').innerText = blog.category || 'General';

      // Set date
      if (blog.updated_at) {
        document.getElementById('view-modal-date').innerText = new Date(blog.updated_at).toLocaleDateString('en-US', {
          year: 'numeric', month: 'short', day: 'numeric'
        });
      }

      // Get content - use html field for formatted content
      let contentHtml = '';
      const content = blog.content;
      if (typeof content === 'object') {
        contentHtml = content.html || content.body || '';
      } else {
        contentHtml = content || '';
      }

      // Set content with proper HTML rendering
      document.getElementById('view-modal-content').innerHTML = contentHtml || '<p class="text-muted">No content available</p>';

      // Display TOC if available
      const tocContainer = document.getElementById('view-modal-toc');
      const tocContent = document.getElementById('view-modal-toc-content');
      if (typeof content === 'object' && content.toc && content.toc.length > 0) {
        let tocHtml = '<ul>';
        content.toc.forEach(item => {
          tocHtml += `<li class="toc-level-${item.level}">
            <a href="#${item.slug}">${item.text}</a>
          </li>`;
        });
        tocHtml += '</ul>';
        tocContent.innerHTML = tocHtml;
        tocContainer.classList.remove('d-none');
      } else if (typeof content === 'object' && content.toc_html) {
        tocContent.innerHTML = content.toc_html;
        tocContainer.classList.remove('d-none');
      } else {
        tocContainer.classList.add('d-none');
      }

      // Calculate reading time and word count from content
      const textContent = contentHtml.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
      const wordCount = textContent.split(' ').filter(w => w.length > 0).length;
      const readingTime = Math.ceil(wordCount / 200);

      document.getElementById('view-modal-reading-time').innerText = readingTime + ' min read';
      document.getElementById('view-modal-word-count').innerText = wordCount + ' words';

      // Set button actions
      document.getElementById('view-edit-btn').onclick = function() {
        bootstrap.Modal.getInstance(document.getElementById('viewModal')).hide();
        openEditModal(id);
      };

      document.getElementById('view-submit-btn').onclick = function() {
        bootstrap.Modal.getInstance(document.getElementById('viewModal')).hide();
        submitForReview(id);
      };

      // Show modal
      const viewModal = new bootstrap.Modal(document.getElementById('viewModal'));
      viewModal.show();
    } else {
      showToast({
        type: 'error',
        title: 'Error',
        message: data.message || 'Failed to load draft.',
        duration: 5000
      });
    }
  } catch (err) {
    console.error('openViewModal error:', err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: err.message || 'Could not connect to server.',
      duration: 5000
    });
  }
}

async function openEditModal(id) {
  currentEditingId = id;
  try {
    const res = await fetch(`/api/get_blog/${id}`);
    if (!res.ok) throw new Error(`Server error (${res.status})`);
    const data = await res.json();
    if (data.success) {
      document.getElementById('modal-title').value = data.blog.title;

      // Set slug field
      document.getElementById('modal-slug').value = data.blog.slug || '';

      // Set SEO fields
      document.getElementById('modal-seo-title').value = data.blog.seo_title || '';
      document.getElementById('modal-seo-description').value = data.blog.seo_description || '';
      updateSeoCounters();

      // Set cover image
      const coverUrl = data.blog.cover_image || '';
      setCoverImagePreview(coverUrl);

      const modalElement = document.getElementById('editModal');
      const editModal = new bootstrap.Modal(modalElement);
      editModal.show();

      // Get content - use html for editing (TinyMCE handles HTML)
      let content = '';
      const blogContent = data.blog.content;
      if (typeof blogContent === 'object') {
        content = blogContent.html || blogContent.body || '';
      } else {
        content = blogContent || '';
      }
      initEditor(content);

      // Setup slug event listeners
      const titleInput = document.getElementById('modal-title');
      const slugInput = document.getElementById('modal-slug');
      const regenerateBtn = document.getElementById('regenerate-slug');

      // Remove old event listeners by cloning
      const newTitleInput = titleInput.cloneNode(true);
      titleInput.parentNode.replaceChild(newTitleInput, titleInput);

      const newRegenerateBtn = regenerateBtn.cloneNode(true);
      regenerateBtn.parentNode.replaceChild(newRegenerateBtn, regenerateBtn);

      // Auto-generate slug from title on blur (only if slug is empty)
      newTitleInput.addEventListener('blur', function() {
        const slugField = document.getElementById('modal-slug');
        if (!slugField.value) {
          slugField.value = generateSlug(this.value);
        }
      });

      // Regenerate slug button
      newRegenerateBtn.addEventListener('click', function() {
        const title = document.getElementById('modal-title').value;
        document.getElementById('modal-slug').value = generateSlug(title);
      });

      // Setup SEO toggle
      setupSeoToggle();

    } else {
      showToast({
        type: 'error',
        title: 'Error',
        message: data.message || 'Failed to load draft.',
        duration: 5000
      });
    }
  } catch (err) {
    console.error('openEditModal error:', err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: err.message || 'Could not connect to server.',
      duration: 5000
    });
  }
}

/**
 * Setup SEO section toggle and character counters
 */
function setupSeoToggle() {
  const toggleBtn = document.getElementById('seo-toggle-btn');
  const seoFields = document.getElementById('seo-fields');
  const seoTitle = document.getElementById('modal-seo-title');
  const seoDesc = document.getElementById('modal-seo-description');

  // Remove old listener by cloning
  const newToggleBtn = toggleBtn.cloneNode(true);
  toggleBtn.parentNode.replaceChild(newToggleBtn, toggleBtn);

  newToggleBtn.addEventListener('click', function() {
    const isVisible = seoFields.style.display !== 'none';
    seoFields.style.display = isVisible ? 'none' : 'block';
    this.classList.toggle('active', !isVisible);
  });

  // Character counters
  seoTitle.addEventListener('input', updateSeoCounters);
  seoDesc.addEventListener('input', updateSeoCounters);
}

/**
 * Update SEO character counters
 */
function updateSeoCounters() {
  const seoTitle = document.getElementById('modal-seo-title');
  const seoDesc = document.getElementById('modal-seo-description');
  document.getElementById('seo-title-count').textContent = seoTitle.value.length;
  document.getElementById('seo-desc-count').textContent = seoDesc.value.length;
}

async function saveModalChanges() {
  const updatedTitle = document.getElementById('modal-title').value;
  const editor = tinymce.get('editor-canvas');
  if (!editor) return;
  const updatedContent = editor.getContent();

  // Get slug (auto-generate if empty)
  let slug = document.getElementById('modal-slug').value.trim();
  if (!slug) {
    slug = generateSlug(updatedTitle);
  }

  // Get SEO fields
  const seoTitle = document.getElementById('modal-seo-title').value.trim();
  const seoDescription = document.getElementById('modal-seo-description').value.trim();

  // Get cover image
  const coverImageImg = document.getElementById('coverImageImg');
  const coverImage = coverImageImg ? coverImageImg.src : '';
  const coverImageValue = document.getElementById('coverImagePreview').style.display !== 'none' ? coverImage : '';

  const saveBtn = document.getElementById('save-changes-btn');
  const originalContent = saveBtn.innerHTML;
  saveBtn.disabled = true;
  saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status"></span> Saving...';

  try {
    const res = await fetch(`/api/update_blog/${currentEditingId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: updatedTitle,
        content: updatedContent,
        slug: slug,
        seo_title: seoTitle,
        seo_description: seoDescription,
        cover_image: coverImageValue
      })
    });

    const data = await res.json();
    if (data.success) {
      const row = document.querySelector(`#row-${currentEditingId} .title`);
      if (row) row.innerText = updatedTitle;
      bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
      saveBtn.disabled = false;
      saveBtn.innerHTML = originalContent;
      showToast({
        type: 'success',
        title: 'Changes Saved',
        message: 'Your draft has been updated successfully.',
        duration: 4000
      });
    } else {
      saveBtn.disabled = false;
      saveBtn.innerHTML = originalContent;
      showToast({
        type: 'error',
        title: 'Save Failed',
        message: data.error || 'Could not save changes.',
        duration: 5000
      });
    }
  } catch (err) {
    saveBtn.disabled = false;
    saveBtn.innerHTML = originalContent;
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to save changes.',
      duration: 5000
    });
  }
}

async function submitForReview(id) {
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
      body: JSON.stringify({ status: "UNDER_REVIEW" })
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'success',
        title: 'Submitted for Review',
        message: 'Your draft has been sent for approval.',
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
        title: 'Submission Failed',
        message: data.error || 'Could not submit for review.',
        duration: 5000
      });
    }
  } catch (e) {
    if (dropdownBtn) {
      dropdownBtn.disabled = false;
      dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
    }
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to submit draft.',
      duration: 5000
    });
  }
}

async function humanizeDraft(id) {
  const row = document.getElementById(`row-${id}`);
  const dropdownBtn = row ? row.querySelector('.btn-dropdown-trigger') : null;
  if (dropdownBtn) {
    dropdownBtn.disabled = true;
    dropdownBtn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
  }

  showToast({
    type: 'info',
    title: 'Humanizing Content',
    message: 'This may take 15-20 seconds. Please wait...',
    duration: 20000
  });

  try {
    const res = await fetch(`/api/humanize/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'success',
        title: 'Content Humanized',
        message: 'Your draft has been rewritten to bypass AI detectors.',
        duration: 5000
      });
    } else {
      showToast({
        type: 'error',
        title: 'Humanization Failed',
        message: data.error || 'Could not humanize content.',
        duration: 5000
      });
    }
  } catch (e) {
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to humanize draft.',
      duration: 5000
    });
  } finally {
    if (dropdownBtn) {
      dropdownBtn.disabled = false;
      dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
    }
  }
}

async function deleteDraft(id) {
  // Find and disable the dropdown button for this row
  const row = document.getElementById(`row-${id}`);
  const dropdownBtn = row ? row.querySelector('.btn-dropdown-trigger') : null;
  if (dropdownBtn) {
    dropdownBtn.disabled = true;
    dropdownBtn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
  }

  try {
    const res = await fetch(`/api/delete_blog/${id}`, { method: 'DELETE' });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'warning',
        title: 'Draft Deleted',
        message: 'The draft has been permanently removed.',
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
        title: 'Delete Failed',
        message: data.error || 'Could not delete draft.',
        duration: 5000
      });
    }
  } catch (e) {
    if (dropdownBtn) {
      dropdownBtn.disabled = false;
      dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
    }
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to delete draft.',
      duration: 5000
    });
  }
}

// ==================== SCHEDULE & SUBMIT ====================

let scheduleBlogId = null;

const FALLBACK_SUGGESTIONS = [
  { day: "Tuesday", day_index: 2, hour: 10, display_time: "Tuesday, 10:00 AM", reasoning: "Tuesdays mid-morning have high engagement across most blogs" },
  { day: "Thursday", day_index: 4, hour: 14, display_time: "Thursday, 2:00 PM", reasoning: "Thursday afternoons are peak reading time for most audiences" },
  { day: "Wednesday", day_index: 3, hour: 9, display_time: "Wednesday, 9:00 AM", reasoning: "Mid-week mornings capture early readers checking content" }
];

function openScheduleModal(blogId) {
  scheduleBlogId = blogId;

  const now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  document.getElementById('scheduleDateTime').min = now.toISOString().slice(0, 16);
  document.getElementById('scheduleDateTime').value = '';

  const modal = new bootstrap.Modal(document.getElementById('scheduleModal'));
  modal.show();
  loadBestTimeSuggestions();
}

document.addEventListener('DOMContentLoaded', function() {
  const confirmBtn = document.getElementById('confirmScheduleBtn');
  if (confirmBtn) {
    confirmBtn.addEventListener('click', async function() {
      const dateTime = document.getElementById('scheduleDateTime').value;
      if (!dateTime) {
        showToast({ type: 'error', title: 'Error', message: 'Please select a date and time.', duration: 3000 });
        return;
      }

      const btn = this;
      btn.disabled = true;
      btn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div> Submitting...';

      try {
        const isoDate = new Date(dateTime).toISOString();
        const res = await fetch(`/api/schedule/${scheduleBlogId}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scheduled_at: isoDate })
        });
        const data = await res.json();

        if (data.success) {
          showToast({ type: 'success', title: 'Scheduled!', message: 'Blog submitted for approval with your requested schedule.', duration: 4000 });
          bootstrap.Modal.getInstance(document.getElementById('scheduleModal')).hide();
          const row = document.getElementById(`row-${scheduleBlogId}`);
          if (row) {
            row.style.transition = 'all 0.3s ease';
            row.style.opacity = '0';
            row.style.transform = 'translateX(20px)';
            setTimeout(() => { row.remove(); checkEmptyState(); }, 300);
          }
        } else {
          showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to schedule.', duration: 4000 });
        }
      } catch (err) {
        showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 4000 });
      } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-calendar-check"></i> Schedule & Submit';
      }
    });
  }
});

async function loadBestTimeSuggestions() {
  const container = document.getElementById('bestTimeSuggestions');
  const list = document.getElementById('bestTimeSuggestionsList');

  list.innerHTML = `
    <div class="best-time-loading">
      <div class="spinner-border spinner-border-sm text-primary"></div>
      <span>Analyzing best publish times...</span>
    </div>`;

  try {
    const res = await fetch('/api/schedule/best-time?t=' + Date.now());
    const data = await res.json();

    if (data.success && data.suggestions && data.suggestions.length > 0) {
      renderSuggestionChips(list, data.suggestions, true);
    } else {
      renderSuggestionChips(list, FALLBACK_SUGGESTIONS, false, data.message || null);
    }
  } catch (err) {
    renderSuggestionChips(list, FALLBACK_SUGGESTIONS, false, null);
  }
}

function renderSuggestionChips(listEl, suggestions, fromAnalytics, apiMessage) {
  let sourceLabel;
  if (fromAnalytics) {
    sourceLabel = '<span class="best-time-source best-time-source-analytics"><i class="bi bi-check-circle-fill"></i> Based on your Google Analytics data (last 28 days)</span>';
  } else if (apiMessage) {
    sourceLabel = `<span class="best-time-source best-time-source-warning"><i class="bi bi-exclamation-triangle-fill"></i> ${apiMessage}</span>`;
  } else {
    sourceLabel = '<span class="best-time-source"><i class="bi bi-lightbulb-fill"></i> General best practices</span>';
  }

  listEl.innerHTML = sourceLabel + suggestions.map(s =>
    `<button type="button" class="best-time-chip" onclick="applyBestTime(${s.day_index}, ${s.hour})" title="${s.reasoning}">
      <i class="bi bi-clock"></i> ${s.display_time}
      <span class="best-time-score">${s.reasoning}</span>
    </button>`
  ).join('');
}

function applyBestTime(dayIndex, hour) {
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

  document.getElementById('scheduleDateTime').value = `${year}-${month}-${day}T${hrs}:00`;
}

// ==================== COVER IMAGE PICKER ====================

function setCoverImagePreview(url) {
  var preview = document.getElementById('coverImagePreview');
  var img = document.getElementById('coverImageImg');
  var placeholder = document.getElementById('coverImagePlaceholder');

  if (url) {
    img.src = url;
    preview.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';
  } else {
    img.src = '';
    preview.style.display = 'none';
    if (placeholder) placeholder.style.display = '';
  }
}

function removeCoverImage() {
  setCoverImagePreview('');
}

function openImagePicker() {
  var overlay = document.getElementById('imagePickerOverlay');
  var grid = document.getElementById('imagePickerGrid');

  overlay.classList.add('active');
  grid.innerHTML = '<div class="text-center py-4" style="grid-column:1/-1;"><div class="spinner-border spinner-border-sm text-primary opacity-50"></div><p class="text-secondary mt-2 mb-0" style="font-size:0.82rem;">Loading gallery...</p></div>';

  var xhr = new XMLHttpRequest();
  xhr.open('GET', '/api/gallery/images?per_page=50', true);
  xhr.timeout = 10000;

  xhr.onload = function () {
    try {
      if (xhr.status === 200) {
        var data = JSON.parse(xhr.responseText);
        if (data.success && data.images && data.images.length > 0) {
          var html = '';
          for (var i = 0; i < data.images.length; i++) {
            var img = data.images[i];
            html += '<div class="image-picker-item" onclick="selectCoverImage(\'' + img.url + '\')">';
            html += '<img src="' + img.url + '" alt="' + (img.filename || '') + '" loading="lazy">';
            html += '</div>';
          }
          grid.innerHTML = html;
        } else {
          grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;"><i class="bi bi-images" style="font-size:1.5rem;display:block;margin-bottom:0.5rem;"></i>No images in gallery. Upload images on the Gallery page first.</div>';
        }
      } else {
        grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
      }
    } catch (e) {
      grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
    }
  };

  xhr.onerror = function () {
    grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
  };

  xhr.ontimeout = function () {
    grid.innerHTML = '<div class="image-picker-empty" style="grid-column:1/-1;">Error loading images.</div>';
  };

  xhr.send();
}

function closeImagePicker(event) {
  if (event && event.target !== event.currentTarget) return;
  document.getElementById('imagePickerOverlay').classList.remove('active');
}

function selectCoverImage(url) {
  setCoverImagePreview(url);
  closeImagePicker();
}
