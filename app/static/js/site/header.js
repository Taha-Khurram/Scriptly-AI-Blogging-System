/**
 * Site Header JavaScript
 * Handles mobile menu, search, subscribe modal, and scroll effects
 */

document.addEventListener('DOMContentLoaded', function() {
    // ==================== HEADER SCROLL EFFECT ====================
    const header = document.querySelector('.site-header');

    if (header) {
        let lastScroll = window.pageYOffset;
        let ticking = false;

        function handleScroll() {
            const currentScroll = window.pageYOffset;

            // Glass/hairline state once scrolled past the top
            if (currentScroll > 40) {
                header.classList.add('scrolled');
            } else {
                header.classList.remove('scrolled');
            }

            // Auto-hide on scroll down, reveal on scroll up (skip near the top
            // and while any modal is open)
            const modalOpen = document.querySelector(
                '.subscribe-modal-overlay.active, .result-modal-overlay.active, .newsletter-modal-overlay.active'
            );
            if (!modalOpen && currentScroll > 140) {
                if (currentScroll > lastScroll + 6) {
                    header.classList.add('header-hidden');
                } else if (currentScroll < lastScroll - 6) {
                    header.classList.remove('header-hidden');
                }
            } else {
                header.classList.remove('header-hidden');
            }

            lastScroll = currentScroll;
            ticking = false;
        }

        window.addEventListener('scroll', function () {
            if (!ticking) { window.requestAnimationFrame(handleScroll); ticking = true; }
        }, { passive: true });
        handleScroll(); // Check initial state
    }

    // ==================== MOBILE MENU ====================
    const mobileToggle = document.querySelector('.mobile-menu-toggle');
    const mobileNav = document.querySelector('.mobile-nav');

    if (mobileToggle && mobileNav) {
        mobileToggle.addEventListener('click', function() {
            this.classList.toggle('active');
            mobileNav.classList.toggle('active');
        });

        // Close mobile menu when clicking a link
        document.querySelectorAll('.mobile-nav a').forEach(link => {
            link.addEventListener('click', () => {
                mobileToggle.classList.remove('active');
                mobileNav.classList.remove('active');
            });
        });
    }

    // ==================== SEARCH FUNCTIONALITY ====================
    const searchInputs = document.querySelectorAll('#header-search-input, .mobile-search input');

    searchInputs.forEach(input => {
        input.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                const query = this.value.trim();
                const searchUrl = this.dataset.searchUrl;
                if (query && searchUrl) {
                    window.location.href = `${searchUrl}?search=${encodeURIComponent(query)}`;
                }
            }
        });
    });

    // ==================== SUBSCRIBE MODAL ====================
    const subscribeModal = document.getElementById('subscribe-modal');
    const subscribeModalClose = document.getElementById('subscribe-modal-close');
    const headerSubscribeBtn = document.getElementById('header-subscribe-btn');
    const mobileSubscribeBtn = document.getElementById('mobile-subscribe-btn');
    const subscribeForm = document.getElementById('subscribe-modal-form');

    function openSubscribeModal() {
        if (subscribeModal) {
            subscribeModal.classList.add('active');
            document.body.style.overflow = 'hidden';
            // Focus the email input
            const emailInput = subscribeModal.querySelector('input[type="email"]');
            if (emailInput) {
                setTimeout(() => emailInput.focus(), 100);
            }
        }
    }

    function closeSubscribeModal() {
        if (subscribeModal) {
            subscribeModal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }

    // Open modal buttons
    if (headerSubscribeBtn) {
        headerSubscribeBtn.addEventListener('click', openSubscribeModal);
    }

    if (mobileSubscribeBtn) {
        mobileSubscribeBtn.addEventListener('click', function() {
            // Close mobile menu first
            if (mobileToggle) mobileToggle.classList.remove('active');
            if (mobileNav) mobileNav.classList.remove('active');
            openSubscribeModal();
        });
    }

    // Close modal
    if (subscribeModalClose) {
        subscribeModalClose.addEventListener('click', closeSubscribeModal);
    }

    // Close on overlay click
    if (subscribeModal) {
        subscribeModal.addEventListener('click', function(e) {
            if (e.target === this) closeSubscribeModal();
        });
    }

    // Close on Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && subscribeModal && subscribeModal.classList.contains('active')) {
            closeSubscribeModal();
        }
    });

    // Subscribe form submission
    if (subscribeForm) {
        subscribeForm.addEventListener('submit', async function(e) {
            e.preventDefault();
            const subscribeUrl = this.dataset.subscribeUrl;
            const emailInput = this.querySelector('input[type="email"]');
            const submitBtn = this.querySelector('button[type="submit"]');

            if (!subscribeUrl || !emailInput) return;

            const email = emailInput.value.trim();
            if (!email) return;

            // Disable button while submitting
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.textContent = 'Subscribing...';
            }

            try {
                const formData = new FormData();
                formData.append('email', email);

                const response = await fetch(subscribeUrl, {
                    method: 'POST',
                    body: formData
                });

                const data = await response.json();

                // Close subscribe modal first
                closeSubscribeModal();

                // Reset form
                emailInput.value = '';

                // Show appropriate result modal
                if (data.success) {
                    if (data.is_new) {
                        openResultModal('success-modal');
                    } else {
                        openResultModal('already-subscribed-modal');
                    }
                }
            } catch (error) {
                console.error('Subscription error:', error);
            } finally {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Subscribe';
                }
            }
        });
    }
});

// ==================== RESULT MODAL FUNCTIONS ====================
function openResultModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function closeResultModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

// Close result modals on overlay click
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('result-modal-overlay')) {
        e.target.classList.remove('active');
        document.body.style.overflow = '';
    }
});

// Close result modals on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.result-modal-overlay.active').forEach(modal => {
            modal.classList.remove('active');
        });
        document.body.style.overflow = '';
    }
});
