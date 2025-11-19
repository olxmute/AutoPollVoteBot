import logging

import requests

log = logging.getLogger("forum-poll-voter")


class AutoPollNotifierBot:
    """
    A notification bot that sends messages via Telegram Bot API.
    """

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_base_url = f"https://api.telegram.org/bot{bot_token}"

    def send_message(self, chat_id: int, text: str) -> bool:
        """
        Send a message to a specific chat using Telegram Bot API.

        Args:
            chat_id: The Telegram user/chat ID to send the message to
            text: The message text to send

        Returns:
            True if the message was sent successfully, False otherwise
        """
        url = f"{self.api_base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            log.info("Notification sent successfully to chat_id %s", chat_id)
            return True
        except requests.exceptions.RequestException as e:
            log.error("Failed to send notification to chat_id %s: %s", chat_id, e)
            return False
