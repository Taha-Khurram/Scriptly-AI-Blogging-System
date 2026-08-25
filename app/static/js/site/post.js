/**
 * Single Post Page JavaScript
 * Handles TOC highlighting, scroll animations, and interactions
 */

document.addEventListener('DOMContentLoaded', function() {
    // ==================== TOC ACTIVE STATE ====================
    const tocLinks = document.querySelectorAll('.toc-list a');
    const headings = [];

    // Collect all headings that have IDs
    tocLinks.forEach(link => {
        const href = link.getAttribute('href');
        if (href && href.startsWith('#')) {
            const heading = document.querySelector(href);
            if (heading) {
                headings.push({
                    element: heading,
                    link: link
                });
            }
        }
    });

    function updateTocActive() {
        if (headings.length === 0) return;

        const scrollPos = window.scrollY + 150;

        let activeIndex = 0;

        headings.forEach((item, index) => {
            if (item.element.offsetTop <= scrollPos) {
                activeIndex = index;
            }
        });

        tocLinks.forEach(link => link.classList.remove('active'));
        if (headings[activeIndex]) {
            headings[activeIndex].link.classList.add('active');
        }
    }

    if (headings.length > 0) {
        window.addEventListener('scroll', updateTocActive);
        updateTocActive();
    }

    // Smooth scroll for TOC links
    tocLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            const href = this.getAttribute('href');
            if (href && href.startsWith('#')) {
                e.preventDefault();
                const target = document.querySelector(href);
                if (target) {
                    const offset = 100;
                    const targetPosition = target.offsetTop - offset;
                    window.scrollTo({
                        top: targetPosition,
                        behavior: 'smooth'
                    });
                }
            }
        });
    });

    // ==================== SIDEBAR PROMO BUTTON ====================
    const sidebarPromoBtn = document.getElementById('sidebar-promo-btn');
    if (sidebarPromoBtn) {
        sidebarPromoBtn.addEventListener('click', function() {
            const subscribeModal = document.getElementById('subscribe-modal');
            if (subscribeModal) {
                subscribeModal.classList.add('active');
                document.body.style.overflow = 'hidden';
                const emailInput = subscribeModal.querySelector('input[type="email"]');
                if (emailInput) {
                    setTimeout(() => emailInput.focus(), 100);
                }
            }
        });
    }

    // ==================== SHARE BUTTON ====================
    const shareBtn = document.getElementById('post-share-btn');
    if (shareBtn && navigator.share) {
        shareBtn.addEventListener('click', async function() {
            try {
                await navigator.share({
                    title: document.title,
                    url: window.location.href
                });
            } catch (err) {
                console.log('Share cancelled');
            }
        });
    }

    // ==================== BOOKMARK BUTTON ====================
    // The glyph is the same ligature either way -- only the FILL axis moves,
    // which .icon-fill drives. Setting className alone would drop the icon
    // font classes and leave the ligature name showing as text.
    function setBookmarkGlyph(btn, filled) {
        const mark = btn.querySelector('i');
        if (!mark) return;
        mark.className = 'material-symbols-outlined icon-inline'
            + (filled ? ' icon-fill' : '');
        mark.textContent = 'bookmark';
        mark.setAttribute('aria-hidden', 'true');
    }

    const bookmarkBtn = document.getElementById('post-bookmark-btn');
    if (bookmarkBtn) {
        const postId = bookmarkBtn.dataset.postId;
        const bookmarkedPosts = JSON.parse(localStorage.getItem('bookmarkedPosts') || '[]');

        if (bookmarkedPosts.includes(postId)) {
            bookmarkBtn.classList.add('active');
            setBookmarkGlyph(bookmarkBtn, true);
        }

        bookmarkBtn.addEventListener('click', function() {
            const isBookmarked = bookmarkedPosts.includes(postId);

            if (isBookmarked) {
                const index = bookmarkedPosts.indexOf(postId);
                bookmarkedPosts.splice(index, 1);
                this.classList.remove('active');
                setBookmarkGlyph(this, false);
            } else {
                bookmarkedPosts.push(postId);
                this.classList.add('active');
                setBookmarkGlyph(this, true);
            }

            localStorage.setItem('bookmarkedPosts', JSON.stringify(bookmarkedPosts));
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

    document.querySelectorAll('.fade-in-up').forEach(el => {
        observer.observe(el);
    });

    // ==================== RELATED CARDS STAGGER ANIMATION ====================
    const relatedCards = document.querySelectorAll('.related-card');
    relatedCards.forEach((card, index) => {
        card.style.transitionDelay = `${index * 0.1}s`;
    });

    // ==================== CODE COPY BUTTON ====================
    document.querySelectorAll('.post-body pre').forEach(pre => {
        const copyBtn = document.createElement('button');
        copyBtn.className = 'code-copy-btn';
        copyBtn.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">content_copy</i>';
        copyBtn.title = 'Copy code';

        copyBtn.addEventListener('click', async function() {
            const code = pre.querySelector('code') || pre;
            try {
                await navigator.clipboard.writeText(code.textContent);
                this.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">check</i>';
                setTimeout(() => {
                    this.innerHTML = '<i class="material-symbols-outlined icon-inline" aria-hidden="true">content_copy</i>';
                }, 2000);
            } catch (err) {
                console.error('Failed to copy');
            }
        });

        pre.style.position = 'relative';
        pre.appendChild(copyBtn);
    });

    // Add code copy button styles dynamically
    const style = document.createElement('style');
    style.textContent = `
        .code-copy-btn {
            position: absolute;
            top: 0.75rem;
            right: 0.75rem;
            width: 32px;
            height: 32px;
            background: rgba(255, 255, 255, 0.1);
            border: none;
            border-radius: 6px;
            color: #94a3b8;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            transition: all 0.2s ease;
        }
        .post-body pre:hover .code-copy-btn {
            opacity: 1;
        }
        .code-copy-btn:hover {
            background: rgba(255, 255, 255, 0.2);
            color: white;
        }
    `;
    document.head.appendChild(style);

    // ==================== READING PROGRESS ====================
    const progressBar = document.getElementById('reading-progress');
    if (progressBar) {
        window.addEventListener('scroll', function() {
            const docHeight = document.documentElement.scrollHeight - window.innerHeight;
            const scrolled = (window.scrollY / docHeight) * 100;
            progressBar.style.width = `${Math.min(scrolled, 100)}%`;
        });
    }
});
