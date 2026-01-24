import os
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
import re
import random
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Configuration options for yt-dlp to download and extract audio
ydl_opts = {
    'format': 'bestaudio/best',
    'default_search': 'ytsearch',
    'quiet': True,
    'extract_flat': False,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',
        'preferredquality': '96',
    }],
}

# FFmpeg options for processing audio streams
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

# Define which intents are needed for the bot
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.voice_states = True
intents.message_content = True

# Initialize bot with defined command prefix and intents
bot = commands.Bot(command_prefix='::', intents=intents)

# Song queue to manage the songs
song_queue = []
shuffle_mode = False
shuffle_results = []
volume_level = 1.0  # Default volume level (100%)
executor = ThreadPoolExecutor(max_workers=5)
VOICE_CONNECT_RETRIES = 3
VOICE_CONNECT_DELAY = 1.5


def shorten_url(url: str) -> str:
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    return f"https://youtu.be/{match.group(1)}" if match else url


async def run_blocking_task(task: Callable, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, task, *args, **kwargs)


async def fetch_info(query: str) -> dict:
    def _extract():
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(query, download=False)

    return await run_blocking_task(_extract)


def build_queue_entries(info: dict) -> list[dict]:
    if 'entries' not in info:
        return [{
            'url': info.get('webpage_url') or info.get('url'),
            'title': info.get('title') or 'Unknown title',
        }]

    entries = [entry for entry in info['entries'] if entry]
    if info.get('_type') == 'playlist':
        return [{
            'url': entry.get('webpage_url') or entry.get('url'),
            'title': entry.get('title') or 'Unknown title',
        } for entry in entries if entry.get('webpage_url') or entry.get('url')]

    if entries:
        entry = entries[0]
        return [{
            'url': entry.get('webpage_url') or entry.get('url'),
            'title': entry.get('title') or 'Unknown title',
        }]
    return []


@bot.command()
async def join(ctx):
    await ensure_voice(ctx)


@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    else:
        await ctx.send("I'm not in a voice channel.")


@bot.command()
async def play(ctx, *, query: str):
    if not await ensure_voice(ctx):
        return

    try:
        info = await fetch_info(query)
        entries = build_queue_entries(info)
        if not entries:
            await ctx.send("No results found.")
            return

        song_queue.extend(entries)
        if len(entries) == 1:
            await ctx.send(f"Added to queue: {entries[0]['title']} ({shorten_url(entries[0]['url'])})")
        else:
            await ctx.send(f"Added {len(entries)} tracks to the queue.")
        if not ctx.voice_client.is_playing():
            await play_next(ctx)
    except Exception as e:
        await ctx.send("Error while processing the request.")
        print(f"Error: {e}")


async def play_next(ctx):
    global shuffle_mode, shuffle_results
    if shuffle_mode:
        if not shuffle_results:
            shuffle_mode = False
            await ctx.send("Shuffle complete.")
            return
        entry = shuffle_results.pop(0)
        await play_song(ctx, entry)
        return

    if song_queue:
        entry = song_queue.pop(0)
        await play_song(ctx, entry)


async def play_song(ctx, entry: dict):
    async with ctx.typing():
        vc = ctx.voice_client

        info = await fetch_info(entry['url'])
        audio_url = info.get('url')
        if not audio_url:
            audio_url = next((f['url'] for f in info.get('formats', []) if f.get('acodec') != 'none'), None)
        if not audio_url:
            await ctx.send("Error: No valid audio found.")
            return

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=volume_level)

        vc.play(source, after=lambda _: asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop))
        await ctx.send(f"Now playing: {entry['title']} ({shorten_url(entry['url'])})")


@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("Skipped song.")
    else:
        await ctx.send("No song is playing.")


@bot.command()
async def queue(ctx):
    if song_queue:
        queue_list = '\n'.join([f"{song['title']} ({shorten_url(song['url'])})" for song in song_queue])
        await ctx.send(f"**Song Queue:**\n{queue_list}")
    else:
        await ctx.send("Queue is empty.")


@bot.command()
async def volume(ctx, level: int):
    global volume_level
    if 0 <= level <= 100:
        volume_level = level / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = volume_level
        await ctx.send(f"Volume set to {level}%")
    else:
        await ctx.send("Volume must be between 0 and 100.")


@bot.command()
async def shuffle(ctx):
    global shuffle_mode, shuffle_results
    if not song_queue:
        await ctx.send("The queue is empty.")
        return
    shuffle_mode = True
    shuffle_results = song_queue[:]
    song_queue.clear()
    random.shuffle(shuffle_results)
    await ctx.send("Shuffle mode activated.")
    await play_next(ctx)


@bot.command()
async def shufflestop(ctx):
    global shuffle_mode, shuffle_results
    if not shuffle_mode:
        await ctx.send("Shuffle mode is not active.")
        return
    shuffle_mode = False
    if shuffle_results:
        song_queue[:0] = shuffle_results
        shuffle_results = []
    await ctx.send("Shuffle mode stopped.")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')


bot.run(TOKEN)
