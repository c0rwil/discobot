import os
import discord
from discord.ext import commands
import yt_dlp as youtube_dl
import asyncio
from dotenv import load_dotenv

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
song_queue = []

async def play_next(ctx):
    """
    Plays the next song in the queue.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
    """
    if song_queue:
        url = song_queue.pop(0)
        await play_song(ctx, url)

async def play_song(ctx, url):
    """
    Plays a song from the given URL using FFmpeg and yt-dlp.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
        url (str): The URL of the song to play.
    """
    vc = ctx.voice_client
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            audio_url = next((format['url'] for format in info['formats'] if format.get('acodec') != 'none'), None)
            if not audio_url:
                raise Exception("No audio URL found in the video data.")

            source = await discord.FFmpegOpusAudio.from_probe(audio_url, **FFMPEG_OPTIONS)
            vc.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), ctx.bot.loop))
    except Exception as e:
        print(f"Failed to process audio for URL {url}: {e}")
        if not vc.is_playing():
            await play_next(ctx)

@bot.command()
async def play(ctx, *, query):
    """
    Command to play music from a YouTube URL or search query.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
        query (str): The YouTube URL or search query for the song.
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
        if "youtube.com" in query or "youtu.be" in query:
            song_queue.append(query)
        else:
            results = youtube_dl.YoutubeDL(ydl_opts).extract_info(f"ytsearch:{query}", download=False)
            video = results['entries'][0] if results.get('entries') else None
            url = video['webpage_url'] if video else None
            song_queue.append(url)

        if not ctx.voice_client.is_playing():
            await play_next(ctx)
    except Exception as e:
        await ctx.send("An error occurred while trying to play the song.")
        print(f"Error: {e}")

@bot.command()
async def skip(ctx):
    """
    Skips the currently playing song.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
    """
    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

@bot.command()
async def queue(ctx, *, query):
    """
    Adds a song to the queue.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
        query (str): The YouTube URL or search query to add to the queue.
    """
    if "youtube.com" in query or "youtu.be" in query:
        song_queue.append(query)
    else:
        results = youtube_dl.YoutubeDL(ydl_opts).extract_info(f"ytsearch:{query}", download=False)
        video = results['entries'][0] if results.get('entries') else None
        url = video['webpage_url'] if video else None
        song_queue.append(url)
    await ctx.send(f"Added to queue: {query}")

@bot.command()
async def join(ctx):
    """
    Joins the voice channel of the command author.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
    """
    if ctx.author.voice:
        channel = ctx.message.author.voice.channel
        await channel.connect()
    else:
        await ctx.send("You are not connected to a voice channel.")

@bot.command()
async def show_queue(ctx):
    """
    Displays the current song queue.

    Args:
        ctx (discord.ext.commands.Context): The context in which the command was called.
    """
    if not song_queue:
        await ctx.send("The queue is currently empty.")
    else:
        queue_str = "\n".join(song_queue)
        await ctx.send(f"Current queue:\n{queue_str}")

@bot.event
async def on_ready():
    """
    Event that is called when the bot has finished logging in and setting up.
    """
    print(f'Logged in as {bot.user.name}')

bot.run(TOKEN)
