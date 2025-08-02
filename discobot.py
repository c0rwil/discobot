import os
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Dict

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# yt-dlp config for playback (not for search)
ydl_opts_play = {
    'format': 'bestaudio/best',
    'quiet': True,
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',
        'preferredquality': '96',
    }],
}

# yt-dlp config for search
ydl_opts_search = {
    'format': 'bestaudio/best',
    'default_search': 'ytsearch',
    'quiet': True,
    'extract_flat': True,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',
}

intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.voice_states = True
intents.message_content = True

bot = commands.Bot(command_prefix='::', intents=intents)

song_queue: List[Dict] = []
volume_level = 1.0
paused = False
executor = ThreadPoolExecutor(max_workers=2)
current_song: Dict | None = None


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
        def search():
            with youtube_dl.YoutubeDL(ydl_opts_search) as ydl:
                return ydl.extract_info(f"ytsearch5:{query}", download=False)

        info = await run_blocking_task(search)
        results = info.get('entries', [])[:5]

        if not results:
            await ctx.send("No results found.")
            return

        message = "**Select a song by reacting:**\n"
        for i, entry in enumerate(results, 1):
            title = entry.get('title', 'Unknown Title')
            uploader = entry.get('uploader', 'Unknown Uploader')
            duration = entry.get('duration', 'Unknown Duration')
            message += f"{i}. **{title}** by **{uploader}** ({duration} sec)\n"
        message += "\nReact with 1️⃣ - 5️⃣ to choose a song."
        vote_msg = await ctx.send(message)

        for i in range(len(results)):
            await vote_msg.add_reaction(f"{i + 1}\u20E3")

        def check(reaction, user):
            return (
                user == ctx.author and
                reaction.message.id == vote_msg.id and
                str(reaction.emoji) in [f"{i + 1}\u20E3" for i in range(len(results))]
            )

        reaction, _ = await bot.wait_for('reaction_add', check=check, timeout=20.0)

        emoji_map = {'1️⃣': 0, '2️⃣': 1, '3️⃣': 2, '4️⃣': 3, '5️⃣': 4}
        selected_index = emoji_map.get(str(reaction.emoji), int(str(reaction.emoji)[0]) - 1)
        selected_song = results[selected_index]

        song_info = {
            'url': f"https://www.youtube.com/watch?v={selected_song['id']}",
            'title': selected_song['title'],
            'uploader': selected_song['uploader'],
            'duration': selected_song.get('duration', 0)
        }

        song_queue.append(song_info)
        await ctx.send(f"✅ Added: **{song_info['title']}** by **{song_info['uploader']}**")

        if not ctx.voice_client.is_playing():
            await play_next(ctx)

    except asyncio.TimeoutError:
        await ctx.send("⏳ Timed out waiting for reaction.")
    except Exception as e:
        await ctx.send("❌ An error occurred while processing your request.")
        print(f"Error: {e}")


async def play_next(ctx):
    if song_queue and not paused:
        song = song_queue.pop(0)
        await play_song(ctx, song)


async def play_song(ctx, song):
    global paused, current_song
    paused = False
    current_song = song

    async with ctx.typing():
        def fetch():
            with youtube_dl.YoutubeDL(ydl_opts_play) as ydl:
                return ydl.extract_info(song['url'], download=False)

        info = await run_blocking_task(fetch)
        audio_url = next((f['url'] for f in info['formats'] if f.get('acodec') != 'none'), None)

        if not audio_url:
            await ctx.send("Error: Couldn't extract audio.")
            return

        source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
        source = discord.PCMVolumeTransformer(source, volume=volume_level)

        def after_playing(_):
            global current_song
            if not paused:
                current_song = None
                fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
                try:
                    fut.result()
                except Exception as e:
                    print(f"Error in after_playing: {e}")

        ctx.voice_client.play(source, after=after_playing)
        await ctx.send(
            f"🎶 Now playing: **{song['title']}** by **{song['uploader']}** ({song['duration']} sec)"
        )


@bot.command()
async def pause(ctx):
    global paused
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        paused = True
        await ctx.send("⏸️ Playback paused.")
    else:
        await ctx.send("Nothing is currently playing.")


@bot.command()
async def resume(ctx):
    global paused
    if ctx.voice_client and paused:
        ctx.voice_client.resume()
        paused = False
        await ctx.send("▶️ Playback resumed.")
    else:
        await ctx.send("Nothing is paused.")


@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Skipped current song.")
    else:
        await ctx.send("Nothing is playing.")


@bot.command()
async def nowplaying(ctx):
    if current_song:
        await ctx.send(
            f"🎵 Now playing: **{current_song['title']}** by **{current_song['uploader']}** ({current_song['duration']} sec)"
        )
    else:
        await ctx.send("No song is currently playing.")


@bot.command()
async def stop(ctx):
    global paused, current_song
    song_queue.clear()
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
    paused = False
    current_song = None
    await ctx.send("⏹️ Stopped playback and cleared the queue.")


@bot.command()
async def queue(ctx):
    if song_queue:
        queue_list = '\n'.join(
            [f"{i+1}. **{s['title']}** by **{s['uploader']}** ({s['duration']} sec)"
             for i, s in enumerate(song_queue)]
        )
        await ctx.send(f"📜 Queue:\n{queue_list}")
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
        await ctx.send("Volume must be 0–100.")


@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user}.')


bot.run(TOKEN)
