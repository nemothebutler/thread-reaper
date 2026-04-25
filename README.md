# Thread Reaper

Auto-closes inactive Discord threads after 1 hour of inactivity.

## How it works

- Runs every **15 minutes** via Hermes cron
- Scans all tracked threads from `discord_threads.json`
- Ignores bot's own messages — only tracks **human activity**
- After **1 hour of inactivity**:
  1. ⏰ Sends a warning message
  2. 🔒 Next run (15 min later) archives the thread

### Edge cases handled

- **Bot-only threads** (auto-thread conversations with no human messages) → uses thread creation time
- **Human messages after warning** → cancels the warning, resets timer
- **Already archived/deleted threads** → cleaned up from state

## Files

| File | Purpose |
|------|---------|
| `thread_reaper.py` | Main script, runs standalone with discord.py |
| `thread_reaper_state.json` | Tracks warned threads (auto-created, stored in `~/.hermes/`) |

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `THREAD_REAPER_MINUTES` | `60` | Inactivity threshold in minutes |
| `HERMES_HOME` | `~/.hermes` | Hermes config directory |

## Usage

```bash
# Run manually
cd ~/.hermes && source hermes-agent/venv/bin/activate
python scripts/thread_reaper.py

# With custom threshold (30 min)
THREAD_REAPER_MINUTES=30 python scripts/thread_reaper.py
```

## Deployment

The script is symlinked/copyed to `~/.hermes/scripts/thread_reaper.py` and runs via Hermes cron every 15 minutes.

## Dependencies

- Python 3.10+
- `discord.py` >= 2.0
- Bot token in `~/.hermes/.env` (`DISCORD_BOT_TOKEN`)
