(function() {
    // Tab switching
    var tabs = document.querySelectorAll('.opt-tab');
    var tabContents = document.querySelectorAll('.opt-tab-content');

    tabs.forEach(function(tab) {
        tab.addEventListener('click', function() {
            tabs.forEach(function(t) { t.classList.remove('active'); });
            tabContents.forEach(function(c) { c.classList.remove('active'); });
            tab.classList.add('active');
            var target = tab.getAttribute('data-tab');
            if (target === 'url-metrics') {
                document.getElementById('tabUrlMetrics').classList.add('active');
            } else if (target === 'keyword-research') {
                document.getElementById('tabKeywordResearch').classList.add('active');
            } else if (target === 'site-audit') {
                document.getElementById('tabSiteAudit').classList.add('active');
            } else if (target === 'auto-optimize') {
                document.getElementById('tabAutoOptimize').classList.add('active');
                loadOptimizeDrafts();
            } else if (target === 'reports') {
                document.getElementById('tabReports').classList.add('active');
                loadReports();
            }
        });
    });

    // ========== URL METRICS ==========
    var urlInput = document.getElementById('urlInput');
    var urlResultsSection = document.getElementById('urlResultsSection');
    var urlEmptyState = document.getElementById('urlEmptyState');
    var urlMetricsGrid = document.getElementById('urlMetricsGrid');
    var urlDetailsGrid = document.getElementById('urlDetailsGrid');
    var analyzedUrlText = document.getElementById('analyzedUrlText');
    var analyzeUrlBtn = document.getElementById('analyzeUrlBtn');

    urlInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') analyzeUrl();
    });

    window.analyzeUrl = async function() {
        var url = urlInput.value.trim();
        if (!url) {
            showToast({ type: 'warning', title: 'Missing URL', message: 'Please enter a URL to analyze.' });
            urlInput.focus();
            return;
        }

        analyzeUrlBtn.disabled = true;
        analyzeUrlBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Analyzing...';
        urlEmptyState.style.display = 'none';
        urlResultsSection.classList.remove('show');

        try {
            var response = await fetch('/api/optimization/url-metrics?url=' + encodeURIComponent(url));
            var result = await response.json();

            if (!response.ok || !result.success) {
                showToast({ type: 'error', title: 'Analysis Failed', message: result.error || 'Unable to fetch metrics.' });
                urlEmptyState.style.display = 'block';
                return;
            }

            renderUrlResults(url, result.data);

        } catch (err) {
            showToast({ type: 'error', title: 'Connection Error', message: 'Failed to connect. Please try again.' });
            urlEmptyState.style.display = 'block';
        } finally {
            analyzeUrlBtn.disabled = false;
            analyzeUrlBtn.innerHTML = '<i class="bi bi-search"></i> Analyze';
        }
    };

    var urlMetricDefs = [
        { key: 'domainRating', altKeys: ['domain_rating', 'dr'], label: 'Domain Rating', icon: 'bi-award-fill', color: 'purple' },
        { key: 'backlinks', altKeys: ['total_backlinks', 'backlink'], label: 'Backlinks', icon: 'bi-link-45deg', color: 'green' },
        { key: 'refDomains', altKeys: ['referring_domains', 'ref_domains'], label: 'Referring Domains', icon: 'bi-diagram-3-fill', color: 'blue' },
        { key: 'traffic', altKeys: ['organic_traffic', 'org_traffic'], label: 'Organic Traffic', icon: 'bi-graph-up-arrow', color: 'orange' },
        { key: 'organicKeywords', altKeys: ['organic_keywords', 'keywords'], label: 'Organic Keywords', icon: 'bi-key-fill', color: 'pink' },
        { key: 'urlRating', altKeys: ['url_rating', 'ur'], label: 'URL Rating', icon: 'bi-shield-check', color: 'teal' }
    ];

    function formatNumber(num) {
        if (num === null || num === undefined || num === '') return 'N/A';
        num = Number(num);
        if (isNaN(num)) return 'N/A';
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toLocaleString();
    }

    function escapeHtml(text) {
        if (!text) return '';
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function getMetricValue(data, def) {
        var val = data[def.key];
        if (val !== undefined && val !== null) return val;
        for (var i = 0; i < def.altKeys.length; i++) {
            val = data[def.altKeys[i]];
            if (val !== undefined && val !== null) return val;
        }
        return null;
    }

    function renderUrlResults(url, data) {
        analyzedUrlText.textContent = url;

        var metricsHtml = '';
        urlMetricDefs.forEach(function(def) {
            var val = getMetricValue(data, def);
            metricsHtml += '<div class="metric-card">' +
                '<div class="metric-icon ' + def.color + '"><i class="bi ' + def.icon + '"></i></div>' +
                '<div class="metric-info">' +
                    '<div class="metric-label">' + def.label + '</div>' +
                    '<div class="metric-value">' + formatNumber(val) + '</div>' +
                '</div></div>';
        });
        urlMetricsGrid.innerHTML = metricsHtml;

        var detailsHtml = '';
        var skipKeys = {};
        urlMetricDefs.forEach(function(def) {
            skipKeys[def.key] = true;
            def.altKeys.forEach(function(k) { skipKeys[k] = true; });
        });

        Object.keys(data).forEach(function(key) {
            if (skipKeys[key]) return;
            var val = data[key];
            if (val === null || val === undefined || val === '') return;
            if (typeof val === 'object') return;
            var label = key.replace(/_/g, ' ').replace(/([A-Z])/g, ' $1').replace(/^./, function(s) { return s.toUpperCase(); });
            detailsHtml += '<div class="detail-row">' +
                '<span class="detail-label">' + escapeHtml(label) + '</span>' +
                '<span class="detail-value">' + escapeHtml(String(val)) + '</span>' +
                '</div>';
        });

        if (detailsHtml) {
            urlDetailsGrid.innerHTML = detailsHtml;
            document.getElementById('urlDetailsCard').style.display = 'block';
        } else {
            document.getElementById('urlDetailsCard').style.display = 'none';
        }

        urlResultsSection.classList.add('show');
    }

    // ========== KEYWORD RESEARCH ==========
    var draftSelect = document.getElementById('draftSelect');
    var countrySelect = document.getElementById('countrySelect');
    var kwResultsSection = document.getElementById('kwResultsSection');
    var kwEmptyState = document.getElementById('kwEmptyState');
    var kwResultsBody = document.getElementById('kwResultsBody');
    var analyzedKeywordText = document.getElementById('analyzedKeywordText');
    var analyzeKeywordBtn = document.getElementById('analyzeKeywordBtn');
    var draftsLoaded = false;

    function loadDrafts() {
        if (draftsLoaded) return;
        fetch('/api/seo/drafts')
            .then(function(r) { return r.json(); })
            .then(function(result) {
                if (result.success && result.drafts) {
                    draftSelect.innerHTML = '<option value="">-- Select a draft --</option>';
                    result.drafts.forEach(function(d) {
                        var opt = document.createElement('option');
                        opt.value = d.id;
                        opt.textContent = d.title || 'Untitled';
                        draftSelect.appendChild(opt);
                    });
                    draftsLoaded = true;
                }
            })
            .catch(function() {});
    }

    // Load drafts when keyword tab is clicked
    tabs.forEach(function(tab) {
        tab.addEventListener('click', function() {
            if (tab.getAttribute('data-tab') === 'keyword-research') {
                loadDrafts();
            }
        });
    });

    window.analyzeKeyword = async function() {
        var blogId = draftSelect.value;
        if (!blogId) {
            showToast({ type: 'warning', title: 'No Draft Selected', message: 'Please select a draft blog to analyze.' });
            draftSelect.focus();
            return;
        }

        var country = countrySelect.value;
        analyzeKeywordBtn.disabled = true;
        analyzeKeywordBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Analyzing...';
        kwEmptyState.style.display = 'none';
        kwResultsSection.classList.remove('show');

        try {
            var response = await fetch('/api/optimization/draft-keywords', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ blog_id: blogId, country: country })
            });
            var result = await response.json();

            if (!response.ok || !result.success) {
                showToast({ type: 'error', title: 'Research Failed', message: result.error || 'Unable to analyze draft.' });
                kwEmptyState.style.display = 'block';
                return;
            }

            renderKeywordResults(result.data);

        } catch (err) {
            showToast({ type: 'error', title: 'Connection Error', message: 'Failed to connect. Please try again.' });
            kwEmptyState.style.display = 'block';
        } finally {
            analyzeKeywordBtn.disabled = false;
            analyzeKeywordBtn.innerHTML = '<i class="bi bi-search"></i> Research';
        }
    };

    function formatKwNum(num) {
        if (num === null || num === undefined) return '-';
        num = Number(num);
        if (isNaN(num)) return '-';
        if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
        if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
        return num.toLocaleString();
    }

    function getDifficultyClass(val) {
        if (val === null || val === undefined) return '';
        val = Number(val);
        if (val >= 70) return 'difficulty-hard';
        if (val >= 40) return 'difficulty-medium';
        return 'difficulty-easy';
    }

    function renderKeywordResults(data) {
        analyzedKeywordText.textContent = data.blog_title || 'Draft Analysis';

        var html = '';
        data.keywords.forEach(function(kw) {
            if (kw.error) {
                html += '<tr class="kw-row-error"><td>' + escapeHtml(kw.keyword) + '</td><td colspan="5">Failed to fetch metrics</td></tr>';
                return;
            }
            var diff = kw.difficulty;
            var diffClass = getDifficultyClass(diff);
            html += '<tr>' +
                '<td class="kw-cell-keyword">' + escapeHtml(kw.keyword || '') + '</td>' +
                '<td>' + formatKwNum(kw.searchVolume) + '</td>' +
                '<td><span class="difficulty-badge ' + diffClass + '">' + (diff !== null && diff !== undefined ? diff + '/100' : '-') + '</span></td>' +
                '<td>' + (kw.cpc !== null && kw.cpc !== undefined ? '$' + Number(kw.cpc).toFixed(2) : '-') + '</td>' +
                '<td>' + formatKwNum(kw.clicks) + '</td>' +
                '<td>' + formatKwNum(kw.trafficPotential) + '</td>' +
                '</tr>';
        });
        kwResultsBody.innerHTML = html;
        kwResultsSection.classList.add('show');
    }

    // ========== SITE AUDIT ==========
    var auditDomainInput = document.getElementById('auditDomainInput');
    var auditResultsSection = document.getElementById('auditResultsSection');
    var auditEmptyState = document.getElementById('auditEmptyState');
    var auditResultsHead = document.getElementById('auditResultsHead');
    var auditResultsBody = document.getElementById('auditResultsBody');
    var auditedDomainText = document.getElementById('auditedDomainText');
    var auditBtn = document.getElementById('auditBtn');
    var auditDetailsCard = document.getElementById('auditDetailsCard');
    var auditDetailsGrid = document.getElementById('auditDetailsGrid');

    auditDomainInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') runSiteAudit();
    });

    window.runSiteAudit = async function() {
        var domain = auditDomainInput.value.trim();
        if (!domain) {
            showToast({ type: 'warning', title: 'Missing Domain', message: 'Please enter a domain to audit.' });
            auditDomainInput.focus();
            return;
        }

        auditBtn.disabled = true;
        auditBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Auditing...';
        auditEmptyState.style.display = 'none';
        auditResultsSection.classList.remove('show');

        try {
            var response = await fetch('/api/optimization/site-audit?domain=' + encodeURIComponent(domain));
            var result = await response.json();

            if (!response.ok || !result.success) {
                showToast({ type: 'error', title: 'Audit Failed', message: result.error || 'Unable to audit this domain.' });
                auditEmptyState.style.display = 'block';
                return;
            }

            renderAuditResults(domain, result.data);

        } catch (err) {
            showToast({ type: 'error', title: 'Connection Error', message: 'Failed to connect. Please try again.' });
            auditEmptyState.style.display = 'block';
        } finally {
            auditBtn.disabled = false;
            auditBtn.innerHTML = '<i class="bi bi-shield-check"></i> Audit';
        }
    };

    function renderAuditResults(domain, data) {
        auditedDomainText.textContent = domain;

        // Handle array of keywords (expected response format)
        if (Array.isArray(data)) {
            renderAuditTable(data);
            auditDetailsCard.style.display = 'none';
            auditResultsSection.classList.add('show');
            return;
        }

        // Handle object with nested array (e.g., { keywords: [...], ... })
        if (typeof data === 'object' && data !== null) {
            var arrayKey = null;
            var extraFields = {};
            Object.keys(data).forEach(function(key) {
                if (Array.isArray(data[key]) && data[key].length > 0 && typeof data[key][0] === 'object') {
                    arrayKey = key;
                } else if (typeof data[key] !== 'object') {
                    extraFields[key] = data[key];
                }
            });

            if (arrayKey) {
                renderAuditTable(data[arrayKey]);
            } else {
                // No array found — display all fields as detail rows
                auditResultsHead.innerHTML = '';
                auditResultsBody.innerHTML = '';
                renderAuditDetails(data);
                auditResultsSection.classList.add('show');
                return;
            }

            // Show extra top-level fields
            if (Object.keys(extraFields).length > 0) {
                renderAuditDetails(extraFields);
                auditDetailsCard.style.display = 'block';
            } else {
                auditDetailsCard.style.display = 'none';
            }

            auditResultsSection.classList.add('show');
            return;
        }

        // Fallback: unexpected format
        showToast({ type: 'warning', title: 'Unexpected Data', message: 'The API returned an unexpected format.' });
        auditEmptyState.style.display = 'block';
    }

    function renderAuditTable(rows) {
        if (!rows || rows.length === 0) {
            auditResultsHead.innerHTML = '';
            auditResultsBody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:#94a3b8;">No keywords found for this domain.</td></tr>';
            return;
        }

        // Build table headers from first row keys
        var keys = Object.keys(rows[0]);
        var headHtml = '<tr>';
        keys.forEach(function(key) {
            var label = key.replace(/_/g, ' ').replace(/([A-Z])/g, ' $1').replace(/^./, function(s) { return s.toUpperCase(); });
            headHtml += '<th>' + escapeHtml(label) + '</th>';
        });
        headHtml += '</tr>';
        auditResultsHead.innerHTML = headHtml;

        // Build table body
        var bodyHtml = '';
        rows.forEach(function(row) {
            bodyHtml += '<tr>';
            keys.forEach(function(key, idx) {
                var val = row[key];
                if (val === null || val === undefined) val = '-';
                var cellClass = idx === 0 ? ' class="kw-cell-keyword"' : '';
                bodyHtml += '<td' + cellClass + '>' + escapeHtml(String(val)) + '</td>';
            });
            bodyHtml += '</tr>';
        });
        auditResultsBody.innerHTML = bodyHtml;
    }

    function renderAuditDetails(obj) {
        var html = '';
        Object.keys(obj).forEach(function(key) {
            var val = obj[key];
            if (val === null || val === undefined || val === '') return;
            if (typeof val === 'object') return;
            var label = key.replace(/_/g, ' ').replace(/([A-Z])/g, ' $1').replace(/^./, function(s) { return s.toUpperCase(); });
            html += '<div class="detail-row">' +
                '<span class="detail-label">' + escapeHtml(label) + '</span>' +
                '<span class="detail-value">' + escapeHtml(String(val)) + '</span>' +
                '</div>';
        });
        auditDetailsGrid.innerHTML = html;
        if (html) {
            auditDetailsCard.style.display = 'block';
        }
    }

    // ========== AUTO OPTIMIZE ==========
    var optimizeDraftSelect = document.getElementById('optimizeDraftSelect');
    var optimizeRegionSelect = document.getElementById('optimizeRegionSelect');
    var optimizeBtn = document.getElementById('optimizeBtn');
    var optimizeLoading = document.getElementById('optimizeLoading');
    var optimizeResultsSection = document.getElementById('optimizeResultsSection');
    var optimizeEmptyState = document.getElementById('optimizeEmptyState');
    var optimizeDraftsLoaded = false;
    var lastOptimizeData = null;

    function loadOptimizeDrafts() {
        if (optimizeDraftsLoaded) return;
        fetch('/api/seo/drafts')
            .then(function(r) { return r.json(); })
            .then(function(result) {
                if (result.success && result.drafts) {
                    optimizeDraftSelect.innerHTML = '<option value="">-- Select a draft --</option>';
                    result.drafts.forEach(function(d) {
                        var opt = document.createElement('option');
                        opt.value = d.id;
                        opt.textContent = d.title || 'Untitled';
                        optimizeDraftSelect.appendChild(opt);
                    });
                    optimizeDraftsLoaded = true;
                }
            })
            .catch(function() {});
    }

    window.runAutoOptimize = async function() {
        var blogId = optimizeDraftSelect.value;
        if (!blogId) {
            showToast({ type: 'warning', title: 'No Draft Selected', message: 'Please select a draft blog to optimize.' });
            optimizeDraftSelect.focus();
            return;
        }

        var region = optimizeRegionSelect.value;
        optimizeBtn.disabled = true;
        optimizeBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Optimizing...';
        optimizeEmptyState.style.display = 'none';
        optimizeResultsSection.classList.remove('show');
        optimizeLoading.style.display = 'flex';

        try {
            var response = await fetch('/api/seo/optimize-blog/' + blogId, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ region: region })
            });
            var result = await response.json();

            if (!response.ok || !result.success) {
                showToast({ type: 'error', title: 'Optimization Failed', message: result.error || 'Unable to optimize this blog.' });
                optimizeEmptyState.style.display = 'block';
                return;
            }

            renderOptimizeResults(result);

        } catch (err) {
            showToast({ type: 'error', title: 'Connection Error', message: 'Failed to connect. Please try again.' });
            optimizeEmptyState.style.display = 'block';
        } finally {
            optimizeBtn.disabled = false;
            optimizeBtn.innerHTML = '<i class="bi bi-magic"></i> Optimize';
            optimizeLoading.style.display = 'none';
        }
    };

    function renderOptimizeResults(data) {
        lastOptimizeData = data;

        var originalScore = data.original_score || 0;
        var newScore = data.seo_score || 0;
        var improvement = data.score_improvement || (newScore - originalScore);

        document.getElementById('optimizeScoreBefore').textContent = Math.round(originalScore);
        document.getElementById('optimizeScoreAfter').textContent = Math.round(newScore);
        document.getElementById('optimizeScoreImprovement').textContent = '+' + Math.round(improvement);

        document.getElementById('optimizeNewTitle').textContent = data.new_title || 'N/A';
        document.getElementById('optimizeGrade').textContent = data.seo_grade || 'N/A';

        var primaryKw = data.primary_keyword;
        if (primaryKw && typeof primaryKw === 'object') {
            document.getElementById('optimizePrimaryKw').textContent = primaryKw.keyword || primaryKw.term || 'N/A';
        } else {
            document.getElementById('optimizePrimaryKw').textContent = primaryKw || 'N/A';
        }

        var changesList = document.getElementById('optimizeChangesList');
        var changes = data.changes_made || [];
        if (changes.length > 0) {
            var changesHtml = '';
            changes.forEach(function(change) {
                var text = typeof change === 'object' ? (change.description || JSON.stringify(change)) : String(change);
                changesHtml += '<li>' + escapeHtml(text) + '</li>';
            });
            changesList.innerHTML = changesHtml;
            document.getElementById('optimizeChangesCard').style.display = 'block';
        } else {
            document.getElementById('optimizeChangesCard').style.display = 'none';
        }

        // Render suggestions
        var suggestionsList = document.getElementById('optimizeSuggestionsList');
        var suggestionsCard = document.getElementById('optimizeSuggestionsCard');
        var recommendations = data.recommendations || [];
        if (recommendations.length > 0) {
            var sugHtml = '';
            recommendations.forEach(function(rec) {
                sugHtml += '<li>' + escapeHtml(String(rec)) + '</li>';
            });
            suggestionsList.innerHTML = sugHtml;
            suggestionsCard.style.display = 'block';
        } else {
            suggestionsCard.style.display = 'none';
        }

        optimizeResultsSection.classList.add('show');
        showToast({ type: 'success', title: 'Optimization Complete', message: 'Your blog has been optimized and saved!' });
        loadReports();
    }

    // ========== EXPORT HTML REPORT ==========
    window.exportOptimizeReport = function() {
        if (!lastOptimizeData) return;
        generateAndDownloadReport(lastOptimizeData);
    };

    // ========== REPORTS TAB ==========
    var reportsCache = [];

    async function loadReports() {
        var listEl = document.getElementById('reportsList');
        var emptyEl = document.getElementById('reportsEmptyState');
        if (!listEl) return;

        try {
            var response = await fetch('/api/optimization/reports');
            var result = await response.json();
            if (!result.success) {
                listEl.innerHTML = '';
                emptyEl.style.display = 'block';
                return;
            }
            reportsCache = result.reports || [];
            renderReportsList();
        } catch (err) {
            listEl.innerHTML = '';
            emptyEl.style.display = 'block';
        }
    }

    function renderReportsList() {
        var listEl = document.getElementById('reportsList');
        var emptyEl = document.getElementById('reportsEmptyState');

        if (reportsCache.length === 0) {
            listEl.innerHTML = '';
            emptyEl.style.display = 'block';
            return;
        }
        emptyEl.style.display = 'none';

        var html = '';
        reportsCache.forEach(function(report, idx) {
            var title = report.new_title || report.blog_title || 'Untitled Blog';
            var grade = report.seo_grade || 'N/A';
            var gradeClass = 'grade-' + grade.charAt(0).toLowerCase();
            var before = Math.round(report.original_score || 0);
            var after = Math.round(report.seo_score || 0);
            var diff = Math.round(report.score_improvement || (after - before));
            var dateStr = '';
            if (report.timestamp) {
                var d = new Date(report.timestamp);
                dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
            }

            html += '<div class="report-card" id="reportCard' + idx + '">'
                + '<div class="report-card-header">'
                + '<div class="report-card-meta">'
                + '<p class="report-card-title">' + escapeHtml(title) + '</p>'
                + '<span class="report-card-date">' + dateStr + '</span>'
                + '</div>'
                + '<div class="report-card-scores">'
                + '<span class="report-grade ' + gradeClass + '">' + escapeHtml(grade) + '</span>'
                + '<span class="report-score-pill">'
                + '<span class="score-before">' + before + '</span>'
                + '<span class="score-arrow"><i class="bi bi-arrow-right-short"></i></span>'
                + '<span class="score-after">' + after + '</span>'
                + '<span class="score-diff">(+' + diff + ')</span>'
                + '</span>'
                + '</div>'
                + '<div class="report-card-actions">'
                + '<button class="report-dots-btn" onclick="toggleReportMenu(' + idx + ')"><i class="bi bi-three-dots-vertical"></i></button>'
                + '<div class="report-dropdown" id="reportMenu' + idx + '">'
                + '<button onclick="toggleReportDetails(' + idx + '); closeReportMenus();"><i class="bi bi-eye"></i> View Details</button>'
                + '<button onclick="exportSavedReport(' + idx + '); closeReportMenus();"><i class="bi bi-download"></i> Export Report</button>'
                + '<button class="danger" onclick="deleteReport(\'' + report.id + '\', ' + idx + '); closeReportMenus();"><i class="bi bi-trash3"></i> Delete</button>'
                + '</div>'
                + '</div>'
                + '</div>'
                + '<div class="report-details" id="reportDetails' + idx + '">'
                + buildReportDetails(report)
                + '</div>'
                + '</div>';
        });
        listEl.innerHTML = html;
    }

    function buildReportDetails(report) {
        var primaryKw = report.primary_keyword || {};
        var kwText = primaryKw.keyword || primaryKw.term || 'N/A';
        var kwVolume = primaryKw.search_volume || 0;
        var kwDiff = primaryKw.difficulty_score || 0;
        var kwCpc = primaryKw.cpc || 0;

        var detailsHtml = '<div class="report-details-grid">'
            + '<div class="report-detail-item"><span class="label">Keyword</span><span class="value">' + escapeHtml(kwText) + '</span></div>'
            + '<div class="report-detail-item"><span class="label">Volume</span><span class="value">' + Number(kwVolume).toLocaleString() + '</span></div>'
            + '<div class="report-detail-item"><span class="label">Difficulty</span><span class="value">' + kwDiff + '/100</span></div>'
            + '<div class="report-detail-item"><span class="label">CPC</span><span class="value">$' + Number(kwCpc).toFixed(2) + '</span></div>'
            + '</div>';

        var changes = report.changes_made || [];
        if (changes.length > 0) {
            detailsHtml += '<ul class="report-changes-list">';
            changes.forEach(function(change) {
                var text = typeof change === 'object' ? (change.description || JSON.stringify(change)) : String(change);
                detailsHtml += '<li>' + escapeHtml(text) + '</li>';
            });
            detailsHtml += '</ul>';
        }

        var recs = report.recommendations || [];
        if (recs.length > 0) {
            detailsHtml += '<div style="margin-top:0.75rem;padding-top:0.75rem;border-top:1px solid #e2e8f0"><strong style="font-size:0.78rem;color:#707EAE">Suggestions:</strong><ul class="report-changes-list" style="margin-top:0.35rem">';
            recs.forEach(function(r) {
                detailsHtml += '<li>' + escapeHtml(String(r)) + '</li>';
            });
            detailsHtml += '</ul></div>';
        }

        return detailsHtml;
    }

    window.toggleReportDetails = function(idx) {
        var el = document.getElementById('reportDetails' + idx);
        if (el) el.classList.toggle('show');
    };

    window.toggleReportMenu = function(idx) {
        var menu = document.getElementById('reportMenu' + idx);
        var isOpen = menu && menu.classList.contains('show');
        closeReportMenus();
        if (!isOpen && menu) menu.classList.add('show');
    };

    window.closeReportMenus = function() {
        document.querySelectorAll('.report-dropdown.show').forEach(function(el) {
            el.classList.remove('show');
        });
    };

    document.addEventListener('click', function(e) {
        if (!e.target.closest('.report-card-actions')) {
            closeReportMenus();
        }
    });

    window.deleteReport = async function(reportId, idx) {
        if (!confirm('Delete this optimization report?')) return;
        try {
            var response = await fetch('/api/optimization/reports/' + reportId, { method: 'DELETE' });
            var result = await response.json();
            if (result.success) {
                reportsCache.splice(idx, 1);
                renderReportsList();
                showToast({ type: 'success', title: 'Deleted', message: 'Report deleted successfully.' });
            } else {
                showToast({ type: 'error', title: 'Error', message: result.error || 'Could not delete report.' });
            }
        } catch (err) {
            showToast({ type: 'error', title: 'Error', message: 'Failed to delete report.' });
        }
    };

    window.exportSavedReport = function(idx) {
        var report = reportsCache[idx];
        if (!report) return;
        generateAndDownloadReport(report);
    };

    function generateAndDownloadReport(d) {
        var originalScore = d.original_score || 0;
        var newScore = d.seo_score || 0;
        var improvement = d.score_improvement || (newScore - originalScore);
        var grade = d.seo_grade || 'N/A';
        var title = d.new_title || d.blog_title || 'Optimized Blog';
        var primaryKw = d.primary_keyword || {};
        var kwText = primaryKw.keyword || primaryKw.term || 'N/A';
        var kwVolume = primaryKw.search_volume || 0;
        var kwDiff = primaryKw.difficulty_score || 0;
        var kwCpc = primaryKw.cpc || 0;
        var changes = d.changes_made || [];
        var comparison = d.comparison || {};
        var recommendations = d.recommendations || [];
        var dateStr = new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });

        var changesRows = '';
        changes.forEach(function(change) {
            if (typeof change === 'object') {
                changesRows += '<tr><td>' + escapeHtml(change.type || '') + '</td><td>' + escapeHtml(change.description || '') + '</td><td>' + escapeHtml(change.before || '—') + '</td><td>' + escapeHtml(change.after || '—') + '</td></tr>';
            } else {
                changesRows += '<tr><td colspan="4">' + escapeHtml(String(change)) + '</td></tr>';
            }
        });

        var breakdownRows = '';
        var catLabels = { content: 'Content', headings: 'Headings', keywords: 'Keywords', meta: 'Meta Tags', readability: 'Readability', structure: 'Structure', links: 'Links' };
        var breakdown = comparison.breakdown_comparison || {};
        Object.keys(breakdown).forEach(function(cat) {
            var bef = breakdown[cat].before || 0;
            var aft = breakdown[cat].after || 0;
            var diff = aft - bef;
            var diffStr = diff > 0 ? '+' + diff : String(diff);
            var diffColor = diff > 0 ? '#05CD99' : diff < 0 ? '#dc3545' : '#707EAE';
            breakdownRows += '<tr><td>' + (catLabels[cat] || cat) + '</td><td>' + bef + '</td><td>' + aft + '</td><td style="color:' + diffColor + ';font-weight:700">' + diffStr + '</td></tr>';
        });

        var recommendationsHtml = '';
        if (recommendations.length > 0) {
            recommendationsHtml = '<div class="section"><h2>Further Recommendations</h2><ul class="suggestions">';
            recommendations.forEach(function(rec) {
                recommendationsHtml += '<li>' + escapeHtml(String(rec)) + '</li>';
            });
            recommendationsHtml += '</ul></div>';
        }

        var html = '<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>SEO Report - ' + escapeHtml(title) + '</title><style>'
            + '*{margin:0;padding:0;box-sizing:border-box}'
            + 'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f8fafc;color:#1B2559;line-height:1.6}'
            + '.report{max-width:800px;margin:2rem auto;background:white;border-radius:20px;box-shadow:0 4px 24px rgba(112,144,176,0.12);overflow:hidden}'
            + '.header{background:linear-gradient(135deg,#4318FF 0%,#6b4dff 100%);color:white;padding:2.5rem 2rem;text-align:center}'
            + '.header h1{font-size:1.5rem;font-weight:800;margin-bottom:0.25rem}'
            + '.header .subtitle{opacity:0.85;font-size:0.9rem}'
            + '.header .date{opacity:0.7;font-size:0.8rem;margin-top:0.5rem}'
            + '.scores{display:flex;justify-content:center;gap:1.5rem;padding:2rem;flex-wrap:wrap}'
            + '.score-box{text-align:center;padding:1.25rem 1.5rem;border-radius:16px;border:1px solid #e2e8f0;min-width:130px;flex:1}'
            + '.score-box.before{background:rgba(220,53,69,0.04);border-color:rgba(220,53,69,0.2)}'
            + '.score-box.after{background:rgba(5,205,153,0.04);border-color:rgba(5,205,153,0.2)}'
            + '.score-box.improvement{background:rgba(67,24,255,0.04);border-color:rgba(67,24,255,0.2)}'
            + '.score-label{display:block;font-size:0.7rem;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:#707EAE;margin-bottom:0.25rem}'
            + '.score-value{display:block;font-size:2rem;font-weight:800}'
            + '.score-box.before .score-value{color:#dc3545}'
            + '.score-box.after .score-value{color:#05CD99}'
            + '.score-box.improvement .score-value{color:#4318FF}'
            + '.score-sub{display:block;font-size:0.72rem;color:#a3aed0;margin-top:0.15rem}'
            + '.section{padding:1.5rem 2rem;border-top:1px solid #f1f5f9}'
            + '.section h2{font-size:1rem;font-weight:700;margin-bottom:1rem;display:flex;align-items:center;gap:0.5rem;color:#1B2559}'
            + '.section h2::before{content:"";width:4px;height:18px;background:#4318FF;border-radius:2px}'
            + 'table{width:100%;border-collapse:collapse;font-size:0.85rem}'
            + 'th{background:#f8fafc;padding:0.7rem 1rem;text-align:left;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.3px;color:#707EAE;border-bottom:1px solid #e2e8f0}'
            + 'td{padding:0.7rem 1rem;border-bottom:1px solid #f1f5f9;color:#1B2559}'
            + 'tr:last-child td{border-bottom:none}'
            + '.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:0.75rem}'
            + '.detail-item{background:#f8fafc;border-radius:10px;padding:0.85rem 1rem;border:1px solid #e2e8f0}'
            + '.detail-item .label{font-size:0.72rem;font-weight:600;text-transform:uppercase;color:#707EAE;margin-bottom:0.2rem}'
            + '.detail-item .value{font-size:0.95rem;font-weight:600;color:#1B2559}'
            + '.grade-badge{display:inline-block;background:linear-gradient(135deg,#4318FF,#6b4dff);color:white;padding:0.3rem 0.8rem;border-radius:20px;font-size:0.8rem;font-weight:700}'
            + '.suggestions{list-style:none;padding:0}'
            + '.suggestions li{padding:0.6rem 0 0.6rem 1.5rem;position:relative;border-bottom:1px solid #f1f5f9;font-size:0.88rem}'
            + '.suggestions li:last-child{border-bottom:none}'
            + '.suggestions li::before{content:"\\26A1";position:absolute;left:0}'
            + '.footer{text-align:center;padding:1.5rem 2rem;border-top:1px solid #f1f5f9;color:#a3aed0;font-size:0.8rem}'
            + '@media print{body{background:white}.report{box-shadow:none;margin:0;border-radius:0}}'
            + '@media(max-width:600px){.scores{flex-direction:column;align-items:center}.detail-grid{grid-template-columns:1fr}}'
            + '</style></head><body><div class="report">'
            + '<div class="header"><h1>SEO Optimization Report</h1><div class="subtitle">' + escapeHtml(title) + '</div><div class="date">' + dateStr + '</div></div>'
            + '<div class="scores">'
            + '<div class="score-box before"><span class="score-label">Before</span><span class="score-value">' + Math.round(originalScore) + '</span><span class="score-sub">SEO Score</span></div>'
            + '<div class="score-box after"><span class="score-label">After</span><span class="score-value">' + Math.round(newScore) + '</span><span class="score-sub">SEO Score</span></div>'
            + '<div class="score-box improvement"><span class="score-label">Improvement</span><span class="score-value">+' + Math.round(improvement) + '</span><span class="score-sub">Grade: <span class="grade-badge">' + escapeHtml(grade) + '</span></span></div>'
            + '</div>'
            + '<div class="section"><h2>Keyword Data</h2><div class="detail-grid">'
            + '<div class="detail-item"><div class="label">Primary Keyword</div><div class="value">' + escapeHtml(kwText) + '</div></div>'
            + '<div class="detail-item"><div class="label">Search Volume</div><div class="value">' + Number(kwVolume).toLocaleString() + '</div></div>'
            + '<div class="detail-item"><div class="label">Difficulty</div><div class="value">' + kwDiff + '/100</div></div>'
            + '<div class="detail-item"><div class="label">CPC</div><div class="value">$' + Number(kwCpc).toFixed(2) + '</div></div>'
            + '</div></div>'
            + '<div class="section"><h2>Changes Made</h2><table><thead><tr><th>Type</th><th>Description</th><th>Before</th><th>After</th></tr></thead><tbody>' + changesRows + '</tbody></table></div>'
            + '<div class="section"><h2>Score Breakdown</h2><table><thead><tr><th>Category</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody>' + breakdownRows + '</tbody></table></div>'
            + recommendationsHtml
            + '<div class="footer">Generated by ScriptlyAI &mdash; ' + dateStr + '</div>'
            + '</div></body></html>';

        var blob = new Blob([html], { type: 'text/html' });
        var dlUrl = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = dlUrl;
        var slug = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').substring(0, 40);
        a.download = 'seo-report-' + slug + '-' + new Date().toISOString().slice(0, 10) + '.html';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(dlUrl);
    }

})();
