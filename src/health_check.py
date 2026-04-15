import logging
import threading
import time
from typing import List, TYPE_CHECKING

import requests
from flask import Flask, jsonify

from src.config import CommonConfig

if TYPE_CHECKING:
    from pyrogram import Client

log = logging.getLogger("health_check")


class HealthCheckServer:
    """Simple HTTP server for health checks"""

    def __init__(self, config: CommonConfig, ping_interval: int = 20):
        self.config = config
        self.app = Flask(__name__)
        self.is_healthy = True
        self.status_message = "OK"
        self.ping_interval = ping_interval
        self._clients: List['Client'] = []
        self._setup_routes()
        self.server_thread = None

    def register_client(self, client: 'Client'):
        """Register a bot client for connection checking"""
        self._clients.append(client)

    def _client_statuses(self) -> list[dict]:
        return [
            {'name': c.name, 'connected': c.is_connected}
            for c in self._clients
        ]

    def _setup_routes(self):
        @self.app.route('/health', methods=['GET'])
        def health():
            clients = self._client_statuses()
            all_connected = bool(clients) and all(c['connected'] for c in clients)
            overall_healthy = self.is_healthy and all_connected

            status_code = 200 if overall_healthy else 503
            response = {
                'status': 'healthy' if overall_healthy else 'unhealthy',
                'message': self.status_message,
                'clients': clients,
            }

            return jsonify(response), status_code

    def set_status(self, is_healthy: bool, message: str = "OK"):
        """Update health status"""
        self.is_healthy = is_healthy
        self.status_message = message

    def _self_ping_loop(self):
        """Periodically ping the health endpoint"""
        # Wait for server to start
        time.sleep(2)

        url = f"{self.config.server.ping_url}/health"
        log.info(f"Starting self-ping loop every {self.ping_interval} seconds")

        while True:
            time.sleep(self.ping_interval)
            try:
                requests.get(url, timeout=5)
            except Exception:
                pass  # Ignore errors

    def start(self):
        """Start the health check server in a background thread"""

        def run_server():
            log.info(f"Starting health check server on port {self.config.server.port}")
            self.app.run(host='0.0.0.0', port=self.config.server.port, debug=False, use_reloader=False)

        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()
        log.info(f"Health check server started on http://0.0.0.0:{self.config.server.port}/health")

        # Start self-ping if enabled
        if self.config.server.enable_self_ping:
            log.info("Self-ping enabled")
            threading.Thread(target=self._self_ping_loop, daemon=True).start()
        else:
            log.info("Self-ping disabled")
