/**
 * Schedule Page — Weekly Calendar View
 * Wrapped in IIFE to avoid let/const re-declaration errors on Pjax re-navigation.
 */
(function() {

var allBlogs = [];
var currentWeekStart = null;
var rescheduleBlogId = null;
var selectedBlogId = null;
var availableBlogs = [];

function getWeekStart(date) {
  var d = new Date(date);
  var day = d.getDay();
  d.setDate(d.getDate() - day);
  d.setHours(0, 0, 0, 0);
  return d;
}

function getWeekDays(weekStart) {
  var days = [];
  for (var i = 0; i < 7; i++) {
    var d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    days.push(d);
  }
  return days;
}

function formatWeekTitle(weekStart) {
  var weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);
  var opts = { month: 'short', day: 'numeric' };
  var startStr = weekStart.toLocaleDateString('en-US', opts);
  var endStr = weekEnd.toLocaleDateString('en-US', opts);
  var year = weekEnd.getFullYear();
  return startStr + ' \u2013 ' + endStr + ', ' + year;
}

function isSameDay(d1, d2) {
  return d1.getFullYear() === d2.getFullYear() &&
         d1.getMonth() === d2.getMonth() &&
         d1.getDate() === d2.getDate();
}

function isToday(date) {
  return isSameDay(date, new Date());
}

function getTimeBlock(hour) {
  if (hour >= 6 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 18) return 'afternoon';
  return 'evening';
}

function escapeHtml(str) {
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// ==================== NAVIGATION ====================

function setupWeekNavigation() {
  var prev = document.getElementById('prevWeek');
  var next = document.getElementById('nextWeek');
  var today = document.getElementById('todayBtn');

  if (prev) prev.addEventListener('click', function() {
    currentWeekStart.setDate(currentWeekStart.getDate() - 7);
    updateWeekTitle();
    renderWeekCalendar();
  });

  if (next) next.addEventListener('click', function() {
    currentWeekStart.setDate(currentWeekStart.getDate() + 7);
    updateWeekTitle();
    renderWeekCalendar();
  });

  if (today) today.addEventListener('click', function() {
    currentWeekStart = getWeekStart(new Date());
    updateWeekTitle();
    renderWeekCalendar();
  });
}

function updateWeekTitle() {
  var el = document.getElementById('weekTitle');
  if (el) el.textContent = formatWeekTitle(currentWeekStart);
}

// ==================== STATS ====================

function updateStats() {
  var scheduled = allBlogs.filter(function(b) { return b.status === 'SCHEDULED'; }).length;
  var published = allBlogs.filter(function(b) { return b.status === 'PUBLISHED'; }).length;
  var weekDays = getWeekDays(currentWeekStart);
  var thisWeek = allBlogs.filter(function(b) {
    if (!b.scheduled_at) return false;
    var d = new Date(b.scheduled_at);
    return d >= weekDays[0] && d <= new Date(weekDays[6].getTime() + 86400000);
  }).length;

  var elSched = document.getElementById('stat-scheduled');
  var elPub = document.getElementById('stat-published');
  var elWeek = document.getElementById('stat-week');
  if (elSched) elSched.textContent = scheduled;
  if (elPub) elPub.textContent = published;
  if (elWeek) elWeek.textContent = thisWeek;
}

// ==================== DATA LOADING ====================

function loadScheduleData() {
  fetch('/api/schedule/list')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success && data.blogs) {
        allBlogs = data.blogs;
        updateStats();
        renderWeekCalendar();
      }
    })
    .catch(function(err) {
      console.error('Schedule load error:', err);
    });
}

// ==================== WEEKLY CALENDAR RENDERING ====================

function renderWeekCalendar() {
  var container = document.getElementById('weeklyCalendar');
  if (!container) return;

  var days = getWeekDays(currentWeekStart);
  var dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var timeBlocks = [
    { key: 'morning', label: 'Morning' },
    { key: 'afternoon', label: 'Afternoon' },
    { key: 'evening', label: 'Evening' }
  ];

  var html = '<div class="calendar-grid">';

  // Header row
  html += '<div class="calendar-header-row">';
  html += '<div class="calendar-header-spacer"></div>';
  for (var i = 0; i < days.length; i++) {
    var day = days[i];
    var todayClass = isToday(day) ? ' is-today' : '';
    html += '<div class="calendar-day-header' + todayClass + '">' +
      '<div class="day-name">' + dayNames[i] + '</div>' +
      '<div class="day-number">' + day.getDate() + '</div>' +
    '</div>';
  }
  html += '</div>';

  // Time block rows
  for (var t = 0; t < timeBlocks.length; t++) {
    var block = timeBlocks[t];
    html += '<div class="calendar-time-row">';
    html += '<div class="calendar-time-label">' + block.label + '</div>';

    for (var d = 0; d < days.length; d++) {
      var cellDay = days[d];
      var blogsInCell = getBlogsForCell(cellDay, block.key);
      html += '<div class="calendar-cell">';

      for (var b = 0; b < blogsInCell.length; b++) {
        var blog = blogsInCell[b];
        var time = new Date(blog.scheduled_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
        var isPublished = blog.status === 'PUBLISHED';
        var eventClass = isPublished ? 'calendar-event calendar-event-published' : 'calendar-event';

        var dropdownHtml = '';
        if (isPublished) {
          dropdownHtml = '<ul class="dropdown-menu dropdown-menu-end">' +
            '<li><span class="dropdown-item text-success" style="cursor:default;">' +
            '<i class="bi bi-check-circle-fill"></i> Published</span></li></ul>';
        } else {
          dropdownHtml = '<ul class="dropdown-menu dropdown-menu-end">' +
            '<li><button class="dropdown-item" onclick="window._scheduleOpenReschedule(\'' + blog.id + '\', \'' + escapeAttr(blog.title) + '\')">' +
            '<i class="bi bi-calendar-event" style="color: var(--primary-color);"></i> Reschedule</button></li>' +
            '<li><button class="dropdown-item" onclick="window._schedulePublishNow(\'' + blog.id + '\')">' +
            '<i class="bi bi-check-circle" style="color: #059669;"></i> Publish Now</button></li>' +
            '<li><hr class="dropdown-divider"></li>' +
            '<li><button class="dropdown-item text-danger" onclick="window._scheduleCancelSchedule(\'' + blog.id + '\')">' +
            '<i class="bi bi-x-circle"></i> Cancel Schedule</button></li></ul>';
        }

        html += '<div class="' + eventClass + '" data-blog-id="' + blog.id + '">' +
          '<div class="calendar-event-title">' + escapeHtml(blog.title) + '</div>' +
          '<div class="calendar-event-time">' + time + (isPublished ? ' &#10003;' : '') + '</div>' +
          '<div class="event-dropdown"><div class="dropdown">' +
          '<button class="btn-event-menu" type="button" data-bs-toggle="dropdown" aria-expanded="false">' +
          '<i class="bi bi-three-dots"></i></button>' +
          dropdownHtml + '</div></div></div>';
      }

      html += '</div>';
    }

    html += '</div>';
  }

  html += '</div>';
  container.innerHTML = html;
}

function getBlogsForCell(day, blockKey) {
  return allBlogs.filter(function(blog) {
    if (!blog.scheduled_at) return false;
    var d = new Date(blog.scheduled_at);
    if (!isSameDay(d, day)) return false;
    var hour = d.getHours();
    return getTimeBlock(hour) === blockKey;
  });
}

// ==================== RESCHEDULE MODAL ====================

function setupRescheduleModal() {
  var btn = document.getElementById('confirmRescheduleBtn');
  if (!btn) return;

  btn.addEventListener('click', function() {
    var dateTime = document.getElementById('rescheduleDateTime').value;
    if (!dateTime) {
      showToast({ type: 'error', title: 'Error', message: 'Please select a date and time.', duration: 3000 });
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div> Rescheduling...';

    var isoDate = new Date(dateTime).toISOString();
    fetch('/api/schedule/' + rescheduleBlogId + '/reschedule', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scheduled_at: isoDate })
    })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        showToast({ type: 'success', title: 'Rescheduled', message: 'Blog rescheduled successfully.', duration: 3000 });
        bootstrap.Modal.getInstance(document.getElementById('rescheduleModal')).hide();
        loadScheduleData();
      } else {
        showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to reschedule.', duration: 4000 });
      }
    })
    .catch(function() {
      showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 4000 });
    })
    .finally(function() {
      btn.disabled = false;
      btn.innerHTML = '<i class="bi bi-calendar-check"></i> Reschedule';
    });
  });
}

function openReschedule(blogId, title) {
  rescheduleBlogId = blogId;
  document.getElementById('reschedule-blog-title').textContent = title;

  var now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  document.getElementById('rescheduleDateTime').min = now.toISOString().slice(0, 16);
  document.getElementById('rescheduleDateTime').value = '';

  var modal = new bootstrap.Modal(document.getElementById('rescheduleModal'));
  modal.show();
  loadBestTimeSuggestions('bestTimeSuggestionsListReschedule');
}

// ==================== PUBLISH / CANCEL ====================

function publishNow(blogId) {
  if (!confirm('Publish this blog immediately?')) return;

  fetch('/api/schedule/' + blogId + '/publish-now', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
  .then(function(res) { return res.json(); })
  .then(function(data) {
    if (data.success) {
      showToast({ type: 'success', title: 'Published!', message: 'Blog is now live on the site.', duration: 3000 });
      loadScheduleData();
    } else {
      showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to publish.', duration: 4000 });
    }
  })
  .catch(function() {
    showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 4000 });
  });
}

function cancelSchedule(blogId) {
  if (!confirm('Cancel this scheduled blog? It will be moved back to drafts.')) return;

  fetch('/api/schedule/' + blogId + '/cancel', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' }
  })
  .then(function(res) { return res.json(); })
  .then(function(data) {
    if (data.success) {
      showToast({ type: 'warning', title: 'Cancelled', message: 'Schedule cancelled, blog moved to drafts.', duration: 3000 });
      loadScheduleData();
    } else {
      showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to cancel.', duration: 4000 });
    }
  })
  .catch(function() {
    showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 4000 });
  });
}

// ==================== ADD SCHEDULE MODAL ====================

function setupAddScheduleModal() {
  var addBtn = document.getElementById('addScheduleBtn');
  var searchInput = document.getElementById('blogSearchInput');
  var confirmBtn = document.getElementById('confirmScheduleBtn');

  if (!addBtn || !searchInput || !confirmBtn) return;

  addBtn.addEventListener('click', function() {
    selectedBlogId = null;
    document.getElementById('scheduleFormSection').style.display = 'none';
    confirmBtn.disabled = true;
    searchInput.value = '';
    loadAvailableBlogs();
    var modal = new bootstrap.Modal(document.getElementById('addScheduleModal'));
    modal.show();
  });

  searchInput.addEventListener('input', function(e) {
    renderBlogList(e.target.value.trim().toLowerCase());
  });

  confirmBtn.addEventListener('click', confirmScheduleBlog);
}

function loadAvailableBlogs() {
  var container = document.getElementById('blogListContainer');
  container.innerHTML = '<div class="blog-list-loading">' +
    '<div class="spinner-border spinner-border-sm text-primary"></div>' +
    '<span>Loading blogs...</span></div>';

  fetch('/api/schedule/available-blogs')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        availableBlogs = data.blogs;
        renderBlogList('');
      } else {
        container.innerHTML = '<div class="blog-list-empty"><i class="bi bi-exclamation-circle"></i>Failed to load blogs.</div>';
      }
    })
    .catch(function() {
      container.innerHTML = '<div class="blog-list-empty"><i class="bi bi-exclamation-circle"></i>Connection error.</div>';
    });
}

function renderBlogList(search) {
  var container = document.getElementById('blogListContainer');
  var filtered = availableBlogs;

  if (search) {
    filtered = availableBlogs.filter(function(b) {
      return b.title.toLowerCase().includes(search) ||
        b.author_name.toLowerCase().includes(search);
    });
  }

  if (filtered.length === 0) {
    container.innerHTML = '<div class="blog-list-empty">' +
      '<i class="bi bi-file-earmark-x"></i>' +
      (search ? 'No blogs match your search.' : 'No drafts or blogs pending approval.') +
    '</div>';
    return;
  }

  container.innerHTML = filtered.map(function(blog) {
    var statusClass = blog.status === 'DRAFT' ? 'badge-draft' : 'badge-review';
    var statusLabel = blog.status === 'DRAFT' ? 'Draft' : 'Approval';
    var selectedClass = selectedBlogId === blog.id ? ' selected' : '';

    return '<div class="blog-list-item' + selectedClass + '" onclick="window._scheduleSelectBlog(\'' + blog.id + '\', \'' + escapeAttr(blog.title) + '\')">' +
      '<div class="blog-list-item-info">' +
      '<div class="blog-list-item-title">' + escapeHtml(blog.title) + '</div>' +
      '<div class="blog-list-item-meta"><span><i class="bi bi-person"></i> ' + escapeHtml(blog.author_name) + '</span></div>' +
      '</div>' +
      '<span class="blog-status-badge ' + statusClass + '">' + statusLabel + '</span></div>';
  }).join('');
}

function selectBlogForSchedule(blogId, blogTitle) {
  selectedBlogId = blogId;
  renderBlogList(document.getElementById('blogSearchInput').value.trim().toLowerCase());

  var formSection = document.getElementById('scheduleFormSection');
  formSection.style.display = 'block';
  document.getElementById('selectedBlogInfo').innerHTML = '<i class="bi bi-file-earmark-text"></i> ' + blogTitle;
  document.getElementById('confirmScheduleBtn').disabled = false;

  var now = new Date();
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
  document.getElementById('scheduleDateTime').min = now.toISOString().slice(0, 16);
  document.getElementById('scheduleDateTime').value = '';

  loadBestTimeSuggestions('bestTimeSuggestionsListAdd');
}

function confirmScheduleBlog() {
  if (!selectedBlogId) return;

  var dateTime = document.getElementById('scheduleDateTime').value;
  if (!dateTime) {
    showToast({ type: 'error', title: 'Error', message: 'Please select a date and time.', duration: 3000 });
    return;
  }

  var btn = document.getElementById('confirmScheduleBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div> Scheduling...';

  var isoDate = new Date(dateTime).toISOString();
  fetch('/api/schedule/' + selectedBlogId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ scheduled_at: isoDate })
  })
  .then(function(res) { return res.json(); })
  .then(function(data) {
    if (data.success) {
      showToast({ type: 'success', title: 'Scheduled!', message: data.message || 'Blog scheduled successfully.', duration: 3000 });
      bootstrap.Modal.getInstance(document.getElementById('addScheduleModal')).hide();
      loadScheduleData();
    } else {
      showToast({ type: 'error', title: 'Error', message: data.error || 'Failed to schedule.', duration: 4000 });
    }
  })
  .catch(function() {
    showToast({ type: 'error', title: 'Error', message: 'Connection error.', duration: 4000 });
  })
  .finally(function() {
    btn.disabled = false;
    btn.innerHTML = '<i class="bi bi-calendar-check"></i> Schedule';
  });
}

// ==================== BEST TIME SUGGESTIONS ====================

var FALLBACK_SUGGESTIONS = [
  { day: "Tuesday", day_index: 2, hour: 10, display_time: "Tuesday, 10:00 AM", reasoning: "Tuesdays mid-morning have high engagement across most blogs" },
  { day: "Thursday", day_index: 4, hour: 14, display_time: "Thursday, 2:00 PM", reasoning: "Thursday afternoons are peak reading time for most audiences" },
  { day: "Wednesday", day_index: 3, hour: 9, display_time: "Wednesday, 9:00 AM", reasoning: "Mid-week mornings capture early readers checking content" }
];

function loadBestTimeSuggestions(listElId) {
  var list = document.getElementById(listElId);
  if (!list) return;

  list.innerHTML = '<div class="best-time-loading">' +
    '<div class="spinner-border spinner-border-sm text-primary"></div>' +
    '<span>Analyzing your traffic data...</span></div>';

  fetch('/api/schedule/best-time?t=' + Date.now())
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var inputId = listElId === 'bestTimeSuggestionsListAdd' ? 'scheduleDateTime' : 'rescheduleDateTime';
      if (data.success && data.suggestions && data.suggestions.length > 0) {
        renderSuggestionChips(list, data.suggestions, inputId, true);
      } else {
        renderSuggestionChips(list, FALLBACK_SUGGESTIONS, inputId, false, data.message || null);
      }
    })
    .catch(function() {
      var inputId = listElId === 'bestTimeSuggestionsListAdd' ? 'scheduleDateTime' : 'rescheduleDateTime';
      renderSuggestionChips(list, FALLBACK_SUGGESTIONS, inputId, false, null);
    });
}

function renderSuggestionChips(listEl, suggestions, inputId, fromAnalytics, apiMessage) {
  var sourceLabel;
  if (fromAnalytics) {
    sourceLabel = '<span class="best-time-source"><i class="bi bi-check-circle-fill"></i> Based on your Google Analytics data (last 28 days)</span>';
  } else if (apiMessage) {
    sourceLabel = '<span class="best-time-source best-time-source-warning"><i class="bi bi-exclamation-triangle-fill"></i> ' + apiMessage + '</span>';
  } else {
    sourceLabel = '<span class="best-time-source"><i class="bi bi-lightbulb-fill"></i> General best practices</span>';
  }

  listEl.innerHTML = sourceLabel + suggestions.map(function(s) {
    return '<button type="button" class="best-time-chip" onclick="window._scheduleApplyBestTime(' + s.day_index + ', ' + s.hour + ', \'' + inputId + '\')" title="' + s.reasoning + '">' +
      '<i class="bi bi-clock"></i> ' + s.display_time +
      '<span class="best-time-score">' + s.reasoning + '</span></button>';
  }).join('');
}

function applyBestTime(dayIndex, hour, inputId) {
  var now = new Date();
  var currentDay = now.getDay();
  var daysUntil = dayIndex - currentDay;
  if (daysUntil < 0) daysUntil += 7;
  if (daysUntil === 0 && hour <= now.getHours()) daysUntil = 7;

  var target = new Date(now);
  target.setDate(target.getDate() + daysUntil);
  target.setHours(hour, 0, 0, 0);

  var year = target.getFullYear();
  var month = String(target.getMonth() + 1).padStart(2, '0');
  var day = String(target.getDate()).padStart(2, '0');
  var hrs = String(target.getHours()).padStart(2, '0');

  document.getElementById(inputId).value = year + '-' + month + '-' + day + 'T' + hrs + ':00';
}

// ==================== EXPOSE TO GLOBAL (for onclick handlers in rendered HTML) ====================

window._scheduleOpenReschedule = openReschedule;
window._schedulePublishNow = publishNow;
window._scheduleCancelSchedule = cancelSchedule;
window._scheduleSelectBlog = selectBlogForSchedule;
window._scheduleApplyBestTime = applyBestTime;

// ==================== INIT ====================

currentWeekStart = getWeekStart(new Date());
renderWeekCalendar();
updateWeekTitle();
setupWeekNavigation();
setupRescheduleModal();
setupAddScheduleModal();
loadScheduleData();

})();
