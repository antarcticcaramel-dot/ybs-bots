import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select
from discord import app_commands
import json
import os
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone
import random
import threading
from flask import Flask, render_template, jsonify, request
import time

# ============================================================
# TOKEN
# ============================================================
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

# ============================================================
# ROBLOX GROUP CONFIG
# ============================================================
REQUIRED_GROUP_ID = 35116281
REQUIRED_GROUP_URL = "https://www.roblox.com/share/g/35116281"

def _group_embed(title: str, desc: str) -> discord.Embed:
    return discord.Embed(title=title, description=desc, color=0xED4245)

async def check_group_membership(roblox_id: int) -> bool:
    """Returns True if the Roblox user is currently in the required group."""
    url = f"https://groups.roblox.com/v1/users/{roblox_id}/groups/roles"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                return any(
                    g.get("group", {}).get("id") == REQUIRED_GROUP_ID
                    for g in data.get("data", [])
                )
    except Exception:
        return False

async def require_group(interaction: discord.Interaction) -> bool:
    """
    Slash-command gate.
    • User must have a linked Roblox account.
    • That account must currently be in the required group.
    Returns True to proceed, False (already replied) to abort.
    """
    linked = roblox_links.get(interaction.user.id)
    if not linked:
        embed = _group_embed(
            "🎮 Roblox Group Required",
            f"You must **verify your Roblox account** and join the YBS group first.\n\n"
            f"1️⃣ Join the group: {REQUIRED_GROUP_URL}\n"
            f"2️⃣ Run `/roblox-verify` to link your account\n"
            f"3️⃣ Try again!",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    in_group = await check_group_membership(linked["roblox_id"])
    if not in_group:
        embed = _group_embed(
            "🚫 Not in Required Group",
            f"Your Roblox account **@{linked['username']}** is **not** in the YBS group "
            f"(or has left it).\n\n"
            f"👉 Rejoin here: {REQUIRED_GROUP_URL}\n"
            f"Once rejoined, try again.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return False

    return True

async def require_group_prefix(ctx) -> bool:
    """Same gate for prefix commands."""
    linked = roblox_links.get(ctx.author.id)
    if not linked:
        embed = _group_embed(
            "🎮 Roblox Group Required",
            f"Verify your Roblox account and join the YBS group first.\n\n"
            f"1️⃣ Join: {REQUIRED_GROUP_URL}\n"
            f"2️⃣ Run `/roblox-verify`\n"
            f"3️⃣ Try again!",
        )
        await ctx.send(embed=embed)
        return False

    in_group = await check_group_membership(linked["roblox_id"])
    if not in_group:
        embed = _group_embed(
            "🚫 Not in Required Group",
            f"**@{linked['username']}** is not in the YBS group.\n\n"
            f"Rejoin: {REQUIRED_GROUP_URL}\n"
            f"Then try again.",
        )
        await ctx.send(embed=embed)
        return False

    return True

# ============================================================
# FIX: always use timezone-aware UTC for Discord timeouts
# ============================================================
def utcnow() -> datetime:
    """Timezone-aware UTC now — required by discord.py v2 timeouts."""
    return datetime.now(timezone.utc)

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
ticket_data = {}
automod_data = {}
mod_log_data = []
voice_log = []
afk_data = {}
ban_log_data = []
verification_data = {}
bug_reports_data = []
premium_data = {}
bot_shutdown_flag = False

# ============================================================
# ADVANCED AUTO-MOD DATA STORES
# ============================================================
import re
from collections import defaultdict, deque

automod_infractions    = {}
automod_message_times  = defaultdict(deque)
automod_duplicate_times = {}

automod_link_pattern   = re.compile(r'(https?://|discord\.gg/|discordapp\.com/invite/|bit\.ly/|tinyurl\.com/|t\.me/)', re.IGNORECASE)
automod_invite_pattern = re.compile(r'(discord\.gg/|discord\.com/invite/|discordapp\.com/invite/)', re.IGNORECASE)

automod_spam_threshold  = 5
automod_spam_window     = 5
automod_duplicate_count = 3
automod_mention_limit   = 5
automod_emoji_limit     = 10
automod_newline_limit   = 15
automod_caps_threshold  = 0.75

ALLOWED_DOMAINS = [
    "roblox.com", "create.roblox.com", "devforum.roblox.com",
    "youtube.com", "youtu.be", "gyazo.com", "imgur.com",
    "github.com", "twitch.tv",
]

SEVERITY_COLORS = {"warn": 0xFFD166, "mute": 0xFF9F43, "kick": 0xED4245, "ban": 0x8B0000}

def is_allowed_link(url):
    return any(domain in url.lower() for domain in ALLOWED_DOMAINS)

def contains_blocked_link(text):
    urls = re.findall(r'https?://\S+|discord\.gg/\S+', text, re.IGNORECASE)
    return any(not is_allowed_link(u) for u in urls)

def contains_invite(text):
    return bool(automod_invite_pattern.search(text))

def is_caps_spam(text):
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    return (sum(1 for c in letters if c.isupper()) / len(letters)) >= automod_caps_threshold

def is_message_spam(user_id):
    now = datetime.now().timestamp()
    times = automod_message_times[user_id]
    times.append(now)
    while times and now - times[0] > automod_spam_window:
        times.popleft()
    return len(times) >= automod_spam_threshold

def is_duplicate_spam(user_id, content):
    if not content.strip():
        return False
    entry = automod_duplicate_times.get(user_id, {"content": "", "count": 0})
    if entry["content"] == content.strip().lower():
        entry["count"] += 1
    else:
        entry = {"content": content.strip().lower(), "count": 1}
    automod_duplicate_times[user_id] = entry
    return entry["count"] >= automod_duplicate_count

def count_mentions(message):
    return len(message.mentions) + len(message.role_mentions)

def count_emojis(text):
    uni = re.findall(r'[\U0001F300-\U0001F9FF]|[\u2600-\u27BF]|\u00a9|\u00ae', text)
    custom = re.findall(r'<a?:\w+:\d+>', text)
    return len(uni) + len(custom)

def get_automod_infractions(user_id):
    if user_id not in automod_infractions:
        automod_infractions[user_id] = {"warns": 0, "muted": False, "kicks": 0, "mute_warns": 0}
    return automod_infractions[user_id]

async def _dm_member(member, content):
    try:
        embed = discord.Embed(title="🤖 AutoMod — Young Boy Studios", description=content, color=0xFF9F43)
        embed.set_footer(text="If you think this is a mistake, open a /ticket in the server.")
        await member.send(embed=embed)
    except Exception:
        pass

async def send_automod_log(guild, member, reason, action, detail=""):
    log_ch_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
    if not log_ch:
        return
    inf = get_automod_infractions(member.id)
    color = SEVERITY_COLORS.get(action.lower(), 0xFF9F43)
    embed = discord.Embed(title=f"🤖 AutoMod — {action.upper()}", description=f"**{member.mention}** was automatically moderated.", color=color, timestamp=utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Member",        value=f"{member} (`{member.id}`)", inline=True)
    embed.add_field(name="⚠️ Trigger",       value=reason,                     inline=True)
    embed.add_field(name="🔨 Action",        value=action.title(),             inline=True)
    embed.add_field(name="📊 AutoMod Warns", value=str(inf["warns"]),          inline=True)
    embed.add_field(name="🦶 Kicks",         value=str(inf["kicks"]),          inline=True)
    embed.add_field(name="🔇 Muted",         value="✅" if inf["muted"] else "❌", inline=True)
    if detail:
        embed.add_field(name="💬 Content", value=f"```{detail[:400]}```", inline=False)
    embed.set_footer(text="Use the buttons below to take further action")
    await log_ch.send(embed=embed, view=AutoModActionView(member))

async def automod_warn_and_escalate(message, reason, detail=""):
    member = message.author
    guild  = message.guild
    inf    = get_automod_infractions(member.id)

    try:
        await message.delete()
    except Exception:
        pass

    inf["warns"] += 1
    total = inf["warns"]

    if member.id not in warnings_data:
        warnings_data[member.id] = []
    warnings_data[member.id].append({"reason": f"[AutoMod] {reason}", "by": "AutoMod", "time": datetime.now().isoformat()})
    add_activity("🤖", f"AutoMod | {member.display_name}", reason)
    add_mod_log("AutoMod", str(member), "AutoMod", reason, "#ff9f43")

    if total == 1:
        try:
            await message.channel.send(embed=discord.Embed(description=f"⚠️ {member.mention} — **{reason}**\n*Warning 1/3 before mute.*", color=0xFFD166), delete_after=8)
        except Exception:
            pass
        await _dm_member(member, f"⚠️ **Warning 1/3** in **{guild.name}**\n**Reason:** {reason}\n\nPlease follow the rules — 3 warnings = mute.")
    elif total == 2:
        try:
            await message.channel.send(embed=discord.Embed(description=f"⚠️ {member.mention} — **{reason}**\n*Warning 2/3. Next = **mute**.*", color=0xFF9F43), delete_after=10)
        except Exception:
            pass
        await _dm_member(member, f"⚠️ **Warning 2/3** in **{guild.name}**\n**Reason:** {reason}\n\n⚡ One more violation = mute!")
    elif total >= 3 and not inf["muted"]:
        inf["muted"]      = True
        inf["mute_warns"] = 0
        try:
            # ✅ FIXED: use timezone-aware utcnow()
            await member.timeout(utcnow() + timedelta(minutes=10), reason=f"[AutoMod] {reason}")
        except Exception:
            pass
        try:
            await message.channel.send(embed=discord.Embed(description=f"🔇 {member.mention} muted **10 minutes** by AutoMod.\n**Reason:** {reason}", color=0xED4245), delete_after=15)
        except Exception:
            pass
        await _dm_member(member, f"🔇 **Muted 10 minutes** in **{guild.name}**.\n**Reason:** {reason}\n\n⚡ Continue after unmute = **kick**.")
        await send_automod_log(guild, member, reason, "Mute (10 min)", detail)
    elif inf["muted"]:
        inf["mute_warns"] = inf.get("mute_warns", 0) + 1
        if inf["mute_warns"] >= 3 and inf["kicks"] == 0:
            inf["kicks"] += 1
            inf["muted"]  = False
            inf["warns"]  = 0
            try:
                await member.timeout(None)
            except Exception:
                pass
            try:
                await member.kick(reason=f"[AutoMod] Repeated violations after mute — {reason}")
            except Exception:
                pass
            await _dm_member(member, f"👢 **Kicked** from **{guild.name}**.\n**Reason:** Continued violations after mute.\n\n🚨 Rejoin and reoffend = **permanent ban**.")
            await send_automod_log(guild, member, reason, "Kick", detail)
            add_mod_log("AutoMod Kick", str(member), "AutoMod", reason, "#ed4245")
        else:
            try:
                await message.channel.send(embed=discord.Embed(description=f"⚠️ {member.mention} — **{reason}**\n*Post-mute warning {inf['mute_warns']}/3 before kick.*", color=0xED4245), delete_after=10)
            except Exception:
                pass
            await _dm_member(member, f"⚠️ **Post-mute warning {inf['mute_warns']}/3** in **{guild.name}**\n**Reason:** {reason}\n\n🚨 3 post-mute warns = **kick**.")
            await send_automod_log(guild, member, reason, f"Warn (post-mute {inf['mute_warns']}/3)", detail)

async def automod_check_ban(member, guild):
    inf = get_automod_infractions(member.id)
    if inf["kicks"] >= 1 and inf["warns"] >= 3:
        try:
            await member.ban(reason="[AutoMod] Repeated violations after kick")
        except Exception:
            pass
        await _dm_member(member, f"🔨 **Permanently banned** from **{guild.name}**.\nAutoMod: reoffended after kick.")
        add_mod_log("AutoMod Ban", str(member), "AutoMod", "Reoffended after kick", "#8b0000")
        add_activity("🔨", f"AutoMod banned {member.display_name}", "Rejoined & reoffended")
        log_ch_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
        log_ch = guild.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            embed = discord.Embed(title="🔨 AutoMod — PERMANENT BAN", description=f"**{member}** banned after rejoining and reoffending.", color=0x8B0000, timestamp=utcnow())
            embed.set_thumbnail(url=member.display_avatar.url)
            await log_ch.send(embed=embed)

# ============================================================
# AUTO-MOD ACTION VIEW
# ============================================================
class AutoModActionView(View):
    def __init__(self, member):
        super().__init__(timeout=None)
        self.member = member

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.secondary, row=0)
    async def warn_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if self.member.id not in warnings_data: warnings_data[self.member.id] = []
        warnings_data[self.member.id].append({"reason": "Staff review after AutoMod", "by": str(interaction.user), "time": datetime.now().isoformat()})
        add_mod_log("Warn", str(self.member), str(interaction.user), "Staff review after AutoMod", "#faa61a")
        await interaction.response.send_message(f"⚠️ Warning issued to **{self.member}**.", ephemeral=True)

    @discord.ui.button(label="🔇 Mute 1h", style=discord.ButtonStyle.danger, row=0)
    async def mute1h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            # ✅ FIXED: timezone-aware
            await self.member.timeout(utcnow() + timedelta(hours=1), reason=f"Staff after AutoMod — {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "1h after AutoMod", "#faa61a")
            await interaction.response.send_message(f"🔇 **{self.member}** muted 1h.", ephemeral=True)
        except Exception: await interaction.response.send_message("❌ Couldn't mute.", ephemeral=True)

    @discord.ui.button(label="🔇 Mute 24h", style=discord.ButtonStyle.danger, row=0)
    async def mute24h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            # ✅ FIXED: timezone-aware
            await self.member.timeout(utcnow() + timedelta(hours=24), reason=f"Staff after AutoMod — {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "24h after AutoMod", "#faa61a")
            await interaction.response.send_message(f"🔇 **{self.member}** muted 24h.", ephemeral=True)
        except Exception: await interaction.response.send_message("❌ Couldn't mute.", ephemeral=True)

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger, row=0)
    async def kick_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.kick(reason=f"Staff after AutoMod — {interaction.user}")
            add_mod_log("Kick", str(self.member), str(interaction.user), "Kick after AutoMod", "#ed4245")
            await interaction.response.send_message(f"👢 **{self.member}** kicked.", ephemeral=True)
        except Exception: await interaction.response.send_message("❌ Couldn't kick.", ephemeral=True)

    @discord.ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger, row=0)
    async def ban_btn(self, interaction, button):
        if not interaction.user.guild_permissions.ban_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.ban(reason=f"Staff after AutoMod — {interaction.user}")
            add_mod_log("Ban", str(self.member), str(interaction.user), "Ban after AutoMod", "#8b0000")
            await interaction.response.send_message(f"🔨 **{self.member}** banned.", ephemeral=True)
        except Exception: await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)

    @discord.ui.button(label="✅ Dismiss / False Positive", style=discord.ButtonStyle.success, row=1)
    async def dismiss_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        inf = get_automod_infractions(self.member.id)
        if inf["warns"] > 0: inf["warns"] -= 1
        if self.member.id in warnings_data and warnings_data[self.member.id]:
            warnings_data[self.member.id].pop()
        try: await self.member.timeout(None)
        except Exception: pass
        await interaction.response.send_message(f"✅ Dismissed for **{self.member}**. Mute removed if active.", ephemeral=True)

    @discord.ui.button(label="🔄 Reset All Strikes", style=discord.ButtonStyle.secondary, row=1)
    async def reset_btn(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        automod_infractions.pop(self.member.id, None)
        automod_duplicate_times.pop(self.member.id, None)
        if self.member.id in automod_message_times: automod_message_times[self.member.id].clear()
        await interaction.response.send_message(f"🔄 AutoMod strikes reset for **{self.member}**.", ephemeral=True)

    @discord.ui.button(label="📋 Full History", style=discord.ButtonStyle.primary, row=1)
    async def history_btn(self, interaction, button):
        inf   = get_automod_infractions(self.member.id)
        warns = warnings_data.get(self.member.id, [])
        embed = discord.Embed(title=f"📋 AutoMod History — {self.member}", color=0x5865F2)
        embed.add_field(name="⚠️ Warns",  value=f"`{inf['warns']}`",                    inline=True)
        embed.add_field(name="🦶 Kicks",  value=f"`{inf['kicks']}`",                    inline=True)
        embed.add_field(name="🔇 Muted",  value="✅" if inf["muted"] else "❌",         inline=True)
        if warns:
            embed.add_field(name="Recent", value="\n".join(f"• {w['reason'][:60]} *({w['time'][:10]})*" for w in warns[-10:]), inline=False)
        else:
            embed.description = "No warning history."
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ---- Helpers ----
def next_bug_id():
    return f"BUG-{len(bug_reports_data)+1:04d}"

def get_level(xp):
    return int((xp / 50) ** 0.5)

def xp_for_level(level):
    return 50 * level ** 2

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
        economy_data[user_id] = {"balance": 0, "last_daily": None, "last_work": None, "total_earned": 0, "name": name}
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

# ============================================================
# SETUP MENU SYSTEM
# ============================================================
CHANNEL_CONFIG_KEYS = [
    ("welcome_channel", "👋 Welcome Channel"),
    ("apply_channel", "📋 Apply Channel"),
    ("applications_channel", "📨 Staff Applications Channel"),
    ("logs_channel", "📋 Logs Channel"),
    ("general_channel", "💬 General Channel"),
    ("announcements_channel", "📢 Announcements Channel"),
    ("giveaway_channel", "🎉 Giveaway Channel"),
    ("levelup_channel", "⬆️ Level-Up Channel"),
    ("rank_channel", "🏅 Rank/Leaderboard Channel"),
    ("verify_channel", "✅ Verification Channel"),
    ("mod_channel", "🔨 Mod-Log Channel"),
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
]

class ChannelTypeSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=key) for key, label in CHANNEL_CONFIG_KEYS]
        super().__init__(placeholder="Which channel to configure?", options=options)
    async def callback(self, interaction):
        config_key = self.values[0]
        label = next(l for k, l in CHANNEL_CONFIG_KEYS if k == config_key)
        await interaction.response.send_message(f"Select the channel for **{label}**:", view=ChannelPickerView(config_key, label), ephemeral=True)

class ChannelTypeSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ChannelTypeSelect())

class ChannelPickerSelect(discord.ui.ChannelSelect):
    def __init__(self, config_key, label):
        super().__init__(placeholder="Select channel...", channel_types=[discord.ChannelType.text])
        self.config_key = config_key
        self.label_text = label
    async def callback(self, interaction):
        set_config(interaction.guild.id, self.config_key, self.values[0].id)
        await interaction.response.send_message(f"✅ **{self.label_text}** → {self.values[0].mention}", ephemeral=True)

class ChannelPickerView(View):
    def __init__(self, config_key, label):
        super().__init__(timeout=120)
        self.add_item(ChannelPickerSelect(config_key, label))

class RoleTypeSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=key) for key, label in ROLE_CONFIG_KEYS]
        super().__init__(placeholder="Which role to configure?", options=options)
    async def callback(self, interaction):
        config_key = self.values[0]
        label = next(l for k, l in ROLE_CONFIG_KEYS if k == config_key)
        await interaction.response.send_message(f"Select the role for **{label}**:", view=RolePickerView(config_key, label), ephemeral=True)

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
        await interaction.response.send_message(f"✅ **{self.label_text}** → {self.values[0].mention}", ephemeral=True)

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
    await interaction.followup.send(f"🔒 Lockdown configured! **Pending** role set on {count} channels.", ephemeral=True)
    add_activity("🔒", f"Lockdown setup by {interaction.user.display_name}", guild.name)

class SetupMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Set Channels", style=discord.ButtonStyle.blurple, row=0)
    async def set_channels(self, interaction, button):
        await interaction.response.send_message(embed=discord.Embed(title="📋 Configure Channels", color=0x5865F2), view=ChannelTypeSelectView(), ephemeral=True)

    @discord.ui.button(label="🎭 Set Roles", style=discord.ButtonStyle.green, row=0)
    async def set_roles(self, interaction, button):
        await interaction.response.send_message(embed=discord.Embed(title="🎭 Configure Roles", color=0x3BA55C), view=RoleTypeSelectView(), ephemeral=True)

    @discord.ui.button(label="🔒 Setup Lockdown", style=discord.ButtonStyle.red, row=1)
    async def setup_lockdown_btn(self, interaction, button):
        await do_lockdown_setup(interaction)

    @discord.ui.button(label="📊 View Config", style=discord.ButtonStyle.secondary, row=1)
    async def view_config_btn(self, interaction, button):
        config = load_config().get(str(interaction.guild.id), {})
        if not config:
            return await interaction.response.send_message("❌ No config yet!", ephemeral=True)
        embed = discord.Embed(title="⚙️ Current Configuration", color=0x5865F2)
        for key, value in config.items():
            obj = interaction.guild.get_role(value) or interaction.guild.get_channel(value)
            embed.add_field(name=key.replace("_"," ").title(), value=obj.mention if obj else str(value), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📮 Post Apply Panel", style=discord.ButtonStyle.blurple, row=2)
    async def post_apply(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        channel_id = get(interaction.guild.id, "apply_channel")
        channel = bot.get_channel(channel_id) if channel_id else interaction.channel
        embed = discord.Embed(title="🚀 Join the Young Boy Studios Dev Team", description="We're looking for talented Roblox developers!\n\n**Roles:** 🔨 Builder · 💻 Scripter · 🎨 Modeller · 🖥️ UI Designer\n\nUse the dropdown below to select your role.", color=0x5865F2)
        await channel.send(embed=embed, view=ApplyView())
        await interaction.response.send_message(f"✅ Apply panel posted in {channel.mention}!", ephemeral=True)

    @discord.ui.button(label="✅ Post Verify Panel", style=discord.ButtonStyle.green, row=2)
    async def post_verify(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        ch_id = get(interaction.guild.id, "verify_channel")
        channel = bot.get_channel(ch_id) if ch_id else interaction.channel
        embed = discord.Embed(
            title="🎮 Link Your Roblox Account",
            description="Click **Verify Roblox** to link your Roblox account to Discord!\n\n**Why verify?**\n✅ Access member-only channels\n✅ Show your Roblox profile in the server\n✅ Get the verified role",
            color=0x00B2FF
        )
        embed.set_footer(text="Young Boy Studios · Roblox Verification")
        await channel.send(embed=embed, view=VerifyPanelView())
        await interaction.response.send_message(f"✅ Verify panel posted!", ephemeral=True)

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
            return {"id": uid, "name": name, "display": disp,
                    "desc": (profile.get("description") or "No bio.")[:300],
                    "created": (profile.get("created") or "")[:10], "thumb": thumb}
    except Exception:
        return None

class VerifyModal(Modal, title="🎮 Link Roblox Account"):
    roblox_username = TextInput(label="Roblox Username", placeholder="Enter your exact Roblox username...", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.roblox_username.value.strip()
        data = await fetch_roblox(username)
        if not data:
            return await interaction.followup.send("❌ Roblox user not found! Check the username and try again.", ephemeral=True)

        # ✅ GROUP CHECK at verify stage too
        in_group = await check_group_membership(data["id"])
        if not in_group:
            embed = _group_embed(
                "🚫 Not in Required Group",
                f"Your Roblox account **@{data['name']}** is not in the YBS group.\n\n"
                f"👉 Join first: {REQUIRED_GROUP_URL}\n"
                f"Then try verifying again.",
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        code = f"YBS-{random.randint(100000, 999999)}"
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
            description=f"Found **{data['display']}** (@{data['name']})\n\n**Step 2:** Add this code to your Roblox **profile bio** (description):\n\n```{code}```\n\nThen click **Confirm Verification** below.\n*Code expires in 10 minutes.*",
            color=0x00B2FF
        )
        if data.get("thumb"):
            embed.set_thumbnail(url=data["thumb"])
        embed.set_footer(text="Go to roblox.com → Profile → Edit → Bio → Paste code → Save")
        await interaction.followup.send(embed=embed, view=ConfirmVerifyView(), ephemeral=True)

class ConfirmVerifyView(View):
    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="✅ Confirm Verification", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        vdata = verification_data.get(interaction.user.id)
        if not vdata:
            return await interaction.followup.send("❌ No pending verification. Start again.", ephemeral=True)
        if datetime.fromisoformat(vdata["expires"]) < datetime.now():
            verification_data.pop(interaction.user.id, None)
            return await interaction.followup.send("❌ Code expired. Start the verification again.", ephemeral=True)

        # ✅ Re-check group membership at confirmation
        in_group = await check_group_membership(vdata["roblox_id"])
        if not in_group:
            embed = _group_embed(
                "🚫 Not in Required Group",
                f"**@{vdata['roblox_username']}** is still not in the YBS group.\n\n"
                f"👉 Join: {REQUIRED_GROUP_URL}\n"
                f"Then try confirming again.",
            )
            return await interaction.followup.send(embed=embed, ephemeral=True)

        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(f"https://users.roblox.com/v1/users/{vdata['roblox_id']}")
                profile = await r.json()
                bio = profile.get("description", "")
        except Exception:
            return await interaction.followup.send("❌ Could not contact Roblox API. Try again.", ephemeral=True)
        if vdata["code"] not in bio:
            return await interaction.followup.send(f"❌ Code `{vdata['code']}` not found in your Roblox bio yet!\n\nMake sure you **saved** the profile. Wait a minute and try again.", ephemeral=True)
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
            description=f"Successfully linked **{vdata['display']}** (@{vdata['roblox_username']}) to your Discord!\n\nYou've been given the verified role.",
            color=0x3BA55C
        )
        if vdata.get("thumb"):
            embed.set_thumbnail(url=vdata["thumb"])
        await interaction.followup.send(embed=embed, ephemeral=True)

class VerifyPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎮 Verify Roblox Account", style=discord.ButtonStyle.blurple, custom_id="verify_roblox_btn")
    async def verify_btn(self, interaction: discord.Interaction, button: Button):
        if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
            linked = roblox_links[interaction.user.id]
            embed = discord.Embed(title="✅ Already Verified!", description=f"You're already linked to **{linked['display']}** (@{linked['username']})\n\nUse `/roblox-unlink` to unlink.", color=0x3BA55C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(VerifyModal())

# ============================================================
# APPLICATION SYSTEM — group-gated
# ============================================================
class ApplicationModal(Modal):
    def __init__(self, role: str = "Developer"):
        super().__init__(title=f"🎮 Apply for {role[:35]} — YBS")
        self.role_value = role
        self.roblox_name = TextInput(label="Roblox Username", placeholder="e.g. CoolBuilder123", required=True, max_length=50)
        self.real_name = TextInput(label="Name & Age", placeholder="e.g. Alex, 17", required=True, max_length=60)
        self.experience = TextInput(label="Experience & Skills", placeholder="How long developing? What are you best at?", required=True, style=discord.TextStyle.paragraph, max_length=500)
        self.why_availability = TextInput(label="Why join? + Hours/week available", placeholder="Your motivation + e.g. 10 hrs/week", required=True, style=discord.TextStyle.paragraph, max_length=500)
        self.portfolio = TextInput(label="Portfolio / Work Samples", placeholder="https://... or describe your work", required=False, style=discord.TextStyle.paragraph, max_length=400)
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
            "status": "pending", "portfolio_images": []
        }
        applications_data[interaction.user.id] = app
        if channel:
            embed = discord.Embed(title=f"📋 New Application — {real_name}", color=0x5865F2, timestamp=utcnow())
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="🎮 Roblox", value=self.roblox_name.value, inline=True)
            embed.add_field(name="👤 Name", value=real_name, inline=True)
            embed.add_field(name="🎂 Age", value=age, inline=True)
            embed.add_field(name="🔨 Role", value=self.role_value, inline=True)
            if interaction.user.id in roblox_links:
                embed.add_field(name="✅ Roblox Verified", value=roblox_links[interaction.user.id]["username"], inline=True)
            embed.add_field(name="⚙️ Experience", value=self.experience.value, inline=False)
            embed.add_field(name="💡 Why Join + Availability", value=self.why_availability.value, inline=False)
            if self.portfolio.value:
                embed.add_field(name="📁 Portfolio", value=self.portfolio.value, inline=False)
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            await channel.send(embed=embed, view=ApplicationReviewView(interaction.user.id))
        add_activity("📋", f"New application from {real_name}", self.role_value)
        await interaction.response.send_message("🎉 **Application submitted!** Our team will review it soon. Good luck! 🚀", ephemeral=True)

class ApplicationReviewView(View):
    def __init__(self, applicant_id):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def is_staff(self, interaction):
        staff_id = get(interaction.guild.id, "admin_role")
        return staff_id and staff_id in [r.id for r in interaction.user.roles]

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            role_id = get(interaction.guild.id, "member_role")
            role = interaction.guild.get_role(role_id) if role_id else None
            if role:
                await member.add_roles(role)
            pending_id = get(interaction.guild.id, "pending_role")
            if pending_id:
                pending = interaction.guild.get_role(pending_id)
                if pending and pending in member.roles:
                    await member.remove_roles(pending)
            try:
                await member.send("🎉 Your application to **Young Boy Studios** has been **accepted!** Welcome to the team! 🚀")
            except:
                pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "accepted"
            add_mod_log("Accept", str(member), str(interaction.user), "Application accepted", "#3ba55c")
            add_activity("✅", f"{member.display_name}'s application was accepted")
        await interaction.message.edit(content=f"✅ Accepted by {interaction.user.mention}", view=None)
        await interaction.response.send_message("✅ Accepted!", ephemeral=True)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send("😔 Your application to **Young Boy Studios** was not accepted this time. Feel free to reapply in 2 weeks!")
            except:
                pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "declined"
        await interaction.message.edit(content=f"❌ Declined by {interaction.user.mention}", view=None)
        await interaction.response.send_message("❌ Declined.", ephemeral=True)

    @discord.ui.button(label="⏳ Interview", style=discord.ButtonStyle.blurple)
    async def interview(self, interaction, button):
        if not self.is_staff(interaction):
            return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send("👋 Your application looks great! A staff member will DM you to arrange an interview.")
            except:
                pass
            if self.applicant_id in applications_data:
                applications_data[self.applicant_id]["status"] = "interview"
        await interaction.message.edit(content=f"⏳ Interview — {interaction.user.mention}", view=None)
        await interaction.response.send_message("⏳ Moved to interview.", ephemeral=True)

class ApplicationRoleDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🔨 Builder", value="Builder", description="Build game environments & maps"),
            discord.SelectOption(label="💻 Scripter", value="Scripter", description="Write Lua scripts & game logic"),
            discord.SelectOption(label="🎨 Modeller", value="Modeller", description="Create 3D models & assets"),
            discord.SelectOption(label="🖥️ UI Designer", value="UI Designer", description="Design game interfaces & UX"),
        ]
        super().__init__(placeholder="🎮 Select the role you're applying for...", options=options, custom_id="apply_role_select_v4")

    async def callback(self, interaction):
        # ✅ GROUP CHECK before opening application modal
        if not await require_group(interaction):
            return
        await interaction.response.send_modal(ApplicationModal(self.values[0]))

class ApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ApplicationRoleDropdown())

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
            description=(f"Hey {member.mention}! We're glad you're here.\n\n"
                        f"📋 Head to <#{apply_id}> to **apply for the dev team**\n"
                        f"✅ Link your Roblox at <#{verify_id}>\n"
                        f"💬 Say hi in general!\n\nWe're building something great — come be part of it!"),
            color=0x5865F2
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await channel.send(embed=embed)
    try:
        await member.send(f"👋 Hey **{member.display_name}**! Welcome to **Young Boy Studios**!\nHead to **#apply-here** to join the dev team! 🚀")
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
        await log.send(f"📥 **{member}** joined. Members: {member.guild.member_count}")

@bot.event
async def on_member_remove(member):
    add_activity("📤", f"{member.display_name} left", member.guild.name)
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        await log.send(f"📤 **{member}** left. Members: {member.guild.member_count}")

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    if message.content:
        snipe_data[message.channel.id] = {"content": message.content[:500], "author": str(message.author), "time": datetime.now().strftime("%H:%M")}
    mod_log_id = get(message.guild.id, "mod_channel") or get(message.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="🗑️ Message Deleted", color=0xFF0000)
        embed.add_field(name="Author", value=message.author.mention)
        embed.add_field(name="Channel", value=message.channel.mention)
        embed.add_field(name="Content", value=message.content[:500] or "*(no text)*", inline=False)
        embed.timestamp = utcnow()
        await log.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    mod_log_id = get(before.guild.id, "mod_channel") or get(before.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="✏️ Message Edited", color=0xFFAA00)
        embed.add_field(name="Author", value=before.author.mention)
        embed.add_field(name="Channel", value=before.channel.mention)
        embed.add_field(name="Before", value=before.content[:400] or "*(empty)*", inline=False)
        embed.add_field(name="After", value=after.content[:400] or "*(empty)*", inline=False)
        embed.timestamp = utcnow()
        await log.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return

    member  = message.author
    content = message.content
    guild   = message.guild

    is_exempt = member.guild_permissions.administrator or member.guild_permissions.manage_messages

    if not is_exempt:
        flagged = False

        if not flagged and contains_invite(content):
            await automod_warn_and_escalate(message, "Discord invite link", content[:200])
            flagged = True
        if not flagged and contains_blocked_link(content):
            await automod_warn_and_escalate(message, "Blocked link / URL", content[:200])
            flagged = True
        if not flagged and is_message_spam(member.id):
            await automod_warn_and_escalate(message, f"Message spam ({automod_spam_threshold}+ msgs/{automod_spam_window}s)", "")
            flagged = True
        if not flagged and is_duplicate_spam(member.id, content):
            await automod_warn_and_escalate(message, "Duplicate message spam", content[:200])
            flagged = True
        if not flagged and count_mentions(message) >= automod_mention_limit:
            await automod_warn_and_escalate(message, f"Mass mentions ({count_mentions(message)} pings)", content[:200])
            flagged = True
        if not flagged and count_emojis(content) >= automod_emoji_limit:
            await automod_warn_and_escalate(message, f"Emoji spam ({count_emojis(content)} emojis)", content[:100])
            flagged = True
        if not flagged and is_caps_spam(content):
            await automod_warn_and_escalate(message, "Excessive caps spam", content[:200])
            flagged = True
        if not flagged and content.count('\n') >= automod_newline_limit:
            await automod_warn_and_escalate(message, f"Wall of text ({content.count(chr(10))} newlines)", content[:100])
            flagged = True
        if not flagged:
            guild_words = automod_data.get(str(guild.id), [])
            if guild_words and any(w.lower() in content.lower() for w in guild_words):
                await automod_warn_and_escalate(message, "Banned word/phrase", content[:200])
                flagged = True
        if not flagged:
            await automod_check_ban(member, guild)
        if flagged:
            return

    # AFK check
    if member.id in afk_data:
        afk_data.pop(member.id)
        await message.channel.send(f"✅ Welcome back, {member.mention}! AFK removed.", delete_after=5)
    for mentioned in message.mentions:
        if mentioned.id in afk_data:
            info = afk_data[mentioned.id]
            await message.channel.send(f"💤 **{mentioned.display_name}** is AFK: {info['reason']} *(since {info['time']})*", delete_after=10)

    # XP system
    now  = datetime.now()
    last = xp_cooldowns.get(member.id)
    if not last or (now - last).total_seconds() >= 60:
        xp_cooldowns[member.id] = now
        if member.id not in xp_data:
            xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
        earned = random.randint(5, 15)
        xp_data[member.id]["xp"]      += earned
        xp_data[member.id]["messages"] = xp_data[member.id].get("messages", 0) + 1
        xp_data[member.id]["name"]     = str(member)
        cur_xp = xp_data[member.id]["xp"]
        old_lv = xp_data[member.id]["level"]
        new_lv = get_level(cur_xp)
        if new_lv > old_lv:
            xp_data[member.id]["level"] = new_lv
            add_activity("⬆️", f"{member.display_name} reached level {new_lv}!", guild.name)
            lv_ch_id = get(guild.id, "levelup_channel")
            lv_ch    = bot.get_channel(lv_ch_id) if lv_ch_id else message.channel
            embed = discord.Embed(title="🎉 Level Up!", description=f"{member.mention} reached **Level {new_lv}**! 🚀", color=0xFAA61A)
            try:
                await lv_ch.send(embed=embed, delete_after=30)
            except Exception:
                pass
            for lv_key, role_key in [("5", "level5_role"), ("10", "level10_role"), ("25", "level25_role")]:
                if new_lv >= int(lv_key):
                    lv_role_id = get(guild.id, role_key)
                    if lv_role_id:
                        lv_role = guild.get_role(lv_role_id)
                        if lv_role and lv_role not in member.roles:
                            try:
                                await member.add_roles(lv_role)
                            except Exception:
                                pass

    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    mod_log_id = get(member.guild.id, "mod_channel") or get(member.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if before.channel is None and after.channel is not None:
        voice_log.insert(0, {"action": "joined", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        add_activity("🎙️", f"{member.display_name} joined voice #{after.channel.name}")
        if log: await log.send(f"🎙️ **{member}** joined **{after.channel.name}**")
    elif before.channel is not None and after.channel is None:
        voice_log.insert(0, {"action": "left", "member": str(member), "channel": before.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log: await log.send(f"🔇 **{member}** left **{before.channel.name}**")
    elif before.channel != after.channel:
        voice_log.insert(0, {"action": "moved", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log: await log.send(f"↔️ **{member}** moved **{before.channel.name}** → **{after.channel.name}**")
    while len(voice_log) > 100:
        voice_log.pop()

@bot.event
async def on_member_ban(guild, user):
    ban_log_data.append({"user": str(user), "uid": user.id, "reason": "—", "time": datetime.now().strftime("%d %b %Y %H:%M")})
    mod_log_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="🔨 Member Banned", color=0xED4245)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)")
        embed.timestamp = utcnow()
        await log.send(embed=embed)

@bot.event
async def on_member_unban(guild, user):
    mod_log_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="✅ Member Unbanned", color=0x3BA55C)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)")
        embed.timestamp = utcnow()
        await log.send(embed=embed)

# ============================================================
# TASKS
# ============================================================
statuses = ["Young Boy Studios 🎮", "Building something epic 🔨", "Hiring developers!", "Roblox game dev team 🚀", "Use /help for commands"]

@tasks.loop(minutes=10)
async def status_cycle():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(statuses)))

@tasks.loop(seconds=5)
async def check_shutdown():
    global bot_shutdown_flag
    if bot_shutdown_flag:
        print("🔴 Bot shutdown triggered by admin.")
        await bot.close()

# ============================================================
# PREFIX COMMANDS — SETUP
# ============================================================
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(title="⚙️ Young Boy Studios — Bot Setup", description="Use the buttons below to fully configure the bot.", color=0x5865F2)
    await ctx.send(embed=embed, view=SetupMainView())

@bot.command()
@commands.has_permissions(administrator=True)
async def showconfig(ctx):
    config = load_config().get(str(ctx.guild.id), {})
    if not config:
        return await ctx.send("❌ No config found. Run `!setup` first!")
    embed = discord.Embed(title="⚙️ Current Config", color=0x5865F2)
    for key, value in config.items():
        obj = ctx.guild.get_role(value) or ctx.guild.get_channel(value)
        embed.add_field(name=key.replace("_"," ").title(), value=obj.mention if obj else str(value), inline=True)
    await ctx.send(embed=embed)

# ============================================================
# PREFIX COMMANDS — MODERATION (all group-gated)
# ============================================================
@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member}** kicked. Reason: {reason}")
    add_mod_log("Kick", str(member), str(ctx.author), reason, "#faa61a")
    add_activity("👢", f"{member.display_name} was kicked", reason)

@bot.command()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member}** banned. Reason: {reason}")
    add_mod_log("Ban", str(member), str(ctx.author), reason, "#ed4245")
    add_activity("🔨", f"{member.display_name} was banned", reason)

@bot.command()
async def unban(ctx, *, name):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == name:
            await ctx.guild.unban(entry.user)
            return await ctx.send(f"✅ **{entry.user}** unbanned.")
    await ctx.send("❌ User not found.")

@bot.command()
async def mute(ctx, member: discord.Member, duration: int = 10, *, reason="No reason"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    try:
        # ✅ FIXED: timezone-aware
        await member.timeout(utcnow() + timedelta(minutes=duration), reason=reason)
        await ctx.send(f"🔇 **{member}** muted for {duration} mins. Reason: {reason}")
        add_mod_log("Mute", str(member), str(ctx.author), f"{duration}min — {reason}", "#faa61a")
    except Exception as e:
        await ctx.send(f"❌ Failed: {e}")

@bot.command()
async def unmute(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.timeout(None)
    await ctx.send(f"🔊 **{member}** unmuted.")

@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    if member.id not in warnings_data:
        warnings_data[member.id] = []
    warnings_data[member.id].append({"reason": reason, "by": str(ctx.author), "time": datetime.now().isoformat()})
    count = len(warnings_data[member.id])
    await ctx.send(f"⚠️ **{member}** warned ({count}/3). Reason: {reason}")
    add_activity("⚠️", f"{member.display_name} warned ({count}/3)", reason)
    add_mod_log("Warn", str(member), str(ctx.author), reason, "#faa61a")
    try:
        await member.send(f"⚠️ You've been warned in **Young Boy Studios**.\nReason: {reason}\nWarnings: {count}/3")
    except:
        pass
    if count >= 3:
        await ctx.send(f"🚨 {member.mention} has reached 3 warnings!")

@bot.command()
async def warnings(ctx, member: discord.Member = None):
    member = member or ctx.author
    warns = warnings_data.get(member.id, [])
    if not warns:
        return await ctx.send(f"✅ **{member}** has no warnings.")
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=0xFFAA00)
    for i, w in enumerate(warns, 1):
        embed.add_field(name=f"Warning {i}", value=f"{w['reason']} — by {w['by']}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def clearwarnings(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    count = len(warnings_data.pop(member.id, []))
    await ctx.send(f"✅ Cleared {count} warning(s) for **{member}**.")

@bot.command()
async def purge(ctx, amount: int):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Deleted {amount} messages.", delete_after=3)

@bot.command()
async def slowmode(ctx, seconds: int):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"⏱️ Slowmode set to {seconds}s.")

@bot.command()
async def lock(ctx):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")

@bot.command()
async def unlock(ctx):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 Channel unlocked.")

@bot.command()
async def nick(ctx, member: discord.Member, *, nickname):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.edit(nick=nickname)
    await ctx.send(f"✅ Nickname changed to **{nickname}**.")

@bot.command()
async def addrole(ctx, member: discord.Member, *, role_name):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.add_roles(role)
        await ctx.send(f"✅ Added **{role.name}** to {member.mention}.")
    else:
        await ctx.send("❌ Role not found.")

@bot.command()
async def removerole(ctx, member: discord.Member, *, role_name):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.remove_roles(role)
        await ctx.send(f"✅ Removed **{role.name}** from {member.mention}.")
    else:
        await ctx.send("❌ Role not found.")

@bot.command()
async def timeout(ctx, member: discord.Member, minutes: int = 10, *, reason="No reason"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if not await require_group_prefix(ctx): return
    try:
        # ✅ FIXED: timezone-aware
        await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
        await ctx.send(f"⏰ **{member}** timed out for **{minutes} minutes**. Reason: {reason}")
        add_mod_log("Timeout", str(member), str(ctx.author), f"{minutes}min — {reason}", "#faa61a")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to timeout this member.")

@bot.command()
async def untimeout(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.timeout(None)
    await ctx.send(f"✅ **{member}**'s timeout removed.")

# ============================================================
# PREFIX COMMANDS — INFO (group-gated for non-info commands)
# ============================================================
@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%d/%m/%Y"))
    embed.add_field(name="Created", value=member.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="Roles", value=", ".join([r.mention for r in member.roles[1:]]) or "None", inline=False)
    embed.add_field(name="Warnings", value=len(warnings_data.get(member.id, [])))
    if member.id in roblox_links:
        embed.add_field(name="🎮 Roblox", value=roblox_links[member.id]["username"])
    await ctx.send(embed=embed)

@bot.command()
async def serverinfo(ctx):
    g = ctx.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2)
    embed.set_thumbnail(url=g.icon.url if g.icon else None)
    embed.add_field(name="Owner", value=g.owner.mention)
    embed.add_field(name="Members", value=g.member_count)
    embed.add_field(name="Channels", value=len(g.channels))
    embed.add_field(name="Roles", value=len(g.roles))
    embed.add_field(name="Created", value=g.created_at.strftime("%d/%m/%Y"))
    await ctx.send(embed=embed)

@bot.command()
async def ping(ctx):
    await ctx.send(f"🏓 Pong! **{round(bot.latency * 1000)}ms**")

@bot.command()
async def uptime(ctx):
    await ctx.send(f"⏱️ Bot uptime: **{uptime_str() or 'just started!'}**")

@bot.command()
async def rank(ctx, member: discord.Member = None):
    if not await require_group_prefix(ctx): return
    member = member or ctx.author
    data = xp_data.get(member.id, {"xp": 0, "level": 0, "messages": 0, "name": str(member)})
    xp = data["xp"]
    level = get_level(xp)
    next_lv = xp_for_level(level + 1)
    prev_lv = xp_for_level(level)
    pct = int((xp - prev_lv) / max(next_lv - prev_lv, 1) * 100)
    rank_pos = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)
    position = next((i + 1 for i, (uid, _) in enumerate(rank_pos) if uid == member.id), "?")
    embed = discord.Embed(title=f"🏅 {member.display_name}'s Rank", color=0x5865F2)
    embed.add_field(name="Level", value=f"**{level}**")
    embed.add_field(name="XP", value=f"**{xp}** / {next_lv}")
    embed.add_field(name="Rank", value=f"**#{position}**")
    embed.add_field(name="Messages", value=str(data.get("messages", 0)))
    embed.add_field(name="Progress", value=f"{pct}% to Level {level + 1}", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)

@bot.command(aliases=["lb", "top"])
async def leaderboard(ctx):
    if not await require_group_prefix(ctx): return
    if not xp_data:
        return await ctx.send("❌ No XP data yet!")
    top = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:10]
    embed = discord.Embed(title="🏆 XP Leaderboard", color=0x5865F2)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, d) in enumerate(top):
        prefix = medals[i] if i < 3 else f"{i+1}."
        name = d.get("name", str(uid)).split("#")[0]
        lines.append(f"{prefix} **{name}** — Level {get_level(d['xp'])} · {d['xp']} XP")
    embed.description = "\n".join(lines)
    await ctx.send(embed=embed)

# ============================================================
# ECONOMY PREFIX (group-gated)
# ============================================================
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    if not await require_group_prefix(ctx): return
    member = member or ctx.author
    eco = get_economy(member.id, str(member))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    await ctx.send(embed=embed)

@bot.command()
async def daily(ctx):
    if not await require_group_prefix(ctx): return
    eco = get_economy(ctx.author.id, str(ctx.author))
    now = datetime.now()
    if eco["last_daily"] and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400:
        remaining = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
        h, rem = divmod(int(remaining), 3600)
        m, _ = divmod(rem, 60)
        return await ctx.send(f"⏰ Come back in **{h}h {m}m**.")
    amount = random.randint(200, 500)
    eco["balance"] += amount
    eco["total_earned"] += amount
    eco["last_daily"] = now.isoformat()
    await ctx.send(f"✅ {ctx.author.mention} claimed **{amount:,} coins!** 💰")

@bot.command()
async def work(ctx):
    if not await require_group_prefix(ctx): return
    eco = get_economy(ctx.author.id, str(ctx.author))
    now = datetime.now()
    if eco["last_work"] and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600:
        remaining = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
        m, s = divmod(int(remaining), 60)
        return await ctx.send(f"⏰ Rest for **{m}m {s}s**.")
    jobs = ["coded a Roblox script 💻", "modelled an epic build 🎨", "fixed a bug 🐛", "designed a UI 🖥️", "ran a game test 🎮"]
    amount = random.randint(50, 200)
    eco["balance"] += amount
    eco["total_earned"] += amount
    eco["last_work"] = now.isoformat()
    await ctx.send(f"💼 {ctx.author.mention} {random.choice(jobs)} and earned **{amount:,} coins!**")

@bot.command(aliases=["give"])
async def pay(ctx, member: discord.Member, amount: int):
    if not await require_group_prefix(ctx): return
    if amount <= 0: return await ctx.send("❌ Positive amount only.")
    payer = get_economy(ctx.author.id, str(ctx.author))
    if payer["balance"] < amount: return await ctx.send(f"❌ Only **{payer['balance']:,} coins**.")
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount
    payee["total_earned"] += amount
    await ctx.send(f"✅ {ctx.author.mention} sent **{amount:,} coins** to {member.mention}! 💸")

@bot.command()
async def gamble(ctx, amount: int):
    if not await require_group_prefix(ctx): return
    if amount <= 0: return await ctx.send("❌ Positive amount only.")
    eco = get_economy(ctx.author.id, str(ctx.author))
    if eco["balance"] < amount: return await ctx.send(f"❌ Only **{eco['balance']:,} coins**.")
    if random.random() > 0.5:
        eco["balance"] += amount
        eco["total_earned"] += amount
        await ctx.send(f"🎲 {ctx.author.mention} bet **{amount:,}** and **WON**! 🎉")
    else:
        eco["balance"] -= amount
        await ctx.send(f"🎲 {ctx.author.mention} bet **{amount:,}** and **LOST**. 😢")

@bot.command()
async def slots(ctx, bet: int = 50):
    if not await require_group_prefix(ctx): return
    eco = get_economy(ctx.author.id, str(ctx.author))
    if eco["balance"] < bet: return await ctx.send(f"❌ Only **{eco['balance']:,} coins**.")
    symbols = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰"]
    reels = [random.choice(symbols) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        multi = 10 if reels[0] == "💎" else 5
        win = bet * multi
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"🎉 **JACKPOT!** Won **{win:,}**!"
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        win = bet * 2
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"✅ **Nice!** Won **{win:,}**!"
    else:
        eco["balance"] -= bet
        result = f"❌ **No luck.** Lost **{bet:,}**."
    await ctx.send(f"🎰 | {reels[0]} | {reels[1]} | {reels[2]} |\n{result}\nBalance: **{eco['balance']:,}**")

# ============================================================
# FUN PREFIX (group-gated)
# ============================================================
EIGHTBALL = ["It is certain. ✅","It is decidedly so. ✅","Without a doubt. ✅","Yes, definitely. ✅","Most likely. 🟡","Outlook good. 🟡","Reply hazy, try again. ❓","Ask again later. ❓","Don't count on it. ❌","My reply is no. ❌","Very doubtful. ❌"]

@bot.command(name="8ball")
async def eightball(ctx, *, question: str):
    if not await require_group_prefix(ctx): return
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def coinflip(ctx):
    if not await require_group_prefix(ctx): return
    await ctx.send(f"🪙 **{random.choice(['Heads', 'Tails'])}!**")

@bot.command()
async def dice(ctx):
    if not await require_group_prefix(ctx): return
    await ctx.send(f"🎲 You rolled a **{random.randint(1, 6)}**!")

@bot.command()
async def snipe(ctx):
    if not await require_group_prefix(ctx): return
    data = snipe_data.get(ctx.channel.id)
    if not data: return await ctx.send("❌ Nothing to snipe here!")
    embed = discord.Embed(description=data["content"], color=0xED4245)
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    await ctx.send(embed=embed)

@bot.command()
async def announce(ctx, *, message):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    ann_id = get(ctx.guild.id, "announcements_channel")
    channel = bot.get_channel(ann_id) if ann_id else ctx.channel
    embed = discord.Embed(title="📢 Announcement", description=message, color=0x5865F2, timestamp=utcnow())
    embed.set_footer(text=f"Posted by {ctx.author}")
    await channel.send("@everyone", embed=embed)

@bot.command()
async def suggest(ctx, *, suggestion):
    if not await require_group_prefix(ctx): return
    general_id = get(ctx.guild.id, "general_channel")
    channel = bot.get_channel(general_id) if general_id else ctx.channel
    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=0x00FF00)
    embed.set_footer(text=f"Suggested by {ctx.author}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    try: await ctx.message.delete()
    except: pass

@bot.command()
async def note(ctx, member: discord.Member, *, note_text):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    uid = str(member.id)
    if uid not in notes_data: notes_data[uid] = []
    notes_data[uid].append({"note": note_text, "by": str(ctx.author), "time": datetime.now().strftime("%d %b %Y %H:%M")})
    await ctx.send(f"📝 Note added for {member.mention}.")

@bot.command()
async def notes(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    member_notes = notes_data.get(str(member.id), [])
    if not member_notes: return await ctx.send(f"📝 No notes for {member}.")
    embed = discord.Embed(title=f"📝 Notes for {member}", color=0xFFAA00)
    for i, n in enumerate(member_notes, 1):
        embed.add_field(name=f"Note {i} by {n['by']}", value=n["note"], inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def giveaway(ctx, duration: int, *, prize):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    if not await require_group_prefix(ctx): return
    gw_ch_id = get(ctx.guild.id, "giveaway_channel")
    channel = bot.get_channel(gw_ch_id) if gw_ch_id else ctx.channel
    embed = discord.Embed(title="🎉 GIVEAWAY!", description=f"**{prize}**\n\nReact with 🎉 to enter!\nEnds in **{duration} minutes**", color=0xFF79C6, timestamp=datetime.now() + timedelta(minutes=duration))
    embed.set_footer(text=f"Hosted by {ctx.author.display_name} · Ends at")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaway_data[msg.id] = {"prize": prize, "channel": channel.id, "ends": (datetime.now() + timedelta(minutes=duration)).isoformat(), "host": str(ctx.author)}
    add_activity("🎉", f"Giveaway started: {prize}", f"{duration}min")
    await asyncio.sleep(duration * 60)
    if msg.id in giveaway_data:
        try:
            msg = await channel.fetch_message(msg.id)
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot] if reaction else []
            if users:
                winner = random.choice(users)
                await channel.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
                add_activity("🏆", f"Giveaway ended: {prize}", f"Winner: {winner.display_name}")
            else:
                await channel.send(f"❌ Not enough entries for **{prize}**.")
        except Exception as e:
            print(f"Giveaway error: {e}")
        giveaway_data.pop(msg.id, None)

@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📚 YBS Bot — Commands", description="Use `/help` for the full interactive slash command menu!", color=0x5865F2)
    embed.add_field(name="⚙️ Setup", value="`!setup` `!showconfig`", inline=False)
    embed.add_field(name="🔨 Moderation", value="`!kick` `!ban` `!unban` `!mute` `!unmute` `!timeout` `!warn` `!warnings` `!clearwarnings` `!purge` `!lock` `!unlock`", inline=False)
    embed.add_field(name="ℹ️ Info", value="`!userinfo` `!serverinfo` `!ping` `!uptime` `!rank` `!leaderboard`", inline=False)
    embed.add_field(name="💰 Economy", value="`!balance` `!daily` `!work` `!pay` `!gamble` `!slots`", inline=False)
    embed.add_field(name="🎮 Fun", value="`!8ball` `!coinflip` `!dice` `!snipe` `!suggest` `!announce`", inline=False)
    embed.set_footer(text="Prefix: ! · Use /help for full slash command menu · Requires YBS Group Membership")
    await ctx.send(embed=embed)

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
        if uid not in warnings_data: warnings_data[uid] = []
        warnings_data[uid].append({"reason": self.reason.value, "by": str(interaction.user), "time": datetime.now().isoformat()})
        add_mod_log("Warn", str(self.member), str(interaction.user), self.reason.value, "#faa61a")
        add_activity("⚠️", f"{self.member.display_name} warned", self.reason.value)
        try: await self.member.send(f"⚠️ You were warned in **{interaction.guild.name}**\nReason: {self.reason.value}")
        except: pass
        await interaction.response.send_message(f"⚠️ **{self.member}** warned. Total: **{len(warnings_data[uid])}**.", ephemeral=True)

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
            await interaction.response.send_message(f"🔨 **{self.member}** banned.", ephemeral=True)
        except: await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)

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
            # ✅ FIXED: timezone-aware
            await self.member.timeout(utcnow() + timedelta(minutes=mins), reason=self.reason.value or "No reason")
            add_mod_log("Timeout", str(self.member), str(interaction.user), f"{mins}m — {self.reason.value or 'No reason'}", "#faa61a")
            await interaction.response.send_message(f"⏰ **{self.member}** timed out for **{mins} minutes**.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Couldn't timeout: {e}", ephemeral=True)

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
        await interaction.response.send_message(f"📝 Note added for **{self.member}**.", ephemeral=True)

class AnnounceModal(Modal, title="📢 Post Announcement"):
    title_input = TextInput(label="Title", placeholder="Announcement title…", max_length=200)
    content = TextInput(label="Message", style=discord.TextStyle.paragraph, placeholder="Your announcement content…", max_length=2000)
    color_hex = TextInput(label="Color hex (e.g. 5865f2)", required=False, max_length=7)
    ping_input = TextInput(label="Ping? (type 'everyone' or leave blank)", required=False, max_length=50)
    def __init__(self, channel):
        super().__init__()
        self.channel = channel
    async def on_submit(self, interaction):
        try: color = int(self.color_hex.value.strip("#"), 16) if self.color_hex.value.strip() else 0x5865f2
        except: color = 0x5865f2
        embed = discord.Embed(title=self.title_input.value, description=self.content.value, color=color)
        embed.set_footer(text=f"Posted by {interaction.user} · {datetime.now().strftime('%d %b %Y %H:%M')}")
        ping = "@everyone" if "everyone" in (self.ping_input.value or "").lower() else None
        await self.channel.send(content=ping, embed=embed)
        await interaction.response.send_message(f"✅ Announcement posted in {self.channel.mention}!", ephemeral=True)
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
        if self.footer_text.value: embed.set_footer(text=self.footer_text.value)
        if self.image_url.value:
            try: embed.set_image(url=self.image_url.value)
            except: pass
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ Embed posted!", ephemeral=True)

# ============================================================
# MOD MENU VIEW
# ============================================================
class ModMenuView(View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=120)
        self.member = member

    @discord.ui.button(label="⚠️ Warn", style=discord.ButtonStyle.secondary, row=0)
    async def warn_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.send_modal(WarnModal(self.member))

    @discord.ui.button(label="👢 Kick", style=discord.ButtonStyle.danger, row=0)
    async def kick_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.kick(reason=f"Kicked by {interaction.user}")
            add_mod_log("Kick", str(self.member), str(interaction.user), "Via mod menu", "#faa61a")
            await interaction.response.send_message(f"👢 **{self.member}** kicked.", ephemeral=True)
        except: await interaction.response.send_message("❌ Couldn't kick.", ephemeral=True)

    @discord.ui.button(label="🔨 Ban", style=discord.ButtonStyle.danger, row=0)
    async def ban_btn(self, interaction, button):
        if not interaction.user.guild_permissions.ban_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.send_modal(BanModal(self.member))

    @discord.ui.button(label="⏰ Timeout", style=discord.ButtonStyle.secondary, row=0)
    async def timeout_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.send_modal(TimeoutModal(self.member))

    @discord.ui.button(label="📝 Add Note", style=discord.ButtonStyle.primary, row=0)
    async def note_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.send_modal(NoteModal(self.member))

    @discord.ui.button(label="🔇 Mute 1h", style=discord.ButtonStyle.secondary, row=1)
    async def mute1h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            # ✅ FIXED: timezone-aware
            await self.member.timeout(utcnow() + timedelta(hours=1), reason=f"Muted 1h by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "1 hour", "#faa61a")
            await interaction.response.send_message(f"🔇 **{self.member}** muted for 1 hour.", ephemeral=True)
        except: await interaction.response.send_message("❌ Failed.", ephemeral=True)

    @discord.ui.button(label="🔇 Mute 24h", style=discord.ButtonStyle.secondary, row=1)
    async def mute24h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            # ✅ FIXED: timezone-aware
            await self.member.timeout(utcnow() + timedelta(hours=24), reason=f"Muted 24h by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "24 hours", "#faa61a")
            await interaction.response.send_message(f"🔇 **{self.member}** muted for 24 hours.", ephemeral=True)
        except: await interaction.response.send_message("❌ Failed.", ephemeral=True)

    @discord.ui.button(label="🔔 Unmute", style=discord.ButtonStyle.success, row=1)
    async def unmute_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.timeout(None)
            await interaction.response.send_message(f"🔔 **{self.member}** unmuted.", ephemeral=True)
        except: await interaction.response.send_message("❌ Failed.", ephemeral=True)

    @discord.ui.button(label="🗑️ Clear Warnings", style=discord.ButtonStyle.danger, row=1)
    async def clearwarn_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        count = len(warnings_data.pop(self.member.id, []))
        await interaction.response.send_message(f"✅ Cleared **{count}** warning(s) for **{self.member}**.", ephemeral=True)

    @discord.ui.button(label="📋 Full History", style=discord.ButtonStyle.primary, row=2)
    async def history_btn(self, interaction, button):
        warns = warnings_data.get(self.member.id, [])
        user_notes = notes_data.get(str(self.member.id), [])
        embed = discord.Embed(title=f"📋 Full History — {self.member}", color=0x5865f2)
        embed.set_thumbnail(url=self.member.display_avatar.url)
        embed.add_field(name="⚠️ Warnings", value=f"`{len(warns)}`", inline=True)
        embed.add_field(name="📝 Notes", value=f"`{len(user_notes)}`", inline=True)
        if self.member.id in roblox_links:
            embed.add_field(name="🎮 Roblox", value=roblox_links[self.member.id]["username"], inline=True)
        if warns:
            embed.add_field(name="Recent Warnings", value="\n".join(f"• **{w['reason'][:55]}** *({w['time'][:10]})*" for w in warns[-5:]), inline=False)
        if user_notes:
            embed.add_field(name="Staff Notes", value="\n".join(f"• {n['note'][:65]} *(by {n['by'].split('#')[0]})*" for n in user_notes[-3:]), inline=False)
        if not warns and not user_notes:
            embed.description = "✅ Clean history."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📤 DM Member", style=discord.ButtonStyle.secondary, row=2)
    async def dm_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        member_ref = self.member
        guild_ref = interaction.guild
        class QuickDMModal(Modal, title="📤 Send DM to Member"):
            msg = TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=1000)
            async def on_submit(self2, interaction2):
                try:
                    emb = discord.Embed(title=f"📬 Message from {guild_ref.name} Staff", description=self2.msg.value, color=0x5865f2)
                    await member_ref.send(embed=emb)
                    await interaction2.response.send_message(f"✅ DM sent to **{member_ref}**.", ephemeral=True)
                except: await interaction2.response.send_message("❌ Couldn't DM.", ephemeral=True)
        await interaction.response.send_modal(QuickDMModal())

    @discord.ui.button(label="🪃 Softban", style=discord.ButtonStyle.danger, row=2)
    async def softban_btn(self, interaction, button):
        if not interaction.user.guild_permissions.ban_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.ban(reason=f"Softban by {interaction.user}", delete_message_days=3)
            await interaction.guild.unban(self.member, reason="Softban")
            add_mod_log("Softban", str(self.member), str(interaction.user), "3 days messages cleared", "#faa61a")
            await interaction.response.send_message(f"🪃 **{self.member}** softbanned.", ephemeral=True)
        except: await interaction.response.send_message("❌ Failed.", ephemeral=True)

    @discord.ui.button(label="🎮 View Roblox", style=discord.ButtonStyle.blurple, row=2)
    async def roblox_btn(self, interaction, button):
        rb = roblox_links.get(self.member.id)
        if not rb:
            return await interaction.response.send_message(f"❌ **{self.member.display_name}** has not linked their Roblox account.", ephemeral=True)
        embed = discord.Embed(title=f"🎮 Roblox Profile — {rb['display']}", url=f"https://www.roblox.com/users/{rb['roblox_id']}/profile", color=0x00B2FF)
        embed.add_field(name="Username", value=f"@{rb['username']}")
        embed.add_field(name="Roblox ID", value=str(rb["roblox_id"]))
        embed.add_field(name="Verified", value="✅ Yes" if rb.get("verified") else "❓ Unverified")
        embed.add_field(name="Linked", value=rb.get("linked_at", "—"))
        if rb.get("thumb"): embed.set_thumbnail(url=rb["thumb"])
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
            btn = Button(label=f"{emojis[i]} {opt} — 0 votes", style=discord.ButtonStyle.secondary, custom_id=f"poll_{i}", row=i // 3)
            btn.callback = self._make_vote(i)
            self.add_item(btn)

    def _make_vote(self, idx):
        async def vote(interaction):
            uid = interaction.user.id
            for voters in self.votes.values():
                voters.discard(uid)
            self.votes[idx].add(uid)
            emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
            for item in self.children:
                if hasattr(item, "custom_id") and item.custom_id.startswith("poll_"):
                    i = int(item.custom_id.split("_")[1])
                    cnt = len(self.votes[i])
                    item.label = f"{emojis[i]} {self.options[i]} — {cnt} vote{'s' if cnt != 1 else ''}"
            await interaction.response.edit_message(view=self)
        return vote

# ============================================================
# SLASH COMMANDS — HELP
# ============================================================
class HelpCategorySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🏅 XP & Leveling", value="xp"),
            discord.SelectOption(label="💰 Economy", value="economy"),
            discord.SelectOption(label="🎮 Fun & Games", value="fun"),
            discord.SelectOption(label="🛠️ Utility", value="utility"),
            discord.SelectOption(label="🔨 Moderation", value="mod"),
            discord.SelectOption(label="🎫 Tickets & AutoMod", value="tickets"),
            discord.SelectOption(label="ℹ️ Info & Server", value="info"),
            discord.SelectOption(label="🎮 Roblox", value="roblox"),
            discord.SelectOption(label="🎉 Events", value="events"),
            discord.SelectOption(label="👑 Admin", value="admin"),
        ]
        super().__init__(placeholder="Select a category…", options=options)

    async def callback(self, interaction):
        cats = {
            "xp": ("🏅 XP & Leveling", "`/rank` `/leaderboard` `/addxp` `/resetxp`"),
            "economy": ("💰 Economy", "`/economy-menu` — All economy in one place\n`/balance` `/daily` `/work` `/pay` `/gamble` `/slots` `/rob` `/richlist` `/givecoins`"),
            "fun": ("🎮 Fun & Games", "`/8ball` `/joke` `/rps` `/rate` `/ship` `/truth` `/dare` `/coinflip` `/dice` `/choose` `/mock` `/compliment` `/pp` `/iq` `/snipe` `/roblox-fact`"),
            "utility": ("🛠️ Utility", "`/calc` `/afk` `/remindme` `/snipe` `/embed` `/say`"),
            "mod": ("🔨 Moderation", "`/modmenu` `/mod-tools` `/warn` `/kick` `/ban` `/unban` `/timeout` `/mute` `/softban` `/tempban` `/purge` `/lock` `/unlock` `/slowmode` `/nuke` `/nick` `/addrole` `/removerole` `/massrole` `/warnings` `/clearwarnings` `/history` `/note` `/notes` `/staffpanel`"),
            "tickets": ("🎫 Tickets & AutoMod", "`/ticket` `/closeticket` `/addword` `/removeword` `/wordlist`"),
            "info": ("ℹ️ Info & Server", "`/member-info` `/server-menu` `/userinfo` `/serverinfo` `/avatar` `/roleinfo` `/ping` `/uptime` `/botinfo` `/membercount` `/stafflist` `/newmembers`"),
            "roblox": ("🎮 Roblox", "`/roblox` `/roblox-verify` `/roblox-link` `/roblox-unlink` `/roblox-whois` `/roblox-fact`"),
            "events": ("🎉 Events", "`/giveaway` `/poll` `/announce` `/suggest` `/serverrules`"),
            "admin": ("👑 Admin", "`/premium` `/shutdown` `/shutdown-confirm` `/lockdown` `/unlockdown` `/config` `/massrole`"),
        }
        title, desc = cats.get(self.values[0], ("Help", "—"))
        embed = discord.Embed(title=title, description=desc, color=0x5865F2)
        embed.set_footer(text="⚠️ All commands require YBS Roblox Group membership")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect())

@tree.command(name="help", description="Browse all bot commands by category")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(title="📚 Young Boy Studios Bot", description="Select a category below to see all available commands.\n\n⚠️ **All commands require YBS Roblox Group membership.**\nJoin: " + REQUIRED_GROUP_URL, color=0x5865F2)
    embed.add_field(name="🔨 Moderation", value="Warn, kick, ban, timeout, purge…", inline=True)
    embed.add_field(name="🏅 XP & Levels", value="Rank, leaderboard, level roles…", inline=True)
    embed.add_field(name="💰 Economy", value="Daily, work, gamble, shop…", inline=True)
    embed.add_field(name="🎮 Roblox", value="Lookup, verify, link accounts…", inline=True)
    embed.add_field(name="🎉 Events", value="Giveaways, polls, announcements…", inline=True)
    embed.add_field(name="👑 Admin", value="Shutdown, config, premium…", inline=True)
    embed.set_footer(text="Young Boy Studios · Use the dropdown to explore")
    await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)

# ============================================================
# SLASH — ROBLOX
# ============================================================
@tree.command(name="roblox", description="Look up a Roblox profile")
@app_commands.describe(username="Roblox username to look up")
async def slash_roblox(interaction: discord.Interaction, username: str):
    if not await require_group(interaction): return
    await interaction.response.defer()
    data = await fetch_roblox(username)
    if not data:
        return await interaction.followup.send(f"❌ Couldn't find **{username}**.", ephemeral=True)
    embed = discord.Embed(title=f"🎮 {data['display']} (@{data['name']})", url=f"https://www.roblox.com/users/{data['id']}/profile", color=0x00B2FF)
    embed.add_field(name="User ID", value=str(data["id"]))
    embed.add_field(name="Account Created", value=data["created"] or "—")
    if data["desc"]: embed.add_field(name="Bio", value=data["desc"][:300], inline=False)
    if data["thumb"]: embed.set_thumbnail(url=data["thumb"])
    embed.set_footer(text=f"Looked up by {interaction.user}")
    await interaction.followup.send(embed=embed)

@tree.command(name="roblox-verify", description="Link your Roblox account to Discord with verification")
async def slash_roblox_verify(interaction: discord.Interaction):
    if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
        linked = roblox_links[interaction.user.id]
        embed = discord.Embed(title="✅ Already Verified!", description=f"Linked to **{linked['display']}** (@{linked['username']})\n\nUse `/roblox-unlink` to remove.", color=0x3BA55C)
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    await interaction.response.send_modal(VerifyModal())

@tree.command(name="roblox-link", description="Manually link a Discord member to a Roblox account [Admin only]")
@app_commands.describe(member="Discord member", username="Roblox username")
async def slash_roblox_link(interaction: discord.Interaction, member: discord.Member, username: str):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    data = await fetch_roblox(username)
    if not data:
        return await interaction.followup.send(f"❌ Roblox user **{username}** not found.", ephemeral=True)
    roblox_links[member.id] = {
        "username": data["name"], "roblox_id": data["id"], "display": data["display"],
        "thumb": data.get("thumb"), "discord_name": str(member),
        "linked_at": datetime.now().strftime("%d %b %Y"), "verified": False, "manual": True
    }
    add_activity("🔗", f"Manual link: {member.display_name} → {data['name']}")
    embed = discord.Embed(title="🔗 Account Linked", description=f"**{member.mention}** linked to **{data['display']}** (@{data['name']})", color=0x3BA55C)
    if data.get("thumb"): embed.set_thumbnail(url=data["thumb"])
    await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="roblox-unlink", description="Remove your Roblox account link")
async def slash_roblox_unlink(interaction: discord.Interaction):
    if interaction.user.id not in roblox_links:
        return await interaction.response.send_message("❌ No linked account.", ephemeral=True)
    old = roblox_links.pop(interaction.user.id)
    verified_role_id = get(interaction.guild.id, "verified_role")
    if verified_role_id:
        role = interaction.guild.get_role(verified_role_id)
        if role and role in interaction.user.roles:
            try: await interaction.user.remove_roles(role)
            except: pass
    await interaction.response.send_message(f"✅ Unlinked Roblox account **@{old['username']}**.", ephemeral=True)

@tree.command(name="roblox-whois", description="Find a Discord user by Roblox username")
@app_commands.describe(username="Roblox username to search for")
async def slash_roblox_whois(interaction: discord.Interaction, username: str):
    if not await require_group(interaction): return
    match = next((uid for uid, v in roblox_links.items() if v["username"].lower() == username.lower()), None)
    if not match:
        return await interaction.response.send_message(f"❌ No linked Discord user for **@{username}**.", ephemeral=True)
    member = interaction.guild.get_member(match)
    rb = roblox_links[match]
    embed = discord.Embed(title="🔍 Roblox → Discord Lookup", color=0x00B2FF)
    embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}")
    embed.add_field(name="💬 Discord", value=member.mention if member else f"User `{match}`")
    embed.add_field(name="✅ Verified", value="Yes" if rb.get("verified") else "No (manual link)")
    embed.add_field(name="Linked", value=rb.get("linked_at", "—"))
    if rb.get("thumb"): embed.set_thumbnail(url=rb["thumb"])
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="roblox-fact", description="Get a random Roblox development fact")
async def slash_roblox_fact(interaction: discord.Interaction):
    if not await require_group(interaction): return
    facts = [
        "Roblox uses Lua 5.1 as its scripting language, but extends it with a custom runtime called Luau.",
        "The `game` object in Roblox is the root of the DataModel — everything lives inside it.",
        "Roblox's physics engine is based on a custom implementation of ODE (Open Dynamics Engine).",
        "LocalScripts only run on the client; Scripts only run on the server. Never mix them up!",
        "You can use `RunService.Heartbeat` to run code every frame — great for smooth animations.",
        "DataStores are rate-limited: you can only make ~60 requests per minute per game server.",
        "Roblox was founded in 2004 and launched to the public in 2006.",
        "ModuleScripts are the best way to share code between scripts — they act like libraries.",
        "Using `task.spawn()` instead of `coroutine.wrap()` is the recommended modern approach in Roblox.",
        "Luau adds type annotations, optional typing, and improved performance over standard Lua.",
    ]
    embed = discord.Embed(title="💡 Roblox Dev Fact", description=random.choice(facts), color=0x00B2FF)
    embed.set_footer(text="Young Boy Studios · Dev Knowledge")
    await interaction.response.send_message(embed=embed)

# ============================================================
# SLASH — MODERATION (all group-gated)
# ============================================================
@tree.command(name="modmenu", description="Open full moderation control panel for a member [Staff]")
@app_commands.describe(member="Member to moderate")
async def slash_modmenu(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    warns = len(warnings_data.get(member.id, []))
    notes_count = len(notes_data.get(str(member.id), []))
    rb = roblox_links.get(member.id)
    embed = discord.Embed(title=f"⚖️ Mod Control Panel — {member.display_name}", color=0x5865f2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 User", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="📅 Joined", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?", inline=True)
    embed.add_field(name="🎭 Top Role", value=member.top_role.mention, inline=True)
    embed.add_field(name="⚠️ Warnings", value=f"`{warns}`", inline=True)
    embed.add_field(name="📝 Notes", value=f"`{notes_count}`", inline=True)
    embed.add_field(name="🔇 Timed Out", value="✅ Yes" if member.is_timed_out() else "❌ No", inline=True)
    embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}" if rb else "Not linked", inline=True)
    embed.add_field(name="✅ Verified", value="Yes" if rb and rb.get("verified") else "No", inline=True)
    embed.set_footer(text="All actions are logged · Use buttons below")
    await interaction.response.send_message(embed=embed, view=ModMenuView(member), ephemeral=True)

@tree.command(name="warn", description="Warn a member [Staff]")
@app_commands.describe(member="Member to warn", reason="Reason")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    if member.id not in warnings_data: warnings_data[member.id] = []
    warnings_data[member.id].append({"reason": reason, "by": str(interaction.user), "time": datetime.now().isoformat()})
    add_mod_log("Warn", str(member), str(interaction.user), reason, "#faa61a")
    add_activity("⚠️", f"{member.display_name} warned", reason)
    try: await member.send(f"⚠️ Warned in **{interaction.guild.name}**: {reason}")
    except: pass
    await interaction.response.send_message(f"⚠️ **{member}** warned. Total: {len(warnings_data[member.id])}")

@tree.command(name="kick", description="Kick a member [Staff]")
@app_commands.describe(member="Member to kick", reason="Reason")
async def slash_kick(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.kick(reason=reason)
        add_mod_log("Kick", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(f"👢 **{member}** kicked.")
    except: await interaction.response.send_message("❌ Couldn't kick.", ephemeral=True)

@tree.command(name="ban", description="Ban a member [Staff]")
@app_commands.describe(member="Member to ban", reason="Reason")
async def slash_ban(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.ban(reason=reason)
        add_mod_log("Ban", str(member), str(interaction.user), reason, "#ed4245")
        await interaction.response.send_message(f"🔨 **{member}** banned.")
    except: await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)

@tree.command(name="unban", description="Unban a user by ID [Staff]")
@app_commands.describe(user_id="Discord user ID", reason="Reason")
async def slash_unban(interaction: discord.Interaction, user_id: str, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user, reason=reason)
        add_mod_log("Unban", str(user), str(interaction.user), reason, "#3ba55c")
        await interaction.response.send_message(f"✅ **{user}** unbanned.")
    except: await interaction.response.send_message("❌ User not found or not banned.", ephemeral=True)

@tree.command(name="timeout", description="Timeout a member [Staff]")
@app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
async def slash_timeout(interaction: discord.Interaction, member: discord.Member, minutes: int = 5, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        # ✅ FIXED: timezone-aware
        await member.timeout(utcnow() + timedelta(minutes=minutes), reason=reason)
        add_mod_log("Timeout", str(member), str(interaction.user), f"{minutes}m — {reason}", "#faa61a")
        await interaction.response.send_message(f"⏰ **{member}** timed out for {minutes}m.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Couldn't timeout: {e}", ephemeral=True)

@tree.command(name="untimeout", description="Remove a member's timeout [Staff]")
@app_commands.describe(member="Member to untimeout")
async def slash_untimeout(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await member.timeout(None)
        await interaction.response.send_message(f"✅ **{member}**'s timeout removed.")
    except: await interaction.response.send_message("❌ Couldn't untimeout.", ephemeral=True)

@tree.command(name="mute", description="Mute a member [Staff]")
@app_commands.describe(member="Member", hours="Duration in hours", reason="Reason")
async def slash_mute(interaction: discord.Interaction, member: discord.Member, hours: float = 1.0, reason: str = "No reason"):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        # ✅ FIXED: timezone-aware
        await member.timeout(utcnow() + timedelta(hours=min(hours, 672)), reason=reason)
        add_mod_log("Mute", str(member), str(interaction.user), f"{hours}h — {reason}", "#faa61a")
        await interaction.response.send_message(f"🔇 **{member}** muted for **{hours}h**.")
    except Exception as e:
        await interaction.response.send_message(f"❌ Couldn't mute: {e}", ephemeral=True)

@tree.command(name="unmute", description="Unmute a member [Staff]")
async def slash_unmute(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await member.timeout(None)
        await interaction.response.send_message(f"🔔 **{member}** unmuted.")
    except: await interaction.response.send_message("❌ Couldn't unmute.", ephemeral=True)

@tree.command(name="softban", description="Softban — ban+unban to clear messages [Staff]")
@app_commands.describe(member="Member", reason="Reason", delete_days="Days of messages to delete (1-7)")
async def slash_softban(interaction: discord.Interaction, member: discord.Member, reason: str = "Softban", delete_days: int = 3):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.ban(reason=f"Softban: {reason}", delete_message_days=max(1, min(7, delete_days)))
        await interaction.guild.unban(member, reason="Softban")
        add_mod_log("Softban", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(f"🪃 **{member}** softbanned.")
    except: await interaction.response.send_message("❌ Couldn't softban.", ephemeral=True)

@tree.command(name="tempban", description="Temporarily ban a member [Staff]")
@app_commands.describe(member="Member", hours="Ban duration in hours", reason="Reason")
async def slash_tempban(interaction: discord.Interaction, member: discord.Member, hours: float = 24.0, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.ban(reason=f"Tempban {hours}h: {reason}", delete_message_days=1)
        add_mod_log("Tempban", str(member), str(interaction.user), f"{hours}h — {reason}", "#ed4245")
        ban_log_data.append({"user": str(member), "uid": member.id, "reason": f"Tempban {hours}h: {reason}", "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
        await interaction.response.send_message(f"🕐 **{member}** banned for **{hours}h**.")
    except: await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)

@tree.command(name="purge", description="Delete messages in bulk [Staff]")
@app_commands.describe(amount="Messages to delete (1-100)")
async def slash_purge(interaction: discord.Interaction, amount: int = 10):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=max(1, min(100, amount)))
    await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)

@tree.command(name="lock", description="Lock a channel [Staff]")
@app_commands.describe(channel="Channel to lock", reason="Reason")
async def slash_lock(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
    embed = discord.Embed(title="🔒 Channel Locked", description=f"**Reason:** {reason}\n**By:** {interaction.user.mention}", color=0xed4245)
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ {ch.mention} locked.", ephemeral=True)

@tree.command(name="unlock", description="Unlock a channel [Staff]")
@app_commands.describe(channel="Channel to unlock")
async def slash_unlock(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    ch = channel or interaction.channel
    await ch.set_permissions(interaction.guild.default_role, send_messages=None)
    embed = discord.Embed(title="🔓 Channel Unlocked", description=f"Unlocked by {interaction.user.mention}.", color=0x3ba55c)
    await ch.send(embed=embed)
    await interaction.response.send_message(f"✅ {ch.mention} unlocked.", ephemeral=True)

@tree.command(name="slowmode", description="Set slowmode [Staff]")
@app_commands.describe(seconds="Delay in seconds (0=off)", channel="Channel")
async def slash_slowmode(interaction: discord.Interaction, seconds: int = 0, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    ch = channel or interaction.channel
    await ch.edit(slowmode_delay=max(0, min(21600, seconds)))
    msg = f"⏱️ Slowmode **disabled** in {ch.mention}." if seconds == 0 else f"⏱️ Slowmode **{seconds}s** in {ch.mention}."
    await interaction.response.send_message(msg)

@tree.command(name="nuke", description="Clone and delete this channel [Staff]")
@app_commands.describe(reason="Reason")
async def slash_nuke(interaction: discord.Interaction, reason: str = "Channel nuke"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    ch = interaction.channel
    await interaction.response.send_message("💥 Nuking...", ephemeral=True)
    new_ch = await ch.clone(reason=f"Nuked by {interaction.user}: {reason}")
    await ch.delete()
    embed = discord.Embed(title="💥 Channel Nuked", description=f"Nuked by **{interaction.user}**. Reason: {reason}", color=0xed4245)
    await new_ch.send(embed=embed)
    add_activity("💥", f"#{ch.name} nuked", reason)

@tree.command(name="nick", description="Change a member's nickname [Staff]")
@app_commands.describe(member="Member", nickname="New nickname (blank to clear)")
async def slash_nick(interaction: discord.Interaction, member: discord.Member, nickname: str = None):
    if not interaction.user.guild_permissions.manage_nicknames:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await member.edit(nick=nickname)
        await interaction.response.send_message(f"✏️ Nickname {'set to **' + nickname + '**' if nickname else 'cleared'} for **{member}**.")
    except: await interaction.response.send_message("❌ Couldn't change nickname.", ephemeral=True)

@tree.command(name="addrole", description="Add a role to a member [Staff]")
@app_commands.describe(member="Member", role="Role to add")
async def slash_addrole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ {role.mention} added to **{member}**.")
    except: await interaction.response.send_message("❌ Couldn't add role.", ephemeral=True)

@tree.command(name="removerole", description="Remove a role from a member [Staff]")
@app_commands.describe(member="Member", role="Role to remove")
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        await member.remove_roles(role)
        await interaction.response.send_message(f"✅ {role.mention} removed from **{member}**.")
    except: await interaction.response.send_message("❌ Couldn't remove role.", ephemeral=True)

@tree.command(name="massrole", description="Add or remove a role from all members [Admin]")
@app_commands.describe(role="The role")
@app_commands.choices(action=[app_commands.Choice(name="➕ Add to all", value="add"), app_commands.Choice(name="➖ Remove from all", value="remove")])
async def slash_massrole(interaction: discord.Interaction, action: str, role: discord.Role):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
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
    await interaction.followup.send(f"✅ **{action.title()}ed** {role.mention} for **{count}** members.", ephemeral=True)

@tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
async def slash_warnings(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    warns = warnings_data.get(member.id, [])
    embed = discord.Embed(title=f"⚠️ Warnings — {member.display_name}", color=0xfaa61a)
    embed.set_thumbnail(url=member.display_avatar.url)
    if warns:
        embed.description = f"**{len(warns)} total warning(s)**"
        for i, w in enumerate(warns, 1):
            embed.add_field(name=f"#{i} — {w['time'][:10]}", value=f"**Reason:** {w['reason']}\n**By:** {w['by'].split('#')[0]}", inline=False)
    else:
        embed.description = "✅ No warnings on record."
    await interaction.response.send_message(embed=embed)

@tree.command(name="clearwarnings", description="Clear all warnings for a member [Staff]")
@app_commands.describe(member="Member")
async def slash_clearwarnings(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    count = len(warnings_data.pop(member.id, []))
    await interaction.response.send_message(f"✅ Cleared **{count}** warning(s) for **{member}**.")

@tree.command(name="history", description="View full moderation history [Staff]")
@app_commands.describe(member="Member to check")
async def slash_history(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    warns = warnings_data.get(member.id, [])
    logs = [l for l in mod_log_data if member.name in l.get("target", "")]
    user_notes = notes_data.get(str(member.id), [])
    embed = discord.Embed(title=f"📋 Mod History — {member}", color=0x5865f2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings", value=f"`{len(warns)}`", inline=True)
    embed.add_field(name="🔨 Actions", value=f"`{len(logs)}`", inline=True)
    embed.add_field(name="📝 Notes", value=f"`{len(user_notes)}`", inline=True)
    rb = roblox_links.get(member.id)
    embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}" if rb else "Not linked", inline=True)
    if warns:
        embed.add_field(name="Warnings", value="\n".join(f"• **{w['reason'][:55]}** *({w['time'][:10]})*" for w in warns[-8:]), inline=False)
    if user_notes:
        embed.add_field(name="Staff Notes", value="\n".join(f"• {n['note'][:70]} *(by {n['by'].split('#')[0]}, {n['time']})*" for n in user_notes[-5:]), inline=False)
    if not warns and not logs and not user_notes:
        embed.description = "✅ Clean history."
    await interaction.response.send_message(embed=embed)

@tree.command(name="note", description="Add a staff note to a member [Staff]")
@app_commands.describe(member="Member", note="Note content")
async def slash_note(interaction: discord.Interaction, member: discord.Member, note: str):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    uid = str(member.id)
    if uid not in notes_data: notes_data[uid] = []
    notes_data[uid].append({"note": note, "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
    await interaction.response.send_message(f"📝 Note added for **{member}**.", ephemeral=True)

@tree.command(name="notes", description="View staff notes for a member [Staff]")
@app_commands.describe(member="Member")
async def slash_notes(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    user_notes = notes_data.get(str(member.id), [])
    embed = discord.Embed(title=f"📝 Staff Notes — {member.display_name}", color=0x5865f2)
    if user_notes:
        for i, n in enumerate(user_notes, 1):
            embed.add_field(name=f"Note #{i} — {n['time']}", value=f"{n['note']}\n*by {n['by'].split('#')[0]}*", inline=False)
    else:
        embed.description = "📭 No notes."
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — INFO
# ============================================================
@tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(member="Member")
async def slash_userinfo(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.add_field(name="ID", value=str(member.id))
    embed.add_field(name="Joined Server", value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?")
    embed.add_field(name="Joined Discord", value=member.created_at.strftime("%d %b %Y"))
    embed.add_field(name="Roles", value=str(len(member.roles) - 1))
    embed.add_field(name="⚠️ Warnings", value=str(len(warnings_data.get(member.id, []))))
    xd = xp_data.get(member.id, {})
    if xd:
        embed.add_field(name="Level", value=str(get_level(xd.get("xp", 0))))
        embed.add_field(name="XP", value=str(xd.get("xp", 0)))
    eco = economy_data.get(member.id, {})
    if eco: embed.add_field(name="Coins", value=f"{eco.get('balance', 0):,}")
    rb = roblox_links.get(member.id)
    if rb: embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}" + (" ✅" if rb.get("verified") else ""))
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="serverinfo", description="View server info")
async def slash_serverinfo(interaction: discord.Interaction):
    if not await require_group(interaction): return
    g = interaction.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2)
    embed.add_field(name="Members", value=str(g.member_count))
    embed.add_field(name="Channels", value=str(len(g.channels)))
    embed.add_field(name="Roles", value=str(len(g.roles)))
    embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"))
    embed.add_field(name="Owner", value=str(g.owner))
    embed.add_field(name="Boosts", value=str(g.premium_subscription_count))
    if g.icon: embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="avatar", description="View a member's avatar")
@app_commands.describe(member="Member")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    embed = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", color=0x5865F2)
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="ping", description="Check bot latency")
async def slash_ping(interaction: discord.Interaction):
    ws_lat = round(bot.latency * 1000)
    color = 0x3ba55c if ws_lat < 80 else 0xfaa61a if ws_lat < 150 else 0xed4245
    embed = discord.Embed(title="🏓 Pong!", color=color)
    embed.add_field(name="📡 WebSocket", value=f"`{ws_lat}ms`")
    embed.add_field(name="⏱️ Uptime", value=f"`{uptime_str() or '—'}`")
    embed.add_field(name="📊 Servers", value=f"`{len(bot.guilds)}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="uptime", description="Check bot uptime")
async def slash_uptime(interaction: discord.Interaction):
    await interaction.response.send_message(f"⏱️ Bot uptime: **{uptime_str() or 'just started!'}**")

@tree.command(name="botinfo", description="View bot information")
async def slash_botinfo(interaction: discord.Interaction):
    upd = datetime.now() - bot_start_time if bot_start_time else timedelta(0)
    h, rem = divmod(int(upd.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    embed = discord.Embed(title="🤖 Young Boy Studios Bot", color=0x5865f2)
    embed.add_field(name="⏱️ Uptime", value=f"`{h}h {m}m {s}s`")
    embed.add_field(name="📊 Servers", value=f"`{len(bot.guilds)}`")
    embed.add_field(name="🏓 Ping", value=f"`{round(bot.latency * 1000)}ms`")
    embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
    embed.add_field(name="🎮 Roblox Linked", value=f"`{len(roblox_links)}`")
    embed.add_field(name="👾 XP Members", value=f"`{len(xp_data)}`")
    embed.add_field(name="💰 Economy Users", value=f"`{len(economy_data)}`")
    embed.add_field(name="🎫 Tickets", value=f"`{len(ticket_data)}`")
    embed.add_field(name="🐛 Bug Reports", value=f"`{len(bug_reports_data)}`")
    embed.add_field(name="👑 Premium", value=f"`{len(premium_data)}`")
    if bot.user and bot.user.avatar: embed.set_thumbnail(url=bot.user.avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="membercount", description="Member count breakdown")
async def slash_membercount(interaction: discord.Interaction):
    if not await require_group(interaction): return
    g = interaction.guild
    bots = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
    embed = discord.Embed(title=f"👥 {g.name}", color=0x5865f2)
    embed.add_field(name="👥 Total", value=f"`{g.member_count}`")
    embed.add_field(name="🧑 Humans", value=f"`{humans}`")
    embed.add_field(name="🤖 Bots", value=f"`{bots}`")
    embed.add_field(name="🟢 Online", value=f"`{online}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="roleinfo", description="View info about a role")
@app_commands.describe(role="Role to inspect")
async def slash_roleinfo(interaction: discord.Interaction, role: discord.Role):
    if not await require_group(interaction): return
    perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
    embed = discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="ID", value=f"`{role.id}`")
    embed.add_field(name="Members", value=f"`{len(role.members)}`")
    embed.add_field(name="Mentionable", value="✅" if role.mentionable else "❌")
    embed.add_field(name="Created", value=role.created_at.strftime("%d %b %Y"))
    if perms: embed.add_field(name="Key Permissions", value=", ".join(perms[:15]), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="stafflist", description="View all staff members")
async def slash_stafflist(interaction: discord.Interaction):
    if not await require_group(interaction): return
    staff = []
    for member in interaction.guild.members:
        if member.bot: continue
        if member.guild_permissions.administrator: staff.append((member, "🔴 Admin"))
        elif member.guild_permissions.ban_members: staff.append((member, "🟠 Moderator"))
        elif member.guild_permissions.kick_members: staff.append((member, "🟡 Jr. Mod"))
        elif member.guild_permissions.manage_messages: staff.append((member, "🟢 Helper"))
    embed = discord.Embed(title="👮 Staff List", color=0x5865f2)
    embed.description = "\n".join(f"{rank} **{m.display_name}**" for m, rank in staff[:25]) or "No staff found."
    embed.set_footer(text=f"{len(staff)} staff member(s)")
    await interaction.response.send_message(embed=embed)

@tree.command(name="newmembers", description="View 10 most recently joined members")
async def slash_newmembers(interaction: discord.Interaction):
    if not await require_group(interaction): return
    members = sorted([m for m in interaction.guild.members if not m.bot], key=lambda m: m.joined_at or datetime.min, reverse=True)[:10]
    embed = discord.Embed(title="🆕 Newest Members", color=0x5865f2)
    embed.description = "\n".join(f"`{i+1}.` **{m.display_name}** — {m.joined_at.strftime('%d %b %Y') if m.joined_at else '?'}" for i, m in enumerate(members))
    await interaction.response.send_message(embed=embed)

# ============================================================
# SLASH — XP
# ============================================================
@tree.command(name="rank", description="View your rank card")
@app_commands.describe(member="Member to check")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    d = xp_data.get(member.id, {"xp": 0, "messages": 0, "name": str(member)})
    xp = d["xp"]
    lv = get_level(xp)
    nxt = xp_for_level(lv + 1)
    prv = xp_for_level(lv)
    pct = int((xp - prv) / max(nxt - prv, 1) * 100)
    pos = next((i + 1 for i, (uid, _) in enumerate(sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)) if uid == member.id), "?")
    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
    embed = discord.Embed(title=f"🏅 {member.display_name}", color=0x5865F2)
    embed.add_field(name="Level", value=f"**{lv}**")
    embed.add_field(name="XP", value=f"**{xp}** / {nxt}")
    embed.add_field(name="Rank", value=f"**#{pos}**")
    embed.add_field(name="Messages", value=f"**{d.get('messages', 0)}**")
    embed.add_field(name="Progress", value=f"`{bar}` {pct}%", inline=False)
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="leaderboard", description="XP and coin leaderboards")
async def slash_leaderboard(interaction: discord.Interaction):
    if not await require_group(interaction): return
    embed = discord.Embed(title="🏆 Leaderboards", color=0x5865F2)
    medals = ["🥇", "🥈", "🥉", "4.", "5."]
    if xp_data:
        top = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:5]
        embed.add_field(name="🏅 XP Rankings", value="\n".join(f"{medals[i]} **{d.get('name','?').split('#')[0]}** — Lv {get_level(d['xp'])} · {d['xp']} XP" for i, (_, d) in enumerate(top)), inline=False)
    if economy_data:
        top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:5]
        embed.add_field(name="💰 Rich List", value="\n".join(f"{medals[i]} **{d.get('name','?').split('#')[0]}** — 💰 {d['balance']:,}" for i, (_, d) in enumerate(top)), inline=False)
    if not xp_data and not economy_data:
        embed.description = "No data yet!"
    await interaction.response.send_message(embed=embed)

@tree.command(name="addxp", description="Add XP to a member [Admin]")
@app_commands.describe(member="Member", amount="XP to add")
async def slash_addxp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    if member.id not in xp_data:
        xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    xp_data[member.id]["xp"] += amount
    xp_data[member.id]["level"] = get_level(xp_data[member.id]["xp"])
    await interaction.response.send_message(f"✅ Gave **{amount} XP** to {member.mention}.")

@tree.command(name="resetxp", description="Reset a member's XP [Admin]")
@app_commands.describe(member="Member")
async def slash_resetxp(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    xp_data.pop(member.id, None)
    await interaction.response.send_message(f"🔄 Reset XP for {member.mention}.")

# ============================================================
# SLASH — ECONOMY (group-gated)
# ============================================================
@tree.command(name="balance", description="Check YBS Coins balance")
@app_commands.describe(member="Member to check")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    eco = get_economy(member.id, str(member))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="daily", description="Claim your daily YBS Coins")
async def slash_daily(interaction: discord.Interaction):
    if not await require_group(interaction): return
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_daily"] and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400:
        diff = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
        h, rem = divmod(int(diff), 3600); m = rem // 60
        return await interaction.response.send_message(f"⏰ Come back in **{h}h {m}m**.", ephemeral=True)
    amt = random.randint(200, 500)
    if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
        amt += random.randint(50, 100)
    eco["balance"] += amt; eco["total_earned"] += amt
    eco["last_daily"] = now.isoformat()
    await interaction.response.send_message(f"✅ {interaction.user.mention} claimed **{amt:,} coins!** 💰")

@tree.command(name="work", description="Work to earn YBS Coins")
async def slash_work(interaction: discord.Interaction):
    if not await require_group(interaction): return
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_work"] and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600:
        diff = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
        m, s = divmod(int(diff), 60)
        return await interaction.response.send_message(f"⏰ Rest **{m}m {s}s** before working again.", ephemeral=True)
    jobs = ["coded a Roblox script 💻", "modelled an epic build 🎨", "fixed a nasty bug 🐛", "scripted an obby 🏃", "designed a UI 🖥️", "ran a game test 🎮"]
    amt = random.randint(50, 200)
    eco["balance"] += amt; eco["total_earned"] += amt
    eco["last_work"] = now.isoformat()
    await interaction.response.send_message(f"💼 {interaction.user.mention} {random.choice(jobs)} and earned **{amt:,} coins!**")

@tree.command(name="pay", description="Send YBS Coins to another member")
@app_commands.describe(member="Who to pay", amount="How many coins")
async def slash_pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not await require_group(interaction): return
    if amount <= 0: return await interaction.response.send_message("❌ Positive amount only.", ephemeral=True)
    payer = get_economy(interaction.user.id, str(interaction.user))
    if payer["balance"] < amount: return await interaction.response.send_message(f"❌ Only **{payer['balance']:,} coins**.", ephemeral=True)
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount; payee["total_earned"] += amount
    await interaction.response.send_message(f"✅ {interaction.user.mention} → {member.mention} **{amount:,} coins** 💸")

@tree.command(name="gamble", description="Gamble your YBS Coins")
@app_commands.describe(amount="Coins to bet")
async def slash_gamble(interaction: discord.Interaction, amount: int):
    if not await require_group(interaction): return
    if amount <= 0: return await interaction.response.send_message("❌ Positive only.", ephemeral=True)
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < amount: return await interaction.response.send_message(f"❌ Only **{eco['balance']:,}**.", ephemeral=True)
    if random.random() > 0.5:
        eco["balance"] += amount; eco["total_earned"] += amount
        await interaction.response.send_message(f"🎲 {interaction.user.mention} bet **{amount:,}** and **WON**! 🎉 Balance: {eco['balance']:,}")
    else:
        eco["balance"] -= amount
        await interaction.response.send_message(f"🎲 {interaction.user.mention} bet **{amount:,}** and **LOST**. 😢 Balance: {eco['balance']:,}")

@tree.command(name="slots", description="Play the slot machine")
@app_commands.describe(bet="Coins to bet (default 50)")
async def slash_slots(interaction: discord.Interaction, bet: int = 50):
    if not await require_group(interaction): return
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < bet: return await interaction.response.send_message(f"❌ Only **{eco['balance']:,}**.", ephemeral=True)
    syms = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰"]
    r = [random.choice(syms) for _ in range(3)]
    if r[0] == r[1] == r[2]:
        win = bet * (10 if r[0] == "💎" else 5); eco["balance"] += win - bet; eco["total_earned"] += win - bet; result = f"🎉 JACKPOT! Won **{win:,}**!"
    elif r[0] == r[1] or r[1] == r[2]:
        win = bet * 2; eco["balance"] += win - bet; eco["total_earned"] += win - bet; result = f"✅ Won **{win:,}**!"
    else:
        eco["balance"] -= bet; result = f"❌ Lost **{bet:,}**."
    await interaction.response.send_message(f"🎰 | {r[0]} | {r[1]} | {r[2]} |\n{result} Balance: **{eco['balance']:,}**")

@tree.command(name="rob", description="Try to rob another member")
@app_commands.describe(member="Who to rob")
async def slash_rob(interaction: discord.Interaction, member: discord.Member):
    if not await require_group(interaction): return
    if member.id == interaction.user.id:
        return await interaction.response.send_message("❌ You can't rob yourself!", ephemeral=True)
    robber = get_economy(interaction.user.id, str(interaction.user))
    victim = get_economy(member.id, str(member))
    if victim["balance"] < 100:
        return await interaction.response.send_message(f"❌ **{member.display_name}** is too broke to rob!", ephemeral=True)
    if random.random() > 0.5:
        stolen = random.randint(50, min(500, victim["balance"]))
        robber["balance"] += stolen; robber["total_earned"] += stolen
        victim["balance"] -= stolen
        await interaction.response.send_message(f"🦝 {interaction.user.mention} robbed **{member.display_name}** for **{stolen:,} coins!** 💸")
    else:
        fine = random.randint(50, 200)
        robber["balance"] = max(0, robber["balance"] - fine)
        await interaction.response.send_message(f"🚨 {interaction.user.mention} got caught and paid a **{fine:,} coin** fine! 🚔")

@tree.command(name="richlist", description="Top 10 richest members")
async def slash_richlist(interaction: discord.Interaction):
    if not await require_group(interaction): return
    if not economy_data: return await interaction.response.send_message("❌ No economy data yet!")
    top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:10]
    embed = discord.Embed(title="💰 Rich List", color=0xFAA61A)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else f'{i+1}.'} **{d.get('name', str(uid)).split('#')[0]}** — {d['balance']:,} coins" for i, (uid, d) in enumerate(top)]
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed)

@tree.command(name="givecoins", description="Give coins to a member [Admin]")
@app_commands.describe(member="Member", amount="Coins to give")
async def slash_givecoins(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    eco = get_economy(member.id, str(member))
    eco["balance"] += amount; eco["total_earned"] += amount
    await interaction.response.send_message(f"✅ Gave **{amount:,} coins** to {member.mention}.")

# ============================================================
# SLASH — FUN (group-gated)
# ============================================================
@tree.command(name="8ball", description="Ask the magic 8-ball")
@app_commands.describe(question="Your question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    if not await require_group(interaction): return
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="joke", description="Get a random dev joke")
async def slash_joke(interaction: discord.Interaction):
    if not await require_group(interaction): return
    jokes = [("Why do programmers prefer dark mode?","Because light attracts bugs! 🐛"),("Why did the Roblox player refuse to leave?","He was ROBLOXed in! 🎮"),("Why did the developer go broke?","He used up all his cache! 💸"),("Why do Java devs wear glasses?","Because they don't C#!")]
    q, a = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke!", color=0xFAA61A)
    embed.add_field(name="Setup", value=q, inline=False)
    embed.add_field(name="Punchline", value=f"||{a}||", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="rps", description="Rock Paper Scissors vs the bot")
@app_commands.choices(choice=[app_commands.Choice(name="🪨 Rock", value="rock"), app_commands.Choice(name="📄 Paper", value="paper"), app_commands.Choice(name="✂️ Scissors", value="scissors")])
async def slash_rps(interaction: discord.Interaction, choice: str):
    if not await require_group(interaction): return
    ch = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    bot_c = random.choice(list(ch.keys()))
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    result = "🤝 **Tie!**" if choice == bot_c else ("🎉 **You win!**" if wins[choice] == bot_c else "😔 **Bot wins!**")
    await interaction.response.send_message(f"{ch[choice]} **{choice.title()}** vs **{bot_c.title()}** {ch[bot_c]}\n{result}")

@tree.command(name="rate", description="Rate something out of 100")
@app_commands.describe(thing="What to rate")
async def slash_rate(interaction: discord.Interaction, thing: str):
    if not await require_group(interaction): return
    score = random.randint(0, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    color = 0x3BA55C if score >= 70 else 0xFAA61A if score >= 40 else 0xED4245
    await interaction.response.send_message(embed=discord.Embed(title=f"⭐ Rating: {thing}", description=f"**{score}/100**\n`{bar}`", color=color))

@tree.command(name="ship", description="Check compatibility")
@app_commands.describe(user1="First member", user2="Second member")
async def slash_ship(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member = None):
    if not await require_group(interaction): return
    user2 = user2 or interaction.user
    score = (user1.id + user2.id) % 101
    bar = "💗" * (score // 10) + "🖤" * (10 - score // 10)
    await interaction.response.send_message(embed=discord.Embed(title="💘 Compatibility", description=f"**{user1.display_name}** 💕 **{user2.display_name}**\n\n{bar}\n\n**{score}%** compatible!", color=0xFF79C6))

@tree.command(name="truth", description="Get a truth question")
async def slash_truth(interaction: discord.Interaction):
    if not await require_group(interaction): return
    ts = ["What's your most embarrassing coding mistake?","What game are you secretly working on?","Have you ever copy-pasted code you didn't understand?","What's the longest you've spent on one bug?","What dev skill do you wish you had?"]
    await interaction.response.send_message(embed=discord.Embed(title="😳 Truth!", description=random.choice(ts), color=0xFF79C6))

@tree.command(name="dare", description="Get a dare challenge")
async def slash_dare(interaction: discord.Interaction):
    if not await require_group(interaction): return
    ds = ["Make a mini-game in 30 minutes!","Build a noob character and screenshot it!","Write hello world in 3 languages!","Script a random feature in 5 minutes!","Draw your game idea in MS Paint!"]
    await interaction.response.send_message(embed=discord.Embed(title="😈 Dare!", description=random.choice(ds), color=0xED4245))

@tree.command(name="coinflip", description="Flip a coin")
async def slash_coinflip(interaction: discord.Interaction):
    if not await require_group(interaction): return
    await interaction.response.send_message(embed=discord.Embed(title="🪙 Coin Flip!", description=f"**{random.choice(['Heads', 'Tails'])}!**", color=0x5865f2))

@tree.command(name="dice", description="Roll dice")
@app_commands.describe(sides="Number of sides", count="Number of dice")
async def slash_dice(interaction: discord.Interaction, sides: int = 6, count: int = 1):
    if not await require_group(interaction): return
    sides = max(2, min(1000, sides)); count = max(1, min(10, count))
    rolls = [random.randint(1, sides) for _ in range(count)]
    embed = discord.Embed(title=f"🎲 {count}d{sides}", color=0x5865f2)
    if count > 1:
        embed.add_field(name="Rolls", value=" + ".join(f"**{r}**" for r in rolls))
        embed.add_field(name="Total", value=f"**{sum(rolls)}**")
    else: embed.description = f"You rolled a **{rolls[0]}**!"
    await interaction.response.send_message(embed=embed)

@tree.command(name="choose", description="Pick randomly from options")
@app_commands.describe(choices="Comma-separated options")
async def slash_choose(interaction: discord.Interaction, choices: str):
    if not await require_group(interaction): return
    options = [c.strip() for c in choices.split(",") if c.strip()]
    if len(options) < 2: return await interaction.response.send_message("❌ Need at least 2 options.", ephemeral=True)
    await interaction.response.send_message(embed=discord.Embed(title="🎯 I Choose...", description=f"**{random.choice(options)}**", color=0x5865f2))

@tree.command(name="mock", description="MoCkIfY some text")
@app_commands.describe(text="Text to mockify")
async def slash_mock(interaction: discord.Interaction, text: str):
    if not await require_group(interaction): return
    await interaction.response.send_message("".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text)))

@tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Who to compliment")
async def slash_compliment(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    cs = ["is an absolute legend! 🌟","makes this server better! 💪","is the most talented dev here! 🎮","is going to build something incredible! 🚀"]
    await interaction.response.send_message(f"💝 {member.mention} {random.choice(cs)}")

@tree.command(name="pp", description="Check pp size (not real)")
@app_commands.describe(member="Member")
async def slash_pp(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    size = member.id % 15
    await interaction.response.send_message(f"🍆 **{member.display_name}:** `8{'=' * size}D` ({size} inches)")

@tree.command(name="iq", description="Check IQ (not real)")
@app_commands.describe(member="Member")
async def slash_iq(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    member = member or interaction.user
    iq = (member.id + 47) % 201
    rating = "needs help 💀" if iq < 70 else "average 😐" if iq < 100 else "smart 🧠" if iq < 130 else "genius 🎓" if iq < 160 else "BIG BRAIN 🤯"
    await interaction.response.send_message(embed=discord.Embed(title=f"🧠 IQ — {member.display_name}", description=f"**IQ: {iq}** — {rating}", color=0x5865f2))

@tree.command(name="snipe", description="See the last deleted message")
async def slash_snipe(interaction: discord.Interaction):
    if not await require_group(interaction): return
    data = snipe_data.get(interaction.channel.id)
    if not data: return await interaction.response.send_message("❌ Nothing to snipe!", ephemeral=True)
    embed = discord.Embed(description=data["content"], color=0xED4245)
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="calc", description="Calculate a math expression")
@app_commands.describe(expression="Math expression")
async def slash_calc(interaction: discord.Interaction, expression: str):
    if not await require_group(interaction): return
    if not all(c in "0123456789+-*/.() " for c in expression):
        return await interaction.response.send_message("❌ Only basic math operators!", ephemeral=True)
    try:
        result = eval(expression)
        await interaction.response.send_message(embed=discord.Embed(title="🧮 Calculator", description=f"`{expression}` = **{result}**", color=0x5865F2))
    except: await interaction.response.send_message("❌ Invalid expression!", ephemeral=True)

@tree.command(name="afk", description="Set or clear your AFK status")
@app_commands.describe(reason="AFK reason (blank to clear)")
async def slash_afk(interaction: discord.Interaction, reason: str = None):
    if not await require_group(interaction): return
    if reason:
        afk_data[interaction.user.id] = {"reason": reason, "time": datetime.now().strftime("%H:%M")}
        await interaction.response.send_message(f"💤 **{interaction.user.display_name}** is now AFK: *{reason}*")
    else:
        afk_data.pop(interaction.user.id, None)
        await interaction.response.send_message("✅ AFK cleared.", ephemeral=True)

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(minutes="Minutes until reminder", reminder="What to remind you of")
async def slash_remindme(interaction: discord.Interaction, minutes: int, reminder: str):
    if not await require_group(interaction): return
    await interaction.response.send_message(f"⏰ I'll remind you in **{minutes} minute{'s' if minutes != 1 else ''}**!", ephemeral=True)
    await asyncio.sleep(minutes * 60)
    try: await interaction.user.send(f"⏰ **Reminder:** {reminder}")
    except:
        ch = interaction.channel
        if ch: await ch.send(f"⏰ {interaction.user.mention} — reminder: {reminder}")

# ============================================================
# SLASH — EVENTS (group-gated)
# ============================================================
@tree.command(name="giveaway", description="Start a giveaway [Staff]")
@app_commands.describe(duration="Duration in minutes", prize="What to give away", channel="Channel")
async def slash_giveaway(interaction: discord.Interaction, duration: int, prize: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    gw_ch_id = get(interaction.guild.id, "giveaway_channel")
    ch = channel or (bot.get_channel(gw_ch_id) if gw_ch_id else interaction.channel)
    embed = discord.Embed(title="🎉 GIVEAWAY!", description=f"**{prize}**\n\nReact with 🎉 to enter!\nEnds in **{duration} minutes**", color=0xFF79C6, timestamp=datetime.now() + timedelta(minutes=duration))
    embed.set_footer(text=f"Hosted by {interaction.user.display_name} · Ends at")
    msg = await ch.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaway_data[msg.id] = {"prize": prize, "channel": ch.id, "ends": (datetime.now() + timedelta(minutes=duration)).isoformat(), "host": str(interaction.user)}
    add_activity("🎉", f"Giveaway: {prize}", f"{duration}min")
    await interaction.response.send_message(f"✅ Giveaway started in {ch.mention}!", ephemeral=True)
    await asyncio.sleep(duration * 60)
    if msg.id in giveaway_data:
        try:
            msg = await ch.fetch_message(msg.id)
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot] if reaction else []
            if users:
                winner = random.choice(users)
                await ch.send(f"🎉 Congratulations {winner.mention}! You won **{prize}**!")
            else:
                await ch.send(f"❌ No entries for **{prize}**.")
        except Exception as e: print(f"Giveaway error: {e}")
        giveaway_data.pop(msg.id, None)

@tree.command(name="poll", description="Create an interactive poll")
@app_commands.describe(question="Poll question", option1="Option 1", option2="Option 2", option3="Option 3", option4="Option 4")
async def slash_poll(interaction: discord.Interaction, question: str, option1: str, option2: str, option3: str = None, option4: str = None):
    if not await require_group(interaction): return
    options = [o for o in [option1, option2, option3, option4] if o]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    embed = discord.Embed(title=f"📊 {question}", color=0x5865f2)
    embed.description = "\n".join(f"{emojis[i]} **{opt}**" for i, opt in enumerate(options))
    embed.set_footer(text=f"Poll by {interaction.user} · Click a button to vote!")
    await interaction.response.send_message(embed=embed, view=PollView(question, options))

@tree.command(name="announce", description="Post an announcement [Staff]")
@app_commands.describe(channel="Channel to post in")
async def slash_announce(interaction: discord.Interaction, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    ch_id = get(interaction.guild.id, "announcements_channel")
    ch = channel or (bot.get_channel(ch_id) if ch_id else interaction.channel)
    await interaction.response.send_modal(AnnounceModal(ch))

@tree.command(name="suggest", description="Submit a suggestion")
@app_commands.describe(suggestion="Your suggestion")
async def slash_suggest(interaction: discord.Interaction, suggestion: str):
    if not await require_group(interaction): return
    general_id = get(interaction.guild.id, "general_channel")
    channel = bot.get_channel(general_id) if general_id else interaction.channel
    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=0x00FF00)
    embed.set_footer(text=f"Suggested by {interaction.user}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅"); await msg.add_reaction("❌")
    await interaction.response.send_message("✅ Suggestion submitted!", ephemeral=True)

@tree.command(name="serverrules", description="Post server rules [Staff]")
async def slash_serverrules(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    rules = ["Be respectful to all members.","No harassment, hate speech, or discrimination.","No spamming or excessive caps.","Keep content relevant to each channel.","No NSFW or inappropriate content.","No advertising without permission.","Follow Discord's Terms of Service.","Listen to staff and moderators.","No sharing personal information.","Have fun and be creative! 🎮"]
    embed = discord.Embed(title="📜 Server Rules", color=0x5865F2)
    embed.description = "\n".join(f"**{i+1}.** {r}" for i, r in enumerate(rules))
    embed.set_footer(text="Failure to follow rules may result in mutes, kicks, or bans.")
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Rules posted!", ephemeral=True)

@tree.command(name="embed", description="Open the custom embed builder [Staff]")
async def slash_embed(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await interaction.response.send_modal(EmbedBuilderModal())

@tree.command(name="say", description="Make the bot say something [Staff]")
@app_commands.describe(message="Message to send", channel="Channel")
async def slash_say(interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    ch = channel or interaction.channel
    await ch.send(message)
    await interaction.response.send_message("✅ Sent!", ephemeral=True)

@tree.command(name="dm", description="Send a DM to a member [Staff]")
@app_commands.describe(member="Member", message="Message")
async def slash_dm(interaction: discord.Interaction, member: discord.Member, message: str):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    try:
        embed = discord.Embed(title=f"📬 Message from {interaction.guild.name} Staff", description=message, color=0x5865f2)
        await member.send(embed=embed)
        await interaction.response.send_message(f"✅ DM sent to **{member}**.", ephemeral=True)
    except: await interaction.response.send_message("❌ Couldn't DM.", ephemeral=True)

# ============================================================
# SLASH — TICKETS (group-gated)
# ============================================================
@tree.command(name="ticket", description="Open a support ticket")
@app_commands.describe(reason="Reason")
async def slash_ticket(interaction: discord.Interaction, reason: str = "General support"):
    if not await require_group(interaction): return
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in guild.roles:
        if role.permissions.manage_messages:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    ch_name = f"ticket-{interaction.user.name.lower()[:15]}-{len(ticket_data)+1}"
    try:
        channel = await guild.create_text_channel(ch_name, overwrites=overwrites)
    except Exception:
        return await interaction.response.send_message("❌ Failed (missing permissions).", ephemeral=True)
    ticket_data[channel.id] = {"user_id": interaction.user.id, "user_name": str(interaction.user), "reason": reason, "created": datetime.now().strftime("%d %b %Y %H:%M"), "status": "open"}
    embed = discord.Embed(title="🎫 Ticket Opened", description=f"Hey {interaction.user.mention}! Staff will be with you shortly.\n**Reason:** {reason}", color=0x3BA55C)
    embed.set_footer(text="Use /closeticket to close this ticket.")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ Ticket created: {channel.mention}", ephemeral=True)
    add_activity("🎫", f"Ticket: {interaction.user.display_name}", reason)

@tree.command(name="closeticket", description="Close this ticket")
async def slash_closeticket(interaction: discord.Interaction):
    if interaction.channel.id not in ticket_data:
        return await interaction.response.send_message("❌ Not a ticket channel.", ephemeral=True)
    ticket_data[interaction.channel.id]["status"] = "closed"
    embed = discord.Embed(title="🎫 Ticket Closed", description=f"Closed by {interaction.user.mention}. Deleting in 5 seconds.", color=0xED4245)
    await interaction.response.send_message(embed=embed)
    add_activity("🔒", f"Ticket closed by {interaction.user.display_name}")
    await asyncio.sleep(5)
    try:
        await interaction.channel.delete()
        ticket_data.pop(interaction.channel.id, None)
    except: pass

# ============================================================
# SLASH — AUTOMOD
# ============================================================
@tree.command(name="addword", description="Add a word to the auto-mod filter [Staff]")
@app_commands.describe(word="Word to ban")
async def slash_addword(interaction: discord.Interaction, word: str):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    gid = str(interaction.guild.id)
    if gid not in automod_data: automod_data[gid] = []
    if word.lower() not in automod_data[gid]:
        automod_data[gid].append(word.lower())
    await interaction.response.send_message(f"✅ Added `{word}` to filter.", ephemeral=True)

@tree.command(name="removeword", description="Remove a word from the filter [Staff]")
@app_commands.describe(word="Word to remove")
async def slash_removeword(interaction: discord.Interaction, word: str):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    gid = str(interaction.guild.id)
    if gid in automod_data and word.lower() in automod_data[gid]:
        automod_data[gid].remove(word.lower())
        await interaction.response.send_message(f"✅ Removed `{word}` from filter.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ `{word}` not in filter.", ephemeral=True)

@tree.command(name="wordlist", description="View the auto-mod word filter [Staff]")
async def slash_wordlist(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    words = automod_data.get(str(interaction.guild.id), [])
    embed = discord.Embed(title="🤖 AutoMod Word Filter", color=0xed4245)
    embed.description = " · ".join(f"`{w}`" for w in words) if words else "No words in filter."
    if words: embed.set_footer(text=f"{len(words)} word(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# SLASH — STAFF PANEL
# ============================================================
class StaffPanelView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="🔒 Lock", style=discord.ButtonStyle.danger, row=0)
    async def lock_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.channel.send(embed=discord.Embed(title="🔒 Channel Locked", description=f"By {interaction.user.mention}", color=0xed4245))
        await interaction.response.send_message("✅ Locked.", ephemeral=True)

    @discord.ui.button(label="🔓 Unlock", style=discord.ButtonStyle.success, row=0)
    async def unlock_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None)
        await interaction.channel.send(embed=discord.Embed(title="🔓 Channel Unlocked", description=f"By {interaction.user.mention}", color=0x3ba55c))
        await interaction.response.send_message("✅ Unlocked.", ephemeral=True)

    @discord.ui.button(label="🗑️ Purge 10", style=discord.ButtonStyle.secondary, row=0)
    async def purge10_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=10)
        await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)

    @discord.ui.button(label="🗑️ Purge 50", style=discord.ButtonStyle.secondary, row=0)
    async def purge50_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_messages: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=50)
        await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)

    @discord.ui.button(label="💥 Nuke", style=discord.ButtonStyle.danger, row=0)
    async def nuke_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        ch = interaction.channel
        await interaction.response.defer(ephemeral=True)
        new_ch = await ch.clone(reason=f"Nuked by {interaction.user}")
        await ch.delete()
        await new_ch.send(embed=discord.Embed(title="💥 Channel Nuked", color=0xed4245))

    @discord.ui.button(label="⏱️ Slow 10s", style=discord.ButtonStyle.secondary, row=1)
    async def slow10_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.edit(slowmode_delay=10)
        await interaction.response.send_message("⏱️ Slowmode: **10 seconds**.", ephemeral=True)

    @discord.ui.button(label="⏱️ Slow 30s", style=discord.ButtonStyle.secondary, row=1)
    async def slow30_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.edit(slowmode_delay=30)
        await interaction.response.send_message("⏱️ Slowmode: **30 seconds**.", ephemeral=True)

    @discord.ui.button(label="⏱️ Slow Off", style=discord.ButtonStyle.success, row=1)
    async def slowoff_btn(self, interaction, button):
        if not interaction.user.guild_permissions.manage_channels: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.edit(slowmode_delay=0)
        await interaction.response.send_message("⏱️ Slowmode **disabled**.", ephemeral=True)

    @discord.ui.button(label="📊 Server Stats", style=discord.ButtonStyle.primary, row=1)
    async def stats_btn(self, interaction, button):
        g = interaction.guild
        bots = sum(1 for m in g.members if m.bot)
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
        embed = discord.Embed(title=f"📊 {g.name} — Stats", color=0x5865f2)
        embed.add_field(name="👥 Total", value=f"`{g.member_count}`")
        embed.add_field(name="🟢 Online", value=f"`{online}`")
        embed.add_field(name="🤖 Bots", value=f"`{bots}`")
        embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
        embed.add_field(name="🎫 Open Tickets", value=f"`{sum(1 for t in ticket_data.values() if t.get('status')=='open')}`")
        embed.add_field(name="🎮 Roblox Linked", value=f"`{len(roblox_links)}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="staffpanel", description="Open staff quick-action panel [Staff]")
async def slash_staffpanel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not await require_group(interaction): return
    embed = discord.Embed(title="🛠️ Staff Quick-Action Panel", description="Fast access to common staff tools.", color=0x5865f2)
    embed.add_field(name="🔒 Row 1", value="Lock · Unlock · Purge 10 · Purge 50 · Nuke", inline=False)
    embed.add_field(name="⏱️ Row 2", value="Slow 10s · Slow 30s · Slow Off · Server Stats", inline=False)
    await interaction.response.send_message(embed=embed, view=StaffPanelView(), ephemeral=True)

# ============================================================
# SLASH — ADMIN
# ============================================================
SHUTDOWN_CONFIRM_CODES = {}

@tree.command(name="shutdown", description="Safely shut down the bot [Owner/Admin only]")
async def slash_shutdown(interaction: discord.Interaction):
    if not (interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message("❌ Only the server owner or administrators can shut down the bot.", ephemeral=True)
    code = random.randint(100000, 999999)
    SHUTDOWN_CONFIRM_CODES[interaction.user.id] = {"code": str(code), "expires": (datetime.now() + timedelta(minutes=2)).isoformat()}
    embed = discord.Embed(title="⚠️ Bot Shutdown Confirmation", description=f"Confirm by typing `/shutdown-confirm code:{code}`\n\n*Code expires in 2 minutes.*", color=0xED4245)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="shutdown-confirm", description="Confirm bot shutdown with code [Admin only]")
@app_commands.describe(code="The confirmation code from /shutdown")
async def slash_shutdown_confirm(interaction: discord.Interaction, code: str):
    global bot_shutdown_flag
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
    pending = SHUTDOWN_CONFIRM_CODES.get(interaction.user.id)
    if not pending:
        return await interaction.response.send_message("❌ No pending shutdown. Use `/shutdown` first.", ephemeral=True)
    if datetime.fromisoformat(pending["expires"]) < datetime.now():
        SHUTDOWN_CONFIRM_CODES.pop(interaction.user.id, None)
        return await interaction.response.send_message("❌ Code expired.", ephemeral=True)
    if pending["code"] != code:
        return await interaction.response.send_message("❌ Wrong code.", ephemeral=True)
    SHUTDOWN_CONFIRM_CODES.pop(interaction.user.id, None)
    embed = discord.Embed(title="🔴 Bot Shutting Down", description=f"Confirmed by **{interaction.user.display_name}**.", color=0xED4245)
    await interaction.response.send_message(embed=embed)
    add_activity("🔴", f"Bot shutdown by {interaction.user.display_name}")
    await asyncio.sleep(2)
    bot_shutdown_flag = True

@tree.command(name="lockdown", description="Emergency lockdown — lock ALL channels [Admin]")
@app_commands.describe(reason="Reason for lockdown")
async def slash_lockdown(interaction: discord.Interaction, reason: str = "Emergency lockdown"):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    count = 0
    for ch in interaction.guild.channels:
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=False)
                count += 1
            except: pass
    add_activity("🔒", f"SERVER LOCKDOWN by {interaction.user.display_name}", reason)
    add_mod_log("Lockdown", "All channels", str(interaction.user), reason, "#ed4245")
    await interaction.followup.send(f"🔒 **Server locked down!** {count} channels locked.", ephemeral=True)

@tree.command(name="unlockdown", description="Lift server lockdown [Admin]")
async def slash_unlockdown(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    count = 0
    for ch in interaction.guild.channels:
        if isinstance(ch, discord.TextChannel):
            try:
                await ch.set_permissions(interaction.guild.default_role, send_messages=None)
                count += 1
            except: pass
    add_activity("🔓", f"Lockdown lifted by {interaction.user.display_name}")
    await interaction.followup.send(f"🔓 **Lockdown lifted!** {count} channels unlocked.", ephemeral=True)

@tree.command(name="config", description="Full bot configuration panel [Admin]")
async def slash_config(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    embed = discord.Embed(title="⚙️ Bot Setup", description="Use the buttons below to configure the bot.", color=0x5865F2)
    await interaction.response.send_message(embed=embed, view=SetupMainView(), ephemeral=True)

# ============================================================
# BUG REPORTS
# ============================================================
class BugReportModal(Modal, title="🐛 Submit Bug Report"):
    bug_title = TextInput(label="Bug Title", placeholder="Short description of the bug", max_length=100)
    description = TextInput(label="Description", style=discord.TextStyle.paragraph, placeholder="What happened? What did you expect?", max_length=1000)
    steps = TextInput(label="Steps to Reproduce", style=discord.TextStyle.paragraph, placeholder="1. Go to... 2. Click... 3. Bug occurs", max_length=500, required=False)
    game_area = TextInput(label="Game Area / Location", placeholder="e.g. Main lobby, Spawn, Shop", max_length=100, required=False)

    def __init__(self, severity: str = "Medium"):
        super().__init__()
        self.severity = severity

    async def on_submit(self, interaction: discord.Interaction):
        report = {
            "id": next_bug_id(),
            "title": self.bug_title.value,
            "description": self.description.value,
            "steps": self.steps.value or "Not provided",
            "game_area": self.game_area.value or "Not specified",
            "severity": self.severity,
            "status": "open",
            "reporter_name": str(interaction.user),
            "reporter_id": interaction.user.id,
            "timestamp": datetime.now().isoformat(),
            "images": [],
        }
        bug_reports_data.insert(0, report)
        log_ch_id = get(interaction.guild.id, "mod_channel") or get(interaction.guild.id, "logs_channel")
        log_ch = bot.get_channel(log_ch_id) if log_ch_id else None
        if log_ch:
            color = {"Critical": 0xED4245, "High": 0xFF9F43, "Medium": 0xFFD166, "Low": 0x3BA55C}.get(self.severity, 0x5865F2)
            embed = discord.Embed(title=f"🐛 {report['id']} — {self.bug_title.value}", color=color)
            embed.add_field(name="Severity", value=self.severity, inline=True)
            embed.add_field(name="Area", value=report["game_area"], inline=True)
            embed.add_field(name="Reporter", value=interaction.user.mention, inline=True)
            embed.add_field(name="Description", value=self.description.value[:500], inline=False)
            if self.steps.value:
                embed.add_field(name="Steps", value=self.steps.value[:400], inline=False)
            embed.set_footer(text=f"Reported at {datetime.now().strftime('%d %b %Y %H:%M')}")
            await log_ch.send(embed=embed)
        add_activity("🐛", f"Bug report: {self.bug_title.value}", self.severity)
        await interaction.response.send_message(f"✅ Bug report **{report['id']}** submitted!", ephemeral=True)

class BugSeverityView(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="🟢 Low", style=discord.ButtonStyle.success)
    async def low(self, interaction, button):
        await interaction.response.send_modal(BugReportModal("Low"))

    @discord.ui.button(label="🟡 Medium", style=discord.ButtonStyle.secondary)
    async def medium(self, interaction, button):
        await interaction.response.send_modal(BugReportModal("Medium"))

    @discord.ui.button(label="🟠 High", style=discord.ButtonStyle.danger)
    async def high(self, interaction, button):
        await interaction.response.send_modal(BugReportModal("High"))

    @discord.ui.button(label="🔴 Critical", style=discord.ButtonStyle.danger)
    async def critical(self, interaction, button):
        await interaction.response.send_modal(BugReportModal("Critical"))

@tree.command(name="bugreport", description="Submit a bug report for the game")
async def slash_bugreport(interaction: discord.Interaction):
    if not await require_group(interaction): return
    embed = discord.Embed(title="🐛 Submit a Bug Report", description="Select the severity of the bug, then fill in the details.", color=0xED4245)
    embed.add_field(name="🟢 Low", value="Minor visual glitch, no gameplay impact", inline=False)
    embed.add_field(name="🟡 Medium", value="Noticeable issue affecting gameplay", inline=False)
    embed.add_field(name="🟠 High", value="Major bug that breaks game features", inline=False)
    embed.add_field(name="🔴 Critical", value="Game-breaking / exploitable / crash", inline=False)
    await interaction.response.send_message(embed=embed, view=BugSeverityView(), ephemeral=True)

# ============================================================
# PREMIUM
# ============================================================
@tree.command(name="premium", description="Grant or revoke premium for a member [Admin]")
@app_commands.describe(member="Member", action="Grant or revoke", tier="premium / vip / supporter", days="Days until expiry (0 = permanent)")
@app_commands.choices(
    action=[app_commands.Choice(name="✅ Grant", value="grant"), app_commands.Choice(name="❌ Revoke", value="revoke")],
    tier=[app_commands.Choice(name="👑 Premium", value="premium"), app_commands.Choice(name="💎 VIP", value="vip"), app_commands.Choice(name="⭐ Supporter", value="supporter")]
)
async def slash_premium(interaction: discord.Interaction, member: discord.Member, action: str, tier: str = "premium", days: int = 0):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    if action == "grant":
        expires = (datetime.now() + timedelta(days=days)).isoformat() if days > 0 else "permanent"
        premium_data[member.id] = {"tier": tier, "granted_by": str(interaction.user), "expires": expires, "granted_at": datetime.now().isoformat()}
        add_activity("👑", f"Premium granted to {member.display_name}", f"{tier} · {'permanent' if days == 0 else f'{days}d'}")
        try:
            await member.send(f"👑 You've been granted **{tier.title()}** in **{interaction.guild.name}**!")
        except: pass
        await interaction.response.send_message(f"✅ Granted **{tier.title()}** to {member.mention}.", ephemeral=True)
    else:
        if member.id not in premium_data:
            return await interaction.response.send_message(f"❌ **{member}** doesn't have premium.", ephemeral=True)
        old_tier = premium_data.pop(member.id)["tier"]
        add_activity("❌", f"Premium revoked from {member.display_name}", old_tier)
        await interaction.response.send_message(f"✅ Revoked **{old_tier.title()}** from {member.mention}.", ephemeral=True)

# ============================================================
# ECONOMY MENU
# ============================================================
class EconomyMenuView(View):
    def __init__(self, user: discord.Member):
        super().__init__(timeout=120)
        self.user = user

    @discord.ui.button(label="💰 Balance", style=discord.ButtonStyle.primary, row=0)
    async def bal_btn(self, interaction, button):
        eco = get_economy(self.user.id, str(self.user))
        embed = discord.Embed(title=f"💰 {self.user.display_name}'s Wallet", color=0xFAA61A)
        embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
        embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📅 Daily", style=discord.ButtonStyle.success, row=0)
    async def daily_btn(self, interaction, button):
        eco = get_economy(interaction.user.id, str(interaction.user))
        now = datetime.now()
        if eco["last_daily"] and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400:
            diff = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
            h, rem = divmod(int(diff), 3600); m = rem // 60
            return await interaction.response.send_message(f"⏰ Come back in **{h}h {m}m**.", ephemeral=True)
        amt = random.randint(200, 500)
        eco["balance"] += amt; eco["total_earned"] += amt
        eco["last_daily"] = now.isoformat()
        await interaction.response.send_message(f"✅ Claimed **{amt:,} coins!** 💰", ephemeral=True)

    @discord.ui.button(label="💼 Work", style=discord.ButtonStyle.success, row=0)
    async def work_btn(self, interaction, button):
        eco = get_economy(interaction.user.id, str(interaction.user))
        now = datetime.now()
        if eco["last_work"] and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600:
            diff = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
            m, s = divmod(int(diff), 60)
            return await interaction.response.send_message(f"⏰ Rest **{m}m {s}s** before working again.", ephemeral=True)
        jobs = ["coded a Roblox script 💻", "modelled an epic build 🎨", "fixed a nasty bug 🐛", "designed a UI 🖥️", "ran a game test 🎮"]
        amt = random.randint(50, 200)
        eco["balance"] += amt; eco["total_earned"] += amt
        eco["last_work"] = now.isoformat()
        await interaction.response.send_message(f"💼 {random.choice(jobs)} — earned **{amt:,} coins!**", ephemeral=True)

    @discord.ui.button(label="🎲 Gamble", style=discord.ButtonStyle.danger, row=0)
    async def gamble_btn(self, interaction, button):
        eco = get_economy(interaction.user.id, str(interaction.user))
        class GambleModal(Modal, title="🎲 Gamble Coins"):
            amount = TextInput(label="Amount to bet", placeholder="Enter coins...", max_length=10)
            async def on_submit(self2, i2):
                try: amt = int(self2.amount.value)
                except ValueError: return await i2.response.send_message("❌ Enter a number.", ephemeral=True)
                if amt <= 0 or eco["balance"] < amt:
                    return await i2.response.send_message(f"❌ Invalid. Balance: **{eco['balance']:,}**", ephemeral=True)
                if random.random() > 0.5:
                    eco["balance"] += amt; eco["total_earned"] += amt
                    await i2.response.send_message(f"🎲 Bet **{amt:,}** and **WON**! 🎉 Balance: {eco['balance']:,}", ephemeral=True)
                else:
                    eco["balance"] -= amt
                    await i2.response.send_message(f"🎲 Bet **{amt:,}** and **LOST**. 😢 Balance: {eco['balance']:,}", ephemeral=True)
        await interaction.response.send_modal(GambleModal())

    @discord.ui.button(label="🎰 Slots", style=discord.ButtonStyle.secondary, row=1)
    async def slots_btn(self, interaction, button):
        eco = get_economy(interaction.user.id, str(interaction.user)); bet = 50
        if eco["balance"] < bet: return await interaction.response.send_message(f"❌ Need at least {bet} coins.", ephemeral=True)
        syms = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰"]
        r = [random.choice(syms) for _ in range(3)]
        if r[0] == r[1] == r[2]:
            win = bet * (10 if r[0] == "💎" else 5); eco["balance"] += win - bet; eco["total_earned"] += win - bet; result = f"🎉 JACKPOT! Won **{win:,}**!"
        elif r[0] == r[1] or r[1] == r[2]:
            win = bet * 2; eco["balance"] += win - bet; eco["total_earned"] += win - bet; result = f"✅ Won **{win:,}**!"
        else:
            eco["balance"] -= bet; result = f"❌ Lost **{bet:,}**."
        await interaction.response.send_message(f"🎰 | {r[0]} | {r[1]} | {r[2]} |\n{result} Balance: **{eco['balance']:,}**", ephemeral=True)

    @discord.ui.button(label="💸 Pay Someone", style=discord.ButtonStyle.secondary, row=1)
    async def pay_btn(self, interaction, button):
        eco = get_economy(interaction.user.id, str(interaction.user))
        class PayModal(Modal, title="💸 Send Coins"):
            target = TextInput(label="Recipient Username (exact or partial)", max_length=50)
            amount = TextInput(label="Amount", max_length=10)
            async def on_submit(self2, i2):
                try: amt = int(self2.amount.value)
                except ValueError: return await i2.response.send_message("❌ Enter a number.", ephemeral=True)
                if amt <= 0 or eco["balance"] < amt:
                    return await i2.response.send_message(f"❌ Invalid. Balance: **{eco['balance']:,}**", ephemeral=True)
                member = discord.utils.find(lambda m: self2.target.value.lower() in m.name.lower() or self2.target.value.lower() in m.display_name.lower(), i2.guild.members)
                if not member: return await i2.response.send_message("❌ Member not found.", ephemeral=True)
                eco["balance"] -= amt
                payee = get_economy(member.id, str(member))
                payee["balance"] += amt; payee["total_earned"] += amt
                await i2.response.send_message(f"✅ Sent **{amt:,} coins** to **{member.display_name}**!", ephemeral=True)
        await interaction.response.send_modal(PayModal())

    @discord.ui.button(label="🏆 Rich List", style=discord.ButtonStyle.primary, row=1)
    async def rich_btn(self, interaction, button):
        if not economy_data: return await interaction.response.send_message("❌ No economy data yet.", ephemeral=True)
        top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:10]
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'{i+1}.'} **{d.get('name','?').split('#')[0]}** — {d['balance']:,} coins" for i, (_, d) in enumerate(top)]
        embed = discord.Embed(title="💰 Rich List", description="\n".join(lines), color=0xFAA61A)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="economy-menu", description="All economy actions in one place")
@app_commands.describe(member="Member to view (defaults to you)")
async def slash_economy_menu(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction): return
    target = member or interaction.user
    eco = get_economy(target.id, str(target))
    lv_data = xp_data.get(target.id, {"xp": 0})
    embed = discord.Embed(title=f"💰 Economy — {target.display_name}", color=0xFAA61A)
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="💰 Balance", value=f"**{eco['balance']:,} coins**", inline=True)
    embed.add_field(name="📈 Total Earned", value=f"{eco['total_earned']:,}", inline=True)
    embed.add_field(name="🏅 XP Level", value=f"Level {get_level(lv_data.get('xp', 0))}", inline=True)
    now = datetime.now()
    daily_ready = not eco["last_daily"] or (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() >= 86400
    work_ready = not eco["last_work"] or (now - datetime.fromisoformat(eco["last_work"])).total_seconds() >= 3600
    embed.add_field(name="📅 Daily", value="✅ Ready!" if daily_ready else "⏰ On cooldown", inline=True)
    embed.add_field(name="💼 Work", value="✅ Ready!" if work_ready else "⏰ On cooldown", inline=True)
    embed.set_footer(text="Use the buttons below")
    await interaction.response.send_message(embed=embed, view=EconomyMenuView(target), ephemeral=True)

# ============================================================
# MOD TOOLS MENU
# ============================================================
class ModToolsSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="📊 Server Overview", value="overview", emoji="📊"),
            discord.SelectOption(label="📜 View Mod Logs", value="modlogs", emoji="📜"),
            discord.SelectOption(label="⚠️ View All Warnings", value="warnings", emoji="⚠️"),
            discord.SelectOption(label="📝 View All Notes", value="notes", emoji="📝"),
            discord.SelectOption(label="🎫 View Open Tickets", value="tickets", emoji="🎫"),
            discord.SelectOption(label="🐛 View Bug Reports", value="bugs", emoji="🐛"),
            discord.SelectOption(label="🤖 View AutoMod Filter", value="automod", emoji="🤖"),
            discord.SelectOption(label="👑 View Premium Members", value="premium", emoji="👑"),
            discord.SelectOption(label="🎮 View Roblox Links", value="roblox", emoji="🎮"),
        ]
        super().__init__(placeholder="Select a data view…", options=options)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        val = self.values[0]
        embed = discord.Embed(color=0x5865F2)
        if val == "overview":
            g = interaction.guild
            bots = sum(1 for m in g.members if m.bot)
            online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
            embed.title = f"📊 {g.name} — Overview"
            embed.add_field(name="👥 Members", value=f"`{g.member_count - bots}` humans, `{bots}` bots")
            embed.add_field(name="🟢 Online", value=f"`{online}`")
            embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
            embed.add_field(name="⚠️ Warned Users", value=f"`{len(warnings_data)}`")
            embed.add_field(name="🎫 Open Tickets", value=f"`{sum(1 for t in ticket_data.values() if t.get('status')=='open')}`")
            embed.add_field(name="🐛 Bug Reports", value=f"`{len(bug_reports_data)}`")
            embed.add_field(name="👑 Premium Members", value=f"`{len(premium_data)}`")
            embed.add_field(name="🎮 Roblox Linked", value=f"`{len(roblox_links)}`")
            embed.add_field(name="📜 Mod Actions", value=f"`{len(mod_log_data)}`")
        elif val == "modlogs":
            embed.title = "📜 Recent Mod Logs"
            if mod_log_data:
                lines = [f"**{m['action']}** → {m['target']} *by {m['by'].split('#')[0]}* · {m['time']}" for m in mod_log_data[:10]]
                embed.description = "\n".join(lines)
            else: embed.description = "No mod actions yet."
        elif val == "warnings":
            embed.title = "⚠️ Members with Warnings"
            if warnings_data:
                lines = [f"`{uid}` — **{len(wlist)}** warn(s)" for uid, wlist in sorted(warnings_data.items(), key=lambda x: len(x[1]), reverse=True)[:15]]
                embed.description = "\n".join(lines)
            else: embed.description = "✅ No warnings."
        elif val == "notes":
            embed.title = "📝 Staff Notes Summary"
            total = sum(len(v) for v in notes_data.values())
            embed.description = f"{total} total notes across {len(notes_data)} members." if notes_data else "No notes yet."
        elif val == "tickets":
            embed.title = "🎫 Open Tickets"
            open_tickets = [(ch, t) for ch, t in ticket_data.items() if t.get("status") == "open"]
            if open_tickets:
                lines = [f"**{t['user_name'].split('#')[0]}** — {t['reason'][:50]} · {t['created']}" for _, t in open_tickets[:15]]
                embed.description = "\n".join(lines)
            else: embed.description = "✅ No open tickets."
        elif val == "bugs":
            embed.title = "🐛 Bug Reports"
            if bug_reports_data:
                lines = [f"**{b['id']}** [{b['severity']}] — {b['title'][:50]}" for b in bug_reports_data[:15]]
                embed.description = "\n".join(lines)
            else: embed.description = "No bug reports yet."
        elif val == "automod":
            embed.title = "🤖 AutoMod Word Filter"
            words = automod_data.get(str(interaction.guild.id), [])
            embed.description = " · ".join(f"`{w}`" for w in words) if words else "No words in filter."
        elif val == "premium":
            embed.title = "👑 Premium Members"
            if premium_data:
                lines = [
                    f"`{uid}` — **{d['tier'].title()}** · by {d.get('granted_by','?').split('#')[0]} · "
                    f"expires {d['expires'][:10] if d['expires'] != 'permanent' else 'Never'}"
                    for uid, d in list(premium_data.items())[:15]
                ]
                embed.description = "\n".join(lines)
                embed.set_footer(text=f"{len(premium_data)} premium members")
            else:
                embed.description = "No premium members yet."
        elif val == "roblox":
            embed.title = "🎮 Roblox Linked Accounts"
            if roblox_links:
                lines = [
                    f"**{v.get('display', v['username'])}** (@{v['username']}) → "
                    f"{v.get('discord_name','?').split('#')[0]} {'✅' if v.get('verified') else '🔗'}"
                    for v in list(roblox_links.values())[:15]
                ]
                embed.description = "\n".join(lines)
                embed.set_footer(
                    text=f"{len(roblox_links)} linked · "
                         f"{sum(1 for v in roblox_links.values() if v.get('verified'))} verified"
                )
            else:
                embed.description = "No Roblox accounts linked yet."

        await interaction.response.send_message(embed=embed, ephemeral=True)


class ModToolsView(View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ModToolsSelect())


@tree.command(name="mod-tools", description="Staff data viewer — warnings, notes, bugs, tickets & more [Staff]")
async def slash_mod_tools(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
    if not await require_group(interaction):
        return
    embed = discord.Embed(
        title="🛠️ Staff Data Viewer",
        description="Use the dropdown to quickly view any section of server data.",
        color=0x5865F2,
    )
    embed.add_field(
        name="Available Views",
        value="📊 Overview · 📜 Mod Logs · ⚠️ Warnings · 📝 Notes\n🎫 Tickets · 🐛 Bugs · 🤖 AutoMod · 👑 Premium · 🎮 Roblox",
        inline=False,
    )
    embed.set_footer(text="For full details visit the dashboard")
    await interaction.response.send_message(embed=embed, view=ModToolsView(), ephemeral=True)


# ============================================================
# SLASH — SERVER MENU
# ============================================================
class ServerMenuSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="🏠 Server Info",      value="server",   emoji="🏠"),
            discord.SelectOption(label="👥 Member Count",     value="members",  emoji="👥"),
            discord.SelectOption(label="🆕 Newest Members",   value="new",      emoji="🆕"),
            discord.SelectOption(label="👮 Staff List",       value="staff",    emoji="👮"),
            discord.SelectOption(label="📢 Channels List",    value="channels", emoji="📢"),
            discord.SelectOption(label="🎭 Roles List",       value="roles",    emoji="🎭"),
            discord.SelectOption(label="⚙️ Bot Config",       value="config",   emoji="⚙️"),
            discord.SelectOption(label="🏓 Bot Status",       value="ping",     emoji="🏓"),
        ]
        super().__init__(placeholder="Select a server view…", options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        g   = interaction.guild
        embed = discord.Embed(color=0x5865F2)

        if val == "server":
            embed.title = f"🏠 {g.name}"
            embed.add_field(name="Owner",    value=str(g.owner))
            embed.add_field(name="Members",  value=str(g.member_count))
            embed.add_field(name="Channels", value=str(len(g.channels)))
            embed.add_field(name="Roles",    value=str(len(g.roles)))
            embed.add_field(name="Created",  value=g.created_at.strftime("%d %b %Y"))
            embed.add_field(name="Boosts",   value=str(g.premium_subscription_count))
            if g.icon:
                embed.set_thumbnail(url=g.icon.url)

        elif val == "members":
            bots   = sum(1 for m in g.members if m.bot)
            online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
            embed.title = f"👥 {g.name} — Members"
            embed.add_field(name="Total",  value=f"`{g.member_count}`")
            embed.add_field(name="Humans", value=f"`{g.member_count - bots}`")
            embed.add_field(name="Bots",   value=f"`{bots}`")
            embed.add_field(name="Online", value=f"`{online}`")

        elif val == "new":
            recents = sorted(
                [m for m in g.members if not m.bot],
                key=lambda m: m.joined_at or datetime.min,
                reverse=True,
            )[:10]
            embed.title = "🆕 Newest Members"
            embed.description = "\n".join(
                f"`{i+1}.` **{m.display_name}** — {m.joined_at.strftime('%d %b %Y') if m.joined_at else '?'}"
                for i, m in enumerate(recents)
            )

        elif val == "staff":
            staff = []
            for m in g.members:
                if m.bot:
                    continue
                if m.guild_permissions.administrator:
                    staff.append((m, "🔴 Admin"))
                elif m.guild_permissions.ban_members:
                    staff.append((m, "🟠 Mod"))
                elif m.guild_permissions.kick_members:
                    staff.append((m, "🟡 Jr.Mod"))
                elif m.guild_permissions.manage_messages:
                    staff.append((m, "🟢 Helper"))
            embed.title = "👮 Staff List"
            embed.description = "\n".join(f"{r} **{m.display_name}**" for m, r in staff[:25]) or "No staff found."

        elif val == "channels":
            text = [c for c in g.channels if isinstance(c, discord.TextChannel)][:20]
            embed.title = "📢 Text Channels"
            embed.description = " · ".join(c.mention for c in text)

        elif val == "roles":
            roles = [r for r in g.roles if r.name != "@everyone"][-20:]
            embed.title = "🎭 Roles"
            embed.description = " · ".join(r.mention for r in reversed(roles))

        elif val == "config":
            if not interaction.user.guild_permissions.administrator:
                return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            config = load_config().get(str(g.id), {})
            embed.title = "⚙️ Bot Config"
            if config:
                for key, value in list(config.items())[:15]:
                    obj = g.get_role(value) or g.get_channel(value)
                    embed.add_field(
                        name=key.replace("_", " ").title(),
                        value=obj.mention if obj else str(value),
                        inline=True,
                    )
            else:
                embed.description = "No config yet. Use `/config` to set up."

        elif val == "ping":
            ws = round(bot.latency * 1000)
            embed.title = "🏓 Bot Status"
            embed.add_field(name="WebSocket", value=f"`{ws}ms`")
            embed.add_field(name="Uptime",    value=f"`{uptime_str() or '—'}`")
            embed.add_field(name="Servers",   value=f"`{len(bot.guilds)}`")
            embed.add_field(name="XP Users",  value=f"`{len(xp_data)}`")
            embed.add_field(name="Eco Users", value=f"`{len(economy_data)}`")

        await interaction.response.send_message(embed=embed, ephemeral=True)


class ServerMenuView(View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(ServerMenuSelect())


@tree.command(name="server-menu", description="Server info, member stats, roles, config & more")
async def slash_server_menu(interaction: discord.Interaction):
    if not await require_group(interaction):
        return
    embed = discord.Embed(
        title=f"🏠 {interaction.guild.name}",
        description="Browse server information using the dropdown below.",
        color=0x5865F2,
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.add_field(name="Members",  value=str(interaction.guild.member_count), inline=True)
    embed.add_field(name="Channels", value=str(len(interaction.guild.channels)), inline=True)
    embed.add_field(name="Roles",    value=str(len(interaction.guild.roles)),    inline=True)
    await interaction.response.send_message(embed=embed, view=ServerMenuView(), ephemeral=True)


# ============================================================
# SLASH — MEMBER INFO
# ============================================================
class MemberInfoView(View):
    def __init__(self, member: discord.Member):
        super().__init__(timeout=120)
        self.member = member

    @discord.ui.button(label="📊 Full Stats", style=discord.ButtonStyle.primary, row=0)
    async def stats_btn(self, interaction, button):
        m  = self.member
        xd = xp_data.get(m.id, {})
        ed = economy_data.get(m.id, {})
        rb = roblox_links.get(m.id)
        embed = discord.Embed(title=f"📊 {m.display_name} — Full Stats", color=m.color)
        embed.set_thumbnail(url=m.display_avatar.url)
        embed.add_field(name="🏅 Level",       value=str(get_level(xd.get("xp", 0))))
        embed.add_field(name="✨ XP",          value=str(xd.get("xp", 0)))
        embed.add_field(name="💬 Messages",    value=str(xd.get("messages", 0)))
        embed.add_field(name="💰 Balance",     value=f"{ed.get('balance', 0):,} coins")
        embed.add_field(name="📈 Total Earned",value=f"{ed.get('total_earned', 0):,}")
        embed.add_field(name="⚠️ Warnings",    value=str(len(warnings_data.get(m.id, []))))
        embed.add_field(name="📝 Notes",       value=str(len(notes_data.get(str(m.id), []))))
        embed.add_field(
            name="🎮 Roblox",
            value=f"@{rb['username']} {'✅' if rb.get('verified') else '🔗'}" if rb else "Not linked",
        )
        embed.add_field(
            name="👑 Premium",
            value=premium_data[m.id]["tier"].title() if m.id in premium_data else "None",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🛠️ Mod Menu", style=discord.ButtonStyle.danger, row=0)
    async def mod_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        await interaction.response.send_message(
            f"Opening mod panel for **{self.member.display_name}**…",
            view=ModMenuView(self.member),
            ephemeral=True,
        )

    @discord.ui.button(label="🎮 Roblox Profile", style=discord.ButtonStyle.blurple, row=0)
    async def roblox_btn(self, interaction, button):
        rb = roblox_links.get(self.member.id)
        if not rb:
            return await interaction.response.send_message(
                f"❌ **{self.member.display_name}** hasn't linked Roblox.", ephemeral=True
            )
        embed = discord.Embed(
            title=f"🎮 {rb['display']} (@{rb['username']})",
            url=f"https://www.roblox.com/users/{rb['roblox_id']}/profile",
            color=0x00B2FF,
        )
        embed.add_field(name="ID",       value=str(rb["roblox_id"]))
        embed.add_field(name="Verified", value="✅ Yes" if rb.get("verified") else "🔗 Manual")
        embed.add_field(name="Linked",   value=rb.get("linked_at", "—"))
        if rb.get("thumb"):
            embed.set_thumbnail(url=rb["thumb"])
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="⚠️ Warning History", style=discord.ButtonStyle.secondary, row=1)
    async def warn_btn(self, interaction, button):
        warns = warnings_data.get(self.member.id, [])
        embed = discord.Embed(title=f"⚠️ Warnings — {self.member.display_name}", color=0xFAA61A)
        embed.description = (
            "\n".join(
                f"**#{i+1}** {w['reason']} *— by {w['by'].split('#')[0]}, {w['time'][:10]}*"
                for i, w in enumerate(warns)
            )
            if warns
            else "✅ No warnings."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📝 Staff Notes", style=discord.ButtonStyle.secondary, row=1)
    async def notes_btn(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        nlist = notes_data.get(str(self.member.id), [])
        embed = discord.Embed(title=f"📝 Notes — {self.member.display_name}", color=0x5865F2)
        embed.description = (
            "\n".join(
                f"**#{i+1}** {n['note']} *— {n['by'].split('#')[0]}, {n['time']}*"
                for i, n in enumerate(nlist)
            )
            if nlist
            else "No notes."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🗓️ Account Details", style=discord.ButtonStyle.secondary, row=1)
    async def account_btn(self, interaction, button):
        m = self.member
        embed = discord.Embed(title=f"🗓️ Account — {m}", color=m.color)
        embed.set_thumbnail(url=m.display_avatar.url)
        embed.add_field(name="Discord ID",      value=f"`{m.id}`")
        embed.add_field(name="Joined Server",   value=m.joined_at.strftime("%d %b %Y %H:%M") if m.joined_at else "?")
        embed.add_field(name="Account Created", value=m.created_at.strftime("%d %b %Y %H:%M"))
        embed.add_field(name="Top Role",        value=m.top_role.mention)
        embed.add_field(name="Roles",           value=str(len(m.roles) - 1))
        embed.add_field(name="Timed Out?",      value="⏰ Yes" if m.is_timed_out() else "✅ No")
        await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="member-info", description="Full member info with stats, mod tools, Roblox & history")
@app_commands.describe(member="Member to inspect (defaults to you)")
async def slash_member_info(interaction: discord.Interaction, member: discord.Member = None):
    if not await require_group(interaction):
        return
    m  = member or interaction.user
    xd = xp_data.get(m.id, {})
    ed = economy_data.get(m.id, {})
    rb = roblox_links.get(m.id)
    embed = discord.Embed(
        title=f"👤 {m.display_name}",
        description=f"{m.mention} · `{m.id}`",
        color=m.color or 0x5865F2,
    )
    embed.set_thumbnail(url=m.display_avatar.url)
    embed.add_field(name="🏅 Level",  value=f"`{get_level(xd.get('xp', 0))}`",    inline=True)
    embed.add_field(name="💰 Coins",  value=f"`{ed.get('balance', 0):,}`",         inline=True)
    embed.add_field(name="⚠️ Warns",  value=f"`{len(warnings_data.get(m.id, []))}`", inline=True)
    embed.add_field(
        name="🎮 Roblox",
        value=f"@{rb['username']} {'✅' if rb.get('verified') else '🔗'}" if rb else "Not linked",
        inline=True,
    )
    embed.add_field(
        name="👑 Premium",
        value=premium_data[m.id]["tier"].title() if m.id in premium_data else "None",
        inline=True,
    )
    embed.add_field(
        name="📅 Joined",
        value=m.joined_at.strftime("%d %b %Y") if m.joined_at else "?",
        inline=True,
    )
    embed.set_footer(text="Use the buttons below to explore more")
    await interaction.response.send_message(embed=embed, view=MemberInfoView(m), ephemeral=True)


# ============================================================
# SLASH — AUTOMOD PANEL
# ============================================================
class AutoModConfigView(View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.button(label="📊 View Settings", style=discord.ButtonStyle.primary, row=0)
    async def view_settings(self, interaction, button):
        words = automod_data.get(str(self.guild_id), [])
        embed = discord.Embed(title="🤖 AutoMod Settings", color=0x6C63FF)
        embed.add_field(name="🔗 Link Blocking",   value="✅ Active",                                          inline=True)
        embed.add_field(name="💌 Invite Blocking", value="✅ Active",                                          inline=True)
        embed.add_field(name="⚡ Spam",            value=f"✅ {automod_spam_threshold}msg/{automod_spam_window}s", inline=True)
        embed.add_field(name="🔁 Duplicate",       value=f"✅ ×{automod_duplicate_count} same msg",            inline=True)
        embed.add_field(name="📢 Mass Mention",    value=f"✅ >{automod_mention_limit} pings",                 inline=True)
        embed.add_field(name="😂 Emoji Spam",      value=f"✅ >{automod_emoji_limit} emojis",                  inline=True)
        embed.add_field(name="🔠 Caps Spam",       value=f"✅ >{int(automod_caps_threshold*100)}% caps",       inline=True)
        embed.add_field(name="📜 Wall of Text",    value=f"✅ >{automod_newline_limit} newlines",              inline=True)
        embed.add_field(name="🚫 Banned Words",    value=f"**{len(words)}** words" if words else "None",       inline=True)
        embed.add_field(
            name="📈 Escalation",
            value=(
                "**×1** Alert + DM\n"
                "**×2** Final warning + DM\n"
                "**×3** Mute 10min + Log\n"
                "**Post-mute ×3** Kick\n"
                "**Rejoin + ×3** Permanent Ban"
            ),
            inline=False,
        )
        embed.set_footer(text="Admins and Manage Messages are exempt")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📋 Strike Leaderboard", style=discord.ButtonStyle.secondary, row=0)
    async def strike_lb(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        if not automod_infractions:
            return await interaction.response.send_message("✅ No strikes recorded yet.", ephemeral=True)
        top = sorted(automod_infractions.items(), key=lambda x: x[1]["warns"], reverse=True)[:15]
        embed = discord.Embed(title="📋 AutoMod Strike Leaderboard", color=0xFF9F43)
        lines = []
        for uid, inf in top:
            m = interaction.guild.get_member(uid)
            name = m.display_name if m else f"User {uid}"
            lines.append(
                f"**{name}** — ⚠️ {inf['warns']} · 🦶 {inf['kicks']} kicks {'🔇' if inf['muted'] else ''}"
            )
        embed.description = "\n".join(lines)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🧹 Clear Member Strikes", style=discord.ButtonStyle.secondary, row=1)
    async def clear_member(self, interaction, button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        class ClearModal(Modal, title="🧹 Clear AutoMod Strikes"):
            uid_input = TextInput(
                label="Member ID or Username",
                placeholder="e.g. 123456789 or PlayerName",
                max_length=50,
            )

            async def on_submit(self2, i2):
                val = self2.uid_input.value.strip()
                if val.isdigit():
                    tid = int(val)
                    target = i2.guild.get_member(tid)
                    if not target:
                        automod_infractions.pop(tid, None)
                        return await i2.response.send_message(
                            f"✅ Cleared strikes for ID `{tid}`.", ephemeral=True
                        )
                else:
                    target = discord.utils.find(
                        lambda m: val.lower() in m.name.lower() or val.lower() in m.display_name.lower(),
                        i2.guild.members,
                    )
                if not target:
                    return await i2.response.send_message("❌ Member not found.", ephemeral=True)
                automod_infractions.pop(target.id, None)
                automod_duplicate_times.pop(target.id, None)
                if target.id in automod_message_times:
                    automod_message_times[target.id].clear()
                await i2.response.send_message(
                    f"✅ Strikes cleared for **{target.display_name}**.", ephemeral=True
                )

        await interaction.response.send_modal(ClearModal())

    @discord.ui.button(label="🔄 Reset ALL Strikes", style=discord.ButtonStyle.danger, row=1)
    async def reset_all(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        automod_infractions.clear()
        automod_duplicate_times.clear()
        automod_message_times.clear()
        await interaction.response.send_message("🔄 All AutoMod strikes cleared.", ephemeral=True)


@tree.command(name="automod", description="AutoMod live settings, strike board & management [Staff]")
async def slash_automod_panel(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
    if not await require_group(interaction):
        return
    words        = automod_data.get(str(interaction.guild.id), [])
    muted_count  = sum(1 for inf in automod_infractions.values() if inf["muted"])
    kicked_count = sum(inf["kicks"] for inf in automod_infractions.values())
    embed = discord.Embed(
        title="🤖 Advanced AutoMod",
        description="YBS auto-moderation is **fully active** watching all messages in real time.",
        color=0x6C63FF,
    )
    embed.add_field(
        name="⚡ Detects",
        value=(
            "Invite links · External URLs\n"
            "Message spam · Duplicate spam\n"
            "Mass mentions · Emoji spam\n"
            "Caps spam · Wall of text\n"
            "Custom word filter"
        ),
        inline=True,
    )
    embed.add_field(
        name="📈 Escalation",
        value=(
            "**×1** Alert + DM\n"
            "**×2** Final warning\n"
            "**×3** Mute 10min\n"
            "**Post-mute ×3** Kick\n"
            "**Rejoin + ×3** Ban"
        ),
        inline=True,
    )
    embed.add_field(
        name="📊 Live Stats",
        value=(
            f"⚠️ **{len(automod_infractions)}** tracked\n"
            f"🔇 **{muted_count}** muted\n"
            f"👢 **{kicked_count}** auto-kicks\n"
            f"🚫 **{len(words)}** banned words"
        ),
        inline=True,
    )
    embed.set_footer(text="Staff with Manage Messages are exempt from all AutoMod checks")
    await interaction.response.send_message(embed=embed, view=AutoModConfigView(interaction.guild.id), ephemeral=True)


# ============================================================
# DASHBOARD (Flask)
# ============================================================
flask_app = Flask(__name__)
flask_app.jinja_env.globals.update(enumerate=enumerate)

# ── Config key allowlists ──
CHANNEL_CONFIG_KEYS_DASH = [
    "welcome_channel", "apply_channel", "applications_channel",
    "logs_channel", "general_channel", "announcements_channel",
    "giveaway_channel", "levelup_channel", "rank_channel",
    "verify_channel", "mod_channel",
]
ROLE_CONFIG_KEYS_DASH = [
    "admin_role", "member_role", "muted_role", "builder_role",
    "scripter_role", "modeller_role", "ui_role", "verified_role",
    "level5_role", "level10_role", "level25_role", "pending_role",
]
BOT_CONFIG_KEYS_DASH = [
    "welcome_message", "automod_enabled", "automod_spam",
    "automod_caps", "automod_links", "automod_invites",
    "automod_duplicate", "automod_mentions", "automod_emoji",
    "levelup_enabled", "economy_enabled",
]
ALL_ALLOWED_KEYS = set(CHANNEL_CONFIG_KEYS_DASH + ROLE_CONFIG_KEYS_DASH + BOT_CONFIG_KEYS_DASH)


def build_xp_leaderboard():
    result = []
    for uid, d in sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True):
        lv         = get_level(d["xp"])
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        pct        = int((d["xp"] - prev_lv_xp) / max(next_lv_xp - prev_lv_xp, 1) * 100)
        result.append({
            "uid": uid, "name": d.get("name", "Unknown"),
            "xp": d["xp"], "level": lv,
            "messages": d.get("messages", 0), "pct": pct,
        })
    return result


def build_eco_leaderboard():
    return [
        {"uid": uid, "name": d.get("name", "Unknown"),
         "balance": d["balance"], "total_earned": d["total_earned"]}
        for uid, d in sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)
    ]


def common():
    role_counts = {}
    for app in applications_data.values():
        role = app.get("role", "Other")
        role_counts[role] = role_counts.get(role, 0) + 1

    mod_action_counts = {}
    for entry in mod_log_data:
        action = entry.get("action", "Other")
        mod_action_counts[action] = mod_action_counts.get(action, 0) + 1

    recent_warns = []
    for uid, wlist in warnings_data.items():
        if wlist:
            recent_warns.append({
                "user_id": uid,
                "count": len(wlist),
                "last_reason": wlist[-1].get("reason", "—"),
            })
    recent_warns.sort(key=lambda x: x["count"], reverse=True)

    all_member_ids = set(list(xp_data.keys()) + list(economy_data.keys()))
    members_list   = []
    for uid in all_member_ids:
        xd         = xp_data.get(uid, {})
        ed         = economy_data.get(uid, {})
        xp_val     = xd.get("xp", 0)
        lv         = get_level(xp_val)
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        pct        = int((xp_val - prev_lv_xp) / max(next_lv_xp - prev_lv_xp, 1) * 100)
        members_list.append({
            "uid":      uid,
            "name":     xd.get("name", ed.get("name", str(uid))),
            "xp":       xp_val, "level": lv, "pct": pct,
            "messages": xd.get("messages", 0),
            "balance":  ed.get("balance", 0),
            "warnings": len(warnings_data.get(uid, [])),
        })
    members_list.sort(key=lambda x: x["xp"], reverse=True)

    eco_lb            = build_eco_leaderboard()
    total_coins       = sum(d["balance"]      for d in economy_data.values())
    total_earned_ever = sum(d["total_earned"] for d in economy_data.values())
    richest           = max((d["balance"] for d in economy_data.values()), default=0)

    roblox_accounts = [
        {
            "discord_name": v.get("discord_name", "Unknown"),
            "username":     v["username"],
            "display":      v.get("display", v["username"]),
            "roblox_id":    v["roblox_id"],
            "thumb":        v.get("thumb"),
            "linked_at":    v.get("linked_at", "—"),
            "verified":     v.get("verified", False),
        }
        for v in roblox_links.values()
    ]

    return dict(
        bot_online=bot.is_ready(),
        bot_name=str(bot.user) if bot.user else "YBS Bot",
        uptime=uptime_str(),
        activity=activity_log,
        app_count=len(applications_data),
        warn_count=len(warnings_data),
        notes_count=sum(len(v) for v in notes_data.values()),
        giveaway_count=len(giveaway_data),
        ticket_count=sum(1 for t in ticket_data.values() if t["status"] == "open"),
        xp_count=len(xp_data),
        eco_count=len(economy_data),
        mod_log_count=len(mod_log_data),
        roblox_count=len(roblox_links),
        bug_count=len(bug_reports_data),
        premium_count=len(premium_data),
        applications=applications_data,
        warnings={str(k): v for k, v in warnings_data.items()},
        all_notes=notes_data,
        giveaways=giveaway_data,
        tickets=ticket_data,
        mod_logs=mod_log_data,
        members=members_list,
        voice_events=voice_log[:100],
        automod_guilds=automod_data,
        total_words=sum(len(v) for v in automod_data.values()),
        bug_reports=bug_reports_data,
        premium_members={str(k): v for k, v in premium_data.items()},
        roblox_accounts=roblox_accounts,
        xp_lb=build_xp_leaderboard()[:25],
        eco_lb=eco_lb[:25],
        top_xp=build_xp_leaderboard()[:5],
        total_coins=total_coins,
        total_earned_ever=total_earned_ever,
        richest=richest,
        role_counts=role_counts,
        mod_action_counts=mod_action_counts,
        recent_warns=recent_warns[:5],
        recent_apps=list(applications_data.values())[-5:],
        # ── new: pass secret so dashboard config JS can call the API ──
        dashboard_secret=os.environ.get("DASHBOARD_SECRET", "changeme"),
    )


# ── Page routes ──
@flask_app.route("/")
def dashboard_home():
    return render_template("dashboard.html", page="home", **common())

@flask_app.route("/activity")
def dashboard_activity():
    return render_template("dashboard.html", page="activity", **common())

@flask_app.route("/applications")
def dashboard_applications():
    return render_template("dashboard.html", page="applications", **common())

@flask_app.route("/warnings")
def dashboard_warnings():
    return render_template("dashboard.html", page="warnings", **common())

@flask_app.route("/notes")
def dashboard_notes():
    return render_template("dashboard.html", page="notes", **common())

@flask_app.route("/members")
def dashboard_members():
    return render_template("dashboard.html", page="members", **common())

@flask_app.route("/leaderboard")
def dashboard_leaderboard():
    return render_template("dashboard.html", page="leaderboard", **common())

@flask_app.route("/modlogs")
def dashboard_modlogs():
    return render_template("dashboard.html", page="modlogs", **common())

@flask_app.route("/automod")
def dashboard_automod():
    return render_template("dashboard.html", page="automod", **common())

@flask_app.route("/tickets")
def dashboard_tickets():
    return render_template("dashboard.html", page="tickets", **common())

@flask_app.route("/bugs")
def dashboard_bugs():
    return render_template("dashboard.html", page="bugs", **common())

@flask_app.route("/giveaways")
def dashboard_giveaways():
    return render_template("dashboard.html", page="giveaways", **common())

@flask_app.route("/voice")
def dashboard_voice():
    return render_template("dashboard.html", page="voice", **common())

@flask_app.route("/economy")
def dashboard_economy():
    return render_template("dashboard.html", page="economy", **common())

@flask_app.route("/analytics")
def dashboard_analytics():
    return render_template("dashboard.html", page="analytics", **common())

@flask_app.route("/roblox")
def dashboard_roblox():
    return render_template("dashboard.html", page="roblox", **common())

@flask_app.route("/premium")
def dashboard_premium():
    return render_template("dashboard.html", page="premium", **common())

# ── NEW: Config page route ──
@flask_app.route("/config")
def dashboard_config():
    return render_template("dashboard.html", page="config", **common())


# ── Existing API routes ──
@flask_app.route("/api/stats")
def api_stats():
    return jsonify({
        "members":         sum(g.member_count for g in bot.guilds),
        "servers":         len(bot.guilds),
        "uptime":          uptime_str(),
        "ping":            round(bot.latency * 1000),
        "xp_users":        len(xp_data),
        "economy_users":   len(economy_data),
        "roblox_linked":   len(roblox_links),
        "verified":        sum(1 for v in roblox_links.values() if v.get("verified")),
        "applications":    len(applications_data),
        "mod_actions":     len(mod_log_data),
        "bug_reports":     len(bug_reports_data),
        "premium_members": len(premium_data),
    })

@flask_app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    global bot_shutdown_flag
    data   = request.json or {}
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if data.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    bot_shutdown_flag = True
    return jsonify({"status": "shutdown initiated"})


# ── NEW: Config API routes ──

@flask_app.route("/api/config", methods=["GET"])
def api_config_get():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if request.args.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    guild_id = request.args.get("guild_id")
    cfg = load_config()
    if guild_id:
        return jsonify(cfg.get(str(guild_id), {}))
    return jsonify(cfg)


@flask_app.route("/api/config/save", methods=["POST"])
def api_config_save():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    data   = request.json or {}
    if data.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403

    guild_id = str(data.get("guild_id", ""))
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400

    incoming = data.get("config", {})
    if not isinstance(incoming, dict):
        return jsonify({"error": "config must be an object"}), 400

    cfg = load_config()
    if guild_id not in cfg:
        cfg[guild_id] = {}

    updated = []
    for key, value in incoming.items():
        if key not in ALL_ALLOWED_KEYS:
            continue
        if value is None or value == "" or value == 0:
            cfg[guild_id].pop(key, None)
        else:
            cfg[guild_id][key] = value
        updated.append(key)

    save_config(cfg)
    add_activity("⚙️", "Dashboard config updated", f"{len(updated)} key(s) saved")
    return jsonify({"status": "ok", "updated": updated})


@flask_app.route("/api/config/reset", methods=["POST"])
def api_config_reset():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    data   = request.json or {}
    if data.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    guild_id = str(data.get("guild_id", ""))
    if not guild_id:
        return jsonify({"error": "guild_id required"}), 400
    cfg = load_config()
    cfg.pop(guild_id, None)
    save_config(cfg)
    return jsonify({"status": "ok"})


@flask_app.route("/api/guilds")
def api_guilds():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if request.args.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    guilds = [
        {
            "id":           str(g.id),
            "name":         g.name,
            "member_count": g.member_count,
            "icon":         str(g.icon.url) if g.icon else None,
        }
        for g in bot.guilds
    ]
    return jsonify(guilds)


@flask_app.route("/api/guild/channels")
def api_guild_channels():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if request.args.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    try:
        guild_id = int(request.args.get("guild_id", 0))
    except ValueError:
        return jsonify({"error": "Invalid guild_id"}), 400
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    channels = [
        {"id": str(c.id), "name": c.name}
        for c in sorted(guild.text_channels, key=lambda c: c.position)
    ]
    return jsonify(channels)


@flask_app.route("/api/guild/roles")
def api_guild_roles():
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if request.args.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    try:
        guild_id = int(request.args.get("guild_id", 0))
    except ValueError:
        return jsonify({"error": "Invalid guild_id"}), 400
    guild = bot.get_guild(guild_id)
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    roles = [
        {"id": str(r.id), "name": r.name}
        for r in reversed(guild.roles)
        if r.name != "@everyone"
    ]
    return jsonify(roles)


# ============================================================
# RUN BOTH
# ============================================================
from threading import Thread

def run_flask():
    flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

Thread(target=run_flask, daemon=True).start()
bot.run(TOKEN)
