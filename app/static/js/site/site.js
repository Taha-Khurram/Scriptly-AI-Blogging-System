/**
 * Public Site JavaScript
 * Handles mobile menu, newsletter modals, and form submissions
 */

// ==================== NEWSLETTER MODAL FUNCTIONS ====================

function showNewsletterModal() {
    document.getElementById('newsletter-modal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeNewsletterModal() {
    document.getElementById('newsletter-modal').classList.remove('active');
    document.body.style.overflow = '';
}

function showAlreadySubscribedModal() {
    document.getElementById('already-subscribed-modal').classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeAlreadySubscribedModal() {
    document.getElementById('already-subscribed-modal').classList.remove('active');
    document.body.style.overflow = '';
}

// ==================== NEWSLETTER FORM HANDLER ====================

async function handleNewsletterSubmit(form, subscribeUrl) {
    const email = form.querySelector('input[name="email"]').value;
    const button = form.querySelector('button');
    const originalHTML = button.innerHTML;
    const originalText = button.textContent;
    const isIconButton = button.innerHTML.includes('<i class="material-symbols-outlined icon-inline" ');

    // Show loading state
    if (isIconButton) {
        button.innerHTML = '<i aria-hidden="true">hourglass_top</i>';
    } else {
        button.textContent = 'Subscribing...';
    }
    button.disabled = true;

    try {
        const response = await fetch(subscribeUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: `email=${encodeURIComponent(email)}`
        });
        const data = await response.json();

        if (data.success) {
            form.querySelector('input').value = '';
            if (isIconButton) {
                button.innerHTML = originalHTML;
            } else {
                button.textContent = originalText;
            }
            button.disabled = false;

            if (data.is_new) {
                showNewsletterModal();
            } else {
                showAlreadySubscribedModal();
            }
        } else {
            showErrorState(button, isIconButton, originalHTML, originalText);
        }
    } catch (err) {
        showErrorState(button, isIconButton, originalHTML, originalText);
    }
}

function showErrorState(button, isIconButton, originalHTML, originalText) {
    if (isIconButton) {
        button.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">close</i>';
    } else {
        button.textContent = 'Failed';
    }
    setTimeout(() => {
        if (isIconButton) {
            button.innerHTML = originalHTML;
        } else {
            button.textContent = originalText;
        }
        button.disabled = false;
    }, 2000);
}

// ==================== INITIALIZATION ====================

document.addEventListener('DOMContentLoaded', function() {
    // Close modals on overlay click
    const newsletterModal = document.getElementById('newsletter-modal');
    const alreadySubscribedModal = document.getElementById('already-subscribed-modal');

    if (newsletterModal) {
        newsletterModal.addEventListener('click', function(e) {
            if (e.target === this) closeNewsletterModal();
        });
    }

    if (alreadySubscribedModal) {
        alreadySubscribedModal.addEventListener('click', function(e) {
            if (e.target === this) closeAlreadySubscribedModal();
        });
    }

    // Close modals on Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            closeNewsletterModal();
            closeAlreadySubscribedModal();
        }
    });
});
