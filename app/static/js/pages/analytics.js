(function() {
    var currentPeriod = '7';
    var realtimeInterval = null;

    // ==================== PROPERTY SELECTION ====================

    var propertyList = document.getElementById('propertyList');
    if (propertyList) {
        loadProperties();
    }

    function loadProperties() {
        var controller = new AbortController();
        var timeoutId = setTimeout(function() { controller.abort(); }, 20000);

        fetch('/analytics/properties', { signal: controller.signal })
            .then(function(r) {
                clearTimeout(timeoutId);
                return r.json();
            })
            .then(function(data) {
                if (data.error) {
                    propertyList.innerHTML = '<p class="text-danger small text-center">' + data.error + '</p>';
                    return;
                }
                if (!data.properties || data.properties.length === 0) {
                    propertyList.innerHTML = '<p class="text-secondary small text-center">No GA4 properties found.</p>';
                    return;
                }
                var html = '';
                data.properties.forEach(function(prop) {
                    html += '<div class="property-item" onclick="selectProperty(\'' +
                        prop.property_id.replace(/'/g, "\\'") + '\', \'' +
                        prop.display_name.replace(/'/g, "\\'") + '\')">' +
                        '<div class="property-item-info">' +
                            '<span class="property-item-name">' + escapeHtml(prop.display_name) + '</span>' +
                            '<span class="property-item-account">' + escapeHtml(prop.account_name) + '</span>' +
                        '</div>' +
                        '<i class="bi bi-chevron-right"></i>' +
                    '</div>';
                });
                propertyList.innerHTML = html;
            })
            .catch(function(err) {
                clearTimeout(timeoutId);
                if (err.name === 'AbortError') {
                    propertyList.innerHTML = '<p class="text-danger small text-center">Request timed out. Make sure the Google Analytics Admin API is enabled in your Google Cloud project.</p>';
                } else {
                    propertyList.innerHTML = '<p class="text-danger small text-center">Failed to load properties. Check your console for errors.</p>';
                }
            });
    }

    window.selectProperty = function(propertyId, propertyName) {
        fetch('/analytics/select-property', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ property_id: propertyId, property_name: propertyName })
        })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                var msg = '';
                if (data.measurement_id && data.domain) {
                    msg = 'Auto-linked: ' + data.measurement_id + ' \u2022 ' + data.domain;
                } else if (data.measurement_id) {
                    msg = 'Tracking linked: ' + data.measurement_id;
                }
                if (msg) showAutoLinkToast(msg);
                window.location.reload();
            }
        });
    };

    // ==================== DASHBOARD DATA ====================

    var realtimeCount = document.getElementById('realtimeCount');
    if (realtimeCount) {
        initDashboard();
    }

    function initDashboard() {
        fetchAllData();
        realtimeInterval = setInterval(fetchRealtime, 30000);

        document.querySelectorAll('.period-btn').forEach(function(btn) {
            btn.addEventListener('click', function() {
                document.querySelectorAll('.period-btn').forEach(function(b) { b.classList.remove('active'); });
                btn.classList.add('active');
                currentPeriod = btn.getAttribute('data-period');
                fetchPeriodData();
            });
        });
    }

    function fetchAllData() {
        fetchRealtime();
        fetchPeriodData();
    }

    function fetchPeriodData() {
        fetchOverview();
        fetchTopPages();
        fetchTrafficSources();
    }

    function handleReconnect(data) {
        if (data.reconnect) {
            if (realtimeInterval) { clearInterval(realtimeInterval); realtimeInterval = null; }
            var wrapper = document.querySelector('.analytics-dashboard') || document.querySelector('.dashboard-content-wrapper');
            if (wrapper) {
                wrapper.innerHTML = '<div class="analytics-reconnect-banner">' +
                    '<i class="bi bi-exclamation-triangle-fill"></i>' +
                    '<div><strong>Google Analytics disconnected</strong>' +
                    '<p>Your session has expired. Please reconnect to continue viewing analytics.</p></div>' +
                    '<a href="/analytics/connect" class="btn-reconnect">Reconnect</a>' +
                '</div>';
            }
            return true;
        }
        return false;
    }

    function fetchRealtime() {
        fetch('/api/analytics/realtime')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (handleReconnect(data)) return;
                if (data.success) {
                    document.getElementById('realtimeCount').textContent = formatNumber(data.active_users);
                }
            })
            .catch(function() {});
    }

    function fetchOverview() {
        fetch('/api/analytics/overview?period=' + currentPeriod)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (handleReconnect(data)) return;
                if (data.success) {
                    var d = data.data;
                    document.getElementById('pageViews').textContent = formatNumber(d.page_views);
                    document.getElementById('sessions').textContent = formatNumber(d.sessions);
                    document.getElementById('totalUsers').textContent = formatNumber(d.users);
                    document.getElementById('avgDuration').textContent = formatDuration(d.avg_duration);
                    document.getElementById('bounceRate').textContent = d.bounce_rate + '%';
                }
            })
            .catch(function() {});
    }

    function fetchTopPages() {
        var body = document.getElementById('topPagesBody');
        fetch('/api/analytics/top-pages?period=' + currentPeriod)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (handleReconnect(data)) return;
                if (!data.success || !data.pages || data.pages.length === 0) {
                    body.innerHTML = '<div class="analytics-empty"><i class="bi bi-file-text"></i><p>No page data yet</p></div>';
                    return;
                }
                var html = '<div class="analytics-table-header">' +
                    '<span class="col-page-path">Page</span>' +
                    '<span class="col-page-views">Views</span>' +
                    '<span class="col-page-time">Avg Time</span>' +
                '</div>';
                data.pages.forEach(function(page) {
                    html += '<div class="analytics-table-row">' +
                        '<span class="col-page-path" title="' + escapeHtml(page.path) + '">' + escapeHtml(page.path) + '</span>' +
                        '<span class="col-page-views">' + formatNumber(page.views) + '</span>' +
                        '<span class="col-page-time">' + formatDuration(page.avg_time) + '</span>' +
                    '</div>';
                });
                body.innerHTML = html;
            })
            .catch(function() {
                body.innerHTML = '<div class="analytics-empty"><i class="bi bi-exclamation-triangle"></i><p>Failed to load</p></div>';
            });
    }

    function fetchTrafficSources() {
        var body = document.getElementById('trafficSourcesBody');
        fetch('/api/analytics/traffic-sources?period=' + currentPeriod)
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (handleReconnect(data)) return;
                if (!data.success || !data.sources || data.sources.length === 0) {
                    body.innerHTML = '<div class="analytics-empty"><i class="bi bi-signpost-split"></i><p>No traffic data yet</p></div>';
                    return;
                }
                var html = '<div class="analytics-table-header">' +
                    '<span class="col-source-name">Channel</span>' +
                    '<span class="col-source-sessions">Sessions</span>' +
                    '<span class="col-source-users">Users</span>' +
                '</div>';
                data.sources.forEach(function(src) {
                    html += '<div class="analytics-table-row">' +
                        '<span class="col-source-name">' + escapeHtml(src.channel) + '</span>' +
                        '<span class="col-source-sessions">' + formatNumber(src.sessions) + '</span>' +
                        '<span class="col-source-users">' + formatNumber(src.users) + '</span>' +
                    '</div>';
                });
                body.innerHTML = html;
            })
            .catch(function() {
                body.innerHTML = '<div class="analytics-empty"><i class="bi bi-exclamation-triangle"></i><p>Failed to load</p></div>';
            });
    }

    // ==================== DISCONNECT ====================

    window.disconnectAnalytics = function() {
        document.getElementById('disconnectModal').style.display = 'flex';
    };

    window.closeDisconnectModal = function() {
        document.getElementById('disconnectModal').style.display = 'none';
    };

    window.confirmDisconnect = function() {
        fetch('/analytics/disconnect', { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    window.location.reload();
                }
            });
    };

    // ==================== HELPERS ====================

    function formatNumber(n) {
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return String(n);
    }

    function formatDuration(seconds) {
        if (!seconds || seconds < 1) return '0s';
        var m = Math.floor(seconds / 60);
        var s = Math.round(seconds % 60);
        if (m > 0) return m + 'm ' + s + 's';
        return s + 's';
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function showAutoLinkToast(message) {
        var toast = document.createElement('div');
        toast.className = 'analytics-toast';
        toast.innerHTML = '<i class="bi bi-check-circle-fill"></i> ' + escapeHtml(message);
        document.body.appendChild(toast);
        setTimeout(function() { toast.classList.add('show'); }, 10);
        setTimeout(function() {
            toast.classList.remove('show');
            setTimeout(function() { toast.remove(); }, 300);
        }, 3000);
    }
})();
