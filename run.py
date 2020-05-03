import discord
from discord.ext import commands
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import spotipy.util as util
from youtube_search import YoutubeSearch
import random
import json
from ytdl import YTDLSource
import os
import asyncio

TOKEN = "NzA1MzAyNTk0ODA5MjMzNDU4.Xq3XZg.XuLJY9PpDvscYBqfeQ_qDuV5mb4"
client = commands.Bot(command_prefix = "$")
client.remove_command("help")

@client.event
async def on_ready():
    # Bot is logged in
    await client.change_presence(status=discord.Status.online, activity=discord.Game("$help"))
    print("Bot is online.")

sp = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials())

# Dictionaries and sets that match guild ids with corresponding data,
# store info for each guild
queues = {}
players = {}
playlists = {}
started = set()
currentTracks = {}

async def clearAudioFiles(guildId, stack=0):
    try:
        # Remove all files in guild's temp audio source folder
        dir = "temp/audioFiles/" + str(guildId)
        filelist = os.listdir(dir)
        for f in filelist:
            os.remove(os.path.join(dir, f))
    except PermissionError:
        # File is being used by a program, wait 1 second and try again recursively
        # Give up after 5 attempts
        if stack < 5:
            stack += 1
            await asyncio.sleep(1)
            await clearAudioFiles(guildId, stack=stack)       

async def stopPlaying(guildId):
    try:
        del queues[guildId] # Get rid of queue
        del currentTracks[guildId]
        started.remove(guildId) # Set state to not started
    except KeyError:
        pass

    # Clear audio files, not used anymore
    await clearAudioFiles(guildId)

# Skips song by stopping the current player, triggering the next song
@client.command()
async def skip(ctx):
    ctx.voice_client.stop()

@client.command()
async def stop(ctx):
    guild = ctx.message.guild

    # Disconnect from voice
    if ctx.voice_client:
        if ctx.voice_client.is_connected():
            await ctx.voice_client.disconnect()

    await stopPlaying(guild.id)

@client.command()
async def pause(ctx):
    if not ctx.voice_client.is_paused():
        ctx.voice_client.pause()
        await ctx.message.add_reaction('✅')
    
@client.command()
async def resume(ctx):
    if ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.message.add_reaction('✅')

@client.command()
async def current(ctx):
    guild = ctx.message.guild
    if guild.id in currentTracks:
        await ctx.send("Currently playing `%s`" % currentTracks[ctx.message.guild.id])
    else:
        await ctx.send("*No tracks currently playing.*")

# Get a list of playlists from user
@client.command()
async def get(ctx, uname):
    playlistIds = []
    
    embed = discord.Embed(
        colour = discord.Colour.green()
    )

    # Try block handles if user doesn't exist
    try:
        # userPlaylists is from JSON format
        userPlaylists = sp.user_playlists(uname)
        if len(userPlaylists['items']) > 0:
            # User has playlists
            desc = ""
            embed.set_author(name="%s's Playlists" % uname)
            for playlist in userPlaylists['items']:
                if playlist['owner']['id'] == uname:
                    # User owns this playlist, add playlist to description and list of playlist ids
                    desc += "\n" + str(len(playlistIds) + 1) + ": " + playlist['name']
                    playlistIds.append(playlist['id'])

            guild = ctx.message.guild
            playlists[guild.id] = playlistIds # Set as guild's selected playlists
            embed.description = desc # Set display as playlist list
            embed.set_footer(text="Use $play <number> to play one of %s's playlists" % uname)
        else:
            embed.description = "This user has no playlists."
    except spotipy.SpotifyException:
        embed.description = "This user does not exist."

    await ctx.send(embed=embed)

@client.command()
async def play(ctx, arg):
    guild = ctx.message.guild
    if guild.id in currentTracks: # Checks if bot is already playing in this guild
        await ctx.send("*Currently playing a playlist. To play another playlist, use $stop first.*")
        return

    try: # Checks user input - typed a number
        index = int(arg) - 1
        if index < 0 or index >= len(playlists[guild.id]): # Checks if number is in range
            await ctx.send("*Number must be between 1 and %i.*" % len(playlists[guild.id]))
            return

    except ValueError:
        await ctx.send("*Choose a number corresponding to the playlist.*")
        return

    # Join audio channel
    if ctx.message.author.voice is None:
        await ctx.send("*Connect to a voice channel first.*")
        return

    channel = ctx.message.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.is_connected():
            await ctx.voice_client.disconnect()

    await channel.connect()

    # Get tracks in playlist
    playlist_tracks = sp.playlist_tracks(playlists[guild.id][index])
    tracks = [] # List of string queries for YouTube search
    for item in playlist_tracks['items']: # Generating the search queries
        track = item['track']
        tracks.append("%s %s" % (track['name'], track['artists'][0]['name']))

    random.shuffle(tracks) # Shuffle the tracks

    # Recursive method for playing each song in the queue.
    def endOfSong():
        if ctx.voice_client:
            if ctx.voice_client.is_connected():
                if len(queues[guild.id]) > 0:
                    # There are still songs in the queue, play the next one
                    player = queues[guild.id][0]['player']
                    track = queues[guild.id][0]['track']
                    queues[guild.id].pop(0) # Remove current from queue
                    players[guild.id] = player # Set guild's player to current player, so can pause/resume.
                    # Set the current track, so when $current command called, this is what it displays.
                    currentTracks[guild.id] = track
                    # Recursive lambda method to start the next song when last song finishes
                    ctx.voice_client.play(player, after=lambda e: endOfSong())
                    return
        # No more songs or got disconnected
        try:
            # Clean playing / queue info for guild
            del queues[guild.id] # Get rid of queue
            del currentTracks[guild.id]
            started.remove(guild.id) # Set state to not started
        except KeyError:
            # Guild info already empty
            pass

        try:
            # Remove files from temp folder
            dir = "temp/audioFiles/" + str(guild.id)
            filelist = os.listdir(dir)
            for f in filelist:
                os.remove(os.path.join(dir, f))
        except PermissionError:
            # Not async method, in case file is still being used it won't be deleted
            print("Leftover file in temp folder")

    # To get song url the YoutubeSearch method must be called, and sometimes it fails
    # and returns nothing. In this case, it tries to search the same song 2 more times
    # and then moves on to the next song if YoutubeSearch fails all three times.
    maxAttempts = 3
    attempt = 1
    while len(tracks) > 0:
        results = json.loads(YoutubeSearch(tracks[0], max_results=1).to_json())
        trackQuery = tracks[0] # For logging if YoutubeSearch fails
        try:
            url = "https://www.youtube.com" + results['videos'][0]['link']
            title = results['videos'][0]['title']
            tracks.pop(0) # Remove, get next track
            attempt = 1

            # Download song from YouTube, create player and add to queue
            player = await YTDLSource.from_url(url, guild.id, loop=client.loop, stream=False)
            # Checks if bot was disconnected from channel when downloading more songs,
            # needs to stop downloading songs and stop playing.
            if not ctx.voice_client:
                await stopPlaying(guild.id)
                break
            if not ctx.voice_client.is_connected():
                await stopPlaying(guild.id)
                break
            # Add player and track name to queue
            if guild.id in queues:
                queues[guild.id].append({
                    'player' : player,
                    'track' : title
                    })
            else:
                queues[guild.id] = [{
                    'player' : player,
                    'track' : title
                    }]

            # After first song is downloaded, start playing. Other songs can be downloaded
            # in the background.
            if not guild.id in started:
                started.add(guild.id)
                endOfSong()
        except IndexError:
            # YoutubeSearch failed, try again with same query
            print("Song couldn't be found on YouTube:")
            print(trackQuery)
            print("(Attempt %i out of %i)" % (attempt, maxAttempts))
            if attempt == maxAttempts:
                # Give up, move to next track
                tracks.pop(0)
                attempt = 1
            else:
                attempt += 1

@client.command()
async def help(ctx):
    embed = discord.Embed(
        title = "How to use Spotify on Discord",
        colour = discord.Colour.green(),
        description = """Spotify on Discord is like other music bots, \
            but instead of playing YouTube videos, it plays Spotify playlists. \
            No need to get Spotify Premium, because Spotify on Discord \
            already has ***NO ADS AND UNLIMITED SKIPS!!*** Here's how you use it:"""
    )
    embed.add_field(inline=False, name="$get <username on spotify>", value="""Lists the playlists \
        owned but the user. If you signed up on Spotify through a provider such as \
        Facebook, follow this link for instructions on how to get your Spotify username:
        https://community.spotify.com/t5/Accounts/How-do-i-find-my-username-when-using-Facebook-login/m-p/1268764#M183681""")
    embed.add_field(inline=False, name="$play <number>", value="Plays the corresponding playlist.")
    embed.add_field(inline=False, name="$pause", value="Pauses the player.")
    embed.add_field(inline=False, name="$resume", value="Resumes the player.")
    embed.add_field(inline=False, name="$skip", value="Skips the current song.")
    embed.add_field(inline=False, name="$stop", value="Stops the player and cancels the playlist.")
    await ctx.send(embed=embed)

# Used for debugging
@client.command()
async def debug(ctx, arg):
    if arg == "queues":
        await ctx.send(queues)
    elif arg == "playlists":
        await ctx.send(playlists)
    elif arg == "players":
        await ctx.send(players)

client.run(TOKEN)