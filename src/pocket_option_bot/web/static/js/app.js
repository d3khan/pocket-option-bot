// Socket.IO client initialization and event forwarding to custom DOM events
// Alpine.js component definitions (must be global for HTMX-swapped content)

// ---------- Alpine Component: candleData ----------
function candleData() {
    return {
        candle: {},
        init() {
            document.addEventListener('candleTick', (e) => {
                this.candle = e.detail;
            });
        }
    };
}

// ---------- Alpine Component: connectionStatus ----------
function connectionStatus() {
    return {
        connected: false,
        init() {
            document.addEventListener('connectionStatus', (e) => {
                this.connected = e.detail?.status === 'connected';
            });
            document.addEventListener('connection_status', (e) => {
                this.connected = e.detail?.status === 'connected';
            });
        }
    };
}

// ---------- Global 401 handler for all fetch/XHR requests ----------
// If any request returns 401, redirect to login.
(function () {
    const originalFetch = window.fetch;
    window.fetch = function (...args) {
        return originalFetch.apply(this, args)
            .then(response => {
                if (response.status === 401) {
                    window.location.href = '/login';
                    throw new Error('Unauthorized');
                }
                return response;
            });
    };
})();

// Also handle HTMX 401 responses
document.addEventListener('htmx:responseError', (e) => {
    if (e.detail.xhr.status === 401) {
        window.location.href = '/login';
    }
});

// ---------- Socket.IO Setup ----------
document.addEventListener('DOMContentLoaded', function () {
    const socket = io({
        path: '/socket.io/',
        transports: ['polling', 'websocket']
    });

    socket.on('connect', () => {
        console.log('Socket.IO connected');
    });

    socket.on('disconnect', () => {
        console.log('Socket.IO disconnected');
    });

    // Map Socket.IO events to HTMX trigger events (PascalCase)
    const eventMap = {
        'stats_update': 'statsUpdate',
        'trade_new': 'tradeNew',
        'trade_closed': 'tradeClosed',
        'candle_tick': 'candleTick',
        'asset_switched': 'assetSwitched',
        'connection_status': 'connectionStatus',
        'bot_status_changed': 'botStatusChange'
    };

    Object.entries(eventMap).forEach(([socketEvent, htmxEvent]) => {
        socket.on(socketEvent, (data) => {
            const customEvent = new CustomEvent(htmxEvent, { detail: data });
            document.body.dispatchEvent(customEvent);
        });
    });
});

// ---------- Alpine re-init after HTMX swaps ----------
document.addEventListener('htmx:afterSwap', (e) => {
    if (window.Alpine) {
        if (typeof Alpine.destroyTree === 'function') {
            Alpine.destroyTree(e.target);
        }
        Alpine.initTree(e.target);
    }
});