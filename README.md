# AutoPollVoteBot

A Telegram bot that automatically votes in forum polls based on configurable schedules and event criteria.

## Features

- Monitors Telegram forum topics for new polls
- Automatically votes based on event schedules (type, day, start time)
- Parses event information from topic names
- Only votes on future events that match configured schedules
- Health check endpoint for monitoring
- Docker support

## Requirements

- Python 3.11+
- Telegram API credentials (API ID and API Hash)
- Active Telegram user session

## Installation

1. Clone the repository:

```bash
git clone https://github.com/olxmute/AutoPollVoteBot
cd AutoPollVoteBot
```

2. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. Set up environment variables (see Configuration section)

## Configuration

Create a `.env` file with the following variables:

```env
# Pyrogram API credentials (get from https://my.telegram.org)
PYROGRAM_API_ID=your_api_id
PYROGRAM_API_HASH=your_api_hash
SESSION_NAME=my_user_session

# Group settings
GROUP_CHAT_ID=your_chat_id
GROUP_VOTE_OPTION=Go!

# Event schedule (DSL format)
EVENT_SCHEDULE=your_schedule_here

# Server settings
PING_URL=http://localhost:8080 # or your server URL
PORT=8080
```

### Schedule Configuration

The `EVENT_SCHEDULE` uses a DSL format to define when to vote. Example:

```
EVENT_SCHEDULE=Game wed; Game sat 11:00; Training tue
```

This configures the bot to vote on:

- Game events on Wednesday (at any time)
- Game events on Saturday at 18:00
- Training events on Tuesday (at any time)

## Usage

### Running Locally

```bash
python app.py
```

### Running with Docker

1. Build the image:

```bash
docker build -t autopollvotebot .
```

2. Run the container:

```bash
docker run -d \
  --name autopollvotebot \
  -p 8080:8080 \
  --env-file .env \
  -v $(pwd)/my_user_session.session:/app/my_user_session.session \
  autopollvotebot
```

## How It Works

1. The bot monitors a specific Telegram forum/group for new polls
2. When a poll is posted in a forum topic, the bot:
    - Parses the topic name to extract event information (type, date, time)
    - Checks if the event date is in the future
    - Verifies if the event matches any configured schedule
    - Automatically votes for the configured option (e.g., "Go!")
    - Skips voting if already voted

3. Health check endpoint is available at `http://localhost:8080/health`
