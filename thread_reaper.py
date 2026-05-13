#!/usr/bin/env python3
"""
Thread Reaper — automatically closes inactive Discord threads.

Behavior:
- Scans all threads tracked in discord_threads.json
- For each thread, checks the last message timestamp (ignoring reaper warnings/closings)
- Phase 1: After 5 hours of inactivity → sends a warning with the closure deadline (UTC+8)
- Phase 2: After 8 hours of inactivity → closes the thread
- If a human sends a message after the warning, the warning state is cleared
- Runs every hour via cron
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
WARN_MINUTES = int(os.environ.get("THREAD_REAPER_WARN_MINUTES", "300"))
CLOSE_MINUTES = int(os.environ.get("THREAD_REAPER_CLOSE_MINUTES", "480"))

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
WARN_THRESHOLD = timedelta(minutes=WARN_MINUTES)
CLOSE_THRESHOLD = timedelta(minutes=CLOSE_MINUTES)
UTC8 = timezone(timedelta(hours=8))
closed_count = 0
warned_count = 0
skipped_count = 0


# Markers that identify reaper system messages (warning + closing)
REAPER_MARKERS = ("⏰", "🔒")


def is_reaper_message(msg):
    """Check if a message is a reaper system message (warning or closing)."""
    return (
        msg.author.id == client.user.id
        and any(msg.content.startswith(marker) for marker in REAPER_MARKERS)
    )


async def get_last_activity(thread):
    """Get the timestamp of the last real activity in a thread.
    Ignores reaper system messages (warnings/closings) only.
    Falls back to thread creation time if no real messages found.
    """
    async for msg in thread.history(limit=50):
        if not is_reaper_message(msg):
            ts = msg.created_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts, msg.author
    # No real messages found — use thread creation time
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

            # If already warned, check if we should close (1 hour total) or still waiting
            if state_status == "warned":
                warned_at_str = state.get("warned_at")
                if warned_at_str:
                    # Re-check last activity to see if someone posted after warning
                    last_activity2, _ = await get_last_activity(channel)
                    inactive_for2 = NOW - last_activity2 if last_activity2 else CLOSE_THRESHOLD

                    # If someone posted recently (within 5 hours), cancel warning
                    if inactive_for2 < WARN_THRESHOLD:
                        del reaper_state[str(thread_id)]
                        skipped_count += 1
                        continue

                # Close the thread
                try:
                    await channel.send(
                        "🔒 Closing this thread due to inactivity. "
                        "Feel free to open a new one if you need anything!"
                    )
                    await channel.edit(archived=True, reason="Auto-archived: 8 hours of inactivity")
                    del reaper_state[str(thread_id)]
                    closed_count += 1
                    print(f"CLOSED: thread {thread_id} ({channel.name})")
                except discord.Forbidden:
                    print(f"FORBIDDEN: cannot close thread {thread_id}")
                except Exception as e:
                    print(f"ERROR closing thread {thread_id}: {e}")
                continue

            # Get last real activity (ignore reaper warning/closing messages only)
            last_activity, last_author = await get_last_activity(channel)

            if last_activity is None:
                skipped_count += 1
                continue

            inactive_for = NOW - last_activity

            if inactive_for >= WARN_THRESHOLD and inactive_for < CLOSE_THRESHOLD:
                # Warn the thread — calculate deadline in UTC+8
                deadline_utc = last_activity + CLOSE_THRESHOLD
                deadline_utc8 = deadline_utc.astimezone(UTC8)
                time_str = deadline_utc8.strftime("%H:%M")
                try:
                    await channel.send(
                        f"⏰ This thread has been quiet for a while. "
                        f"If there's no further activity by **{time_str} (UTC+8)**, "
                        f"I'll close it to keep things tidy. "
                        f"Speak up if you'd like to keep it open!"
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
            elif inactive_for >= CLOSE_THRESHOLD:
                # Past close threshold without warning (edge case) — close directly
                try:
                    await channel.send(
                        "🔒 Closing this thread due to prolonged inactivity. "
                        "Feel free to open a new one if you need anything!"
                    )
                    await channel.edit(archived=True, reason="Auto-archived: 8 hours of inactivity")
                    if str(thread_id) in reaper_state:
                        del reaper_state[str(thread_id)]
                    closed_count += 1
                    print(f"CLOSED (direct): thread {thread_id} ({channel.name})")
                except discord.Forbidden:
                    print(f"FORBIDDEN: cannot close thread {thread_id}")
                except Exception as e:
                    print(f"ERROR closing thread {thread_id}: {e}")
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
    await process_threads()

    # Only notify if something actually happened
    if warned_count > 0 or closed_count > 0:
        print(f"Thread Reaper: warned={warned_count} closed={closed_count}")
    await client.close()


client.run(bot_token)
