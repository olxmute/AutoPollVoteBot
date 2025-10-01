import logging
import threading
import time
from typing import Optional

import requests
from flask import Flask, jsonify

from src.config import AppConfig

log = logging.getLogger("health_check")


class HealthCheckServer:
    """Simple HTTP server for health checks"""

    def __init__(self, config: AppConfig, ping_interval: int = 20):
        self.config = config
        self.app = Flask(__name__)
        self.is_healthy = True
        self.status_message = "OK"
        self.ping_interval = ping_interval
        self._setup_routes()
        self.server_thread: Optional[threading.Thread] = None

    def _setup_routes(self):
        @self.app.route('/health', methods=['GET'])
        def health():
            status_code = 200 if self.is_healthy else 503
            return jsonify({
                'status': 'healthy' if self.is_healthy else 'unhealthy',
                'message': self.status_message
            }), status_code

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
        threading.Thread(target=self._self_ping_loop, daemon=True).start()
