/**
 * Gallery Page - gallery.js
 * Handles image upload, listing, delete, copy URL
 */

let currentPage = 1;
let deleteImageId = null;

(function initGallery() {
    try { setupUploadZone(); } catch(e) { console.error(e); }
    try { setupFileInput(); } catch(e) { console.error(e); }
    try { setupDeleteModal(); } catch(e) { console.error(e); }
    // Skip initial load — server already rendered first page
})();

// ==================== UPLOAD ZONE ====================

function setupUploadZone() {
    const zone = document.getElementById('uploadZone');
    if (!zone) return;

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const files = e.dataTransfer.files;
        if (files.length > 0) uploadFiles(files);
    });

    zone.addEventListener('click', (e) => {
        if (e.target.closest('.upload-zone-btn')) return;
        document.getElementById('fileInput').click();
    });
}

function setupFileInput() {
    const input = document.getElementById('fileInput');
    if (!input) return;
    input.addEventListener('change', function () {
        if (this.files.length > 0) {
            uploadFiles(this.files);
            this.value = '';
        }
    });
}

// ==================== UPLOAD ====================

async function uploadFiles(files) {
    const progressArea = document.getElementById('uploadProgress');
    const progressFill = document.getElementById('uploadFill');
    const progressText = document.getElementById('uploadText');

    progressArea.style.display = 'flex';
    let uploaded = 0;
    const total = files.length;

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        progressText.textContent = `Uploading ${i + 1} of ${total}...`;
        progressFill.style.width = `${(i / total) * 100}%`;

        const formData = new FormData();
        formData.append('file', file);

        try {
            const res = await fetch('/api/gallery/upload', {
                method: 'POST',
                body: formData
            });
            const data = await res.json();

            if (data.success) {
                uploaded++;
            } else {
                showGalleryToast(data.error || 'Upload failed');
            }
        } catch (err) {
            console.error('Upload error:', err);
            showGalleryToast('Upload failed');
        }
    }

    progressFill.style.width = '100%';
    progressText.textContent = `${uploaded} of ${total} uploaded`;

    setTimeout(() => {
        progressArea.style.display = 'none';
        progressFill.style.width = '0%';
    }, 1500);

    loadImages();
}

// ==================== LOAD IMAGES ====================

function loadImages() {
    const grid = document.getElementById('galleryGrid');
    if (!grid) return;

    grid.innerHTML = '<div class="gallery-loading"><div class="spinner-border spinner-border-sm text-primary opacity-50"></div><p class="text-secondary fw-medium mt-2 mb-0">Loading images...</p></div>';

    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/gallery/images?page=' + currentPage + '&per_page=20', true);
    xhr.timeout = 10000;

    xhr.onload = function () {
        try {
            if (xhr.status === 200) {
                var data = JSON.parse(xhr.responseText);
                if (data.success && data.images && data.images.length > 0) {
                    var html = '';
                    for (var i = 0; i < data.images.length; i++) {
                        var img = data.images[i];
                        html += '<div class="gallery-card" data-id="' + img.id + '">';
                        html += '<img src="' + img.url + '" alt="' + escapeHtml(img.filename) + '" loading="lazy">';
                        html += '<div class="gallery-card-overlay">';
                        html += '<div class="gallery-card-actions">';
                        html += '<button class="gallery-card-btn copy-btn" onclick="copyUrl(event, \'' + img.url + '\')" title="Copy URL"><i class="bi bi-clipboard"></i></button>';
                        html += '<button class="gallery-card-btn delete-btn" onclick="showDeleteConfirm(event, \'' + img.id + '\')" title="Delete"><i class="bi bi-trash"></i></button>';
                        html += '</div>';
                        html += '<div class="gallery-card-name">' + escapeHtml(img.filename) + '</div>';
                        html += '</div></div>';
                    }
                    grid.innerHTML = html;
                    renderPagination(data.page, data.total_pages);
                } else {
                    showEmptyState(grid);
                }
            } else {
                showEmptyState(grid);
            }
        } catch (e) {
            console.error('Parse error:', e);
            showEmptyState(grid);
        }
    };

    xhr.onerror = function () {
        console.error('Network error loading gallery');
        showEmptyState(grid);
    };

    xhr.ontimeout = function () {
        console.error('Timeout loading gallery');
        showEmptyState(grid);
    };

    xhr.send();
}

function showEmptyState(grid) {
    grid.innerHTML = '<div class="gallery-empty"><div class="gallery-empty-icon"><i class="bi bi-images"></i></div><div class="gallery-empty-title">No images yet</div><div class="gallery-empty-text">Upload images to build your gallery.</div></div>';
    var pagination = document.getElementById('galleryPagination');
    if (pagination) pagination.innerHTML = '';
}

// ==================== PAGINATION ====================

function renderPagination(page, totalPages) {
    const container = document.getElementById('galleryPagination');
    if (!container) return;
    if (totalPages <= 1) {
        container.innerHTML = '';
        return;
    }

    let html = '';
    html += '<button class="page-btn" ' + (page <= 1 ? 'disabled' : '') + ' onclick="goToPage(' + (page - 1) + ')"><i class="bi bi-chevron-left"></i></button>';

    for (let i = 1; i <= totalPages; i++) {
        html += '<button class="page-btn ' + (i === page ? 'active' : '') + '" onclick="goToPage(' + i + ')">' + i + '</button>';
    }

    html += '<button class="page-btn" ' + (page >= totalPages ? 'disabled' : '') + ' onclick="goToPage(' + (page + 1) + ')"><i class="bi bi-chevron-right"></i></button>';
    container.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    loadImages();
}

// ==================== COPY URL ====================

function copyUrl(event, url) {
    event.stopPropagation();
    navigator.clipboard.writeText(url).then(function() {
        showGalleryToast('URL copied to clipboard');
    }).catch(function() {
        showGalleryToast('Failed to copy URL');
    });
}

// ==================== DELETE ====================

function setupDeleteModal() {
    var btn = document.getElementById('confirmDeleteBtn');
    if (!btn) return;
    btn.addEventListener('click', function () {
        if (deleteImageId) deleteImage(deleteImageId);
    });
}

function showDeleteConfirm(event, id) {
    event.stopPropagation();
    deleteImageId = id;
    var btn = document.getElementById('confirmDeleteBtn');
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-trash"></i> Delete';
    document.getElementById('deleteOverlay').classList.add('active');
}

function closeDeleteModal(event) {
    if (event && event.target !== event.currentTarget) return;
    document.getElementById('deleteOverlay').classList.remove('active');
}

async function deleteImage(id) {
    var btn = document.getElementById('confirmDeleteBtn');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Deleting...';

    try {
        var res = await fetch('/api/gallery/images/' + id, { method: 'DELETE' });
        var data = await res.json();

        if (data.success) {
            closeDeleteModal();
            deleteImageId = null;
            loadImages();
            showGalleryToast('Image deleted');
        } else {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-trash"></i> Delete';
            showGalleryToast(data.error || 'Delete failed');
        }
    } catch (err) {
        console.error('Delete error:', err);
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-trash"></i> Delete';
    }
}

// ==================== TOAST ====================

function showGalleryToast(message) {
    var toast = document.querySelector('.gallery-toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'gallery-toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 2500);
}

// ==================== UTILITIES ====================

function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
