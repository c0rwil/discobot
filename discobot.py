import os
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Configuration options for yt-dlp to download and extract audio
ydl_opts = {
    'format': 'bestaudio/best',
    'default_search': 'auto',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'opus',
        'preferredquality': '96',
    }],
}

# FFmpeg options for processing audio streams
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -analyzeduration 1000000 -probesize 5000000',
    'options': '-vn -bufsize 128k -b:a 96k',
    'executable': 'Z:\\FFmpeg\\bin\\ffmpeg.exe'
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
song_queue: list[str] = []
shuffle_mode: bool = False
shuffle_results: list[str] = []
current_shuffle_index: int = 0
volume_level: float = 1.0  # Default volume level (100%)

# ThreadPoolExecutor for running blocking tasks in separate threads
executor = ThreadPoolExecutor(max_workers=5)


def shorten_url(url: str) -> str:
    """
    Shortens a YouTube URL by extracting its video ID and converting it to a youtu.be format.
    """
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    if match:
        video_id = match.group(1)
        return f"https://youtu.be/{video_id}"
    return url  # Return the original URL if no match is found


async def run_blocking_task(task: Callable, *args, **kwargs):
    """
    Utility function to run a blocking task in a separate thread.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, task, *args, **kwargs)


@bot.command()
async def play(ctx: commands.Context, *, query: str) -> None:
    """
    Command to play music from a YouTube URL, playlist link, or search query.
    """
    if not ctx.voice_client:
        if ctx.author.voice:
            try:
                await ctx.author.voice.channel.connect()
            except Exception as e:
                await ctx.send("Error connecting to voice channel.")
                print(f"Error: {e}")
                return
        else:
            await ctx.send("You need to be connected to a voice channel.")
            return

    try:
        if "youtube.com/playlist" in query or "list=" in query:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                playlist_info = ydl.extract_info(query, download=False)
                for entry in playlist_info['entries']:
                    if "music video" not in entry['title'].lower():
                        song_queue.append(entry['webpage_url'])
                await ctx.send(f"Playlist added to queue: {playlist_info.get('title', 'Unnamed Playlist')}")
        elif "youtube.com" in query or "youtu.be" in query:
            song_queue.append(query)
        else:
            ydl_search_opts = {'quiet': True, 'default_search': 'ytsearch5', 'noplaylist': True}
            with youtube_dl.YoutubeDL(ydl_search_opts) as ydl:
                results = ydl.extract_info(query, download=False)
                filtered_entries = [entry for entry in results.get('entries', [])
                                    if "music video" not in entry['title'].lower() and "webpage_url" in entry]
                if not filtered_entries:
                    await ctx.send("No valid results found for the query.")
                    return

                shortened_urls = [shorten_url(entry['webpage_url']) for entry in filtered_entries]
                result_message = "\n".join(
                    [f"{i + 1}. {entry['title']} ({shortened_urls[i]})" for i, entry in enumerate(filtered_entries)]
                )
                message = await ctx.send(f"```Top 5 results:\n{result_message}```")

                emoji_numbers = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
                for emoji in emoji_numbers[:len(filtered_entries)]:
                    await message.add_reaction(emoji)

                def check(reaction, user):
                    return user == ctx.author and reaction.message.id == message.id and str(
                        reaction.emoji) in emoji_numbers

                try:
                    reaction, _ = await bot.wait_for("reaction_add", check=check, timeout=30.0)
                    selected_index = emoji_numbers.index(str(reaction.emoji))
                    selected_url = filtered_entries[selected_index]['webpage_url']
                    song_queue.append(selected_url)
                    await ctx.send(f"Added to queue: {filtered_entries[selected_index]['title']}")
                    if not ctx.voice_client.is_playing():
                        await play_next(ctx)
                except asyncio.TimeoutError:
                    await ctx.send("Selection timed out. Please try again.")
                    await message.clear_reactions()
    except Exception as e:
        await ctx.send("An error occurred while trying to play the song or playlist.")
        print(f"Error: {e}")


@bot.command()
async def volume(ctx: commands.Context, level: int) -> None:
    """
    Adjusts the bot's output audio volume directly via PCMVolumeTransformer.
    """
    if 0 <= level <= 100:
        volume_level = level / 100  # Scale to 0-1
        if ctx.voice_client and ctx.voice_client.source:
            ctx.voice_client.source.volume = volume_level
        await ctx.send(f"Volume set to {level}%")
    else:
        await ctx.send("Volume must be between 0 and 100.")


async def play_song(ctx: commands.Context, url: str) -> None:
    """
    Plays a song from the given URL using FFmpeg and yt-dlp, ensuring compatibility with Discord.
    """
    await run_blocking_task(play_song_blocking, ctx, url)


def play_song_blocking(ctx: commands.Context, url: str) -> None:
    """
    Blocking implementation of play_song to run in a separate thread.
    """
    vc = ctx.voice_client
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        audio_url = next((format['url'] for format in info['formats'] if format.get('acodec') != 'none'), None)
        if not audio_url:
            raise Exception("No audio URL found in the video data.")

        ffmpeg_options = FFMPEG_OPTIONS.copy()
        source = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
        source = discord.PCMVolumeTransformer(source, volume=volume_level)
        vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
            play_next(ctx) if not shuffle_mode else play_shuffle(ctx), ctx.bot.loop))


@bot.command()
async def shuffle(ctx: commands.Context, *, query: str) -> None:
    """
    Starts shuffle mode for a given artist or search query. Cycles through search results until stopped with :shufflestop.
    """
    global shuffle_mode, shuffle_results, current_shuffle_index
    if shuffle_mode:
        await ctx.send("Shuffle mode is already active. Use :shufflestop to stop it first.")
        return

    results = youtube_dl.YoutubeDL(ydl_opts).extract_info(f"ytsearch10:{query}", download=False)
    shuffle_results = [entry['webpage_url'] for entry in results.get('entries', []) if 'webpage_url' in entry]
    if not shuffle_results:
        await ctx.send("No results found for shuffle.")
        return

    shuffle_mode = True
    current_shuffle_index = 0
    await ctx.send(f"Starting shuffle for query: {query}")
    await play_shuffle(ctx)


async def play_shuffle(ctx: commands.Context) -> None:
    """
    Plays the next song in shuffle mode, skipping songs with "music video" in the title.
    """
    global shuffle_mode, shuffle_results, current_shuffle_index
    if not shuffle_mode or not shuffle_results:
        return

    while True:
        url = shuffle_results[current_shuffle_index]
        current_shuffle_index = (current_shuffle_index + 1) % len(shuffle_results)
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if "music video" not in info['title'].lower():
                break

    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be connected to a voice channel.")
            shuffle_mode = False
            return

    await play_song(ctx, url)


@bot.command()
async def shufflestop(ctx: commands.Context) -> None:
    """
    Stops the shuffle mode.
    """
    global shuffle_mode, shuffle_results
    if not shuffle_mode:
        await ctx.send("Shuffle mode is not active.")
        return

    shuffle_mode = False
    shuffle_results = []
    await ctx.send("Shuffle mode stopped.")


async def play_next(ctx: commands.Context) -> None:
    """
    Plays the next song in the queue.
    """
    if song_queue:
        url = song_queue.pop(0)
        await play_song(ctx, url)


@bot.command()
async def skip(ctx: commands.Context) -> None:
    """
    Skips the currently playing song.
    """
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()


@bot.command()
async def queue(ctx: commands.Context, *, query: str) -> None:
    """
    Adds a song to the queue.
    """
    if "youtube.com" in query or "youtu.be" in query:
        song_queue.append(query)
    else:
        results = youtube_dl.YoutubeDL(ydl_opts).extract_info(f"ytsearch:{query}", download=False)
        video = results['entries'][0] if results.get('entries') else None
        if video:
            url = video['webpage_url']
            song_queue.append(url)
            await ctx.send(f"Added to queue: {query}")


@bot.command()
async def join(ctx: commands.Context) -> None:
    """
    Joins the voice channel of the command author.
    """
    if ctx.author.voice:
        channel = ctx.message.author.voice.channel
        await channel.connect()
    else:
        await ctx.send("You are not connected to a voice channel.")


@bot.command()
async def show_queue(ctx: commands.Context) -> None:
    """
    Displays the current song queue.
    """
    if not song_queue:
        await ctx.send("The queue is currently empty.")
    else:
        queue_str = "\n".join(song_queue)
        await ctx.send(f"Current queue:\n{queue_str}")


@bot.event
async def on_ready() -> None:
    """
    Event that is called when the bot has finished logging in and setting up.
    """
    print(f'Logged in as {bot.user.name}')


bot.run(TOKEN)
