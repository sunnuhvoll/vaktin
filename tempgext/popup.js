const timeoutStorageKey = "timeoutHours";
const defaultTimeoutHours = 3;
const minTimeoutHours = 0.1;
const maxTimeoutHours = 24;

const input = document.getElementById("timeoutHours");
const saveButton = document.getElementById("saveButton");
const status = document.getElementById("status");

function normalizeTimeoutHours(value) {
  const parsedValue = Number(value);

  if (!Number.isFinite(parsedValue)) {
    return defaultTimeoutHours;
  }

  return Math.min(Math.max(parsedValue, minTimeoutHours), maxTimeoutHours);
}

function showStatus(message) {
  status.textContent = message;
}

function saveTimeoutHours() {
  const timeoutHours = normalizeTimeoutHours(input.value);
  input.value = String(timeoutHours);

  chrome.storage.sync.set({ [timeoutStorageKey]: timeoutHours }, function() {
    showStatus(`Saved: ${timeoutHours} hours`);
  });
}

chrome.storage.sync.get({ [timeoutStorageKey]: defaultTimeoutHours }, function(items) {
  input.value = String(normalizeTimeoutHours(items[timeoutStorageKey]));
  showStatus("Default is 3 hours");
});

input.addEventListener("change", saveTimeoutHours);
input.addEventListener("blur", saveTimeoutHours);

if (saveButton) {
  saveButton.addEventListener("click", saveTimeoutHours);
}
