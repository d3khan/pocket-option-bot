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
            // Fetch initial status (falls back gracefully)
            fetch('/api/connection-status')
                .then(r => r.json())
                .then(data => { this.connected = data.connected; })
                .catch(() => {});
        }
    };
}

// ---------- Socket.IO Setup ----------
document.addEventListener('DOMContentLoaded', function() {
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

    const events = [
        'stats_update',
        'trade_new',
        'trade_closed',
        'candle_tick',
        'asset_switched',
        'connection_status',
        'bot_status_changed'
    ];

    events.forEach(eventName => {
        socket.on(eventName, (data) => {
            const customEvent = new CustomEvent(eventName, { detail: data });
            document.body.dispatchEvent(customEvent);
        });
    });
});

// ---------- Alpine re-init after HTMX swaps ----------
document.addEventListener('htmx:afterSwap', (e) => {
    if (window.Alpine) {
        // Destroy any existing Alpine components in the swapped element
        if (typeof Alpine.destroyTree === 'function') {
            Alpine.destroyTree(e.target);
        }
        Alpine.initTree(e.target);
    }
});