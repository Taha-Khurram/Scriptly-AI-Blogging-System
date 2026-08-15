/**
 * All Blogs Listing - all_blogs.js
 * Handles blog listing, filtering, pagination, and date range modal
 */

var currentStatus = 'all';
var currentCategory = 'all';
var currentSearch = '';
var currentDateFrom = '';
var currentDateTo = '';
var currentPage = 1;
var perPage = 10;
var searchTimeout = null;
var initialLoadDone = false;

(function initAllBlogs() {
    applyUrlParams();
    setupFilterTabs();
    setupControls();
    if (window.location.search) {
        loadBlogs();
    } else {
        initialLoadDone = true;
    }
})();

function applyUrlParams() {
    var params = new URLSearchParams(window.location.search);
    var status = params.get('status');
    if (status) {
        currentStatus = status;
        document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(function(tab) {
            tab.classList.remove('active');
            if (tab.dataset.filter === status) {
                tab.classList.add('active');
            }
        });
    }

    // ?search= is how the dashboard's header search hands a query off to this
    // screen — mirror it into the field so the reader can see and edit what is
    // being filtered on, rather than facing a narrowed list with an empty box.
    var search = (params.get('search') || '').trim();
    if (search) {
        currentSearch = search;
        var searchField = document.getElementById('searchInput');
        if (searchField) searchField.value = search;
    }
}

// ==================== FILTER TABS ====================

function setupFilterTabs() {
    document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(tab => {
        tab.addEventListener('click', function () {
            document.querySelectorAll('.blogs-filter-tabs .filter-tab').forEach(t => t.classList.remove('active'));
            this.classList.add('active');
            currentStatus = this.dataset.filter;
            currentPage = 1;
            loadBlogs();
        });
    });
}

// ==================== CONTROLS ====================

function setupControls() {
    const categorySelect = document.getElementById('categoryFilter');
    categorySelect.addEventListener('change', function () {
        currentCategory = this.value;
        currentPage = 1;
        loadBlogs();
    });

    const searchInput = document.getElementById('searchInput');
    searchInput.addEventListener('input', function () {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            currentSearch = this.value.trim();
            currentPage = 1;
            loadBlogs();
        }, 300);
    });
}

// ==================== LOAD BLOGS ====================

async function loadBlogs() {
    const listEl = document.getElementById('blogsList');
    listEl.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border spinner-border-sm text-primary opacity-50"></div>
            <p class="text-secondary fw-medium mt-2 mb-0">Loading blogs...</p>
        </div>`;

    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: perPage
        });
        if (currentStatus !== 'all') params.set('status', currentStatus);
        if (currentCategory !== 'all') params.set('category', currentCategory);
        if (currentSearch) params.set('search', currentSearch);
        if (currentDateFrom) params.set('date_from', currentDateFrom);
        if (currentDateTo) params.set('date_to', currentDateTo);

        const res = await fetch('/api/all-blogs?' + params.toString());
        const data = await res.json();

        if (data.success && data.blogs && data.blogs.length > 0) {
            listEl.innerHTML = data.blogs.map(renderBlogRow).join('');
            renderPagination(data.total, data.page, data.per_page);
        } else {
            listEl.innerHTML = `
                <div class="blogs-empty">
                    <i class="bi bi-journals"></i>
                    <p>No blogs found.</p>
                </div>`;
            document.getElementById('blogsPagination').innerHTML = '';
        }
    } catch (err) {
        console.error('Error loading blogs:', err);
        listEl.innerHTML = `
            <div class="text-center py-4 text-danger">
                <i class="bi bi-exclamation-circle"></i> Failed to load blogs.
            </div>`;
    }
}

// ==================== RENDER ROW ====================

function renderBlogRow(blog) {
    const id = blog.id;
    const title = escapeHtml(blog.title || 'Untitled');
    const authorName = escapeHtml(blog.author_name || blog.user_name || 'Unknown');
    const initial = authorName.charAt(0).toUpperCase();
    const category = escapeHtml(blog.category || 'Uncategorized');
    const status = blog.status || 'DRAFT';
    const updatedAt = formatDate(blog.updated_at || blog.created_at);

    return `
    <div class="blog-row" id="row-${id}">
        <div class="col-blog-title">
            <span class="blog-title-cell" title="${title}">${truncate(title, 45)}</span>
        </div>
        <div class="col-blog-author">
            <div class="blog-author-cell">
                <div class="blog-author-avatar">${initial}</div>
                <span class="blog-author-name">${truncate(authorName, 20)}</span>
            </div>
        </div>
        <div class="col-blog-category">
            <span class="blog-category-badge">${category}</span>
        </div>
        <div class="col-blog-status">
            ${getStatusBadge(status)}
        </div>
        <div class="col-blog-date">
            <span class="blog-date-cell">${updatedAt}</span>
        </div>
        <div class="col-blog-actions">
            <div class="dropdown">
                <button class="btn-dropdown-trigger" type="button" data-bs-toggle="dropdown" aria-expanded="false">
                    <i class="bi bi-three-dots-vertical"></i>
                </button>
                <ul class="dropdown-menu dropdown-menu-end">
                    <li>
                        <button class="dropdown-item" onclick="openEditModal('${id}')">
                            <i class="bi bi-pencil-square" style="color: var(--primary-color);"></i> Edit Blog
                        </button>
                    </li>
                    <li><hr class="dropdown-divider"></li>
                    <li>
                        <button class="dropdown-item text-danger" onclick="deleteBlog('${id}')">
                            <i class="bi bi-x-circle"></i> Delete
                        </button>
                    </li>
                </ul>
            </div>
        </div>
    </div>`;
}

// ==================== STATUS BADGE ====================

function getStatusBadge(status) {
    const statusMap = {
        'DRAFT': { label: 'Draft', cls: 'status-badge-draft' },
        'UNDER_REVIEW': { label: 'Under Review', cls: 'status-badge-under_review' },
        'PUBLISHED': { label: 'Published', cls: 'status-badge-published' },
        'REJECTED': { label: 'Rejected', cls: 'status-badge-rejected' }
    };
    const info = statusMap[status] || statusMap['DRAFT'];
    return `<span class="blog-status-badge ${info.cls}"><i class="bi bi-circle-fill"></i> ${info.label}</span>`;
}

// ==================== PAGINATION ====================

function renderPagination(total, page, perPage) {
    const container = document.getElementById('blogsPagination');
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
    loadBlogs();
    document.querySelector('.blogs-container').scrollIntoView({ behavior: 'smooth', block: 'start' });
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
    loadBlogs();
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
    loadBlogs();
}

// ==================== HELPERS ====================

function formatDate(timestamp) {
    if (!timestamp) return '';
    try {
        const date = new Date(timestamp);
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

// ==================== EDIT / DELETE ACTIONS ====================
// Reuses the shared blog editing flow (same backend as drafts): loads the blog
// into a TinyMCE modal, saves via /api/update_blog, deletes via /api/delete_blog.
// Works for any blog regardless of status, including PUBLISHED ones.

var currentEditingId = null;

function generateSlug(title) {
    if (!title) return '';
    return title.toLowerCase()
        .replace(/[^\w\s-]/g, '')
        .replace(/[\s_]+/g, '-')
        .replace(/-+/g, '-')
        .trim()
        .replace(/^-|-$/g, '')
        .substring(0, 100);
}

function initEditor(initialContent) {
    if (window.tinymce && tinymce.get('editor-canvas')) {
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
}

async function openEditModal(id) {
    currentEditingId = id;
    if (window.closeAllDropdowns) closeAllDropdowns();
    showActionLoader('Loading blog...');
    try {
        const res = await fetch(`/api/get_blog/${id}`);
        if (!res.ok) throw new Error(`Server error (${res.status})`);
        const data = await res.json();
        if (data.success) {
            hideActionLoader();
            document.getElementById('modal-title').value = data.blog.title || '';
            document.getElementById('modal-slug').value = data.blog.slug || '';
            document.getElementById('modal-seo-title').value = data.blog.seo_title || '';
            document.getElementById('modal-seo-description').value = data.blog.seo_description || '';
            updateSeoCounters();
            setCoverImagePreview(data.blog.cover_image || '');

            const modalElement = document.getElementById('editModal');
            const editModal = new bootstrap.Modal(modalElement);
            editModal.show();

            let content = '';
            const blogContent = data.blog.content;
            if (typeof blogContent === 'object' && blogContent !== null) {
                content = blogContent.html || blogContent.body || '';
            } else {
                content = blogContent || '';
            }
            initEditor(content);

            // Rebind slug listeners (clone to drop stale handlers)
            const titleInput = document.getElementById('modal-title');
            const regenerateBtn = document.getElementById('regenerate-slug');
            const newTitleInput = titleInput.cloneNode(true);
            titleInput.parentNode.replaceChild(newTitleInput, titleInput);
            const newRegenerateBtn = regenerateBtn.cloneNode(true);
            regenerateBtn.parentNode.replaceChild(newRegenerateBtn, regenerateBtn);

            newTitleInput.addEventListener('blur', function () {
                const slugField = document.getElementById('modal-slug');
                if (!slugField.value) {
                    slugField.value = generateSlug(this.value);
                }
            });
            newRegenerateBtn.addEventListener('click', function () {
                document.getElementById('modal-slug').value = generateSlug(document.getElementById('modal-title').value);
            });

            setupSeoToggle();
        } else {
            hideActionLoader();
            showToast({ type: 'error', title: 'Error', message: data.message || 'Failed to load blog.', duration: 5000 });
        }
    } catch (err) {
        hideActionLoader();
        console.error('openEditModal error:', err);
        showToast({ type: 'error', title: 'Connection Error', message: err.message || 'Could not connect to server.', duration: 5000 });
    }
}

function setupSeoToggle() {
    const toggleBtn = document.getElementById('seo-toggle-btn');
    const seoFields = document.getElementById('seo-fields');
    const seoTitle = document.getElementById('modal-seo-title');
    const seoDesc = document.getElementById('modal-seo-description');

    const newToggleBtn = toggleBtn.cloneNode(true);
    toggleBtn.parentNode.replaceChild(newToggleBtn, toggleBtn);
    newToggleBtn.addEventListener('click', function () {
        const isVisible = seoFields.style.display !== 'none';
        seoFields.style.display = isVisible ? 'none' : 'block';
        this.classList.toggle('active', !isVisible);
    });

    seoTitle.addEventListener('input', updateSeoCounters);
    seoDesc.addEventListener('input', updateSeoCounters);
}

function updateSeoCounters() {
    document.getElementById('seo-title-count').textContent = document.getElementById('modal-seo-title').value.length;
    document.getElementById('seo-desc-count').textContent = document.getElementById('modal-seo-description').value.length;
}

function copyBlogContent() {
    const editor = window.tinymce && tinymce.get('editor-canvas');
    if (!editor) return;
    const content = editor.getContent({ format: 'text' });
    navigator.clipboard.writeText(content).then(function () {
        showToast({ type: 'success', title: 'Copied', message: 'Content copied to clipboard.', duration: 2000 });
    }).catch(function () {
        showToast({ type: 'error', title: 'Error', message: 'Failed to copy content.', duration: 3000 });
    });
}

async function saveModalChanges() {
    const updatedTitle = document.getElementById('modal-title').value;
    const editor = window.tinymce && tinymce.get('editor-canvas');
    if (!editor) return;
    const updatedContent = editor.getContent();

    let slug = document.getElementById('modal-slug').value.trim();
    if (!slug) slug = generateSlug(updatedTitle);

    const seoTitle = document.getElementById('modal-seo-title').value.trim();
    const seoDescription = document.getElementById('modal-seo-description').value.trim();

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
            // Update the row title in place without a full reload
            const titleCell = document.querySelector(`#row-${currentEditingId} .blog-title-cell`);
            if (titleCell) {
                titleCell.textContent = truncate(updatedTitle, 45);
                titleCell.setAttribute('title', updatedTitle);
            }
            bootstrap.Modal.getInstance(document.getElementById('editModal')).hide();
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalContent;
            showToast({ type: 'success', title: 'Changes Saved', message: 'Your blog has been updated successfully.', duration: 4000 });
        } else {
            saveBtn.disabled = false;
            saveBtn.innerHTML = originalContent;
            showToast({ type: 'error', title: 'Save Failed', message: data.error || 'Could not save changes.', duration: 5000 });
        }
    } catch (err) {
        saveBtn.disabled = false;
        saveBtn.innerHTML = originalContent;
        showToast({ type: 'error', title: 'Error', message: 'Failed to save changes.', duration: 5000 });
    }
}

async function deleteBlog(id) {
    if (window.closeAllDropdowns) closeAllDropdowns();
    showActionLoader('Deleting blog...');

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
            hideActionLoader();
            showToast({ type: 'warning', title: 'Blog Deleted', message: 'The blog has been permanently removed.', duration: 4000 });
            if (row) {
                row.style.transition = 'all 0.3s ease';
                row.style.opacity = '0';
                row.style.transform = 'translateX(20px)';
                setTimeout(() => {
                    row.remove();
                    if (!document.querySelector('#blogsList .blog-row')) {
                        loadBlogs();
                    }
                }, 300);
            }
        } else {
            hideActionLoader();
            if (dropdownBtn) {
                dropdownBtn.disabled = false;
                dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
            }
            showToast({ type: 'error', title: 'Delete Failed', message: data.error || 'Could not delete blog.', duration: 5000 });
        }
    } catch (e) {
        hideActionLoader();
        if (dropdownBtn) {
            dropdownBtn.disabled = false;
            dropdownBtn.innerHTML = '<i class="bi bi-three-dots-vertical"></i>';
        }
        showToast({ type: 'error', title: 'Error', message: 'Failed to delete blog.', duration: 5000 });
    }
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
