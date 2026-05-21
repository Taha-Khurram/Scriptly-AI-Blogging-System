/**
 * Create Blog Page JavaScript
 */

function autoResize(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = textarea.scrollHeight + 'px';
}

function handleKeyPress(event) {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    handleGeneration();
  }
}

// Humanize toggle button
document.addEventListener('DOMContentLoaded', () => {
  const toggleBtn = document.getElementById('humanizeToggleBtn');
  const hiddenInput = document.getElementById('humanizeToggle');
  if (toggleBtn && hiddenInput) {
    toggleBtn.addEventListener('click', () => {
      const isActive = toggleBtn.classList.toggle('active');
      hiddenInput.value = isActive ? 'true' : 'false';
    });
  }
});

const STAGE_MESSAGES = {
  'starting': 'Starting generation...',
  'outline': 'Generating outline...',
  'content': 'Writing blog content...',
  'humanizing': 'Humanizing content...',
  'formatting': 'Formatting and styling...',
  'categorizing': 'Assigning category...',
  'saving': 'Saving to drafts...',
  'completed': 'Done!'
};

function updateLoaderStage(stage, progress) {
  const loaderText = document.querySelector('#loader span');
  if (loaderText && STAGE_MESSAGES[stage]) {
    loaderText.textContent = STAGE_MESSAGES[stage];
  }
  const progressBar = document.getElementById('genProgressBar');
  if (progressBar) {
    progressBar.style.width = progress + '%';
  }
}

function pollTaskStatus(taskId) {
  const pollInterval = setInterval(async () => {
    try {
      const res = await fetch(`/api/generate/status/${taskId}`);
      if (res.status === 401) {
        clearInterval(pollInterval);
        window.location.href = '/login';
        return;
      }
      const data = await res.json();

      updateLoaderStage(data.stage, data.progress);

      if (data.status === 'completed') {
        clearInterval(pollInterval);
        showToast({
          type: 'success',
          title: 'Blog Generated!',
          message: 'Your blog has been created and saved.',
          duration: 3000
        });
        setTimeout(() => {
          window.location.href = data.result.redirect || "/drafts";
        }, 1000);
      } else if (data.status === 'failed') {
        clearInterval(pollInterval);
        showToast({
          type: 'error',
          title: 'Generation Failed',
          message: data.error || 'Please try again.',
          duration: 5000
        });
        resetForm();
      }
    } catch (err) {
      clearInterval(pollInterval);
      console.error("Polling error:", err);
      showToast({
        type: 'error',
        title: 'Connection Error',
        message: 'Lost connection. Check your network.',
        duration: 5000
      });
      resetForm();
    }
  }, 3000);
}

function resetForm() {
  const promptInput = document.getElementById('prompt');
  const loader = document.getElementById('loader');
  const genBtn = document.getElementById('genBtn');
  const promptBox = promptInput.closest('.prompt-box');

  genBtn.disabled = false;
  promptInput.disabled = false;
  promptBox.classList.remove('locked');
  loader.classList.add('d-none');

  const progressBar = document.getElementById('genProgressBar');
  if (progressBar) progressBar.style.width = '0%';
}

async function handleGeneration() {
  const promptInput = document.getElementById('prompt');
  const promptText = promptInput.value.trim();
  const loader = document.getElementById('loader');
  const genBtn = document.getElementById('genBtn');
  const promptBox = promptInput.closest('.prompt-box');

  if (!promptText) return;

  const enableHumanize = document.getElementById('humanizeToggle')?.value === 'true';

  loader.classList.remove('d-none');
  genBtn.disabled = true;
  promptInput.disabled = true;
  promptInput.blur();
  promptBox.classList.add('locked');

  updateLoaderStage('starting', 5);

  try {
    const response = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: promptText, enable_humanize: enableHumanize })
    });

    const data = await response.json();

    if (data.success && data.task_id) {
      pollTaskStatus(data.task_id);
    } else {
      showToast({
        type: 'error',
        title: 'Generation Failed',
        message: data.error || 'Please try again.',
        duration: 5000
      });
      resetForm();
    }
  } catch (err) {
    console.error("Error:", err);
    showToast({
      type: 'error',
      title: 'Connection Error',
      message: 'Something went wrong. Check your connection.',
      duration: 5000
    });
    resetForm();
  }
}
