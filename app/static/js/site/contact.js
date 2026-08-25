/**
 * Contact Page JavaScript
 * Handles FAQ accordion, form submission, and scroll animations
 */

document.addEventListener('DOMContentLoaded', function() {
    // ==================== FAQ ACCORDION ====================
    // Note: FAQ now uses native <details>/<summary>; this legacy accordion
    // only runs for the old .faq-question markup if present.
    const faqItems = document.querySelectorAll('.faq-item');

    faqItems.forEach(item => {
        const question = item.querySelector('.faq-question');
        if (!question) return;

        question.addEventListener('click', () => {
            // Close other items
            faqItems.forEach(otherItem => {
                if (otherItem !== item && otherItem.classList.contains('active')) {
                    otherItem.classList.remove('active');
                }
            });

            // Toggle current item
            item.classList.toggle('active');
        });
    });

    // ==================== FORM SUBMISSION ====================
    const contactForm = document.getElementById('contact-form');
    const submitBtn = document.getElementById('submit-btn');

    if (contactForm && submitBtn) {
        contactForm.addEventListener('submit', function() {
            submitBtn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">hourglass_top</i> Sending...';
            submitBtn.disabled = true;
        });
    }

    // ==================== CTA NEWSLETTER FORM ====================
    const ctaForm = document.getElementById('cta-subscribe-form');

    if (ctaForm) {
        ctaForm.addEventListener('submit', function(e) {
            e.preventDefault();

            const email = this.querySelector('input[type="email"]').value;
            const btn = this.querySelector('button');
            const originalText = btn.innerHTML;

            if (!email || !email.includes('@')) {
                return;
            }

            btn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">hourglass_top</i>';
            btn.disabled = true;

            // Get the subscribe URL from data attribute
            const subscribeUrl = this.dataset.subscribeUrl;

            if (subscribeUrl) {
                fetch(subscribeUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: `email=${encodeURIComponent(email)}`
                })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        btn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">check</i> Subscribed!';
                        this.querySelector('input[type="email"]').value = '';
                        setTimeout(() => {
                            btn.innerHTML = originalText;
                            btn.disabled = false;
                        }, 3000);
                    } else {
                        btn.innerHTML = originalText;
                        btn.disabled = false;
                    }
                })
                .catch(() => {
                    btn.innerHTML = originalText;
                    btn.disabled = false;
                });
            }
        });
    }

    // ==================== SCROLL ANIMATIONS ====================
    const observerOptions = {
        threshold: 0.1,
        rootMargin: '0px 0px -50px 0px'
    };

    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);

    // Observe all fade-in elements
    document.querySelectorAll('.fade-in-up').forEach(el => {
        observer.observe(el);
    });

    // ==================== FAQ ITEMS STAGGER ANIMATION ====================
    faqItems.forEach((item, index) => {
        item.style.transitionDelay = `${index * 0.05}s`;
    });
});
