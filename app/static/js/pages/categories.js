/**
 * Categories Page JavaScript
 */

// Add Category
document.getElementById('addCategoryForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(e.target);
  const name = formData.get('name').trim();

  try {
    const res = await fetch('/api/categories', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'success',
        title: 'Category Created',
        message: `"${name}" has been added successfully.`,
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      showToast({
        type: 'error',
        title: 'Error',
        message: data.error || 'Failed to create category.',
        duration: 5000
      });
    }
  } catch (err) {
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

  try {
    const res = await fetch(`/api/edit_category/${id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'success',
        title: 'Category Updated',
        message: `Category renamed to "${name}".`,
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      showToast({
        type: 'error',
        title: 'Update Failed',
        message: data.error || 'Could not update category.',
        duration: 5000
      });
    }
  } catch (err) {
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
  try {
    const res = await fetch(`/api/delete_category/${id}`, {
      method: 'DELETE'
    });
    const data = await res.json();
    if (data.success) {
      showToast({
        type: 'warning',
        title: 'Category Deleted',
        message: 'The category has been removed.',
        duration: 3000
      });
      setTimeout(() => location.reload(), 1000);
    } else {
      showToast({
        type: 'error',
        title: 'Delete Failed',
        message: data.error || 'Could not delete category.',
        duration: 5000
      });
    }
  } catch (err) {
    console.error(err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: 'Failed to delete category.',
      duration: 5000
    });
  }
}

// Simple Search Filter
document.getElementById('searchInput').addEventListener('keyup', function () {
  const value = this.value.toLowerCase();
  document.querySelectorAll('.category-item').forEach(item => {
    const text = item.querySelector('.col-name').innerText.toLowerCase();
    item.style.setProperty('display', text.includes(value) ? 'flex' : 'none', 'important');
  });
});

// View Blogs in Category
async function viewCategoryBlogs(categoryId, categoryName) {
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
      const tbody = document.getElementById('blogsListBody');
      tbody.innerHTML = '';

      data.blogs.forEach(blog => {
        const status = (blog.status || 'DRAFT').toUpperCase();
        let statusBadge;
        if (status === 'PUBLISHED') {
          statusBadge = '<span class="badge bg-success-subtle text-success rounded-pill px-3">Published</span>';
        } else if (status === 'UNDER_REVIEW') {
          statusBadge = '<span class="badge bg-info-subtle text-info rounded-pill px-3">Under Review</span>';
        } else if (status === 'REJECTED') {
          statusBadge = '<span class="badge bg-danger-subtle text-danger rounded-pill px-3">Rejected</span>';
        } else {
          statusBadge = '<span class="badge bg-warning-subtle text-warning rounded-pill px-3">Draft</span>';
        }

        const createdAt = blog.created_at
          ? new Date(blog.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
          : '—';

        const row = document.createElement('tr');
        row.innerHTML = `
          <td class="fw-medium">${blog.title}</td>
          <td>${statusBadge}</td>
          <td class="text-muted small">${createdAt}</td>
        `;
        tbody.appendChild(row);
      });

      document.getElementById('blogsListContent').classList.remove('d-none');
      document.getElementById('blogsCount').textContent = `${data.count} blog${data.count !== 1 ? 's' : ''} found`;
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
