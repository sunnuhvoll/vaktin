const intervalSeconds = 10;
const intervalMillis = intervalSeconds * 1000;
const defaultTimeoutHours = 3;
const minTimeoutHours = 0.1;
const maxTimeoutHours = 24;
const timeoutStorageKey = "timeoutHours";

let timeoutHours = defaultTimeoutHours;
let lastHumanActivityAt = Date.now();

function normalizeTimeoutHours(value) {
    const parsedValue = Number(value);

    if (!Number.isFinite(parsedValue)) {
        return defaultTimeoutHours;
    }

    return Math.min(Math.max(parsedValue, minTimeoutHours), maxTimeoutHours);
}

function timeoutMillis() {
    return timeoutHours * 60 * 60 * 1000;
}

function hasRecentHumanActivity() {
    return Date.now() - lastHumanActivityAt < timeoutMillis();
}

function recordHumanActivity(event) {
    if (event.isTrusted) {
        lastHumanActivityAt = Date.now();
    }
}

/**
 * The function for performing the mouse jiggle.
 *
 * We simulate mouse movement by creating a "mousemove" event and
 * dispatching it to the DOM.
 * This creates "fake" mouse movement.
 * ie. The web page thinks the user has moved their mouse, but the user's
 * mouse position doesn't actually change.
 *
 * That way we can trigger DOM listeners (eg. ones monitoring for user activity)
 * without getting in the user's way by legitimately moving the mouse.
 */
function moveMouse() {
    var evt = new MouseEvent("mousemove", {
        view: window,
        bubbles: true,
        cancelable: true
    });
    document.dispatchEvent(evt);
}

function maybeMoveMouse() {
    if (hasRecentHumanActivity()) {
        moveMouse();
    }
}

function loadTimeoutSetting() {
    if (!chrome.storage || !chrome.storage.sync) {
        timeoutHours = defaultTimeoutHours;
        return;
    }

    chrome.storage.sync.get({ [timeoutStorageKey]: defaultTimeoutHours }, function(items) {
        timeoutHours = normalizeTimeoutHours(items[timeoutStorageKey]);
    });
}

function watchTimeoutSetting() {
    if (!chrome.storage || !chrome.storage.onChanged) {
        return;
    }

    chrome.storage.onChanged.addListener(function(changes, areaName) {
        if (areaName !== "sync" || !changes[timeoutStorageKey]) {
            return;
        }

        timeoutHours = normalizeTimeoutHours(changes[timeoutStorageKey].newValue);
    });
}

[
    "mousemove",
    "pointerdown",
    "click",
    "keydown",
    "wheel",
    "touchstart"
].forEach(function(eventName) {
    window.addEventListener(eventName, recordHumanActivity, {
        capture: true,
        passive: true
    });
});

loadTimeoutSetting();
watchTimeoutSetting();
setInterval(maybeMoveMouse, intervalMillis);
