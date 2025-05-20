import os
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Dict

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
song_queue: List[Dict] = []
volume_level = 1.0  # Default volume level (100%)
paused = False
crossfade_seconds = 5  # Length of crossfade in seconds
executor = ThreadPoolExecutor(max_workers=2)


async def run_blocking_task(task: Callable, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, task, *args, **kwargs)


@bot.command()
async def join(ctx):
    if ctx.author.voice:
        channel = ctx.author.voice.channel
        if ctx.voice_client is None:
            await channel.connect()
        else:
            await ctx.voice_client.move_to(channel)
    else:
        await ctx.send("You need to be in a voice channel.")


@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    else:
        await ctx.send("I'm not in a voice channel.")


@bot.command()
async def play(ctx, *, query: str):
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be in a voice channel.")
            return

    try:
        ydl_opts['extract_flat'] = True  # Ensure we get a list of results
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch5:{query}", download=False)

            # Extract up to 5 results
            results = info.get('entries', [])[:5]

            if not results:
                await ctx.send("No results found.")
                return

            message = "**Select a song by reacting:**\n"
            for i, entry in enumerate(results, 1):
                title = entry.get('title', 'Unknown Title')
                uploader = entry.get('uploader', 'Unknown Uploader')
                duration = entry.get('duration', 'Unknown Duration')
                message += f"{i}. **{title}** by **{uploader}** ({duration} seconds)\n"
            message += "\nReact with 1️⃣ - 5️⃣ to choose a song."
            vote_msg = await ctx.send(message)

            # Add number reactions concurrently
            async def add_reactions():
                for i in range(1, len(results) + 1):
                    await vote_msg.add_reaction(f"{i}\u20E3")

            # Start adding reactions and listen for votes at the same time
            await asyncio.gather(
                add_reactions(),
                handle_reaction(ctx, vote_msg, results)
            )

    except Exception as e:
        await ctx.send("Error while processing the request.")
        print(f"Error: {e}")


async def handle_reaction(ctx, vote_msg, results):
    try:
        def check(reaction, user):
            return (
                    reaction.message.id == vote_msg.id and
                    user == ctx.author and
                    str(reaction.emoji) in [f"{i}\u20E3" for i in range(1, len(results) + 1)]
            )

        reaction, _ = await bot.wait_for('reaction_add', check=check)
        selected_index = int(reaction.emoji[0]) - 1
        selected_song = results[selected_index]

        # Convert to full URL for playback
        song_info = {
            'url': f"https://www.youtube.com/watch?v={selected_song['id']}",
            'title': selected_song['title'],
            'uploader': selected_song['uploader'],
            'duration': selected_song.get('duration', 'Unknown Duration')
        }

        song_queue.append(song_info)
        await ctx.send(f"Added to queue: **{song_info['title']}** by **{song_info['uploader']}**")

        # Play if nothing is currently playing
        if not ctx.voice_client.is_playing():
            await play_next(ctx)

    except asyncio.TimeoutError:
        await ctx.send("No selection made in time. Please try again.")


async def play_next(ctx):
    if song_queue and not paused:
        song = song_queue.pop(0)
        await play_song(ctx, song)


async def play_song(ctx, song):
    global paused
    paused = False

    async with ctx.typing():
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(song['url'], download=False)
            audio_url = next((f['url'] for f in info['formats'] if f.get('acodec') != 'none'), None)
            if not audio_url:
                await ctx.send("Error: No valid audio found.")
                return

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=volume_level)

        # Crossfade logic
        def after_playing(_):
            if song_queue:
                next_song = song_queue[0]
                asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
                if len(song_queue) > 1:
                    asyncio.run_coroutine_threadsafe(
                        ctx.send(f"🎶 Up next: **{next_song['title']}** by **{next_song['uploader']}**"), bot.loop
                    )

        ctx.voice_client.play(source, after=after_playing)
        await ctx.send(f"🎶 Now playing: **{song['title']}** by **{song['uploader']}** ({song['duration']} seconds)")

        # Crossfade into the next song
        song_duration = int(song.get('duration', 0))
        if song_duration > crossfade_seconds and song_queue:
            await asyncio.sleep(max(0, song_duration - crossfade_seconds))
            await play_next(ctx)


@bot.command()
async def pause(ctx):
    global paused
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        paused = True
        await ctx.send("⏸️ Paused playback.")
    else:
        await ctx.send("No song is currently playing.")


@bot.command()
async def resume(ctx):
    global paused
    if ctx.voice_client and paused:
        ctx.voice_client.resume()
        paused = False
        await ctx.send("▶️ Resumed playback.")
    else:
        await ctx.send("No song is currently paused.")


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
        queue_list = '\n'.join(
            [f"{idx + 1}. **{song['title']}** by **{song['uploader']}** ({song['duration']} seconds)"
             for idx, song in enumerate(song_queue)]
        )
        await ctx.send(f"**Current Queue:**\n{queue_list}")
    else:
        await ctx.send("Queue is empty.")


@bot.command()
async def volume(ctx, level: int):
    global volume_level
    if 0 <= level <= 100:
        volume_level = level / 100
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = volume_level
        await ctx.send(f"🔊 Volume set to {level}%")
    else:
        await ctx.send("Volume must be between 0 and 100.")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')


bot.run(TOKEN)
