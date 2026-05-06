import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select
from discord import app_commands
import json
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta
import random
import threading
from flask import Flask, render_template, jsonify, request
import time
import re
import math
import hashlib
import string

# ============================================================
# TOKEN & SECRETS
# ============================================================
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")  # For Roblox AI
DASHBOARD_SECRET = os.environ.get("DASHBOARD_SECRET", "changeme")

# ============================================================
# CONFIG SYSTEM
# ============================================================
CONFIG_FILE = "config.json"
DATA_FILE = "data.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}

def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get(guild_id, key, default=None):
    return load_config().get(str(guild_id), {}).get(key, default)

def set_config(guild_id, key, value):
    config = load_config()
    if str(guild_id) not in config:
        config[str(guild_id)] = {}
    config[str(guild_id)][key] = value
    save_config(config)

# ============================================================
# BOT SETUP
# ============================================================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
tree = bot.tree

# ---- Global Data Stores ----
applications_data = {}
warnings_data = {}
notes_data = {}
activity_log = []
giveaway_data = {}
bot_start_time = None
roblox_links = {}
xp_data = {}
xp_cooldowns = {}
economy_data = {}
snipe_data = {}
edit_snipe_data = {}
ticket_data = {}
automod_data = {}
mod_log_data = []
voice_log = []
afk_data = {}
ban_log_data = []
verification_data = {}
bot_shutdown_flag = False
timers_data = {}           # timer_id -> {user_id, channel_id, end_time, label}
counting_data = {}         # guild_id -> {count, last_user, channel_id, broken_by}
bug_reports = []           # list of bug report dicts
premium_users = {}         # user_id -> {expires, tier}
ai_conversations = {}      # user_id -> list of message dicts
staff_codes = {}           # guild_id -> {code, generated_at, generated_by}
daily_staff_codes = {}     # user_id -> {code, date, used}
dm_reply_map = {}          # user_id -> channel_id (for DM replies)
reaction_roles = {}        # guild_id -> {message_id -> {emoji -> role_id}}
slowmode_log = {}
lockdown_backup = {}       # guild_id -> {channel_id -> old_permissions}
nickname_logs = []
bot_cmd_cooldowns = {}
reminders_data = []        # list of {user_id, channel_id, time, reminder, sent}
polls_active = {}
birthday_data = {}         # user_id -> "MM-DD"
warnings_threshold = {}    # guild_id -> {count: action}
temp_bans = []             # list of {user_id, guild_id, unban_at, reason}
mute_logs = []
starboard_data = {}        # guild_id -> {message_id -> star_count}
fun_stats = {}             # user_id -> {games_played, games_won, ...}
custom_commands = {}       # guild_id -> {command_name -> response}
join_to_create_vcs = {}    # channel_id -> creator user_id
user_created_vcs = {}      # channel_id -> user_id
status_pages = {}          # guild_id -> message_id for status embed
SHUTDOWN_CONFIRM_CODES = {}
report_threads = {}

# ---- Level/XP ----
def get_level(xp):
    return int((xp / 50) ** 0.5)

def xp_for_level(level):
    return 50 * level ** 2

# ---- Logging helpers ----
def add_mod_log(action, target, by, reason="", color="#5865f2"):
    mod_log_data.insert(0, {
        "action": action, "target": target, "by": by,
        "reason": reason, "color": color,
        "time": datetime.now().strftime("%H:%M · %d %b"),
    })
    while len(mod_log_data) > 500:
        mod_log_data.pop()

def get_economy(user_id, name="Unknown"):
    if user_id not in economy_data:
        economy_data[user_id] = {"balance": 0, "last_daily": None, "last_work": None,
                                  "total_earned": 0, "name": name, "last_rob": None,
                                  "bank": 0, "inventory": []}
    return economy_data[user_id]

def add_activity(icon, action, detail=""):
    activity_log.insert(0, {
        "icon": icon, "action": action, "detail": detail,
        "time": datetime.now().strftime("%H:%M · %d %b"),
    })
    while len(activity_log) > 150:
        activity_log.pop()

def uptime_str():
    if not bot_start_time:
        return None
    delta = datetime.now() - bot_start_time
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def is_staff(ctx):
    staff_id = get(ctx.guild.id, "admin_role")
    return ctx.author.guild_permissions.administrator or (staff_id and staff_id in [r.id for r in ctx.author.roles])

def is_staff_interaction(interaction):
    staff_id = get(interaction.guild.id, "admin_role")
    return interaction.user.guild_permissions.administrator or (staff_id and staff_id in [r.id for r in interaction.user.roles])

def get_bot_cmd_channel(guild_id):
    return get(guild_id, "bot_commands_channel")

async def check_bot_channel(interaction: discord.Interaction):
    """Returns True if command is allowed in this channel."""
    if interaction.user.guild_permissions.administrator:
        return True
    ch_id = get_bot_cmd_channel(interaction.guild.id)
    if ch_id and interaction.channel.id != ch_id:
        ch = interaction.guild.get_channel(ch_id)
        await interaction.response.send_message(
            f"❌ Please use bot commands in {ch.mention if ch else 'the designated bot channel'}!",
            ephemeral=True
        )
        return False
    return True

def generate_staff_code():
    """Generate a random alphanumeric staff code."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

async def verify_staff_code(interaction: discord.Interaction):
    """Check if a staff member has verified with their daily code."""
    guild_id = str(interaction.guild.id)
    user_id = interaction.user.id
    today = datetime.now().strftime("%Y-%m-%d")
    
    if interaction.user.guild_permissions.administrator:
        return True  # Admins bypass staff code
    
    user_daily = daily_staff_codes.get(user_id, {})
    if user_daily.get("date") == today and user_daily.get("verified"):
        return True
    
    return False

# ============================================================
# BEAUTIFUL EMBED HELPERS
# ============================================================
def success_embed(title, description="", color=0x2ecc71):
    e = discord.Embed(title=f"✅ {title}", description=description, color=color)
    e.set_footer(text="Young Boy Studios")
    return e

def error_embed(title, description=""):
    e = discord.Embed(title=f"❌ {title}", description=description, color=0xe74c3c)
    e.set_footer(text="Young Boy Studios")
    return e

def info_embed(title, description="", color=0x3498db):
    e = discord.Embed(title=f"ℹ️ {title}", description=description, color=color)
    e.set_footer(text="Young Boy Studios · Use /help for commands")
    return e

def mod_embed(title, description="", color=0xe67e22):
    e = discord.Embed(title=f"⚖️ {title}", description=description, color=color)
    e.timestamp = discord.utils.utcnow()
    e.set_footer(text="YBS Moderation System")
    return e

# ============================================================
# EXTENDED CHANNEL CONFIG KEYS
# ============================================================
CHANNEL_CONFIG_KEYS = [
    ("welcome_channel", "👋 Welcome Channel"),
    ("apply_channel", "📋 Apply Channel"),
    ("applications_channel", "📨 Staff Applications Channel"),
    ("logs_channel", "📋 General Logs Channel"),
    ("general_channel", "💬 General Channel"),
    ("announcements_channel", "📢 Announcements Channel"),
    ("giveaway_channel", "🎉 Giveaway Channel"),
    ("levelup_channel", "⬆️ Level-Up Channel"),
    ("rank_channel", "🏅 Rank/Leaderboard Channel"),
    ("verify_channel", "✅ Verification Channel"),
    ("mod_channel", "🔨 Mod-Log Channel"),
    ("bot_commands_channel", "🤖 Bot Commands Channel"),
    ("counting_channel", "🔢 Counting Channel"),
    ("suggestions_channel", "💡 Suggestions Channel"),
    ("bug_reports_channel", "🐛 Bug Reports Channel"),
    ("starboard_channel", "⭐ Starboard Channel"),
    ("staff_channel", "👮 Staff-Only Channel"),
    ("premium_channel", "👑 Premium Channel"),
    ("nickname_log_channel", "📝 Nickname Logs Channel"),
    ("join_to_create_channel", "🔊 Join-to-Create VC Channel"),
    ("ticket_category", "🎫 Ticket Category"),
    ("birthday_channel", "🎂 Birthday Channel"),
    ("ai_channel", "🤖 Roblox AI Channel"),
]

ROLE_CONFIG_KEYS = [
    ("admin_role", "⚙️ Admin / Staff Role"),
    ("member_role", "👤 Member Role"),
    ("muted_role", "🔇 Muted Role"),
    ("builder_role", "🔨 Builder Role"),
    ("scripter_role", "💻 Scripter Role"),
    ("modeller_role", "🎨 Modeller Role"),
    ("ui_role", "🖥️ UI Designer Role"),
    ("verified_role", "✅ Verified Role"),
    ("level5_role", "⭐ Level 5 Role Reward"),
    ("level10_role", "🌟 Level 10 Role Reward"),
    ("level25_role", "💎 Level 25 Role Reward"),
    ("premium_role", "👑 Premium Role"),
    ("staff_role", "👮 Staff Role"),
    ("junior_mod_role", "🟡 Junior Mod Role"),
    ("senior_mod_role", "🔴 Senior Mod Role"),
    ("booster_role", "💜 Server Booster Role"),
    ("birthday_role", "🎂 Birthday Role"),
]

# ============================================================
# SETUP MENU SYSTEM (ENHANCED)
# ============================================================
class ChannelTypeSelect(Select):
    def __init__(self, page=0):
        chunk = CHANNEL_CONFIG_KEYS[page*24:(page+1)*24]
        options = [discord.SelectOption(label=label[:100], value=key) for key, label in chunk]
        super().__init__(placeholder="Which channel to configure?", options=options)
    async def callback(self, interaction):
        config_key = self.values[0]
        label = next((l for k, l in CHANNEL_CONFIG_KEYS if k == config_key), config_key)
        await interaction.response.send_message(
            embed=info_embed(f"Configure: {label}", "Select the channel below:"),
            view=ChannelPickerView(config_key, label), ephemeral=True
        )

class ChannelTypeSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ChannelTypeSelect(0))

class ChannelPickerSelect(discord.ui.ChannelSelect):
    def __init__(self, config_key, label):
        super().__init__(placeholder="Select channel...", channel_types=[discord.ChannelType.text])
        self.config_key = config_key
        self.label_text = label
    async def callback(self, interaction):
        set_config(interaction.guild.id, self.config_key, self.values[0].id)
        await interaction.response.send_message(
            embed=success_embed("Channel Configured", f"**{self.label_text}** → {self.values[0].mention}"),
            ephemeral=True
        )

class ChannelPickerView(View):
    def __init__(self, config_key, label):
        super().__init__(timeout=120)
        self.add_item(ChannelPickerSelect(config_key, label))

class RoleTypeSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=label[:100], value=key) for key, label in ROLE_CONFIG_KEYS]
        super().__init__(placeholder="Which role to configure?", options=options)
    async def callback(self, interaction):
        config_key = self.values[0]
        label = next(l for k, l in ROLE_CONFIG_KEYS if k == config_key)
        await interaction.response.send_message(
            embed=info_embed(f"Configure: {label}", "Select the role below:"),
            view=RolePickerView(config_key, label), ephemeral=True
        )

class RoleTypeSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RoleTypeSelect())

class RolePickerSelect(discord.ui.RoleSelect):
    def __init__(self, config_key, label):
        super().__init__(placeholder="Select role...")
        self.config_key = config_key
        self.label_text = label
    async def callback(self, interaction):
        set_config(interaction.guild.id, self.config_key, self.values[0].id)
        await interaction.response.send_message(
            embed=success_embed("Role Configured", f"**{self.label_text}** → {self.values[0].mention}"),
            ephemeral=True
        )

class RolePickerView(View):
    def __init__(self, config_key, label):
        super().__init__(timeout=120)
        self.add_item(RolePickerSelect(config_key, label))

async def do_lockdown_setup(interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    pending = discord.utils.get(guild.roles, name="Pending")
    if not pending:
        pending = await guild.create_role(name="Pending", color=discord.Color.from_rgb(90, 90, 90), reason="YBS Lockdown")
    set_config(guild.id, "pending_role", pending.id)
    apply_id = get(guild.id, "apply_channel")
    apply_ch = guild.get_channel(apply_id) if apply_id else None
    count = 0
    for ch in guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
            try:
                if ch == apply_ch:
                    await ch.set_permissions(pending, read_messages=True, send_messages=False)
                else:
                    await ch.set_permissions(pending, read_messages=False)
                count += 1
            except Exception:
                pass
    await interaction.followup.send(
        embed=success_embed("Lockdown Configured", f"**Pending** role set on {count} channels."),
        ephemeral=True
    )
    add_activity("🔒", f"Lockdown setup by {interaction.user.display_name}", guild.name)

class SetupMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Set Channels", style=discord.ButtonStyle.blurple, row=0)
    async def set_channels(self, interaction, button):
        await interaction.response.send_message(
            embed=info_embed("Configure Channels", "Use the dropdown to pick which channel to set."),
            view=ChannelTypeSelectView(), ephemeral=True
        )

    @discord.ui.button(label="🎭 Set Roles", style=discord.ButtonStyle.green, row=0)
    async def set_roles(self, interaction, button):
        await interaction.response.send_message(
            embed=info_embed("Configure Roles", "Use the dropdown to pick which role to set."),
            view=RoleTypeSelectView(), ephemeral=True
        )

    @discord.ui.button(label="🔒 Setup Lockdown", style=discord.ButtonStyle.red, row=1)
    async def setup_lockdown_btn(self, interaction, button):
        await do_lockdown_setup(interaction)

    @discord.ui.button(label="📊 View Config", style=discord.ButtonStyle.secondary, row=1)
    async def view_config_btn(self, interaction, button):
        config = load_config().get(str(interaction.guild.id), {})
        if not config:
            return await interaction.response.send_message(embed=error_embed("No Config", "Run `!setup` first!"), ephemeral=True)
        embed = discord.Embed(title="⚙️ Current Configuration", color=0x5865F2)
        embed.set_footer(text="Young Boy Studios Bot Config")
        for key, value in config.items():
            obj = interaction.guild.get_role(value) or interaction.guild.get_channel(value)
            embed.add_field(name=key.replace("_", " ").title(), value=obj.mention if obj else str(value), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📮 Post Apply Panel", style=discord.ButtonStyle.blurple, row=2)
    async def post_apply(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        channel_id = get(interaction.guild.id, "apply_channel")
        channel = bot.get_channel(channel_id) if channel_id else interaction.channel
        embed = discord.Embed(
            title="🚀 Join the Young Boy Studios Dev Team",
            description=(
                "We're looking for talented Roblox developers!\n\n"
                "**Available Roles:**\n"
                "🔨 **Builder** — Create stunning game environments\n"
                "💻 **Scripter** — Write powerful Lua scripts\n"
                "🎨 **Modeller** — Design 3D assets & models\n"
                "🖥️ **UI Designer** — Build beautiful interfaces\n\n"
                "*Use the dropdown below to begin your application.*"
            ),
            color=0x5865F2
        )
        embed.set_footer(text="Young Boy Studios · Applications")
        await channel.send(embed=embed, view=ApplyView())
        await interaction.response.send_message(embed=success_embed("Panel Posted", f"Apply panel posted in {channel.mention}!"), ephemeral=True)

    @discord.ui.button(label="✅ Post Verify Panel", style=discord.ButtonStyle.green, row=2)
    async def post_verify(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        ch_id = get(interaction.guild.id, "verify_channel")
        channel = bot.get_channel(ch_id) if ch_id else interaction.channel
        embed = discord.Embed(
            title="🎮 Link Your Roblox Account",
            description=(
                "Click **Verify Roblox** to link your Roblox account to Discord!\n\n"
                "**Why verify?**\n"
                "✅ Access exclusive member channels\n"
                "✅ Show your Roblox profile in the server\n"
                "✅ Get the verified role\n"
                "✅ Earn bonus daily coins\n\n"
                "*Verification takes under 2 minutes!*"
            ),
            color=0x00B2FF
        )
        embed.set_footer(text="Young Boy Studios · Roblox Verification")
        await channel.send(embed=embed, view=VerifyPanelView())
        await interaction.response.send_message(embed=success_embed("Verify Panel Posted"), ephemeral=True)

    @discord.ui.button(label="🤖 Post AI Panel", style=discord.ButtonStyle.blurple, row=3)
    async def post_ai_panel(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        ch_id = get(interaction.guild.id, "ai_channel")
        channel = bot.get_channel(ch_id) if ch_id else interaction.channel
        embed = discord.Embed(
            title="🤖 YBS Roblox AI Assistant",
            description=(
                "Ask me anything about **Roblox game development!**\n\n"
                "I can help with:\n"
                "🔧 Lua / Luau scripting\n"
                "🎨 Building & modelling tips\n"
                "🖥️ UI design guidance\n"
                "📐 Game design concepts\n"
                "🐛 Debugging your code\n\n"
                "Use `/askai <question>` or click the button below!"
            ),
            color=0x9B59B6
        )
        embed.set_footer(text="Powered by Claude AI · Roblox Dev Focus")
        await channel.send(embed=embed, view=AIPanelView())
        await interaction.response.send_message(embed=success_embed("AI Panel Posted"), ephemeral=True)

    @discord.ui.button(label="📋 Post Bug Report Panel", style=discord.ButtonStyle.secondary, row=3)
    async def post_bug_panel(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        ch_id = get(interaction.guild.id, "bug_reports_channel")
        channel = bot.get_channel(ch_id) if ch_id else interaction.channel
        embed = discord.Embed(
            title="🐛 Bug Report Center",
            description=(
                "Found a bug in our games or bot?\n"
                "Please report it so we can fix it!\n\n"
                "**What to include:**\n"
                "📝 A clear description of the bug\n"
                "🔄 Steps to reproduce it\n"
                "📸 Screenshots (optional)\n"
                "🎮 Which game/feature is affected\n\n"
                "Click **Report a Bug** below to get started."
            ),
            color=0xe74c3c
        )
        embed.set_footer(text="Young Boy Studios · Bug Reports")
        await channel.send(embed=embed, view=BugReportPanelView())
        await interaction.response.send_message(embed=success_embed("Bug Report Panel Posted"), ephemeral=True)

# ============================================================
# ROBLOX VERIFICATION SYSTEM
# ============================================================
async def fetch_roblox(username: str):
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post("https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                headers={"Content-Type": "application/json"})
            data = await r.json()
            if not data.get("data"):
                return None
            uid = data["data"][0]["id"]
            name = data["data"][0]["name"]
            disp = data["data"][0].get("displayName", name)
            r2 = await s.get(f"https://users.roblox.com/v1/users/{uid}")
            profile = await r2.json()
            r3 = await s.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=420x420&format=Png")
            thumb_data = await r3.json()
            thumb = thumb_data["data"][0]["imageUrl"] if thumb_data.get("data") else None
            # Fetch friend count
            r4 = await s.get(f"https://friends.roblox.com/v1/users/{uid}/friends/count")
            friend_data = await r4.json()
            friends = friend_data.get("count", 0)
            return {
                "id": uid, "name": name, "display": disp,
                "desc": (profile.get("description") or "No bio.")[:300],
                "created": (profile.get("created") or "")[:10],
                "thumb": thumb,
                "friends": friends,
                "is_banned": profile.get("isBanned", False)
            }
    except Exception:
        return None

async def fetch_roblox_by_id(uid: int):
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"https://users.roblox.com/v1/users/{uid}")
            profile = await r.json()
            r3 = await s.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=420x420&format=Png")
            thumb_data = await r3.json()
            thumb = thumb_data["data"][0]["imageUrl"] if thumb_data.get("data") else None
            return {
                "id": uid,
                "name": profile.get("name", "Unknown"),
                "display": profile.get("displayName", "Unknown"),
                "desc": (profile.get("description") or "No bio.")[:300],
                "created": (profile.get("created") or "")[:10],
                "thumb": thumb,
                "is_banned": profile.get("isBanned", False)
            }
    except Exception:
        return None

class VerifyModal(Modal, title="🎮 Link Roblox Account"):
    roblox_username = TextInput(
        label="Roblox Username",
        placeholder="Enter your exact Roblox username...",
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.roblox_username.value.strip()
        data = await fetch_roblox(username)
        if not data:
            return await interaction.followup.send(
                embed=error_embed("User Not Found", f"Couldn't find a Roblox user named **{username}**. Double-check the spelling."),
                ephemeral=True
            )
        if data.get("is_banned"):
            return await interaction.followup.send(
                embed=error_embed("Account Banned", "That Roblox account has been banned from the platform."),
                ephemeral=True
            )

        # Generate verification code (alphanumeric)
        code = f"YBS-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        verification_data[interaction.user.id] = {
            "code": code,
            "roblox_username": data["name"],
            "roblox_id": data["id"],
            "display": data["display"],
            "thumb": data.get("thumb"),
            "expires": (datetime.now() + timedelta(minutes=10)).isoformat()
        }

        embed = discord.Embed(
            title="🎮 Verify Your Roblox Account",
            description=(
                f"Found: **{data['display']}** (`@{data['name']}`)\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"**Step 2:** Add this code to your Roblox **bio/description**:\n\n"
                f"```\n{code}\n```\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"**Then click Confirm Verification below.**\n"
                f"*Code expires in 10 minutes.*"
            ),
            color=0x00B2FF
        )
        if data.get("thumb"):
            embed.set_thumbnail(url=data["thumb"])
        embed.set_footer(text="roblox.com → Profile → Edit → About Me → Paste code → Save")
        await interaction.followup.send(embed=embed, view=ConfirmVerifyView(), ephemeral=True)

class ConfirmVerifyView(View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="✅ Confirm Verification", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        vdata = verification_data.get(interaction.user.id)
        if not vdata:
            return await interaction.followup.send(embed=error_embed("No Pending Verification", "Start again by clicking Verify."), ephemeral=True)
        if datetime.fromisoformat(vdata["expires"]) < datetime.now():
            verification_data.pop(interaction.user.id, None)
            return await interaction.followup.send(embed=error_embed("Code Expired", "Your code expired. Start the process again."), ephemeral=True)
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(f"https://users.roblox.com/v1/users/{vdata['roblox_id']}")
                profile = await r.json()
                bio = profile.get("description", "")
        except Exception:
            return await interaction.followup.send(embed=error_embed("API Error", "Couldn't reach Roblox. Try again."), ephemeral=True)
        if vdata["code"] not in bio:
            return await interaction.followup.send(
                embed=error_embed(
                    "Code Not Found",
                    f"The code `{vdata['code']}` wasn't found in your bio yet.\n\nMake sure you **saved** your profile and wait a moment."
                ),
                ephemeral=True
            )
        roblox_links[interaction.user.id] = {
            "username": vdata["roblox_username"],
            "roblox_id": vdata["roblox_id"],
            "display": vdata["display"],
            "thumb": vdata.get("thumb"),
            "discord_name": str(interaction.user),
            "linked_at": datetime.now().strftime("%d %b %Y"),
            "verified": True
        }
        verification_data.pop(interaction.user.id, None)
        verified_role_id = get(interaction.guild.id, "verified_role")
        if verified_role_id:
            role = interaction.guild.get_role(verified_role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                except Exception:
                    pass
        try:
            await interaction.user.edit(nick=f"{vdata['display']} [{vdata['roblox_username']}]")
        except Exception:
            pass
        add_activity("✅", f"{interaction.user.display_name} verified Roblox: {vdata['roblox_username']}")
        embed = discord.Embed(
            title="✅ Roblox Account Linked!",
            description=(
                f"Successfully linked **{vdata['display']}** (`@{vdata['roblox_username']}`) to your Discord!\n\n"
                f"🎭 You've been given the verified role.\n"
                f"💰 You'll now earn **bonus daily coins!**"
            ),
            color=0x2ecc71
        )
        if vdata.get("thumb"):
            embed.set_thumbnail(url=vdata["thumb"])
        embed.set_footer(text="Young Boy Studios · Verified ✅")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="❓ Help", style=discord.ButtonStyle.secondary, emoji="❓")
    async def help_btn(self, interaction: discord.Interaction, button: Button):
        embed = discord.Embed(
            title="❓ Verification Help",
            description=(
                "**How to add the code to your bio:**\n\n"
                "1. Go to [roblox.com](https://www.roblox.com)\n"
                "2. Click your avatar → **Profile**\n"
                "3. Click the **pencil/edit icon** next to your bio\n"
                "4. Paste the `YBS-XXXXXX` code anywhere in your bio\n"
                "5. Click **Save**\n"
                "6. Come back here and click **Confirm Verification**\n\n"
                "⚠️ The code must be in your bio, not your display name."
            ),
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class VerifyPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎮 Verify Roblox Account", style=discord.ButtonStyle.blurple, custom_id="verify_roblox_btn")
    async def verify_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
            linked = roblox_links[interaction.user.id]
            embed = discord.Embed(
                title="✅ Already Verified!",
                description=f"You're linked to **{linked['display']}** (`@{linked['username']}`)\n\nUse `/roblox-unlink` to remove the link.",
                color=0x2ecc71
            )
            if linked.get("thumb"):
                embed.set_thumbnail(url=linked["thumb"])
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(VerifyModal())

# ============================================================
# APPLICATION SYSTEM (ENHANCED with image portfolio)
# ============================================================
class ApplicationModal(Modal):
    def __init__(self, role: str = "Developer"):
        super().__init__(title=f"🎮 Apply for {role[:35]} — YBS")
        self.role_value = role
        self.roblox_name = TextInput(label="Roblox Username", placeholder="e.g. CoolBuilder123", required=True, max_length=50)
        self.real_name = TextInput(label="Name & Age", placeholder="e.g. Alex, 17", required=True, max_length=60)
        self.experience = TextInput(label="Experience & Skills", placeholder="How long developing? What are you best at?", required=True, style=discord.TextStyle.paragraph, max_length=500)
        self.why_availability = TextInput(label="Why join? + Hours/week available", placeholder="Your motivation + e.g. 10 hrs/week", required=True, style=discord.TextStyle.paragraph, max_length=500)
        self.portfolio = TextInput(
            label="Portfolio / Work Samples (links or description)",
            placeholder="Paste image URLs, Roblox game links, or describe your work. You can also upload images after submitting!",
            required=False, style=discord.TextStyle.paragraph, max_length=1000
        )
        self.add_item(self.roblox_name)
        self.add_item(self.real_name)
        self.add_item(self.experience)
        self.add_item(self.why_availability)
        self.add_item(self.portfolio)

    async def on_submit(self, interaction: discord.Interaction):
        apps_channel_id = get(interaction.guild.id, "applications_channel")
        channel = bot.get_channel(apps_channel_id) if apps_channel_id else None
        name_age = self.real_name.value.split(",", 1)
        real_name = name_age[0].strip()
        age = name_age[1].strip() if len(name_age) > 1 else "—"
        app = {
            "roblox_name": self.roblox_name.value, "real_name": real_name, "age": age,
            "role": self.role_value, "experience": self.experience.value,
            "why_join": self.why_availability.value, "portfolio": self.portfolio.value or "N/A",
            "user": interaction.user, "timestamp": datetime.now().isoformat(),
            "status": "pending", "user_id": interaction.user.id,
            "portfolio_images": []
        }
        applications_data[interaction.user.id] = app
        if channel:
            embed = discord.Embed(
                title=f"📋 New Application — {real_name}",
                color=0x5865F2,
                timestamp=datetime.now()
            )
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="🎮 Roblox", value=f"`{self.roblox_name.value}`", inline=True)
            embed.add_field(name="👤 Name", value=real_name, inline=True)
            embed.add_field(name="🎂 Age", value=age, inline=True)
            embed.add_field(name="🔨 Role", value=f"**{self.role_value}**", inline=True)
            embed.add_field(name="🕐 Applied", value=f"<t:{int(datetime.now().timestamp())}:R>", inline=True)
            if interaction.user.id in roblox_links:
                rb = roblox_links[interaction.user.id]
                embed.add_field(name="✅ Roblox Verified", value=f"`{rb['username']}`", inline=True)
            embed.add_field(name="⚙️ Experience", value=self.experience.value, inline=False)
            embed.add_field(name="💡 Why Join + Availability", value=self.why_availability.value, inline=False)
            if self.portfolio.value:
                embed.add_field(name="📁 Portfolio", value=self.portfolio.value[:1000], inline=False)
            embed.add_field(
                name="📸 Portfolio Images",
                value="*The applicant can upload portfolio images using `/portfolio-upload`*",
                inline=False
            )
            embed.set_footer(text=f"User ID: {interaction.user.id} · Use buttons to review")
            await channel.send(embed=embed, view=ApplicationReviewView(interaction.user.id))
        add_activity("📋", f"New application from {real_name}", self.role_value)

        embed_reply = discord.Embed(
            title="🎉 Application Submitted!",
            description=(
                "Your application has been received! Our team will review it soon.\n\n"
                "📸 **Want to add portfolio images?**\n"
                "Use `/portfolio-upload` to attach images to your application!\n\n"
                "💡 **Tip:** Link your Roblox account with `/roblox-verify` to boost your chances!"
            ),
            color=0x2ecc71
        )
        embed_reply.set_footer(text="Good luck! 🚀")
        await interaction.response.send_message(embed=embed_reply, ephemeral=True)

class ApplicationReviewView(View):
    def __init__(self, applicant_id):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def is_staff(self, interaction):
        staff_id = get(interaction.guild.id, "admin_role")
        return interaction.user.guild_permissions.administrator or (staff_id and staff_id in [r.id for r in interaction.user.roles])

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green, emoji="✅")
    async def accept(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            role_id = get(interaction.guild.id, "member_role")
            role = interaction.guild.get_role(role_id) if role_id else None
            if role: await member.add_roles(role)
            pending_id = get(interaction.guild.id, "pending_role")
            if pending_id:
                pending = interaction.guild.get_role(pending_id)
                if pending and pending in member.roles:
                    await member.remove_roles(pending)
            app = applications_data.get(self.applicant_id, {})
            role_name = app.get("role", "Developer")
            role_map = {"Builder": "builder_role", "Scripter": "scripter_role", "Modeller": "modeller_role", "UI Designer": "ui_role"}
            specific_role_id = get(interaction.guild.id, role_map.get(role_name, ""))
            if specific_role_id:
                specific_role = interaction.guild.get_role(specific_role_id)
                if specific_role: await member.add_roles(specific_role)
            try:
                dm_embed = discord.Embed(
                    title="🎉 Application Accepted!",
                    description=(
                        f"Congratulations! Your application to **Young Boy Studios** has been **accepted!**\n\n"
                        f"🔨 Role: **{role_name}**\n"
                        f"✅ Accepted by: {interaction.user.display_name}\n\n"
                        f"Welcome to the team! 🚀\n"
                        f"*If you have questions, reply to this DM or ask in the server.*"
                    ),
                    color=0x2ecc71
                )
                await member.send(embed=dm_embed)
            except: pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "accepted"
                applications_data[self.applicant_id]["reviewed_by"] = str(interaction.user)
            add_mod_log("Accept App", str(member), str(interaction.user), "Application accepted", "#3ba55c")
            add_activity("✅", f"{member.display_name}'s application accepted")
        embed = discord.Embed(
            title="✅ Application Accepted",
            description=f"Accepted by {interaction.user.mention}",
            color=0x2ecc71,
            timestamp=datetime.now()
        )
        await interaction.message.edit(embed=interaction.message.embeds[0], view=None)
        await interaction.message.reply(embed=embed)
        await interaction.response.send_message(embed=success_embed("Accepted!"), ephemeral=True)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
        await interaction.response.send_modal(DeclineReasonModal(self.applicant_id))

    @discord.ui.button(label="⏳ Interview", style=discord.ButtonStyle.blurple, emoji="📞")
    async def interview(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                dm_embed = discord.Embed(
                    title="📞 Interview Scheduled!",
                    description=(
                        f"Your application to **Young Boy Studios** looks great!\n\n"
                        f"A staff member will DM you shortly to arrange an interview.\n"
                        f"Please keep an eye on your Direct Messages.\n\n"
                        f"*Reviewed by: {interaction.user.display_name}*"
                    ),
                    color=0x3498db
                )
                await member.send(embed=dm_embed)
            except: pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "interview"
                applications_data[self.applicant_id]["reviewed_by"] = str(interaction.user)
        await interaction.message.edit(view=None)
        await interaction.message.reply(embed=discord.Embed(
            title="📞 Interview Stage",
            description=f"Moved to interview by {interaction.user.mention}",
            color=0x3498db
        ))
        await interaction.response.send_message(embed=success_embed("Moved to interview!"), ephemeral=True)

    @discord.ui.button(label="📸 View Images", style=discord.ButtonStyle.secondary, emoji="📸")
    async def view_images(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
        app = applications_data.get(self.applicant_id, {})
        images = app.get("portfolio_images", [])
        if not images:
            return await interaction.response.send_message(
                embed=info_embed("No Images", "No portfolio images uploaded for this application."),
                ephemeral=True
            )
        embed = discord.Embed(title="📸 Portfolio Images", color=0x5865f2)
        for i, img_url in enumerate(images[:5], 1):
            embed.add_field(name=f"Image {i}", value=f"[View]({img_url})", inline=True)
        if images:
            embed.set_image(url=images[0])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🗑️ Delete", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_app(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        applications_data.pop(self.applicant_id, None)
        await interaction.message.delete()
        await interaction.response.send_message(embed=success_embed("Application Deleted"), ephemeral=True)

class DeclineReasonModal(Modal, title="❌ Decline Application"):
    reason = TextInput(label="Reason for declining", style=discord.TextStyle.paragraph, max_length=500)
    def __init__(self, applicant_id):
        super().__init__()
        self.applicant_id = applicant_id
    async def on_submit(self, interaction):
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                dm_embed = discord.Embed(
                    title="😔 Application Update",
                    description=(
                        f"Thank you for applying to **Young Boy Studios**.\n\n"
                        f"After reviewing your application, we've decided not to move forward at this time.\n\n"
                        f"**Reason:** {self.reason.value}\n\n"
                        f"Feel free to reapply in 2 weeks with an updated application!\n"
                        f"Keep developing your skills — we'd love to see you again. 💪"
                    ),
                    color=0xe74c3c
                )
                await member.send(embed=dm_embed)
            except: pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "declined"
                applications_data[self.applicant_id]["decline_reason"] = self.reason.value
        await interaction.response.send_message(embed=success_embed("Application Declined"), ephemeral=True)

class ApplicationRoleDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🔨 Builder", value="Builder", description="Build game environments & maps"),
            discord.SelectOption(label="💻 Scripter", value="Scripter", description="Write Lua scripts & game logic"),
            discord.SelectOption(label="🎨 Modeller", value="Modeller", description="Create 3D models & assets"),
            discord.SelectOption(label="🖥️ UI Designer", value="UI Designer", description="Design game interfaces & UX"),
        ]
        super().__init__(placeholder="🎮 Select the role you're applying for...", options=options, custom_id="apply_role_select_v5")

    async def callback(self, interaction):
        await interaction.response.send_modal(ApplicationModal(self.values[0]))

class ApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ApplicationRoleDropdown())

# ============================================================
# BUG REPORT SYSTEM
# ============================================================
class BugReportModal(Modal, title="🐛 Submit Bug Report"):
    bug_title = TextInput(label="Bug Title", placeholder="Short description of the bug", max_length=100)
    game_area = TextInput(label="Game/Feature Affected", placeholder="e.g. Obby World, Bot verification, etc.", max_length=100)
    description = TextInput(label="Detailed Description", style=discord.TextStyle.paragraph, placeholder="Describe the bug in detail...", max_length=1000)
    steps = TextInput(label="Steps to Reproduce", style=discord.TextStyle.paragraph, placeholder="1. Open the game\n2. Walk to...\n3. Bug occurs", max_length=500)
    severity = TextInput(label="Severity (Low/Medium/High/Critical)", placeholder="How bad is this bug?", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        report_id = f"BUG-{len(bug_reports)+1:04d}"
        severity_colors = {"critical": 0xe74c3c, "high": 0xe67e22, "medium": 0xf1c40f, "low": 0x2ecc71}
        sev_lower = self.severity.value.lower()
        color = severity_colors.get(sev_lower, 0x95a5a6)

        report = {
            "id": report_id,
            "reporter_id": interaction.user.id,
            "reporter_name": str(interaction.user),
            "title": self.bug_title.value,
            "game_area": self.game_area.value,
            "description": self.description.value,
            "steps": self.steps.value,
            "severity": self.severity.value,
            "status": "open",
            "timestamp": datetime.now().isoformat(),
            "images": []
        }
        bug_reports.append(report)

        embed = discord.Embed(
            title=f"🐛 Bug Report — {report_id}",
            color=color,
            timestamp=datetime.now()
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.add_field(name="🏷️ Title", value=self.bug_title.value, inline=True)
        embed.add_field(name="🎮 Area", value=self.game_area.value, inline=True)
        embed.add_field(name="⚠️ Severity", value=self.severity.value.title(), inline=True)
        embed.add_field(name="📝 Description", value=self.description.value, inline=False)
        embed.add_field(name="🔄 Steps to Reproduce", value=self.steps.value, inline=False)
        embed.set_footer(text=f"Report ID: {report_id} · Status: Open")

        ch_id = get(interaction.guild.id, "bug_reports_channel")
        channel = bot.get_channel(ch_id) if ch_id else interaction.channel
        msg = await channel.send(embed=embed, view=BugReportReviewView(report_id))

        # DM the reporter with instructions to add images
        try:
            dm_embed = discord.Embed(
                title="🐛 Bug Report Received!",
                description=(
                    f"**Report ID:** `{report_id}`\n\n"
                    f"Your bug report has been submitted!\n\n"
                    f"📸 **Want to add screenshots?**\n"
                    f"Reply to this DM with images to attach them to your report.\n\n"
                    f"We'll look into it as soon as possible. Thank you! 🙏"
                ),
                color=0x3498db
            )
            await interaction.user.send(embed=dm_embed)
            dm_reply_map[interaction.user.id] = {"type": "bug_report", "report_id": report_id, "channel_id": channel.id, "msg_id": msg.id}
        except: pass

        add_activity("🐛", f"Bug report: {report_id}", self.bug_title.value)
        await interaction.response.send_message(
            embed=success_embed("Bug Report Submitted!", f"Report ID: `{report_id}`\nThank you for helping us improve!"),
            ephemeral=True
        )

class BugReportReviewView(View):
    def __init__(self, report_id):
        super().__init__(timeout=None)
        self.report_id = report_id

    @discord.ui.button(label="✅ Mark Fixed", style=discord.ButtonStyle.green, emoji="✅")
    async def mark_fixed(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        for r in bug_reports:
            if r["id"] == self.report_id:
                r["status"] = "fixed"
                r["fixed_by"] = str(interaction.user)
                reporter = interaction.guild.get_member(r["reporter_id"])
                if reporter:
                    try:
                        await reporter.send(embed=discord.Embed(
                            title="✅ Bug Fixed!",
                            description=f"Your bug report **{self.report_id}** has been marked as fixed!\nThank you for reporting it. 🙏",
                            color=0x2ecc71
                        ))
                    except: pass
                break
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Report ID: {self.report_id} · Status: Fixed ✅ · By {interaction.user.display_name}")
        embed.color = 0x2ecc71
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(embed=success_embed("Marked as Fixed"), ephemeral=True)

    @discord.ui.button(label="🔄 In Progress", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def in_progress(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        for r in bug_reports:
            if r["id"] == self.report_id:
                r["status"] = "in_progress"
                break
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Report ID: {self.report_id} · Status: In Progress 🔄")
        embed.color = 0x3498db
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message(embed=success_embed("Marked as In Progress"), ephemeral=True)

    @discord.ui.button(label="❌ Won't Fix", style=discord.ButtonStyle.danger, emoji="❌")
    async def wont_fix(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        for r in bug_reports:
            if r["id"] == self.report_id:
                r["status"] = "wont_fix"
                break
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Report ID: {self.report_id} · Status: Won't Fix")
        embed.color = 0x95a5a6
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(embed=success_embed("Marked as Won't Fix"), ephemeral=True)

    @discord.ui.button(label="📋 Duplicate", style=discord.ButtonStyle.secondary, emoji="📋")
    async def duplicate(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        for r in bug_reports:
            if r["id"] == self.report_id:
                r["status"] = "duplicate"
                break
        embed = interaction.message.embeds[0]
        embed.set_footer(text=f"Report ID: {self.report_id} · Status: Duplicate")
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message(embed=success_embed("Marked as Duplicate"), ephemeral=True)

class BugReportPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🐛 Report a Bug", style=discord.ButtonStyle.red, custom_id="bug_report_btn")
    async def report_bug(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(BugReportModal())

# ============================================================
# ROBLOX AI SYSTEM (Claude-powered, Roblox-focused)
# ============================================================
ROBLOX_AI_SYSTEM = """You are RobloxBot, an AI assistant exclusively for Roblox game development questions.
You are part of Young Boy Studios, a Roblox game development team.

You ONLY answer questions about:
- Roblox game development (Lua/Luau scripting)
- Building and level design in Roblox Studio
- 3D modelling and asset creation for Roblox
- UI design in Roblox
- Roblox game mechanics, physics, and systems
- DataStores, RemoteEvents, RemoteFunctions
- Monetization and game passes
- Roblox Studio features and tools
- Debugging Roblox scripts
- Game optimization for Roblox
- Roblox APIs and services

If someone asks about anything NOT related to Roblox development, politely redirect them.
Keep responses helpful, concise, and formatted nicely with code blocks where appropriate.
Always use Luau syntax in code examples."""

async def ask_roblox_ai(user_id: int, question: str) -> str:
    """Send a question to the Roblox AI and get a response."""
    if not ANTHROPIC_API_KEY:
        return "❌ The AI assistant is not configured. Please ask an admin to set the `ANTHROPIC_API_KEY` environment variable."
    
    if user_id not in ai_conversations:
        ai_conversations[user_id] = []
    
    ai_conversations[user_id].append({"role": "user", "content": question})
    if len(ai_conversations[user_id]) > 20:
        ai_conversations[user_id] = ai_conversations[user_id][-20:]
    
    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "system": ROBLOX_AI_SYSTEM,
                "messages": ai_conversations[user_id]
            }
            headers = {
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01"
            }
            async with session.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    reply = data["content"][0]["text"]
                    ai_conversations[user_id].append({"role": "assistant", "content": reply})
                    return reply
                else:
                    return f"❌ AI error (status {resp.status}). Try again later."
    except Exception as e:
        return f"❌ AI connection error: {str(e)[:100]}"

class AIQuestionModal(Modal, title="🤖 Ask the Roblox AI"):
    question = TextInput(
        label="Your Roblox Dev Question",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. How do I make a DataStore that saves player coins?",
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        thinking_embed = discord.Embed(
            title="🤔 Thinking...",
            description="The Roblox AI is working on your answer...",
            color=0x9B59B6
        )
        msg = await interaction.followup.send(embed=thinking_embed)
        answer = await ask_roblox_ai(interaction.user.id, self.question.value)
        
        # Split if too long
        chunks = [answer[i:i+3900] for i in range(0, len(answer), 3900)]
        
        embed = discord.Embed(
            title="🤖 Roblox AI Assistant",
            color=0x9B59B6,
            timestamp=datetime.now()
        )
        embed.add_field(name="❓ Question", value=self.question.value[:500], inline=False)
        embed.add_field(name="💡 Answer", value=chunks[0], inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="YBS Roblox AI · Powered by Claude · Ask more with /askai")
        
        await msg.edit(embed=embed, view=AIFollowUpView(interaction.user.id))
        
        if len(chunks) > 1:
            for chunk in chunks[1:]:
                cont_embed = discord.Embed(description=chunk, color=0x9B59B6)
                await interaction.channel.send(embed=cont_embed)

class AIFollowUpView(View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="Ask Follow-Up", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def follow_up(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AIQuestionModal())

    @discord.ui.button(label="Clear Conversation", style=discord.ButtonStyle.secondary, emoji="🗑️")
    async def clear_conv(self, interaction: discord.Interaction, button: Button):
        ai_conversations.pop(self.user_id, None)
        await interaction.response.send_message(
            embed=success_embed("Conversation Cleared", "Starting fresh!"),
            ephemeral=True
        )

    @discord.ui.button(label="🐛 Report Issue", style=discord.ButtonStyle.secondary, emoji="🐛")
    async def report_issue(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(BugReportModal())

class AIPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🤖 Ask Roblox AI", style=discord.ButtonStyle.blurple, custom_id="ai_ask_btn")
    async def ask_ai(self, interaction: discord.Interaction, button: Button):
        ch_id = get(interaction.guild.id, "ai_channel")
        if ch_id and interaction.channel.id != ch_id and not interaction.user.guild_permissions.administrator:
            ch = interaction.guild.get_channel(ch_id)
            return await interaction.response.send_message(
                embed=error_embed("Wrong Channel", f"Please use the AI in {ch.mention if ch else 'the AI channel'}!"),
                ephemeral=True
            )
        await interaction.response.send_modal(AIQuestionModal())

# ============================================================
# STAFF CODE SYSTEM
# ============================================================
class StaffCodeModal(Modal, title="🔐 Enter Staff Code"):
    code = TextInput(label="Daily Staff Code", placeholder="Enter the code provided by the server owner", max_length=20)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        today = datetime.now().strftime("%Y-%m-%d")
        daily = staff_codes.get(guild_id, {})
        
        if daily.get("date") != today:
            return await interaction.response.send_message(
                embed=error_embed("No Code Set", "The server owner hasn't generated today's staff code yet."),
                ephemeral=True
            )
        
        if self.code.value.upper() != daily.get("code", "").upper():
            return await interaction.response.send_message(
                embed=error_embed("Wrong Code", "Incorrect staff code. Contact the server owner."),
                ephemeral=True
            )
        
        daily_staff_codes[interaction.user.id] = {
            "code": self.code.value,
            "date": today,
            "verified": True,
            "verified_at": datetime.now().strftime("%H:%M")
        }
        
        add_activity("🔐", f"{interaction.user.display_name} verified staff code")
        await interaction.response.send_message(
            embed=success_embed(
                "Staff Access Granted!",
                f"You're verified as staff for today.\n\nYou can now use all staff commands until midnight.\n*Code valid for: {today}*"
            ),
            ephemeral=True
        )

# ============================================================
# TIMER SYSTEM
# ============================================================
class TimerView(View):
    def __init__(self, timer_id):
        super().__init__(timeout=None)
        self.timer_id = timer_id

    @discord.ui.button(label="⏹️ Cancel Timer", style=discord.ButtonStyle.danger)
    async def cancel_timer(self, interaction: discord.Interaction, button: Button):
        timer = timers_data.get(self.timer_id)
        if not timer:
            return await interaction.response.send_message(embed=error_embed("Timer Not Found"), ephemeral=True)
        if timer["user_id"] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Not Your Timer"), ephemeral=True)
        timers_data.pop(self.timer_id, None)
        await interaction.response.send_message(embed=success_embed("Timer Cancelled"), ephemeral=True)
        await interaction.message.edit(
            embed=discord.Embed(title="⏹️ Timer Cancelled", color=0xe74c3c),
            view=None
        )

# ============================================================
# COUNTING SYSTEM
# ============================================================
async def handle_counting(message):
    guild_id = str(message.guild.id)
    counting_ch_id = get(message.guild.id, "counting_channel")
    if not counting_ch_id or message.channel.id != counting_ch_id:
        return False
    if message.author.bot:
        return True

    if guild_id not in counting_data:
        counting_data[guild_id] = {"count": 0, "last_user": None, "broken_by": None, "highscore": 0}

    data = counting_data[guild_id]
    content = message.content.strip()

    try:
        num = int(content.split()[0])
    except (ValueError, IndexError):
        return True  # Not a number, ignore

    expected = data["count"] + 1
    last_user = data["last_user"]

    if num != expected:
        data["broken_by"] = message.author.id
        old_count = data["count"]
        if old_count > data.get("highscore", 0):
            data["highscore"] = old_count
        data["count"] = 0
        data["last_user"] = None
        await message.add_reaction("❌")
        embed = discord.Embed(
            title="❌ Counting Broken!",
            description=(
                f"{message.author.mention} broke the count!\n\n"
                f"**Expected:** `{expected}`\n"
                f"**Got:** `{num}`\n"
                f"**Count reached:** `{old_count}`\n"
                f"**High Score:** `{data.get('highscore', 0)}`\n\n"
                f"Starting over from **1**..."
            ),
            color=0xe74c3c
        )
        await message.channel.send(embed=embed)
        return True

    if message.author.id == last_user:
        data["broken_by"] = message.author.id
        old_count = data["count"]
        data["count"] = 0
        data["last_user"] = None
        await message.add_reaction("❌")
        embed = discord.Embed(
            title="❌ Can't Count Twice!",
            description=(
                f"{message.author.mention} counted twice in a row!\n\n"
                f"**Count reached:** `{old_count}`\n"
                f"Starting over from **1**..."
            ),
            color=0xe74c3c
        )
        await message.channel.send(embed=embed)
        return True

    data["count"] = expected
    data["last_user"] = message.author.id
    if expected > data.get("highscore", 0):
        data["highscore"] = expected

    if expected % 100 == 0:
        await message.add_reaction("🎉")
        await message.channel.send(embed=discord.Embed(
            title=f"🎉 {expected}!",
            description=f"Amazing! Counting reached **{expected}**! Keep it going! 🚀",
            color=0xf1c40f
        ))
    elif expected % 10 == 0:
        await message.add_reaction("✅")
    else:
        await message.add_reaction("✅")
    return True

# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.now()
    print(f"✅ {bot.user} is online!")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Young Boy Studios 🎮"))
    if not status_cycle.is_running():
        status_cycle.start()
    if not check_shutdown.is_running():
        check_shutdown.start()
    if not check_reminders.is_running():
        check_reminders.start()
    if not check_temp_bans.is_running():
        check_temp_bans.start()
    if not birthday_check.is_running():
        birthday_check.start()
    if not generate_daily_staff_codes.is_running():
        generate_daily_staff_codes.start()
    add_activity("🟢", f"{bot.user} came online")
    try:
        synced = await tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(e)

@bot.event
async def on_member_join(member):
    channel_id = get(member.guild.id, "welcome_channel")
    channel = bot.get_channel(channel_id) if channel_id else None
    apply_id = get(member.guild.id, "apply_channel")
    verify_id = get(member.guild.id, "verify_channel")
    if channel:
        embed = discord.Embed(
            title=f"👋 Welcome to Young Boy Studios, {member.display_name}!",
            description=(
                f"Hey {member.mention}! We're thrilled to have you here! 🎉\n\n"
                f"**Get started:**\n"
                f"📋 Apply for the dev team in <#{apply_id}>\n" if apply_id else "" +
                f"✅ Link your Roblox in <#{verify_id}>\n" if verify_id else "" +
                f"💬 Introduce yourself in #general!\n\n"
                f"*We're building something incredible — come be part of it!*"
            ),
            color=0x5865F2
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count} · Young Boy Studios")
        embed.timestamp = discord.utils.utcnow()
        await channel.send(embed=embed)
    try:
        dm_embed = discord.Embed(
            title=f"👋 Welcome to Young Boy Studios!",
            description=(
                f"Hey **{member.display_name}**! Welcome aboard!\n\n"
                f"🎮 We're a Roblox game development team.\n"
                f"📋 Head to the server to apply for the dev team!\n\n"
                f"*This is an automated message. You can reply to this DM and it will reach our staff!*"
            ),
            color=0x5865F2
        )
        await member.send(embed=dm_embed)
        dm_reply_map[member.id] = {"type": "general", "guild_id": member.guild.id}
    except:
        pass
    pending_id = get(member.guild.id, "pending_role")
    if pending_id:
        pending_role = member.guild.get_role(pending_id)
        if pending_role:
            try:
                await member.add_roles(pending_role, reason="New member — pending application")
            except Exception:
                pass
    add_activity("📥", f"{member.display_name} joined", member.guild.name)
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="📥 Member Joined", color=0x2ecc71, timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)")
        embed.add_field(name="Account Age", value=f"<t:{int(member.created_at.timestamp())}:R>")
        embed.add_field(name="Member Count", value=str(member.guild.member_count))
        await log.send(embed=embed)

@bot.event
async def on_member_remove(member):
    add_activity("📤", f"{member.display_name} left", member.guild.name)
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="📤 Member Left", color=0xe74c3c, timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"**{member}** (`{member.id}`)")
        embed.add_field(name="Member Count", value=str(member.guild.member_count))
        await log.send(embed=embed)

@bot.event
async def on_member_update(before, after):
    # Nickname log
    if before.nick != after.nick:
        log_id = get(after.guild.id, "nickname_log_channel") or get(after.guild.id, "logs_channel")
        log = bot.get_channel(log_id) if log_id else None
        if log:
            embed = discord.Embed(title="📝 Nickname Changed", color=0x3498db, timestamp=discord.utils.utcnow())
            embed.set_thumbnail(url=after.display_avatar.url)
            embed.add_field(name="User", value=after.mention)
            embed.add_field(name="Before", value=before.nick or "*(none)*")
            embed.add_field(name="After", value=after.nick or "*(none)*")
            await log.send(embed=embed)
            nickname_logs.append({
                "user": str(after), "uid": after.id,
                "before": before.nick, "after": after.nick,
                "time": datetime.now().strftime("%H:%M · %d %b")
            })
    # Role changes
    if before.roles != after.roles:
        added = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        log_id = get(after.guild.id, "logs_channel")
        log = bot.get_channel(log_id) if log_id else None
        if log and (added or removed):
            embed = discord.Embed(title="🎭 Role Update", color=0x9B59B6, timestamp=discord.utils.utcnow())
            embed.add_field(name="User", value=after.mention)
            if added: embed.add_field(name="➕ Added", value=", ".join(r.mention for r in added))
            if removed: embed.add_field(name="➖ Removed", value=", ".join(r.mention for r in removed))
            await log.send(embed=embed)

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    if message.content:
        snipe_data[message.channel.id] = {
            "content": message.content[:500],
            "author": str(message.author),
            "author_id": message.author.id,
            "avatar": message.author.display_avatar.url,
            "time": datetime.now().strftime("%H:%M"),
            "attachments": [a.url for a in message.attachments]
        }
    mod_log_id = get(message.guild.id, "mod_channel") or get(message.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="🗑️ Message Deleted", color=0xe74c3c, timestamp=discord.utils.utcnow())
        embed.add_field(name="Author", value=message.author.mention)
        embed.add_field(name="Channel", value=message.channel.mention)
        embed.add_field(name="Content", value=message.content[:500] or "*(no text)*", inline=False)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.filename for a in message.attachments))
        await log.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    edit_snipe_data[before.channel.id] = {
        "before": before.content[:500],
        "after": after.content[:500],
        "author": str(before.author),
        "time": datetime.now().strftime("%H:%M")
    }
    mod_log_id = get(before.guild.id, "mod_channel") or get(before.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="✏️ Message Edited", color=0xf39c12, timestamp=discord.utils.utcnow())
        embed.add_field(name="Author", value=before.author.mention)
        embed.add_field(name="Channel", value=before.channel.mention)
        embed.add_field(name="Before", value=before.content[:400] or "*(empty)*", inline=False)
        embed.add_field(name="After", value=after.content[:400] or "*(empty)*", inline=False)
        embed.add_field(name="Jump", value=f"[Go to message]({after.jump_url})", inline=False)
        await log.send(embed=embed)

@bot.event
async def on_message(message):
    if not message.guild:
        # Handle DM replies
        user_id = message.author.id
        if user_id in dm_reply_map and not message.author.bot:
            dm_info = dm_reply_map[user_id]
            if dm_info.get("type") == "bug_report":
                # Forward images to bug report
                for attachment in message.attachments:
                    if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                        for r in bug_reports:
                            if r["id"] == dm_info["report_id"]:
                                r["images"].append(attachment.url)
                                break
                        ch = bot.get_channel(dm_info["channel_id"])
                        if ch:
                            embed = discord.Embed(title="📸 Bug Report Image Added", color=0x3498db)
                            embed.add_field(name="Report", value=dm_info["report_id"])
                            embed.set_image(url=attachment.url)
                            await ch.send(embed=embed)
                        await message.author.send(embed=success_embed("Image Added!", f"Your screenshot has been added to report `{dm_info['report_id']}`!"))
            elif dm_info.get("type") == "general":
                # Forward DM to staff channel
                guild = bot.get_guild(dm_info.get("guild_id"))
                if guild:
                    staff_ch_id = get(guild.id, "staff_channel") or get(guild.id, "logs_channel")
                    staff_ch = guild.get_channel(staff_ch_id) if staff_ch_id else None
                    if staff_ch:
                        embed = discord.Embed(
                            title="📬 DM Received from Member",
                            description=message.content or "*(no text)*",
                            color=0x9B59B6
                        )
                        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
                        embed.add_field(name="User ID", value=str(user_id))
                        embed.set_footer(text="Reply with /dm to respond")
                        await staff_ch.send(embed=embed)
                        await message.author.send(embed=info_embed("Message Forwarded", "Your message has been forwarded to staff! We'll get back to you soon."))
            elif dm_info.get("type") == "portfolio":
                # Add image to application
                for attachment in message.attachments:
                    if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
                        app = applications_data.get(user_id, {})
                        if "portfolio_images" not in app:
                            app["portfolio_images"] = []
                        if len(app["portfolio_images"]) < 5:
                            app["portfolio_images"].append(attachment.url)
                            await message.author.send(embed=success_embed("Portfolio Image Added!", f"Image added! ({len(app['portfolio_images'])}/5)"))
                        else:
                            await message.author.send(embed=error_embed("Max Images", "You can only add up to 5 portfolio images."))
        return

    if message.author.bot:
        await bot.process_commands(message)
        return

    # Counting channel
    if await handle_counting(message):
        return

    # AutoMod
    guild_words = automod_data.get(str(message.guild.id), [])
    if guild_words and any(w.lower() in message.content.lower() for w in guild_words):
        try:
            await message.delete()
            await message.channel.send(
                embed=discord.Embed(
                    title="⚠️ Message Removed",
                    description=f"{message.author.mention}, your message contained a banned word.",
                    color=0xe74c3c
                ),
                delete_after=5
            )
            if message.author.id not in warnings_data:
                warnings_data[message.author.id] = []
            warnings_data[message.author.id].append({"reason": "AutoMod: banned word", "by": "AutoMod", "time": datetime.now().isoformat()})
            add_activity("🤖", f"AutoMod: message removed from {message.author.display_name}")
        except Exception:
            pass
        await bot.process_commands(message)
        return

    # Starboard check
    # AFK
    if message.author.id in afk_data:
        afk_data.pop(message.author.id)
        await message.channel.send(
            embed=discord.Embed(
                title="✅ Welcome Back!",
                description=f"{message.author.mention}'s AFK status has been removed.",
                color=0x2ecc71
            ),
            delete_after=5
        )
    for mentioned in message.mentions:
        if mentioned.id in afk_data:
            info_a = afk_data[mentioned.id]
            embed = discord.Embed(
                title="💤 User is AFK",
                description=f"**{mentioned.display_name}** is AFK: *{info_a['reason']}*\n*(Since {info_a['time']})*",
                color=0xf39c12
            )
            await message.channel.send(embed=embed, delete_after=10)

    # AI channel auto-response
    ai_ch_id = get(message.guild.id, "ai_channel")
    if ai_ch_id and message.channel.id == ai_ch_id and not message.author.bot:
        if len(message.content) > 10 and not message.content.startswith("!") and not message.content.startswith("/"):
            async with message.channel.typing():
                answer = await ask_roblox_ai(message.author.id, message.content)
                chunks = [answer[i:i+3900] for i in range(0, len(answer), 3900)]
                embed = discord.Embed(
                    title="🤖 Roblox AI",
                    description=chunks[0],
                    color=0x9B59B6
                )
                embed.set_footer(text="YBS Roblox AI · Ask me anything about Roblox dev!")
                await message.reply(embed=embed, view=AIFollowUpView(message.author.id))
                for chunk in chunks[1:]:
                    await message.channel.send(embed=discord.Embed(description=chunk, color=0x9B59B6))

    # Bot commands channel enforcement
    bot_ch_id = get_bot_cmd_channel(message.guild.id)
    if bot_ch_id and not message.author.guild_permissions.administrator:
        if message.content.startswith("!") and message.channel.id != bot_ch_id:
            bot_ch = message.guild.get_channel(bot_ch_id)
            await message.reply(
                embed=discord.Embed(
                    title="❌ Wrong Channel",
                    description=f"Please use bot commands in {bot_ch.mention if bot_ch else 'the bot commands channel'}!",
                    color=0xe74c3c
                ),
                delete_after=5
            )
            try:
                await message.delete()
            except:
                pass
            return

    # XP
    now = datetime.now()
    last = xp_cooldowns.get(message.author.id)
    if not last or (now - last).total_seconds() >= 60:
        xp_cooldowns[message.author.id] = now
        if message.author.id not in xp_data:
            xp_data[message.author.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(message.author)}
        earned = random.randint(5, 15)
        # Premium bonus
        if message.author.id in premium_users:
            earned = int(earned * 1.5)
        xp_data[message.author.id]["xp"] += earned
        xp_data[message.author.id]["messages"] = xp_data[message.author.id].get("messages", 0) + 1
        xp_data[message.author.id]["name"] = str(message.author)
        cur_xp = xp_data[message.author.id]["xp"]
        old_lv = xp_data[message.author.id]["level"]
        new_lv = get_level(cur_xp)
        if new_lv > old_lv:
            xp_data[message.author.id]["level"] = new_lv
            add_activity("⬆️", f"{message.author.display_name} reached level {new_lv}!", message.guild.name)
            lv_ch_id = get(message.guild.id, "levelup_channel")
            lv_ch = bot.get_channel(lv_ch_id) if lv_ch_id else message.channel
            embed = discord.Embed(
                title="🎉 Level Up!",
                description=f"{message.author.mention} reached **Level {new_lv}**! 🚀",
                color=0xf1c40f
            )
            embed.set_thumbnail(url=message.author.display_avatar.url)
            try:
                await lv_ch.send(embed=embed, delete_after=30)
            except:
                pass
            for lv_key, role_key in [("5", "level5_role"), ("10", "level10_role"), ("25", "level25_role")]:
                if new_lv >= int(lv_key):
                    lv_role_id = get(message.guild.id, role_key)
                    if lv_role_id:
                        lv_role = message.guild.get_role(lv_role_id)
                        if lv_role and lv_role not in message.author.roles:
                            try:
                                await message.author.add_roles(lv_role)
                            except:
                                pass

    # Custom commands
    guild_customs = custom_commands.get(str(message.guild.id), {})
    if message.content.startswith("!") and len(message.content) > 1:
        cmd_name = message.content[1:].split()[0].lower()
        if cmd_name in guild_customs:
            await message.channel.send(guild_customs[cmd_name])
            return

    await bot.process_commands(message)

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    guild = reaction.message.guild
    if not guild:
        return

    # Starboard
    starboard_ch_id = get(guild.id, "starboard_channel")
    if starboard_ch_id and str(reaction.emoji) == "⭐":
        if reaction.count >= 3:  # 3 stars to get on starboard
            msg_id = reaction.message.id
            guild_id = str(guild.id)
            if guild_id not in starboard_data:
                starboard_data[guild_id] = {}
            if msg_id not in starboard_data[guild_id]:
                starboard_data[guild_id][msg_id] = reaction.count
                sb_channel = bot.get_channel(starboard_ch_id)
                if sb_channel:
                    embed = discord.Embed(
                        description=reaction.message.content or "*(no text)*",
                        color=0xf1c40f,
                        timestamp=reaction.message.created_at
                    )
                    embed.set_author(name=reaction.message.author.display_name, icon_url=reaction.message.author.display_avatar.url)
                    embed.add_field(name="Channel", value=reaction.message.channel.mention)
                    embed.add_field(name="Jump", value=f"[Click here]({reaction.message.jump_url})")
                    if reaction.message.attachments:
                        embed.set_image(url=reaction.message.attachments[0].url)
                    await sb_channel.send(content=f"⭐ **{reaction.count}** stars | {reaction.message.channel.mention}", embed=embed)

    # Reaction roles
    guild_rr = reaction_roles.get(str(guild.id), {})
    msg_rr = guild_rr.get(str(reaction.message.id), {})
    role_id = msg_rr.get(str(reaction.emoji))
    if role_id:
        member = guild.get_member(user.id)
        role = guild.get_role(role_id)
        if member and role:
            try:
                await member.add_roles(role)
            except:
                pass

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    guild = reaction.message.guild
    if not guild:
        return
    guild_rr = reaction_roles.get(str(guild.id), {})
    msg_rr = guild_rr.get(str(reaction.message.id), {})
    role_id = msg_rr.get(str(reaction.emoji))
    if role_id:
        member = guild.get_member(user.id)
        role = guild.get_role(role_id)
        if member and role:
            try:
                await member.remove_roles(role)
            except:
                pass

@bot.event
async def on_voice_state_update(member, before, after):
    mod_log_id = get(member.guild.id, "mod_channel") or get(member.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None

    if before.channel is None and after.channel is not None:
        voice_log.insert(0, {"action": "joined", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        add_activity("🎙️", f"{member.display_name} joined voice #{after.channel.name}")
        if log:
            await log.send(embed=discord.Embed(
                title="🎙️ Voice Joined",
                description=f"{member.mention} joined **{after.channel.name}**",
                color=0x2ecc71, timestamp=discord.utils.utcnow()
            ))
        # Join-to-create
        jtc_id = get(member.guild.id, "join_to_create_channel")
        if jtc_id and after.channel.id == jtc_id:
            new_vc = await member.guild.create_voice_channel(
                name=f"🎮 {member.display_name}'s VC",
                category=after.channel.category,
                user_limit=10
            )
            user_created_vcs[new_vc.id] = member.id
            await member.move_to(new_vc)
    elif before.channel is not None and after.channel is None:
        voice_log.insert(0, {"action": "left", "member": str(member), "channel": before.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log:
            await log.send(embed=discord.Embed(
                title="🔇 Voice Left",
                description=f"{member.mention} left **{before.channel.name}**",
                color=0xe74c3c, timestamp=discord.utils.utcnow()
            ))
        # Clean up empty user-created VCs
        if before.channel.id in user_created_vcs:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete(reason="Empty user-created VC")
                    user_created_vcs.pop(before.channel.id, None)
                except:
                    pass
    elif before.channel != after.channel:
        voice_log.insert(0, {"action": "moved", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log:
            await log.send(embed=discord.Embed(
                title="↔️ Voice Moved",
                description=f"{member.mention}: **{before.channel.name}** → **{after.channel.name}**",
                color=0x3498db, timestamp=discord.utils.utcnow()
            ))
    while len(voice_log) > 100:
        voice_log.pop()

@bot.event
async def on_member_ban(guild, user):
    ban_log_data.append({"user": str(user), "uid": user.id, "reason": "—", "time": datetime.now().strftime("%d %b %Y %H:%M")})
    mod_log_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="🔨 Member Banned", color=0xe74c3c, timestamp=discord.utils.utcnow())
        embed.add_field(name="User", value=f"**{user}** (`{user.id}`)")
        embed.set_thumbnail(url=user.display_avatar.url)
        await log.send(embed=embed)

@bot.event
async def on_member_unban(guild, user):
    mod_log_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="✅ Member Unbanned", color=0x2ecc71, timestamp=discord.utils.utcnow())
        embed.add_field(name="User", value=f"**{user}** (`{user.id}`)")
        await log.send(embed=embed)

# ============================================================
# TASKS
# ============================================================
statuses = [
    "Young Boy Studios 🎮", "Building something epic 🔨",
    "Hiring developers!", "Roblox game dev team 🚀",
    "Use /help for commands", "Writing Luau scripts 💻",
    "Designing UI 🖥️", "Modelling 3D assets 🎨"
]

@tasks.loop(minutes=10)
async def status_cycle():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(statuses)))

@tasks.loop(seconds=5)
async def check_shutdown():
    global bot_shutdown_flag
    if bot_shutdown_flag:
        print("🔴 Bot shutdown triggered.")
        await bot.close()

@tasks.loop(minutes=1)
async def check_reminders():
    now = datetime.now()
    for reminder in reminders_data[:]:
        if not reminder.get("sent") and datetime.fromisoformat(reminder["time"]) <= now:
            try:
                user = bot.get_user(reminder["user_id"])
                if user:
                    embed = discord.Embed(
                        title="⏰ Reminder!",
                        description=reminder["reminder"],
                        color=0x3498db
                    )
                    embed.set_footer(text="Young Boy Studios · Reminder System")
                    await user.send(embed=embed)
                reminder["sent"] = True
            except:
                reminder["sent"] = True

@tasks.loop(minutes=5)
async def check_temp_bans():
    now = datetime.now()
    for ban in temp_bans[:]:
        if datetime.fromisoformat(ban["unban_at"]) <= now:
            guild = bot.get_guild(ban["guild_id"])
            if guild:
                try:
                    user = await bot.fetch_user(ban["user_id"])
                    await guild.unban(user, reason="Temp ban expired")
                    add_activity("✅", f"Temp ban expired for {user}", ban.get("reason", ""))
                except:
                    pass
            temp_bans.remove(ban)

@tasks.loop(hours=24)
async def birthday_check():
    today = datetime.now().strftime("%m-%d")
    for guild in bot.guilds:
        ch_id = get(guild.id, "birthday_channel")
        if not ch_id:
            continue
        channel = guild.get_channel(ch_id)
        if not channel:
            continue
        birthday_role_id = get(guild.id, "birthday_role")
        # Remove yesterday's birthday roles
        if birthday_role_id:
            bday_role = guild.get_role(birthday_role_id)
            if bday_role:
                for member in guild.members:
                    if bday_role in member.roles and birthday_data.get(member.id) != today:
                        try:
                            await member.remove_roles(bday_role)
                        except:
                            pass
        for uid, bday in birthday_data.items():
            if bday == today:
                member = guild.get_member(uid)
                if member:
                    embed = discord.Embed(
                        title=f"🎂 Happy Birthday, {member.display_name}!",
                        description=f"🎉 {member.mention}'s special day is today!\n\nHappy Birthday from the whole YBS team! 🎈🎊",
                        color=0xFF79C6
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    await channel.send(embed=embed)
                    if birthday_role_id:
                        bday_role = guild.get_role(birthday_role_id)
                        if bday_role:
                            try:
                                await member.add_roles(bday_role)
                            except:
                                pass

@tasks.loop(hours=24)
async def generate_daily_staff_codes():
    """Auto-generate new staff codes each day and DM to server owners."""
    for guild in bot.guilds:
        code = generate_staff_code()
        today = datetime.now().strftime("%Y-%m-%d")
        staff_codes[str(guild.id)] = {
            "code": code,
            "date": today,
            "generated_at": datetime.now().strftime("%H:%M")
        }
        # DM the server owner
        try:
            owner = guild.owner
            if owner:
                embed = discord.Embed(
                    title="🔐 Daily Staff Code",
                    description=(
                        f"Here is today's staff verification code for **{guild.name}**:\n\n"
                        f"```\n{code}\n```\n\n"
                        f"**Valid for:** {today}\n"
                        f"Share this with your staff so they can use `/staffverify` to unlock staff commands.\n\n"
                        f"⚠️ *Keep this code private! A new one is generated each day.*"
                    ),
                    color=0x9B59B6
                )
                embed.set_footer(text="Young Boy Studios · Staff System")
                await owner.send(embed=embed)
        except:
            pass

# ============================================================
# MODERATION MODALS
# ============================================================
class WarnModal(Modal, title="⚠️ Warn Member"):
    reason = TextInput(label="Reason", placeholder="Enter warn reason…", max_length=500)
    def __init__(self, member):
        super().__init__()
        self.member = member
    async def on_submit(self, interaction):
        uid = self.member.id
        if uid not in warnings_data:
            warnings_data[uid] = []
        warnings_data[uid].append({"reason": self.reason.value, "by": str(interaction.user), "time": datetime.now().isoformat()})
        count = len(warnings_data[uid])
        add_mod_log("Warn", str(self.member), str(interaction.user), self.reason.value, "#faa61a")
        add_activity("⚠️", f"{self.member.display_name} warned", self.reason.value)
        try:
            await self.member.send(embed=discord.Embed(
                title="⚠️ Warning Received",
                description=f"You were warned in **{interaction.guild.name}**\n\n**Reason:** {self.reason.value}\n**Warnings:** {count}/3",
                color=0xf39c12
            ))
        except: pass
        # Auto escalation
        if count >= 3:
            await interaction.channel.send(embed=discord.Embed(
                title="🚨 Warning Threshold",
                description=f"{self.member.mention} has reached **{count} warnings!** Consider further action.",
                color=0xe74c3c
            ))
        await interaction.response.send_message(
            embed=success_embed("Warned", f"**{self.member}** warned. Total: **{count}** warning(s)."),
            ephemeral=True
        )

class BanModal(Modal, title="🔨 Ban Member"):
    reason = TextInput(label="Reason", placeholder="Enter ban reason…", max_length=500)
    del_days = TextInput(label="Delete message days (0-7)", default="1", required=False, max_length=1)
    def __init__(self, member):
        super().__init__()
        self.member = member
    async def on_submit(self, interaction):
        try: days = max(0, min(7, int(self.del_days.value or 1)))
        except: days = 1
        try:
            await self.member.ban(reason=self.reason.value, delete_message_days=days)
            add_mod_log("Ban", str(self.member), str(interaction.user), self.reason.value, "#ed4245")
            ban_log_data.append({"user": str(self.member), "uid": self.member.id, "reason": self.reason.value, "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
            await interaction.response.send_message(embed=success_embed("Banned", f"**{self.member}** has been banned."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed", "Couldn't ban that member."), ephemeral=True)

class TimeoutModal(Modal, title="⏰ Timeout Member"):
    duration = TextInput(label="Duration in minutes (max 40320 = 28d)", placeholder="e.g. 60", default="10", max_length=6)
    reason = TextInput(label="Reason", placeholder="Enter reason…", max_length=500, required=False)
    def __init__(self, member):
        super().__init__()
        self.member = member
    async def on_submit(self, interaction):
        try: mins = max(1, min(40320, int(self.duration.value)))
        except: mins = 10
        try:
            await self.member.timeout(discord.utils.utcnow() + timedelta(minutes=mins), reason=self.reason.value or "No reason")
            add_mod_log("Timeout", str(self.member), str(interaction.user), f"{mins}m — {self.reason.value or 'No reason'}", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Timed Out", f"**{self.member}** timed out for **{mins} minutes**."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

class NoteModal(Modal, title="📝 Add Staff Note"):
    note = TextInput(label="Note", style=discord.TextStyle.paragraph, placeholder="Enter your staff note…", max_length=1000)
    def __init__(self, member):
        super().__init__()
        self.member = member
    async def on_submit(self, interaction):
        uid = str(self.member.id)
        if uid not in notes_data: notes_data[uid] = []
        notes_data[uid].append({"note": self.note.value, "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
        add_activity("📝", f"Note added for {self.member.display_name}")
        await interaction.response.send_message(embed=success_embed("Note Added", f"Note added for **{self.member}**."), ephemeral=True)

class AnnounceModal(Modal, title="📢 Post Announcement"):
    title_input = TextInput(label="Title", placeholder="Announcement title…", max_length=200)
    content = TextInput(label="Message", style=discord.TextStyle.paragraph, placeholder="Your announcement content…", max_length=2000)
    color_hex = TextInput(label="Color hex (e.g. 5865f2)", required=False, max_length=7)
    ping_input = TextInput(label="Ping? (type 'everyone', 'here', or role name)", required=False, max_length=100)
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
    async def on_submit(self, interaction):
        try: color = int(self.color_hex.value.strip("#"), 16) if self.color_hex.value.strip() else 0x5865f2
        except: color = 0x5865f2
        embed = discord.Embed(title=self.title_input.value, description=self.content.value, color=color)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"Young Boy Studios · {datetime.now().strftime('%d %b %Y %H:%M')}")
        embed.timestamp = discord.utils.utcnow()
        ping_val = self.ping_input.value.lower().strip() if self.ping_input.value else ""
        if "everyone" in ping_val:
            ping = "@everyone"
        elif "here" in ping_val:
            ping = "@here"
        else:
            ping = None
        await self.channel.send(content=ping, embed=embed)
        await interaction.response.send_message(embed=success_embed("Announcement Posted", f"Posted in {self.channel.mention}!"), ephemeral=True)
        add_activity("📢", f"Announcement: {self.title_input.value}")

class EmbedBuilderModal(Modal, title="✨ Custom Embed Builder"):
    title_input = TextInput(label="Title", max_length=200)
    description = TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=2000)
    color_hex = TextInput(label="Color hex (e.g. 5865f2)", required=False, max_length=7)
    footer_text = TextInput(label="Footer text (optional)", required=False, max_length=200)
    image_url = TextInput(label="Image URL (optional)", required=False, max_length=500)
    async def on_submit(self, interaction):
        try: color = int(self.color_hex.value.strip("#"), 16) if self.color_hex.value.strip() else 0x5865f2
        except: color = 0x5865f2
        embed = discord.Embed(title=self.title_input.value, description=self.description.value, color=color)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if self.footer_text.value: embed.set_footer(text=self.footer_text.value)
        if self.image_url.value:
            try: embed.set_image(url=self.image_url.value)
            except: pass
        embed.timestamp = discord.utils.utcnow()
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message(embed=success_embed("Embed Posted!"), ephemeral=True)

class CustomCommandModal(Modal, title="➕ Add Custom Command"):
    cmd_name = TextInput(label="Command name (without !)", placeholder="e.g. socials", max_length=30)
    response = TextInput(label="Response", style=discord.TextStyle.paragraph, placeholder="What the bot should reply", max_length=1000)
    async def on_submit(self, interaction):
        guild_id = str(interaction.guild.id)
        if guild_id not in custom_commands:
            custom_commands[guild_id] = {}
        custom_commands[guild_id][self.cmd_name.value.lower()] = self.response.value
        await interaction.response.send_message(
            embed=success_embed("Custom Command Added", f"Command `!{self.cmd_name.value.lower()}` created!"),
            ephemeral=True
        )

# ============================================================
# MASSIVE MOD MENU VIEW (EXPANDED)
# ============================================================
class ModMenuView(View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=120)
        self.member = member

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.secondary, row=0)
    async def warn_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        await interaction.response.send_modal(WarnModal(self.member))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger, row=0)
    async def kick_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.kick(reason=f"Kicked by {interaction.user}")
            add_mod_log("Kick", str(self.member), str(interaction.user), "Via mod menu", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Kicked", f"**{self.member}** has been kicked."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger, row=0)
    async def ban_btn(self, interaction, button):
        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        await interaction.response.send_modal(BanModal(self.member))

    @discord.ui.button(label="⏰ Timeout", style=discord.ButtonStyle.secondary, row=0)
    async def timeout_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        await interaction.response.send_modal(TimeoutModal(self.member))

    @discord.ui.button(label="📝 Add Note", style=discord.ButtonStyle.primary, row=0)
    async def note_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        await interaction.response.send_modal(NoteModal(self.member))

    @discord.ui.button(label="🔇 Mute 1h", style=discord.ButtonStyle.secondary, row=1)
    async def mute1h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.timeout(discord.utils.utcnow() + timedelta(hours=1), reason=f"Muted 1h by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "1 hour", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Muted", f"**{self.member}** muted for 1 hour."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🔇 Mute 24h", style=discord.ButtonStyle.secondary, row=1)
    async def mute24h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.timeout(discord.utils.utcnow() + timedelta(hours=24), reason=f"Muted 24h by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "24 hours", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Muted", f"**{self.member}** muted for 24 hours."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🔇 Mute 7d", style=discord.ButtonStyle.secondary, row=1)
    async def mute7d_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.timeout(discord.utils.utcnow() + timedelta(days=7), reason=f"Muted 7d by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "7 days", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Muted", f"**{self.member}** muted for 7 days."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🔔 Unmute", style=discord.ButtonStyle.success, row=1)
    async def unmute_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.timeout(None)
            await interaction.response.send_message(embed=success_embed("Unmuted", f"**{self.member}** unmuted."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🗑️ Clear Warns", style=discord.ButtonStyle.danger, row=1)
    async def clearwarn_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        count = len(warnings_data.pop(self.member.id, []))
        await interaction.response.send_message(embed=success_embed("Warnings Cleared", f"Cleared **{count}** warning(s) for **{self.member}**."), ephemeral=True)

    @discord.ui.button(label="📋 Full History", style=discord.ButtonStyle.primary, row=2)
    async def history_btn(self, interaction, button):
        warns = warnings_data.get(self.member.id, [])
        user_notes = notes_data.get(str(self.member.id), [])
        embed = discord.Embed(title=f"📋 Full History — {self.member}", color=0x5865f2, timestamp=discord.utils.utcnow())
        embed.set_thumbnail(url=self.member.display_avatar.url)
        embed.add_field(name="⚠️ Warnings", value=f"`{len(warns)}`", inline=True)
        embed.add_field(name="📝 Notes", value=f"`{len(user_notes)}`", inline=True)
        embed.add_field(name="🔇 Timed Out", value="✅" if self.member.is_timed_out() else "❌", inline=True)
        if self.member.id in roblox_links:
            embed.add_field(name="🎮 Roblox", value=roblox_links[self.member.id]["username"], inline=True)
        eco = economy_data.get(self.member.id, {})
        if eco:
            embed.add_field(name="💰 Balance", value=f"{eco.get('balance', 0):,} coins", inline=True)
        xd = xp_data.get(self.member.id, {})
        if xd:
            embed.add_field(name="⭐ Level", value=str(get_level(xd.get("xp", 0))), inline=True)
        if warns:
            embed.add_field(name="Recent Warnings", value="\n".join(f"• **{w['reason'][:55]}** *({w['time'][:10]})*" for w in warns[-5:]), inline=False)
        if user_notes:
            embed.add_field(name="Staff Notes", value="\n".join(f"• {n['note'][:65]} *(by {n['by'].split('#')[0]})*" for n in user_notes[-3:]), inline=False)
        if not warns and not user_notes:
            embed.description = "✅ Clean history — no warnings or notes."
        embed.set_footer(text=f"User ID: {self.member.id}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📤 DM Member", style=discord.ButtonStyle.secondary, row=2)
    async def dm_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        member_ref = self.member
        guild_ref = interaction.guild
        class QuickDMModal(Modal, title="📤 Send DM to Member"):
            msg = TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=1000)
            async def on_submit(self2, interaction2):
                try:
                    emb = discord.Embed(
                        title=f"📬 Message from {guild_ref.name} Staff",
                        description=self2.msg.value,
                        color=0x5865f2
                    )
                    emb.set_footer(text="You can reply to this DM and staff will see it.")
                    await member_ref.send(embed=emb)
                    dm_reply_map[member_ref.id] = {"type": "general", "guild_id": guild_ref.id}
                    await interaction2.response.send_message(embed=success_embed("DM Sent", f"DM sent to **{member_ref}**."), ephemeral=True)
                except:
                    await interaction2.response.send_message(embed=error_embed("Couldn't DM"), ephemeral=True)
        await interaction.response.send_modal(QuickDMModal())

    @discord.ui.button(label="🪃 Softban", style=discord.ButtonStyle.danger, row=2)
    async def softban_btn(self, interaction, button):
        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            await self.member.ban(reason=f"Softban by {interaction.user}", delete_message_days=3)
            await interaction.guild.unban(self.member, reason="Softban")
            add_mod_log("Softban", str(self.member), str(interaction.user), "3 days messages cleared", "#faa61a")
            await interaction.response.send_message(embed=success_embed("Softbanned", f"**{self.member}** softbanned."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

    @discord.ui.button(label="🎮 View Roblox", style=discord.ButtonStyle.blurple, row=2)
    async def roblox_btn(self, interaction, button):
        rb = roblox_links.get(self.member.id)
        if not rb:
            return await interaction.response.send_message(embed=error_embed("Not Linked", f"**{self.member.display_name}** has not linked their Roblox account."), ephemeral=True)
        embed = discord.Embed(
            title=f"🎮 Roblox Profile — {rb['display']}",
            url=f"https://www.roblox.com/users/{rb['roblox_id']}/profile",
            color=0x00B2FF
        )
        embed.add_field(name="Username", value=f"`@{rb['username']}`")
        embed.add_field(name="Roblox ID", value=str(rb["roblox_id"]))
        embed.add_field(name="Verified", value="✅ Yes" if rb.get("verified") else "❓ Manual")
        embed.add_field(name="Linked", value=rb.get("linked_at", "—"))
        if rb.get("thumb"):
            embed.set_thumbnail(url=rb["thumb"])
        embed.set_footer(text="Click title to view profile")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💰 View Economy", style=discord.ButtonStyle.secondary, row=3)
    async def eco_btn(self, interaction, button):
        eco = economy_data.get(self.member.id, {})
        embed = discord.Embed(title=f"💰 Economy — {self.member.display_name}", color=0xf1c40f)
        embed.add_field(name="Balance", value=f"{eco.get('balance', 0):,} coins")
        embed.add_field(name="Bank", value=f"{eco.get('bank', 0):,} coins")
        embed.add_field(name="Total Earned", value=f"{eco.get('total_earned', 0):,} coins")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⭐ View XP", style=discord.ButtonStyle.secondary, row=3)
    async def xp_btn(self, interaction, button):
        xd = xp_data.get(self.member.id, {"xp": 0, "messages": 0})
        lv = get_level(xd.get("xp", 0))
        embed = discord.Embed(title=f"⭐ XP — {self.member.display_name}", color=0x5865f2)
        embed.add_field(name="Level", value=str(lv))
        embed.add_field(name="XP", value=str(xd.get("xp", 0)))
        embed.add_field(name="Messages", value=str(xd.get("messages", 0)))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="👑 Grant Premium", style=discord.ButtonStyle.success, row=3)
    async def premium_btn(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
        premium_users[self.member.id] = {
            "expires": (datetime.now() + timedelta(days=30)).isoformat(),
            "tier": "premium",
            "granted_by": str(interaction.user)
        }
        prem_role_id = get(interaction.guild.id, "premium_role")
        if prem_role_id:
            prem_role = interaction.guild.get_role(prem_role_id)
            if prem_role:
                try:
                    await self.member.add_roles(prem_role)
                except:
                    pass
        try:
            await self.member.send(embed=discord.Embed(
                title="👑 Premium Granted!",
                description=f"You've been granted **Premium** in {interaction.guild.name}!\n\n✅ 1.5x XP boost\n✅ Bonus daily coins\n✅ Access to premium channels",
                color=0xf1c40f
            ))
        except:
            pass
        await interaction.response.send_message(embed=success_embed("Premium Granted!", f"**{self.member}** has been given Premium for 30 days."), ephemeral=True)

    @discord.ui.button(label="🔗 Generate Invite", style=discord.ButtonStyle.secondary, row=3)
    async def invite_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
        try:
            invite = await interaction.channel.create_invite(max_uses=1, unique=True, max_age=86400)
            await self.member.send(embed=discord.Embed(
                title="🔗 Your Invite Link",
                description=f"Here's a personal invite link to **{interaction.guild.name}**:\n\n{invite.url}\n\n*Valid for 24 hours, 1 use only.*",
                color=0x5865f2
            ))
            await interaction.response.send_message(embed=success_embed("Invite Sent", f"Personal invite DM'd to **{self.member}**."), ephemeral=True)
        except:
            await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

# ============================================================
# INTERACTIVE POLL VIEW
# ============================================================
class PollView(View):
    def __init__(self, question, options):
        super().__init__(timeout=86400)
        self.votes = {i: set() for i in range(len(options))}
        self.options = options
        emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
        for i, opt in enumerate(options):
            btn = Button(label=f"{emojis[i]} {opt[:50]} — 0 votes", style=discord.ButtonStyle.secondary, custom_id=f"poll_{i}", row=i // 3)
            btn.callback = self._make_vote(i)
            self.add_item(btn)

    def _make_vote(self, idx):
        async def vote(interaction):
            uid = interaction.user.id
            for voters in self.votes.values():
                voters.discard(uid)
            self.votes[idx].add(uid)
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
            total = sum(len(v) for v in self.votes.values())
            for item in self.children:
                if hasattr(item, "custom_id") and item.custom_id.startswith("poll_"):
                    i = int(item.custom_id.split("_")[1])
                    cnt = len(self.votes[i])
                    pct = int(cnt / max(total, 1) * 100)
                    item.label = f"{emojis[i]} {self.options[i][:30]} — {cnt} ({pct}%)"
            await interaction.response.edit_message(view=self)
        return vote

# ============================================================
# SLASH COMMANDS — HELP
# ============================================================
class HelpCategorySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🏅 XP & Leveling", value="xp", description="Rank, leaderboard, XP commands"),
            discord.SelectOption(label="💰 Economy", value="economy", description="Coins, daily, work, gamble, bank"),
            discord.SelectOption(label="🎮 Fun & Games", value="fun", description="8ball, trivia, rps, ship…"),
            discord.SelectOption(label="🛠️ Utility", value="utility", description="Calc, snipe, timer, reminders…"),
            discord.SelectOption(label="🔨 Moderation", value="mod", description="Warn, kick, ban, timeout…"),
            discord.SelectOption(label="🎫 Tickets & AutoMod", value="tickets", description="Tickets, word filter…"),
            discord.SelectOption(label="ℹ️ Info & Server", value="info", description="Userinfo, serverinfo, avatar…"),
            discord.SelectOption(label="🎮 Roblox", value="roblox", description="Roblox lookup & verification"),
            discord.SelectOption(label="🎉 Events", value="events", description="Giveaways, polls, announcements"),
            discord.SelectOption(label="👑 Admin", value="admin", description="Config, premium, custom commands"),
            discord.SelectOption(label="🤖 Roblox AI", value="ai", description="AI-powered Roblox dev assistant"),
            discord.SelectOption(label="🐛 Bug Reports", value="bugs", description="Submit and track bug reports"),
        ]
        super().__init__(placeholder="Select a category…", options=options)

    async def callback(self, interaction):
        cats = {
            "xp": ("🏅 XP & Leveling", "`/rank` · `/leaderboard` · `/addxp` · `/resetxp`\n`/givexp` · `/setlevel` · `/xp-reset-all`"),
            "economy": ("💰 Economy", "`/balance` · `/daily` · `/work` · `/pay`\n`/gamble` · `/slots` · `/rob` · `/richlist`\n`/deposit` · `/withdraw` · `/heist` · `/fish`\n`/mine` · `/shop` · `/buy` · `/givecoins`"),
            "fun": ("🎮 Fun & Games", "`/8ball` · `/joke` · `/rps` · `/rate` · `/ship`\n`/truth` · `/dare` · `/wouldyou` · `/trivia`\n`/compliment` · `/mock` · `/reverse` · `/pp` · `/iq`\n`/coinflip` · `/dice` · `/choose` · `/roblox-fact`\n`/wyr` · `/nhie` · `/riddle` · `/fortune`"),
            "utility": ("🛠️ Utility", "`/snipe` · `/editsnipe` · `/calc` · `/timer`\n`/remindme` · `/afk` · `/color` · `/timestamp`\n`/charcount` · `/b64` · `/say` · `/embed`\n`/translate` · `/weather` · `/urban` · `/qr`\n`/howlong` · `/poll` · `/vote`"),
            "mod": ("🔨 Moderation", "`/warn` · `/kick` · `/ban` · `/unban`\n`/timeout` · `/untimeout` · `/mute` · `/unmute`\n`/softban` · `/tempban` · `/purge` · `/lock`\n`/unlock` · `/slowmode` · `/nick` · `/nuke`\n`/addrole` · `/removerole` · `/massrole`\n`/history` · `/warnings` · `/clearwarnings`\n`/note` · `/notes` · `/modmenu` · `/staffpanel`\n`/ban-list` · `/watch` · `/unwatch` · `/infractions`\n`/case` · `/reason` · `/modstats`"),
            "tickets": ("🎫 Tickets & AutoMod", "`/ticket` · `/closeticket` · `/adduser` · `/removeuser`\n`/addword` · `/removeword` · `/wordlist`\n`/antilink` · `/anticaps` · `/antispam`"),
            "info": ("ℹ️ Info & Server", "`/userinfo` · `/serverinfo` · `/avatar`\n`/roleinfo` · `/channelinfo` · `/servericon`\n`/ping` · `/uptime` · `/botinfo` · `/membercount`\n`/stafflist` · `/newmembers` · `/howlong`\n`/invites` · `/banner` · `/activity`\n`/voicelog` · `/nicklog` · `/joindate`"),
            "roblox": ("🎮 Roblox", "`/roblox` · `/roblox-verify` · `/roblox-link`\n`/roblox-unlink` · `/roblox-whois` · `/roblox-fact`\n`/roblox-games` · `/portfolio-upload` · `/roblox-id`"),
            "events": ("🎉 Events & Community", "`/giveaway` · `/endgiveaway` · `/rerollgiveaway`\n`/poll` · `/announce` · `/suggest` · `/serverrules`\n`/birthday` · `/starboard` · `/reactionrole`\n`/counting-stats`"),
            "admin": ("👑 Admin Commands", "`/shutdown` · `/shutdown-confirm` · `/lockdown`\n`/unlockdown` · `/massrole` · `/config` · `/resetconfig`\n`/givecoins` · `/addxp` · `/premium` · `/unpremium`\n`/customcmd` · `/delcmd` · `/listcmds`\n`/staffverify-code` · `/reactionrole`\n`/counting-reset` · `/botchannel`"),
            "ai": ("🤖 Roblox AI", "`/askai` — Ask the Roblox AI anything\n`/ai-clear` — Clear your AI conversation\n\n*The AI also responds automatically in the AI channel!\nAsk about: Lua/Luau, building, modelling, UI, DataStores, and more.*"),
            "bugs": ("🐛 Bug Reports", "`/bugreport` — Submit a bug report\n`/bugstatus` — Check a bug report status\n`/buglist` — List recent bug reports\n\n*You can also upload screenshots via DM after submitting!*"),
        }
        title, desc = cats.get(self.values[0], ("Help", "—"))
        embed = discord.Embed(title=title, description=desc, color=0x5865F2)
        embed.set_footer(text="YBS Bot · Use / prefix for all commands")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect())

    @discord.ui.button(label="🐛 Report Bug", style=discord.ButtonStyle.secondary)
    async def report_bug_btn(self, interaction, button):
        await interaction.response.send_modal(BugReportModal())

    @discord.ui.button(label="🤖 Ask AI", style=discord.ButtonStyle.blurple)
    async def ask_ai_btn(self, interaction, button):
        await interaction.response.send_modal(AIQuestionModal())

    @discord.ui.button(label="🌐 Dashboard", style=discord.ButtonStyle.secondary)
    async def dashboard_btn(self, interaction, button):
        await interaction.response.send_message(
            embed=info_embed("Dashboard", "Access the web dashboard at your hosted URL!\n\nThe dashboard shows:\n📊 Live stats\n📋 Applications\n⚠️ Warnings\n📝 Mod logs\n💰 Economy leaderboard"),
            ephemeral=True
        )

@tree.command(name="help", description="Browse all bot commands by category")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Young Boy Studios Bot — Command Center",
        description=(
            "**Select a category** from the dropdown to explore commands.\n\n"
            "🔨 **Moderation** · ⚠️ Warn, kick, ban, timeout, purge...\n"
            "🏅 **XP & Levels** · Rank, leaderboard, level roles...\n"
            "💰 **Economy** · Daily, work, gamble, bank, shop...\n"
            "🎮 **Roblox** · Lookup, verify, portfolio...\n"
            "🤖 **Roblox AI** · AI-powered dev assistant...\n"
            "🎉 **Events** · Giveaways, polls, birthdays...\n"
            "🐛 **Bug Reports** · Submit and track issues...\n"
            "👑 **Admin** · Config, premium, custom commands..."
        ),
        color=0x5865F2
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else discord.Embed.Empty)
    embed.set_footer(text="Young Boy Studios Bot · All commands use / prefix")
    embed.timestamp = discord.utils.utcnow()
    await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)

# ============================================================
# SLASH — ROBLOX
# ============================================================
@tree.command(name="roblox", description="Look up a Roblox profile")
@app_commands.describe(username="Roblox username to look up")
async def slash_roblox(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    data = await fetch_roblox(username)
    if not data:
        return await interaction.followup.send(embed=error_embed("Not Found", f"Couldn't find **{username}** on Roblox."), ephemeral=True)
    embed = discord.Embed(
        title=f"🎮 {data['display']} (@{data['name']})",
        url=f"https://www.roblox.com/users/{data['id']}/profile",
        color=0x00B2FF
    )
    embed.add_field(name="🆔 User ID", value=f"`{data['id']}`")
    embed.add_field(name="📅 Created", value=data["created"] or "—")
    embed.add_field(name="👥 Friends", value=str(data.get("friends", "—")))
    if data["desc"]:
        embed.add_field(name="📝 Bio", value=data["desc"][:300], inline=False)
    if data.get("is_banned"):
        embed.add_field(name="⚠️ Status", value="🚫 Banned from Roblox", inline=False)
    if data["thumb"]:
        embed.set_thumbnail(url=data["thumb"])
    embed.set_footer(text=f"Looked up by {interaction.user} · Click title to view profile")
    embed.timestamp = discord.utils.utcnow()
    await interaction.followup.send(embed=embed)

@tree.command(name="roblox-id", description="Look up a Roblox profile by ID")
@app_commands.describe(user_id="Roblox user ID")
async def slash_roblox_id(interaction: discord.Interaction, user_id: int):
    await interaction.response.defer()
    data = await fetch_roblox_by_id(user_id)
    if not data:
        return await interaction.followup.send(embed=error_embed("Not Found", f"No Roblox user with ID `{user_id}`."), ephemeral=True)
    embed = discord.Embed(
        title=f"🎮 {data['display']} (@{data['name']})",
        url=f"https://www.roblox.com/users/{data['id']}/profile",
        color=0x00B2FF
    )
    embed.add_field(name="🆔 ID", value=f"`{data['id']}`")
    embed.add_field(name="📅 Created", value=data["created"] or "—")
    if data["desc"]:
        embed.add_field(name="📝 Bio", value=data["desc"][:300], inline=False)
    if data["thumb"]:
        embed.set_thumbnail(url=data["thumb"])
    await interaction.followup.send(embed=embed)

@tree.command(name="roblox-verify", description="Link your Roblox account to Discord with verification")
async def slash_roblox_verify(interaction: discord.Interaction):
    if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
        linked = roblox_links[interaction.user.id]
        embed = discord.Embed(title="✅ Already Verified!", color=0x2ecc71)
        embed.add_field(name="Linked Account", value=f"**{linked['display']}** (`@{linked['username']}`)")
        embed.add_field(name="Linked On", value=linked.get("linked_at", "—"))
        if linked.get("thumb"):
            embed.set_thumbnail(url=linked["thumb"])
        embed.set_footer(text="Use /roblox-unlink to remove the link.")
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    await interaction.response.send_modal(VerifyModal())

@tree.command(name="roblox-link", description="Manually link a Discord member to a Roblox account [Admin only]")
@app_commands.describe(member="Discord member", username="Roblox username")
async def slash_roblox_link(interaction: discord.Interaction, member: discord.Member, username: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    data = await fetch_roblox(username)
    if not data:
        return await interaction.followup.send(embed=error_embed("Not Found", f"Roblox user **{username}** not found."), ephemeral=True)
    roblox_links[member.id] = {
        "username": data["name"], "roblox_id": data["id"], "display": data["display"],
        "thumb": data.get("thumb"), "discord_name": str(member),
        "linked_at": datetime.now().strftime("%d %b %Y"), "verified": False, "manual": True
    }
    add_activity("🔗", f"Manual link: {member.display_name} → {data['name']}")
    embed = discord.Embed(title="🔗 Account Linked", color=0x2ecc71)
    embed.add_field(name="Discord", value=member.mention)
    embed.add_field(name="Roblox", value=f"**{data['display']}** (`@{data['name']}`)")
    if data.get("thumb"):
        embed.set_thumbnail(url=data["thumb"])
    await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="roblox-unlink", description="Remove your Roblox account link")
async def slash_roblox_unlink(interaction: discord.Interaction):
    if interaction.user.id not in roblox_links:
        return await interaction.response.send_message(embed=error_embed("Not Linked", "You don't have a linked Roblox account."), ephemeral=True)
    old = roblox_links.pop(interaction.user.id)
    verified_role_id = get(interaction.guild.id, "verified_role")
    if verified_role_id:
        role = interaction.guild.get_role(verified_role_id)
        if role and role in interaction.user.roles:
            try: await interaction.user.remove_roles(role)
            except: pass
    await interaction.response.send_message(embed=success_embed("Unlinked", f"Roblox account **@{old['username']}** has been unlinked."), ephemeral=True)

@tree.command(name="roblox-whois", description="Find a Discord user by Roblox username")
@app_commands.describe(username="Roblox username to search for")
async def slash_roblox_whois(interaction: discord.Interaction, username: str):
    match = next((uid for uid, v in roblox_links.items() if v["username"].lower() == username.lower()), None)
    if not match:
        return await interaction.response.send_message(embed=error_embed("Not Found", f"No linked Discord user for **@{username}**."), ephemeral=True)
    member = interaction.guild.get_member(match)
    rb = roblox_links[match]
    embed = discord.Embed(title=f"🔍 Roblox → Discord Lookup", color=0x00B2FF)
    embed.add_field(name="🎮 Roblox", value=f"`@{rb['username']}`")
    embed.add_field(name="💬 Discord", value=member.mention if member else f"User `{match}`")
    embed.add_field(name="✅ Verified", value="Yes ✅" if rb.get("verified") else "No (manual)")
    embed.add_field(name="Linked", value=rb.get("linked_at", "—"))
    if rb.get("thumb"):
        embed.set_thumbnail(url=rb["thumb"])
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="roblox-fact", description="Get a random Roblox development fact")
async def slash_roblox_fact(interaction: discord.Interaction):
    facts = [
        "Roblox uses **Lua 5.1** extended with **Luau** — a custom, fast runtime with optional typing.",
        "The `game` object is the **root of the DataModel** — every service and object lives inside it.",
        "**LocalScripts** run on the client; **Scripts** run on the server. Never mix them up!",
        "Use `RunService.Heartbeat` to run code every frame — perfect for smooth animations.",
        "**DataStores** are rate-limited: ~60 requests/min per server. Always batch your saves!",
        "`task.spawn()` is the modern alternative to `coroutine.wrap()` in Roblox Luau.",
        "**Luau** adds type annotations, generics, and improved performance over standard Lua 5.1.",
        "`ModuleScripts` are your best friend for sharing code — they work like imported libraries.",
        "Always use `pcall()` around DataStore operations to handle errors gracefully.",
        "**RemoteEvents** (fire-and-forget) vs **RemoteFunctions** (request-response) — know the difference!",
        "Use `CollectionService` with tags to organize and manage groups of instances easily.",
        "The `Players.LocalPlayer` property is **only available in LocalScripts**, never in server Scripts.",
        "**StreamingEnabled** can boost performance for large worlds by only loading nearby geometry.",
        "Use `TweenService` for smooth, performance-friendly animations instead of manual loops.",
        "Roblox's physics engine handles up to **512 physics regions** in a single place.",
    ]
    embed = discord.Embed(title="💡 Roblox Dev Fact", description=random.choice(facts), color=0x00B2FF)
    embed.set_footer(text="Young Boy Studios · Dev Knowledge 🎮")
    await interaction.response.send_message(embed=embed)

@tree.command(name="portfolio-upload", description="Upload portfolio images to your application (sends DM instructions)")
async def slash_portfolio_upload(interaction: discord.Interaction):
    if interaction.user.id not in applications_data:
        return await interaction.response.send_message(embed=error_embed("No Application", "You don't have a pending application. Apply first!"), ephemeral=True)
    app = applications_data[interaction.user.id]
    img_count = len(app.get("portfolio_images", []))
    if img_count >= 5:
        return await interaction.response.send_message(embed=error_embed("Max Images", "You've already uploaded 5 images (the maximum)."), ephemeral=True)
    try:
        dm_embed = discord.Embed(
            title="📸 Portfolio Image Upload",
            description=(
                f"Send your portfolio images here as attachments!\n\n"
                f"**Current images:** {img_count}/5\n\n"
                f"Supported formats: PNG, JPG, GIF, WebP\n"
                f"Just attach your images to a message and send it here!"
            ),
            color=0x5865f2
        )
        await interaction.user.send(embed=dm_embed)
        dm_reply_map[interaction.user.id] = {"type": "portfolio"}
        await interaction.response.send_message(embed=success_embed("Check your DMs!", "I've sent you instructions for uploading portfolio images!"), ephemeral=True)
    except:
        await interaction.response.send_message(embed=error_embed("DM Failed", "Please enable DMs from server members to upload portfolio images."), ephemeral=True)

# ============================================================
# SLASH — AI
# ============================================================
@tree.command(name="askai", description="Ask the Roblox AI assistant a question")
@app_commands.describe(question="Your Roblox development question")
async def slash_askai(interaction: discord.Interaction, question: str = None):
    if question:
        await interaction.response.defer()
        thinking_embed = discord.Embed(title="🤔 Thinking...", description="The Roblox AI is working on your answer...", color=0x9B59B6)
        msg = await interaction.followup.send(embed=thinking_embed)
        answer = await ask_roblox_ai(interaction.user.id, question)
        chunks = [answer[i:i+3900] for i in range(0, len(answer), 3900)]
        embed = discord.Embed(title="🤖 Roblox AI Assistant", color=0x9B59B6, timestamp=discord.utils.utcnow())
        embed.add_field(name="❓ Question", value=question[:500], inline=False)
        embed.add_field(name="💡 Answer", value=chunks[0], inline=False)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text="YBS Roblox AI · Powered by Claude · Use /askai for more")
        await msg.edit(embed=embed, view=AIFollowUpView(interaction.user.id))
        for chunk in chunks[1:]:
            await interaction.channel.send(embed=discord.Embed(description=chunk, color=0x9B59B6))
    else:
        await interaction.response.send_modal(AIQuestionModal())

@tree.command(name="ai-clear", description="Clear your AI conversation history")
async def slash_ai_clear(interaction: discord.Interaction):
    ai_conversations.pop(interaction.user.id, None)
    await interaction.response.send_message(embed=success_embed("Conversation Cleared", "Starting fresh!"), ephemeral=True)

# ============================================================
# SLASH — BUG REPORTS
# ============================================================
@tree.command(name="bugreport", description="Submit a bug report")
async def slash_bugreport(interaction: discord.Interaction):
    await interaction.response.send_modal(BugReportModal())

@tree.command(name="bugstatus", description="Check the status of a bug report")
@app_commands.describe(report_id="Bug report ID (e.g. BUG-0001)")
async def slash_bugstatus(interaction: discord.Interaction, report_id: str):
    report = next((r for r in bug_reports if r["id"].upper() == report_id.upper()), None)
    if not report:
        return await interaction.response.send_message(embed=error_embed("Not Found", f"No bug report with ID `{report_id}`."), ephemeral=True)
    status_colors = {"open": 0xe74c3c, "in_progress": 0x3498db, "fixed": 0x2ecc71, "wont_fix": 0x95a5a6, "duplicate": 0x7f8c8d}
    embed = discord.Embed(
        title=f"🐛 {report['id']} — {report['title']}",
        color=status_colors.get(report["status"], 0x95a5a6)
    )
    embed.add_field(name="Status", value=report["status"].replace("_", " ").title())
    embed.add_field(name="Severity", value=report["severity"].title())
    embed.add_field(name="Area", value=report["game_area"])
    embed.add_field(name="Submitted", value=f"<t:{int(datetime.fromisoformat(report['timestamp']).timestamp())}:R>")
    embed.add_field(name="Images", value=str(len(report.get("images", []))))
    if report.get("fixed_by"):
        embed.add_field(name="Fixed By", value=report["fixed_by"])
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="buglist", description="View recent bug reports [Staff]")
@app_commands.describe(status="Filter by status")
@app_commands.choices(status=[
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Open", value="open"),
    app_commands.Choice(name="In Progress", value="in_progress"),
    app_commands.Choice(name="Fixed", value="fixed"),
])
async def slash_buglist(interaction: discord.Interaction, status: str = "all"):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    filtered = [r for r in bug_reports if status == "all" or r["status"] == status][-10:]
    if not filtered:
        return await interaction.response.send_message(embed=info_embed("No Reports", f"No bug reports with status: {status}"), ephemeral=True)
    embed = discord.Embed(title=f"🐛 Bug Reports ({status.title()})", color=0xe74c3c)
    for r in filtered:
        embed.add_field(
            name=f"`{r['id']}` — {r['title'][:40]}",
            value=f"**Severity:** {r['severity']} · **Status:** {r['status'].replace('_',' ').title()}\n**Area:** {r['game_area']}",
            inline=False
        )
    embed.set_footer(text=f"Showing {len(filtered)} report(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — STAFF VERIFICATION
# ============================================================
@tree.command(name="staffverify", description="Verify your daily staff code to unlock staff commands")
async def slash_staffverify(interaction: discord.Interaction):
    if interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=info_embed("Admin", "Admins don't need a staff code!"), ephemeral=True)
    staff_role_id = get(interaction.guild.id, "staff_role") or get(interaction.guild.id, "admin_role")
    if staff_role_id and staff_role_id not in [r.id for r in interaction.user.roles]:
        return await interaction.response.send_message(embed=error_embed("Not Staff", "You don't have the staff role."), ephemeral=True)
    await interaction.response.send_modal(StaffCodeModal())

@tree.command(name="staffverify-code", description="Get today's staff code [Server Owner only]")
async def slash_staffverify_code(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Owner Only", "Only the server owner can get the staff code."), ephemeral=True)
    today = datetime.now().strftime("%Y-%m-%d")
    guild_id = str(interaction.guild.id)
    daily = staff_codes.get(guild_id, {})
    if daily.get("date") != today:
        code = generate_staff_code()
        staff_codes[guild_id] = {"code": code, "date": today, "generated_at": datetime.now().strftime("%H:%M")}
    code = staff_codes[guild_id]["code"]
    embed = discord.Embed(title="🔐 Today's Staff Code", color=0x9B59B6)
    embed.add_field(name="Code", value=f"```\n{code}\n```")
    embed.add_field(name="Valid For", value=today)
    embed.set_footer(text="Share ONLY with verified staff members. Regenerates daily.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — TIMER
# ============================================================
@tree.command(name="timer", description="Set a timer in the current channel")
@app_commands.describe(duration="Duration (e.g. 30s, 5m, 1h, 2h30m)", label="Timer label")
async def slash_timer(interaction: discord.Interaction, duration: str, label: str = "Timer"):
    # Parse duration
    total_seconds = 0
    pattern = r"(\d+)\s*([smhd])"
    matches = re.findall(pattern, duration.lower())
    if not matches:
        return await interaction.response.send_message(embed=error_embed("Invalid Duration", "Use format like: `30s`, `5m`, `1h`, `2h30m`, `1d`"), ephemeral=True)
    for value, unit in matches:
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        total_seconds += int(value) * multipliers.get(unit, 0)
    if total_seconds <= 0 or total_seconds > 86400 * 7:
        return await interaction.response.send_message(embed=error_embed("Invalid Duration", "Duration must be between 1 second and 7 days."), ephemeral=True)

    timer_id = f"TIMER-{random.randint(1000, 9999)}"
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    timers_data[timer_id] = {
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id,
        "end_time": end_time.isoformat(),
        "label": label,
        "guild_id": interaction.guild.id
    }

    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    duration_str = f"{h}h {m}m {s}s".strip() if h else f"{m}m {s}s".strip() if m else f"{s}s"

    embed = discord.Embed(
        title=f"⏱️ Timer Started — {label}",
        description=f"**Duration:** {duration_str}\n**Ends:** <t:{int(end_time.timestamp())}:R> (<t:{int(end_time.timestamp())}:T>)\n**Timer ID:** `{timer_id}`",
        color=0x3498db
    )
    embed.set_footer(text=f"Started by {interaction.user.display_name}")
    embed.timestamp = discord.utils.utcnow()
    await interaction.response.send_message(embed=embed, view=TimerView(timer_id))

    # Wait and send notification
    await asyncio.sleep(total_seconds)
    if timer_id in timers_data:
        timers_data.pop(timer_id)
        channel = bot.get_channel(interaction.channel.id)
        if channel:
            done_embed = discord.Embed(
                title=f"⏰ Timer Done — {label}",
                description=f"{interaction.user.mention} your timer has finished!\n\n**Label:** {label}\n**Duration:** {duration_str}",
                color=0x2ecc71
            )
            done_embed.timestamp = discord.utils.utcnow()
            await channel.send(embed=done_embed)

# ============================================================
# SLASH — MODERATION (EXPANDED)
# ============================================================
@tree.command(name="modmenu", description="Open full moderation control panel for a member [Staff]")
@app_commands.describe(member="Member to moderate")
async def slash_modmenu(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    warns = len(warnings_data.get(member.id, []))
    notes_count = len(notes_data.get(str(member.id), []))
    rb = roblox_links.get(member.id)
    eco = economy_data.get(member.id, {})
    xd = xp_data.get(member.id, {"xp": 0})
    embed = discord.Embed(title=f"⚖️ Mod Control Panel — {member.display_name}", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 User", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="📅 Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "?", inline=True)
    embed.add_field(name="🎭 Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="⚠️ Warnings", value=f"`{warns}`", inline=True)
    embed.add_field(name="📝 Notes", value=f"`{notes_count}`", inline=True)
    embed.add_field(name="🔇 Timed Out", value="✅ Yes" if member.is_timed_out() else "❌ No", inline=True)
    embed.add_field(name="📅 Account Created", value=f"<t:{int(member.created_at.timestamp())}:R>", inline=True)
    embed.add_field(name="🎮 Roblox", value=f"`@{rb['username']}`" if rb else "Not linked", inline=True)
    embed.add_field(name="⭐ Level", value=str(get_level(xd.get("xp", 0))), inline=True)
    embed.add_field(name="💰 Balance", value=f"{eco.get('balance', 0):,} coins", inline=True)
    embed.add_field(name="✅ Verified", value="Yes" if rb and rb.get("verified") else "No", inline=True)
    embed.add_field(name="👑 Premium", value="Yes" if member.id in premium_users else "No", inline=True)
    embed.set_footer(text="All actions are logged · Young Boy Studios Mod System")
    await interaction.response.send_message(embed=embed, view=ModMenuView(member), ephemeral=True)

@tree.command(name="warn", description="Warn a member [Staff]")
@app_commands.describe(member="Member to warn", reason="Reason")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    if member.id not in warnings_data: warnings_data[member.id] = []
    warnings_data[member.id].append({"reason": reason, "by": str(interaction.user), "time": datetime.now().isoformat()})
    count = len(warnings_data[member.id])
    add_mod_log("Warn", str(member), str(interaction.user), reason, "#faa61a")
    add_activity("⚠️", f"{member.display_name} warned", reason)
    try:
        await member.send(embed=discord.Embed(
            title="⚠️ Warning Received",
            description=f"You were warned in **{interaction.guild.name}**\n\n**Reason:** {reason}\n**Warnings:** {count}/3",
            color=0xf39c12
        ))
    except: pass
    mod_log_id = get(interaction.guild.id, "mod_channel") or get(interaction.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = mod_embed("Member Warned", color=0xf39c12)
        embed.add_field(name="Member", value=member.mention)
        embed.add_field(name="Warned By", value=interaction.user.mention)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Total Warnings", value=f"{count}/3")
        await log.send(embed=embed)
    embed = success_embed("Warned", f"**{member}** warned.\n**Reason:** {reason}\n**Total warnings:** {count}")
    if count >= 3:
        embed.add_field(name="⚠️ Alert", value=f"{member.mention} has hit 3 warnings! Consider further action.")
    await interaction.response.send_message(embed=embed)

@tree.command(name="kick", description="Kick a member [Staff]")
@app_commands.describe(member="Member to kick", reason="Reason")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        try:
            await member.send(embed=discord.Embed(title="👢 Kicked", description=f"You've been kicked from **{interaction.guild.name}**\n**Reason:** {reason}", color=0xe74c3c))
        except: pass
        await member.kick(reason=reason)
        add_mod_log("Kick", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(embed=success_embed("Kicked", f"**{member}** has been kicked.\n**Reason:** {reason}"))
    except:
        await interaction.response.send_message(embed=error_embed("Failed", "Couldn't kick that member."), ephemeral=True)

@tree.command(name="ban", description="Ban a member [Staff]")
@app_commands.describe(member="Member to ban", reason="Reason", delete_days="Days of messages to delete")
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason", delete_days: int = 1):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        try:
            await member.send(embed=discord.Embed(title="🔨 Banned", description=f"You've been banned from **{interaction.guild.name}**\n**Reason:** {reason}", color=0xe74c3c))
        except: pass
        await member.ban(reason=reason, delete_message_days=max(0, min(7, delete_days)))
        add_mod_log("Ban", str(member), str(interaction.user), reason, "#ed4245")
        ban_log_data.append({"user": str(member), "uid": member.id, "reason": reason, "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
        await interaction.response.send_message(embed=success_embed("Banned", f"**{member}** has been banned.\n**Reason:** {reason}"))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID [Staff]")
@app_commands.describe(user_id="Discord user ID", reason="Reason")
async def slash_unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        add_mod_log("Unban", str(user), str(interaction.user), reason, "#3ba55c")
        await interaction.response.send_message(embed=success_embed("Unbanned", f"**{user}** has been unbanned."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed", "User not found or not banned."), ephemeral=True)

@tree.command(name="timeout", description="Timeout a member [Staff]")
@app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
async def slash_timeout(interaction: discord.Interaction, member: discord.Member, minutes: int = 5, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
        add_mod_log("Timeout", str(member), str(interaction.user), f"{minutes}m — {reason}", "#faa61a")
        await interaction.response.send_message(embed=success_embed("Timed Out", f"**{member}** timed out for **{minutes} minutes**.\n**Reason:** {reason}"))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="untimeout", description="Remove a member's timeout [Staff]")
@app_commands.describe(member="Member to untimeout")
async def slash_untimeout(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.timeout(None)
        await interaction.response.send_message(embed=success_embed("Timeout Removed", f"**{member}**'s timeout has been removed."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="mute", description="Mute a member [Staff]")
@app_commands.describe(member="Member", hours="Duration in hours", reason="Reason")
async def slash_mute(interaction: discord.Interaction, member: discord.Member, hours: float = 1.0, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.timeout(discord.utils.utcnow() + timedelta(hours=min(hours, 672)), reason=reason)
        add_mod_log("Mute", str(member), str(interaction.user), f"{hours}h — {reason}", "#faa61a")
        await interaction.response.send_message(embed=success_embed("Muted", f"**{member}** muted for **{hours}h**.\n**Reason:** {reason}"))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="unmute", description="Unmute a member [Staff]")
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.timeout(None)
        await interaction.response.send_message(embed=success_embed("Unmuted", f"**{member}** has been unmuted."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="softban", description="Softban — ban+unban to clear messages [Staff]")
@app_commands.describe(member="Member", reason="Reason", delete_days="Days of messages to delete (1-7)")
async def slash_softban(interaction: discord.Interaction, member: discord.Member, reason: str = "Softban", delete_days: int = 3):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.ban(reason=f"Softban: {reason}", delete_message_days=max(1, min(7, delete_days)))
        await interaction.guild.unban(member, reason="Softban")
        add_mod_log("Softban", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(embed=success_embed("Softbanned", f"**{member}** softbanned. They can rejoin."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="tempban", description="Temporarily ban a member [Staff]")
@app_commands.describe(member="Member", hours="Ban duration in hours", reason="Reason")
async def slash_tempban(interaction: discord.Interaction, member: discord.Member, hours: float = 24.0, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.ban(reason=f"Tempban {hours}h: {reason}", delete_message_days=1)
        add_mod_log("Tempban", str(member), str(interaction.user), f"{hours}h — {reason}", "#ed4245")
        unban_at = (datetime.now() + timedelta(hours=hours)).isoformat()
        temp_bans.append({"user_id": member.id, "guild_id": interaction.guild.id, "unban_at": unban_at, "reason": reason})
        ban_log_data.append({"user": str(member), "uid": member.id, "reason": f"Tempban {hours}h: {reason}", "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
        await interaction.response.send_message(embed=success_embed("Temp Banned", f"**{member}** banned for **{hours}h**.\nThey will be automatically unbanned."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="purge", description="Delete messages in bulk [Staff]")
@app_commands.describe(amount="Messages to delete (1-100)", member="Only delete from this member")
async def slash_purge(interaction: discord.Interaction, amount: int = 10, member: discord.Member = None):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    if member:
        def check(m): return m.author == member
        deleted = await interaction.channel.purge(limit=max(1, min(100, amount)), check=check)
    else:
        deleted = await interaction.channel.purge(limit=max(1, min(100, amount)))
    await interaction.followup.send(embed=success_embed("Purged", f"Deleted **{len(deleted)}** messages." + (f" from {member.mention}" if member else "")), ephemeral=True)

@tree.command(name="lock", description="Lock a channel [Staff]")
@app_commands.describe(channel="Channel to lock", reason="Reason")
async def slash_lock(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = discord.Embed(title="🔒 Channel Locked", description=f"**Reason:** {reason}\n**Locked by:** {interaction.user.mention}", color=0xe74c3c, timestamp=discord.utils.utcnow())
    await ch.send(embed=embed)
    await interaction.response.send_message(embed=success_embed("Locked", f"{ch.mention} has been locked."), ephemeral=True)

@tree.command(name="unlock", description="Unlock a channel [Staff]")
@app_commands.describe(channel="Channel to unlock")
async def slash_unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=None)
    embed = discord.Embed(title="🔓 Channel Unlocked", description=f"Unlocked by {interaction.user.mention}.", color=0x2ecc71, timestamp=discord.utils.utcnow())
    await ch.send(embed=embed)
    await interaction.response.send_message(embed=success_embed("Unlocked", f"{ch.mention} is now unlocked."), ephemeral=True)

@tree.command(name="slowmode", description="Set slowmode [Staff]")
@app_commands.describe(seconds="Delay in seconds (0=off)", channel="Channel")
async def slash_slowmode(interaction: discord.Interaction, seconds: int = 0, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    await ch.edit(slowmode_delay=max(0, min(21600, seconds)))
    msg = f"⏱️ Slowmode **disabled** in {ch.mention}." if seconds == 0 else f"⏱️ Slowmode **{seconds}s** set in {ch.mention}."
    await interaction.response.send_message(embed=success_embed("Slowmode Updated", msg))

@tree.command(name="nuke", description="Clone and delete this channel [Staff]")
@app_commands.describe(reason="Reason")
async def slash_nuke(interaction: discord.Interaction, reason: str = "Channel nuke"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    ch = interaction.channel
    await interaction.response.send_message(embed=info_embed("Nuking...", "💥 Stand back!"), ephemeral=True)
    new_ch = await ch.clone(reason=f"Nuked by {interaction.user}: {reason}")
    await ch.delete()
    embed = discord.Embed(title="💥 Channel Nuked", description=f"Nuked by **{interaction.user}**.\n**Reason:** {reason}", color=0xe74c3c, timestamp=discord.utils.utcnow())
    await new_ch.send(embed=embed)
    add_activity("💥", f"#{ch.name} nuked", reason)

@tree.command(name="nick", description="Change a member's nickname [Staff]")
@app_commands.describe(member="Member", nickname="New nickname (blank to clear)")
async def slash_nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    if not interaction.user.guild_permissions.manage_nicknames:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    old_nick = member.nick
    try:
        await member.edit(nick=nickname)
        nickname_logs.append({
            "user": str(member), "uid": member.id,
            "before": old_nick, "after": nickname,
            "changed_by": str(interaction.user),
            "time": datetime.now().strftime("%H:%M · %d %b")
        })
        await interaction.response.send_message(embed=success_embed("Nickname Changed", f"{'Set to **' + nickname + '**' if nickname else 'Cleared'} for **{member}**."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="addrole", description="Add a role to a member [Staff]")
@app_commands.describe(member="Member", role="Role to add")
async def slash_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.add_roles(role)
        await interaction.response.send_message(embed=success_embed("Role Added", f"{role.mention} added to **{member}**."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="removerole", description="Remove a role from a member [Staff]")
@app_commands.describe(member="Member", role="Role to remove")
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    try:
        await member.remove_roles(role)
        await interaction.response.send_message(embed=success_embed("Role Removed", f"{role.mention} removed from **{member}**."))
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="massrole", description="Add or remove a role from all members [Admin]")
@app_commands.describe(role="The role")
@app_commands.choices(action=[app_commands.Choice(name="➕ Add to all", value="add"), app_commands.Choice(name="➖ Remove from all", value="remove")])
async def slash_massrole(interaction: discord.Interaction, action: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    count = 0
    for member in interaction.guild.members:
        if member.bot: continue
        try:
            if action == "add" and role not in member.roles:
                await member.add_roles(role); count += 1
            elif action == "remove" and role in member.roles:
                await member.remove_roles(role); count += 1
        except: pass
    await interaction.followup.send(embed=success_embed("Mass Role", f"**{action.title()}ed** {role.mention} for **{count}** members."), ephemeral=True)

@tree.command(name="ban-list", description="View recent bans [Staff]")
async def slash_banlist(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    recent = ban_log_data[-10:]
    if not recent:
        return await interaction.response.send_message(embed=info_embed("No Bans", "No ban history recorded."), ephemeral=True)
    embed = discord.Embed(title="🔨 Recent Bans", color=0xe74c3c)
    for b in recent:
        embed.add_field(
            name=f"{b['user']}",
            value=f"**Reason:** {b['reason']}\n**By:** {b.get('by', '—')}\n**Time:** {b['time']}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
async def slash_warnings(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    warns = warnings_data.get(member.id, [])
    embed = discord.Embed(title=f"⚠️ Warnings — {member.display_name}", color=0xf39c12)
    embed.set_thumbnail(url=member.display_avatar.url)
    if warns:
        embed.description = f"**{len(warns)} total warning(s)**"
        for i, w in enumerate(warns, 1):
            embed.add_field(name=f"#{i} — {w['time'][:10]}", value=f"**Reason:** {w['reason']}\n**By:** {w['by'].split('#')[0]}", inline=False)
    else:
        embed.description = "✅ No warnings on record. Clean slate!"
    await interaction.response.send_message(embed=embed)

@tree.command(name="clearwarnings", description="Clear all warnings for a member [Staff]")
@app_commands.describe(member="Member")
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    count = len(warnings_data.pop(member.id, []))
    await interaction.response.send_message(embed=success_embed("Warnings Cleared", f"Cleared **{count}** warning(s) for **{member}**."))

@tree.command(name="history", description="View full moderation history [Staff]")
@app_commands.describe(member="Member to check")
async def slash_history(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    warns = warnings_data.get(member.id, [])
    user_notes = notes_data.get(str(member.id), [])
    rb = roblox_links.get(member.id)
    embed = discord.Embed(title=f"📋 Mod History — {member}", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings", value=f"`{len(warns)}`", inline=True)
    embed.add_field(name="📝 Notes", value=f"`{len(user_notes)}`", inline=True)
    embed.add_field(name="🎮 Roblox", value=f"`@{rb['username']}`" if rb else "Not linked", inline=True)
    embed.add_field(name="📅 Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "?", inline=True)
    embed.add_field(name="🔇 Currently Muted", value="Yes" if member.is_timed_out() else "No", inline=True)
    embed.add_field(name="👑 Premium", value="Yes" if member.id in premium_users else "No", inline=True)
    if warns:
        embed.add_field(name="Recent Warnings", value="\n".join(f"• **{w['reason'][:55]}** *({w['time'][:10]})*" for w in warns[-8:]), inline=False)
    if user_notes:
        embed.add_field(name="Staff Notes", value="\n".join(f"• {n['note'][:70]} *(by {n['by'].split('#')[0]}, {n['time']})*" for n in user_notes[-5:]), inline=False)
    if not warns and not user_notes:
        embed.description = "✅ Clean history — no warnings or notes on record."
    await interaction.response.send_message(embed=embed)

@tree.command(name="note", description="Add a staff note to a member [Staff]")
@app_commands.describe(member="Member", note="Note content")
async def slash_note(interaction: discord.Interaction, member: discord.Member, note: str):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    uid = str(member.id)
    if uid not in notes_data: notes_data[uid] = []
    notes_data[uid].append({"note": note, "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
    await interaction.response.send_message(embed=success_embed("Note Added", f"Note added for **{member}**."), ephemeral=True)

@tree.command(name="notes", description="View staff notes for a member [Staff]")
@app_commands.describe(member="Member")
async def slash_notes(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    user_notes = notes_data.get(str(member.id), [])
    embed = discord.Embed(title=f"📝 Staff Notes — {member.display_name}", color=0x5865f2)
    if user_notes:
        for i, n in enumerate(user_notes, 1):
            embed.add_field(name=f"Note #{i} — {n['time']}", value=f"{n['note']}\n*by {n['by'].split('#')[0]}*", inline=False)
    else:
        embed.description = "📭 No notes on record."
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="modstats", description="View moderation statistics [Staff]")
async def slash_modstats(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    total_warns = sum(len(v) for v in warnings_data.values())
    total_bans = len(ban_log_data)
    total_logs = len(mod_log_data)
    total_notes = sum(len(v) for v in notes_data.values())
    action_counts = {}
    for log in mod_log_data:
        action_counts[log["action"]] = action_counts.get(log["action"], 0) + 1
    embed = discord.Embed(title="📊 Moderation Statistics", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.add_field(name="⚠️ Total Warnings", value=f"`{total_warns}`")
    embed.add_field(name="🔨 Total Bans", value=f"`{total_bans}`")
    embed.add_field(name="📋 Total Mod Actions", value=f"`{total_logs}`")
    embed.add_field(name="📝 Staff Notes", value=f"`{total_notes}`")
    embed.add_field(name="🎫 Open Tickets", value=f"`{sum(1 for t in ticket_data.values() if t.get('status') == 'open')}`")
    embed.add_field(name="🤖 AutoMod Filters", value=f"`{sum(len(v) for v in automod_data.values())}`")
    if action_counts:
        top_actions = sorted(action_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        embed.add_field(name="Top Actions", value="\n".join(f"**{a}:** {c}" for a, c in top_actions), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="infractions", description="View all users with warnings [Staff]")
async def slash_infractions(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    if not warnings_data:
        return await interaction.response.send_message(embed=info_embed("No Infractions", "No members have warnings."), ephemeral=True)
    sorted_warns = sorted(warnings_data.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    embed = discord.Embed(title="⚠️ Top Infractions", color=0xf39c12)
    for uid, warns in sorted_warns:
        member = interaction.guild.get_member(uid)
        name = member.display_name if member else f"User {uid}"
        embed.add_field(name=f"{name}", value=f"**{len(warns)}** warning(s)", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — INFO
# ============================================================
@tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(member="Member")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"👤 {member}", color=member.color if str(member.color) != "#000000" else 0x5865f2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🆔 ID", value=f"`{member.id}`")
    embed.add_field(name="📅 Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "?")
    embed.add_field(name="📅 Joined Discord", value=f"<t:{int(member.created_at.timestamp())}:R>")
    embed.add_field(name="🎭 Roles", value=str(len(member.roles) - 1))
    embed.add_field(name="⚠️ Warnings", value=str(len(warnings_data.get(member.id, []))))
    embed.add_field(name="🔇 Timed Out", value="Yes" if member.is_timed_out() else "No")
    xd = xp_data.get(member.id, {})
    if xd:
        embed.add_field(name="⭐ Level", value=str(get_level(xd.get("xp", 0))))
        embed.add_field(name="📊 XP", value=str(xd.get("xp", 0)))
    eco = economy_data.get(member.id, {})
    if eco:
        embed.add_field(name="💰 Coins", value=f"{eco.get('balance', 0):,}")
    rb = roblox_links.get(member.id)
    if rb:
        embed.add_field(name="🎮 Roblox", value=f"`@{rb['username']}`" + (" ✅" if rb.get("verified") else ""))
    if member.id in premium_users:
        embed.add_field(name="👑 Premium", value="Yes")
    if member.top_role.name != "@everyone":
        embed.add_field(name="🏅 Top Role", value=member.top_role.mention)
    embed.set_footer(text=f"Requested by {interaction.user}")
    embed.timestamp = discord.utils.utcnow()
    await interaction.response.send_message(embed=embed)

@tree.command(name="serverinfo", description="View server info")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    bots = sum(1 for m in g.members if m.bot)
    online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
    embed = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2, timestamp=discord.utils.utcnow())
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    embed.add_field(name="👑 Owner", value=str(g.owner))
    embed.add_field(name="👥 Members", value=str(g.member_count))
    embed.add_field(name="🟢 Online", value=str(online))
    embed.add_field(name="🤖 Bots", value=str(bots))
    embed.add_field(name="💬 Channels", value=str(len(g.channels)))
    embed.add_field(name="🎭 Roles", value=str(len(g.roles)))
    embed.add_field(name="📅 Created", value=f"<t:{int(g.created_at.timestamp())}:R>")
    embed.add_field(name="💜 Boosts", value=str(g.premium_subscription_count))
    embed.add_field(name="📋 Applications", value=str(len(applications_data)))
    embed.add_field(name="🎮 Roblox Linked", value=str(len(roblox_links)))
    embed.set_footer(text=f"Server ID: {g.id}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="avatar", description="View a member's avatar")
@app_commands.describe(member="Member")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", color=0x5865F2)
    embed.set_image(url=member.display_avatar.url)
    embed.add_field(name="PNG", value=f"[Link]({member.display_avatar.with_format('png').url})")
    embed.add_field(name="JPG", value=f"[Link]({member.display_avatar.with_format('jpg').url})")
    embed.add_field(name="WebP", value=f"[Link]({member.display_avatar.with_format('webp').url})")
    await interaction.response.send_message(embed=embed)

@tree.command(name="banner", description="View a member's banner")
@app_commands.describe(member="Member")
async def slash_banner(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    try:
        user = await bot.fetch_user(member.id)
        if user.banner:
            embed = discord.Embed(title=f"🖼️ {member.display_name}'s Banner", color=0x5865F2)
            embed.set_image(url=user.banner.url)
            await interaction.response.send_message(embed=embed)
        else:
            await interaction.response.send_message(embed=info_embed("No Banner", f"**{member.display_name}** doesn't have a banner."), ephemeral=True)
    except:
        await interaction.response.send_message(embed=error_embed("Failed"), ephemeral=True)

@tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    ws_lat = round(bot.latency * 1000)
    color = 0x2ecc71 if ws_lat < 80 else 0xf39c12 if ws_lat < 150 else 0xe74c3c
    embed = discord.Embed(title="🏓 Pong!", color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="📡 WebSocket", value=f"`{ws_lat}ms`")
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str() or '—'}`")
    embed.add_field(name="📊 Servers", value=f"`{len(bot.guilds)}`")
    embed.add_field(name="👥 Total Members", value=f"`{sum(g.member_count for g in bot.guilds)}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="uptime", description="Check bot uptime")
async def slash_uptime(interaction: discord.Interaction):
    embed = discord.Embed(title="⏱️ Bot Uptime", description=f"**{uptime_str() or 'just started!'}**", color=0x2ecc71)
    if bot_start_time:
        embed.add_field(name="Started", value=f"<t:{int(bot_start_time.timestamp())}:R>")
    await interaction.response.send_message(embed=embed)

@tree.command(name="botinfo", description="View bot information")
async def slash_botinfo(interaction: discord.Interaction):
    embed = discord.Embed(title="🤖 Young Boy Studios Bot", color=0x5865f2, timestamp=discord.utils.utcnow())
    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str() or '—'}`")
    embed.add_field(name="📊 Servers", value=f"`{len(bot.guilds)}`")
    embed.add_field(name="🏓 Ping", value=f"`{round(bot.latency * 1000)}ms`")
    embed.add_field(name="⚡ Commands", value=f"`{len(tree.get_commands())}`")
    embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
    embed.add_field(name="🎮 Roblox Linked", value=f"`{len(roblox_links)}`")
    embed.add_field(name="👾 XP Members", value=f"`{len(xp_data)}`")
    embed.add_field(name="💰 Economy Users", value=f"`{len(economy_data)}`")
    embed.add_field(name="🎫 Tickets", value=f"`{len(ticket_data)}`")
    embed.add_field(name="🐛 Bug Reports", value=f"`{len(bug_reports)}`")
    embed.add_field(name="⭐ Starred Messages", value=f"`{sum(len(v) for v in starboard_data.values())}`")
    embed.add_field(name="👑 Premium Users", value=f"`{len(premium_users)}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="membercount", description="Member count breakdown")
async def slash_membercount(interaction: discord.Interaction):
    g = interaction.guild
    bots = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
    idle = sum(1 for m in g.members if m.status == discord.Status.idle and not m.bot)
    dnd = sum(1 for m in g.members if m.status == discord.Status.dnd and not m.bot)
    embed = discord.Embed(title=f"👥 {g.name} — Member Count", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.add_field(name="👥 Total", value=f"`{g.member_count}`")
    embed.add_field(name="🧑 Humans", value=f"`{humans}`")
    embed.add_field(name="🤖 Bots", value=f"`{bots}`")
    embed.add_field(name="🟢 Online", value=f"`{online}`")
    embed.add_field(name="🟡 Idle", value=f"`{idle}`")
    embed.add_field(name="🔴 DND", value=f"`{dnd}`")
    embed.add_field(name="💬 Channels", value=f"`{len(g.channels)}`")
    embed.add_field(name="🎭 Roles", value=f"`{len(g.roles)}`")
    embed.add_field(name="📋 Apps", value=f"`{len(applications_data)}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="roleinfo", description="View info about a role")
@app_commands.describe(role="Role to inspect")
async def slash_roleinfo(interaction: discord.Interaction, role: discord.Role):
    perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
    embed = discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="🆔 ID", value=f"`{role.id}`")
    embed.add_field(name="🎨 Color", value=f"`{str(role.color)}`")
    embed.add_field(name="👥 Members", value=f"`{len(role.members)}`")
    embed.add_field(name="💬 Mentionable", value="✅" if role.mentionable else "❌")
    embed.add_field(name="📌 Hoisted", value="✅" if role.hoist else "❌")
    embed.add_field(name="🤖 Managed", value="✅" if role.managed else "❌")
    embed.add_field(name="📅 Created", value=f"<t:{int(role.created_at.timestamp())}:R>")
    embed.add_field(name="📊 Position", value=f"`{role.position}`")
    if perms:
        embed.add_field(name="🔑 Key Permissions", value=", ".join(perms[:15]), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="stafflist", description="View all staff members")
async def slash_stafflist(interaction: discord.Interaction):
    staff = []
    for member in interaction.guild.members:
        if member.bot: continue
        if member.guild_permissions.administrator: staff.append((member, "🔴 Admin"))
        elif member.guild_permissions.ban_members: staff.append((member, "🟠 Moderator"))
        elif member.guild_permissions.kick_members: staff.append((member, "🟡 Jr. Mod"))
        elif member.guild_permissions.manage_messages: staff.append((member, "🟢 Helper"))
    embed = discord.Embed(title=f"👮 Staff List — {interaction.guild.name}", color=0x5865f2, timestamp=discord.utils.utcnow())
    if staff:
        embed.description = "\n".join(f"{rank} **{m.display_name}**" for m, rank in staff[:25])
    else:
        embed.description = "No staff members found."
    embed.set_footer(text=f"{len(staff)} staff member(s)")
    await interaction.response.send_message(embed=embed)

@tree.command(name="newmembers", description="View 10 most recently joined members")
async def slash_newmembers(interaction: discord.Interaction):
    members = sorted([m for m in interaction.guild.members if not m.bot], key=lambda m: m.joined_at or datetime.min, reverse=True)[:10]
    embed = discord.Embed(title="🆕 Newest Members", color=0x5865f2)
    embed.description = "\n".join(f"`{i+1}.` **{m.display_name}** — <t:{int(m.joined_at.timestamp())}:R>" if m.joined_at else f"`{i+1}.` **{m.display_name}**" for i, m in enumerate(members))
    await interaction.response.send_message(embed=embed)

@tree.command(name="howlong", description="See how long someone has been in the server")
@app_commands.describe(member="Member")
async def slash_howlong(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    if not member.joined_at:
        return await interaction.response.send_message(embed=error_embed("Unknown"), ephemeral=True)
    delta = datetime.now(member.joined_at.tzinfo) - member.joined_at
    days = delta.days
    embed = discord.Embed(title=f"📅 Server Tenure — {member.display_name}", color=0x5865f2)
    embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:F>")
    embed.add_field(name="Time in Server", value=f"**{days}** days")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="joindate", description="View when a member joined")
@app_commands.describe(member="Member")
async def slash_joindate(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"📅 Join Info — {member.display_name}", color=0x5865f2)
    if member.joined_at:
        embed.add_field(name="Joined Server", value=f"<t:{int(member.joined_at.timestamp())}:F>")
    embed.add_field(name="Created Account", value=f"<t:{int(member.created_at.timestamp())}:F>")
    await interaction.response.send_message(embed=embed)

@tree.command(name="channelinfo", description="View info about a channel")
@app_commands.describe(channel="Channel to inspect")
async def slash_channelinfo(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    embed = discord.Embed(title=f"💬 #{ch.name}", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.add_field(name="🆔 ID", value=f"`{ch.id}`")
    embed.add_field(name="📂 Category", value=str(ch.category) if ch.category else "None")
    embed.add_field(name="🔒 NSFW", value="Yes" if ch.is_nsfw() else "No")
    embed.add_field(name="⏱️ Slowmode", value=f"{ch.slowmode_delay}s")
    embed.add_field(name="📅 Created", value=f"<t:{int(ch.created_at.timestamp())}:R>")
    if ch.topic:
        embed.add_field(name="📝 Topic", value=ch.topic, inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="servericon", description="View the server icon")
async def slash_servericon(interaction: discord.Interaction):
    g = interaction.guild
    if not g.icon:
        return await interaction.response.send_message(embed=error_embed("No Icon", "This server has no icon."), ephemeral=True)
    embed = discord.Embed(title=f"🖼️ {g.name}'s Icon", color=0x5865f2)
    embed.set_image(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="voicelog", description="View recent voice channel activity [Staff]")
async def slash_voicelog(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    if not voice_log:
        return await interaction.response.send_message(embed=info_embed("No Voice Log", "No voice activity recorded."), ephemeral=True)
    embed = discord.Embed(title="🎙️ Recent Voice Activity", color=0x5865f2)
    for entry in voice_log[:10]:
        action_emojis = {"joined": "🟢", "left": "🔴", "moved": "↔️"}
        embed.add_field(
            name=f"{action_emojis.get(entry['action'], '•')} {entry['member']}",
            value=f"**{entry['action'].title()}** #{entry['channel']}\n*{entry['time']}*",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="nicklog", description="View recent nickname changes [Staff]")
async def slash_nicklog(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    if not nickname_logs:
        return await interaction.response.send_message(embed=info_embed("No Log", "No nickname changes recorded."), ephemeral=True)
    embed = discord.Embed(title="📝 Recent Nickname Changes", color=0x3498db)
    for log in nickname_logs[-10:]:
        embed.add_field(
            name=log["user"],
            value=f"`{log['before'] or 'None'}` → `{log['after'] or 'None'}`\n*{log['time']}*",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — XP
# ============================================================
@tree.command(name="rank", description="View your rank card")
@app_commands.describe(member="Member to check")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    d = xp_data.get(member.id, {"xp": 0, "messages": 0, "name": str(member)})
    xp = d["xp"]
    lv = get_level(xp)
    nxt = xp_for_level(lv + 1)
    prv = xp_for_level(lv)
    pct = int((xp - prv) / max(nxt - prv, 1) * 100)
    pos = next((i + 1 for i, (uid, _) in enumerate(sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)) if uid == member.id), "?")
    bar_filled = "█" * (pct // 10)
    bar_empty = "░" * (10 - pct // 10)
    embed = discord.Embed(title=f"🏅 {member.display_name}", color=0x5865F2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⭐ Level", value=f"**{lv}**")
    embed.add_field(name="📊 XP", value=f"**{xp:,}** / {nxt:,}")
    embed.add_field(name="🏆 Rank", value=f"**#{pos}**")
    embed.add_field(name="💬 Messages", value=f"**{d.get('messages', 0):,}**")
    embed.add_field(name="📈 Progress", value=f"`{bar_filled}{bar_empty}` {pct}% to Level {lv+1}", inline=False)
    if member.id in premium_users:
        embed.add_field(name="👑 Premium", value="1.5x XP Boost!", inline=False)
    embed.set_footer(text="Young Boy Studios · XP System")
    await interaction.response.send_message(embed=embed)

@tree.command(name="leaderboard", description="XP and coin leaderboards")
async def slash_leaderboard(interaction: discord.Interaction):
    embed = discord.Embed(title="🏆 Young Boy Studios Leaderboards", color=0x5865F2, timestamp=discord.utils.utcnow())
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    if xp_data:
        top = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:5]
        embed.add_field(
            name="🏅 XP Rankings",
            value="\n".join(f"{medals[i]} **{d.get('name','?').split('#')[0]}** — Lv {get_level(d['xp'])} · {d['xp']:,} XP" for i, (_, d) in enumerate(top)),
            inline=False
        )
    if economy_data:
        top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:5]
        embed.add_field(
            name="💰 Rich List",
            value="\n".join(f"{medals[i]} **{d.get('name','?').split('#')[0]}** — 💰 {d['balance']:,}" for i, (_, d) in enumerate(top)),
            inline=False
        )
    if not xp_data and not economy_data:
        embed.description = "No data yet! Start chatting to earn XP!"
    embed.set_footer(text="Updated in real-time")
    await interaction.response.send_message(embed=embed)

@tree.command(name="addxp", description="Add XP to a member [Admin]")
@app_commands.describe(member="Member", amount="XP to add")
async def slash_addxp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    if member.id not in xp_data:
        xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    xp_data[member.id]["xp"] += amount
    xp_data[member.id]["level"] = get_level(xp_data[member.id]["xp"])
    await interaction.response.send_message(embed=success_embed("XP Added", f"Gave **{amount:,} XP** to {member.mention}."))

@tree.command(name="resetxp", description="Reset a member's XP [Admin]")
@app_commands.describe(member="Member")
async def slash_resetxp(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    xp_data.pop(member.id, None)
    await interaction.response.send_message(embed=success_embed("XP Reset", f"XP reset for {member.mention}."))

@tree.command(name="setlevel", description="Set a member's level [Admin]")
@app_commands.describe(member="Member", level="Level to set")
async def slash_setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    xp = xp_for_level(max(0, level))
    if member.id not in xp_data:
        xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    xp_data[member.id]["xp"] = xp
    xp_data[member.id]["level"] = level
    await interaction.response.send_message(embed=success_embed("Level Set", f"Set **{member.mention}** to Level **{level}**."))

# ============================================================
# SLASH — ECONOMY
# ============================================================
@tree.command(name="balance", description="Check YBS Coins balance")
@app_commands.describe(member="Member to check")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    eco = get_economy(member.id, str(member))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xf1c40f)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="💵 Wallet", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="🏦 Bank", value=f"**{eco.get('bank', 0):,} coins**")
    embed.add_field(name="💎 Total", value=f"**{eco['balance'] + eco.get('bank', 0):,} coins**")
    embed.add_field(name="📈 Total Earned", value=f"{eco['total_earned']:,} coins")
    embed.set_footer(text="YBS Economy · Use /daily to earn more!")
    await interaction.response.send_message(embed=embed)

@tree.command(name="daily", description="Claim your daily YBS Coins")
async def slash_daily(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_daily"] and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400:
        diff = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
        h, rem = divmod(int(diff), 3600)
        m = rem // 60
        return await interaction.response.send_message(embed=error_embed("Already Claimed", f"Come back in **{h}h {m}m**!"), ephemeral=True)
    amt = random.randint(200, 500)
    bonus_text = ""
    if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
        bonus = random.randint(50, 100)
        amt += bonus
        bonus_text += f"\n✅ **Roblox Verified Bonus:** +{bonus} coins"
    if interaction.user.id in premium_users:
        prem_bonus = int(amt * 0.5)
        amt += prem_bonus
        bonus_text += f"\n👑 **Premium Bonus:** +{prem_bonus} coins"
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_daily"] = now.isoformat()
    embed = discord.Embed(
        title="💰 Daily Claimed!",
        description=f"{interaction.user.mention} claimed **{amt:,} coins!** 💰{bonus_text}\n\n**New balance:** {eco['balance']:,} coins",
        color=0x2ecc71
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="work", description="Work to earn YBS Coins")
async def slash_work(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_work"] and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600:
        diff = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
        m, s = divmod(int(diff), 60)
        return await interaction.response.send_message(embed=error_embed("Still Recovering", f"Rest for **{m}m {s}s** before working again."), ephemeral=True)
    jobs = [
        "coded a smooth Luau script 💻", "modelled an epic environment 🎨",
        "squashed a nasty bug 🐛", "scripted a fun obby mechanic 🏃",
        "designed a gorgeous UI 🖥️", "ran a full game playtest 🎮",
        "built a detailed game map 🗺️", "optimised the frame rate 📊",
        "scripted a DataStore system 💾", "created a new game mechanic ⚙️"
    ]
    amt = random.randint(50, 200)
    if interaction.user.id in premium_users:
        amt = int(amt * 1.5)
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_work"] = now.isoformat()
    embed = discord.Embed(
        title="💼 Work Complete!",
        description=f"{interaction.user.mention} {random.choice(jobs)} and earned **{amt:,} coins!**\n\n**Balance:** {eco['balance']:,} coins",
        color=0x3498db
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="pay", description="Send YBS Coins to another member")
@app_commands.describe(member="Who to pay", amount="How many coins")
async def slash_pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0:
        return await interaction.response.send_message(embed=error_embed("Invalid Amount", "Amount must be positive!"), ephemeral=True)
    if member.id == interaction.user.id:
        return await interaction.response.send_message(embed=error_embed("Can't Pay Yourself"), ephemeral=True)
    payer = get_economy(interaction.user.id, str(interaction.user))
    if payer["balance"] < amount:
        return await interaction.response.send_message(embed=error_embed("Insufficient Funds", f"You only have **{payer['balance']:,} coins**."), ephemeral=True)
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount
    payee["total_earned"] += amount
    embed = discord.Embed(
        title="💸 Payment Sent!",
        description=f"{interaction.user.mention} → {member.mention}\n**Amount:** {amount:,} coins",
        color=0x2ecc71
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="deposit", description="Deposit coins into your bank")
@app_commands.describe(amount="Amount to deposit (or 'all')")
async def slash_deposit(interaction: discord.Interaction, amount: str):
    eco = get_economy(interaction.user.id, str(interaction.user))
    dep = eco["balance"] if amount.lower() == "all" else int(amount) if amount.isdigit() else -1
    if dep <= 0 or dep > eco["balance"]:
        return await interaction.response.send_message(embed=error_embed("Invalid Amount", f"You have **{eco['balance']:,} coins**."), ephemeral=True)
    eco["balance"] -= dep
    eco["bank"] = eco.get("bank", 0) + dep
    await interaction.response.send_message(embed=success_embed("Deposited!", f"**{dep:,} coins** → bank\n\n💵 Wallet: {eco['balance']:,}\n🏦 Bank: {eco['bank']:,}"))

@tree.command(name="withdraw", description="Withdraw coins from your bank")
@app_commands.describe(amount="Amount to withdraw (or 'all')")
async def slash_withdraw(interaction: discord.Interaction, amount: str):
    eco = get_economy(interaction.user.id, str(interaction.user))
    bank = eco.get("bank", 0)
    wd = bank if amount.lower() == "all" else int(amount) if amount.isdigit() else -1
    if wd <= 0 or wd > bank:
        return await interaction.response.send_message(embed=error_embed("Invalid Amount", f"Bank: **{bank:,} coins**."), ephemeral=True)
    eco["bank"] = bank - wd
    eco["balance"] += wd
    await interaction.response.send_message(embed=success_embed("Withdrawn!", f"**{wd:,} coins** → wallet\n\n💵 Wallet: {eco['balance']:,}\n🏦 Bank: {eco['bank']:,}"))

@tree.command(name="gamble", description="Gamble your YBS Coins")
@app_commands.describe(amount="Coins to bet (or 'all')")
async def slash_gamble(interaction: discord.Interaction, amount: str):
    eco = get_economy(interaction.user.id, str(interaction.user))
    bet = eco["balance"] if amount.lower() == "all" else int(amount) if amount.isdigit() else -1
    if bet <= 0:
        return await interaction.response.send_message(embed=error_embed("Invalid Amount"), ephemeral=True)
    if eco["balance"] < bet:
        return await interaction.response.send_message(embed=error_embed("Insufficient Funds", f"You have **{eco['balance']:,} coins**."), ephemeral=True)
    win = random.random() > 0.5
    if win:
        eco["balance"] += bet
        eco["total_earned"] += bet
        embed = discord.Embed(title="🎲 You Won!", description=f"Bet **{bet:,}** and **WON**! 🎉\n+**{bet:,} coins**\n**Balance:** {eco['balance']:,}", color=0x2ecc71)
    else:
        eco["balance"] -= bet
        embed = discord.Embed(title="🎲 You Lost!", description=f"Bet **{bet:,}** and **LOST**. 😢\n-**{bet:,} coins**\n**Balance:** {eco['balance']:,}", color=0xe74c3c)
    await interaction.response.send_message(embed=embed)

@tree.command(name="slots", description="Play the slot machine")
@app_commands.describe(bet="Coins to bet (default 50)")
async def slash_slots(interaction: discord.Interaction, bet: int = 50):
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < bet:
        return await interaction.response.send_message(embed=error_embed("Insufficient Funds", f"You have **{eco['balance']:,} coins**."), ephemeral=True)
    syms = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰", "🔔", "🍀"]
    r = [random.choice(syms) for _ in range(3)]
    if r[0] == r[1] == r[2]:
        multi = 15 if r[0] == "💎" else 10 if r[0] == "🎰" else 5
        win = bet * multi
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"🎉 **JACKPOT! {r[0]}** — Won **{win:,}**!"
        color = 0xf1c40f
    elif r[0] == r[1] or r[1] == r[2] or r[0] == r[2]:
        win = bet * 2
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"✅ **Two of a kind!** — Won **{win:,}**!"
        color = 0x2ecc71
    else:
        eco["balance"] -= bet
        result = f"❌ **No luck!** — Lost **{bet:,}**."
        color = 0xe74c3c
    embed = discord.Embed(title="🎰 Slot Machine", color=color)
    embed.add_field(name="Result", value=f"┌ {r[0]} ┬ {r[1]} ┬ {r[2]} ┐", inline=False)
    embed.add_field(name="Outcome", value=result, inline=False)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    await interaction.response.send_message(embed=embed)

@tree.command(name="rob", description="Try to rob another member")
@app_commands.describe(member="Who to rob")
async def slash_rob(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id:
        return await interaction.response.send_message(embed=error_embed("Can't Rob Yourself"), ephemeral=True)
    robber = get_economy(interaction.user.id, str(interaction.user))
    victim = get_economy(member.id, str(member))
    now = datetime.now()
    last_rob = robber.get("last_rob")
    if last_rob and (now - datetime.fromisoformat(last_rob)).total_seconds() < 3600:
        diff = 3600 - (now - datetime.fromisoformat(last_rob)).total_seconds()
        m, s = divmod(int(diff), 60)
        return await interaction.response.send_message(embed=error_embed("On Cooldown", f"Wait **{m}m {s}s** before robbing again."), ephemeral=True)
    if victim["balance"] < 100:
        return await interaction.response.send_message(embed=error_embed("Too Broke", f"**{member.display_name}** doesn't have enough to rob!"), ephemeral=True)
    robber["last_rob"] = now.isoformat()
    if random.random() > 0.45:
        stolen = random.randint(50, min(500, victim["balance"]))
        robber["balance"] += stolen
        robber["total_earned"] += stolen
        victim["balance"] -= stolen
        embed = discord.Embed(title="🦝 Robbery Successful!", description=f"{interaction.user.mention} robbed **{member.display_name}** for **{stolen:,} coins!** 💸", color=0x2ecc71)
    else:
        fine = random.randint(50, 250)
        robber["balance"] = max(0, robber["balance"] - fine)
        embed = discord.Embed(title="🚨 Caught Red-Handed!", description=f"{interaction.user.mention} got caught and paid a **{fine:,} coin** fine! 🚔", color=0xe74c3c)
    await interaction.response.send_message(embed=embed)

@tree.command(name="fish", description="Go fishing for coins!")
async def slash_fish(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    catches = [
        ("🐟 Small Fish", 20, 50), ("🐠 Tropical Fish", 30, 80),
        ("🐡 Pufferfish", 40, 100), ("🦈 Shark", 100, 300),
        ("💎 Treasure Chest", 500, 1000), ("🎣 Nothing", 0, 0),
        ("👟 Old Boot", -10, -5), ("🐙 Octopus", 80, 200)
    ]
    catch, min_c, max_c = random.choices(catches, weights=[25, 20, 15, 10, 5, 20, 3, 2])[0]
    if max_c > 0:
        coins = random.randint(min_c, max_c)
        eco["balance"] += coins
        eco["total_earned"] += coins
        embed = discord.Embed(title="🎣 Fishing Result!", description=f"You caught: **{catch}**!\n💰 **+{coins} coins**\n**Balance:** {eco['balance']:,}", color=0x3498db)
    elif min_c < 0:
        loss = abs(random.randint(min_c, max_c))
        eco["balance"] = max(0, eco["balance"] - loss)
        embed = discord.Embed(title="🎣 Fishing Result!", description=f"You caught: **{catch}**!\n💸 **-{loss} coins**\n**Balance:** {eco['balance']:,}", color=0xe74c3c)
    else:
        embed = discord.Embed(title="🎣 Fishing Result!", description=f"You got: **{catch}**!\nBetter luck next time!", color=0x95a5a6)
    await interaction.response.send_message(embed=embed)

@tree.command(name="mine", description="Go mining for coins!")
async def slash_mine(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    finds = [
        ("💎 Diamond", 500, 1000), ("🥇 Gold Ore", 200, 400),
        ("🥈 Silver Ore", 100, 200), ("🪨 Stone", 10, 30),
        ("💀 Nothing", 0, 0), ("🧨 Cave-in!", -50, -20),
    ]
    find, min_c, max_c = random.choices(finds, weights=[5, 15, 20, 35, 20, 5])[0]
    if max_c > 0:
        coins = random.randint(min_c, max_c)
        eco["balance"] += coins
        eco["total_earned"] += coins
        embed = discord.Embed(title="⛏️ Mining Result!", description=f"You found: **{find}**!\n💰 **+{coins} coins**\n**Balance:** {eco['balance']:,}", color=0x8B4513)
    elif min_c < 0:
        loss = abs(random.randint(min_c, max_c))
        eco["balance"] = max(0, eco["balance"] - loss)
        embed = discord.Embed(title="⛏️ Mining Accident!", description=f"**{find}** Lost **{loss} coins**.\n**Balance:** {eco['balance']:,}", color=0xe74c3c)
    else:
        embed = discord.Embed(title="⛏️ Mining Result!", description=f"**{find}**! Nothing this time.", color=0x95a5a6)
    await interaction.response.send_message(embed=embed)

@tree.command(name="richlist", description="View the richest members")
async def slash_richlist(interaction: discord.Interaction):
    if not economy_data:
        return await interaction.response.send_message(embed=error_embed("No Data", "No economy data yet!"), ephemeral=True)
    top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:10]
    medals = ["🥇", "🥈", "🥉"]
    embed = discord.Embed(title="💰 Rich List — Top 10", color=0xf1c40f, timestamp=discord.utils.utcnow())
    lines = []
    for i, (uid, d) in enumerate(top):
        prefix = medals[i] if i < 3 else f"`{i+1}.`"
        name = d.get("name", str(uid)).split("#")[0]
        lines.append(f"{prefix} **{name}** — {d['balance']:,} coins")
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed)

@tree.command(name="givecoins", description="Give coins to a member [Admin]")
@app_commands.describe(member="Member", amount="Coins to give")
async def slash_givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    eco = get_economy(member.id, str(member))
    eco["balance"] += amount
    eco["total_earned"] += amount
    await interaction.response.send_message(embed=success_embed("Coins Given", f"Gave **{amount:,} coins** to {member.mention}."))

@tree.command(name="removecoins", description="Remove coins from a member [Admin]")
@app_commands.describe(member="Member", amount="Coins to remove")
async def slash_removecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    eco = get_economy(member.id, str(member))
    eco["balance"] = max(0, eco["balance"] - amount)
    await interaction.response.send_message(embed=success_embed("Coins Removed", f"Removed **{amount:,} coins** from {member.mention}."))

@tree.command(name="ecoreset", description="Reset a member's economy [Admin]")
@app_commands.describe(member="Member")
async def slash_ecoreset(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    economy_data.pop(member.id, None)
    await interaction.response.send_message(embed=success_embed("Economy Reset", f"Economy data cleared for {member.mention}."))

# ============================================================
# SLASH — FUN
# ============================================================
@tree.command(name="8ball", description="Ask the magic 8-ball")
@app_commands.describe(question="Your yes/no question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain. ✅", "It is decidedly so. ✅", "Without a doubt. ✅",
        "Yes, definitely. ✅", "You may rely on it. ✅", "As I see it, yes. ✅",
        "Most likely. 🟡", "Outlook good. 🟡", "Signs point to yes. 🟡",
        "Reply hazy, try again. ❓", "Ask again later. ❓", "Cannot predict now. ❓",
        "Don't count on it. ❌", "My reply is no. ❌", "Very doubtful. ❌",
    ]
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x1a1a2e)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(responses), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="joke", description="Get a random dev joke")
async def slash_joke(interaction: discord.Interaction):
    jokes = [
        ("Why do programmers prefer dark mode?", "Because light attracts bugs! 🐛"),
        ("Why did the Roblox player refuse to leave?", "He was ROBLOXed in! 🎮"),
        ("Why did the developer go broke?", "He used up all his cache! 💸"),
        ("How many programmers to change a light bulb?", "None, that's a hardware problem!"),
        ("Why do coders hate nature?", "It has too many bugs!"),
        ("What's a programmer's favourite hangout place?", "Foo Bar!"),
        ("Why do Java developers wear glasses?", "Because they don't C#!"),
        ("What is a pirate's favourite programming language?", "R, matey! 🏴‍☠️"),
    ]
    q, a = random.choice(jokes)
    embed = discord.Embed(title="😂 Dev Joke!", color=0xf1c40f)
    embed.add_field(name="Setup", value=q, inline=False)
    embed.add_field(name="Punchline", value=f"||{a}||", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="rps", description="Rock Paper Scissors vs the bot")
@app_commands.choices(choice=[
    app_commands.Choice(name="🪨 Rock", value="rock"),
    app_commands.Choice(name="📄 Paper", value="paper"),
    app_commands.Choice(name="✂️ Scissors", value="scissors"),
])
async def slash_rps(interaction: discord.Interaction, choice: str):
    ch = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    bot_c = random.choice(list(ch.keys()))
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    result = "🤝 **Tie!**" if choice == bot_c else ("🎉 **You win!**" if wins[choice] == bot_c else "😔 **Bot wins!**")
    embed = discord.Embed(title="🎮 Rock Paper Scissors", color=0x5865f2)
    embed.add_field(name="You", value=f"{ch[choice]} {choice.title()}")
    embed.add_field(name="Bot", value=f"{ch[bot_c]} {bot_c.title()}")
    embed.add_field(name="Result", value=result, inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="rate", description="Rate something out of 100")
@app_commands.describe(thing="What to rate")
async def slash_rate(interaction: discord.Interaction, thing: str):
    score = random.randint(0, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    color = 0x2ecc71 if score >= 70 else 0xf39c12 if score >= 40 else 0xe74c3c
    embed = discord.Embed(title=f"⭐ Rating: {thing}", color=color)
    embed.add_field(name="Score", value=f"**{score}/100**\n`{bar}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="ship", description="Check compatibility between two members")
@app_commands.describe(user1="First member", user2="Second member")
async def slash_ship(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member = None):
    user2 = user2 or interaction.user
    score = (user1.id + user2.id) % 101
    bar = "💗" * (score // 10) + "🖤" * (10 - score // 10)
    color = 0xFF79C6 if score >= 70 else 0xf39c12 if score >= 40 else 0x6b7280
    embed = discord.Embed(title="💘 Compatibility Meter", color=color)
    embed.description = f"**{user1.display_name}** 💕 **{user2.display_name}**\n\n{bar}\n\n**{score}%** compatible!"
    embed.set_footer(text="💞 Perfect match!" if score >= 70 else "💛 Could work!" if score >= 40 else "💔 Maybe just friends...")
    await interaction.response.send_message(embed=embed)

@tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Who to compliment")
async def slash_compliment(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    cs = [
        "is an absolute legend! 🌟", "makes this server better! 💪",
        "is the most talented dev here! 🎮", "is going to build something incredible! 🚀",
        "has the best scripts! 💻", "could model anything! 🎨",
        "is the backbone of YBS! 🏆", "has insane UI design skills! 🖥️",
    ]
    await interaction.response.send_message(f"💝 {member.mention} {random.choice(cs)}")

@tree.command(name="truth", description="Get a random truth question")
async def slash_truth(interaction: discord.Interaction):
    ts = [
        "What's your most embarrassing coding mistake?",
        "What game are you secretly working on?",
        "Have you ever copy-pasted code you didn't understand?",
        "What's the longest you've spent on one bug?",
        "Have you ever rage-quit Roblox Studio?",
        "What's the worst code you've ever written?",
    ]
    await interaction.response.send_message(embed=discord.Embed(title="😳 Truth!", description=random.choice(ts), color=0xFF79C6))

@tree.command(name="dare", description="Get a random dare")
async def slash_dare(interaction: discord.Interaction):
    ds = [
        "Make a mini-game in 30 minutes!",
        "Build a noob character and screenshot it!",
        "Write hello world in 3 languages!",
        "Script a random feature in 5 minutes!",
        "Draw your game idea in MS Paint and share it!",
        "Change your nickname to 'Roblox Noob' for 1 hour!",
    ]
    await interaction.response.send_message(embed=discord.Embed(title="😈 Dare!", description=random.choice(ds), color=0xe74c3c))

@tree.command(name="coinflip", description="Flip a coin")
async def slash_coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(embed=discord.Embed(title=f"🪙 Coin Flip — **{result}!**", color=0xf1c40f))

@tree.command(name="dice", description="Roll a dice")
@app_commands.describe(sides="Number of sides (default 6)")
async def slash_dice(interaction: discord.Interaction, sides: int = 6):
    if sides < 2 or sides > 100:
        return await interaction.response.send_message(embed=error_embed("Invalid", "Sides must be 2–100."), ephemeral=True)
    await interaction.response.send_message(embed=discord.Embed(title=f"🎲 D{sides} Roll — **{random.randint(1, sides)}**", color=0x5865f2))

@tree.command(name="choose", description="Pick randomly from options")
@app_commands.describe(options="Comma-separated options")
async def slash_choose(interaction: discord.Interaction, options: str):
    choices = [o.strip() for o in options.split(",") if o.strip()]
    if len(choices) < 2:
        return await interaction.response.send_message(embed=error_embed("Need More Options", "Separate options with commas."), ephemeral=True)
    embed = discord.Embed(title="🎯 I Choose...", description=f"**{random.choice(choices)}**", color=0x5865f2)
    embed.set_footer(text=f"From: {', '.join(choices)}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="mock", description="Mock some text")
@app_commands.describe(text="Text to mock")
async def slash_mock(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text)))

@tree.command(name="reverse", description="Reverse some text")
@app_commands.describe(text="Text to reverse")
async def slash_reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔄 {text[::-1]}")

@tree.command(name="pp", description="Check pp size 😏")
@app_commands.describe(member="Member to check")
async def slash_pp(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    score = (member.id * 7) % 21
    await interaction.response.send_message(f"📏 **{member.display_name}:**\n`8{'=' * score}D` ({score} cm)")

@tree.command(name="iq", description="Check someone's IQ")
@app_commands.describe(member="Member to check")
async def slash_iq(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    score = random.randint(50, 200)
    label = "Genius 🧠" if score >= 160 else "Very Smart 🎓" if score >= 130 else "Smart 📚" if score >= 110 else "Average 🙂" if score >= 90 else "Hmm... 🤔" if score >= 70 else "Uh oh... 😬"
    await interaction.response.send_message(embed=discord.Embed(title="🧠 IQ Test", description=f"**{member.display_name}** — IQ: **{score}** — {label}", color=0x9b59b6))

@tree.command(name="wouldyou", description="Would you rather question")
@app_commands.describe(option_a="Option A (optional)", option_b="Option B (optional)")
async def slash_wouldyou(interaction: discord.Interaction, option_a: str = None, option_b: str = None):
    presets = [
        ("Be a Roblox dev forever", "Be a Minecraft dev forever"),
        ("Script complex games", "Build stunning maps"),
        ("Have 1M Robux now", "Have 10M Robux in a year"),
        ("Work solo", "Work in a team"),
    ]
    a, b = (option_a, option_b) if option_a and option_b else random.choice(presets)
    embed = discord.Embed(title="🤔 Would You Rather…", color=0x9B59B6)
    embed.add_field(name="🅰️", value=a, inline=True)
    embed.add_field(name="🅱️", value=b, inline=True)
    msg = await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")

@tree.command(name="nhie", description="Never Have I Ever")
async def slash_nhie(interaction: discord.Interaction):
    questions = [
        "Never have I ever rage-quit Roblox Studio.",
        "Never have I ever copied code from the DevForum without understanding it.",
        "Never have I ever spent 3+ hours on a single bug.",
        "Never have I ever accidentally deleted a script with no backup.",
        "Never have I ever published a broken game by mistake.",
    ]
    embed = discord.Embed(title="🤚 Never Have I Ever", description=random.choice(questions), color=0xFF79C6)
    embed.set_footer(text="🖐️ = Have | ☝️ = Never")
    msg = await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🖐️")
    await msg.add_reaction("☝️")

@tree.command(name="fortune", description="Get your fortune cookie")
async def slash_fortune(interaction: discord.Interaction):
    fortunes = [
        "A great game idea is already in your mind. Start building today.",
        "Your next script will work first try. (Believe it!)",
        "The bug you've been chasing will reveal itself tomorrow.",
        "Collaboration with a teammate will unlock something great.",
        "The best games are made by those who never stop learning.",
    ]
    embed = discord.Embed(title="🥠 Fortune Cookie", description=f"*\"{random.choice(fortunes)}\"*", color=0xf1c40f)
    await interaction.response.send_message(embed=embed)

@tree.command(name="riddle", description="Get a random riddle")
async def slash_riddle(interaction: discord.Interaction):
    riddles = [
        ("I have keys but no locks. I have space but no room. What am I?", "A keyboard!"),
        ("The more you take, the more you leave behind. What am I?", "Footsteps!"),
        ("What has to be broken before you can use it?", "An egg!"),
        ("I'm tall when I'm young, short when I'm old. What am I?", "A candle!"),
    ]
    q, a = random.choice(riddles)
    embed = discord.Embed(title="🧩 Riddle Time!", description=q, color=0x9b59b6)
    embed.set_footer(text="React with 💡 to reveal the answer!")
    msg = await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("💡")
    try:
        await bot.wait_for("reaction_add", timeout=30, check=lambda r, u: str(r.emoji) == "💡" and not u.bot and r.message.id == msg.id)
        embed.add_field(name="✅ Answer", value=f"||{a}||", inline=False)
        await msg.edit(embed=embed)
    except:
        embed.add_field(name="⏰ Time's Up!", value=f"**{a}**", inline=False)
        await msg.edit(embed=embed)

@tree.command(name="trivia", description="Answer a Roblox dev trivia question")
async def slash_trivia(interaction: discord.Interaction):
    questions = [
        ("What language does Roblox Studio use?", "Lua / Luau"),
        ("What year was Roblox founded?", "2004"),
        ("What Roblox service handles player data persistence?", "DataStoreService"),
        ("What event fires when a player joins a server?", "Players.PlayerAdded"),
        ("What does API stand for?", "Application Programming Interface"),
    ]
    q, a = random.choice(questions)
    embed = discord.Embed(title="🎓 Trivia!", description=q, color=0x5865f2)
    embed.set_footer(text="React with 💡 to reveal the answer!")
    msg = await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("💡")
    try:
        await bot.wait_for("reaction_add", timeout=30, check=lambda r, u: str(r.emoji) == "💡" and not u.bot and r.message.id == msg.id)
        embed.add_field(name="✅ Answer", value=f"||{a}||", inline=False)
        await msg.edit(embed=embed)
    except:
        embed.add_field(name="⏰ Time's Up!", value=f"**{a}**", inline=False)
        await msg.edit(embed=embed)

# ============================================================
# SLASH — UTILITY
# ============================================================
@tree.command(name="snipe", description="See the last deleted message")
async def slash_snipe(interaction: discord.Interaction):
    data = snipe_data.get(interaction.channel.id)
    if not data:
        return await interaction.response.send_message(embed=error_embed("Nothing to snipe!"), ephemeral=True)
    embed = discord.Embed(description=data["content"], color=0xe74c3c, timestamp=discord.utils.utcnow())
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    if data.get("attachments"):
        embed.set_image(url=data["attachments"][0])
    await interaction.response.send_message(embed=embed)

@tree.command(name="editsnipe", description="See the last edited message")
async def slash_editsnipe(interaction: discord.Interaction):
    data = edit_snipe_data.get(interaction.channel.id)
    if not data:
        return await interaction.response.send_message(embed=error_embed("Nothing to edit-snipe!"), ephemeral=True)
    embed = discord.Embed(title="✏️ Edit Snipe", color=0xf39c12)
    embed.set_author(name=data["author"])
    embed.add_field(name="Before", value=data["before"], inline=False)
    embed.add_field(name="After", value=data["after"], inline=False)
    embed.set_footer(text=f"Edited at {data['time']}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="calc", description="Calculate a math expression")
@app_commands.describe(expression="Math to calculate")
async def slash_calc(interaction: discord.Interaction, expression: str):
    if not all(c in "0123456789+-*/.() " for c in expression):
        return await interaction.response.send_message(embed=error_embed("Invalid", "Only basic math allowed!"), ephemeral=True)
    try:
        result = eval(expression)
        embed = discord.Embed(title="🧮 Calculator", color=0x5865f2)
        embed.add_field(name="Expression", value=f"`{expression}`")
        embed.add_field(name="Result", value=f"**{result}**")
        await interaction.response.send_message(embed=embed)
    except:
        await interaction.response.send_message(embed=error_embed("Invalid expression!"), ephemeral=True)

@tree.command(name="afk", description="Set your AFK status")
@app_commands.describe(reason="AFK reason")
async def slash_afk(interaction: discord.Interaction, reason: str = "AFK"):
    afk_data[interaction.user.id] = {"reason": reason, "time": datetime.now().strftime("%H:%M")}
    await interaction.response.send_message(embed=discord.Embed(description=f"💤 {interaction.user.mention} is now AFK: **{reason}**", color=0x6b7280))

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(duration="e.g. 30m, 1h, 2h30m", reminder="What to remind you about")
async def slash_remindme(interaction: discord.Interaction, duration: str, reminder: str):
    total_seconds = 0
    matches = re.findall(r"(\d+)\s*([smhd])", duration.lower())
    if not matches:
        return await interaction.response.send_message(embed=error_embed("Invalid Duration", "Use format: `30m`, `1h`, `2h30m`"), ephemeral=True)
    for value, unit in matches:
        multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
        total_seconds += int(value) * multipliers.get(unit, 0)
    remind_time = datetime.now() + timedelta(seconds=total_seconds)
    reminders_data.append({
        "user_id": interaction.user.id,
        "channel_id": interaction.channel.id,
        "time": remind_time.isoformat(),
        "reminder": reminder,
        "sent": False
    })
    embed = discord.Embed(title="⏰ Reminder Set!", description=f"**{reminder}**\n\nWhen: <t:{int(remind_time.timestamp())}:R>", color=0x3498db)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="color", description="View info about a hex color")
@app_commands.describe(hex_code="Hex code e.g. 5865f2")
async def slash_color(interaction: discord.Interaction, hex_code: str):
    hex_code = hex_code.lstrip("#")
    try:
        r, g, b = int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
        embed = discord.Embed(title=f"🎨 #{hex_code.upper()}", color=int(hex_code, 16))
        embed.add_field(name="Hex", value=f"#{hex_code.upper()}")
        embed.add_field(name="RGB", value=f"rgb({r}, {g}, {b})")
        embed.add_field(name="Decimal", value=str(int(hex_code, 16)))
        await interaction.response.send_message(embed=embed)
    except:
        await interaction.response.send_message(embed=error_embed("Invalid hex color!"), ephemeral=True)

@tree.command(name="timestamp", description="Get current Unix timestamp")
async def slash_timestamp(interaction: discord.Interaction):
    ts = int(datetime.now().timestamp())
    embed = discord.Embed(title="🕐 Current Timestamp", color=0x5865f2)
    embed.add_field(name="Unix", value=f"`{ts}`")
    embed.add_field(name="Discord", value=f"`<t:{ts}>` → <t:{ts}>")
    embed.add_field(name="Relative", value=f"`<t:{ts}:R>` → <t:{ts}:R>")
    await interaction.response.send_message(embed=embed)

@tree.command(name="charcount", description="Count characters in text")
@app_commands.describe(text="Text to count")
async def slash_charcount(interaction: discord.Interaction, text: str):
    embed = discord.Embed(title="📊 Character Count", color=0x5865f2)
    embed.add_field(name="Characters", value=str(len(text)))
    embed.add_field(name="Words", value=str(len(text.split())))
    embed.add_field(name="Lines", value=str(text.count("\n") + 1))
    await interaction.response.send_message(embed=embed)

@tree.command(name="b64", description="Encode or decode base64")
@app_commands.describe(text="Text to encode/decode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Encode", value="encode"),
    app_commands.Choice(name="Decode", value="decode"),
])
async def slash_b64(interaction: discord.Interaction, mode: str, text: str):
    import base64 as b64lib
    try:
        if mode == "encode":
            result = b64lib.b64encode(text.encode()).decode()
            await interaction.response.send_message(embed=discord.Embed(title="🔐 Encoded", description=f"```{result}```", color=0x5865f2))
        else:
            result = b64lib.b64decode(text.encode()).decode()
            await interaction.response.send_message(embed=discord.Embed(title="🔓 Decoded", description=f"```{result}```", color=0x5865f2))
    except:
        await interaction.response.send_message(embed=error_embed("Invalid input!"), ephemeral=True)

@tree.command(name="say", description="Make the bot say something [Staff]")
@app_commands.describe(message="What to say", channel="Channel to send in")
async def slash_say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    ch = channel or interaction.channel
    await ch.send(message)
    await interaction.response.send_message(embed=success_embed("Sent!"), ephemeral=True)

@tree.command(name="embed", description="Create a custom embed [Staff]")
async def slash_embed(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    await interaction.response.send_modal(EmbedBuilderModal())

@tree.command(name="poll", description="Create an interactive poll")
@app_commands.describe(question="Poll question", option_a="Option A", option_b="Option B", option_c="Option C (optional)", option_d="Option D (optional)")
async def slash_poll(interaction: discord.Interaction, question: str, option_a: str, option_b: str, option_c: str = None, option_d: str = None):
    options = [o for o in [option_a, option_b, option_c, option_d] if o]
    embed = discord.Embed(title=f"📊 {question}", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.set_footer(text=f"Poll by {interaction.user.display_name} · Vote using the buttons!")
    await interaction.response.send_message(embed=embed, view=PollView(question, options))

@tree.command(name="suggest", description="Submit a suggestion")
@app_commands.describe(suggestion="Your suggestion")
async def slash_suggest(interaction: discord.Interaction, suggestion: str):
    sug_ch_id = get(interaction.guild.id, "suggestions_channel")
    channel = bot.get_channel(sug_ch_id) if sug_ch_id else interaction.channel
    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=0x2ecc71, timestamp=discord.utils.utcnow())
    embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await interaction.response.send_message(embed=success_embed("Suggestion Submitted!"), ephemeral=True)

# ============================================================
# SLASH — EVENTS
# ============================================================
@tree.command(name="giveaway", description="Start a giveaway [Staff]")
@app_commands.describe(prize="What to give away", duration_minutes="Duration in minutes")
async def slash_giveaway(interaction: discord.Interaction, prize: str, duration_minutes: int = 60):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
    gw_ch_id = get(interaction.guild.id, "giveaway_channel")
    channel = bot.get_channel(gw_ch_id) if gw_ch_id else interaction.channel
    end_time = datetime.now() + timedelta(minutes=duration_minutes)
    embed = discord.Embed(
        title="🎉 GIVEAWAY!",
        description=f"**{prize}**\n\nReact with 🎉 to enter!\nEnds: <t:{int(end_time.timestamp())}:R>",
        color=0xFF79C6,
        timestamp=end_time
    )
    embed.set_footer(text=f"Hosted by {interaction.user.display_name} · Ends at")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaway_data[msg.id] = {"prize": prize, "channel": channel.id, "ends": end_time.isoformat(), "host": str(interaction.user)}
    add_activity("🎉", f"Giveaway: {prize}", f"{duration_minutes}min")
    await interaction.response.send_message(embed=success_embed("Giveaway Started!", f"Posted in {channel.mention}!"), ephemeral=True)
    await asyncio.sleep(duration_minutes * 60)
    if msg.id in giveaway_data:
        try:
            msg = await channel.fetch_message(msg.id)
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot] if reaction else []
            if users:
                winner = random.choice(users)
                await channel.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
            else:
                await channel.send(f"❌ Not enough entries for **{prize}**.")
        except Exception as e:
            print(f"Giveaway error: {e}")
        giveaway_data.pop(msg.id, None)

@tree.command(name="announce", description="Post an announcement [Staff]")
@app_commands.describe(channel="Channel to post in")
async def slash_announce(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
    ch = channel or bot.get_channel(get(interaction.guild.id, "announcements_channel")) or interaction.channel
    await interaction.response.send_modal(AnnounceModal(ch))

@tree.command(name="serverrules", description="Post server rules [Staff]")
async def slash_serverrules(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message(embed=error_embed("No Permission"), ephemeral=True)
    rules = [
        "Be respectful to all members.",
        "No harassment, hate speech, or discrimination.",
        "No spamming or flooding.",
        "Keep content relevant to each channel.",
        "No NSFW content.",
        "No advertising without permission.",
        "Follow Discord's Terms of Service.",
        "Listen to staff and moderators.",
        "No sharing personal information.",
        "Have fun and be creative! 🎮",
    ]
    embed = discord.Embed(title="📜 Server Rules", color=0x5865f2, timestamp=discord.utils.utcnow())
    embed.description = "\n".join(f"**{i+1}.** {r}" for i, r in enumerate(rules))
    embed.set_footer(text="Failure to follow rules may result in mutes, kicks, or bans.")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message(embed=success_embed("Rules Posted!"), ephemeral=True)

@tree.command(name="birthday", description="Set your birthday")
@app_commands.describe(month="Month (1-12)", day="Day (1-31)")
async def slash_birthday(interaction: discord.Interaction, month: int, day: int):
    if month < 1 or month > 12 or day < 1 or day > 31:
        return await interaction.response.send_message(embed=error_embed("Invalid Date"), ephemeral=True)
    birthday_data[interaction.user.id] = f"{month:02d}-{day:02d}"
    await interaction.response.send_message(embed=success_embed("Birthday Set!", f"Set to **{month}/{day}**! We'll celebrate with you! 🎉"), ephemeral=True)

@tree.command(name="counting-stats", description="View counting channel stats")
async def slash_counting_stats(interaction: discord.Interaction):
    data = counting_data.get(str(interaction.guild.id), {"count": 0, "highscore": 0})
    embed = discord.Embed(title="🔢 Counting Stats", color=0x5865f2)
    embed.add_field(name="Current Count", value=str(data.get("count", 0)))
    embed.add_field(name="High Score", value=str(data.get("highscore", 0)))
    last_uid = data.get("last_user")
    if last_uid:
        member = interaction.guild.get_member(last_uid)
        embed.add_field(name="Last Counter", value=member.mention if member else str(last_uid))
    await interaction.response.send_message(embed=embed)

@tree.command(name="counting-reset", description="Reset the counting channel [Admin]")
async def slash_counting_reset(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    gid = str(interaction.guild.id)
    if gid in counting_data:
        counting_data[gid]["count"] = 0
        counting_data[gid]["last_user"] = None
    await interaction.response.send_message(embed=success_embed("Counting Reset!", "Count reset to 0."))

# ============================================================
# SLASH — ADMIN
# ============================================================
@tree.command(name="customcmd", description="Add a custom command [Admin]")
async def slash_customcmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    await interaction.response.send_modal(CustomCommandModal())

@tree.command(name="delcmd", description="Delete a custom command [Admin]")
@app_commands.describe(name="Command name to delete")
async def slash_delcmd(interaction: discord.Interaction, name: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    gid = str(interaction.guild.id)
    if gid in custom_commands and name.lower() in custom_commands[gid]:
        del custom_commands[gid][name.lower()]
        await interaction.response.send_message(embed=success_embed("Deleted", f"Command `!{name}` deleted."), ephemeral=True)
    else:
        await interaction.response.send_message(embed=error_embed("Not Found", f"No command `!{name}`."), ephemeral=True)

@tree.command(name="listcmds", description="List all custom commands")
async def slash_listcmds(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    cmds = custom_commands.get(gid, {})
    if not cmds:
        return await interaction.response.send_message(embed=info_embed("No Custom Commands", "Add one with /customcmd!"), ephemeral=True)
    embed = discord.Embed(title="📋 Custom Commands", color=0x5865f2)
    embed.description = "\n".join(f"`!{k}` — {v[:60]}" for k, v in cmds.items())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="premium", description="Grant premium to a member [Admin]")
@app_commands.describe(member="Member", days="Days of premium (default 30)")
async def slash_premium(interaction: discord.Interaction, member: discord.Member, days: int = 30):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    premium_users[member.id] = {"expires": (datetime.now() + timedelta(days=days)).isoformat(), "tier": "premium", "granted_by": str(interaction.user)}
    prem_role_id = get(interaction.guild.id, "premium_role")
    if prem_role_id:
        prem_role = interaction.guild.get_role(prem_role_id)
        if prem_role:
            try: await member.add_roles(prem_role)
            except: pass
    try:
        await member.send(embed=discord.Embed(title="👑 Premium Granted!", description=f"You have **Premium** in {interaction.guild.name} for **{days} days**!\n\n✅ 1.5x XP & coin boost\n✅ Bonus daily coins\n✅ Premium channels access", color=0xf1c40f))
    except: pass
    await interaction.response.send_message(embed=success_embed("Premium Granted!", f"**{member}** has Premium for **{days}** days!"))

@tree.command(name="unpremium", description="Remove premium from a member [Admin]")
@app_commands.describe(member="Member")
async def slash_unpremium(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    premium_users.pop(member.id, None)
    prem_role_id = get(interaction.guild.id, "premium_role")
    if prem_role_id:
        prem_role = interaction.guild.get_role(prem_role_id)
        if prem_role and prem_role in member.roles:
            try: await member.remove_roles(prem_role)
            except: pass
    await interaction.response.send_message(embed=success_embed("Premium Removed", f"**{member}**'s premium has been removed."))

@tree.command(name="reactionrole", description="Set up a reaction role [Admin]")
@app_commands.describe(message_id="Message ID", emoji="Emoji", role="Role to give")
async def slash_reactionrole(interaction: discord.Interaction, message_id: str, emoji: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    gid = str(interaction.guild.id)
    if gid not in reaction_roles: reaction_roles[gid] = {}
    if message_id not in reaction_roles[gid]: reaction_roles[gid][message_id] = {}
    reaction_roles[gid][message_id][emoji] = role.id
    try:
        msg = await interaction.channel.fetch_message(int(message_id))
        await msg.add_reaction(emoji)
    except: pass
    await interaction.response.send_message(embed=success_embed("Reaction Role Added", f"{emoji} → {role.mention}"), ephemeral=True)

@tree.command(name="dm", description="DM a member [Staff]")
@app_commands.describe(member="Member to DM", message="Message to send")
async def slash_dm(interaction: discord.Interaction, member: discord.Member, message: str):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message(embed=error_embed("Staff Only"), ephemeral=True)
    try:
        embed = discord.Embed(title=f"📬 Message from {interaction.guild.name} Staff", description=message, color=0x5865f2)
        embed.set_footer(text="You can reply to this DM and staff will see it.")
        await member.send(embed=embed)
        dm_reply_map[member.id] = {"type": "general", "guild_id": interaction.guild.id}
        await interaction.response.send_message(embed=success_embed("DM Sent!", f"DM sent to **{member}**."), ephemeral=True)
    except:
        await interaction.response.send_message(embed=error_embed("Couldn't DM", "User may have DMs disabled."), ephemeral=True)

@tree.command(name="lockdown", description="Lock all channels [Admin]")
async def slash_lockdown(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    await interaction.response.defer()
    count = 0
    for ch in interaction.guild.channels:
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=False)
                count += 1
            except: pass
    embed = discord.Embed(title="🔒 Server Lockdown", description=f"**{count}** channels locked by {interaction.user.mention}.", color=0xe74c3c, timestamp=discord.utils.utcnow())
    await interaction.followup.send(embed=embed)
    add_activity("🔒", f"Server lockdown by {interaction.user.display_name}")

@tree.command(name="unlockdown", description="Unlock all channels [Admin]")
async def slash_unlockdown(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    await interaction.response.defer()
    count = 0
    for ch in interaction.guild.channels:
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=None)
                count += 1
            except: pass
    embed = discord.Embed(title="🔓 Server Unlocked", description=f"**{count}** channels unlocked by {interaction.user.mention}.", color=0x2ecc71, timestamp=discord.utils.utcnow())
    await interaction.followup.send(embed=embed)

@tree.command(name="shutdown", description="Shutdown the bot [Owner only]")
async def slash_shutdown(interaction: discord.Interaction):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(embed=error_embed("Owner Only"), ephemeral=True)
    code = generate_staff_code()
    SHUTDOWN_CONFIRM_CODES[interaction.user.id] = code
    embed = discord.Embed(title="⚠️ Confirm Shutdown", description=f"Use `/shutdown-confirm` with code:\n```\n{code}\n```\n**This takes the bot offline immediately.**", color=0xe74c3c)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="shutdown-confirm", description="Confirm bot shutdown [Owner only]")
@app_commands.describe(code="Confirmation code from /shutdown")
async def slash_shutdown_confirm(interaction: discord.Interaction, code: str):
    global bot_shutdown_flag
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(embed=error_embed("Owner Only"), ephemeral=True)
    stored = SHUTDOWN_CONFIRM_CODES.get(interaction.user.id)
    if not stored or stored.upper() != code.upper():
        return await interaction.response.send_message(embed=error_embed("Wrong Code"), ephemeral=True)
    SHUTDOWN_CONFIRM_CODES.pop(interaction.user.id, None)
    await interaction.response.send_message(embed=discord.Embed(title="🔴 Shutting Down...", description="Bot going offline. Goodbye!", color=0xe74c3c))
    add_activity("🔴", f"Bot shutdown by {interaction.user.display_name}")
    await asyncio.sleep(2)
    bot_shutdown_flag = True

@tree.command(name="setup", description="Configure the bot [Admin]")
async def slash_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(embed=error_embed("Admin Only"), ephemeral=True)
    embed = discord.Embed(title="⚙️ Bot Setup", description="Use the buttons below to configure channels, roles, and features.", color=0x5865F2)
    await interaction.response.send_message(embed=embed, view=SetupMainView(), ephemeral=True)

# ============================================================
# FLASK DASHBOARD
# ============================================================
flask_app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), 'templates'))
flask_app.jinja_env.globals.update(enumerate=enumerate)

def build_xp_leaderboard():
    result = []
    for uid, d in sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True):
        lv = get_level(d["xp"])
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        pct = int((d["xp"] - prev_lv_xp) / max(next_lv_xp - prev_lv_xp, 1) * 100)
        result.append({"uid": uid, "name": d.get("name", "Unknown"), "xp": d["xp"], "level": lv, "messages": d.get("messages", 0), "pct": pct})
    return result

def build_eco_leaderboard():
    return [
        {"uid": uid, "name": d.get("name", "Unknown"), "balance": d["balance"], "total_earned": d["total_earned"]}
        for uid, d in sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)
    ]

def build_members():
    all_ids = set(xp_data.keys()) | set(economy_data.keys())
    members = []
    for uid in all_ids:
        xd = xp_data.get(uid, {"xp": 0, "level": 0, "messages": 0, "name": "Unknown"})
        ed = economy_data.get(uid, {"balance": 0})
        lv = get_level(xd["xp"])
        pct = int((xd["xp"] - xp_for_level(lv)) / max(xp_for_level(lv + 1) - xp_for_level(lv), 1) * 100)
        members.append({
            "uid": uid, "name": xd.get("name", "Unknown"),
            "xp": xd["xp"], "level": lv, "messages": xd.get("messages", 0),
            "pct": pct, "balance": ed.get("balance", 0),
            "warnings": len(warnings_data.get(uid, []))
        })
    return sorted(members, key=lambda x: x["xp"], reverse=True)

def common():
    role_counts = {}
    for app in applications_data.values():
        role = app.get("role", "Other")
        role_counts[role] = role_counts.get(role, 0) + 1
    recent_warns = []
    for uid, warns in list(warnings_data.items())[-8:]:
        if warns:
            recent_warns.append({"user_id": uid, "count": len(warns), "last_reason": warns[-1]["reason"], "last_by": warns[-1].get("by", "—")})
    mod_action_counts = {}
    for log in mod_log_data:
        mod_action_counts[log["action"]] = mod_action_counts.get(log["action"], 0) + 1
    return dict(
        bot_online=bot.is_ready(),
        bot_name=str(bot.user) if bot.user else "YBS Bot",
        app_count=len(applications_data),
        warn_count=len(warnings_data),
        notes_count=sum(len(v) for v in notes_data.values()),
        giveaway_count=len(giveaway_data),
        ticket_count=sum(1 for t in ticket_data.values() if t.get("status") == "open"),
        xp_count=len(xp_data),
        eco_count=len(economy_data),
        mod_log_count=len(mod_log_data),
        activity=activity_log,
        uptime=uptime_str(),
        role_counts=role_counts,
        recent_warns=recent_warns,
        recent_apps=list(applications_data.values())[-5:],
        top_xp=build_xp_leaderboard()[:5],
        mod_logs=mod_log_data,
        roblox_count=len(roblox_links),
        bug_count=len(bug_reports),
        premium_count=len(premium_users),
        mod_action_counts=mod_action_counts,
    )

@flask_app.route("/")
def dashboard_home():
    return render_template("dashboard.html", page="home", **common())

@flask_app.route("/applications")
def dashboard_applications():
    return render_template("dashboard.html", page="applications", applications=applications_data, **common())

@flask_app.route("/warnings")
def dashboard_warnings():
    return render_template("dashboard.html", page="warnings", warnings={str(k): v for k, v in warnings_data.items()}, **common())

@flask_app.route("/notes")
def dashboard_notes():
    all_notes = {str(k): v for k, v in notes_data.items() if v}
    return render_template("dashboard.html", page="notes", all_notes=all_notes, **common())

@flask_app.route("/activity")
def dashboard_activity():
    return render_template("dashboard.html", page="activity", **common())

@flask_app.route("/giveaways")
def dashboard_giveaways():
    return render_template("dashboard.html", page="giveaways", giveaways=giveaway_data, **common())

@flask_app.route("/leaderboard")
def dashboard_leaderboard():
    return render_template("dashboard.html", page="leaderboard", xp_lb=build_xp_leaderboard()[:25], eco_lb=build_eco_leaderboard()[:25], **common())

@flask_app.route("/members")
def dashboard_members():
    return render_template("dashboard.html", page="members", members=build_members(), **common())

@flask_app.route("/modlogs")
def dashboard_modlogs():
    return render_template("dashboard.html", page="modlogs", **common())

@flask_app.route("/automod")
def dashboard_automod():
    return render_template("dashboard.html", page="automod", automod_guilds=automod_data, total_words=sum(len(v) for v in automod_data.values()), **common())

@flask_app.route("/tickets")
def dashboard_tickets():
    return render_template("dashboard.html", page="tickets", tickets=ticket_data, **common())

@flask_app.route("/voice")
def dashboard_voice():
    return render_template("dashboard.html", page="voice", voice_events=voice_log, **common())

@flask_app.route("/economy")
def dashboard_economy():
    eco_lb = build_eco_leaderboard()
    total_coins = sum(d["balance"] for d in economy_data.values())
    total_earned_ever = sum(d["total_earned"] for d in economy_data.values())
    richest = max((d["balance"] for d in economy_data.values()), default=0)
    return render_template("dashboard.html", page="economy", eco_lb=eco_lb[:25], total_coins=total_coins, total_earned_ever=total_earned_ever, richest=richest, **common())

@flask_app.route("/roblox")
def dashboard_roblox():
    accounts = [{"discord_name": v.get("discord_name", "Unknown"), "username": v["username"], "display": v.get("display", v["username"]), "roblox_id": v["roblox_id"], "thumb": v.get("thumb"), "linked_at": v.get("linked_at", "—"), "verified": v.get("verified", False)} for v in roblox_links.values()]
    return render_template("dashboard.html", page="roblox", roblox_accounts=accounts, **common())

@flask_app.route("/bugs")
def dashboard_bugs():
    return render_template("dashboard.html", page="bugs", bug_reports=bug_reports, **common())

@flask_app.route("/analytics")
def dashboard_analytics():
    return render_template("dashboard.html", page="analytics", **common())

@flask_app.route("/premium")
def dashboard_premium():
    premium_members = {str(uid): data for uid, data in premium_users.items()}
    return render_template("dashboard.html", page="premium", premium_members=premium_members, **common())

@flask_app.route("/api/stats")
def api_stats():
    return jsonify({
        "bot_online": bot.is_ready(),
        "applications": len(applications_data),
        "warnings": len(warnings_data),
        "xp_members": len(xp_data),
        "economy_users": len(economy_data),
        "roblox_linked": len(roblox_links),
        "uptime": uptime_str(),
        "ping": round(bot.latency * 1000) if bot.is_ready() else 0,
        "activity": activity_log[:10],
        "bug_reports": len(bug_reports),
        "premium_users": len(premium_users),
    })

# ============================================================
# RUN BOTH BOT AND FLASK
# ============================================================
def run_bot():
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Bot error: {e}")

bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
