// Neural-Chromium Browser Control Extension
// Background Service Worker - WebSocket Client Architecture

const WS_URL = 'ws://127.0.0.1:9223';
let socket = null;
let heartbeatInterval = null;

// Connect to the Standalone Python Server
function connectWebSocket() {
    console.log('[Extension] Connecting to WebSocket Server:', WS_URL);
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
        console.log('[Extension] WebSocket Connected');
        // Register as "browser"
        socket.send(JSON.stringify({ type: 'browser' }));

        // Start Heartbeat to keep Service Worker alive (Chrome 116+)
        startHeartbeat();
    };

    socket.onmessage = (event) => {
        try {
            const message = JSON.parse(event.data);
            console.log('[Extension] Received:', message);
            handleCommand(message);
        } catch (error) {
            console.error('[Extension] Message error:', error);
        }
    };

    socket.onclose = () => {
        console.log('[Extension] WebSocket Disconnected. Retrying in 5s...');
        stopHeartbeat();
        socket = null;
        setTimeout(connectWebSocket, 5000);
    };

    socket.onerror = (error) => {
        console.error('[Extension] WebSocket Error:', error);
    };
}

function startHeartbeat() {
    stopHeartbeat();
    heartbeatInterval = setInterval(() => {
        if (socket && socket.readyState === WebSocket.OPEN) {
            // Send traffic to reset Service Worker idle timer
            // Sending 'pong' (unsolicited) is handled by server as keep-alive
            socket.send(JSON.stringify({ pong: true }));
        }
    }, 20000); // 20 seconds
}

function stopHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
    }
}

// Handle commands from Agent
async function handleCommand(message) {
    const { id, action, params } = message;
    let result = { id, success: false };

    try {
        // Get active tab
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

        if (!tab) {
            result.error = 'No active tab';
            sendResponse(result);
            return;
        }

        switch (action) {
            case 'click':
                result = await executeClick(tab.id, params);
                break;
            case 'type':
                result = await executeType(tab.id, params);
                break;
            case 'press_key':
                result = await executePressKey(tab.id, params);
                break;
            case 'navigate':
                result = await executeNavigate(tab.id, params);
                break;
            case 'get_dom':
                result = await getDOM(tab.id);
                break;
            case 'execute_js':
                result = await executeJS(tab.id, params);
                break;
            case 'ping':
                // Agent might ping us?
                result = { pong: true };
                break;
            default:
                result.error = `Unknown action: ${action}`;
        }

    } catch (error) {
        result.error = error.message;
    }

    // Ensure ID is preserved for response correlation
    result.id = id;
    sendResponse(result);
}

function sendResponse(result) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(result));
    }
}

// --- Action Implementations (Same as before) ---

async function executeClick(tabId, params) {
    try {
        const res = await chrome.scripting.executeScript({
            target: { tabId },
            func: (x, y) => {
                const el = document.elementFromPoint(x, y);
                if (el) { el.click(); return { success: true, tag: el.tagName }; }
                return { success: false, error: 'No element' };
            },
            args: [params.x, params.y]
        });
        return res[0].result;
    } catch (e) { return { success: false, error: e.message }; }
}

async function executeType(tabId, params) {
    try {
        const res = await chrome.scripting.executeScript({
            target: { tabId },
            world: 'MAIN',
            func: (text) => {
                let el = document.activeElement;
                if (!el || (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA')) {
                    el = document.querySelector('input[type="text"], input[type="search"], textarea');
                    if (el) el.focus();
                }
                if (el) {
                    el.value = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return {
                        success: true,
                        tag: el.tagName,
                        id: el.id,
                        className: el.className
                    };
                }
                return { success: false, error: 'No input found' };
            },
            args: [params.text]
        });
        return res[0].result;
    } catch (e) { return { success: false, error: e.message }; }
}

async function executePressKey(tabId, params) {
    try {
        const res = await chrome.scripting.executeScript({
            target: { tabId },
            func: (key) => {
                document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
                document.activeElement.dispatchEvent(new KeyboardEvent('keypress', { key, bubbles: true }));
                document.activeElement.dispatchEvent(new KeyboardEvent('keyup', { key, bubbles: true }));
                return { success: true };
            },
            args: [params.key]
        });
        return res[0].result;
    } catch (e) { return { success: false, error: e.message }; }
}

async function executeNavigate(tabId, params) {
    await chrome.tabs.update(tabId, { url: params.url });
    return { success: true };
}

async function getDOM(tabId) {
    // Simplify for now
    try {
        const res = await chrome.scripting.executeScript({
            target: { tabId },
            func: () => ({ title: document.title, url: window.location.href })
        });
        return { success: true, dom: res[0].result };
    } catch (e) { return { success: false, error: e.message }; }
}

async function executeJS(tabId, params) {
    try {
        const res = await chrome.scripting.executeScript({
            target: { tabId },
            func: new Function(params.code)
        });
        return { success: true, result: res[0].result };
    } catch (e) { return { success: false, error: e.message }; }
}

// Initialize
chrome.runtime.onInstalled.addListener(() => {
    connectWebSocket();
});
connectWebSocket();
