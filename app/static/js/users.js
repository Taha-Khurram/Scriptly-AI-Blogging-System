/**
 * User Management JS - Invitation-based system
 */

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function loadUsers() {
    const usersList = document.getElementById('usersList');
    const noUsers = document.getElementById('noUsers');

    if (!usersList) return;

    usersList.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border text-primary opacity-50 mb-3" role="status"></div>
            <p class="text-secondary fw-medium">Loading users...</p>
        </div>
    `;

    try {
        const response = await fetch('/users/list');
        if (!response.ok) throw new Error(`Server returned ${response.status}`);

        let data;
        try {
            data = await response.json();
        } catch (e) {
            throw new Error('Invalid response from server');
        }

        if (!data.success) throw new Error(data.error || "Failed to fetch users.");

        const users = data.users || [];
        const invitations = data.invitations || [];

        if (users.length === 0 && invitations.length === 0) {
            usersList.innerHTML = '';
            if (noUsers) noUsers.classList.remove('d-none');
            return;
        }

        if (noUsers) noUsers.classList.add('d-none');

        let html = '';

        // Render active users
        html += users.map(user => {
            const uid = user.uid || 'unknown';
            const name = (user.name || user.username || 'User').trim();
            const email = (user.email || '').trim();
            const role = (user.role || 'editor').toLowerCase().trim();
            const initials = (name === 'User' ? (email[0] || '?') : name.substring(0, 2)).toUpperCase();
            const roleClass = role === 'admin' ? 'badge-admin' : 'badge-editor';

            return `
                <div class="user-row animate-fade-up">
                    <div class="col-username d-flex align-items-center gap-3">
                        <div class="footer-avatar shadow-sm" style="width: 36px; height: 36px; font-size: 0.8rem; border-radius: 10px;">
                            ${initials}
                        </div>
                        <span class="fw-bold">${escapeHtml(name)}</span>
                    </div>
                    <div class="col-email text-secondary">${escapeHtml(email)}</div>
                    <div class="col-role">
                        <span class="badge-role ${roleClass}">${role.toUpperCase()}</span>
                    </div>
                    <div class="col-status">
                        <span class="badge-status badge-active"><i class="bi bi-check-circle-fill"></i> Active</span>
                    </div>
                    <div class="col-actions">
                        <button class="btn btn-sm btn-light rounded-circle shadow-sm"
                            onclick="openEditRole('${uid}', '${escapeHtml(name)}', '${role}')"
                            style="width: 32px; height: 32px; padding: 0;" title="Edit Role">
                            <i class="bi bi-pencil-square text-primary"></i>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        // Render pending invitations
        const pendingInvitations = invitations.filter(inv => inv.status === 'pending');
        html += pendingInvitations.map(inv => {
            const email = inv.email || '';
            const role = (inv.role || 'editor').toLowerCase().trim();
            const roleClass = role === 'admin' ? 'badge-admin' : 'badge-editor';
            const initial = email.charAt(0).toUpperCase();

            return `
                <div class="user-row animate-fade-up">
                    <div class="col-username d-flex align-items-center gap-3">
                        <div class="footer-avatar shadow-sm" style="width: 36px; height: 36px; font-size: 0.8rem; border-radius: 10px; opacity: 0.6;">
                            ${initial}
                        </div>
                        <span class="fw-bold text-muted">${escapeHtml(email.split('@')[0])}</span>
                    </div>
                    <div class="col-email text-secondary">${escapeHtml(email)}</div>
                    <div class="col-role">
                        <span class="badge-role ${roleClass}">${role.toUpperCase()}</span>
                    </div>
                    <div class="col-status">
                        <span class="badge-status badge-pending"><i class="bi bi-clock-fill"></i> Pending</span>
                    </div>
                    <div class="col-actions">
                        <button class="btn btn-sm btn-outline-primary rounded-pill px-3"
                            onclick="resendInvitation('${escapeHtml(email)}')" title="Resend Invitation">
                            <i class="bi bi-arrow-clockwise"></i> Resend
                        </button>
                    </div>
                </div>
            `;
        }).join('');

        usersList.innerHTML = html;

    } catch (error) {
        console.error('Error loading users:', error);
        usersList.innerHTML = `
            <div class="p-5 text-center bg-white rounded border">
                <div class="text-danger mb-2"><i class="bi bi-exclamation-triangle-fill fs-2"></i></div>
                <p class="text-secondary fw-bold">Failed to load users</p>
                <p class="small text-muted mb-3">${error.message}</p>
                <button class="btn btn-sm btn-primary rounded-pill px-4" onclick="loadUsers()">Retry</button>
            </div>
        `;
    }
}

function openEditRole(uid, username, currentRole) {
    document.getElementById('editUserId').value = uid;
    document.getElementById('editUsername').value = username;
    document.getElementById('editRole').value = currentRole.toLowerCase();

    const modalEl = document.getElementById('editRoleModal');
    const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
    modal.show();
}

async function resendInvitation(email) {
    showActionLoader('Sending...');
    try {
        const res = await fetch('/users/resend-invite', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email })
        });
        const data = await res.json();
        if (data.success) {
            hideActionLoader();
            showToast('Invitation resent to ' + email, 'success');
        } else {
            hideActionLoader();
            showToast(data.error || 'Failed to resend', 'error');
        }
    } catch (err) {
        hideActionLoader();
        showToast('Connection failed', 'error');
    }
}

function showToast(message, type) {
    const existing = document.querySelector('.toast-notification');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast-notification toast-${type}`;
    toast.innerHTML = `<i class="bi ${type === 'success' ? 'bi-check-circle-fill' : 'bi-exclamation-circle-fill'}"></i> ${escapeHtml(message)}`;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 300); }, 3000);
}

// --- Event Listeners ---
document.addEventListener('DOMContentLoaded', () => {
    loadUsers();

    const inviteForm = document.getElementById('inviteUserForm');
    const editRoleForm = document.getElementById('editRoleForm');

    if (inviteForm) {
        inviteForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const submitBtn = inviteForm.querySelector('button[type="submit"]');
            submitBtn.disabled = true;
            submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Sending...';

            showActionLoader('Sending...');
            try {
                const formData = Object.fromEntries(new FormData(inviteForm).entries());
                const res = await fetch('/users/invite', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(formData)
                });
                const data = await res.json();

                if (data.success) {
                    const modalEl = document.getElementById('inviteUserModal');
                    const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                    modal.hide();
                    inviteForm.reset();
                    loadUsers();
                    showToast(data.message || 'Invitation sent!', 'success');
                } else {
                    showToast(data.error || 'Failed to send invitation', 'error');
                }
            } catch (err) {
                showToast('Connection failed', 'error');
            } finally {
                hideActionLoader();
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i class="bi bi-send-fill"></i> Send Invitation';
            }
        });
    }

    if (editRoleForm) {
        editRoleForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const userId = document.getElementById('editUserId').value;
            const role = document.getElementById('editRole').value;

            showActionLoader('Updating...');
            try {
                const res = await fetch('/users/update-role', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ userId, role })
                });
                const data = await res.json();
                if (data.success) {
                    hideActionLoader();
                    const modalEl = document.getElementById('editRoleModal');
                    const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
                    modal.hide();
                    loadUsers();
                    showToast('Role updated', 'success');
                } else {
                    hideActionLoader();
                    showToast(data.error || 'Failed to update', 'error');
                }
            } catch (err) {
                hideActionLoader();
                showToast('Connection failed', 'error');
            }
        });
    }
});
