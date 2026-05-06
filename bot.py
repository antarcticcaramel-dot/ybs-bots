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

# ============================================================
# TOKEN
# ============================================================
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

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
roblox_links = {}       # user_id -> {username, roblox_id, display, thumb, linked_at, discord_name}
xp_data = {}            # user_id -> {xp, level, messages, name}
xp_cooldowns = {}
economy_data = {}       # user_id -> {balance, last_daily, last_work, total_earned, name}
snipe_data = {}
ticket_data = {}
automod_data = {}
mod_log_data = []
voice_log = []
afk_data = {}
ban_log_data = []
verification_data = {}  # user_id -> {code, roblox_username, expires}
bot_shutdown_flag = False

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

        # Generate a verification code
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

        # Check if code is in bio
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.get(f"https://users.roblox.com/v1/users/{vdata['roblox_id']}")
                profile = await r.json()
                bio = profile.get("description", "")
        except Exception:
            return await interaction.followup.send("❌ Could not contact Roblox API. Try again.", ephemeral=True)

        if vdata["code"] not in bio:
            return await interaction.followup.send(f"❌ Code `{vdata['code']}` not found in your Roblox bio yet!\n\nMake sure you **saved** the profile. Wait a minute and try again.", ephemeral=True)

        # Verified!
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

        # Give verified role
        verified_role_id = get(interaction.guild.id, "verified_role")
        if verified_role_id:
            role = interaction.guild.get_role(verified_role_id)
            if role:
                try:
                    await interaction.user.add_roles(role)
                except Exception:
                    pass

        # Update nickname to Roblox display name
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
        # Check if already verified
        if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
            linked = roblox_links[interaction.user.id]
            embed = discord.Embed(title="✅ Already Verified!", description=f"You're already linked to **{linked['display']}** (@{linked['username']})\n\nUse `/roblox-unlink` to unlink.", color=0x3BA55C)
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        await interaction.response.send_modal(VerifyModal())

# ============================================================
# APPLICATION SYSTEM
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
            "status": "pending"
        }
        applications_data[interaction.user.id] = app
        if channel:
            embed = discord.Embed(title=f"📋 New Application — {real_name}", color=0x5865F2, timestamp=datetime.now())
            embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="🎮 Roblox", value=self.roblox_name.value, inline=True)
            embed.add_field(name="👤 Name", value=real_name, inline=True)
            embed.add_field(name="🎂 Age", value=age, inline=True)
            embed.add_field(name="🔨 Role", value=self.role_value, inline=True)
            # Check if they have Roblox verified
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
        embed.timestamp = discord.utils.utcnow()
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
        embed.timestamp = discord.utils.utcnow()
        await log.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    # AutoMod
    guild_words = automod_data.get(str(message.guild.id), [])
    if guild_words and any(w.lower() in message.content.lower() for w in guild_words):
        try:
            await message.delete()
            await message.channel.send(f"⚠️ {message.author.mention}, that message contained a banned word.", delete_after=5)
            if message.author.id not in warnings_data:
                warnings_data[message.author.id] = []
            warnings_data[message.author.id].append({"reason": "AutoMod: banned word", "by": "AutoMod", "time": datetime.now().isoformat()})
            add_activity("🤖", f"AutoMod removed message from {message.author.display_name}")
        except Exception:
            pass
        await bot.process_commands(message)
        return
    # AFK
    if message.author.id in afk_data:
        afk_data.pop(message.author.id)
        await message.channel.send(f"✅ Welcome back, {message.author.mention}! AFK removed.", delete_after=5)
    for mentioned in message.mentions:
        if mentioned.id in afk_data:
            info = afk_data[mentioned.id]
            await message.channel.send(f"💤 **{mentioned.display_name}** is AFK: {info['reason']} *(since {info['time']})*", delete_after=10)
    # XP
    now = datetime.now()
    last = xp_cooldowns.get(message.author.id)
    if not last or (now - last).total_seconds() >= 60:
        xp_cooldowns[message.author.id] = now
        if message.author.id not in xp_data:
            xp_data[message.author.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(message.author)}
        earned = random.randint(5, 15)
        xp_data[message.author.id]["xp"] += earned
        xp_data[message.author.id]["messages"] = xp_data[message.author.id].get("messages", 0) + 1
        xp_data[message.author.id]["name"] = str(message.author)
        cur_xp = xp_data[message.author.id]["xp"]
        old_lv = xp_data[message.author.id]["level"]
        new_lv = get_level(cur_xp)
        if new_lv > old_lv:
            xp_data[message.author.id]["level"] = new_lv
            add_activity("⬆️", f"{message.author.display_name} reached level {new_lv}!", message.guild.name)
            # Send to level-up channel if configured
            lv_ch_id = get(message.guild.id, "levelup_channel")
            lv_ch = bot.get_channel(lv_ch_id) if lv_ch_id else message.channel
            embed = discord.Embed(title="🎉 Level Up!", description=f"{message.author.mention} reached **Level {new_lv}**! 🚀", color=0xFAA61A)
            try:
                await lv_ch.send(embed=embed, delete_after=30)
            except:
                pass
            # Give level roles
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
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):
    mod_log_id = get(member.guild.id, "mod_channel") or get(member.guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if before.channel is None and after.channel is not None:
        voice_log.insert(0, {"action": "joined", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        add_activity("🎙️", f"{member.display_name} joined voice #{after.channel.name}")
        if log:
            await log.send(f"🎙️ **{member}** joined **{after.channel.name}**")
    elif before.channel is not None and after.channel is None:
        voice_log.insert(0, {"action": "left", "member": str(member), "channel": before.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log:
            await log.send(f"🔇 **{member}** left **{before.channel.name}**")
    elif before.channel != after.channel:
        voice_log.insert(0, {"action": "moved", "member": str(member), "channel": after.channel.name, "time": datetime.now().strftime("%H:%M · %d %b")})
        if log:
            await log.send(f"↔️ **{member}** moved **{before.channel.name}** → **{after.channel.name}**")
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
        embed.timestamp = discord.utils.utcnow()
        await log.send(embed=embed)

@bot.event
async def on_member_unban(guild, user):
    mod_log_id = get(guild.id, "mod_channel") or get(guild.id, "logs_channel")
    log = bot.get_channel(mod_log_id) if mod_log_id else None
    if log:
        embed = discord.Embed(title="✅ Member Unbanned", color=0x3BA55C)
        embed.add_field(name="User", value=f"{user} (`{user.id}`)")
        embed.timestamp = discord.utils.utcnow()
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
# PREFIX COMMANDS — MODERATION
# ============================================================
@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member}** kicked. Reason: {reason}")
    add_mod_log("Kick", str(member), str(ctx.author), reason, "#faa61a")
    add_activity("👢", f"{member.display_name} was kicked", reason)

@bot.command()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member}** banned. Reason: {reason}")
    add_mod_log("Ban", str(member), str(ctx.author), reason, "#ed4245")
    add_activity("🔨", f"{member.display_name} was banned", reason)

@bot.command()
async def unban(ctx, *, name):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == name:
            await ctx.guild.unban(entry.user)
            return await ctx.send(f"✅ **{entry.user}** unbanned.")
    await ctx.send("❌ User not found.")

@bot.command()
async def mute(ctx, member: discord.Member, duration: int = 10, *, reason="No reason"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    try:
        await member.timeout(timedelta(minutes=duration), reason=reason)
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
        await ctx.send(f"🚨 {member.mention} has reached 3 warnings! Consider taking further action.")

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
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
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
# PREFIX COMMANDS — INFO
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
# ECONOMY PREFIX
# ============================================================
@bot.command(aliases=["bal"])
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    eco = get_economy(member.id, str(member))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    await ctx.send(embed=embed)

@bot.command()
async def daily(ctx):
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
# FUN PREFIX
# ============================================================
EIGHTBALL = ["It is certain. ✅","It is decidedly so. ✅","Without a doubt. ✅","Yes, definitely. ✅","Most likely. 🟡","Outlook good. 🟡","Reply hazy, try again. ❓","Ask again later. ❓","Don't count on it. ❌","My reply is no. ❌","Very doubtful. ❌"]

@bot.command(name="8ball")
async def eightball(ctx, *, question: str):
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def coinflip(ctx):
    await ctx.send(f"🪙 **{random.choice(['Heads', 'Tails'])}!**")

@bot.command()
async def dice(ctx):
    await ctx.send(f"🎲 You rolled a **{random.randint(1, 6)}**!")

@bot.command()
async def snipe(ctx):
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
    embed = discord.Embed(title="📢 Announcement", description=message, color=0x5865F2, timestamp=datetime.now())
    embed.set_footer(text=f"Posted by {ctx.author}")
    await channel.send("@everyone", embed=embed)

@bot.command()
async def suggest(ctx, *, suggestion):
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
    # Use giveaway channel if set
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
    embed.set_footer(text="Prefix: ! · Use /help for full slash command menu")
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
        await interaction.response.send_message(f"⚠️ **{self.member}** warned. Total: **{len(warnings_data[uid])}** warning(s).", ephemeral=True)

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
            await self.member.timeout(discord.utils.utcnow() + timedelta(minutes=mins), reason=self.reason.value or "No reason")
            add_mod_log("Timeout", str(self.member), str(interaction.user), f"{mins}m — {self.reason.value or 'No reason'}", "#faa61a")
            await interaction.response.send_message(f"⏰ **{self.member}** timed out for **{mins} minutes**.", ephemeral=True)
        except: await interaction.response.send_message("❌ Couldn't timeout.", ephemeral=True)

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
            await self.member.timeout(discord.utils.utcnow() + timedelta(hours=1), reason=f"Muted 1h by {interaction.user}")
            add_mod_log("Mute", str(self.member), str(interaction.user), "1 hour", "#faa61a")
            await interaction.response.send_message(f"🔇 **{self.member}** muted for 1 hour.", ephemeral=True)
        except: await interaction.response.send_message("❌ Failed.", ephemeral=True)

    @discord.ui.button(label="🔇 Mute 24h", style=discord.ButtonStyle.secondary, row=1)
    async def mute24h_btn(self, interaction, button):
        if not interaction.user.guild_permissions.moderate_members: return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            await self.member.timeout(discord.utils.utcnow() + timedelta(hours=24), reason=f"Muted 24h by {interaction.user}")
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
            discord.SelectOption(label="🏅 XP & Leveling", value="xp", description="Rank, leaderboard, XP commands"),
            discord.SelectOption(label="💰 Economy", value="economy", description="Coins, daily, work, gamble"),
            discord.SelectOption(label="🎮 Fun & Games", value="fun", description="8ball, trivia, rps, ship…"),
            discord.SelectOption(label="🛠️ Utility", value="utility", description="Calc, snipe, color, timestamp…"),
            discord.SelectOption(label="🔨 Moderation", value="mod", description="Warn, kick, ban, timeout…"),
            discord.SelectOption(label="🎫 Tickets & AutoMod", value="tickets", description="Tickets, word filter…"),
            discord.SelectOption(label="ℹ️ Info & Server", value="info", description="Userinfo, serverinfo, avatar…"),
            discord.SelectOption(label="🎮 Roblox", value="roblox", description="Roblox lookup & verification"),
            discord.SelectOption(label="🎉 Events", value="events", description="Giveaways, polls, announcements"),
            discord.SelectOption(label="👑 Admin", value="admin", description="Shutdown, mass actions, advanced config"),
        ]
        super().__init__(placeholder="Select a category…", options=options)

    async def callback(self, interaction):
        cats = {
            "xp": ("🏅 XP & Leveling", "`/rank` — View your rank card\n`/leaderboard` — XP & coin leaderboards\n`/addxp` — Add XP to a member (Admin)\n`/resetxp` — Reset XP (Admin)\n`/givexp` — Give XP to a member (Admin)\n`/setlevel` — Set a member's level (Admin)"),
            "economy": ("💰 Economy", "`/balance` — Check wallet\n`/daily` — Claim daily coins\n`/work` — Work for coins\n`/pay` — Send coins\n`/gamble` — 50/50 bet\n`/slots` — Slot machine\n`/rob` — Rob someone\n`/shop` — View coin shop\n`/richlist` — Top 10 richest\n`/givecoins` — Give coins (Admin)"),
            "fun": ("🎮 Fun & Games", "`/8ball` · `/joke` · `/rps` · `/rate` · `/ship`\n`/truth` · `/dare` · `/wouldyou` · `/trivia`\n`/compliment` · `/mock` · `/reverse` · `/pp` · `/iq`\n`/coinflip` · `/dice` · `/choose` · `/roblox-fact`"),
            "utility": ("🛠️ Utility", "`/snipe` · `/calc` · `/color` · `/timestamp`\n`/charcount` · `/b64` · `/say` · `/embed`\n`/pigLatin` · `/afk` · `/remindme` · `/howlong`"),
            "mod": ("🔨 Moderation", "`/warn` · `/kick` · `/ban` · `/unban`\n`/timeout` · `/untimeout` · `/mute` · `/unmute`\n`/softban` · `/tempban` · `/purge` · `/lock`\n`/unlock` · `/slowmode` · `/nick` · `/nuke`\n`/addrole` · `/removerole` · `/massrole`\n`/history` · `/warnings` · `/clearwarnings`\n`/note` · `/notes` · `/modmenu` · `/staffpanel`"),
            "tickets": ("🎫 Tickets & AutoMod", "`/ticket` — Open a support ticket\n`/closeticket` — Close a ticket\n`/addword` — Add word to filter\n`/removeword` — Remove word from filter\n`/wordlist` — View word filter"),
            "info": ("ℹ️ Info & Server", "`/userinfo` · `/serverinfo` · `/avatar`\n`/roleinfo` · `/channelinfo` · `/servericon`\n`/ping` · `/uptime` · `/botinfo` · `/membercount`\n`/stafflist` · `/newmembers` · `/howlong`"),
            "roblox": ("🎮 Roblox", "`/roblox` — Look up any Roblox profile\n`/roblox-link` — Manually link a Roblox account\n`/roblox-unlink` — Unlink Roblox account\n`/roblox-verify` — Start Roblox verification\n`/roblox-whois` — Find Discord user by Roblox name\n`/roblox-fact` — Random Roblox fact"),
            "events": ("🎉 Events & Community", "`/giveaway` — Start a giveaway\n`/endgiveaway` — End a giveaway early\n`/rerollgiveaway` — Reroll winner\n`/poll` — Create an interactive poll\n`/announce` — Post announcement\n`/suggest` — Submit a suggestion\n`/serverrules` — Post server rules"),
            "admin": ("👑 Admin Commands", "`/shutdown` — Safely shut down the bot (**Admin only**)\n`/restart` — Restart the bot (flag)\n`/massrole` — Add/remove role to all members\n`/config` — Full bot configuration panel\n`/resetconfig` — Reset all config\n`/setprefix` — Change bot prefix\n`/lockdown` — Emergency server lockdown\n`/unlockdown` — Lift lockdown\n`/nuke` — Nuke a channel"),
        }
        title, desc = cats.get(self.values[0], ("Help", "—"))
        embed = discord.Embed(title=title, description=desc, color=0x5865F2)
        embed.set_footer(text="Use ! prefix for most commands too")
        await interaction.response.send_message(embed=embed, ephemeral=True)

class HelpView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect())

@tree.command(name="help", description="Browse all bot commands by category")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 Young Boy Studios Bot",
        description="Select a category below to see all available commands.\n\nAll commands work with both `/` and `!` prefix.",
        color=0x5865F2
    )
    embed.add_field(name="🔨 Moderation", value="Warn, kick, ban, timeout, purge…", inline=True)
    embed.add_field(name="🏅 XP & Levels", value="Rank, leaderboard, level roles…", inline=True)
    embed.add_field(name="💰 Economy", value="Daily, work, gamble, shop…", inline=True)
    embed.add_field(name="🎮 Roblox", value="Lookup, verify, link accounts…", inline=True)
    embed.add_field(name="🎉 Events", value="Giveaways, polls, announcements…", inline=True)
    embed.add_field(name="👑 Admin", value="Shutdown, config, mass actions…", inline=True)
    embed.set_footer(text="Young Boy Studios · Use the dropdown to explore commands")
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
        embed = discord.Embed(title="✅ Already Verified!", description=f"Linked to **{linked['display']}** (@{linked['username']})\n\nUse `/roblox-unlink` to remove the link.", color=0x3BA55C)
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
        return await interaction.response.send_message("❌ You don't have a linked Roblox account.", ephemeral=True)
    old = roblox_links.pop(interaction.user.id)
    # Remove verified role
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
    match = next((uid for uid, v in roblox_links.items() if v["username"].lower() == username.lower()), None)
    if not match:
        return await interaction.response.send_message(f"❌ No linked Discord user found for **@{username}**.", ephemeral=True)
    member = interaction.guild.get_member(match)
    rb = roblox_links[match]
    embed = discord.Embed(title=f"🔍 Roblox → Discord Lookup", color=0x00B2FF)
    embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}")
    embed.add_field(name="💬 Discord", value=member.mention if member else f"User `{match}`")
    embed.add_field(name="✅ Verified", value="Yes" if rb.get("verified") else "No (manual link)")
    embed.add_field(name="Linked", value=rb.get("linked_at", "—"))
    if rb.get("thumb"): embed.set_thumbnail(url=rb["thumb"])
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="roblox-fact", description="Get a random Roblox development fact")
async def slash_roblox_fact(interaction: discord.Interaction):
    facts = [
        "Roblox uses Lua 5.1 as its scripting language, but extends it with a custom runtime called Luau.",
        "The `game` object in Roblox is the root of the DataModel — everything lives inside it.",
        "Roblox's physics engine is based on a custom implementation of ODE (Open Dynamics Engine).",
        "LocalScripts only run on the client; Scripts only run on the server. Never mix them up!",
        "You can use `RunService.Heartbeat` to run code every frame — great for smooth animations.",
        "DataStores are rate-limited: you can only make ~60 requests per minute per game server.",
        "The maximum size of a Model in Roblox Studio is determined by your workspace's StreamingEnabled setting.",
        "Roblox was founded in 2004 and launched to the public in 2006.",
        "The Roblox economy uses Robux, which was introduced in 2013 replacing ROBLOX Points and Tix.",
        "ModuleScripts are the best way to share code between scripts — they act like libraries.",
        "Using `task.spawn()` instead of `coroutine.wrap()` is the recommended modern approach in Roblox.",
        "Luau adds type annotations, optional typing, and improved performance over standard Lua.",
    ]
    embed = discord.Embed(title="💡 Roblox Dev Fact", description=random.choice(facts), color=0x00B2FF)
    embed.set_footer(text="Young Boy Studios · Dev Knowledge")
    await interaction.response.send_message(embed=embed)

# ============================================================
# SLASH — MODERATION
# ============================================================
@tree.command(name="modmenu", description="Open full moderation control panel for a member [Staff]")
@app_commands.describe(member="Member to moderate")
async def slash_modmenu(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
    embed.add_field(name="📅 Account Created", value=member.created_at.strftime("%d %b %Y"), inline=True)
    embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}" if rb else "Not linked", inline=True)
    embed.add_field(name="✅ Verified", value="Yes" if rb and rb.get("verified") else "No", inline=True)
    embed.set_footer(text="All actions are logged · Use buttons below")
    await interaction.response.send_message(embed=embed, view=ModMenuView(member), ephemeral=True)

@tree.command(name="warn", description="Warn a member [Staff]")
@app_commands.describe(member="Member to warn", reason="Reason")
async def slash_warn(interaction: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
    try:
        await member.timeout(discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason)
        add_mod_log("Timeout", str(member), str(interaction.user), f"{minutes}m — {reason}", "#faa61a")
        await interaction.response.send_message(f"⏰ **{member}** timed out for {minutes}m.")
    except: await interaction.response.send_message("❌ Couldn't timeout.", ephemeral=True)

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
    try:
        await member.timeout(discord.utils.utcnow() + timedelta(hours=min(hours, 672)), reason=reason)
        add_mod_log("Mute", str(member), str(interaction.user), f"{hours}h — {reason}", "#faa61a")
        await interaction.response.send_message(f"🔇 **{member}** muted for **{hours}h**.")
    except: await interaction.response.send_message("❌ Couldn't mute.", ephemeral=True)

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
    try:
        await member.ban(reason=f"Softban: {reason}", delete_message_days=max(1, min(7, delete_days)))
        await interaction.guild.unban(member, reason="Softban")
        add_mod_log("Softban", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(f"🪃 **{member}** softbanned. Can rejoin.")
    except: await interaction.response.send_message("❌ Couldn't softban.", ephemeral=True)

@tree.command(name="tempban", description="Temporarily ban a member [Staff]")
@app_commands.describe(member="Member", hours="Ban duration in hours", reason="Reason")
async def slash_tempban(interaction: discord.Interaction, member: discord.Member, hours: float = 24.0, reason: str = "No reason"):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        await member.ban(reason=f"Tempban {hours}h: {reason}", delete_message_days=1)
        add_mod_log("Tempban", str(member), str(interaction.user), f"{hours}h — {reason}", "#ed4245")
        ban_log_data.append({"user": str(member), "uid": member.id, "reason": f"Tempban {hours}h: {reason}", "by": str(interaction.user), "time": datetime.now().strftime("%d %b %Y %H:%M")})
        await interaction.response.send_message(f"🕐 **{member}** banned for **{hours}h**. Use `/unban` with their ID to unban early.")
    except: await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)

@tree.command(name="purge", description="Delete messages in bulk [Staff]")
@app_commands.describe(amount="Messages to delete (1-100)")
async def slash_purge(interaction: discord.Interaction, amount: int = 10):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=max(1, min(100, amount)))
    await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)

@tree.command(name="lock", description="Lock a channel [Staff]")
@app_commands.describe(channel="Channel to lock", reason="Reason")
async def slash_lock(interaction: discord.Interaction, channel: discord.TextChannel = None, reason: str = "No reason"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
    ch = channel or interaction.channel
    await ch.edit(slowmode_delay=max(0, min(21600, seconds)))
    msg = f"⏱️ Slowmode **disabled** in {ch.mention}." if seconds == 0 else f"⏱️ Slowmode **{seconds}s** in {ch.mention}."
    await interaction.response.send_message(msg)

@tree.command(name="nuke", description="Clone and delete this channel [Staff]")
@app_commands.describe(reason="Reason")
async def slash_nuke(interaction: discord.Interaction, reason: str = "Channel nuke"):
    if not interaction.user.guild_permissions.manage_channels:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
    try:
        await member.add_roles(role)
        await interaction.response.send_message(f"✅ {role.mention} added to **{member}**.")
    except: await interaction.response.send_message("❌ Couldn't add role.", ephemeral=True)

@tree.command(name="removerole", description="Remove a role from a member [Staff]")
@app_commands.describe(member="Member", role="Role to remove")
async def slash_removerole(interaction: discord.Interaction, member: discord.Member, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
                await member.add_roles(role)
                count += 1
            elif action == "remove" and role in member.roles:
                await member.remove_roles(role)
                count += 1
        except: pass
    await interaction.followup.send(f"✅ **{action.title()}ed** {role.mention} for **{count}** members.", ephemeral=True)

@tree.command(name="warnings", description="View warnings for a member")
@app_commands.describe(member="Member to check")
async def slash_warnings(interaction: discord.Interaction, member: discord.Member = None):
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
    if rb:
        embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}" + (" ✅" if rb.get("verified") else ""))
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="serverinfo", description="View server info")
async def slash_serverinfo(interaction: discord.Interaction):
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
    embed.add_field(name="⚡ Slash Cmds", value=f"`{len(tree.get_commands())}`")
    embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
    embed.add_field(name="🎮 Roblox Linked", value=f"`{len(roblox_links)}`")
    embed.add_field(name="👾 XP Members", value=f"`{len(xp_data)}`")
    embed.add_field(name="💰 Economy Users", value=f"`{len(economy_data)}`")
    embed.add_field(name="🎫 Tickets", value=f"`{len(ticket_data)}`")
    if bot.user and bot.user.avatar: embed.set_thumbnail(url=bot.user.avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="membercount", description="Member count breakdown")
async def slash_membercount(interaction: discord.Interaction):
    g = interaction.guild
    bots = sum(1 for m in g.members if m.bot)
    humans = g.member_count - bots
    online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
    embed = discord.Embed(title=f"👥 {g.name}", color=0x5865f2)
    embed.add_field(name="👥 Total", value=f"`{g.member_count}`")
    embed.add_field(name="🧑 Humans", value=f"`{humans}`")
    embed.add_field(name="🤖 Bots", value=f"`{bots}`")
    embed.add_field(name="🟢 Online", value=f"`{online}`")
    embed.add_field(name="📺 Channels", value=f"`{len(g.channels)}`")
    embed.add_field(name="🎭 Roles", value=f"`{len(g.roles)}`")
    await interaction.response.send_message(embed=embed)

@tree.command(name="roleinfo", description="View info about a role")
@app_commands.describe(role="Role to inspect")
async def slash_roleinfo(interaction: discord.Interaction, role: discord.Role):
    perms = [p.replace("_", " ").title() for p, v in role.permissions if v]
    embed = discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="ID", value=f"`{role.id}`")
    embed.add_field(name="Color", value=f"`{str(role.color)}`")
    embed.add_field(name="Members", value=f"`{len(role.members)}`")
    embed.add_field(name="Mentionable", value="✅" if role.mentionable else "❌")
    embed.add_field(name="Hoisted", value="✅" if role.hoist else "❌")
    embed.add_field(name="Created", value=role.created_at.strftime("%d %b %Y"))
    if perms: embed.add_field(name="Key Permissions", value=", ".join(perms[:15]), inline=False)
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
    embed = discord.Embed(title=f"👮 Staff List", color=0x5865f2)
    embed.description = "\n".join(f"{rank} **{m.display_name}**" for m, rank in staff[:25]) or "No staff found."
    embed.set_footer(text=f"{len(staff)} staff member(s)")
    await interaction.response.send_message(embed=embed)

@tree.command(name="newmembers", description="View 10 most recently joined members")
async def slash_newmembers(interaction: discord.Interaction):
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
# SLASH — ECONOMY
# ============================================================
@tree.command(name="balance", description="Check YBS Coins balance")
@app_commands.describe(member="Member to check")
async def slash_balance(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    eco = get_economy(member.id, str(member))
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)

@tree.command(name="daily", description="Claim your daily YBS Coins")
async def slash_daily(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_daily"] and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400:
        diff = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
        h, rem = divmod(int(diff), 3600)
        m = rem // 60
        return await interaction.response.send_message(f"⏰ Come back in **{h}h {m}m**.", ephemeral=True)
    amt = random.randint(200, 500)
    # Bonus for verified Roblox
    if interaction.user.id in roblox_links and roblox_links[interaction.user.id].get("verified"):
        bonus = random.randint(50, 100)
        amt += bonus
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_daily"] = now.isoformat()
    await interaction.response.send_message(f"✅ {interaction.user.mention} claimed **{amt:,} coins!** 💰")

@tree.command(name="work", description="Work to earn YBS Coins")
async def slash_work(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if eco["last_work"] and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600:
        diff = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
        m, s = divmod(int(diff), 60)
        return await interaction.response.send_message(f"⏰ Rest **{m}m {s}s** before working again.", ephemeral=True)
    jobs = ["coded a Roblox script 💻", "modelled an epic build 🎨", "fixed a nasty bug 🐛", "scripted an obby 🏃", "designed a UI 🖥️", "ran a game test 🎮"]
    amt = random.randint(50, 200)
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_work"] = now.isoformat()
    await interaction.response.send_message(f"💼 {interaction.user.mention} {random.choice(jobs)} and earned **{amt:,} coins!**")

@tree.command(name="pay", description="Send YBS Coins to another member")
@app_commands.describe(member="Who to pay", amount="How many coins")
async def slash_pay(interaction: discord.Interaction, member: discord.Member, amount: int):
    if amount <= 0: return await interaction.response.send_message("❌ Positive amount only.", ephemeral=True)
    payer = get_economy(interaction.user.id, str(interaction.user))
    if payer["balance"] < amount: return await interaction.response.send_message(f"❌ Only **{payer['balance']:,} coins**.", ephemeral=True)
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount
    payee["total_earned"] += amount
    await interaction.response.send_message(f"✅ {interaction.user.mention} → {member.mention} **{amount:,} coins** 💸")

@tree.command(name="gamble", description="Gamble your YBS Coins")
@app_commands.describe(amount="Coins to bet")
async def slash_gamble(interaction: discord.Interaction, amount: int):
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
    if member.id == interaction.user.id:
        return await interaction.response.send_message("❌ You can't rob yourself!", ephemeral=True)
    robber = get_economy(interaction.user.id, str(interaction.user))
    victim = get_economy(member.id, str(member))
    if victim["balance"] < 100:
        return await interaction.response.send_message(f"❌ **{member.display_name}** is too broke to rob!", ephemeral=True)
    success = random.random() > 0.5
    if success:
        stolen = random.randint(50, min(500, victim["balance"]))
        robber["balance"] += stolen; robber["total_earned"] += stolen
        victim["balance"] -= stolen
        await interaction.response.send_message(f"🦝 {interaction.user.mention} robbed **{member.display_name}** for **{stolen:,} coins!** 💸")
    else:
        fine = random.randint(50, 200)
        robber["balance"] = max(0, robber["balance"] - fine)
        await interaction.response.send_message(f"🚨 {interaction.user.mention} got caught robbing **{member.display_name}** and paid a **{fine:,} coin** fine! 🚔")

@tree.command(name="richlist", description="Top 10 richest members")
async def slash_richlist(interaction: discord.Interaction):
    if not economy_data: return await interaction.response.send_message("❌ No economy data yet!")
    top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:10]
    embed = discord.Embed(title="💰 Rich List", color=0xFAA61A)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, d) in enumerate(top):
        prefix = medals[i] if i < 3 else f"{i+1}."
        name = d.get("name", str(uid)).split("#")[0]
        lines.append(f"{prefix} **{name}** — {d['balance']:,} coins")
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
# SLASH — FUN
# ============================================================
@tree.command(name="8ball", description="Ask the magic 8-ball")
@app_commands.describe(question="Your question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="joke", description="Get a random dev joke")
async def slash_joke(interaction: discord.Interaction):
    jokes = [("Why do programmers prefer dark mode?","Because light attracts bugs! 🐛"),("Why did the Roblox player refuse to leave?","He was ROBLOXed in! 🎮"),("Why did the developer go broke?","He used up all his cache! 💸"),("Why do Java devs wear glasses?","Because they don't C#!"),("What's a pirate's fav language?","R, matey! 🏴‍☠️")]
    q, a = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke!", color=0xFAA61A)
    embed.add_field(name="Setup", value=q, inline=False)
    embed.add_field(name="Punchline", value=f"||{a}||", inline=False)
    await interaction.response.send_message(embed=embed)

@tree.command(name="rps", description="Rock Paper Scissors vs the bot")
@app_commands.choices(choice=[app_commands.Choice(name="🪨 Rock", value="rock"), app_commands.Choice(name="📄 Paper", value="paper"), app_commands.Choice(name="✂️ Scissors", value="scissors")])
async def slash_rps(interaction: discord.Interaction, choice: str):
    ch = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    bot_c = random.choice(list(ch.keys()))
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    result = "🤝 **Tie!**" if choice == bot_c else ("🎉 **You win!**" if wins[choice] == bot_c else "😔 **Bot wins!**")
    await interaction.response.send_message(f"{ch[choice]} **{choice.title()}** vs **{bot_c.title()}** {ch[bot_c]}\n{result}")

@tree.command(name="rate", description="Rate something out of 100")
@app_commands.describe(thing="What to rate")
async def slash_rate(interaction: discord.Interaction, thing: str):
    score = random.randint(0, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    color = 0x3BA55C if score >= 70 else 0xFAA61A if score >= 40 else 0xED4245
    await interaction.response.send_message(embed=discord.Embed(title=f"⭐ Rating: {thing}", description=f"**{score}/100**\n`{bar}`", color=color))

@tree.command(name="ship", description="Check compatibility")
@app_commands.describe(user1="First member", user2="Second member")
async def slash_ship(interaction: discord.Interaction, user1: discord.Member, user2: discord.Member = None):
    user2 = user2 or interaction.user
    score = (user1.id + user2.id) % 101
    bar = "💗" * (score // 10) + "🖤" * (10 - score // 10)
    await interaction.response.send_message(embed=discord.Embed(title="💘 Compatibility", description=f"**{user1.display_name}** 💕 **{user2.display_name}**\n\n{bar}\n\n**{score}%** compatible!", color=0xFF79C6))

@tree.command(name="truth", description="Get a truth question")
async def slash_truth(interaction: discord.Interaction):
    ts = ["What's your most embarrassing coding mistake?","What game are you secretly working on?","Have you ever copy-pasted code you didn't understand?","What's the longest you've spent on one bug?","What dev skill do you wish you had?"]
    await interaction.response.send_message(embed=discord.Embed(title="😳 Truth!", description=random.choice(ts), color=0xFF79C6))

@tree.command(name="dare", description="Get a dare challenge")
async def slash_dare(interaction: discord.Interaction):
    ds = ["Make a mini-game in 30 minutes!","Build a noob character and screenshot it!","Write hello world in 3 languages!","Script a random feature in 5 minutes!","Draw your game idea in MS Paint!"]
    await interaction.response.send_message(embed=discord.Embed(title="😈 Dare!", description=random.choice(ds), color=0xED4245))

@tree.command(name="coinflip", description="Flip a coin")
async def slash_coinflip(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    await interaction.response.send_message(embed=discord.Embed(title="🪙 Coin Flip!", description=f"**{result}!**", color=0x5865f2))

@tree.command(name="dice", description="Roll dice")
@app_commands.describe(sides="Number of sides", count="Number of dice")
async def slash_dice(interaction: discord.Interaction, sides: int = 6, count: int = 1):
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
    options = [c.strip() for c in choices.split(",") if c.strip()]
    if len(options) < 2: return await interaction.response.send_message("❌ Need at least 2 options.", ephemeral=True)
    await interaction.response.send_message(embed=discord.Embed(title="🎯 I Choose...", description=f"**{random.choice(options)}**", color=0x5865f2))

@tree.command(name="mock", description="MoCkIfY some text")
@app_commands.describe(text="Text to mockify")
async def slash_mock(interaction: discord.Interaction, text: str):
    await interaction.response.send_message("".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text)))

@tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Who to compliment")
async def slash_compliment(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    cs = ["is an absolute legend! 🌟","makes this server better! 💪","is the most talented dev here! 🎮","is going to build something incredible! 🚀","has the best scripts in the game! 💻"]
    await interaction.response.send_message(f"💝 {member.mention} {random.choice(cs)}")

@tree.command(name="pp", description="Check pp size (not real)")
@app_commands.describe(member="Member")
async def slash_pp(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    size = member.id % 15
    await interaction.response.send_message(f"🍆 **{member.display_name}:** `8{'=' * size}D` ({size} inches)")

@tree.command(name="iq", description="Check IQ (not real)")
@app_commands.describe(member="Member")
async def slash_iq(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    iq = (member.id + 47) % 201
    rating = "needs help 💀" if iq < 70 else "average 😐" if iq < 100 else "smart 🧠" if iq < 130 else "genius 🎓" if iq < 160 else "BIG BRAIN 🤯"
    await interaction.response.send_message(embed=discord.Embed(title=f"🧠 IQ — {member.display_name}", description=f"**IQ: {iq}** — {rating}", color=0x5865f2))

@tree.command(name="snipe", description="See the last deleted message")
async def slash_snipe(interaction: discord.Interaction):
    data = snipe_data.get(interaction.channel.id)
    if not data: return await interaction.response.send_message("❌ Nothing to snipe!", ephemeral=True)
    embed = discord.Embed(description=data["content"], color=0xED4245)
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    await interaction.response.send_message(embed=embed)

@tree.command(name="calc", description="Calculate a math expression")
@app_commands.describe(expression="Math expression")
async def slash_calc(interaction: discord.Interaction, expression: str):
    if not all(c in "0123456789+-*/.() " for c in expression):
        return await interaction.response.send_message("❌ Only basic math operators!", ephemeral=True)
    try:
        result = eval(expression)
        await interaction.response.send_message(embed=discord.Embed(title="🧮 Calculator", description=f"`{expression}` = **{result}**", color=0x5865F2))
    except: await interaction.response.send_message("❌ Invalid expression!", ephemeral=True)

@tree.command(name="afk", description="Set or clear your AFK status")
@app_commands.describe(reason="AFK reason (blank to clear)")
async def slash_afk(interaction: discord.Interaction, reason: str = None):
    if reason:
        afk_data[interaction.user.id] = {"reason": reason, "time": datetime.now().strftime("%H:%M")}
        await interaction.response.send_message(f"💤 **{interaction.user.display_name}** is now AFK: *{reason}*")
    else:
        afk_data.pop(interaction.user.id, None)
        await interaction.response.send_message("✅ AFK cleared.", ephemeral=True)

@tree.command(name="remindme", description="Set a reminder")
@app_commands.describe(minutes="Minutes until reminder", reminder="What to remind you of")
async def slash_remindme(interaction: discord.Interaction, minutes: int, reminder: str):
    await interaction.response.send_message(f"⏰ I'll remind you in **{minutes} minute{'s' if minutes != 1 else ''}**!", ephemeral=True)
    await asyncio.sleep(minutes * 60)
    try: await interaction.user.send(f"⏰ **Reminder:** {reminder}")
    except:
        ch = interaction.channel
        if ch: await ch.send(f"⏰ {interaction.user.mention} — reminder: {reminder}")

# ============================================================
# SLASH — EVENTS
# ============================================================
@tree.command(name="giveaway", description="Start a giveaway [Staff]")
@app_commands.describe(duration="Duration in minutes", prize="What to give away", channel="Channel (default: giveaway channel)")
async def slash_giveaway(interaction: discord.Interaction, duration: int, prize: str, channel: discord.TextChannel = None):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
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
    try:
        embed = discord.Embed(title=f"📬 Message from {interaction.guild.name} Staff", description=message, color=0x5865f2)
        await member.send(embed=embed)
        await interaction.response.send_message(f"✅ DM sent to **{member}**.", ephemeral=True)
    except: await interaction.response.send_message("❌ Couldn't DM.", ephemeral=True)

# ============================================================
# SLASH — TICKETS
# ============================================================
@tree.command(name="ticket", description="Open a support ticket")
@app_commands.describe(reason="Reason")
async def slash_ticket(interaction: discord.Interaction, reason: str = "General support"):
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
    embed = discord.Embed(title="🛠️ Staff Quick-Action Panel", description="Fast access to common staff tools. All actions logged.", color=0x5865f2)
    embed.add_field(name="🔒 Row 1", value="Lock · Unlock · Purge 10 · Purge 50 · Nuke", inline=False)
    embed.add_field(name="⏱️ Row 2", value="Slow 10s · Slow 30s · Slow Off · Server Stats", inline=False)
    await interaction.response.send_message(embed=embed, view=StaffPanelView(), ephemeral=True)

# ============================================================
# SLASH — ADMIN (SHUTDOWN etc)
# ============================================================
SHUTDOWN_CONFIRM_CODES = {}  # user_id -> code

@tree.command(name="shutdown", description="Safely shut down the bot [Owner/Admin only]")
async def slash_shutdown(interaction: discord.Interaction):
    # Only server owner or specific admin can do this
    if not (interaction.user.id == interaction.guild.owner_id or interaction.user.guild_permissions.administrator):
        return await interaction.response.send_message("❌ Only the server owner or administrators can shut down the bot.", ephemeral=True)
    code = random.randint(100000, 999999)
    SHUTDOWN_CONFIRM_CODES[interaction.user.id] = {"code": str(code), "expires": (datetime.now() + timedelta(minutes=2)).isoformat()}
    embed = discord.Embed(title="⚠️ Bot Shutdown Confirmation", description=f"Are you sure you want to shut down the bot?\n\nConfirm by typing `/shutdown-confirm code:{code}`\n\n*Code expires in 2 minutes.*", color=0xED4245)
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
        return await interaction.response.send_message("❌ Code expired. Use `/shutdown` again.", ephemeral=True)
    if pending["code"] != code:
        return await interaction.response.send_message("❌ Wrong code.", ephemeral=True)
    SHUTDOWN_CONFIRM_CODES.pop(interaction.user.id, None)
    embed = discord.Embed(title="🔴 Bot Shutting Down", description=f"Shutdown confirmed by **{interaction.user.display_name}**.\n\nThe bot will go offline now. Restart it manually or via hosting panel.", color=0xED4245)
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
    await interaction.followup.send(f"🔒 **Server locked down!** {count} channels locked.\n**Reason:** {reason}\n\nUse `/unlockdown` to restore.", ephemeral=True)

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
# DASHBOARD (Flask)
# ============================================================
flask_app = Flask(__name__)
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
    return [{"uid": uid, "name": d.get("name", "Unknown"), "balance": d["balance"], "total_earned": d["total_earned"]} for uid, d in sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)]

def common():
    return dict(
        bot_online=bot.is_ready(),
        bot_name=str(bot.user) if bot.user else "YBS Bot",
        app_count=len(applications_data),
        warn_count=sum(len(v) for v in warnings_data.values()),
        notes_count=sum(len(v) for v in notes_data.values()),
        giveaway_count=len(giveaway_data),
        ticket_count=sum(1 for t in ticket_data.values() if t["status"] == "open"),
        xp_count=len(xp_data),
        eco_count=len(economy_data),
        mod_log_count=len(mod_log_data),
        activity=activity_log,
        uptime=uptime_str(),
        mod_logs=mod_log_data,
        roblox_count=len(roblox_links),
        verified_count=sum(1 for v in roblox_links.values() if v.get("verified")),
        top_xp=build_xp_leaderboard()[:5],
        recent_apps=list(applications_data.values())[-5:],
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

@flask_app.route("/activity")
def dashboard_activity():
    return render_template("dashboard.html", page="activity", **common())

@flask_app.route("/leaderboard")
def dashboard_leaderboard():
    return render_template("dashboard.html", page="leaderboard", xp_lb=build_xp_leaderboard()[:25], eco_lb=build_eco_leaderboard()[:25], **common())

@flask_app.route("/modlogs")
def dashboard_modlogs():
    return render_template("dashboard.html", page="modlogs", **common())

@flask_app.route("/tickets")
def dashboard_tickets():
    return render_template("dashboard.html", page="tickets", tickets=ticket_data, **common())

@flask_app.route("/roblox")
def dashboard_roblox():
    accounts = [{"discord_name": v.get("discord_name", "Unknown"), "username": v["username"], "display": v.get("display", v["username"]), "roblox_id": v["roblox_id"], "thumb": v.get("thumb"), "linked_at": v.get("linked_at", "—"), "verified": v.get("verified", False)} for v in roblox_links.values()]
    return render_template("dashboard.html", page="roblox", roblox_accounts=accounts, **common())

@flask_app.route("/economy")
def dashboard_economy():
    eco_lb = build_eco_leaderboard()
    total_coins = sum(d["balance"] for d in economy_data.values())
    return render_template("dashboard.html", page="economy", eco_lb=eco_lb[:25], total_coins=total_coins, **common())

@flask_app.route("/api/stats")
def api_stats():
    return jsonify({
        "members": sum(g.member_count for g in bot.guilds),
        "servers": len(bot.guilds),
        "uptime": uptime_str(),
        "ping": round(bot.latency * 1000),
        "xp_users": len(xp_data),
        "economy_users": len(economy_data),
        "roblox_linked": len(roblox_links),
        "verified": sum(1 for v in roblox_links.values() if v.get("verified")),
        "applications": len(applications_data),
        "mod_actions": len(mod_log_data),
    })

@flask_app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Dashboard shutdown endpoint — requires secret key"""
    global bot_shutdown_flag
    data = request.json or {}
    secret = os.environ.get("DASHBOARD_SECRET", "changeme")
    if data.get("secret") != secret:
        return jsonify({"error": "Unauthorized"}), 403
    bot_shutdown_flag = True
    return jsonify({"status": "shutdown initiated"})

def run_bot():
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Bot error: {e}")

# ============================================================
# YBS BOT — ADDITIONS MODULE
# Paste this ENTIRE file at the bottom of your main bot.py
# (before the final bot_thread / flask_app.run lines)
# ============================================================

import re, hashlib, string, unicodedata

# ============================================================
# EXTRA GLOBAL DATA STORES
# ============================================================
bug_reports_data      = []          # list of dicts
channel_pins_data     = {}          # channel_id -> pin code (str)
daily_staff_codes     = {}          # user_id -> {"code": str, "date": str, "used": bool}
dm_replies_data       = {}          # user_id -> channel_id (for DM relay)
timers_data           = []          # list of active timers
counting_data         = {}          # guild_id -> {"channel": id, "count": int, "last_user": id}
premium_channels_data = {}          # guild_id -> [channel_ids]
bot_commands_channels = {}          # guild_id -> channel_id  (where non-admin slash cmds must run)
nickname_log_data     = []          # list of nick change dicts
report_log_data       = []          # command usage reports

EXTRA_CHANNEL_KEYS = [
    ("bot_commands_channel",  "🤖 Bot Commands Channel"),
    ("counting_channel",      "🔢 Counting Channel"),
    ("nickname_log_channel",  "📝 Nickname Log Channel"),
    ("bug_report_channel",    "🐛 Bug Report Channel"),
    ("premium_category",      "⭐ Premium Category ID"),
    ("staff_log_channel",     "👮 Staff Log Channel"),
    ("timer_channel",         "⏲️ Timer Announce Channel"),
    ("dm_relay_channel",      "📨 DM Relay Channel"),
    ("report_channel",        "📊 Command Report Channel"),
]

# ─── helpers ────────────────────────────────────────────────
def gen_code(length=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def is_bot_commands_channel(interaction: discord.Interaction) -> bool:
    """Return True if the interaction is in the designated bot-commands channel OR user is admin."""
    if interaction.user.guild_permissions.administrator:
        return True
    ch_id = get(interaction.guild.id, "bot_commands_channel")
    if not ch_id:
        return True  # no restriction set
    return interaction.channel.id == ch_id

def add_report(command_name, user, guild, extra=""):
    report_log_data.insert(0, {
        "command": command_name,
        "user": str(user),
        "guild": str(guild),
        "extra": extra,
        "time": datetime.now().strftime("%H:%M · %d %b"),
    })
    while len(report_log_data) > 500:
        report_log_data.pop()

# ─── Universal report + dashboard footer embed helper ───────
def report_footer_view(command_name: str):
    """Returns a View with 'Report Bug' and 'Dashboard' buttons appended to any command."""
    class ReportFooterView(View):
        def __init__(self):
            super().__init__(timeout=120)

        @discord.ui.button(label="🐛 Report Bug", style=discord.ButtonStyle.secondary)
        async def report_bug(self, interaction: discord.Interaction, button: Button):
            await interaction.response.send_modal(QuickBugModal(command_name))

        @discord.ui.button(label="🌐 Dashboard", style=discord.ButtonStyle.link, url="http://localhost:5000")
        async def dashboard(self, interaction: discord.Interaction, button: Button):
            pass  # link button needs no callback

    return ReportFooterView()

class QuickBugModal(Modal, title="🐛 Quick Bug Report"):
    description_input = TextInput(
        label="What went wrong?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe the bug...",
        max_length=1000,
    )
    def __init__(self, command_name: str = "Unknown"):
        super().__init__()
        self.command_name = command_name

    async def on_submit(self, interaction: discord.Interaction):
        report = {
            "command": self.command_name,
            "description": self.description_input.value,
            "reporter": str(interaction.user),
            "reporter_id": interaction.user.id,
            "guild": interaction.guild.name if interaction.guild else "DM",
            "time": datetime.now().strftime("%d %b %Y %H:%M"),
            "status": "open",
        }
        bug_reports_data.insert(0, report)
        ch_id = get(interaction.guild.id, "bug_report_channel") if interaction.guild else None
        if ch_id:
            ch = bot.get_channel(ch_id)
            if ch:
                embed = discord.Embed(title="🐛 Bug Report", color=0xED4245)
                embed.add_field(name="Command", value=f"`/{self.command_name}`", inline=True)
                embed.add_field(name="Reporter", value=interaction.user.mention, inline=True)
                embed.add_field(name="Description", value=self.description_input.value, inline=False)
                embed.timestamp = discord.utils.utcnow()
                await ch.send(embed=embed)
        add_activity("🐛", f"Bug report by {interaction.user.display_name}", self.command_name)
        await interaction.response.send_message(
            "✅ Bug report submitted! Thank you for helping improve the bot 🙏", ephemeral=True
        )

# ============================================================
# EXTENDED CHANNEL SETUP SYSTEM
# ============================================================
class ExtraChannelTypeSelect(Select):
    def __init__(self):
        options = [discord.SelectOption(label=label, value=key) for key, label in EXTRA_CHANNEL_KEYS]
        super().__init__(placeholder="Extra channels to configure...", options=options)

    async def callback(self, interaction):
        key = self.values[0]
        label = next(l for k, l in EXTRA_CHANNEL_KEYS if k == key)
        await interaction.response.send_message(
            f"Select the channel/category for **{label}**:",
            view=ChannelPickerView(key, label),
            ephemeral=True,
        )

class ExtraChannelSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ExtraChannelTypeSelect())

# Patch SetupMainView to add extra channels button (monkey-patch a new button)
_old_setup_init = SetupMainView.__init__

def _new_setup_init(self):
    _old_setup_init(self)

    @discord.ui.button(label="🔧 Extra Channels", style=discord.ButtonStyle.secondary, row=3)
    async def extra_channels(s, interaction, button):
        await interaction.response.send_message(
            embed=discord.Embed(title="🔧 Extra Channel Config", color=0x5865F2),
            view=ExtraChannelSelectView(),
            ephemeral=True,
        )
    self.add_item(extra_channels)

SetupMainView.__init__ = _new_setup_init

# ============================================================
# DM RELAY — when bot DMs a user they can reply back
# ============================================================
@bot.event
async def on_raw_reaction_add(payload):
    pass  # placeholder so we can extend later

# Override on_message to catch DM replies
_original_on_message = bot.on_message.callback if hasattr(bot, '_on_message_original') else None

@bot.listen('on_message')
async def on_dm_reply(message):
    """Relay DMs back to the configured DM relay channel."""
    if message.guild or message.author.bot:
        return
    relay_ch_id = None
    # Find any guild that has a relay channel
    for guild in bot.guilds:
        rid = get(guild.id, "dm_relay_channel")
        if rid:
            relay_ch_id = rid
            break
    if not relay_ch_id:
        return
    relay_ch = bot.get_channel(relay_ch_id)
    if not relay_ch:
        return
    embed = discord.Embed(
        title=f"📨 DM from {message.author}",
        description=message.content or "*(no text)*",
        color=0x00B2FF,
        timestamp=datetime.now(),
    )
    embed.set_thumbnail(url=message.author.display_avatar.url)
    embed.set_footer(text=f"User ID: {message.author.id}")
    # Forward attachments
    files = []
    for att in message.attachments:
        try:
            files.append(await att.to_file())
        except Exception:
            pass
    await relay_ch.send(embed=embed, files=files, view=DMReplyView(message.author.id))
    add_activity("📨", f"DM relay from {message.author}", message.content[:60] if message.content else "")

class DMReplyView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id

    @discord.ui.button(label="↩️ Reply", style=discord.ButtonStyle.blurple)
    async def reply_btn(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        uid = self.user_id
        class DMReplyModal(Modal, title="↩️ Reply to DM"):
            reply_text = TextInput(
                label="Your reply",
                style=discord.TextStyle.paragraph,
                max_length=2000,
            )
            async def on_submit(s2, interaction2: discord.Interaction):
                try:
                    user = await bot.fetch_user(uid)
                    embed = discord.Embed(
                        title="📬 Reply from Young Boy Studios Staff",
                        description=s2.reply_text.value,
                        color=0x5865F2,
                    )
                    embed.set_footer(text="Reply to this message to contact us again!")
                    await user.send(embed=embed)
                    await interaction2.response.send_message("✅ Reply sent!", ephemeral=True)
                except Exception:
                    await interaction2.response.send_message("❌ Couldn't send DM.", ephemeral=True)
        await interaction.response.send_modal(DMReplyModal())

# ============================================================
# COUNTING CHANNEL SYSTEM
# ============================================================
@bot.listen('on_message')
async def counting_listener(message):
    if message.author.bot or not message.guild:
        return
    gid = str(message.guild.id)
    ch_id = get(message.guild.id, "counting_channel")
    if not ch_id or message.channel.id != ch_id:
        return
    data = counting_data.setdefault(gid, {"count": 0, "last_user": None})
    content = message.content.strip()
    try:
        number = int(content)
    except ValueError:
        try:
            await message.delete()
        except Exception:
            pass
        return
    expected = data["count"] + 1
    if number == expected and message.author.id != data["last_user"]:
        data["count"] = expected
        data["last_user"] = message.author.id
        await message.add_reaction("✅")
        if expected % 100 == 0:
            await message.channel.send(f"🎉 **{expected}!** Amazing counting, keep it up!")
    else:
        if number != expected:
            await message.channel.send(
                f"❌ {message.author.mention} ruined it at **{data['count']}**! Next number is **1**.",
                delete_after=5,
            )
        else:
            await message.channel.send(
                f"❌ {message.author.mention} you can't count twice in a row! Next is still **{expected}**.",
                delete_after=5,
            )
        data["count"] = 0
        data["last_user"] = None
        try:
            await message.delete()
        except Exception:
            pass

# ============================================================
# BOT COMMANDS CHANNEL ENFORCEMENT
# ============================================================
@bot.listen('on_interaction')
async def enforce_bot_commands_channel(interaction: discord.Interaction):
    if interaction.type != discord.InteractionType.application_command:
        return
    if not interaction.guild:
        return
    if interaction.user.guild_permissions.administrator:
        return
    ch_id = get(interaction.guild.id, "bot_commands_channel")
    if not ch_id:
        return
    if interaction.channel.id == ch_id:
        return
    # List of commands exempt from channel restriction
    exempt = {"help", "ping", "rank", "balance", "daily", "work", "leaderboard", "userinfo", "roblox-verify"}
    cmd_name = getattr(interaction.command, "name", "") if interaction.command else ""
    if cmd_name in exempt:
        return
    try:
        ch = interaction.guild.get_channel(ch_id)
        await interaction.response.send_message(
            f"❌ Please use bot commands in {ch.mention if ch else 'the bot commands channel'}!",
            ephemeral=True,
        )
        # Stop further processing by raising (discord.py will catch this gracefully)
    except Exception:
        pass

# ============================================================
# NICKNAME LOG
# ============================================================
@bot.listen('on_member_update')
async def nickname_log_listener(before: discord.Member, after: discord.Member):
    if before.nick == after.nick:
        return
    entry = {
        "user": str(after),
        "user_id": after.id,
        "before": before.nick or before.name,
        "after": after.nick or after.name,
        "time": datetime.now().strftime("%d %b %Y %H:%M"),
    }
    nickname_log_data.insert(0, entry)
    while len(nickname_log_data) > 300:
        nickname_log_data.pop()
    ch_id = get(after.guild.id, "nickname_log_channel")
    if not ch_id:
        return
    ch = bot.get_channel(ch_id)
    if not ch:
        return
    embed = discord.Embed(title="📝 Nickname Changed", color=0xFAA61A)
    embed.add_field(name="User", value=after.mention)
    embed.add_field(name="Before", value=entry["before"])
    embed.add_field(name="After", value=entry["after"])
    embed.timestamp = discord.utils.utcnow()
    await ch.send(embed=embed)

# ============================================================
# CHANNEL PIN LOCK — staff can lock a channel behind a PIN
# ============================================================
@tree.command(name="pinlock", description="Lock a channel behind a PIN (only channel owner / admin can unlock) [Admin]")
@app_commands.describe(pin="4-digit PIN code to lock the channel", channel="Channel to lock")
async def slash_pinlock(interaction: discord.Interaction, pin: str, channel: discord.TextChannel = None):
    if not (interaction.user.id == interaction.guild.owner_id):
        return await interaction.response.send_message("❌ Server owner only!", ephemeral=True)
    if not pin.isdigit() or len(pin) != 4:
        return await interaction.response.send_message("❌ PIN must be exactly 4 digits.", ephemeral=True)
    ch = channel or interaction.channel
    # Lock for everyone including mods
    overwrite = discord.PermissionOverwrite(read_messages=False, send_messages=False)
    for member in interaction.guild.members:
        if not member.bot:
            try:
                await ch.set_permissions(member, overwrite=overwrite)
            except Exception:
                pass
    channel_pins_data[ch.id] = hashlib.sha256(pin.encode()).hexdigest()
    embed = discord.Embed(
        title="🔐 Channel PIN Locked",
        description=f"**{ch.mention}** is now PIN-locked.\nOnly the server owner can unlock it with `/pinunlock`.",
        color=0xED4245,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    add_activity("🔐", f"PIN-lock on #{ch.name}", f"by {interaction.user.display_name}")

@tree.command(name="pinunlock", description="Unlock a PIN-locked channel [Owner only]")
@app_commands.describe(pin="4-digit PIN", channel="Channel to unlock")
async def slash_pinunlock(interaction: discord.Interaction, pin: str, channel: discord.TextChannel = None):
    if not (interaction.user.id == interaction.guild.owner_id):
        return await interaction.response.send_message("❌ Server owner only!", ephemeral=True)
    ch = channel or interaction.channel
    stored = channel_pins_data.get(ch.id)
    if not stored:
        return await interaction.response.send_message("❌ This channel is not PIN-locked.", ephemeral=True)
    if hashlib.sha256(pin.encode()).hexdigest() != stored:
        return await interaction.response.send_message("❌ Wrong PIN!", ephemeral=True)
    # Restore default perms
    await ch.set_permissions(interaction.guild.default_role, overwrite=None)
    for member in interaction.guild.members:
        try:
            await ch.set_permissions(member, overwrite=None)
        except Exception:
            pass
    channel_pins_data.pop(ch.id, None)
    embed = discord.Embed(title="🔓 Channel PIN Unlocked", description=f"{ch.mention} is now accessible.", color=0x3BA55C)
    await interaction.response.send_message(embed=embed, ephemeral=True)
    add_activity("🔓", f"PIN-unlock #{ch.name}", f"by {interaction.user.display_name}")

# ============================================================
# TIMER COMMAND
# ============================================================
@tree.command(name="timer", description="Start a countdown timer")
@app_commands.describe(
    duration="Duration e.g. 30s, 5m, 2h",
    label="What the timer is for",
    announce="Announce in timer channel when done?",
)
async def slash_timer(
    interaction: discord.Interaction,
    duration: str,
    label: str = "Timer",
    announce: bool = True,
):
    # Parse duration
    total_seconds = 0
    pattern = re.findall(r'(\d+)([smh])', duration.lower())
    if not pattern:
        return await interaction.response.send_message(
            "❌ Invalid duration! Use formats like `30s`, `5m`, `2h`, or `1h30m`.", ephemeral=True
        )
    for value, unit in pattern:
        v = int(value)
        if unit == 's': total_seconds += v
        elif unit == 'm': total_seconds += v * 60
        elif unit == 'h': total_seconds += v * 3600

    if total_seconds <= 0 or total_seconds > 86400:
        return await interaction.response.send_message("❌ Duration must be between 1 second and 24 hours.", ephemeral=True)

    end_time = datetime.now() + timedelta(seconds=total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    dur_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s" if m else f"{s}s"

    embed = discord.Embed(
        title=f"⏲️ Timer Started — {label}",
        description=f"Duration: **{dur_str}**\nEnds: <t:{int(end_time.timestamp())}:R>",
        color=0x00B2FF,
    )
    embed.set_footer(text=f"Started by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, view=report_footer_view("timer"))
    add_report("timer", interaction.user, interaction.guild, label)

    async def run_timer():
        await asyncio.sleep(total_seconds)
        if announce:
            ch_id = get(interaction.guild.id, "timer_channel") if interaction.guild else None
            ch = bot.get_channel(ch_id) if ch_id else interaction.channel
            if not ch:
                ch = interaction.channel
            done_embed = discord.Embed(
                title=f"⏰ Timer Done — {label}",
                description=f"{interaction.user.mention} your **{dur_str}** timer has ended!",
                color=0x3BA55C,
            )
            await ch.send(embed=done_embed)
        try:
            await interaction.user.send(f"⏰ Your **{label}** timer ({dur_str}) has ended!")
        except Exception:
            pass

    asyncio.create_task(run_timer())

# ============================================================
# BUG REPORT COMMAND (standalone)
# ============================================================
@tree.command(name="bugreport", description="Report a bug with the bot or server")
@app_commands.describe(command="Which command had the bug (optional)")
async def slash_bugreport(interaction: discord.Interaction, command: str = "general"):
    await interaction.response.send_modal(QuickBugModal(command))

# ============================================================
# DAILY STAFF CODE — admins get a random code each day to use staff commands
# ============================================================
def get_daily_staff_code(user_id: int) -> str:
    """Generate/fetch today's code for a staff member."""
    today = today_str()
    existing = daily_staff_codes.get(user_id)
    if existing and existing["date"] == today:
        return existing["code"]
    code = gen_code(8)
    daily_staff_codes[user_id] = {"code": code, "date": today, "used": False}
    return code

@tree.command(name="getcode", description="Get today's staff action code (sent via DM) [Staff]")
async def slash_getcode(interaction: discord.Interaction):
    staff_role_id = get(interaction.guild.id, "admin_role") if interaction.guild else None
    is_s = interaction.user.guild_permissions.administrator or (
        staff_role_id and staff_role_id in [r.id for r in interaction.user.roles]
    )
    if not is_s:
        return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
    code = get_daily_staff_code(interaction.user.id)
    try:
        embed = discord.Embed(
            title="🔐 Your Daily Staff Code",
            description=f"Today's code: **`{code}`**\n\nThis code refreshes at midnight.\nUse it with `/staffaction` to perform privileged actions.",
            color=0x5865F2,
        )
        embed.set_footer(text=f"Valid for: {today_str()} · Do not share this code!")
        await interaction.user.send(embed=embed)
        await interaction.response.send_message("✅ Code sent to your DMs!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(
            f"❌ Couldn't DM you! Your code is: ||`{code}`|| (visible only to you)", ephemeral=True
        )

@tree.command(name="staffaction", description="Perform a verified staff action using today's code [Staff]")
@app_commands.describe(code="Your daily staff code from /getcode", action="Action to perform", target="Target member")
@app_commands.choices(action=[
    app_commands.Choice(name="🔇 Mute 1h",       value="mute1h"),
    app_commands.Choice(name="🔇 Mute 24h",      value="mute24h"),
    app_commands.Choice(name="👢 Kick",           value="kick"),
    app_commands.Choice(name="⚠️ Warn",           value="warn"),
    app_commands.Choice(name="🔒 Lock Channel",  value="lock"),
    app_commands.Choice(name="🔓 Unlock Channel", value="unlock"),
])
async def slash_staffaction(
    interaction: discord.Interaction,
    code: str,
    action: str,
    target: discord.Member = None,
):
    staff_role_id = get(interaction.guild.id, "admin_role") if interaction.guild else None
    is_s = interaction.user.guild_permissions.administrator or (
        staff_role_id and staff_role_id in [r.id for r in interaction.user.roles]
    )
    if not is_s:
        return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
    stored = daily_staff_codes.get(interaction.user.id)
    if not stored or stored["date"] != today_str() or stored["code"] != code.upper():
        return await interaction.response.send_message(
            "❌ Invalid or expired code! Get today's code with `/getcode`.", ephemeral=True
        )
    # Perform the action
    if action in ("mute1h", "mute24h", "kick", "warn") and not target:
        return await interaction.response.send_message("❌ This action requires a target member.", ephemeral=True)
    try:
        if action == "mute1h":
            await target.timeout(discord.utils.utcnow() + timedelta(hours=1), reason=f"Staff action by {interaction.user}")
            await interaction.response.send_message(f"🔇 **{target}** muted for 1 hour.", ephemeral=True)
        elif action == "mute24h":
            await target.timeout(discord.utils.utcnow() + timedelta(hours=24), reason=f"Staff action by {interaction.user}")
            await interaction.response.send_message(f"🔇 **{target}** muted for 24 hours.", ephemeral=True)
        elif action == "kick":
            await target.kick(reason=f"Staff action by {interaction.user}")
            await interaction.response.send_message(f"👢 **{target}** kicked.", ephemeral=True)
        elif action == "warn":
            if target.id not in warnings_data:
                warnings_data[target.id] = []
            warnings_data[target.id].append({"reason": "Staff action", "by": str(interaction.user), "time": datetime.now().isoformat()})
            await interaction.response.send_message(f"⚠️ **{target}** warned.", ephemeral=True)
        elif action == "lock":
            await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
            await interaction.response.send_message("🔒 Channel locked.", ephemeral=True)
        elif action == "unlock":
            await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None)
            await interaction.response.send_message("🔓 Channel unlocked.", ephemeral=True)
        add_mod_log(f"StaffAction:{action}", str(target or interaction.channel), str(interaction.user), "Via staff action code", "#5865f2")
        add_activity("👮", f"Staff action: {action}", str(interaction.user))
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to do that.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

# ============================================================
# EXTENDED ROBLOX VERIFICATION — alphanumeric codes
# ============================================================
def gen_verify_code() -> str:
    """Generate a code like YBS-A3F9K2 (letters + numbers)."""
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(random.choices(chars, k=6))
    return f"YBS-{suffix}"

# Monkey-patch VerifyModal to use the new code generator
_OrigVerifyModal = VerifyModal

class VerifyModal(Modal, title="🎮 Link Roblox Account"):
    roblox_username = TextInput(
        label="Roblox Username",
        placeholder="Enter your exact Roblox username...",
        max_length=50,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        username = self.roblox_username.value.strip()
        data = await fetch_roblox(username)
        if not data:
            return await interaction.followup.send(
                "❌ Roblox user not found! Double-check the username and try again.", ephemeral=True
            )
        code = gen_verify_code()          # <── alphanumeric
        verification_data[interaction.user.id] = {
            "code": code,
            "roblox_username": data["name"],
            "roblox_id": data["id"],
            "display": data["display"],
            "thumb": data.get("thumb"),
            "expires": (datetime.now() + timedelta(minutes=10)).isoformat(),
        }
        embed = discord.Embed(
            title="🎮 Verify Your Roblox Account",
            description=(
                f"Found **{data['display']}** (@{data['name']})\n\n"
                f"**Step 2 — Add this code to your Roblox profile bio:**\n\n"
                f"```{code}```\n"
                f"Go to **roblox.com → Profile → Edit → Bio** and paste the code, then click **Confirm**.\n"
                f"*Code expires in 10 minutes.*"
            ),
            color=0x00B2FF,
        )
        if data.get("thumb"):
            embed.set_thumbnail(url=data["thumb"])
        embed.set_footer(text="The code uses letters A-Z and digits 0-9")
        await interaction.followup.send(embed=embed, view=ConfirmVerifyView(), ephemeral=True)

# Replace old class in global scope so VerifyPanelView uses the new one
VerifyPanelView.verify_btn.callback  # just reference to ensure no breakage

# ============================================================
# PORTFOLIO WITH IMAGE SUPPORT
# ============================================================
class PortfolioModal(Modal, title="📁 Portfolio Submission"):
    description_input = TextInput(
        label="Describe your work",
        style=discord.TextStyle.paragraph,
        placeholder="Tell us about your portfolio, projects, experience...",
        max_length=1500,
    )
    links_input = TextInput(
        label="Links (URLs, one per line)",
        style=discord.TextStyle.paragraph,
        placeholder="https://...\nhttps://...",
        required=False,
        max_length=500,
    )
    image_url_input = TextInput(
        label="Image URL (screenshot / showcase image)",
        placeholder="https://i.imgur.com/... or any direct image link",
        required=False,
        max_length=300,
    )

    def __init__(self, member: discord.Member = None):
        super().__init__()
        self.member = member

    async def on_submit(self, interaction: discord.Interaction):
        submitter = self.member or interaction.user
        ch_id = get(interaction.guild.id, "applications_channel")
        ch = bot.get_channel(ch_id) if ch_id else interaction.channel
        embed = discord.Embed(
            title=f"📁 Portfolio — {submitter.display_name}",
            description=self.description_input.value,
            color=0x5865F2,
            timestamp=datetime.now(),
        )
        embed.set_author(name=str(submitter), icon_url=submitter.display_avatar.url)
        if self.links_input.value:
            embed.add_field(name="🔗 Links", value=self.links_input.value[:500], inline=False)
        if self.image_url_input.value.strip():
            try:
                embed.set_image(url=self.image_url_input.value.strip())
            except Exception:
                pass
        if submitter.id in roblox_links:
            rb = roblox_links[submitter.id]
            embed.add_field(name="🎮 Roblox", value=f"@{rb['username']}", inline=True)
        embed.set_footer(text=f"User ID: {submitter.id}")
        await ch.send(embed=embed)
        add_activity("📁", f"Portfolio submitted by {submitter.display_name}")
        await interaction.response.send_message(
            "✅ Portfolio submitted successfully! Staff will review it soon.", ephemeral=True
        )

@tree.command(name="portfolio", description="Submit your development portfolio")
async def slash_portfolio(interaction: discord.Interaction):
    await interaction.response.send_modal(PortfolioModal(interaction.user))

# ============================================================
# PREMIUM CHANNELS
# ============================================================
@tree.command(name="addpremium", description="Add a channel to the premium list [Admin]")
@app_commands.describe(channel="Channel to make premium")
async def slash_addpremium(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    gid = str(interaction.guild.id)
    premium_channels_data.setdefault(gid, [])
    if channel.id not in premium_channels_data[gid]:
        premium_channels_data[gid].append(channel.id)
    # Lock for non-premium role
    pr_role_id = get(interaction.guild.id, "verified_role")
    if pr_role_id:
        pr_role = interaction.guild.get_role(pr_role_id)
        if pr_role:
            await channel.set_permissions(interaction.guild.default_role, read_messages=False)
            await channel.set_permissions(pr_role, read_messages=True)
    await interaction.response.send_message(f"⭐ {channel.mention} is now a premium channel!", ephemeral=True)

@tree.command(name="removepremium", description="Remove a channel from the premium list [Admin]")
@app_commands.describe(channel="Channel to demote")
async def slash_removepremium(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    gid = str(interaction.guild.id)
    lst = premium_channels_data.get(gid, [])
    if channel.id in lst:
        lst.remove(channel.id)
    await channel.set_permissions(interaction.guild.default_role, overwrite=None)
    await interaction.response.send_message(f"✅ {channel.mention} is no longer premium.", ephemeral=True)

@tree.command(name="premiumlist", description="List all premium channels")
async def slash_premiumlist(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    ids = premium_channels_data.get(gid, [])
    if not ids:
        return await interaction.response.send_message("❌ No premium channels set.", ephemeral=True)
    mentions = [f"<#{cid}>" for cid in ids]
    embed = discord.Embed(title="⭐ Premium Channels", description="\n".join(mentions), color=0xFAA61A)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# /staff COMMAND — enhanced menu
# ============================================================
class StaffMenuView(View):
    """The big /staff interactive menu."""
    def __init__(self):
        super().__init__(timeout=300)

    # ── Row 0 ──
    @discord.ui.button(label="🔒 Lock Channel", style=discord.ButtonStyle.danger, row=0)
    async def lock_ch(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=False)
        await interaction.response.send_message("🔒 Channel locked.", ephemeral=True)

    @discord.ui.button(label="🔓 Unlock Channel", style=discord.ButtonStyle.success, row=0)
    async def unlock_ch(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.manage_channels:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.channel.set_permissions(interaction.guild.default_role, send_messages=None)
        await interaction.response.send_message("🔓 Channel unlocked.", ephemeral=True)

    @discord.ui.button(label="🌐 Server Lockdown", style=discord.ButtonStyle.danger, row=0)
    async def svr_lockdown(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        count = 0
        for ch in interaction.guild.channels:
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.set_permissions(interaction.guild.default_role, send_messages=False)
                    count += 1
                except Exception:
                    pass
        add_activity("🔒", f"Lockdown via /staff by {interaction.user.display_name}")
        await interaction.followup.send(f"🔒 Server locked! {count} channels affected.", ephemeral=True)

    @discord.ui.button(label="🔓 Lift Lockdown", style=discord.ButtonStyle.success, row=0)
    async def lift_lockdown(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        count = 0
        for ch in interaction.guild.channels:
            if isinstance(ch, discord.TextChannel):
                try:
                    await ch.set_permissions(interaction.guild.default_role, send_messages=None)
                    count += 1
                except Exception:
                    pass
        await interaction.followup.send(f"🔓 Lockdown lifted! {count} channels unlocked.", ephemeral=True)

    # ── Row 1 ──
    @discord.ui.button(label="🗑️ Purge 10", style=discord.ButtonStyle.secondary, row=1)
    async def purge10(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=10)
        await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

    @discord.ui.button(label="🗑️ Purge 50", style=discord.ButtonStyle.secondary, row=1)
    async def purge50(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.manage_messages:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=50)
        await interaction.followup.send(f"🗑️ Deleted {len(deleted)} messages.", ephemeral=True)

    @discord.ui.button(label="📊 Live Stats", style=discord.ButtonStyle.primary, row=1)
    async def live_stats(self, interaction: discord.Interaction, button: Button):
        g = interaction.guild
        online = sum(1 for m in g.members if m.status != discord.Status.offline and not m.bot)
        bots = sum(1 for m in g.members if m.bot)
        embed = discord.Embed(title=f"📊 {g.name} — Live Stats", color=0x5865F2)
        embed.add_field(name="👥 Members",     value=f"`{g.member_count}`")
        embed.add_field(name="🟢 Online",      value=f"`{online}`")
        embed.add_field(name="🤖 Bots",        value=f"`{bots}`")
        embed.add_field(name="⚠️ Warnings",    value=f"`{sum(len(v) for v in warnings_data.values())}`")
        embed.add_field(name="🎫 Open Tickets",value=f"`{sum(1 for t in ticket_data.values() if t.get('status')=='open')}`")
        embed.add_field(name="🎮 Roblox Linked",value=f"`{len(roblox_links)}`")
        embed.add_field(name="📋 Applications", value=f"`{len(applications_data)}`")
        embed.add_field(name="💰 Eco Users",   value=f"`{len(economy_data)}`")
        embed.add_field(name="🏅 XP Users",    value=f"`{len(xp_data)}`")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📋 Recent Apps", style=discord.ButtonStyle.primary, row=1)
    async def recent_apps_btn(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        apps = list(applications_data.values())[-8:]
        embed = discord.Embed(title="📋 Recent Applications", color=0x5865F2)
        if apps:
            embed.description = "\n".join(
                f"• **{a.get('real_name','?')}** — {a.get('role','?')} — `{a.get('status','pending')}`"
                for a in reversed(apps)
            )
        else:
            embed.description = "No applications yet."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Row 2 ──
    @discord.ui.button(label="🐛 Bug Reports", style=discord.ButtonStyle.secondary, row=2)
    async def bug_rpts(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        reports = bug_reports_data[:8]
        embed = discord.Embed(title="🐛 Recent Bug Reports", color=0xED4245)
        if reports:
            embed.description = "\n".join(
                f"• `/{r['command']}` — {r['description'][:60]} *(by {r['reporter'].split('#')[0]})*"
                for r in reports
            )
        else:
            embed.description = "No bug reports yet! 🎉"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="📝 Nickname Logs", style=discord.ButtonStyle.secondary, row=2)
    async def nick_logs(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.kick_members:
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        logs = nickname_log_data[:10]
        embed = discord.Embed(title="📝 Recent Nickname Changes", color=0xFAA61A)
        if logs:
            embed.description = "\n".join(
                f"• **{l['before']}** → **{l['after']}** — <@{l['user_id']}> `{l['time']}`"
                for l in logs
            )
        else:
            embed.description = "No nickname changes logged."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="🔐 Get Daily Code", style=discord.ButtonStyle.blurple, row=2)
    async def get_code_btn(self, interaction: discord.Interaction, button: Button):
        staff_role_id = get(interaction.guild.id, "admin_role")
        is_s = interaction.user.guild_permissions.administrator or (
            staff_role_id and staff_role_id in [r.id for r in interaction.user.roles]
        )
        if not is_s:
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)
        code = get_daily_staff_code(interaction.user.id)
        try:
            embed = discord.Embed(
                title="🔐 Daily Staff Code",
                description=f"Code: **`{code}`**\nValid today only. Use with `/staffaction`.",
                color=0x5865F2,
            )
            await interaction.user.send(embed=embed)
            await interaction.response.send_message("✅ Code sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"Your code: ||`{code}`||", ephemeral=True)

    @discord.ui.button(label="📨 DM All Members", style=discord.ButtonStyle.danger, row=2)
    async def dm_all(self, interaction: discord.Interaction, button: Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        class DmAllModal(Modal, title="📨 DM All Members"):
            subject = TextInput(label="Subject / Title", max_length=100)
            body = TextInput(label="Message Body", style=discord.TextStyle.paragraph, max_length=1500)
            async def on_submit(s2, interaction2: discord.Interaction):
                await interaction2.response.defer(ephemeral=True)
                sent = 0
                for member in interaction2.guild.members:
                    if member.bot:
                        continue
                    try:
                        embed = discord.Embed(title=s2.subject.value, description=s2.body.value, color=0x5865F2)
                        embed.set_footer(text=f"From {interaction2.guild.name} Staff")
                        await member.send(embed=embed)
                        sent += 1
                        await asyncio.sleep(0.5)  # rate limit
                    except Exception:
                        pass
                await interaction2.followup.send(f"✅ DM sent to **{sent}** members.", ephemeral=True)
                add_activity("📨", f"Mass DM by {interaction2.user.display_name}", s2.subject.value)
        await interaction.response.send_modal(DmAllModal())

@tree.command(name="staff", description="Open the full staff control panel [Staff]")
async def slash_staff(interaction: discord.Interaction):
    staff_role_id = get(interaction.guild.id, "admin_role") if interaction.guild else None
    is_s = (
        interaction.user.guild_permissions.kick_members
        or (staff_role_id and staff_role_id in [r.id for r in interaction.user.roles])
    )
    if not is_s:
        return await interaction.response.send_message("❌ Staff only!", ephemeral=True)
    embed = discord.Embed(
        title="👮 YBS Staff Control Panel",
        description=(
            "Full staff toolkit in one place.\n"
            "**Row 1:** Channel controls & lockdown\n"
            "**Row 2:** Moderation & stats\n"
            "**Row 3:** Logs, codes & DM tools"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="All actions are logged · Misuse will be reviewed")
    await interaction.response.send_message(embed=embed, view=StaffMenuView(), ephemeral=True)
    add_report("staff", interaction.user, interaction.guild)

# ============================================================
# EXTRA COMMANDS — MASSIVE EXPANSION
# ============================================================

# ── Utility ──────────────────────────────────────────────────

@tree.command(name="howlong", description="How long has a member been in the server?")
@app_commands.describe(member="Member to check")
async def slash_howlong(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    if not member.joined_at:
        return await interaction.response.send_message("❌ Unknown join date.", ephemeral=True)
    delta = datetime.now(member.joined_at.tzinfo) - member.joined_at
    days = delta.days
    years, rem = divmod(days, 365)
    months, d = divmod(rem, 30)
    parts = []
    if years:  parts.append(f"**{years}y**")
    if months: parts.append(f"**{months}mo**")
    if d:      parts.append(f"**{d}d**")
    duration = " ".join(parts) or "**<1 day**"
    embed = discord.Embed(
        title=f"📅 {member.display_name} — Server Tenure",
        description=f"Joined **{member.joined_at.strftime('%d %b %Y')}**\nIn server for {duration}",
        color=0x5865F2,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed, view=report_footer_view("howlong"))
    add_report("howlong", interaction.user, interaction.guild)

@tree.command(name="servericon", description="View the server icon")
async def slash_servericon(interaction: discord.Interaction):
    g = interaction.guild
    if not g.icon:
        return await interaction.response.send_message("❌ Server has no icon.", ephemeral=True)
    embed = discord.Embed(title=f"🖼️ {g.name} — Server Icon", color=0x5865F2)
    embed.set_image(url=g.icon.url)
    await interaction.response.send_message(embed=embed, view=report_footer_view("servericon"))

@tree.command(name="color", description="Preview a hex color")
@app_commands.describe(hex_code="Hex color code e.g. ff5733")
async def slash_color(interaction: discord.Interaction, hex_code: str):
    hex_code = hex_code.strip("#")
    try:
        r, g, b = int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
    except Exception:
        return await interaction.response.send_message("❌ Invalid hex code!", ephemeral=True)
    color_int = int(hex_code, 16)
    embed = discord.Embed(title=f"🎨 #{hex_code.upper()}", color=color_int)
    embed.add_field(name="RGB", value=f"`{r}, {g}, {b}`")
    embed.add_field(name="Hex", value=f"`#{hex_code.upper()}`")
    embed.add_field(name="Int", value=f"`{color_int}`")
    await interaction.response.send_message(embed=embed, view=report_footer_view("color"))

@tree.command(name="charcount", description="Count characters, words and lines in text")
@app_commands.describe(text="Text to analyse")
async def slash_charcount(interaction: discord.Interaction, text: str):
    embed = discord.Embed(title="🔢 Text Analysis", color=0x5865F2)
    embed.add_field(name="Characters",     value=f"`{len(text)}`")
    embed.add_field(name="Words",          value=f"`{len(text.split())}`")
    embed.add_field(name="Lines",          value=f"`{text.count(chr(10)) + 1}`")
    embed.add_field(name="Letters",        value=f"`{sum(c.isalpha() for c in text)}`")
    embed.add_field(name="Digits",         value=f"`{sum(c.isdigit() for c in text)}`")
    embed.add_field(name="Spaces",         value=f"`{text.count(' ')}`")
    await interaction.response.send_message(embed=embed, view=report_footer_view("charcount"))

@tree.command(name="b64", description="Base64 encode or decode text")
@app_commands.describe(text="Text to encode/decode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Encode", value="encode"),
    app_commands.Choice(name="Decode", value="decode"),
])
async def slash_b64(interaction: discord.Interaction, mode: str, text: str):
    import base64
    try:
        if mode == "encode":
            result = base64.b64encode(text.encode()).decode()
        else:
            result = base64.b64decode(text.encode()).decode()
        embed = discord.Embed(title=f"🔐 Base64 {mode.title()}", color=0x5865F2)
        embed.add_field(name="Input",  value=f"```{text[:200]}```", inline=False)
        embed.add_field(name="Output", value=f"```{result[:500]}```", inline=False)
        await interaction.response.send_message(embed=embed, view=report_footer_view("b64"))
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

@tree.command(name="timestamp", description="Convert a date/time to a Discord timestamp")
@app_commands.describe(date="Date in DD/MM/YYYY format", time_input="Time in HH:MM 24h format (optional)")
async def slash_timestamp(interaction: discord.Interaction, date: str, time_input: str = "00:00"):
    try:
        dt = datetime.strptime(f"{date} {time_input}", "%d/%m/%Y %H:%M")
        ts = int(dt.timestamp())
        embed = discord.Embed(title="⏰ Discord Timestamps", color=0x5865F2)
        formats = [
            ("<t:{ts}>",   "Default"),
            ("<t:{ts}:f>", "Long Date+Time"),
            ("<t:{ts}:D>", "Long Date"),
            ("<t:{ts}:T>", "Long Time"),
            ("<t:{ts}:R>", "Relative"),
        ]
        for fmt, label in formats:
            code = fmt.format(ts=ts)
            embed.add_field(name=label, value=f"`{code}` → {code}", inline=False)
        await interaction.response.send_message(embed=embed, view=report_footer_view("timestamp"))
    except ValueError:
        await interaction.response.send_message("❌ Invalid date/time format! Use DD/MM/YYYY and HH:MM.", ephemeral=True)

@tree.command(name="reverse", description="Reverse some text")
@app_commands.describe(text="Text to reverse")
async def slash_reverse(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(f"🔄 {text[::-1]}", view=report_footer_view("reverse"))

@tree.command(name="piglatin", description="Convert text to Pig Latin")
@app_commands.describe(text="Text to convert")
async def slash_piglatin(interaction: discord.Interaction, text: str):
    def word_to_pig(word):
        vowels = "aeiouAEIOU"
        if word[0] in vowels:
            return word + "yay"
        i = next((idx for idx, c in enumerate(word) if c in vowels), len(word))
        return word[i:] + word[:i] + "ay"
    result = " ".join(word_to_pig(w) for w in text.split() if w)
    await interaction.response.send_message(f"🐷 {result}", view=report_footer_view("piglatin"))

@tree.command(name="wouldyou", description="Would you rather question")
async def slash_wouldyou(interaction: discord.Interaction):
    questions = [
        ("build an entire Roblox game solo in 24 hours", "work in a team of 10 for 1 week"),
        ("script everything and never build", "build everything and never script"),
        ("have 1000 Robux right now", "have 100 Robux every day for a year"),
        ("only work on obby games forever", "only work on simulators forever"),
        ("have infinite Roblox RAM but low salary", "have high salary but always hit memory limits"),
        ("use only free models", "build everything yourself from scratch"),
        ("publish a game that goes viral but was rushed", "perfect a game for 2 years with 10 players"),
    ]
    a, b = random.choice(questions)
    embed = discord.Embed(title="🤔 Would You Rather…", color=0xFF79C6)
    embed.add_field(name="🅰️ Option A", value=a, inline=False)
    embed.add_field(name="🅱️ Option B", value=b, inline=False)
    embed.set_footer(text="React with 🅰️ or 🅱️!")
    msg = await interaction.channel.send(embed=embed)
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")
    await interaction.response.send_message("✅ Posted!", ephemeral=True)

@tree.command(name="trivia", description="Answer a Roblox dev trivia question")
async def slash_trivia(interaction: discord.Interaction):
    questions = [
        {"q": "What scripting language does Roblox use?", "a": "luau", "choices": ["Python", "JavaScript", "Luau", "C++"], "correct": 2},
        {"q": "What does `game.Players.LocalPlayer` return?", "a": "the local player", "choices": ["All players", "The server", "The local player", "Nothing"], "correct": 2},
        {"q": "Which service is used to store persistent data?", "a": "datastore", "choices": ["DataStoreService", "PlayerDataService", "SaveService", "MemoryService"], "correct": 0},
        {"q": "What is the parent of all parts by default?", "a": "workspace", "choices": ["Lighting", "Workspace", "StarterPack", "ReplicatedStorage"], "correct": 1},
        {"q": "What does `RunService.Heartbeat` fire?", "a": "every frame", "choices": ["Every second", "Every minute", "Every frame", "Once"], "correct": 2},
        {"q": "Which object is used to connect scripts across server/client?", "a": "remoteevent", "choices": ["Signal", "RemoteEvent", "ModuleScript", "BindableEvent"], "correct": 1},
    ]
    q = random.choice(questions)
    embed = discord.Embed(title="🎯 Roblox Dev Trivia", description=f"**{q['q']}**", color=0x00B2FF)
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
    for i, ch in enumerate(q["choices"]):
        embed.add_field(name=f"{emojis[i]} Option {i+1}", value=ch, inline=True)
    embed.set_footer(text="React with the emoji of your answer!")
    msg = await interaction.channel.send(embed=embed)
    for e in emojis[:len(q["choices"])]:
        await msg.add_reaction(e)

    async def reveal():
        await asyncio.sleep(15)
        reveal_embed = discord.Embed(
            title="✅ Answer Revealed!",
            description=f"**{q['q']}**\n\n✅ Correct answer: **{q['choices'][q['correct']]}**",
            color=0x3BA55C,
        )
        await interaction.channel.send(embed=reveal_embed)
    asyncio.create_task(reveal())
    await interaction.response.send_message("✅ Trivia posted! Answer in 15 seconds…", ephemeral=True)

# ── Fun ──────────────────────────────────────────────────────

@tree.command(name="roll", description="Roll a dice with custom sides and a leaderboard display")
@app_commands.describe(sides="Sides on the dice", rolls="Number of rolls")
async def slash_roll(interaction: discord.Interaction, sides: int = 6, rolls: int = 1):
    sides = max(2, min(1000, sides))
    rolls = max(1, min(20, rolls))
    results = [random.randint(1, sides) for _ in range(rolls)]
    embed = discord.Embed(title=f"🎲 {rolls}d{sides}", color=0x5865F2)
    embed.add_field(name="Rolls", value=" · ".join(f"**{r}**" for r in results), inline=False)
    if rolls > 1:
        embed.add_field(name="Total",   value=f"**{sum(results)}**")
        embed.add_field(name="Average", value=f"**{sum(results)/rolls:.1f}**")
        embed.add_field(name="Highest", value=f"**{max(results)}**")
        embed.add_field(name="Lowest",  value=f"**{min(results)}**")
    await interaction.response.send_message(embed=embed, view=report_footer_view("roll"))

@tree.command(name="quote", description="Get an inspirational dev quote")
async def slash_quote(interaction: discord.Interaction):
    quotes = [
        ("First, solve the problem. Then, write the code.", "John Johnson"),
        ("Any fool can write code a computer understands. Good programmers write code humans can understand.", "Martin Fowler"),
        ("The best error message is the one that never shows up.", "Thomas Fuchs"),
        ("It works on my machine.", "Every Developer Ever"),
        ("Debugging is twice as hard as writing the code in the first place.", "Brian Kernighan"),
        ("The most dangerous phrase is: 'It's always been done that way.'", "Grace Hopper"),
        ("Code never lies; comments sometimes do.", "Ron Jeffries"),
        ("Make it work, make it right, make it fast.", "Kent Beck"),
    ]
    text, author = random.choice(quotes)
    embed = discord.Embed(
        title="💬 Dev Quote",
        description=f"*\"{text}\"*\n\n— **{author}**",
        color=0x5865F2,
    )
    await interaction.response.send_message(embed=embed, view=report_footer_view("quote"))

@tree.command(name="meme", description="Get a random Roblox/dev meme text")
async def slash_meme(interaction: discord.Interaction):
    memes = [
        ("When you finally fix the bug", "But introduce 3 new ones 🐛"),
        ("Me: I'll just add one more feature", "5 hours later… 😭"),
        ("The game: This should work", "Production: 💀"),
        ("Free model: Trust me bro", "Security vulnerabilities: 👀"),
        ("Scripter: It's a simple fix", "*4 hours and 200 lines later*"),
        ("When the build looks perfect in Studio", "But breaks in-game 💔"),
        ("Client: Can you add this feature?", "Me internally: *screams in Lua*"),
        ("One does not simply", "Publish a game without testing 🧙‍♂️"),
    ]
    top, bottom = random.choice(memes)
    embed = discord.Embed(color=0xFAA61A)
    embed.add_field(name="😂 Meme", value=f"**{top}**\n\n*{bottom}*", inline=False)
    await interaction.response.send_message(embed=embed, view=report_footer_view("meme"))

@tree.command(name="define", description="Define a Roblox/dev term")
@app_commands.describe(term="Term to define")
async def slash_define(interaction: discord.Interaction, term: str):
    definitions = {
        "luau":     "Roblox's custom Lua scripting language with type annotations and improved performance.",
        "remotevent": "An object that allows communication between the server and client in Roblox.",
        "datastore":"A service for saving and loading persistent player data across sessions.",
        "modulescript": "A script type that returns a value (usually a table) to be reused across other scripts.",
        "heartbeat":"A RunService event that fires every frame on the client — great for animations.",
        "workspace": "The root container for all 3D objects in a Roblox place.",
        "baseplate": "The default flat platform in a new Roblox Studio place.",
        "obby":     "Short for 'obstacle course' — one of the most popular Roblox game genres.",
        "r6":       "A Roblox avatar rig with 6 body parts; simpler than R15.",
        "r15":      "A Roblox avatar rig with 15 body parts; supports more animations.",
        "gui":      "Graphical User Interface — in-game UI elements like buttons and frames.",
        "tween":    "An animation technique that smoothly transitions an object between two states.",
        "pcall":    "Protected call — runs code safely and catches errors without crashing the script.",
        "coroutine":"A way to run code concurrently in Luau without blocking the main thread.",
        "debounce": "A programming pattern that prevents a function from being called too frequently.",
    }
    key = term.lower().replace(" ", "")
    defn = definitions.get(key)
    embed = discord.Embed(title=f"📖 Definition — {term}", color=0x00B2FF)
    embed.description = defn or f"No definition found for **{term}**. Try a Roblox/dev term!"
    await interaction.response.send_message(embed=embed, view=report_footer_view("define"))

@tree.command(name="ascii", description="Convert text to ASCII art")
@app_commands.describe(text="Text (max 8 characters)")
async def slash_ascii(interaction: discord.Interaction, text: str):
    if len(text) > 8:
        return await interaction.response.send_message("❌ Max 8 characters for ASCII art.", ephemeral=True)
    # Simple block-letter style using Unicode full-width
    result = ''.join(chr(ord(c) + 0xFEE0) if '!' <= c <= '~' else c for c in text.upper())
    await interaction.response.send_message(f"```\n{result}\n```", view=report_footer_view("ascii"))

@tree.command(name="clap", description="Add 👏 claps 👏 between 👏 words")
@app_commands.describe(text="Text to clap-ify")
async def slash_clap(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(" 👏 ".join(text.split()), view=report_footer_view("clap"))

@tree.command(name="uwu", description="UwUify your text")
@app_commands.describe(text="Text to uwuify")
async def slash_uwu(interaction: discord.Interaction, text: str):
    result = (
        text.replace("r", "w").replace("l", "w")
            .replace("R", "W").replace("L", "W")
            .replace("na", "nya").replace("Nu", "Nyu")
            .replace("ni", "nyi").replace("no", "nyo")
            .replace("ne", "nye")
    )
    await interaction.response.send_message(f"OwO {result} UwU", view=report_footer_view("uwu"))

@tree.command(name="emojify", description="Turn text into emoji letters")
@app_commands.describe(text="Text to emojify (letters only)")
async def slash_emojify(interaction: discord.Interaction, text: str):
    mapping = {c: f":regional_indicator_{c}:" for c in "abcdefghijklmnopqrstuvwxyz"}
    result = " ".join(mapping.get(c.lower(), c) for c in text)
    if len(result) > 1900:
        return await interaction.response.send_message("❌ Text too long!", ephemeral=True)
    await interaction.response.send_message(result, view=report_footer_view("emojify"))

@tree.command(name="challenge", description="Get a random dev challenge for today")
async def slash_challenge(interaction: discord.Interaction):
    challenges = [
        "Build a working vending machine GUI in Roblox Studio.",
        "Script a part that changes colour every second using a sine wave.",
        "Create a leaderboard that persists after rejoining using DataStores.",
        "Make a custom chat system with emoji reactions.",
        "Build a tycoon dropper and collecter in under 2 hours.",
        "Script a proximity-prompt door that unlocks only for certain roles.",
        "Build a parkour course with 10 unique obstacles.",
        "Create a mini-game selector lobby with teleport pads.",
        "Make a day/night cycle with dynamic lighting.",
        "Script an inventory system that saves to a DataStore.",
    ]
    embed = discord.Embed(
        title="🏆 Today's Dev Challenge",
        description=random.choice(challenges),
        color=0xFAA61A,
    )
    embed.set_footer(text="Share your result in the server! 🚀")
    await interaction.response.send_message(embed=embed, view=report_footer_view("challenge"))

@tree.command(name="inspireme", description="Get inspired with a random project idea")
async def slash_inspireme(interaction: discord.Interaction):
    ideas = [
        "A survival game where players build shelters and craft tools.",
        "A racing game with custom car physics and upgrades.",
        "A tower defence with waves of NPC enemies.",
        "A detective mystery where players solve crimes together.",
        "A restaurant tycoon with realistic cooking mechanics.",
        "A space exploration game with procedurally generated planets.",
        "A battle royale on a shrinking island.",
        "A music game where players play mini piano challenges.",
        "A horror escape room with jumpscares and puzzles.",
        "A shop simulator where you manage inventory and prices.",
    ]
    embed = discord.Embed(
        title="💡 Game Idea Inspiration",
        description=f"**Build this:** {random.choice(ideas)}",
        color=0x00B2FF,
    )
    embed.set_footer(text="Go build it! Young Boy Studios is counting on you 🚀")
    await interaction.response.send_message(embed=embed, view=report_footer_view("inspireme"))

@tree.command(name="numfact", description="Random number fact")
@app_commands.describe(number="Number to get a fact about (leave blank for random)")
async def slash_numfact(interaction: discord.Interaction, number: int = None):
    if number is None:
        number = random.randint(1, 100)
    facts = {
        1: "1 is the only positive integer that is neither prime nor composite.",
        2: "2 is the only even prime number.",
        7: "7 is considered the luckiest number in many cultures.",
        42: "42 is the 'Answer to the Ultimate Question of Life, the Universe, and Everything'.",
        0: "0 is neither positive nor negative and was invented separately in different cultures.",
        100: "100 is a perfect square (10²) and the basis of the percentage system.",
        69: "69 is a strobogrammatic number — it looks the same upside-down.",
        256: "256 = 2⁸. Every Roblox dev knows this one from byte limits!",
        1337: "1337 is 'leet speak' for LEET — a form of internet slang.",
    }
    fact = facts.get(number, f"{number} is {'even' if number % 2 == 0 else 'odd'} and {'prime' if all(number % i != 0 for i in range(2, int(number**0.5)+1)) and number > 1 else 'composite'}.")
    embed = discord.Embed(title=f"🔢 Number Fact — {number}", description=fact, color=0x5865F2)
    await interaction.response.send_message(embed=embed, view=report_footer_view("numfact"))

# ── Server Utility ────────────────────────────────────────────

@tree.command(name="inviteinfo", description="View info about an invite link")
@app_commands.describe(code="Invite code or full link")
async def slash_inviteinfo(interaction: discord.Interaction, code: str):
    code = code.split("/")[-1]
    try:
        invite = await bot.fetch_invite(code, with_counts=True)
        embed = discord.Embed(title=f"🔗 Invite — {code}", color=0x5865F2)
        if invite.guild:
            embed.add_field(name="Server", value=invite.guild.name)
            embed.add_field(name="Members", value=f"{invite.approximate_member_count}")
        if invite.channel:
            embed.add_field(name="Channel", value=f"#{invite.channel.name}")
        if invite.inviter:
            embed.add_field(name="Created By", value=str(invite.inviter))
        await interaction.response.send_message(embed=embed, view=report_footer_view("inviteinfo"))
    except Exception:
        await interaction.response.send_message("❌ Invalid invite or invite expired.", ephemeral=True)

@tree.command(name="emojilist", description="List all custom emojis in the server")
async def slash_emojilist(interaction: discord.Interaction):
    emojis = interaction.guild.emojis
    if not emojis:
        return await interaction.response.send_message("❌ No custom emojis.", ephemeral=True)
    embed = discord.Embed(title=f"😀 Custom Emojis — {len(emojis)}", color=0x5865F2)
    chunks = [emojis[i:i+20] for i in range(0, min(len(emojis), 60), 20)]
    for i, chunk in enumerate(chunks):
        embed.add_field(
            name=f"Emojis {i*20+1}–{i*20+len(chunk)}",
            value=" ".join(str(e) for e in chunk),
            inline=False,
        )
    await interaction.response.send_message(embed=embed, view=report_footer_view("emojilist"))

@tree.command(name="boosters", description="View all server boosters")
async def slash_boosters(interaction: discord.Interaction):
    boosters = [m for m in interaction.guild.premium_subscribers if not m.bot]
    embed = discord.Embed(
        title=f"💎 Server Boosters — {len(boosters)}",
        color=0xFF73FA,
    )
    if boosters:
        embed.description = "\n".join(f"💎 **{m.display_name}**" for m in boosters[:25])
    else:
        embed.description = "No boosters yet. Be the first!"
    await interaction.response.send_message(embed=embed, view=report_footer_view("boosters"))

@tree.command(name="channelinfo", description="View info about a channel")
@app_commands.describe(channel="Channel to inspect")
async def slash_channelinfo(interaction: discord.Interaction, channel: discord.TextChannel = None):
    ch = channel or interaction.channel
    embed = discord.Embed(title=f"📋 #{ch.name}", color=0x5865F2)
    embed.add_field(name="ID",       value=f"`{ch.id}`")
    embed.add_field(name="Category", value=ch.category.name if ch.category else "None")
    embed.add_field(name="Created",  value=ch.created_at.strftime("%d %b %Y"))
    embed.add_field(name="NSFW",     value="✅" if ch.is_nsfw() else "❌")
    embed.add_field(name="Slowmode", value=f"{ch.slowmode_delay}s")
    embed.add_field(name="Position", value=f"#{ch.position}")
    if ch.topic:
        embed.add_field(name="Topic", value=ch.topic[:200], inline=False)
    await interaction.response.send_message(embed=embed, view=report_footer_view("channelinfo"))

@tree.command(name="botinvite", description="Get a link to invite the bot to another server")
async def slash_botinvite(interaction: discord.Interaction):
    if not bot.user:
        return await interaction.response.send_message("❌ Bot not ready.", ephemeral=True)
    perms = discord.Permissions(administrator=True)
    url = discord.utils.oauth_url(bot.user.id, permissions=perms)
    embed = discord.Embed(
        title="🤖 Invite YBS Bot",
        description=f"[Click here to invite the bot]({url})",
        color=0x5865F2,
    )
    await interaction.response.send_message(embed=embed, view=report_footer_view("botinvite"))

@tree.command(name="searchmember", description="Search for a member by name")
@app_commands.describe(query="Name or username to search")
async def slash_searchmember(interaction: discord.Interaction, query: str):
    results = [
        m for m in interaction.guild.members
        if query.lower() in m.display_name.lower() or query.lower() in m.name.lower()
    ][:10]
    embed = discord.Embed(title=f"🔍 Member Search — \"{query}\"", color=0x5865F2)
    if results:
        embed.description = "\n".join(f"• **{m.display_name}** (`{m.id}`)" for m in results)
    else:
        embed.description = "No members found."
    await interaction.response.send_message(embed=embed, view=report_footer_view("searchmember"))

@tree.command(name="weather", description="Get simple weather info (powered by open-meteo)")
@app_commands.describe(city="City name")
async def slash_weather(interaction: discord.Interaction, city: str):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1")
            geo = await r.json()
            if not geo.get("results"):
                return await interaction.followup.send(f"❌ City **{city}** not found.", ephemeral=True)
            loc = geo["results"][0]
            lat, lon = loc["latitude"], loc["longitude"]
            r2 = await s.get(
                f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                "&current_weather=true&hourly=relativehumidity_2m&timezone=auto"
            )
            wdata = await r2.json()
            cw = wdata.get("current_weather", {})
            temp = cw.get("temperature", "?")
            wind = cw.get("windspeed", "?")
            code = cw.get("weathercode", 0)
            icons = {0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️", 45: "🌫️", 51: "🌦️", 61: "🌧️", 71: "❄️", 80: "🌧️", 95: "⛈️"}
            icon = next((v for k, v in icons.items() if code >= k), "🌡️")
            embed = discord.Embed(title=f"{icon} Weather — {loc['name']}, {loc.get('country','')}", color=0x00B2FF)
            embed.add_field(name="🌡️ Temperature", value=f"**{temp}°C**")
            embed.add_field(name="💨 Wind Speed",  value=f"**{wind} km/h**")
            embed.set_footer(text="Powered by Open-Meteo · Data may be approximate")
            await interaction.followup.send(embed=embed, view=report_footer_view("weather"))
    except Exception as e:
        await interaction.followup.send(f"❌ Couldn't fetch weather: {e}", ephemeral=True)

@tree.command(name="urban", description="Look up a term on Urban Dictionary")
@app_commands.describe(term="Term to look up")
async def slash_urban(interaction: discord.Interaction, term: str):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"https://api.urbandictionary.com/v0/define?term={term}")
            data = await r.json()
            entries = data.get("list", [])
            if not entries:
                return await interaction.followup.send(f"❌ No definition found for **{term}**.", ephemeral=True)
            top = entries[0]
            definition = top["definition"][:800].replace("[", "").replace("]", "")
            example = top.get("example", "")[:300].replace("[", "").replace("]", "")
            embed = discord.Embed(title=f"📖 {top['word']}", url=top.get("permalink",""), color=0xFAA61A)
            embed.add_field(name="Definition", value=definition or "—", inline=False)
            if example:
                embed.add_field(name="Example", value=f"*{example}*", inline=False)
            embed.add_field(name="👍", value=str(top.get("thumbs_up", 0)))
            embed.add_field(name="👎", value=str(top.get("thumbs_down", 0)))
            embed.set_footer(text="Urban Dictionary · May contain mature content")
            await interaction.followup.send(embed=embed, view=report_footer_view("urban"))
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

@tree.command(name="math", description="Evaluate a math expression safely")
@app_commands.describe(expression="Math expression e.g. (5+3)*2")
async def slash_math(interaction: discord.Interaction, expression: str):
    allowed = set("0123456789+-*/.() ")
    if not all(c in allowed for c in expression):
        return await interaction.response.send_message("❌ Only basic math operators!", ephemeral=True)
    try:
        result = eval(expression, {"__builtins__": {}})
        embed = discord.Embed(title="🧮 Math Result", color=0x5865F2)
        embed.add_field(name="Expression", value=f"`{expression}`")
        embed.add_field(name="Result",     value=f"**{result}**")
        await interaction.response.send_message(embed=embed, view=report_footer_view("math"))
    except Exception:
        await interaction.response.send_message("❌ Invalid expression!", ephemeral=True)

@tree.command(name="qr", description="Generate a QR code for a URL")
@app_commands.describe(url="URL to encode")
async def slash_qr(interaction: discord.Interaction, url: str):
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={url}"
    embed = discord.Embed(title="📱 QR Code", description=f"URL: {url}", color=0x5865F2)
    embed.set_image(url=qr_url)
    await interaction.response.send_message(embed=embed, view=report_footer_view("qr"))

@tree.command(name="country", description="Get info about a country")
@app_commands.describe(name="Country name")
async def slash_country(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.get(f"https://restcountries.com/v3.1/name/{name}?fullText=false")
            data = await r.json()
            if not data or isinstance(data, dict):
                return await interaction.followup.send(f"❌ Country **{name}** not found.", ephemeral=True)
            c = data[0]
            embed = discord.Embed(
                title=f"🌍 {c['name']['common']} {c.get('flag','')}",
                color=0x5865F2,
            )
            embed.add_field(name="Capital",    value=", ".join(c.get("capital", ["?"])))
            embed.add_field(name="Region",     value=c.get("region", "?"))
            embed.add_field(name="Population", value=f"{c.get('population',0):,}")
            embed.add_field(name="Languages",  value=", ".join(c.get("languages", {}).values())[:200] or "?")
            embed.add_field(name="Currency",   value=", ".join(c.get("currencies", {}).keys()) or "?")
            embed.add_field(name="TLD",        value=", ".join(c.get("tld", ["?"])))
            await interaction.followup.send(embed=embed, view=report_footer_view("country"))
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}", ephemeral=True)

# ── Leaderboard additions ─────────────────────────────────────

@tree.command(name="xptop", description="Detailed XP leaderboard (top 15)")
async def slash_xptop(interaction: discord.Interaction):
    if not xp_data:
        return await interaction.response.send_message("❌ No XP data yet!", ephemeral=True)
    top = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:15]
    medals = ["🥇", "🥈", "🥉"] + [f"{i+1}." for i in range(3, 15)]
    embed = discord.Embed(title="🏅 XP Leaderboard — Top 15", color=0x5865F2)
    lines = []
    for i, (uid, d) in enumerate(top):
        lv = get_level(d["xp"])
        name = d.get("name", str(uid)).split("#")[0][:20]
        lines.append(f"{medals[i]} **{name}** — Lv **{lv}** · `{d['xp']} XP`")
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, view=report_footer_view("xptop"))

@tree.command(name="cointop", description="Detailed coin leaderboard (top 15)")
async def slash_cointop(interaction: discord.Interaction):
    if not economy_data:
        return await interaction.response.send_message("❌ No economy data yet!", ephemeral=True)
    top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:15]
    medals = ["🥇", "🥈", "🥉"] + [f"{i+1}." for i in range(3, 15)]
    embed = discord.Embed(title="💰 Coin Leaderboard — Top 15", color=0xFAA61A)
    lines = []
    for i, (uid, d) in enumerate(top):
        name = d.get("name", str(uid)).split("#")[0][:20]
        lines.append(f"{medals[i]} **{name}** — `{d['balance']:,}` coins")
    embed.description = "\n".join(lines)
    await interaction.response.send_message(embed=embed, view=report_footer_view("cointop"))

# ── Profile card ─────────────────────────────────────────────

@tree.command(name="profile", description="View your full YBS profile")
@app_commands.describe(member="Member to view")
async def slash_profile(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    xd   = xp_data.get(member.id, {"xp": 0, "messages": 0})
    eco  = economy_data.get(member.id, {"balance": 0, "total_earned": 0})
    rb   = roblox_links.get(member.id)
    warns = len(warnings_data.get(member.id, []))
    lv   = get_level(xd["xp"])
    nxt  = xp_for_level(lv + 1)
    prv  = xp_for_level(lv)
    pct  = int((xd["xp"] - prv) / max(nxt - prv, 1) * 100)
    bar  = "█" * (pct // 10) + "░" * (10 - pct // 10)
    pos  = next(
        (i + 1 for i, (uid, _) in enumerate(sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)) if uid == member.id),
        "?",
    )
    embed = discord.Embed(title=f"🎮 {member.display_name}'s Profile", color=member.color or 0x5865F2)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="📅 Joined",    value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?", inline=True)
    embed.add_field(name="🏅 Level",     value=f"**{lv}**", inline=True)
    embed.add_field(name="📊 XP Rank",  value=f"**#{pos}**", inline=True)
    embed.add_field(name="💬 Messages",  value=f"`{xd.get('messages',0)}`", inline=True)
    embed.add_field(name="💰 Balance",   value=f"`{eco['balance']:,}` coins", inline=True)
    embed.add_field(name="⚠️ Warnings",  value=f"`{warns}`", inline=True)
    embed.add_field(name="📈 Progress",  value=f"`{bar}` {pct}% to Lv {lv+1}", inline=False)
    if rb:
        embed.add_field(name="🎮 Roblox", value=f"@{rb['username']} {'✅' if rb.get('verified') else ''}", inline=True)
    await interaction.response.send_message(embed=embed, view=report_footer_view("profile"))
    add_report("profile", interaction.user, interaction.guild)

# ── Moderation extras ─────────────────────────────────────────

@tree.command(name="warncount", description="Quick warning count for all warned members [Staff]")
async def slash_warncount(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not warnings_data:
        return await interaction.response.send_message("✅ No warnings on record!", ephemeral=True)
    sorted_warns = sorted(warnings_data.items(), key=lambda x: len(x[1]), reverse=True)[:15]
    embed = discord.Embed(title="⚠️ Warning Counts", color=0xFAA61A)
    lines = []
    for uid, warns in sorted_warns:
        member = interaction.guild.get_member(uid)
        name = member.display_name if member else f"User {uid}"
        lines.append(f"• **{name}** — `{len(warns)}` warning(s)")
    embed.description = "\n".join(lines) or "None."
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="clearnotes", description="Clear all staff notes for a member [Staff]")
@app_commands.describe(member="Member")
async def slash_clearnotes(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    count = len(notes_data.pop(str(member.id), []))
    await interaction.response.send_message(f"✅ Cleared **{count}** note(s) for **{member}**.", ephemeral=True)

@tree.command(name="banlist", description="View the ban log [Staff]")
async def slash_banlist(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not ban_log_data:
        return await interaction.response.send_message("✅ Ban log is empty.", ephemeral=True)
    embed = discord.Embed(title="🔨 Ban Log", color=0xED4245)
    embed.description = "\n".join(
        f"• **{e['user']}** — {e.get('reason','?')[:50]} `{e.get('time','?')}`"
        for e in ban_log_data[-15:]
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="checkinvites", description="Check how many invites a member has created")
@app_commands.describe(member="Member")
async def slash_checkinvites(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    try:
        invites = await interaction.guild.invites()
        user_invites = [i for i in invites if i.inviter and i.inviter.id == member.id]
        total_uses   = sum(i.uses for i in user_invites)
        embed = discord.Embed(title=f"🔗 Invites — {member.display_name}", color=0x5865F2)
        embed.add_field(name="Active Invites", value=f"`{len(user_invites)}`")
        embed.add_field(name="Total Uses",     value=f"`{total_uses}`")
        if user_invites:
            embed.add_field(
                name="Invite Links",
                value="\n".join(f"`{i.code}` — {i.uses} uses" for i in user_invites[:5]),
                inline=False,
            )
        await interaction.response.send_message(embed=embed, view=report_footer_view("checkinvites"))
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions to view invites.", ephemeral=True)

@tree.command(name="recentbans", description="View recent server bans [Staff]")
async def slash_recentbans(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    try:
        bans = [entry async for entry in interaction.guild.bans()]
        embed = discord.Embed(title=f"🔨 Current Bans — {len(bans)}", color=0xED4245)
        if bans:
            embed.description = "\n".join(
                f"• **{e.user}** — {(e.reason or 'No reason')[:60]}"
                for e in bans[:15]
            )
        else:
            embed.description = "No active bans."
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

# ── Economy extras ────────────────────────────────────────────

@tree.command(name="shop", description="View the YBS coin shop")
async def slash_shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛒 YBS Coin Shop",
        description=(
            "Spend your coins on server perks!\n"
            "*(Contact staff to redeem after purchasing)*"
        ),
        color=0xFAA61A,
    )
    items = [
        ("⭐ Custom Nickname", "2,000 coins",  "Get any nickname you want for 7 days"),
        ("🎨 Custom Role Color","5,000 coins", "Custom colour for your role for 30 days"),
        ("📢 Announcement Shoutout","3,000 coins","Get a shoutout in announcements"),
        ("🎮 Game Tester Early Access","1,500 coins","Test YBS games before public release"),
        ("💎 Premium Channel Access","4,000 coins","7 days access to premium channels"),
        ("🎉 Giveaway Entry Bonus","500 coins","2x entries in the next giveaway"),
    ]
    for name, cost, desc in items:
        embed.add_field(name=f"{name} — {cost}", value=desc, inline=False)
    await interaction.response.send_message(embed=embed, view=report_footer_view("shop"))

@tree.command(name="coinflip2", description="Coin flip bet — call it and double or lose")
@app_commands.describe(amount="Bet amount", call="Heads or Tails")
@app_commands.choices(call=[
    app_commands.Choice(name="Heads", value="heads"),
    app_commands.Choice(name="Tails", value="tails"),
])
async def slash_coinflip2(interaction: discord.Interaction, amount: int, call: str):
    if amount <= 0:
        return await interaction.response.send_message("❌ Positive amount only.", ephemeral=True)
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < amount:
        return await interaction.response.send_message(f"❌ Only **{eco['balance']:,} coins**.", ephemeral=True)
    result = random.choice(["heads", "tails"])
    won = call == result
    if won:
        eco["balance"] += amount
        eco["total_earned"] += amount
        msg = f"🪙 **{result.upper()}!** You called it! Won **{amount:,} coins!** 🎉"
    else:
        eco["balance"] -= amount
        msg = f"🪙 **{result.upper()}!** You called {call}. Lost **{amount:,} coins**. 😢"
    embed = discord.Embed(description=msg, color=0x3BA55C if won else 0xED4245)
    embed.set_footer(text=f"Balance: {eco['balance']:,} coins")
    await interaction.response.send_message(embed=embed, view=report_footer_view("coinflip2"))

@tree.command(name="blackjack", description="Play a simplified blackjack game")
@app_commands.describe(bet="Coins to bet")
async def slash_blackjack(interaction: discord.Interaction, bet: int = 100):
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < bet:
        return await interaction.response.send_message(f"❌ Only **{eco['balance']:,} coins**.", ephemeral=True)
    if bet <= 0:
        return await interaction.response.send_message("❌ Positive bet required.", ephemeral=True)
    deck = [2,3,4,5,6,7,8,9,10,10,10,10,11] * 4
    random.shuffle(deck)
    player = [deck.pop(), deck.pop()]
    dealer = [deck.pop(), deck.pop()]

    def hand_val(hand):
        v = sum(hand)
        aces = hand.count(11)
        while v > 21 and aces:
            v -= 10
            aces -= 1
        return v

    pv = hand_val(player)
    dv = hand_val(dealer)

    if pv == 21:
        win = int(bet * 1.5)
        eco["balance"] += win
        eco["total_earned"] += win
        result_text = f"🃏 **BLACKJACK!** You won **{win:,} coins!**"
    elif dv == 21:
        eco["balance"] -= bet
        result_text = f"😱 Dealer **BLACKJACK!** You lost **{bet:,} coins**."
    else:
        # Simulate dealer hitting until 17+
        while dv < 17:
            dealer.append(deck.pop())
            dv = hand_val(dealer)
        if pv > 21:
            eco["balance"] -= bet
            result_text = f"💥 **Bust!** You lost **{bet:,} coins**."
        elif dv > 21 or pv > dv:
            eco["balance"] += bet
            eco["total_earned"] += bet
            result_text = f"🎉 **You win!** Dealer had {dv}. Won **{bet:,} coins!**"
        elif pv == dv:
            result_text = f"🤝 **Push!** Tie at {pv}. Bet returned."
        else:
            eco["balance"] -= bet
            result_text = f"😢 **Dealer wins** ({dv} vs {pv}). Lost **{bet:,} coins**."

    embed = discord.Embed(title="🃏 Blackjack", color=0x5865F2)
    embed.add_field(name=f"Your Hand ({pv})",   value=" ".join(str(c) for c in player))
    embed.add_field(name=f"Dealer Hand ({dv})",  value=" ".join(str(c) for c in dealer))
    embed.add_field(name="Result", value=result_text, inline=False)
    embed.set_footer(text=f"Balance: {eco['balance']:,} coins")
    await interaction.response.send_message(embed=embed, view=report_footer_view("blackjack"))

@tree.command(name="heist", description="Start a server heist — everyone can join! [Staff]")
@app_commands.describe(pot="Starting pot in coins")
async def slash_heist(interaction: discord.Interaction, pot: int = 1000):
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    participants: list = []
    embed = discord.Embed(
        title="🏦 SERVER HEIST!",
        description=f"A heist is being planned! Starting pot: **{pot:,} coins**\nClick **Join Heist** before time runs out!",
        color=0xED4245,
    )
    embed.set_footer(text="Starts in 60 seconds · The more robbers, the bigger the split!")

    class HeistView(View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="🤝 Join Heist", style=discord.ButtonStyle.danger)
        async def join(self, intr: discord.Interaction, button: Button):
            if intr.user.id in [p[0] for p in participants]:
                return await intr.response.send_message("✅ You're already in!", ephemeral=True)
            eco = get_economy(intr.user.id, str(intr.user))
            if eco["balance"] < 100:
                return await intr.response.send_message("❌ Need at least 100 coins to join.", ephemeral=True)
            eco["balance"] -= 100
            participants.append((intr.user.id, str(intr.user)))
            await intr.response.send_message(f"✅ You joined the heist! **{len(participants)}** robbers so far.", ephemeral=True)

    msg = await interaction.channel.send(embed=embed, view=HeistView())
    await interaction.response.send_message("✅ Heist started!", ephemeral=True)
    await asyncio.sleep(60)

    if not participants:
        return await interaction.channel.send("🚔 No one showed up for the heist! The vault is safe.")
    success = random.random() > (0.3 if len(participants) >= 3 else 0.6)
    if success:
        total = pot + 100 * len(participants)
        share = total // len(participants)
        for uid, uname in participants:
            eco = get_economy(uid, uname)
            eco["balance"] += share
            eco["total_earned"] += share
        result_embed = discord.Embed(
            title="🏆 HEIST SUCCESSFUL!",
            description=f"**{len(participants)}** robbers cracked the vault!\nEach gets **{share:,} coins!**",
            color=0x3BA55C,
        )
        result_embed.add_field(
            name="Crew", value="\n".join(f"• {uname}" for _, uname in participants[:10])
        )
    else:
        result_embed = discord.Embed(
            title="🚔 HEIST FAILED!",
            description=f"Police caught the crew! **{len(participants)}** robbers lost their entry fees.",
            color=0xED4245,
        )
    await msg.edit(embed=result_embed, view=None)
    add_activity("🏦", f"Heist: {'success' if success else 'failed'}", f"{len(participants)} participants")

# ── Counting commands ─────────────────────────────────────────

@tree.command(name="countinginfo", description="View current counting channel progress")
async def slash_countinginfo(interaction: discord.Interaction):
    gid = str(interaction.guild.id)
    data = counting_data.get(gid, {"count": 0, "last_user": None})
    ch_id = get(interaction.guild.id, "counting_channel")
    embed = discord.Embed(title="🔢 Counting Stats", color=0x00B2FF)
    embed.add_field(name="Current Count", value=f"**{data['count']}**")
    embed.add_field(name="Next Number",   value=f"**{data['count']+1}**")
    embed.add_field(name="Channel",       value=f"<#{ch_id}>" if ch_id else "Not set")
    last = interaction.guild.get_member(data["last_user"]) if data.get("last_user") else None
    if last:
        embed.add_field(name="Last Counter", value=last.mention)
    await interaction.response.send_message(embed=embed, view=report_footer_view("countinginfo"))

@tree.command(name="resetcounting", description="Reset the counting channel [Admin]")
async def slash_resetcounting(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    gid = str(interaction.guild.id)
    counting_data[gid] = {"count": 0, "last_user": None}
    await interaction.response.send_message("✅ Counting reset to **0**!", ephemeral=True)

# ── Application management ────────────────────────────────────

@tree.command(name="appstats", description="View application statistics [Staff]")
async def slash_appstats(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    apps = list(applications_data.values())
    total   = len(apps)
    pending  = sum(1 for a in apps if a.get("status") == "pending")
    accepted = sum(1 for a in apps if a.get("status") == "accepted")
    declined = sum(1 for a in apps if a.get("status") == "declined")
    interview= sum(1 for a in apps if a.get("status") == "interview")
    by_role  = {}
    for a in apps:
        r = a.get("role", "Unknown")
        by_role[r] = by_role.get(r, 0) + 1
    embed = discord.Embed(title="📋 Application Statistics", color=0x5865F2)
    embed.add_field(name="Total",     value=f"`{total}`")
    embed.add_field(name="⏳ Pending", value=f"`{pending}`")
    embed.add_field(name="✅ Accepted",value=f"`{accepted}`")
    embed.add_field(name="❌ Declined",value=f"`{declined}`")
    embed.add_field(name="🎤 Interview",value=f"`{interview}`")
    if by_role:
        embed.add_field(
            name="By Role",
            value="\n".join(f"{k}: **{v}**" for k, v in by_role.items()),
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ── Misc ──────────────────────────────────────────────────────

@tree.command(name="givexp", description="Give XP to a member [Admin]")
@app_commands.describe(member="Member", amount="XP amount")
async def slash_givexp(interaction: discord.Interaction, member: discord.Member, amount: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    if member.id not in xp_data:
        xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    xp_data[member.id]["xp"] += amount
    xp_data[member.id]["level"] = get_level(xp_data[member.id]["xp"])
    await interaction.response.send_message(f"✅ Gave **{amount} XP** to {member.mention}.", ephemeral=True)

@tree.command(name="setlevel", description="Set a member's level [Admin]")
@app_commands.describe(member="Member", level="Level to set")
async def slash_setlevel(interaction: discord.Interaction, member: discord.Member, level: int):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    new_xp = xp_for_level(max(0, level))
    xp_data[member.id] = xp_data.get(member.id, {"messages": 0, "name": str(member)})
    xp_data[member.id]["xp"]   = new_xp
    xp_data[member.id]["level"] = level
    await interaction.response.send_message(f"✅ Set **{member}** to Level **{level}**.", ephemeral=True)

@tree.command(name="reseteco", description="Reset a member's economy data [Admin]")
@app_commands.describe(member="Member")
async def slash_reseteco(interaction: discord.Interaction, member: discord.Member):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    economy_data.pop(member.id, None)
    await interaction.response.send_message(f"✅ Economy data reset for **{member}**.", ephemeral=True)

@tree.command(name="viewbugs", description="View all bug reports [Staff]")
async def slash_viewbugs(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    if not bug_reports_data:
        return await interaction.response.send_message("✅ No bug reports!", ephemeral=True)
    embed = discord.Embed(title=f"🐛 Bug Reports — {len(bug_reports_data)}", color=0xED4245)
    for r in bug_reports_data[:10]:
        embed.add_field(
            name=f"`/{r['command']}` — {r['time']}",
            value=f"{r['description'][:100]}\n*by {r['reporter'].split('#')[0]}*",
            inline=False,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="clearbugs", description="Clear all bug reports [Admin]")
async def slash_clearbugs(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    bug_reports_data.clear()
    await interaction.response.send_message("✅ All bug reports cleared.", ephemeral=True)

@tree.command(name="commandstats", description="View most used commands [Staff]")
async def slash_commandstats(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)
    from collections import Counter
    counts = Counter(r["command"] for r in report_log_data)
    top = counts.most_common(15)
    embed = discord.Embed(title="📊 Command Usage Stats", color=0x5865F2)
    if top:
        embed.description = "\n".join(f"`/{cmd}` — **{cnt}** uses" for cmd, cnt in top)
    else:
        embed.description = "No stats yet."
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ============================================================
# FLASK DASHBOARD — EXTRA ROUTES
# ============================================================
@flask_app.route("/bugs")
def dashboard_bugs():
    return render_template(
        "dashboard.html",
        page="bugs",
        bugs=bug_reports_data,
        **common(),
    )

@flask_app.route("/counting")
def dashboard_counting():
    return render_template(
        "dashboard.html",
        page="counting",
        counting=counting_data,
        **common(),
    )

@flask_app.route("/commandstats")
def dashboard_commandstats():
    from collections import Counter
    counts = Counter(r["command"] for r in report_log_data)
    return render_template(
        "dashboard.html",
        page="commandstats",
        command_stats=counts.most_common(30),
        **common(),
    )

@flask_app.route("/nicklogs")
def dashboard_nicklogs():
    return render_template(
        "dashboard.html",
        page="nicklogs",
        nick_logs=nickname_log_data,
        **common(),
    )

@flask_app.route("/api/extended_stats")
def api_extended_stats():
    return jsonify({
        "bug_reports": len(bug_reports_data),
        "open_bugs":   sum(1 for b in bug_reports_data if b.get("status") == "open"),
        "counting_servers": len(counting_data),
        "premium_channels": sum(len(v) for v in premium_channels_data.values()),
        "command_uses": len(report_log_data),
        "nick_changes": len(nickname_log_data),
        "timers_run": len(timers_data),
    })

# ── Tell user about slash command count at startup ────────────
@bot.listen('on_ready')
async def log_slash_count():
    await asyncio.sleep(5)  # wait for sync
    count = len(tree.get_commands())
    print(f"📋 Total slash commands registered: {count}")
    if count > 100:
        print("⚠️  WARNING: Discord has a 100 slash command limit per bot. Consider grouping some commands.")

# ============================================================
# END OF ADDITIONS — copy everything above into bot.py
# ============================================================

# ============================================================
# RUN BOTH
# ============================================================
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
