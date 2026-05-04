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
from flask import Flask, render_template

# ============================================================
# TOKEN - only thing you need to set!
# ============================================================
TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

# ============================================================
# CONFIG SYSTEM - saves everything automatically
# ============================================================
CONFIG_FILE = "config.json"


def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {}


def save_config(data):
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get(guild_id, key, default=None):
    config = load_config()
    return config.get(str(guild_id), {}).get(key, default)


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

applications_data = {}
warnings_data = {}
notes_data = {}
activity_log = []  # Live activity feed, max 150 entries
giveaway_data = {}  # Active giveaways keyed by message_id
bot_start_time = None  # Set in on_ready
roblox_links = {}  # user_id -> {username, roblox_id, display, thumb, linked_at}
xp_data = {}  # user_id -> {xp, level, messages, name}
xp_cooldowns = {}  # user_id -> last_xp datetime
economy_data = {}  # user_id -> {balance, last_daily, last_work, total_earned, name}
snipe_data = {}  # channel_id -> {content, author, time}
ticket_data = {}  # channel_id -> {user_id, user_name, reason, created, status}
automod_data = {}  # guild_id -> [words]
mod_log_data = []  # Detailed moderation log, max 500
voice_log = []  # Voice activity, max 100


def get_level(xp):
    return int((xp / 50) ** 0.5)


def xp_for_level(level):
    return 50 * level**2


def add_mod_log(action, target, by, reason="", color="#5865f2"):
    mod_log_data.insert(
        0,
        {
            "action": action,
            "target": target,
            "by": by,
            "reason": reason,
            "color": color,
            "time": datetime.now().strftime("%H:%M · %d %b"),
        },
    )
    while len(mod_log_data) > 500:
        mod_log_data.pop()


def get_economy(user_id, name="Unknown"):
    if user_id not in economy_data:
        economy_data[user_id] = {
            "balance": 0,
            "last_daily": None,
            "last_work": None,
            "total_earned": 0,
            "name": name,
        }
    return economy_data[user_id]


def add_activity(icon, action, detail=""):
    activity_log.insert(
        0,
        {
            "icon": icon,
            "action": action,
            "detail": detail,
            "time": datetime.now().strftime("%H:%M · %d %b"),
        },
    )
    while len(activity_log) > 150:
        activity_log.pop()


# ============================================================
# SETUP MENU SYSTEM (Discord native channel/role selects)
# ============================================================
CHANNEL_CONFIG_KEYS = [
    ("welcome_channel", "👋 Welcome Channel"),
    ("apply_channel", "📋 Apply Channel"),
    ("applications_channel", "📨 Staff Applications Channel"),
    ("logs_channel", "📋 Logs Channel"),
    ("general_channel", "💬 General Channel"),
    ("announcements_channel", "📢 Announcements Channel"),
]
ROLE_CONFIG_KEYS = [
    ("admin_role", "⚙️ Admin / Staff Role"),
    ("member_role", "👤 Member Role"),
    ("muted_role", "🔇 Muted Role"),
    ("builder_role", "🔨 Builder Role"),
    ("scripter_role", "💻 Scripter Role"),
    ("modeller_role", "🎨 Modeller Role"),
    ("ui_role", "🖥️ UI Designer Role"),
]


class ChannelTypeSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key)
            for key, label in CHANNEL_CONFIG_KEYS
        ]
        super().__init__(placeholder="Which channel to configure?", options=options)

    async def callback(self, interaction):
        config_key = self.values[0]
        label = next(l for k, l in CHANNEL_CONFIG_KEYS if k == config_key)
        await interaction.response.send_message(
            f"Select the channel for **{label}**:",
            view=ChannelPickerView(config_key, label),
            ephemeral=True,
        )


class ChannelTypeSelectView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(ChannelTypeSelect())


class ChannelPickerSelect(discord.ui.ChannelSelect):
    def __init__(self, config_key, label):
        super().__init__(
            placeholder="Select channel...", channel_types=[discord.ChannelType.text]
        )
        self.config_key = config_key
        self.label_text = label

    async def callback(self, interaction):
        set_config(interaction.guild.id, self.config_key, self.values[0].id)
        await interaction.response.send_message(
            f"✅ **{self.label_text}** → {self.values[0].mention}", ephemeral=True
        )


class ChannelPickerView(View):
    def __init__(self, config_key, label):
        super().__init__(timeout=120)
        self.add_item(ChannelPickerSelect(config_key, label))


class RoleTypeSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=label, value=key)
            for key, label in ROLE_CONFIG_KEYS
        ]
        super().__init__(placeholder="Which role to configure?", options=options)

    async def callback(self, interaction):
        config_key = self.values[0]
        label = next(l for k, l in ROLE_CONFIG_KEYS if k == config_key)
        await interaction.response.send_message(
            f"Select the role for **{label}**:",
            view=RolePickerView(config_key, label),
            ephemeral=True,
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
            f"✅ **{self.label_text}** → {self.values[0].mention}", ephemeral=True
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
        pending = await guild.create_role(
            name="Pending",
            color=discord.Color.from_rgb(90, 90, 90),
            reason="YBS Lockdown",
        )
    set_config(guild.id, "pending_role", pending.id)
    apply_id = get(guild.id, "apply_channel")
    apply_ch = guild.get_channel(apply_id) if apply_id else None
    count = 0
    for ch in guild.channels:
        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
            try:
                if ch == apply_ch:
                    await ch.set_permissions(
                        pending, read_messages=True, send_messages=False
                    )
                else:
                    await ch.set_permissions(pending, read_messages=False)
                count += 1
            except Exception:
                pass
    await interaction.followup.send(
        f"🔒 Lockdown configured! **Pending** role set on {count} channels. New members auto-get this role until accepted.",
        ephemeral=True,
    )
    add_activity("🔒", f"Lockdown setup by {interaction.user.display_name}", guild.name)


class SetupMainView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="📋 Set Channels", style=discord.ButtonStyle.blurple, row=0
    )
    async def set_channels(self, interaction, button):
        embed = discord.Embed(
            title="📋 Configure Channels",
            description="Select which channel type to configure, then pick the channel from your server.",
            color=0x5865F2,
        )
        await interaction.response.send_message(
            embed=embed, view=ChannelTypeSelectView(), ephemeral=True
        )

    @discord.ui.button(label="🎭 Set Roles", style=discord.ButtonStyle.green, row=0)
    async def set_roles(self, interaction, button):
        embed = discord.Embed(
            title="🎭 Configure Roles",
            description="Select which role type to configure, then pick the role from your server.",
            color=0x3BA55C,
        )
        await interaction.response.send_message(
            embed=embed, view=RoleTypeSelectView(), ephemeral=True
        )

    @discord.ui.button(label="🔒 Setup Lockdown", style=discord.ButtonStyle.red, row=1)
    async def setup_lockdown_btn(self, interaction, button):
        await do_lockdown_setup(interaction)

    @discord.ui.button(
        label="📊 View Config", style=discord.ButtonStyle.secondary, row=1
    )
    async def view_config_btn(self, interaction, button):
        config = load_config().get(str(interaction.guild.id), {})
        if not config:
            await interaction.response.send_message(
                "❌ No config yet! Set channels and roles first.", ephemeral=True
            )
            return
        embed = discord.Embed(title="⚙️ Current Configuration", color=0x5865F2)
        for key, value in config.items():
            obj = interaction.guild.get_role(value) or interaction.guild.get_channel(
                value
            )
            embed.add_field(
                name=key, value=obj.mention if obj else str(value), inline=True
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(
        label="📮 Post Apply Panel", style=discord.ButtonStyle.blurple, row=2
    )
    async def post_apply(self, interaction, button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message(
                "❌ Admin only.", ephemeral=True
            )
        channel_id = get(interaction.guild.id, "apply_channel")
        channel = bot.get_channel(channel_id) if channel_id else interaction.channel
        embed = discord.Embed(
            title="🚀 Join the Young Boy Studios Dev Team",
            description="We're looking for talented Roblox developers!\n\n**Roles:** 🔨 Builder · 💻 Scripter · 🎨 Modeller · 🖥️ UI Designer\n\nUse the dropdown below to select your role and open the application form.",
            color=0x5865F2,
        )
        await channel.send(embed=embed, view=ApplyView())
        await interaction.response.send_message(
            f"✅ Apply panel posted in {channel.mention}!", ephemeral=True
        )


# ============================================================
# SETUP COMMAND
# ============================================================
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(
        title="⚙️ Young Boy Studios — Bot Setup",
        description=(
            "Use the buttons below to fully configure the bot.\n\n"
            "**📋 Set Channels** — Welcome, apply, logs, announcements etc.\n"
            "**🎭 Set Roles** — Admin, member, muted, developer roles\n"
            "**🔒 Setup Lockdown** — New members see 0 channels until accepted\n"
            "**📊 View Config** — See all current settings\n"
            "**📮 Post Apply Panel** — Post the application panel\n\n"
            "*All settings are saved instantly and shown only to you.*"
        ),
        color=0x5865F2,
    )
    embed.set_footer(text="All menu selections save immediately")
    await ctx.send(embed=embed, view=SetupMainView())


@bot.command()
@commands.has_permissions(administrator=True)
async def showconfig(ctx):
    config = load_config().get(str(ctx.guild.id), {})
    if not config:
        await ctx.send("❌ No config found. Run `!setup` first!")
        return
    embed = discord.Embed(title="⚙️ Current Config", color=0x5865F2)
    for key, value in config.items():
        obj = ctx.guild.get_role(value) or ctx.guild.get_channel(value)
        embed.add_field(name=key, value=obj.mention if obj else str(value), inline=True)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def setconfig(ctx, key: str, *, mention: str):
    if ctx.message.role_mentions:
        set_config(ctx.guild.id, key, ctx.message.role_mentions[0].id)
        await ctx.send(f"✅ Set `{key}` to {ctx.message.role_mentions[0].mention}")
    elif ctx.message.channel_mentions:
        set_config(ctx.guild.id, key, ctx.message.channel_mentions[0].id)
        await ctx.send(f"✅ Set `{key}` to {ctx.message.channel_mentions[0].mention}")
    else:
        await ctx.send("❌ Please mention a role or channel.")


# ============================================================
# APPLICATION SYSTEM — select role then single modal
# ============================================================
class ApplicationModal(Modal):
    def __init__(self, role: str = "Developer"):
        super().__init__(title=f"🎮 Apply for {role[:35]} — YBS")
        self.role_value = role
        self.roblox_name = TextInput(
            label="Roblox Username",
            placeholder="e.g. CoolBuilder123",
            required=True,
            max_length=50,
        )
        self.real_name = TextInput(
            label="Name & Age",
            placeholder="e.g. Alex, 17",
            required=True,
            max_length=60,
        )
        self.experience = TextInput(
            label="Experience & Skills",
            placeholder="How long developing? What are you best at?",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.why_availability = TextInput(
            label="Why join? + Hours/week available",
            placeholder="Your motivation + e.g. 10 hrs/week, weekends only",
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.portfolio = TextInput(
            label="Portfolio / Work Samples",
            placeholder="https://... or describe your work (N/A if none)",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=400,
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
            "roblox_name": self.roblox_name.value,
            "real_name": real_name,
            "age": age,
            "role": self.role_value,
            "experience": self.experience.value,
            "why_join": self.why_availability.value,
            "availability": "—",
            "portfolio": self.portfolio.value or "N/A",
            "extra": "",
            "user": interaction.user,
            "timestamp": datetime.now().isoformat(),
        }
        applications_data[interaction.user.id] = app
        if channel:
            embed = discord.Embed(
                title=f"📋 New Application — {real_name}",
                color=0x5865F2,
                timestamp=datetime.now(),
            )
            embed.set_author(
                name=str(interaction.user), icon_url=interaction.user.display_avatar.url
            )
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
            embed.add_field(name="🎮 Roblox", value=self.roblox_name.value, inline=True)
            embed.add_field(name="👤 Name", value=real_name, inline=True)
            embed.add_field(name="🎂 Age", value=age, inline=True)
            embed.add_field(name="🔨 Role", value=self.role_value, inline=True)
            embed.add_field(
                name="⚙️ Experience", value=self.experience.value, inline=False
            )
            embed.add_field(
                name="💡 Why Join + Availability",
                value=self.why_availability.value,
                inline=False,
            )
            if self.portfolio.value:
                embed.add_field(
                    name="📁 Portfolio", value=self.portfolio.value, inline=False
                )
            embed.set_footer(text=f"User ID: {interaction.user.id}")
            await channel.send(
                embed=embed, view=ApplicationReviewView(interaction.user.id)
            )
        add_activity("📋", f"New application from {real_name}", self.role_value)
        await interaction.response.send_message(
            "🎉 **Application submitted!** Our team will review it soon. Good luck! 🚀",
            ephemeral=True,
        )


class ApplicationReviewView(View):
    def __init__(self, applicant_id):
        super().__init__(timeout=None)
        self.applicant_id = applicant_id

    def is_staff(self, interaction):
        staff_id = get(interaction.guild.id, "admin_role")
        return staff_id and staff_id in [r.id for r in interaction.user.roles]

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            await interaction.response.send_message("❌ Staff only!", ephemeral=True)
            return
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
                    await member.remove_roles(pending, reason="Application accepted")
            try:
                await member.send(
                    "🎉 Your application to **Young Boy Studios** has been **accepted!** Welcome to the team! 🚀"
                )
            except:
                pass
            add_mod_log(
                "Accept",
                str(member),
                str(interaction.user),
                "Application accepted",
                "#3ba55c",
            )
            add_activity(
                "✅",
                f"{member.display_name}'s application was accepted",
                interaction.guild.name,
            )
        await interaction.message.edit(
            content=f"✅ Accepted by {interaction.user.mention}", view=None
        )
        await interaction.response.send_message("✅ Accepted!", ephemeral=True)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            await interaction.response.send_message("❌ Staff only!", ephemeral=True)
            return
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send(
                    "😔 Your application to **Young Boy Studios** was not accepted this time. Feel free to reapply in 2 weeks!"
                )
            except:
                pass
        await interaction.message.edit(
            content=f"❌ Declined by {interaction.user.mention}", view=None
        )
        await interaction.response.send_message("❌ Declined.", ephemeral=True)

    @discord.ui.button(label="⏳ Interview", style=discord.ButtonStyle.blurple)
    async def interview(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            await interaction.response.send_message("❌ Staff only!", ephemeral=True)
            return
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send(
                    "👋 Your application looks great! A staff member will DM you to arrange an interview."
                )
            except:
                pass
        await interaction.message.edit(
            content=f"⏳ Interview — {interaction.user.mention}", view=None
        )
        await interaction.response.send_message(
            "⏳ Moved to interview.", ephemeral=True
        )


class ApplicationRoleDropdown(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🔨 Builder",
                value="Builder",
                description="Build game environments & maps",
            ),
            discord.SelectOption(
                label="💻 Scripter",
                value="Scripter",
                description="Write Lua scripts & game logic",
            ),
            discord.SelectOption(
                label="🎨 Modeller",
                value="Modeller",
                description="Create 3D models & assets",
            ),
            discord.SelectOption(
                label="🖥️ UI Designer",
                value="UI Designer",
                description="Design game interfaces & UX",
            ),
        ]
        super().__init__(
            placeholder="🎮 Select the role you're applying for...",
            options=options,
            custom_id="apply_role_select_v3",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ApplicationModal(self.values[0]))


class ApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ApplicationRoleDropdown())


class RoleSelectView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def toggle_role(self, interaction, role_key, label):
        role_id = get(interaction.guild.id, role_key)
        if not role_id:
            await interaction.response.send_message(
                f"❌ {label} role not configured. Run `!setup`!", ephemeral=True
            )
            return
        role = interaction.guild.get_role(role_id)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                f"Removed **{label}** role!", ephemeral=True
            )
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(
                f"Added **{label}** role!", ephemeral=True
            )

    @discord.ui.button(
        label="🔨 Builder",
        style=discord.ButtonStyle.secondary,
        custom_id="role_builder",
    )
    async def builder(self, interaction, button):
        await self.toggle_role(interaction, "builder_role", "Builder")

    @discord.ui.button(
        label="💻 Scripter",
        style=discord.ButtonStyle.secondary,
        custom_id="role_scripter",
    )
    async def scripter(self, interaction, button):
        await self.toggle_role(interaction, "scripter_role", "Scripter")

    @discord.ui.button(
        label="🎨 Modeller",
        style=discord.ButtonStyle.secondary,
        custom_id="role_modeller",
    )
    async def modeller(self, interaction, button):
        await self.toggle_role(interaction, "modeller_role", "Modeller")

    @discord.ui.button(
        label="🖥️ UI Designer", style=discord.ButtonStyle.secondary, custom_id="role_ui"
    )
    async def ui(self, interaction, button):
        await self.toggle_role(interaction, "ui_role", "UI Designer")


# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    global bot_start_time
    bot_start_time = datetime.now()
    print(f"✅ {bot.user} is online!")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="Young Boy Studios 🎮"
        )
    )
    if not status_cycle.is_running():
        status_cycle.start()
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
    if channel:
        embed = discord.Embed(
            title=f"👋 Welcome to Young Boy Studios, {member.display_name}!",
            description=(
                f"Hey {member.mention}! We're glad you're here.\n\n"
                f"📋 Head to <#{apply_id}> to **apply for the dev team**\n"
                f"💬 Say hi in general!\n\n"
                f"We're building something great — come be part of it!"
            ),
            color=0x5865F2,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await channel.send(embed=embed)
    try:
        await member.send(
            f"👋 Hey **{member.display_name}**! Welcome to **Young Boy Studios**!\nHead to the **#apply-here** channel to join the dev team! 🚀"
        )
    except:
        pass
    pending_id = get(member.guild.id, "pending_role")
    if pending_id:
        pending_role = member.guild.get_role(pending_id)
        if pending_role:
            try:
                await member.add_roles(
                    pending_role, reason="New member — pending application"
                )
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
        snipe_data[message.channel.id] = {
            "content": message.content[:500],
            "author": str(message.author),
            "time": datetime.now().strftime("%H:%M"),
        }
    log_id = get(message.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="🗑️ Message Deleted", color=0xFF0000)
        embed.add_field(name="Author", value=message.author.mention)
        embed.add_field(name="Channel", value=message.channel.mention)
        embed.add_field(
            name="Content", value=message.content[:500] or "*(no text)*", inline=False
        )
        await log.send(embed=embed)


@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild or before.content == after.content:
        return
    log_id = get(before.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="✏️ Message Edited", color=0xFFAA00)
        embed.add_field(name="Author", value=before.author.mention)
        embed.add_field(
            name="Before", value=before.content[:400] or "*(empty)*", inline=False
        )
        embed.add_field(
            name="After", value=after.content[:400] or "*(empty)*", inline=False
        )
        await log.send(embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        await bot.process_commands(message)
        return
    # Auto-mod word filter
    guild_words = automod_data.get(str(message.guild.id), [])
    if guild_words and any(w.lower() in message.content.lower() for w in guild_words):
        try:
            await message.delete()
            await message.channel.send(
                f"⚠️ {message.author.mention}, that message contained a banned word.",
                delete_after=5,
            )
            if message.author.id not in warnings_data:
                warnings_data[message.author.id] = []
            warnings_data[message.author.id].append(
                {
                    "reason": "AutoMod: banned word",
                    "by": "AutoMod",
                    "time": datetime.now().isoformat(),
                }
            )
            add_activity(
                "🤖",
                f"AutoMod removed message from {message.author.display_name}",
                message.guild.name,
            )
            add_mod_log(
                "AutoMod", str(message.author), "AutoMod", "Banned word", "#ed4245"
            )
        except Exception:
            pass
        await bot.process_commands(message)
        return
    # XP system
    now = datetime.now()
    last = xp_cooldowns.get(message.author.id)
    if not last or (now - last).total_seconds() >= 60:
        xp_cooldowns[message.author.id] = now
        if message.author.id not in xp_data:
            xp_data[message.author.id] = {
                "xp": 0,
                "level": 0,
                "messages": 0,
                "name": str(message.author),
            }
        earned = random.randint(5, 15)
        xp_data[message.author.id]["xp"] += earned
        xp_data[message.author.id]["messages"] = (
            xp_data[message.author.id].get("messages", 0) + 1
        )
        xp_data[message.author.id]["name"] = str(message.author)
        cur_xp = xp_data[message.author.id]["xp"]
        old_lv = xp_data[message.author.id]["level"]
        new_lv = get_level(cur_xp)
        if new_lv > old_lv:
            xp_data[message.author.id]["level"] = new_lv
            add_activity(
                "⬆️",
                f"{message.author.display_name} reached level {new_lv}!",
                message.guild.name,
            )
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} leveled up to **Level {new_lv}**! 🚀",
                    delete_after=10,
                )
            except Exception:
                pass
    await bot.process_commands(message)


@bot.event
async def on_voice_state_update(member, before, after):
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if before.channel is None and after.channel is not None:
        voice_log.insert(
            0,
            {
                "action": "joined",
                "member": str(member),
                "channel": after.channel.name,
                "time": datetime.now().strftime("%H:%M · %d %b"),
            },
        )
        add_activity("🎙️", f"{member.display_name} joined voice #{after.channel.name}")
        if log:
            await log.send(f"🎙️ **{member}** joined **{after.channel.name}**")
    elif before.channel is not None and after.channel is None:
        voice_log.insert(
            0,
            {
                "action": "left",
                "member": str(member),
                "channel": before.channel.name,
                "time": datetime.now().strftime("%H:%M · %d %b"),
            },
        )
        add_activity("🔇", f"{member.display_name} left voice #{before.channel.name}")
        if log:
            await log.send(f"🔇 **{member}** left **{before.channel.name}**")
    elif before.channel != after.channel:
        voice_log.insert(
            0,
            {
                "action": "moved",
                "member": str(member),
                "channel": after.channel.name,
                "time": datetime.now().strftime("%H:%M · %d %b"),
            },
        )
        if log:
            await log.send(
                f"↔️ **{member}** moved **{before.channel.name}** → **{after.channel.name}**"
            )
    while len(voice_log) > 100:
        voice_log.pop()


@bot.event
async def on_member_update(before, after):
    if before.roles == after.roles:
        return
    log_id = get(before.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    added = [r for r in after.roles if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    for role in added:
        add_activity("✨", f"{before.display_name} gained role: {role.name}")
        if log:
            await log.send(f"✨ **{before}** gained role **{role.name}**")
    for role in removed:
        add_activity("➖", f"{before.display_name} lost role: {role.name}")
        if log:
            await log.send(f"➖ **{before}** lost role **{role.name}**")


# ============================================================
# TASKS
# ============================================================
statuses = [
    "Young Boy Studios 🎮",
    "Building something epic 🔨",
    "Hiring developers!",
    "Roblox game dev team 🚀",
]


@tasks.loop(minutes=10)
async def status_cycle():
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name=random.choice(statuses)
        )
    )


# ============================================================
# SETUP PANEL COMMANDS
# ============================================================
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_apply(ctx):
    channel_id = get(ctx.guild.id, "apply_channel")
    channel = bot.get_channel(channel_id) if channel_id else ctx.channel
    embed = discord.Embed(
        title="🚀 Join Young Boy Studios Dev Team",
        description=(
            "We're looking for talented Roblox developers!\n\n"
            "**We need:** 🔨 Builders · 🎨 Modellers · 💻 Scripters · 🖥️ UI Designers\n\n"
            "**The form has 2 parts — complete both!**\n\n"
            "✅ Work on real Roblox projects\n✅ Grow your portfolio\n✅ Be part of a studio from day one"
        ),
        color=0x5865F2,
    )
    await channel.send(embed=embed, view=ApplyView())
    await ctx.send("✅ Apply panel sent!", delete_after=3)


@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx):
    embed = discord.Embed(
        title="🎭 Pick Your Dev Roles",
        description="Click buttons to add/remove your skill roles!",
        color=0x5865F2,
    )
    await ctx.send(embed=embed, view=RoleSelectView())


# ============================================================
# MODERATION
# ============================================================
def is_staff(ctx):
    staff_id = get(ctx.guild.id, "admin_role")
    return ctx.author.guild_permissions.administrator or (
        staff_id and staff_id in [r.id for r in ctx.author.roles]
    )


@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member}** kicked. Reason: {reason}")
    add_activity("👢", f"{member.display_name} was kicked", reason)


@bot.command()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member}** banned. Reason: {reason}")
    add_activity("🔨", f"{member.display_name} was banned", reason)


@bot.command()
async def unban(ctx, *, name):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    banned = [entry async for entry in ctx.guild.bans()]
    for entry in banned:
        if str(entry.user) == name:
            await ctx.guild.unban(entry.user)
            return await ctx.send(f"✅ **{entry.user}** unbanned.")
    await ctx.send("❌ User not found.")


@bot.command()
async def mute(ctx, member: discord.Member, duration: int = 10, *, reason="No reason"):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    role_id = get(ctx.guild.id, "muted_role")
    role = ctx.guild.get_role(role_id) if role_id else None
    if not role:
        return await ctx.send("❌ Muted role not configured. Run `!setup`!")
    await member.add_roles(role)
    await ctx.send(f"🔇 **{member}** muted for {duration} mins. Reason: {reason}")
    await asyncio.sleep(duration * 60)
    await member.remove_roles(role)
    await ctx.send(f"🔊 **{member}** unmuted.")


@bot.command()
async def unmute(ctx, member: discord.Member):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    role_id = get(ctx.guild.id, "muted_role")
    role = ctx.guild.get_role(role_id) if role_id else None
    if role:
        await member.remove_roles(role)
        await ctx.send(f"🔊 **{member}** unmuted.")


@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    if member.id not in warnings_data:
        warnings_data[member.id] = []
    warnings_data[member.id].append(
        {"reason": reason, "by": str(ctx.author), "time": datetime.now().isoformat()}
    )
    count = len(warnings_data[member.id])
    await ctx.send(f"⚠️ **{member}** warned ({count}/3). Reason: {reason}")
    add_activity("⚠️", f"{member.display_name} warned ({count}/3)", reason)
    try:
        await member.send(
            f"⚠️ You've been warned in **Young Boy Studios**.\nReason: {reason}\nWarnings: {count}/3"
        )
    except:
        pass
    if count >= 3:
        await ctx.send(f"🚨 {member.mention} has 3 warnings!")


@bot.command()
async def warnings(ctx, member: discord.Member = None):
    member = member or ctx.author
    warns = warnings_data.get(member.id, [])
    if not warns:
        return await ctx.send(f"✅ **{member}** has no warnings.")
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=0xFFAA00)
    for i, w in enumerate(warns, 1):
        embed.add_field(
            name=f"Warning {i}", value=f"{w['reason']} — by {w['by']}", inline=False
        )
    await ctx.send(embed=embed)


@bot.command()
async def clearwarnings(ctx, member: discord.Member):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    warnings_data[member.id] = []
    await ctx.send(f"✅ Cleared warnings for **{member}**.")


@bot.command()
async def purge(ctx, amount: int):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"🧹 Deleted {amount} messages.", delete_after=3)


@bot.command()
async def slowmode(ctx, seconds: int):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"⏱️ Slowmode set to {seconds}s.")


@bot.command()
async def lock(ctx):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
    await ctx.send("🔒 Channel locked.")


@bot.command()
async def unlock(ctx):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=True)
    await ctx.send("🔓 Channel unlocked.")


@bot.command()
async def nick(ctx, member: discord.Member, *, nickname):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    await member.edit(nick=nickname)
    await ctx.send(f"✅ Nickname changed to **{nickname}**.")


@bot.command()
async def addrole(ctx, member: discord.Member, *, role_name):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.add_roles(role)
        await ctx.send(f"✅ Added **{role.name}** to {member.mention}.")
    else:
        await ctx.send("❌ Role not found.")


@bot.command()
async def removerole(ctx, member: discord.Member, *, role_name):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if role:
        await member.remove_roles(role)
        await ctx.send(f"✅ Removed **{role.name}** from {member.mention}.")
    else:
        await ctx.send("❌ Role not found.")


# ============================================================
# INFO COMMANDS
# ============================================================
@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="ID", value=member.id)
    embed.add_field(name="Joined", value=member.joined_at.strftime("%d/%m/%Y"))
    embed.add_field(name="Created", value=member.created_at.strftime("%d/%m/%Y"))
    embed.add_field(
        name="Roles",
        value=", ".join([r.mention for r in member.roles[1:]]) or "None",
        inline=False,
    )
    embed.add_field(name="Warnings", value=len(warnings_data.get(member.id, [])))
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
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(title=f"{member}'s Avatar")
    embed.set_image(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command()
async def ping(ctx):
    await ctx.send(f"🏓 Pong! **{round(bot.latency * 1000)}ms**")


@bot.command()
async def membercount(ctx):
    await ctx.send(f"👥 **{ctx.guild.member_count}** members!")


@bot.command()
async def stafflist(ctx):
    role_id = get(ctx.guild.id, "admin_role")
    role = ctx.guild.get_role(role_id) if role_id else None
    if not role:
        return await ctx.send("❌ Admin role not configured.")
    members = [m.mention for m in role.members]
    embed = discord.Embed(
        title="👮 Staff Members",
        description="\n".join(members) or "None",
        color=0x5865F2,
    )
    await ctx.send(embed=embed)


# ============================================================
# FUN & UTILITY
# ============================================================
@bot.command()
async def dice(ctx):
    await ctx.send(f"🎲 You rolled a **{random.randint(1, 6)}**!")


@bot.command()
async def coinflip(ctx):
    await ctx.send(f"🪙 **{random.choice(['Heads', 'Tails'])}!**")


@bot.command()
async def poll(ctx, question, *options):
    if len(options) < 2:
        return await ctx.send("❌ Need at least 2 options.")
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    desc = "\n".join([f"{emojis[i]} {opt}" for i, opt in enumerate(options)])
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=0x5865F2)
    msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])


@bot.command()
async def suggest(ctx, *, suggestion):
    general_id = get(ctx.guild.id, "general_channel")
    channel = bot.get_channel(general_id) if general_id else ctx.channel
    embed = discord.Embed(
        title="💡 New Suggestion", description=suggestion, color=0x00FF00
    )
    embed.set_footer(text=f"Suggested by {ctx.author}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass


@bot.command()
async def pick(ctx, *choices):
    if not choices:
        return await ctx.send("❌ Give me some options!")
    await ctx.send(f"🎯 I pick: **{random.choice(choices)}**")


@bot.command()
async def announce(ctx, *, message):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    ann_id = get(ctx.guild.id, "announcements_channel")
    channel = bot.get_channel(ann_id) if ann_id else ctx.channel
    embed = discord.Embed(
        title="📢 Announcement",
        description=message,
        color=0x5865F2,
        timestamp=datetime.now(),
    )
    embed.set_footer(text=f"Posted by {ctx.author}")
    await channel.send("@everyone", embed=embed)


@bot.command()
async def dm(ctx, member: discord.Member, *, message):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    try:
        await member.send(f"📨 **Message from Young Boy Studios staff:**\n{message}")
        await ctx.send(f"✅ DM sent to {member.mention}.")
    except:
        await ctx.send("❌ Couldn't DM that user.")


@bot.command()
async def note(ctx, member: discord.Member, *, note_text):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    if member.id not in notes_data:
        notes_data[member.id] = []
    notes_data[member.id].append({"note": note_text, "by": str(ctx.author)})
    await ctx.send(f"📝 Note added for {member.mention}.")


@bot.command()
async def notes(ctx, member: discord.Member):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    member_notes = notes_data.get(member.id, [])
    if not member_notes:
        return await ctx.send(f"📝 No notes for {member}.")
    embed = discord.Embed(title=f"📝 Notes for {member}", color=0xFFAA00)
    for i, n in enumerate(member_notes, 1):
        embed.add_field(name=f"Note {i} by {n['by']}", value=n["note"], inline=False)
    await ctx.send(embed=embed)


# ============================================================
# NEW COMMANDS
# ============================================================


@bot.command()
async def timeout(
    ctx, member: discord.Member, minutes: int = 10, *, reason="No reason"
):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    try:
        await member.timeout(timedelta(minutes=minutes), reason=reason)
        await ctx.send(
            f"⏰ **{member}** timed out for **{minutes} minutes**. Reason: {reason}"
        )
        add_activity("⏰", f"{member.display_name} timed out ({minutes}min)", reason)
        log_id = get(ctx.guild.id, "logs_channel")
        log = bot.get_channel(log_id) if log_id else None
        if log:
            await log.send(
                f"⏰ {member} timed out for {minutes}min by {ctx.author} — {reason}"
            )
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to timeout this member.")


@bot.command()
async def untimeout(ctx, member: discord.Member):
    if not is_staff(ctx):
        return await ctx.send("❌ No permission.")
    try:
        await member.timeout(None)
        await ctx.send(f"✅ **{member}**'s timeout has been removed.")
        add_activity(
            "✅",
            f"{member.display_name}'s timeout removed",
            f"by {ctx.author.display_name}",
        )
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove this timeout.")


@bot.command()
async def report(ctx, member: discord.Member, *, reason):
    log_id = get(ctx.guild.id, "logs_channel")
    channel = bot.get_channel(log_id) if log_id else ctx.channel
    embed = discord.Embed(
        title="🚨 Member Report", color=0xFF0000, timestamp=datetime.now()
    )
    embed.add_field(name="Reported User", value=f"{member.mention} ({member})")
    embed.add_field(name="Reported By", value=ctx.author.mention)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"User ID: {member.id}")
    await channel.send(embed=embed)
    await ctx.send("✅ Your report has been sent to staff.", delete_after=5)
    add_activity("🚨", f"{member.display_name} was reported", reason)
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass


@bot.command()
async def giveaway(ctx, duration: int, *, prize):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    embed = discord.Embed(
        title="🎉 GIVEAWAY!",
        description=f"**{prize}**\n\nReact with 🎉 to enter!\nEnds in **{duration} minutes**",
        color=0xFF79C6,
        timestamp=datetime.now() + timedelta(minutes=duration),
    )
    embed.set_footer(text=f"Hosted by {ctx.author.display_name} · Ends at")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🎉")
    giveaway_data[msg.id] = {
        "prize": prize,
        "channel": ctx.channel.id,
        "ends": (datetime.now() + timedelta(minutes=duration)).isoformat(),
        "host": str(ctx.author),
    }
    add_activity(
        "🎉",
        f"Giveaway started: {prize}",
        f"{duration}min · hosted by {ctx.author.display_name}",
    )
    await asyncio.sleep(duration * 60)
    if msg.id in giveaway_data:
        try:
            msg = await ctx.channel.fetch_message(msg.id)
            reaction = discord.utils.get(msg.reactions, emoji="🎉")
            users = [u async for u in reaction.users() if not u.bot] if reaction else []
            if users:
                winner = random.choice(users)
                await ctx.channel.send(
                    f"🎉 Congratulations {winner.mention}! You won **{prize}**!"
                )
                result_embed = discord.Embed(
                    title="🎉 GIVEAWAY ENDED",
                    description=f"Winner: {winner.mention}\nPrize: **{prize}**",
                    color=0x57F287,
                )
                await msg.edit(embed=result_embed)
                add_activity(
                    "🏆", f"Giveaway ended: {prize}", f"Winner: {winner.display_name}"
                )
            else:
                await ctx.channel.send(f"❌ Not enough entries for **{prize}**.")
        except Exception as e:
            print(f"Giveaway error: {e}")
        giveaway_data.pop(msg.id, None)


@bot.command()
async def remindme(ctx, minutes: int, *, reminder):
    await ctx.send(
        f"⏰ Got it! I'll remind you in **{minutes} minute{'s' if minutes != 1 else ''}**."
    )
    await asyncio.sleep(minutes * 60)
    try:
        await ctx.author.send(f"⏰ **Reminder from Young Boy Studios:**\n{reminder}")
    except:
        await ctx.send(f"⏰ {ctx.author.mention} — your reminder: {reminder}")


@bot.command()
async def uptime(ctx):
    if bot_start_time:
        delta = datetime.now() - bot_start_time
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        await ctx.send(f"⏱️ Bot uptime: **{h}h {m}m {s}s**")
    else:
        await ctx.send("⏱️ Bot just started!")


@bot.command()
async def roleinfo(ctx, *, role_name):
    role = discord.utils.get(ctx.guild.roles, name=role_name)
    if not role:
        return await ctx.send("❌ Role not found.")
    embed = discord.Embed(title=f"🎭 {role.name}", color=role.color)
    embed.add_field(name="ID", value=role.id)
    embed.add_field(name="Members", value=len(role.members))
    embed.add_field(name="Mentionable", value="Yes" if role.mentionable else "No")
    embed.add_field(name="Hoisted", value="Yes" if role.hoist else "No")
    embed.add_field(name="Color", value=str(role.color))
    embed.add_field(name="Position", value=role.position)
    await ctx.send(embed=embed)


@bot.command()
async def servericon(ctx):
    if not ctx.guild.icon:
        return await ctx.send("❌ This server has no icon.")
    embed = discord.Embed(title=f"{ctx.guild.name} — Server Icon", color=0x5865F2)
    embed.set_image(url=ctx.guild.icon.url)
    await ctx.send(embed=embed)


@bot.command()
async def stealemoji(ctx, emoji: discord.PartialEmoji):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    try:
        emoji_bytes = await emoji.read()
        new_emoji = await ctx.guild.create_custom_emoji(
            name=emoji.name, image=emoji_bytes
        )
        await ctx.send(f"✅ Emoji {new_emoji} added successfully!")
        add_activity(
            "😀", f"Emoji {emoji.name} added to server", f"by {ctx.author.display_name}"
        )
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to add emojis.")
    except Exception as e:
        await ctx.send(f"❌ Failed: {e}")


@bot.command()
async def history(ctx, member: discord.Member):
    if not is_staff(ctx):
        return await ctx.send("❌ Staff only!")
    warns = warnings_data.get(member.id, [])
    notes = notes_data.get(member.id, [])
    embed = discord.Embed(title=f"📜 Moderation History — {member}", color=0xFF8C00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="⚠️ Warnings", value=str(len(warns)), inline=True)
    embed.add_field(name="📝 Staff Notes", value=str(len(notes)), inline=True)
    embed.add_field(
        name="📅 Joined",
        value=member.joined_at.strftime("%d/%m/%Y") if member.joined_at else "Unknown",
        inline=True,
    )
    if warns:
        warn_text = "\n".join(
            [
                f"`{i + 1}.` {w['reason']} — *{w['by']}*"
                for i, w in enumerate(warns[-5:])
            ]
        )
        embed.add_field(name="Recent Warnings", value=warn_text, inline=False)
    if notes:
        note_text = "\n".join(
            [f"`{i + 1}.` {n['note']} — *{n['by']}*" for i, n in enumerate(notes[-3:])]
        )
        embed.add_field(name="Recent Notes", value=note_text, inline=False)
    if not warns and not notes:
        embed.add_field(
            name="Status", value="✅ Clean record — no warnings or notes.", inline=False
        )
    await ctx.send(embed=embed)


# ============================================================
# XP / LEVELING COMMANDS
# ============================================================
@bot.command(aliases=["xp"])
async def rank(ctx, member: discord.Member = None):
    member = member or ctx.author
    data = xp_data.get(
        member.id, {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    )
    xp = data["xp"]
    level = get_level(xp)
    next_lv = xp_for_level(level + 1)
    prev_lv = xp_for_level(level)
    pct = int((xp - prev_lv) / max(next_lv - prev_lv, 1) * 100)
    rank_pos = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)
    position = next(
        (i + 1 for i, (uid, _) in enumerate(rank_pos) if uid == member.id), "?"
    )
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
        prefix = medals[i] if i < 3 else f"{i + 1}."
        name = d.get("name", str(uid)).split("#")[0]
        lines.append(f"{prefix} **{name}** — Level {get_level(d['xp'])} · {d['xp']} XP")
    embed.description = "\n".join(lines)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def addxp(ctx, member: discord.Member, amount: int):
    if member.id not in xp_data:
        xp_data[member.id] = {"xp": 0, "level": 0, "messages": 0, "name": str(member)}
    xp_data[member.id]["xp"] += amount
    xp_data[member.id]["level"] = get_level(xp_data[member.id]["xp"])
    await ctx.send(
        f"✅ Gave **{amount} XP** to {member.mention}. Total: {xp_data[member.id]['xp']} XP"
    )


@bot.command()
@commands.has_permissions(administrator=True)
async def resetxp(ctx, member: discord.Member):
    xp_data.pop(member.id, None)
    await ctx.send(f"🔄 Reset XP for {member.mention}.")


# ============================================================
# ECONOMY COMMANDS
# ============================================================
@bot.command(aliases=["bal", "coins", "wallet"])
async def balance(ctx, member: discord.Member = None):
    member = member or ctx.author
    eco = get_economy(member.id, str(member))
    rank_pos = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)
    position = next(
        (i + 1 for i, (uid, _) in enumerate(rank_pos) if uid == member.id), "?"
    )
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    embed.add_field(name="Rank", value=f"#{position}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command()
async def daily(ctx):
    eco = get_economy(ctx.author.id, str(ctx.author))
    now = datetime.now()
    last = eco["last_daily"]
    if last:
        diff = (now - datetime.fromisoformat(last)).total_seconds()
        if diff < 86400:
            remaining = 86400 - diff
            h, rem = divmod(int(remaining), 3600)
            m, _ = divmod(rem, 60)
            return await ctx.send(
                f"⏰ Daily already claimed! Come back in **{h}h {m}m**."
            )
    amount = random.randint(200, 500)
    eco["balance"] += amount
    eco["total_earned"] += amount
    eco["last_daily"] = now.isoformat()
    add_activity("💰", f"{ctx.author.display_name} claimed daily: {amount} coins")
    await ctx.send(
        f"✅ {ctx.author.mention} claimed your daily reward: **{amount:,} coins!** 💰"
    )


@bot.command()
async def work(ctx):
    eco = get_economy(ctx.author.id, str(ctx.author))
    now = datetime.now()
    last = eco["last_work"]
    if last:
        diff = (now - datetime.fromisoformat(last)).total_seconds()
        if diff < 3600:
            remaining = 3600 - diff
            m, s = divmod(int(remaining), 60)
            return await ctx.send(
                f"⏰ You're tired! Rest for **{m}m {s}s** before working again."
            )
    jobs = [
        "coded a Roblox script 💻",
        "modelled an epic build 🎨",
        "fixed a nasty bug 🐛",
        "scripted an obby 🏃",
        "designed a UI 🖥️",
        "ran a game test 🎮",
    ]
    amount = random.randint(50, 200)
    eco["balance"] += amount
    eco["total_earned"] += amount
    eco["last_work"] = now.isoformat()
    add_activity("🔨", f"{ctx.author.display_name} worked and earned {amount} coins")
    await ctx.send(
        f"💼 {ctx.author.mention} {random.choice(jobs)} and earned **{amount:,} coins!**"
    )


@bot.command(aliases=["give"])
async def pay(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Amount must be positive!")
    payer = get_economy(ctx.author.id, str(ctx.author))
    if payer["balance"] < amount:
        return await ctx.send(f"❌ You only have **{payer['balance']:,} coins**.")
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount
    payee["total_earned"] += amount
    await ctx.send(
        f"✅ {ctx.author.mention} sent **{amount:,} coins** to {member.mention}! 💸"
    )


@bot.command()
async def gamble(ctx, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Amount must be positive!")
    eco = get_economy(ctx.author.id, str(ctx.author))
    if eco["balance"] < amount:
        return await ctx.send(f"❌ You only have **{eco['balance']:,} coins**.")
    if random.random() > 0.5:
        eco["balance"] += amount
        eco["total_earned"] += amount
        await ctx.send(
            f"🎲 {ctx.author.mention} gambled **{amount:,}** and **WON**! New balance: {eco['balance']:,} 🎉"
        )
    else:
        eco["balance"] -= amount
        await ctx.send(
            f"🎲 {ctx.author.mention} gambled **{amount:,}** and **LOST**. New balance: {eco['balance']:,} 😢"
        )


@bot.command()
async def slots(ctx, bet: int = 50):
    eco = get_economy(ctx.author.id, str(ctx.author))
    if eco["balance"] < bet:
        return await ctx.send(f"❌ You only have **{eco['balance']:,} coins**.")
    symbols = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰"]
    reels = [random.choice(symbols) for _ in range(3)]
    if reels[0] == reels[1] == reels[2]:
        multi = 10 if reels[0] == "💎" else 5
        win = bet * multi
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"🎉 **JACKPOT!** You won **{win:,} coins!**"
    elif reels[0] == reels[1] or reels[1] == reels[2]:
        win = bet * 2
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"✅ **Nice!** You won **{win:,} coins!**"
    else:
        eco["balance"] -= bet
        result = f"❌ **No luck.** You lost **{bet:,} coins.**"
    await ctx.send(
        f"🎰 | {reels[0]} | {reels[1]} | {reels[2]} |\n{result}\nBalance: **{eco['balance']:,}**"
    )


@bot.command()
async def richlist(ctx):
    if not economy_data:
        return await ctx.send("❌ No economy data yet!")
    top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[:10]
    embed = discord.Embed(title="💰 Rich List", color=0xFAA61A)
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, d) in enumerate(top):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        name = d.get("name", str(uid)).split("#")[0]
        lines.append(f"{prefix} **{name}** — {d['balance']:,} coins")
    embed.description = "\n".join(lines)
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def givecoins(ctx, member: discord.Member, amount: int):
    eco = get_economy(member.id, str(member))
    eco["balance"] += amount
    eco["total_earned"] += amount
    await ctx.send(f"✅ Gave **{amount:,} coins** to {member.mention}.")


# ============================================================
# FUN COMMANDS
# ============================================================
EIGHTBALL = [
    "It is certain. ✅",
    "It is decidedly so. ✅",
    "Without a doubt. ✅",
    "Yes, definitely. ✅",
    "You may rely on it. ✅",
    "As I see it, yes. ✅",
    "Most likely. 🟡",
    "Outlook good. 🟡",
    "Yes. 🟡",
    "Signs point to yes. 🟡",
    "Reply hazy, try again. ❓",
    "Ask again later. ❓",
    "Better not tell you now. ❓",
    "Cannot predict now. ❓",
    "Don't count on it. ❌",
    "My reply is no. ❌",
    "My sources say no. ❌",
    "Outlook not so good. ❌",
    "Very doubtful. ❌",
]


@bot.command(name="8ball", aliases=["magic8ball"])
async def eightball(ctx, *, question: str):
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def joke(ctx):
    jokes = [
        ("Why do programmers prefer dark mode?", "Because light attracts bugs! 🐛"),
        ("Why did the Roblox player refuse to leave?", "He was ROBLOXed in! 🎮"),
        ("Why did the developer go broke?", "He used up all his cache! 💸"),
        (
            "How many programmers does it take to change a light bulb?",
            "None, that's a hardware problem!",
        ),
        ("Why do Java developers wear glasses?", "Because they don't C#!"),
        ("What do you call a bug that no one can find?", "A fea-ture! ✨"),
        (
            "Why was the JavaScript developer sad?",
            "Because he didn't Node how to Express himself!",
        ),
        ("What's a pirate's favourite programming language?", "R, matey! 🏴‍☠️"),
    ]
    q, a = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke Time!", color=0xFAA61A)
    embed.add_field(name="Setup", value=q, inline=False)
    embed.add_field(name="Punchline", value=f"||{a}||", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def rps(ctx, choice: str):
    choices = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    choice = choice.lower()
    if choice not in choices:
        return await ctx.send("❌ Pick `rock`, `paper`, or `scissors`!")
    bot_choice = random.choice(list(choices.keys()))
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    if choice == bot_choice:
        result = "🤝 **It's a tie!**"
    elif wins[choice] == bot_choice:
        result = "🎉 **You win!**"
    else:
        result = "😔 **Bot wins!**"
    await ctx.send(
        f"{choices[choice]} **{choice.title()}** vs **{bot_choice.title()}** {choices[bot_choice]}\n{result}"
    )


@bot.command()
async def mock(ctx, *, text: str):
    result = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(text))
    await ctx.send(f"🐦 {result}")


@bot.command()
async def reverse(ctx, *, text: str):
    await ctx.send(f"🔄 {text[::-1]}")


@bot.command()
async def rate(ctx, *, thing: str):
    score = random.randint(0, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    color = 0x3BA55C if score >= 70 else 0xFAA61A if score >= 40 else 0xED4245
    embed = discord.Embed(title=f"⭐ Rating: {thing}", color=color)
    embed.add_field(name="Score", value=f"**{score}/100**\n`{bar}`", inline=False)
    await ctx.send(embed=embed)


@bot.command()
async def ship(ctx, user1: discord.Member, user2: discord.Member = None):
    user2 = user2 or ctx.author
    score = (user1.id + user2.id) % 101
    bar = "💗" * (score // 10) + "🖤" * (10 - score // 10)
    color = 0xFF79C6 if score >= 70 else 0xFAA61A if score >= 40 else 0x6B7280
    embed = discord.Embed(title="💘 Compatibility Meter", color=color)
    embed.description = f"**{user1.display_name}** 💕 **{user2.display_name}**\n\n{bar}\n\n**{score}%** compatible!"
    if score >= 70:
        embed.set_footer(text="💞 Perfect match!")
    elif score >= 40:
        embed.set_footer(text="💛 Could work out!")
    else:
        embed.set_footer(text="💔 Maybe just friends...")
    await ctx.send(embed=embed)


@bot.command()
async def trivia(ctx):
    questions = [
        ("What does CSS stand for?", "Cascading Style Sheets"),
        ("What year was Roblox founded?", "2004"),
        ("In Lua, what function prints to console?", "print()"),
        ("What is the max level in most Roblox games?", "Depends on the game!"),
        ("What language does Roblox Studio use?", "Lua"),
        ("What does API stand for?", "Application Programming Interface"),
        ("What does HTML stand for?", "HyperText Markup Language"),
        ("What does RGB stand for?", "Red Green Blue"),
    ]
    q, a = random.choice(questions)
    embed = discord.Embed(title="🎓 Trivia Question!", description=q, color=0x5865F2)
    embed.set_footer(text="Think you know it? React with 💡 to reveal the answer!")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("💡")
    try:
        await bot.wait_for(
            "reaction_add",
            timeout=30,
            check=lambda r, u: str(r.emoji) == "💡"
            and not u.bot
            and r.message.id == msg.id,
        )
        embed.add_field(name="✅ Answer", value=f"||{a}||", inline=False)
        await msg.edit(embed=embed)
    except Exception:
        embed.add_field(
            name="⏰ Time's up!", value=f"The answer was: **{a}**", inline=False
        )
        await msg.edit(embed=embed)


@bot.command()
async def compliment(ctx, member: discord.Member = None):
    member = member or ctx.author
    compliments = [
        "is an absolute legend! 🌟",
        "makes this server 10x better! 💪",
        "is the most talented dev here! 🎮",
        "is going to build something incredible! 🚀",
        "is an XP machine! 📈",
        "is the backbone of Young Boy Studios! 🏆",
        "could script anything! 💻",
        "has the best Roblox builds ever! 🎨",
    ]
    await ctx.send(f"💝 {member.mention} {random.choice(compliments)}")


@bot.command(aliases=["wyr"])
async def wouldyou(ctx, *, text: str = None):
    presets = [
        ("Be a Roblox dev forever", "Be a Minecraft dev forever"),
        ("Script complex games", "Build stunning maps"),
        ("Have 1M Robux now", "Have 10M Robux in a year"),
        ("Work solo", "Work in a team"),
        ("Start with Lua", "Start with Python"),
    ]
    if text and " or " in text.lower():
        parts = text.split(" or ", 1)
        a, b = parts[0].strip(), parts[1].strip()
    else:
        a, b = random.choice(presets)
    embed = discord.Embed(title="🤔 Would You Rather…", color=0x9B59B6)
    embed.add_field(name="🅰️ Option A", value=a, inline=True)
    embed.add_field(name="🅱️ Option B", value=b, inline=True)
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")


@bot.command()
async def truth(ctx):
    truths = [
        "What's your most embarrassing coding mistake?",
        "What game are you secretly working on?",
        "What dev skill do you wish you had?",
        "Have you ever copy-pasted code without understanding it?",
        "What's the longest you've spent debugging one bug?",
        "What do you really think of Roblox scripting?",
        "What feature have you always wanted to build but never did?",
    ]
    embed = discord.Embed(
        title="😳 Truth!", description=random.choice(truths), color=0xFF79C6
    )
    await ctx.send(embed=embed)


@bot.command()
async def dare(ctx):
    dares = [
        "Make a game in 30 minutes!",
        "Build a noob character and screenshot it!",
        "Write a hello world in 3 different languages!",
        "Change your Discord status to 'I love Roblox' for 1 hour!",
        "DM the person above you a Roblox meme!",
        "Script a random feature in 5 minutes!",
        "Draw your game idea in MS Paint!",
    ]
    embed = discord.Embed(
        title="😈 Dare!", description=random.choice(dares), color=0xED4245
    )
    await ctx.send(embed=embed)


@bot.command()
async def pp(ctx, member: discord.Member = None):
    member = member or ctx.author
    score = (member.id * 7) % 21
    bar = "8" + "=" * score + "D"
    await ctx.send(f"📏 **{member.display_name}'s pp size:**\n`{bar}` ({score} cm)")


@bot.command()
async def iq(ctx, member: discord.Member = None):
    member = member or ctx.author
    score = random.randint(50, 200)
    label = (
        "Genius 🧠"
        if score >= 160
        else "Very Smart 🎓"
        if score >= 130
        else "Smart 📚"
        if score >= 110
        else "Average 🙂"
        if score >= 90
        else "Hmm... 🤔"
        if score >= 70
        else "Uh oh... 😬"
    )
    await ctx.send(f"🧠 **{member.display_name}**'s IQ is **{score}** — {label}")


# ============================================================
# UTILITY COMMANDS
# ============================================================
@bot.command()
async def calc(ctx, *, expression: str):
    try:
        allowed = set("0123456789+-*/.() ")
        if not all(c in allowed for c in expression):
            return await ctx.send(
                "❌ Only basic math is allowed (0-9, +, -, *, /, ., ())"
            )
        result = eval(expression)
        embed = discord.Embed(title="🧮 Calculator", color=0x5865F2)
        embed.add_field(name="Expression", value=f"`{expression}`")
        embed.add_field(name="Result", value=f"**{result}**")
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("❌ Invalid expression!")


@bot.command()
async def b64(ctx, mode: str, *, text: str):
    import base64 as b64lib

    try:
        if mode.lower() in ("encode", "enc"):
            result = b64lib.b64encode(text.encode()).decode()
            await ctx.send(f"🔐 **Encoded:**\n```{result}```")
        elif mode.lower() in ("decode", "dec"):
            result = b64lib.b64decode(text.encode()).decode()
            await ctx.send(f"🔓 **Decoded:**\n```{result}```")
        else:
            await ctx.send("❌ Use `!b64 encode <text>` or `!b64 decode <text>`")
    except Exception:
        await ctx.send("❌ Invalid input for base64 operation.")


@bot.command()
async def charcount(ctx, *, text: str):
    embed = discord.Embed(title="📊 Character Count", color=0x5865F2)
    embed.add_field(name="Characters", value=str(len(text)))
    embed.add_field(name="Words", value=str(len(text.split())))
    embed.add_field(name="Lines", value=str(text.count("\n") + 1))
    embed.add_field(name="Spaces", value=str(text.count(" ")))
    await ctx.send(embed=embed)


@bot.command()
async def snipe(ctx):
    data = snipe_data.get(ctx.channel.id)
    if not data:
        return await ctx.send("❌ Nothing to snipe here!")
    embed = discord.Embed(description=data["content"], color=0xED4245)
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    await ctx.send(embed=embed)


@bot.command()
async def say(ctx, *, text: str):
    if not ctx.author.guild_permissions.manage_messages:
        return await ctx.send("❌ No permission.")
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send(text)


@bot.command()
async def embed(ctx, *, content: str):
    if not ctx.author.guild_permissions.manage_messages:
        return await ctx.send("❌ No permission.")
    if "|" in content:
        parts = content.split("|", 1)
        title, desc = parts[0].strip(), parts[1].strip()
    else:
        title, desc = "Announcement", content
    e = discord.Embed(title=title, description=desc, color=0x5865F2)
    e.set_footer(text=f"Posted by {ctx.author.display_name}")
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send(embed=e)


@bot.command()
async def color(ctx, hex_code: str):
    hex_code = hex_code.lstrip("#")
    try:
        r, g, b = int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
        int_val = int(hex_code, 16)
        embed = discord.Embed(title=f"🎨 Color: #{hex_code.upper()}", color=int_val)
        embed.add_field(name="Hex", value=f"#{hex_code.upper()}")
        embed.add_field(name="RGB", value=f"rgb({r}, {g}, {b})")
        embed.add_field(name="Decimal", value=str(int_val))
        await ctx.send(embed=embed)
    except Exception:
        await ctx.send("❌ Invalid hex color. Example: `!color #5865f2`")


@bot.command()
async def timestamp(ctx, *, text: str = None):
    ts = int(datetime.now().timestamp())
    embed = discord.Embed(title="🕐 Current Timestamp", color=0x5865F2)
    embed.add_field(name="Unix", value=f"`{ts}`")
    embed.add_field(name="Discord", value=f"`<t:{ts}>` → <t:{ts}>")
    embed.add_field(name="Relative", value=f"`<t:{ts}:R>` → <t:{ts}:R>")
    await ctx.send(embed=embed)


@bot.command()
async def pigLatin(ctx, *, text: str):
    def to_pig(word):
        vowels = "aeiouAEIOU"
        if word[0] in vowels:
            return word + "way"
        for i, c in enumerate(word):
            if c in vowels:
                return word[i:] + word[:i] + "ay"
        return word + "ay"

    result = " ".join(to_pig(w) for w in text.split())
    await ctx.send(f"🐷 {result}")


@bot.command()
async def poll2(ctx, *, question: str):
    embed = discord.Embed(title="📊 Quick Poll", description=question, color=0x5865F2)
    embed.set_footer(text=f"Asked by {ctx.author.display_name}")
    msg = await ctx.send(embed=embed)
    await msg.add_reaction("👍")
    await msg.add_reaction("👎")
    await msg.add_reaction("🤷")


@bot.command()
async def afk(ctx, *, reason: str = "AFK"):
    embed = discord.Embed(
        description=f"💤 {ctx.author.mention} is now AFK: **{reason}**", color=0x6B7280
    )
    await ctx.send(embed=embed)


@bot.command()
async def howlong(ctx, member: discord.Member = None):
    member = member or ctx.author
    if member.joined_at:
        delta = datetime.now(member.joined_at.tzinfo) - member.joined_at
        days = delta.days
        embed = discord.Embed(
            title=f"📅 {member.display_name} has been here", color=0x5865F2
        )
        embed.add_field(name="Duration", value=f"**{days}** days")
        embed.add_field(name="Joined", value=member.joined_at.strftime("%d %b %Y"))
        await ctx.send(embed=embed)
    else:
        await ctx.send("❌ Could not determine join date.")


# ============================================================
# SERVER MANAGEMENT COMMANDS
# ============================================================
@bot.command()
@commands.has_permissions(manage_channels=True)
async def nuke(ctx, *, reason: str = "Channel nuked"):
    confirm_embed = discord.Embed(
        title="⚠️ Confirm Nuke",
        description=f"This will delete and recreate **#{ctx.channel.name}**. React with ✅ to confirm.",
        color=0xED4245,
    )
    msg = await ctx.send(embed=confirm_embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    try:
        reaction, user = await bot.wait_for(
            "reaction_add",
            timeout=30,
            check=lambda r, u: u == ctx.author and str(r.emoji) in ["✅", "❌"],
        )
    except Exception:
        return await ctx.send("⏰ Nuke cancelled (timeout).")
    if str(reaction.emoji) == "❌":
        return await ctx.send("❌ Nuke cancelled.")
    pos = ctx.channel.position
    new_ch = await ctx.channel.clone(reason=reason)
    await new_ch.edit(position=pos)
    await ctx.channel.delete()
    await new_ch.send(f"💥 Channel nuked by {ctx.author.mention}. Fresh start!")
    add_activity(
        "💥", f"{ctx.author.display_name} nuked #{new_ch.name}", ctx.guild.name
    )
    add_mod_log("Nuke", f"#{new_ch.name}", str(ctx.author), reason, "#ed4245")


@bot.command()
@commands.has_permissions(manage_roles=True)
async def massrole(ctx, action: str, role: discord.Role):
    action = action.lower()
    if action not in ("add", "remove"):
        return await ctx.send("❌ Use `add` or `remove`.")
    msg = await ctx.send(f"⏳ Processing {len(ctx.guild.members)} members...")
    count = 0
    for member in ctx.guild.members:
        if member.bot:
            continue
        try:
            if action == "add" and role not in member.roles:
                await member.add_roles(role)
                count += 1
            elif action == "remove" and role in member.roles:
                await member.remove_roles(role)
                count += 1
        except Exception:
            pass
    await msg.edit(
        content=f"✅ {action.title()}ed **{role.name}** {'to' if action == 'add' else 'from'} **{count}** members."
    )
    add_activity(
        "🎭", f"Mass {action} role: {role.name} — {count} members", ctx.guild.name
    )


@bot.command()
async def ticket(ctx, *, reason: str = "General support"):
    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in guild.roles:
        if role.permissions.manage_messages:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            )
    ch_name = (
        f"ticket-{ctx.author.name.lower().replace(' ', '-')}-{len(ticket_data) + 1}"
    )
    try:
        channel = await guild.create_text_channel(
            ch_name, overwrites=overwrites, reason=f"Ticket by {ctx.author}"
        )
    except Exception:
        return await ctx.send(
            "❌ Failed to create ticket channel (missing permissions)."
        )
    ticket_data[channel.id] = {
        "user_id": ctx.author.id,
        "user_name": str(ctx.author),
        "reason": reason,
        "created": datetime.now().strftime("%d %b %Y %H:%M"),
        "status": "open",
    }
    embed = discord.Embed(
        title="🎫 Ticket Opened",
        description=f"Hey {ctx.author.mention}! Staff will be with you shortly.\n\n**Reason:** {reason}",
        color=0x3BA55C,
    )
    embed.set_footer(text="Use !closeticket to close this ticket.")
    await channel.send(embed=embed)
    await ctx.send(f"✅ Ticket created: {channel.mention}")
    add_activity("🎫", f"Ticket opened by {ctx.author.display_name}", reason)


@bot.command()
async def closeticket(ctx):
    if ctx.channel.id not in ticket_data:
        return await ctx.send("❌ This is not a ticket channel.")
    ticket_data[ctx.channel.id]["status"] = "closed"
    embed = discord.Embed(
        title="🎫 Ticket Closed",
        description=f"Closed by {ctx.author.mention}. Channel will be deleted in 5 seconds.",
        color=0xED4245,
    )
    await ctx.send(embed=embed)
    add_activity("🔒", f"Ticket closed by {ctx.author.display_name}")
    await asyncio.sleep(5)
    try:
        await ctx.channel.delete()
        ticket_data.pop(ctx.channel.id, None)
    except Exception:
        pass


@bot.command()
async def serverrules(ctx):
    if not ctx.author.guild_permissions.manage_guild:
        return await ctx.send("❌ No permission.")
    embed = discord.Embed(title="📜 Server Rules", color=0x5865F2)
    rules = [
        "Be respectful to all members.",
        "No harassment, hate speech, or discrimination.",
        "No spamming, flooding, or excessive caps.",
        "Keep content relevant to each channel.",
        "No NSFW or inappropriate content.",
        "No advertising without permission.",
        "Follow Discord's Terms of Service.",
        "Listen to staff and moderators.",
        "No sharing personal information.",
        "Have fun and be creative! 🎮",
    ]
    embed.description = "\n".join(f"**{i + 1}.** {r}" for i, r in enumerate(rules))
    embed.set_footer(
        text="Failure to follow rules may result in mutes, kicks, or bans."
    )
    await ctx.send(embed=embed)
    add_activity("📜", f"Server rules posted by {ctx.author.display_name}")


@bot.command()
async def botinfo(ctx):
    embed = discord.Embed(title="🤖 Bot Information", color=0x5865F2)
    embed.add_field(name="Name", value=str(bot.user))
    embed.add_field(name="Servers", value=str(len(bot.guilds)))
    embed.add_field(name="Commands", value=str(len(bot.commands)))
    embed.add_field(name="Uptime", value=uptime_str() or "—")
    embed.add_field(name="Members Tracked", value=str(len(xp_data)))
    embed.add_field(name="Economy Users", value=str(len(economy_data)))
    embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="Young Boy Studios · Roblox Dev Team")
    await ctx.send(embed=embed)


def uptime_str():
    if not bot_start_time:
        return None
    delta = datetime.now() - bot_start_time
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


# ============================================================
# AUTO-MOD COMMANDS
# ============================================================
@bot.command()
@commands.has_permissions(manage_messages=True)
async def addword(ctx, *, word: str):
    gid = str(ctx.guild.id)
    if gid not in automod_data:
        automod_data[gid] = []
    if word.lower() in [w.lower() for w in automod_data[gid]]:
        return await ctx.send(f"⚠️ `{word}` is already in the word filter.")
    automod_data[gid].append(word.lower())
    await ctx.send(
        f"✅ Added `{word}` to the auto-mod filter. Total: {len(automod_data[gid])} words."
    )
    add_activity("🤖", f"AutoMod word added: {word}", ctx.guild.name)


@bot.command()
@commands.has_permissions(manage_messages=True)
async def removeword(ctx, *, word: str):
    gid = str(ctx.guild.id)
    wl = automod_data.get(gid, [])
    match = next((w for w in wl if w.lower() == word.lower()), None)
    if not match:
        return await ctx.send(f"❌ `{word}` is not in the filter.")
    wl.remove(match)
    await ctx.send(f"✅ Removed `{word}` from the filter.")


@bot.command()
@commands.has_permissions(manage_messages=True)
async def wordlist(ctx):
    gid = str(ctx.guild.id)
    words = automod_data.get(gid, [])
    if not words:
        return await ctx.send("✅ No banned words configured.")
    embed = discord.Embed(title="🤖 Auto-Mod Word List", color=0xED4245)
    embed.description = ", ".join(f"`{w}`" for w in words) or "None"
    embed.set_footer(text=f"{len(words)} word(s) filtered")
    await ctx.send(embed=embed)


# ============================================================
# UPDATED HELP
# ============================================================
@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📚 YBS Bot — Full Command List", color=0x5865F2)
    embed.add_field(
        name="⚙️ Setup",
        value="`!setup` `!showconfig` `!setup_apply` `!setup_roles`",
        inline=False,
    )
    embed.add_field(
        name="🔨 Moderation",
        value="`!kick` `!ban` `!unban` `!timeout` `!untimeout` `!mute` `!unmute` `!warn` `!warnings` `!clearwarnings` `!history` `!purge` `!slowmode` `!lock` `!unlock` `!nick` `!addrole` `!removerole` `!nuke` `!massrole`",
        inline=False,
    )
    embed.add_field(
        name="ℹ️ Info",
        value="`!userinfo` `!serverinfo` `!avatar` `!roleinfo` `!servericon` `!ping` `!uptime` `!botinfo` `!membercount` `!stafflist` `!howlong`",
        inline=False,
    )
    embed.add_field(
        name="🏅 XP System",
        value="`!rank` `!leaderboard` `!addxp` `!resetxp`",
        inline=False,
    )
    embed.add_field(
        name="💰 Economy",
        value="`!balance` `!daily` `!work` `!pay` `!gamble` `!slots` `!richlist` `!givecoins`",
        inline=False,
    )
    embed.add_field(
        name="🎮 Fun",
        value="`!8ball` `!joke` `!rps` `!mock` `!reverse` `!rate` `!ship` `!trivia` `!compliment` `!wouldyou` `!truth` `!dare` `!pp` `!iq`",
        inline=False,
    )
    embed.add_field(
        name="🛠️ Utility",
        value="`!calc` `!b64` `!charcount` `!snipe` `!say` `!embed` `!color` `!timestamp` `!pigLatin` `!poll2` `!afk` `!report` `!remindme`",
        inline=False,
    )
    embed.add_field(name="🎫 Tickets", value="`!ticket` `!closeticket`", inline=False)
    embed.add_field(
        name="🤖 Auto-Mod", value="`!addword` `!removeword` `!wordlist`", inline=False
    )
    embed.add_field(
        name="🎉 Events",
        value="`!giveaway` `!serverrules` `!poll` `!suggest` `!announce` `!dm` `!note` `!notes` `!stealemoji`",
        inline=False,
    )
    embed.set_footer(text="Prefix: ! | Dashboard: check the web panel")
    await ctx.send(embed=embed)


# ============================================================
# ROBLOX API
# ============================================================
async def fetch_roblox(username: str):
    try:
        async with aiohttp.ClientSession() as s:
            r = await s.post(
                "https://users.roblox.com/v1/usernames/users",
                json={"usernames": [username], "excludeBannedUsers": False},
                headers={"Content-Type": "application/json"},
            )
            data = await r.json()
            if not data.get("data"):
                return None
            uid = data["data"][0]["id"]
            name = data["data"][0]["name"]
            disp = data["data"][0].get("displayName", name)
            r2 = await s.get(f"https://users.roblox.com/v1/users/{uid}")
            profile = await r2.json()
            r3 = await s.get(
                f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={uid}&size=420x420&format=Png"
            )
            thumb_data = await r3.json()
            thumb = (
                thumb_data["data"][0]["imageUrl"] if thumb_data.get("data") else None
            )
            return {
                "id": uid,
                "name": name,
                "display": disp,
                "desc": (profile.get("description") or "No bio.")[:300],
                "created": (profile.get("created") or "")[:10],
                "thumb": thumb,
            }
    except Exception:
        return None


# ============================================================
# SLASH COMMANDS
# ============================================================
class HelpCategorySelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="🏅 XP & Leveling",
                value="xp",
                description="Rank, leaderboard, XP commands",
            ),
            discord.SelectOption(
                label="💰 Economy",
                value="economy",
                description="Coins, daily, work, gamble",
            ),
            discord.SelectOption(
                label="🎮 Fun & Games",
                value="fun",
                description="8ball, trivia, rps, ship…",
            ),
            discord.SelectOption(
                label="🛠️ Utility",
                value="utility",
                description="Calc, snipe, color, timestamp…",
            ),
            discord.SelectOption(
                label="🔨 Moderation",
                value="mod",
                description="Warn, kick, ban, timeout…",
            ),
            discord.SelectOption(
                label="🎫 Tickets & AutoMod",
                value="tickets",
                description="Tickets, word filter…",
            ),
            discord.SelectOption(
                label="ℹ️ Info & Server",
                value="info",
                description="Userinfo, serverinfo, avatar…",
            ),
            discord.SelectOption(
                label="🎮 Roblox", value="roblox", description="Roblox profile lookup"
            ),
        ]
        super().__init__(placeholder="Select a category…", options=options)

    async def callback(self, interaction):
        cats = {
            "xp": (
                "🏅 XP & Leveling",
                "/rank · /leaderboard\n!rank · !leaderboard · !addxp · !resetxp",
            ),
            "economy": (
                "💰 Economy",
                "/balance · /daily · /work · /pay · /gamble · /slots · /leaderboard\n!balance · !daily · !work · !pay · !gamble · !slots · !richlist · !givecoins",
            ),
            "fun": (
                "🎮 Fun & Games",
                "/8ball · /joke · /rps · /rate · /ship · /compliment · /truth · /dare\n!8ball · !joke · !rps · !mock · !reverse · !rate · !ship · !trivia · !compliment · !wouldyou · !truth · !dare · !pp · !iq",
            ),
            "utility": (
                "🛠️ Utility",
                "/snipe · /calc · /color\n!snipe · !calc · !b64 · !charcount · !say · !embed · !color · !timestamp · !pigLatin · !poll2 · !afk · !howlong",
            ),
            "mod": (
                "🔨 Moderation",
                "/warn · /kick · /ban · /timeout · /purge\n!kick · !ban · !unban · !timeout · !untimeout · !mute · !unmute · !warn · !warnings · !clearwarnings · !history · !purge · !slowmode · !lock · !unlock · !nick · !addrole · !removerole · !nuke · !massrole",
            ),
            "tickets": (
                "🎫 Tickets & AutoMod",
                "/ticket · /addword\n!ticket · !closeticket · !addword · !removeword · !wordlist",
            ),
            "info": (
                "ℹ️ Info & Server",
                "/userinfo · /serverinfo · /avatar\n!userinfo · !serverinfo · !avatar · !ping · !uptime · !botinfo · !membercount · !stafflist · !roleinfo · !servericon · !howlong",
            ),
            "roblox": (
                "🎮 Roblox",
                "/roblox <username> — look up a Roblox profile, shows avatar & bio\nLinked accounts appear on the dashboard Roblox page.",
            ),
        }
        title, desc = cats.get(self.values[0], ("Help", "—"))
        embed = discord.Embed(title=title, description=desc, color=0x5865F2)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpView(View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpCategorySelect())


@tree.command(name="help", description="Browse all bot commands by category")
async def slash_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📚 YBS Bot — Help",
        description="Select a category below to see all available commands. All commands also work with the `!` prefix.",
        color=0x5865F2,
    )
    await interaction.response.send_message(
        embed=embed, view=HelpView(), ephemeral=True
    )


@tree.command(name="rank", description="Check your XP rank")
@app_commands.describe(member="Member to check (default: you)")
async def slash_rank(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    d = xp_data.get(member.id, {"xp": 0, "messages": 0, "name": str(member)})
    xp = d["xp"]
    lv = get_level(xp)
    nxt = xp_for_level(lv + 1)
    prv = xp_for_level(lv)
    pct = int((xp - prv) / max(nxt - prv, 1) * 100)
    pos = next(
        (
            i + 1
            for i, (uid, _) in enumerate(
                sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)
            )
            if uid == member.id
        ),
        "?",
    )
    embed = discord.Embed(title=f"🏅 {member.display_name}", color=0x5865F2)
    embed.add_field(name="Level", value=f"**{lv}**")
    embed.add_field(name="XP", value=f"**{xp}** / {nxt}")
    embed.add_field(name="Rank", value=f"**#{pos}**")
    embed.add_field(name="Progress", value=f"{pct}% to Lv {lv + 1}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="balance", description="Check YBS Coins balance")
@app_commands.describe(member="Member to check")
async def slash_balance(
    interaction: discord.Interaction, member: discord.Member = None
):
    member = member or interaction.user
    eco = get_economy(member.id, str(member))
    pos = next(
        (
            i + 1
            for i, (uid, _) in enumerate(
                sorted(
                    economy_data.items(), key=lambda x: x[1]["balance"], reverse=True
                )
            )
            if uid == member.id
        ),
        "?",
    )
    embed = discord.Embed(title=f"💰 {member.display_name}'s Wallet", color=0xFAA61A)
    embed.add_field(name="Balance", value=f"**{eco['balance']:,} coins**")
    embed.add_field(name="Total Earned", value=f"{eco['total_earned']:,} coins")
    embed.add_field(name="Rank", value=f"#{pos}")
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="daily", description="Claim your daily YBS Coins")
async def slash_daily(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if (
        eco["last_daily"]
        and (now - datetime.fromisoformat(eco["last_daily"])).total_seconds() < 86400
    ):
        diff = 86400 - (now - datetime.fromisoformat(eco["last_daily"])).total_seconds()
        h, rem = divmod(int(diff), 3600)
        m = rem // 60
        return await interaction.response.send_message(
            f"⏰ Come back in **{h}h {m}m**.", ephemeral=True
        )
    amt = random.randint(200, 500)
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_daily"] = now.isoformat()
    await interaction.response.send_message(
        f"✅ {interaction.user.mention} claimed **{amt:,} coins**! 💰"
    )


@tree.command(name="work", description="Work to earn YBS Coins")
async def slash_work(interaction: discord.Interaction):
    eco = get_economy(interaction.user.id, str(interaction.user))
    now = datetime.now()
    if (
        eco["last_work"]
        and (now - datetime.fromisoformat(eco["last_work"])).total_seconds() < 3600
    ):
        diff = 3600 - (now - datetime.fromisoformat(eco["last_work"])).total_seconds()
        m, s = divmod(int(diff), 60)
        return await interaction.response.send_message(
            f"⏰ Rest **{m}m {s}s** before working again.", ephemeral=True
        )
    jobs = [
        "coded a Roblox script 💻",
        "modelled an epic build 🎨",
        "fixed a nasty bug 🐛",
        "scripted an obby 🏃",
        "designed a UI 🖥️",
        "wrote a dev diary entry 📝",
    ]
    amt = random.randint(50, 200)
    eco["balance"] += amt
    eco["total_earned"] += amt
    eco["last_work"] = now.isoformat()
    await interaction.response.send_message(
        f"💼 {interaction.user.mention} {random.choice(jobs)} and earned **{amt:,} coins!**"
    )


@tree.command(name="pay", description="Send YBS Coins to another member")
@app_commands.describe(member="Who to pay", amount="How many coins")
async def slash_pay(
    interaction: discord.Interaction, member: discord.Member, amount: int
):
    if amount <= 0:
        return await interaction.response.send_message(
            "❌ Positive amount only.", ephemeral=True
        )
    payer = get_economy(interaction.user.id, str(interaction.user))
    if payer["balance"] < amount:
        return await interaction.response.send_message(
            f"❌ Only **{payer['balance']:,} coins**.", ephemeral=True
        )
    payer["balance"] -= amount
    payee = get_economy(member.id, str(member))
    payee["balance"] += amount
    payee["total_earned"] += amount
    await interaction.response.send_message(
        f"✅ {interaction.user.mention} → {member.mention} **{amount:,} coins** 💸"
    )


@tree.command(name="gamble", description="Gamble your YBS Coins (50/50)")
@app_commands.describe(amount="How many coins to bet")
async def slash_gamble(interaction: discord.Interaction, amount: int):
    if amount <= 0:
        return await interaction.response.send_message(
            "❌ Positive amount only.", ephemeral=True
        )
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < amount:
        return await interaction.response.send_message(
            f"❌ Only **{eco['balance']:,} coins**.", ephemeral=True
        )
    if random.random() > 0.5:
        eco["balance"] += amount
        eco["total_earned"] += amount
        await interaction.response.send_message(
            f"🎲 {interaction.user.mention} bet **{amount:,}** and **WON**! Balance: {eco['balance']:,} 🎉"
        )
    else:
        eco["balance"] -= amount
        await interaction.response.send_message(
            f"🎲 {interaction.user.mention} bet **{amount:,}** and **LOST**. Balance: {eco['balance']:,} 😢"
        )


@tree.command(name="slots", description="Play the slot machine")
@app_commands.describe(bet="Coins to bet (default 50)")
async def slash_slots(interaction: discord.Interaction, bet: int = 50):
    eco = get_economy(interaction.user.id, str(interaction.user))
    if eco["balance"] < bet:
        return await interaction.response.send_message(
            f"❌ Only **{eco['balance']:,} coins**.", ephemeral=True
        )
    syms = ["🍒", "🍋", "🍇", "⭐", "💎", "🎰"]
    r = [random.choice(syms) for _ in range(3)]
    if r[0] == r[1] == r[2]:
        win = bet * (10 if r[0] == "💎" else 5)
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"🎉 JACKPOT! Won **{win:,}**!"
    elif r[0] == r[1] or r[1] == r[2]:
        win = bet * 2
        eco["balance"] += win - bet
        eco["total_earned"] += win - bet
        result = f"✅ Won **{win:,}**!"
    else:
        eco["balance"] -= bet
        result = f"❌ Lost **{bet:,}**."
    await interaction.response.send_message(
        f"🎰 | {r[0]} | {r[1]} | {r[2]} |\n{result} Balance: **{eco['balance']:,}**"
    )


@tree.command(name="leaderboard", description="View XP and coin leaderboards")
async def slash_leaderboard(interaction: discord.Interaction):
    embed = discord.Embed(title="🏆 Leaderboards", color=0x5865F2)
    medals = ["🥇", "🥈", "🥉", "4.", "5."]
    if xp_data:
        top = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)[:5]
        embed.add_field(
            name="🏅 XP Rankings",
            value="\n".join(
                f"{medals[i]} **{d.get('name', '?').split('#')[0]}** — Lv {get_level(d['xp'])} · {d['xp']} XP"
                for i, (_, d) in enumerate(top)
            ),
            inline=False,
        )
    if economy_data:
        top = sorted(economy_data.items(), key=lambda x: x[1]["balance"], reverse=True)[
            :5
        ]
        embed.add_field(
            name="💰 Rich List",
            value="\n".join(
                f"{medals[i]} **{d.get('name', '?').split('#')[0]}** — 💰 {d['balance']:,}"
                for i, (_, d) in enumerate(top)
            ),
            inline=False,
        )
    if not xp_data and not economy_data:
        embed.description = "No data yet! Chat to earn XP and use `/daily` for coins."
    await interaction.response.send_message(embed=embed)


@tree.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your yes/no question")
async def slash_8ball(interaction: discord.Interaction, question: str):
    embed = discord.Embed(title="🎱 Magic 8-Ball", color=0x5865F2)
    embed.add_field(name="❓ Question", value=question, inline=False)
    embed.add_field(name="🎱 Answer", value=random.choice(EIGHTBALL), inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="joke", description="Get a random dev/Roblox joke")
async def slash_joke(interaction: discord.Interaction):
    jokes = [
        ("Why do programmers prefer dark mode?", "Because light attracts bugs! 🐛"),
        ("Why did the Roblox player refuse to leave?", "He was ROBLOXed in! 🎮"),
        ("Why did the developer go broke?", "He used up all his cache! 💸"),
        (
            "How many programmers to change a light bulb?",
            "None, that's a hardware problem!",
        ),
        ("Why do coders hate nature?", "It has too many bugs!"),
    ]
    q, a = random.choice(jokes)
    embed = discord.Embed(title="😂 Joke!", color=0xFAA61A)
    embed.add_field(name="Setup", value=q, inline=False)
    embed.add_field(name="Punchline", value=f"||{a}||", inline=False)
    await interaction.response.send_message(embed=embed)


@tree.command(name="rps", description="Rock Paper Scissors vs the bot")
@app_commands.describe(choice="Your move")
@app_commands.choices(
    choice=[
        app_commands.Choice(name="🪨 Rock", value="rock"),
        app_commands.Choice(name="📄 Paper", value="paper"),
        app_commands.Choice(name="✂️ Scissors", value="scissors"),
    ]
)
async def slash_rps(interaction: discord.Interaction, choice: str):
    ch = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    bot_c = random.choice(list(ch.keys()))
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    result = (
        "🤝 **Tie!**"
        if choice == bot_c
        else ("🎉 **You win!**" if wins[choice] == bot_c else "😔 **Bot wins!**")
    )
    await interaction.response.send_message(
        f"{ch[choice]} **{choice.title()}** vs **{bot_c.title()}** {ch[bot_c]}\n{result}"
    )


@tree.command(name="rate", description="Rate something out of 100")
@app_commands.describe(thing="What to rate")
async def slash_rate(interaction: discord.Interaction, thing: str):
    score = random.randint(0, 100)
    bar = "█" * (score // 10) + "░" * (10 - score // 10)
    color = 0x3BA55C if score >= 70 else 0xFAA61A if score >= 40 else 0xED4245
    await interaction.response.send_message(
        embed=discord.Embed(
            title=f"⭐ Rating: {thing}",
            description=f"**{score}/100**\n`{bar}`",
            color=color,
        )
    )


@tree.command(name="ship", description="Check compatibility between two members")
@app_commands.describe(user1="First member", user2="Second member (default: you)")
async def slash_ship(
    interaction: discord.Interaction,
    user1: discord.Member,
    user2: discord.Member = None,
):
    user2 = user2 or interaction.user
    score = (user1.id + user2.id) % 101
    bar = "💗" * (score // 10) + "🖤" * (10 - score // 10)
    embed = discord.Embed(
        title="💘 Compatibility",
        description=f"**{user1.display_name}** 💕 **{user2.display_name}**\n\n{bar}\n\n**{score}%** compatible!",
        color=0xFF79C6,
    )
    await interaction.response.send_message(embed=embed)


@tree.command(name="compliment", description="Compliment a member")
@app_commands.describe(member="Who to compliment")
async def slash_compliment(
    interaction: discord.Interaction, member: discord.Member = None
):
    member = member or interaction.user
    cs = [
        "is an absolute legend! 🌟",
        "makes this server better! 💪",
        "is the most talented dev here! 🎮",
        "is going to build something incredible! 🚀",
        "has the best scripts in the game! 💻",
        "could model anything! 🎨",
    ]
    await interaction.response.send_message(f"💝 {member.mention} {random.choice(cs)}")


@tree.command(name="truth", description="Get a random truth question")
async def slash_truth(interaction: discord.Interaction):
    ts = [
        "What's your most embarrassing coding mistake?",
        "What game are you secretly working on?",
        "What dev skill do you wish you had?",
        "Have you ever copy-pasted code you didn't understand?",
        "What's the longest you've spent on one bug?",
    ]
    await interaction.response.send_message(
        embed=discord.Embed(
            title="😳 Truth!", description=random.choice(ts), color=0xFF79C6
        )
    )


@tree.command(name="dare", description="Get a random dare challenge")
async def slash_dare(interaction: discord.Interaction):
    ds = [
        "Make a mini-game in 30 minutes!",
        "Build a noob character and screenshot it!",
        "Write hello world in 3 languages!",
        "Script a random feature in 5 minutes!",
        "Draw your game idea in MS Paint!",
    ]
    await interaction.response.send_message(
        embed=discord.Embed(
            title="😈 Dare!", description=random.choice(ds), color=0xED4245
        )
    )


@tree.command(name="snipe", description="See the last deleted message in this channel")
async def slash_snipe(interaction: discord.Interaction):
    data = snipe_data.get(interaction.channel.id)
    if not data:
        return await interaction.response.send_message(
            "❌ Nothing to snipe here!", ephemeral=True
        )
    embed = discord.Embed(description=data["content"], color=0xED4245)
    embed.set_author(name=data["author"])
    embed.set_footer(text=f"Deleted at {data['time']}")
    await interaction.response.send_message(embed=embed)


@tree.command(name="calc", description="Calculate a math expression")
@app_commands.describe(expression="Math to calculate")
async def slash_calc(interaction: discord.Interaction, expression: str):
    if not all(c in "0123456789+-*/.() " for c in expression):
        return await interaction.response.send_message(
            "❌ Only basic math operators allowed!", ephemeral=True
        )
    try:
        result = eval(expression)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🧮 Calculator",
                description=f"`{expression}` = **{result}**",
                color=0x5865F2,
            )
        )
    except:
        await interaction.response.send_message(
            "❌ Invalid expression!", ephemeral=True
        )


@tree.command(name="color", description="View info about a hex color")
@app_commands.describe(hex_code="Hex code e.g. 5865f2")
async def slash_color(interaction: discord.Interaction, hex_code: str):
    hex_code = hex_code.lstrip("#")
    try:
        r, g, b = int(hex_code[0:2], 16), int(hex_code[2:4], 16), int(hex_code[4:6], 16)
        embed = discord.Embed(title=f"🎨 #{hex_code.upper()}", color=int(hex_code, 16))
        embed.add_field(name="Hex", value=f"#{hex_code.upper()}")
        embed.add_field(name="RGB", value=f"rgb({r},{g},{b})")
        await interaction.response.send_message(embed=embed)
    except:
        await interaction.response.send_message("❌ Invalid hex!", ephemeral=True)


@tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(member="Member to look up")
async def slash_userinfo(
    interaction: discord.Interaction, member: discord.Member = None
):
    member = member or interaction.user
    embed = discord.Embed(title=f"👤 {member}", color=member.color)
    embed.add_field(name="ID", value=str(member.id))
    embed.add_field(
        name="Joined Server",
        value=member.joined_at.strftime("%d %b %Y") if member.joined_at else "?",
    )
    embed.add_field(name="Joined Discord", value=member.created_at.strftime("%d %b %Y"))
    embed.add_field(name="Roles", value=str(len(member.roles) - 1))
    xd = xp_data.get(member.id, {})
    if xd:
        embed.add_field(name="Level", value=str(get_level(xd.get("xp", 0))))
        embed.add_field(name="XP", value=str(xd.get("xp", 0)))
    eco = economy_data.get(member.id, {})
    if eco:
        embed.add_field(name="Coins", value=f"{eco.get('balance', 0):,}")
    rb = roblox_links.get(member.id)
    if rb:
        embed.add_field(name="🎮 Roblox", value=rb["username"])
    embed.set_thumbnail(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="serverinfo", description="View info about this server")
async def slash_serverinfo(interaction: discord.Interaction):
    g = interaction.guild
    embed = discord.Embed(title=f"🏠 {g.name}", color=0x5865F2)
    embed.add_field(name="Members", value=str(g.member_count))
    embed.add_field(name="Channels", value=str(len(g.channels)))
    embed.add_field(name="Roles", value=str(len(g.roles)))
    embed.add_field(name="Created", value=g.created_at.strftime("%d %b %Y"))
    embed.add_field(name="Owner", value=str(g.owner))
    if g.icon:
        embed.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="avatar", description="View a member's avatar")
@app_commands.describe(member="Member to check")
async def slash_avatar(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    embed = discord.Embed(title=f"🖼️ {member.display_name}'s Avatar", color=0x5865F2)
    embed.set_image(url=member.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@tree.command(name="roblox", description="Look up a Roblox profile")
@app_commands.describe(username="Roblox username to look up")
async def slash_roblox(interaction: discord.Interaction, username: str):
    await interaction.response.defer()
    data = await fetch_roblox(username)
    if not data:
        return await interaction.followup.send(
            f"❌ Couldn't find Roblox user **{username}**.", ephemeral=True
        )
    embed = discord.Embed(
        title=f"🎮 {data['display']} (@{data['name']})",
        url=f"https://www.roblox.com/users/{data['id']}/profile",
        color=0x00B2FF,
    )
    embed.add_field(name="User ID", value=str(data["id"]))
    embed.add_field(name="Account Created", value=data["created"] or "—")
    if data["desc"]:
        embed.add_field(name="Bio", value=data["desc"][:300], inline=False)
    if data["thumb"]:
        embed.set_thumbnail(url=data["thumb"])
    embed.set_footer(text=f"Looked up by {interaction.user}")
    roblox_links[interaction.user.id] = {
        "username": data["name"],
        "roblox_id": data["id"],
        "display": data["display"],
        "thumb": data.get("thumb"),
        "discord_name": str(interaction.user),
        "linked_at": datetime.now().strftime("%d %b %Y"),
    }
    add_activity(
        "🎮", f"{interaction.user.display_name} looked up Roblox: {data['name']}"
    )
    await interaction.followup.send(embed=embed)


@tree.command(name="ticket", description="Open a support ticket")
@app_commands.describe(reason="Reason for the ticket")
async def slash_ticket(
    interaction: discord.Interaction, reason: str = "General support"
):
    guild = interaction.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        interaction.user: discord.PermissionOverwrite(
            read_messages=True, send_messages=True
        ),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for role in guild.roles:
        if role.permissions.manage_messages:
            overwrites[role] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            )
    ch_name = f"ticket-{interaction.user.name.lower()[:15]}-{len(ticket_data) + 1}"
    try:
        channel = await guild.create_text_channel(ch_name, overwrites=overwrites)
    except Exception:
        return await interaction.response.send_message(
            "❌ Failed (missing permissions).", ephemeral=True
        )
    ticket_data[channel.id] = {
        "user_id": interaction.user.id,
        "user_name": str(interaction.user),
        "reason": reason,
        "created": datetime.now().strftime("%d %b %Y %H:%M"),
        "status": "open",
    }
    embed = discord.Embed(
        title="🎫 Ticket Opened",
        description=f"Hey {interaction.user.mention}! Staff will be with you shortly.\n**Reason:** {reason}",
        color=0x3BA55C,
    )
    await channel.send(embed=embed)
    await interaction.response.send_message(
        f"✅ Ticket created: {channel.mention}", ephemeral=True
    )
    add_activity("🎫", f"Ticket: {interaction.user.display_name}", reason)


@tree.command(name="warn", description="Warn a member [Staff only]")
@app_commands.describe(member="Member to warn", reason="Reason")
async def slash_warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided",
):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    if member.id not in warnings_data:
        warnings_data[member.id] = []
    warnings_data[member.id].append(
        {
            "reason": reason,
            "by": str(interaction.user),
            "time": datetime.now().isoformat(),
        }
    )
    add_mod_log("Warn", str(member), str(interaction.user), reason, "#faa61a")
    add_activity("⚠️", f"{member.display_name} warned", reason)
    try:
        await member.send(f"⚠️ Warned in **{interaction.guild.name}**: {reason}")
    except:
        pass
    await interaction.response.send_message(
        f"⚠️ **{member}** warned. Total: {len(warnings_data[member.id])}"
    )


@tree.command(name="kick", description="Kick a member [Staff only]")
@app_commands.describe(member="Member to kick", reason="Reason")
async def slash_kick(
    interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"
):
    if not interaction.user.guild_permissions.kick_members:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    try:
        await member.kick(reason=reason)
        add_mod_log("Kick", str(member), str(interaction.user), reason, "#faa61a")
        await interaction.response.send_message(
            f"👢 **{member}** kicked. Reason: {reason}"
        )
    except:
        await interaction.response.send_message("❌ Couldn't kick.", ephemeral=True)


@tree.command(name="ban", description="Ban a member [Staff only]")
@app_commands.describe(member="Member to ban", reason="Reason")
async def slash_ban(
    interaction: discord.Interaction, member: discord.Member, reason: str = "No reason"
):
    if not interaction.user.guild_permissions.ban_members:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    try:
        await member.ban(reason=reason)
        add_mod_log("Ban", str(member), str(interaction.user), reason, "#ed4245")
        await interaction.response.send_message(
            f"🔨 **{member}** banned. Reason: {reason}"
        )
    except:
        await interaction.response.send_message("❌ Couldn't ban.", ephemeral=True)


@tree.command(name="timeout", description="Timeout a member [Staff only]")
@app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
async def slash_timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: int = 5,
    reason: str = "No reason",
):
    if not interaction.user.guild_permissions.moderate_members:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    try:
        await member.timeout(
            discord.utils.utcnow() + timedelta(minutes=minutes), reason=reason
        )
        add_mod_log(
            "Timeout",
            str(member),
            str(interaction.user),
            f"{minutes}m — {reason}",
            "#faa61a",
        )
        await interaction.response.send_message(
            f"⏰ **{member}** timed out for {minutes}m."
        )
    except:
        await interaction.response.send_message("❌ Couldn't timeout.", ephemeral=True)


@tree.command(name="purge", description="Delete messages in bulk [Staff only]")
@app_commands.describe(amount="Messages to delete (1-100)")
async def slash_purge(interaction: discord.Interaction, amount: int = 10):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=max(1, min(100, amount)))
    await interaction.followup.send(
        f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True
    )


@tree.command(
    name="addword", description="Add a word to the auto-mod filter [Staff only]"
)
@app_commands.describe(word="Word to ban")
async def slash_addword(interaction: discord.Interaction, word: str):
    if not interaction.user.guild_permissions.manage_messages:
        return await interaction.response.send_message(
            "❌ No permission.", ephemeral=True
        )
    gid = str(interaction.guild.id)
    if gid not in automod_data:
        automod_data[gid] = []
    if word.lower() not in automod_data[gid]:
        automod_data[gid].append(word.lower())
    await interaction.response.send_message(
        f"✅ Added `{word}` to filter.", ephemeral=True
    )


@tree.command(name="setup", description="Configure the bot [Admin only]")
async def slash_setup(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    embed = discord.Embed(
        title="⚙️ Bot Setup",
        description="Use the buttons below to configure channels, roles, and lockdown.",
        color=0x5865F2,
    )
    await interaction.response.send_message(
        embed=embed, view=SetupMainView(), ephemeral=True
    )


# ============================================================
# DASHBOARD (Flask web server)
# ============================================================
flask_app = Flask(__name__)
flask_app.jinja_env.globals.update(enumerate=enumerate)


def build_xp_leaderboard():
    sorted_xp = sorted(xp_data.items(), key=lambda x: x[1]["xp"], reverse=True)
    result = []
    for uid, d in sorted_xp:
        lv = get_level(d["xp"])
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        denom = max(next_lv_xp - prev_lv_xp, 1)
        pct = int((d["xp"] - prev_lv_xp) / denom * 100)
        result.append(
            {
                "uid": uid,
                "name": d.get("name", "Unknown"),
                "xp": d["xp"],
                "level": lv,
                "messages": d.get("messages", 0),
                "pct": pct,
            }
        )
    return result


def build_eco_leaderboard():
    sorted_eco = sorted(
        economy_data.items(), key=lambda x: x[1]["balance"], reverse=True
    )
    return [
        {
            "uid": uid,
            "name": d.get("name", "Unknown"),
            "balance": d["balance"],
            "total_earned": d["total_earned"],
        }
        for uid, d in sorted_eco
    ]


def build_members():
    all_ids = set(xp_data.keys()) | set(economy_data.keys())
    members = []
    for uid in all_ids:
        xd = xp_data.get(uid, {"xp": 0, "level": 0, "messages": 0, "name": "Unknown"})
        ed = economy_data.get(uid, {"balance": 0})
        lv = get_level(xd["xp"])
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        pct = int((xd["xp"] - prev_lv_xp) / max(next_lv_xp - prev_lv_xp, 1) * 100)
        warn_count = len(warnings_data.get(uid, []))
        members.append(
            {
                "uid": uid,
                "name": xd.get("name", "Unknown"),
                "xp": xd["xp"],
                "level": lv,
                "messages": xd.get("messages", 0),
                "pct": pct,
                "balance": ed.get("balance", 0),
                "warnings": warn_count,
            }
        )
    return sorted(members, key=lambda x: x["xp"], reverse=True)


def common():
    role_counts = {}
    for app in applications_data.values():
        role = app.get("role", "Other")
        role_counts[role] = role_counts.get(role, 0) + 1
    recent_warns = []
    for uid, warns in list(warnings_data.items())[-8:]:
        if warns:
            recent_warns.append(
                {
                    "user_id": uid,
                    "count": len(warns),
                    "last_reason": warns[-1]["reason"],
                    "last_by": warns[-1].get("by", "—"),
                }
            )
    top_xp = build_xp_leaderboard()[:5]
    for m in top_xp:
        lv = m["level"]
        next_lv_xp = xp_for_level(lv + 1)
        prev_lv_xp = xp_for_level(lv)
        m["pct"] = int((m["xp"] - prev_lv_xp) / max(next_lv_xp - prev_lv_xp, 1) * 100)
    return dict(
        bot_online=bot.is_ready(),
        bot_name=str(bot.user) if bot.user else "YBS Bot",
        app_count=len(applications_data),
        warn_count=len(warnings_data),
        notes_count=sum(len(v) for v in notes_data.values()),
        giveaway_count=len(giveaway_data),
        ticket_count=sum(1 for t in ticket_data.values() if t["status"] == "open"),
        xp_count=len(xp_data),
        eco_count=len(economy_data),
        mod_log_count=len(mod_log_data),
        activity=activity_log,
        uptime=uptime_str(),
        role_counts=role_counts,
        recent_warns=recent_warns,
        recent_apps=list(applications_data.values())[-5:],
        top_xp=top_xp,
        mod_logs=mod_log_data,
        roblox_count=len(roblox_links),
    )


@flask_app.route("/")
def dashboard_home():
    return render_template("dashboard.html", page="home", **common())


@flask_app.route("/applications")
def dashboard_applications():
    return render_template(
        "dashboard.html",
        page="applications",
        applications=applications_data,
        **common(),
    )


@flask_app.route("/warnings")
def dashboard_warnings():
    return render_template(
        "dashboard.html",
        page="warnings",
        warnings={str(k): v for k, v in warnings_data.items()},
        **common(),
    )


@flask_app.route("/activity")
def dashboard_activity():
    return render_template("dashboard.html", page="activity", **common())


@flask_app.route("/giveaways")
def dashboard_giveaways():
    return render_template(
        "dashboard.html", page="giveaways", giveaways=giveaway_data, **common()
    )


@flask_app.route("/leaderboard")
def dashboard_leaderboard():
    return render_template(
        "dashboard.html",
        page="leaderboard",
        xp_lb=build_xp_leaderboard()[:25],
        eco_lb=build_eco_leaderboard()[:25],
        **common(),
    )


@flask_app.route("/members")
def dashboard_members():
    return render_template(
        "dashboard.html", page="members", members=build_members(), **common()
    )


@flask_app.route("/modlogs")
def dashboard_modlogs():
    return render_template("dashboard.html", page="modlogs", **common())


@flask_app.route("/automod")
def dashboard_automod():
    return render_template(
        "dashboard.html",
        page="automod",
        automod_guilds=automod_data,
        total_words=sum(len(v) for v in automod_data.values()),
        **common(),
    )


@flask_app.route("/tickets")
def dashboard_tickets():
    return render_template(
        "dashboard.html", page="tickets", tickets=ticket_data, **common()
    )


@flask_app.route("/voice")
def dashboard_voice():
    return render_template(
        "dashboard.html", page="voice", voice_events=voice_log, **common()
    )


@flask_app.route("/economy")
def dashboard_economy():
    eco_lb = build_eco_leaderboard()
    total_coins = sum(d["balance"] for d in economy_data.values())
    total_earned_ever = sum(d["total_earned"] for d in economy_data.values())
    richest = max((d["balance"] for d in economy_data.values()), default=0)
    return render_template(
        "dashboard.html",
        page="economy",
        eco_lb=eco_lb[:25],
        total_coins=total_coins,
        total_earned_ever=total_earned_ever,
        richest=richest,
        **common(),
    )


@flask_app.route("/roblox")
def dashboard_roblox():
    accounts = [
        {
            "discord_name": v.get("discord_name", "Unknown"),
            "username": v["username"],
            "display": v.get("display", v["username"]),
            "roblox_id": v["roblox_id"],
            "thumb": v.get("thumb"),
            "linked_at": v.get("linked_at", "—"),
        }
        for v in roblox_links.values()
    ]
    return render_template(
        "dashboard.html", page="roblox", roblox_accounts=accounts, **common()
    )


def run_bot():
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Bot error: {e}")


# ============================================================
# RUN BOTH
# ============================================================
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()

flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
