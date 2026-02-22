import os
import discord
from discord import app_commands
import uuid
import requests
from supabase import create_client
from dotenv import load_dotenv

# ---------------- LOAD ENV ----------------
load_dotenv()

# Discord + Supabase
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Optional API settings
API_URL = os.getenv("API_URL")  # e.g., https://my-economy-api.up.railway.app
API_KEY = os.getenv("API_KEY")  # optional secret key for API calls

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Discord setup
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------- DATABASE FUNCTIONS ----------------

def get_user(discord_id):
    """Get user info from Supabase"""
    res = supabase.table("users").select("*").eq("discord_id", str(discord_id)).execute()
    return res.data[0] if res.data else None

def create_user(discord_id):
    """Create new user in Supabase"""
    uid = str(uuid.uuid4())
    supabase.table("users").insert({
        "uuid": uid,
        "discord_id": str(discord_id),
        "balance": 0
    }).execute()
    return uid

def get_balance(discord_id):
    """Get balance from API or Supabase"""
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)

    # Use API if configured
    if API_URL:
        try:
            r = requests.get(f"{API_URL}/balance/{user['uuid']}", headers={"x-api-key": API_KEY})
            return r.json().get("balance", 0)
        except Exception:
            return user["balance"]
    return user["balance"]

def update_balance(discord_id, amount):
    """Update balance via API or Supabase"""
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)

    if API_URL:
        try:
            # For simplicity, queue as /give call if positive, negative handled locally
            if amount > 0:
                requests.post(f"{API_URL}/give", json={
                    "uuid": user["uuid"],
                    "item": "currency",
                    "amount": amount
                }, headers={"x-api-key": API_KEY})
                return get_balance(discord_id)
            else:
                # Negative amount: update locally (Supabase) to avoid negative API items
                new_balance = user["balance"] + amount
                supabase.table("users").update({"balance": new_balance}).eq("discord_id", str(discord_id)).execute()
                return new_balance
        except Exception:
            # fallback to local Supabase
            new_balance = user["balance"] + amount
            supabase.table("users").update({"balance": new_balance}).eq("discord_id", str(discord_id)).execute()
            return new_balance
    else:
        new_balance = user["balance"] + amount
        supabase.table("users").update({"balance": new_balance}).eq("discord_id", str(discord_id)).execute()
        return new_balance

# ---------------- COMMANDS ----------------

@tree.command(name="help", description="Show all economy commands")
async def help_cmd(interaction: discord.Interaction):
    msg = """
**💰 Economy Commands**
/balance - Check your balance  
/sell item amount price - List item  
/market - View listings  
/buy listing_id - Buy listing  
/give user amount - Give money  

**👑 Admin Commands**
/addmoney user amount  
/removemoney user amount  
"""
    await interaction.response.send_message(msg, ephemeral=True)

# -------- BALANCE --------
@tree.command(name="balance", description="Check your balance")
async def balance(interaction: discord.Interaction):
    bal = get_balance(interaction.user.id)
    await interaction.response.send_message(f"💰 Your balance: **{bal}**")

# -------- GIVE MONEY --------
@tree.command(name="give", description="Give money to another user")
async def give(interaction: discord.Interaction, user: discord.User, amount: float):
    if amount <= 0:
        return await interaction.response.send_message("❌ Invalid amount.")
    sender_balance = get_balance(interaction.user.id)
    if sender_balance < amount:
        return await interaction.response.send_message("❌ Not enough money.")

    update_balance(interaction.user.id, -amount)
    update_balance(user.id, amount)

    await interaction.response.send_message(f"✅ Gave {amount} to {user.mention}")

# -------- SELL ITEM --------
@tree.command(name="sell", description="List an item on the marketplace")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    user = get_user(interaction.user.id)
    if not user:
        create_user(interaction.user.id)
        user = get_user(interaction.user.id)

    supabase.table("marketplace").insert({
        "seller_uuid": user["uuid"],
        "item": item,
        "amount": amount,
        "price": price
    }).execute()

    await interaction.response.send_message(f"📦 Listed {amount}x {item} for {price} each.")

# -------- MARKETPLACE --------
@tree.command(name="market", description="View marketplace listings")
async def market(interaction: discord.Interaction):
    data = supabase.table("marketplace").select("*").execute().data
    if not data:
        return await interaction.response.send_message("📭 Marketplace is empty.")

    msg = "**🛒 Marketplace Listings**\n"
    for row in data[:15]:
        msg += f"ID `{row['id']}` | {row['amount']}x {row['item']} | {row['price']} each\n"

    await interaction.response.send_message(msg)

# -------- BUY --------
@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: int):
    listing = supabase.table("marketplace").select("*").eq("id", listing_id).execute().data
    if not listing:
        return await interaction.response.send_message("❌ Listing not found.")
    listing = listing[0]
    total_price = listing["price"] * listing["amount"]
    buyer_balance = get_balance(interaction.user.id)
    if buyer_balance < total_price:
        return await interaction.response.send_message("❌ Not enough money.")

    buyer = get_user(interaction.user.id)
    seller = supabase.table("users").select("*").eq("uuid", listing["seller_uuid"]).execute().data[0]

    update_balance(interaction.user.id, -total_price)
    update_balance(seller["discord_id"], total_price)

    supabase.table("transactions").insert({
        "buyer_uuid": buyer["uuid"],
        "seller_uuid": seller["uuid"],
        "item": listing["item"],
        "amount": listing["amount"],
        "price": listing["price"]
    }).execute()

    supabase.table("marketplace").delete().eq("id", listing_id).execute()

    await interaction.response.send_message(f"✅ Bought {listing['amount']}x {listing['item']} for {total_price}")

# ---------------- ADMIN ----------------
@tree.command(name="addmoney", description="Admin: Add money")
@app_commands.checks.has_permissions(administrator=True)
async def addmoney(interaction: discord.Interaction, user: discord.User, amount: float):
    update_balance(user.id, amount)
    await interaction.response.send_message(f"💸 Added {amount} to {user.mention}")

@tree.command(name="removemoney", description="Admin: Remove money")
@app_commands.checks.has_permissions(administrator=True)
async def removemoney(interaction: discord.Interaction, user: discord.User, amount: float):
    update_balance(user.id, -amount)
    await interaction.response.send_message(f"💸 Removed {amount} from {user.mention}")

# ---------------- STARTUP ----------------
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
