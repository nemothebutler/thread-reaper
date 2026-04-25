#!/usr/bin/env python3
"""
Thread Reaper — automatically closes inactive Discord threads.

Behavior:
- Scans all threads tracked in discord_threads.json
- For each thread, checks the last non-bot message timestamp
- If inactive for >= INACTIVITY_MINUTES, sends a farewell message and archives it
- Tracks which threads have been warned vs closed to avoid duplicate actions
- Once warned, the next run will close the thread (unless a human sent a message)
"""

import json
import os
import asyncio
import discord
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/home/hermes/.hermes"))
THREADS_FILE = HERMES_HOME / "discord_threads.json"
REAPER_STATE_FILE = HERMES_HOME / "thread_reaper_state.json"
INACTIVITY_MINUTES = int(os.environ.get("THREAD_REAPER_MINUTES", "60"))

# Load bot token
env_path = HERMES_HOME / ".env"
bot_token = None
bot_user_id = None
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            bot_token = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

if not bot_token:
    print("ERROR: DISCORD_BOT_TOKEN not found")
    exit(1)

# Load tracked threads
tracked_threads = []
if THREADS_FILE.exists():
    try:
        tracked_threads = json.loads(THREADS_FILE.read_text())
    except json.JSONDecodeError:
        tracked_threads = []

# Load reaper state (warned threads + their timestamps)
reaper_state = {}
if REAPER_STATE_FILE.exists():
    try:
        reaper_state = json.loads(REAPER_STATE_FILE.read_text())
    except json.JSONDecodeError:
        reaper_state = {}


def save_state():
    REAPER_STATE_FILE.write_text(json.dumps(reaper_state, indent=2))


intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

NOW = datetime.now(timezone.utc)
INACTIVITY_THRESHOLD = timedelta(minutes=INACTIVITY_MINUTES)
closed_count = 0
warned_count = 0
skipped_count = 0


async def get_last_human_activity(thread):
    """Get the timestamp of the last non-bot message in a thread.
    Returns (timestamp, author) or falls back to thread creation time.
    """
    async for msg in thread.history(limit=50):
        if msg.author.id != client.user.id:
            ts = msg.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts, msg.author
    # No human messages — use thread creation time
    created = thread.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created, None


async def process_threads():
    global closed_count, warned_count, skipped_count

    for thread_id_str in tracked_threads:
        thread_id = int(thread_id_str)
        state = reaper_state.get(str(thread_id), {})
        state_status = state.get("status", "active")

        try:
            channel = client.get_channel(thread_id)
            if channel is None:
                channel = await client.fetch_channel(thread_id)

            if not isinstance(channel, discord.Thread):
                skipped_count += 1
                continue

            # Skip already archived threads
            if channel.archived:
                if str(thread_id) in reaper_state:
                    del reaper_state[str(thread_id)]
                skipped_count += 1
                continue

            # If already warned, close it on this run
            if state_status == "warned":
                try:
                    await channel.send(
                        "🔒 Closing this thread due to inactivity. "
                        "Feel free to open a new one if you need anything!"
                    )
                    await channel.edit(archived=True, reason="Auto-archived: 1 hour of inactivity")
                    del reaper_state[str(thread_id)]
                    closed_count += 1
                    print(f"CLOSED: thread {thread_id} ({channel.name})")
                except discord.Forbidden:
                    print(f"FORBIDDEN: cannot close thread {thread_id}")
                except Exception as e:
                    print(f"ERROR closing thread {thread_id}: {e}")
                continue

            # Get last human activity (ignore bot's own messages)
            last_activity, last_author = await get_last_human_activity(channel)

            if last_activity is None:
                skipped_count += 1
                continue

            inactive_for = NOW - last_activity

            if inactive_for >= INACTIVITY_THRESHOLD:
                # Warn the thread
                try:
                    await channel.send(
                        "⏰ This thread has been inactive for over an hour. "
                        "I'll close it shortly to keep things tidy. "
                        "Just send a message if you'd like to keep it open!"
                    )
                    reaper_state[str(thread_id)] = {
                        "status": "warned",
                        "warned_at": NOW.isoformat(),
                        "thread_name": channel.name,
                    }
                    warned_count += 1
                    print(f"WARNED: thread {thread_id} ({channel.name}) — inactive for {inactive_for}")
                except discord.Forbidden:
                    print(f"FORBIDDEN: cannot message thread {thread_id}")
                except Exception as e:
                    print(f"ERROR warning thread {thread_id}: {e}")
            else:
                # Thread is active — clear any warning state
                if str(thread_id) in reaper_state:
                    del reaper_state[str(thread_id)]

        except discord.NotFound:
            if str(thread_id) in reaper_state:
                del reaper_state[str(thread_id)]
            skipped_count += 1
        except discord.Forbidden:
            skipped_count += 1
        except Exception as e:
            print(f"ERROR processing thread {thread_id}: {e}")

    save_state()


@client.event
async def on_ready():
    global closed_count, warned_count, skipped_count
    print(f"Thread Reaper connected as {client.user}")
    print(f"Checking {len(tracked_threads)} tracked threads (threshold: {INACTIVITY_MINUTES} min)")
    await process_threads()
    print(f"\nDone! warned={warned_count} closed={closed_count} skipped={skipped_count}")
    await client.close()


client.run(bot_token)
