import discord
from discord.ext import commands, tasks
from discord.ui import Button, View, Modal, TextInput, Select
from discord import app_commands
import json
import os
import asyncio
from datetime import datetime
import random

# ============================================================
# TOKEN - only thing you need to set!
# ============================================================
TOKEN = os.environ.get("TOKEN")

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

# ============================================================
# SETUP WIZARD VIEWS
# ============================================================

class SetupStartView(View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Start Setup Wizard", style=discord.ButtonStyle.blurple, emoji="🚀")
    async def start(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="⚙️ Step 1 — Set Admin Role",
                description=(
                    "**Mention the role that should be able to use admin/staff commands.**\n\n"
                    "Example: type `@Staff` or `@Admin` in this channel right now.\n\n"
                    "*You have 60 seconds to reply.*"
                ),
                color=0x5865F2
            ),
            ephemeral=True
        )
        await run_setup(interaction)

async def run_setup(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel
    user = interaction.user

    def check(m):
        return m.author == user and m.channel == channel

    steps = [
        ("admin_role", "⚙️ Step 1 — Admin Role", "Mention the **admin/staff role** (e.g. `@Staff`)", "role"),
        ("member_role", "👤 Step 2 — Member Role", "Mention the **member role** given to accepted applicants (e.g. `@Member`)", "role"),
        ("welcome_channel", "👋 Step 3 — Welcome Channel", "Mention the **welcome channel** (e.g. `#welcome`)", "channel"),
        ("apply_channel", "📋 Step 4 — Apply Channel", "Mention the **apply channel** where people apply (e.g. `#apply-here`)", "channel"),
        ("applications_channel", "📨 Step 5 — Applications Channel", "Mention the **staff applications channel** — only staff should see this (e.g. `#applications`)", "channel"),
        ("logs_channel", "📋 Step 6 — Logs Channel", "Mention the **logs channel** for mod actions (e.g. `#logs`)", "channel"),
        ("general_channel", "💬 Step 7 — General Channel", "Mention the **general chat channel** (e.g. `#general`)", "channel"),
        ("announcements_channel", "📢 Step 8 — Announcements Channel", "Mention the **announcements channel** (e.g. `#announcements`)", "channel"),
        ("muted_role", "🔇 Step 9 — Muted Role", "Mention the **muted role** (create one if needed, e.g. `@Muted`)", "role"),
        ("builder_role", "🔨 Step 10 — Builder Role", "Mention the **Builder role** (e.g. `@Builder`)", "role"),
        ("scripter_role", "💻 Step 11 — Scripter Role", "Mention the **Scripter role** (e.g. `@Scripter`)", "role"),
        ("modeller_role", "🎨 Step 12 — Modeller Role", "Mention the **Modeller role** (e.g. `@Modeller`)", "role"),
        ("ui_role", "🖥️ Step 13 — UI Designer Role", "Mention the **UI Designer role** (e.g. `@UI Designer`)", "role"),
    ]

    for key, title, desc, type_ in steps:
        embed = discord.Embed(title=title, description=desc + "\n\n*Type it in this channel now. You have 60 seconds.*", color=0x5865F2)
        await channel.send(embed=embed)

        try:
            msg = await bot.wait_for("message", check=check, timeout=60)
        except asyncio.TimeoutError:
            await channel.send("⏰ Setup timed out. Run `!setup` to start again.")
            return

        value = None
        if type_ == "role" and msg.role_mentions:
            value = msg.role_mentions[0].id
        elif type_ == "channel" and msg.channel_mentions:
            value = msg.channel_mentions[0].id

        if value is None:
            await channel.send(f"❌ Couldn't read that. Please mention a valid {'role' if type_ == 'role' else 'channel'}. Run `!setup` to restart.")
            return

        set_config(guild.id, key, value)
        await msg.delete()
        await channel.send(f"✅ Saved!", delete_after=2)

    # Done!
    embed = discord.Embed(
        title="🎉 Setup Complete!",
        description=(
            "Your bot is fully configured!\n\n"
            "**Next steps:**\n"
            "`!setup_apply` — Post the application panel\n"
            "`!setup_roles` — Post the role picker\n"
            "`!help` — See all commands\n\n"
            "Your bot is ready to go! 🚀"
        ),
        color=0x00ff00
    )
    await channel.send(embed=embed)

# ============================================================
# SETUP COMMAND
# ============================================================
@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    embed = discord.Embed(
        title="🤖 Young Boy Studios — Bot Setup",
        description=(
            "Welcome to the setup wizard!\n\n"
            "I'll walk you through setting up:\n"
            "✅ Admin & staff roles\n"
            "✅ Member roles\n"
            "✅ All channels\n"
            "✅ Developer skill roles\n"
            "✅ Muted role\n\n"
            "Click the button to begin!"
        ),
        color=0x5865F2
    )
    await ctx.send(embed=embed, view=SetupStartView())

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
# APPLICATION MODALS
# ============================================================
class ApplicationModal(Modal, title="🎮 Young Boy Studios — Apply (Part 1)"):
    roblox_name = TextInput(label="Your Roblox Username", placeholder="e.g. CoolBuilder123", required=True, max_length=50)
    real_name = TextInput(label="What should we call you?", placeholder="First name or nickname", required=True, max_length=30)
    age = TextInput(label="Your Age", placeholder="e.g. 16", required=True, max_length=3)
    role = TextInput(label="Role applying for?", placeholder="Builder / Scripter / Modeller / UI Designer", required=True, max_length=50)
    experience = TextInput(label="Experience & skills", placeholder="How long developing? What are you best at?", required=True, style=discord.TextStyle.paragraph, max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        applications_data[interaction.user.id] = {
            "roblox_name": self.roblox_name.value,
            "real_name": self.real_name.value,
            "age": self.age.value,
            "role": self.role.value,
            "experience": self.experience.value,
            "user": interaction.user,
            "timestamp": datetime.now().isoformat()
        }
        await interaction.response.send_message(
            "✅ **Part 1 done!** Now click **Part 2** to finish your application!", ephemeral=True
        )

class ApplicationModal2(Modal, title="📋 Young Boy Studios — Apply (Part 2)"):
    why_join = TextInput(label="Why do you want to join?", placeholder="What motivates you? What can you bring?", required=True, style=discord.TextStyle.paragraph, max_length=500)
    availability = TextInput(label="Hours per week available?", placeholder="e.g. 10 hours/week, weekends only", required=True, max_length=100)
    portfolio = TextInput(label="Portfolio links (N/A if none)", placeholder="https://... or describe your work", required=True, style=discord.TextStyle.paragraph, max_length=500)
    extra = TextInput(label="Anything else we should know?", placeholder="Extra skills, goals, questions...", required=False, style=discord.TextStyle.paragraph, max_length=300)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id not in applications_data:
            await interaction.response.send_message("❌ Please complete Part 1 first!", ephemeral=True)
            return

        app = applications_data[interaction.user.id]
        app.update({
            "why_join": self.why_join.value,
            "availability": self.availability.value,
            "portfolio": self.portfolio.value,
            "extra": self.extra.value
        })

        apps_channel_id = get(interaction.guild.id, "applications_channel")
        channel = bot.get_channel(apps_channel_id) if apps_channel_id else None

        if channel:
            embed = discord.Embed(title=f"📋 New Application — {app['real_name']}", color=0x5865F2, timestamp=datetime.now())
            embed.set_author(name=str(app['user']), icon_url=app['user'].display_avatar.url)
            embed.add_field(name="🎮 Roblox", value=app['roblox_name'], inline=True)
            embed.add_field(name="👤 Name", value=app['real_name'], inline=True)
            embed.add_field(name="🎂 Age", value=app['age'], inline=True)
            embed.add_field(name="🔨 Role", value=app['role'], inline=True)
            embed.add_field(name="⏰ Availability", value=app['availability'], inline=True)
            embed.add_field(name="⚙️ Experience", value=app['experience'], inline=False)
            embed.add_field(name="💡 Why Join", value=app['why_join'], inline=False)
            embed.add_field(name="📁 Portfolio", value=app['portfolio'], inline=False)
            if app.get('extra'):
                embed.add_field(name="➕ Extra", value=app['extra'], inline=False)
            embed.set_footer(text=f"User ID: {app['user'].id}")
            await channel.send(embed=embed, view=ApplicationReviewView(app['user'].id))

        await interaction.response.send_message("🎉 **Application submitted!** We'll review it and get back to you. Good luck!", ephemeral=True)

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
            try:
                await member.send("🎉 Your application to **Young Boy Studios** has been **accepted!** Welcome to the team!")
            except:
                pass
        await interaction.message.edit(content=f"✅ Accepted by {interaction.user.mention}", view=None)
        await interaction.response.send_message("✅ Accepted!", ephemeral=True)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            await interaction.response.send_message("❌ Staff only!", ephemeral=True)
            return
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send("😔 Your application to **Young Boy Studios** was not accepted this time. Feel free to reapply in 2 weeks!")
            except:
                pass
        await interaction.message.edit(content=f"❌ Declined by {interaction.user.mention}", view=None)
        await interaction.response.send_message("❌ Declined.", ephemeral=True)

    @discord.ui.button(label="⏳ Interview", style=discord.ButtonStyle.blurple)
    async def interview(self, interaction: discord.Interaction, button: Button):
        if not self.is_staff(interaction):
            await interaction.response.send_message("❌ Staff only!", ephemeral=True)
            return
        member = interaction.guild.get_member(self.applicant_id)
        if member:
            try:
                await member.send("👋 Your application looks great! A staff member will DM you to arrange an interview.")
            except:
                pass
        await interaction.message.edit(content=f"⏳ Interview — {interaction.user.mention}", view=None)
        await interaction.response.send_message("⏳ Moved to interview.", ephemeral=True)

class ApplyView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Apply — Part 1", style=discord.ButtonStyle.blurple, custom_id="apply_p1")
    async def part1(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ApplicationModal())

    @discord.ui.button(label="📝 Apply — Part 2", style=discord.ButtonStyle.green, custom_id="apply_p2")
    async def part2(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(ApplicationModal2())

class RoleSelectView(View):
    def __init__(self):
        super().__init__(timeout=None)

    async def toggle_role(self, interaction, role_key, label):
        role_id = get(interaction.guild.id, role_key)
        if not role_id:
            await interaction.response.send_message(f"❌ {label} role not configured. Run `!setup`!", ephemeral=True)
            return
        role = interaction.guild.get_role(role_id)
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"Removed **{label}** role!", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added **{label}** role!", ephemeral=True)

    @discord.ui.button(label="🔨 Builder", style=discord.ButtonStyle.secondary, custom_id="role_builder")
    async def builder(self, interaction, button): await self.toggle_role(interaction, "builder_role", "Builder")

    @discord.ui.button(label="💻 Scripter", style=discord.ButtonStyle.secondary, custom_id="role_scripter")
    async def scripter(self, interaction, button): await self.toggle_role(interaction, "scripter_role", "Scripter")

    @discord.ui.button(label="🎨 Modeller", style=discord.ButtonStyle.secondary, custom_id="role_modeller")
    async def modeller(self, interaction, button): await self.toggle_role(interaction, "modeller_role", "Modeller")

    @discord.ui.button(label="🖥️ UI Designer", style=discord.ButtonStyle.secondary, custom_id="role_ui")
    async def ui(self, interaction, button): await self.toggle_role(interaction, "ui_role", "UI Designer")

# ============================================================
# EVENTS
# ============================================================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online!")
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Young Boy Studios 🎮"))
    status_cycle.start()
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
            color=0x5865F2
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        await channel.send(embed=embed)
    try:
        await member.send(f"👋 Hey **{member.display_name}**! Welcome to **Young Boy Studios**!\nHead to the **#apply-here** channel to join the dev team! 🚀")
    except:
        pass
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        await log.send(f"📥 **{member}** joined. Members: {member.guild.member_count}")

@bot.event
async def on_member_remove(member):
    log_id = get(member.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        await log.send(f"📤 **{member}** left. Members: {member.guild.member_count}")

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    log_id = get(message.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="🗑️ Message Deleted", color=0xff0000)
        embed.add_field(name="Author", value=message.author.mention)
        embed.add_field(name="Channel", value=message.channel.mention)
        embed.add_field(name="Content", value=message.content[:500] or "*(no text)*", inline=False)
        await log.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content: return
    log_id = get(before.guild.id, "logs_channel")
    log = bot.get_channel(log_id) if log_id else None
    if log:
        embed = discord.Embed(title="✏️ Message Edited", color=0xffaa00)
        embed.add_field(name="Author", value=before.author.mention)
        embed.add_field(name="Before", value=before.content[:400] or "*(empty)*", inline=False)
        embed.add_field(name="After", value=after.content[:400] or "*(empty)*", inline=False)
        await log.send(embed=embed)

# ============================================================
# TASKS
# ============================================================
statuses = ["Young Boy Studios 🎮", "Building something epic 🔨", "Hiring developers!", "Roblox game dev team 🚀"]

@tasks.loop(minutes=10)
async def status_cycle():
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=random.choice(statuses)))

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
        color=0x5865F2
    )
    await channel.send(embed=embed, view=ApplyView())
    await ctx.send("✅ Apply panel sent!", delete_after=3)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup_roles(ctx):
    embed = discord.Embed(title="🎭 Pick Your Dev Roles", description="Click buttons to add/remove your skill roles!", color=0x5865F2)
    await ctx.send(embed=embed, view=RoleSelectView())

# ============================================================
# MODERATION
# ============================================================
def is_staff(ctx):
    staff_id = get(ctx.guild.id, "admin_role")
    return ctx.author.guild_permissions.administrator or (staff_id and staff_id in [r.id for r in ctx.author.roles])

@bot.command()
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.kick(reason=reason)
    await ctx.send(f"👢 **{member}** kicked. Reason: {reason}")

@bot.command()
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    await member.ban(reason=reason)
    await ctx.send(f"🔨 **{member}** banned. Reason: {reason}")

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
    role_id = get(ctx.guild.id, "muted_role")
    role = ctx.guild.get_role(role_id) if role_id else None
    if not role: return await ctx.send("❌ Muted role not configured. Run `!setup`!")
    await member.add_roles(role)
    await ctx.send(f"🔇 **{member}** muted for {duration} mins. Reason: {reason}")
    await asyncio.sleep(duration * 60)
    await member.remove_roles(role)
    await ctx.send(f"🔊 **{member}** unmuted.")

@bot.command()
async def unmute(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    role_id = get(ctx.guild.id, "muted_role")
    role = ctx.guild.get_role(role_id) if role_id else None
    if role:
        await member.remove_roles(role)
        await ctx.send(f"🔊 **{member}** unmuted.")

@bot.command()
async def warn(ctx, member: discord.Member, *, reason="No reason"):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    if member.id not in warnings_data:
        warnings_data[member.id] = []
    warnings_data[member.id].append({"reason": reason, "by": str(ctx.author), "time": datetime.now().isoformat()})
    count = len(warnings_data[member.id])
    await ctx.send(f"⚠️ **{member}** warned ({count}/3). Reason: {reason}")
    try:
        await member.send(f"⚠️ You've been warned in **Young Boy Studios**.\nReason: {reason}\nWarnings: {count}/3")
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
    embed = discord.Embed(title=f"⚠️ Warnings for {member}", color=0xffaa00)
    for i, w in enumerate(warns, 1):
        embed.add_field(name=f"Warning {i}", value=f"{w['reason']} — by {w['by']}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def clearwarnings(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ No permission.")
    warnings_data[member.id] = []
    await ctx.send(f"✅ Cleared warnings for **{member}**.")

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
    embed.add_field(name="Roles", value=", ".join([r.mention for r in member.roles[1:]]) or "None", inline=False)
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
    if not role: return await ctx.send("❌ Admin role not configured.")
    members = [m.mention for m in role.members]
    embed = discord.Embed(title="👮 Staff Members", description="\n".join(members) or "None", color=0x5865F2)
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
    if len(options) < 2: return await ctx.send("❌ Need at least 2 options.")
    emojis = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    desc = "\n".join([f"{emojis[i]} {opt}" for i, opt in enumerate(options)])
    embed = discord.Embed(title=f"📊 {question}", description=desc, color=0x5865F2)
    msg = await ctx.send(embed=embed)
    for i in range(len(options)):
        await msg.add_reaction(emojis[i])

@bot.command()
async def suggest(ctx, *, suggestion):
    general_id = get(ctx.guild.id, "general_channel")
    channel = bot.get_channel(general_id) if general_id else ctx.channel
    embed = discord.Embed(title="💡 New Suggestion", description=suggestion, color=0x00ff00)
    embed.set_footer(text=f"Suggested by {ctx.author}")
    msg = await channel.send(embed=embed)
    await msg.add_reaction("✅")
    await msg.add_reaction("❌")
    await ctx.message.delete()

@bot.command()
async def pick(ctx, *choices):
    if not choices: return await ctx.send("❌ Give me some options!")
    await ctx.send(f"🎯 I pick: **{random.choice(choices)}**")

@bot.command()
async def announce(ctx, *, message):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    ann_id = get(ctx.guild.id, "announcements_channel")
    channel = bot.get_channel(ann_id) if ann_id else ctx.channel
    embed = discord.Embed(title="📢 Announcement", description=message, color=0x5865F2, timestamp=datetime.now())
    embed.set_footer(text=f"Posted by {ctx.author}")
    await channel.send("@everyone", embed=embed)

@bot.command()
async def dm(ctx, member: discord.Member, *, message):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    try:
        await member.send(f"📨 **Message from Young Boy Studios staff:**\n{message}")
        await ctx.send(f"✅ DM sent to {member.mention}.")
    except:
        await ctx.send("❌ Couldn't DM that user.")

@bot.command()
async def note(ctx, member: discord.Member, *, note_text):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    if member.id not in notes_data:
        notes_data[member.id] = []
    notes_data[member.id].append({"note": note_text, "by": str(ctx.author)})
    await ctx.send(f"📝 Note added for {member.mention}.")

@bot.command()
async def notes(ctx, member: discord.Member):
    if not is_staff(ctx): return await ctx.send("❌ Staff only!")
    member_notes = notes_data.get(member.id, [])
    if not member_notes: return await ctx.send(f"📝 No notes for {member}.")
    embed = discord.Embed(title=f"📝 Notes for {member}", color=0xffaa00)
    for i, n in enumerate(member_notes, 1):
        embed.add_field(name=f"Note {i} by {n['by']}", value=n['note'], inline=False)
    await ctx.send(embed=embed)

# ============================================================
# HELP
# ============================================================
@bot.command()
async def help(ctx):
    embed = discord.Embed(title="🤖 Young Boy Studios Bot", color=0x5865F2)
    embed.add_field(name="⚙️ Setup", value="`!setup` `!showconfig` `!setconfig` `!setup_apply` `!setup_roles`", inline=False)
    embed.add_field(name="⚖️ Mod", value="`!kick` `!ban` `!unban` `!mute` `!unmute` `!warn` `!warnings` `!clearwarnings` `!purge` `!slowmode` `!lock` `!unlock` `!nick` `!addrole` `!removerole`", inline=False)
    embed.add_field(name="ℹ️ Info", value="`!userinfo` `!serverinfo` `!avatar` `!ping` `!membercount` `!stafflist`", inline=False)
    embed.add_field(name="🎉 Fun", value="`!dice` `!coinflip` `!poll` `!suggest` `!pick` `!announce` `!dm` `!note` `!notes`", inline=False)
    embed.set_footer(text="Prefix: ! | Run !setup to configure the bot")
    await ctx.send(embed=embed)

bot.run(TOKEN)
