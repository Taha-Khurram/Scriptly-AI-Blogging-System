/**
 * Categories Page JavaScript
 */

// Bootstrap's dropdowns are NOT initialised here on purpose.
//
// This file used to open with `new bootstrap.Dropdown(el)` for every trigger.
// bootstrap.bundle is loaded with `defer`, so on a hard page load it has not
// executed yet when this script runs — the constructor threw
// "bootstrap is not defined" and took the whole file down with it, leaving the
// form handlers and the search unbound. (It only appeared to work when arriving
// via PJAX, which injects this script after Bootstrap is ready.)
//
// The loop was never needed: Bootstrap's data-api is a delegated listener on
// document, so `data-bs-toggle="dropdown"` works on markup added later. The
// drafts and all-blogs listings rely on exactly that.

// Add Category
document.getElementById('addCategoryForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const name = formData.get('name').trim();

  showActionLoader('Creating category...');
  try {
    const res = await fetch('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.success) {
      // Keep the loader visible through the reload below.
      showToast({
        type: 'success',
        title: 'Category Created',
        message: `"${name}" has been added successfully.`,
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      hideActionLoader();
      showToast({
        type: 'error',
        title: 'Error',
        message: data.error || 'Failed to create category.',
        duration: 5000
      });
    }
  } catch (err) {
    hideActionLoader();
    console.error(err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: 'Failed to add category.',
      duration: 5000
    });
  }
});

// Edit Category Modal Opener
function openEditModal(id, name) {
  closeAllDropdowns();
  document.getElementById('editCategoryId').value = id;
  document.getElementById('editCategoryName').value = name;
  const editModal = new bootstrap.Modal(document.getElementById('editCategoryModal'));
  editModal.show();
}

// Submit Edit
document.getElementById('editCategoryForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = document.getElementById('editCategoryId').value;
  const name = document.getElementById('editCategoryName').value.trim();

  showActionLoader('Updating category...');
  try {
    const res = await fetch(`/api/edit_category/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.success) {
      // Keep the loader visible through the reload below.
      showToast({
        type: 'success',
        title: 'Category Updated',
        message: `Category renamed to "${name}".`,
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      hideActionLoader();
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: data.error || 'Could not update category.',
        duration: 5000
      });
    }
  } catch (err) {
    hideActionLoader();
    console.error(err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: 'Failed to update category.',
      duration: 5000
    });
  }
});

// Delete Category
async function deleteCategory(id) {
  // Close the dropdown and show the shared loader (consistent async UX).
  closeAllDropdowns();
  showActionLoader('Deleting category...');
  try {
    const res = await fetch(`/api/delete_category/${id}`, {
      method: 'DELETE'
    });
    const data = await res.json();
    if (data.success) {
      // Keep the loader visible through the reload below.
      showToast({
        type: 'warning',
        title: 'Category Deleted',
        message: 'The category has been removed.',
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      hideActionLoader();
      showToast({
        type: 'error',
        title: 'Delete Failed',
        message: data.error || 'Could not delete category.',
        duration: 5000
      });
    }
  } catch (err) {
    hideActionLoader();
    console.error(err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: 'Failed to delete category.',
      duration: 5000
    });
  }
}

// --------------------------------------------------------------------------
// Header search
//
// Driven by the page header's `page-search` event rather than a keyup handler
// of its own, and it toggles the `hidden` attribute instead of writing
// `display: flex !important` inline — that old inline style fought the row's
// own grid layout and could not be undone by CSS.
//
// The listener goes through an AbortController the next run of this file
// aborts, because PJAX re-injects the script on every visit.
// --------------------------------------------------------------------------
(function categoriesSearch() {
  if (window.__categoriesAbort) {
    try { window.__categoriesAbort.abort(); } catch (e) { }
  }
  const controller = new AbortController();
  window.__categoriesAbort = controller;

  document.addEventListener('page-search', (e) => {
    const q = ((e.detail && e.detail.value) || '').trim().toLowerCase();
    const rows = document.querySelectorAll('#categoriesList .category-item');
    if (!rows.length) return;

    let shown = 0;
    rows.forEach((row) => {
      const hit = !q || (row.dataset.search || '').indexOf(q) !== -1;
      row.hidden = !hit;
      if (hit) shown++;
    });

    const none = document.querySelector('#categoriesList [data-noresults]');
    if (none) none.hidden = shown !== 0;
  }, { signal: controller.signal });

  // syncThemeControls only runs on DOMContentLoaded, which PJAX never fires.
  if (typeof window.syncThemeControls === 'function') window.syncThemeControls();
})();

function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// The shared status pill, so a blog carries the same badge here as on the
// listings. Bootstrap's *-subtle utilities carry Bootstrap's palette, not ours.
function statusPill(status) {
  const map = {
    DRAFT: 'Draft',
    UNDER_REVIEW: 'Under review',
    PUBLISHED: 'Published',
    REJECTED: 'Rejected'
  };
  const key = (status || 'DRAFT').toUpperCase();
  const label = map[key] || map.DRAFT;
  const cls = map[key] ? key.toLowerCase() : 'draft';
  return `<span class="status-pill status-${cls}">${label}</span>`;
}

// View Blogs in Category
async function viewCategoryBlogs(categoryId, categoryName) {
  closeAllDropdowns();
  const modal = new bootstrap.Modal(document.getElementById('viewBlogsModal'));
  document.getElementById('viewBlogsCategoryName').textContent = categoryName;
  document.getElementById('blogsListLoading').classList.remove('d-none');
  document.getElementById('blogsListEmpty').classList.add('d-none');
  document.getElementById('blogsListContent').classList.add('d-none');
  document.getElementById('blogsCount').textContent = '';
  modal.show();

  try {
    const res = await fetch(`/api/category/${categoryId}/blogs`);
    const data = await res.json();

    document.getElementById('blogsListLoading').classList.add('d-none');

    if (data.success && data.blogs.length > 0) {
      const body = document.getElementById('blogsListBody');

      // Rendered as the same .data-row the listings use, with the shared
      // status pill — a blog should not look like a different kind of thing
      // just because it is being viewed inside a modal.
      body.innerHTML = data.blogs.map((blog) => {
        const title = escapeHtml(blog.title || 'Untitled');
        const createdAt = blog.created_at
          ? new Date(blog.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
          : '—';

        return `
          <div class="data-row">
            <span class="row-title">${title}</span>
            ${statusPill(blog.status)}
            <span class="row-time">${createdAt}</span>
          </div>`;
      }).join('');

      document.getElementById('blogsListContent').classList.remove('d-none');
      document.getElementById('blogsCount').textContent = `${data.count} blog${data.count !== 1 ? 's' : ''}`;
    } else {
      document.getElementById('blogsListEmpty').classList.remove('d-none');
    }
  } catch (err) {
    console.error(err);
    document.getElementById('blogsListLoading').classList.add('d-none');
    document.getElementById('blogsListEmpty').classList.remove('d-none');
    showToast({
      type: 'error',
      title: 'Error',
      message: 'Failed to load blogs for this category.',
      duration: 5000
    });
  }
}
