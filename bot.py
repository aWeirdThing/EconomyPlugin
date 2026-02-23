import os
import discord
from discord import app_commands
import uuid
import firebase_admin
from firebase_admin import credentials, firestore

# ---------------- LOAD ENV ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")
FIREBASE_PRIVATE_KEY = os.getenv("FIREBASE_PRIVATE_KEY").replace("\\n", "\n")
FIREBASE_CLIENT_EMAIL = os.getenv("FIREBASE_CLIENT_EMAIL")

# ---------------- FIREBASE INIT ----------------
cred = credentials.Certificate({
    "type": "service_account",
    "project_id": FIREBASE_PROJECT_ID,
    "private_key": FIREBASE_PRIVATE_KEY,
    "client_email": FIREBASE_CLIENT_EMAIL,
    "token_uri": "https://oauth2.googleapis.com/token"
})
firebase_admin.initialize_app(cred)
db = firestore.client()

# ---------------- DISCORD SETUP ----------------
intents = discord.Intents.default()
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ---------------- DATABASE FUNCTIONS ----------------

def get_user(discord_id):
    """Get user info from Firestore"""
    doc = db.collection("users").document(str(discord_id)).get()
    if doc.exists:
        return doc.to_dict()
    return None

def create_user(discord_id):
    """Create new user in Firestore"""
    uid = str(uuid.uuid4())
    db.collection("users").document(str(discord_id)).set({
        "uuid": uid,
        "discord_id": str(discord_id),
        "balance": 0
    })
    return uid

def get_balance(discord_id):
    """Get user balance"""
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    return user["balance"]

def update_balance(discord_id, amount):
    """Update user balance"""
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    new_balance = user["balance"] + amount
    db.collection("users").document(str(discord_id)).update({"balance": new_balance})
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

    db.collection("marketplace").add({
        "seller_uuid": user["uuid"],
        "item": item,
        "amount": amount,
        "price": price
    })

    await interaction.response.send_message(f"📦 Listed {amount}x {item} for {price} each.")

# -------- MARKETPLACE --------
@tree.command(name="market", description="View marketplace listings")
async def market(interaction: discord.Interaction):
    docs = db.collection("marketplace").limit(15).stream()
    listings = [doc.to_dict() | {"id": doc.id} for doc in docs]

    if not listings:
        return await interaction.response.send_message("📭 Marketplace is empty.")

    msg = "**🛒 Marketplace Listings**\n"
    for row in listings:
        msg += f"ID `{row['id']}` | {row['amount']}x {row['item']} | {row['price']} each\n"

    await interaction.response.send_message(msg)

# -------- BUY --------
@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: str):
    listing_ref = db.collection("marketplace").document(listing_id)
    listing_doc = listing_ref.get()
    if not listing_doc.exists:
        return await interaction.response.send_message("❌ Listing not found.")
    listing = listing_doc.to_dict()
    total_price = listing["price"] * listing["amount"]
    buyer_balance = get_balance(interaction.user.id)
    if buyer_balance < total_price:
        return await interaction.response.send_message("❌ Not enough money.")

    buyer = get_user(interaction.user.id)
    # Find seller by uuid
    seller_docs = db.collection("users").where("uuid", "==", listing["seller_uuid"]).stream()
    seller = None
    for s in seller_docs:
        seller = s.to_dict()
        break
    if not seller:
        return await interaction.response.send_message("❌ Seller not found.")

    update_balance(interaction.user.id, -total_price)
    update_balance(seller["discord_id"], total_price)

    db.collection("transactions").add({
        "buyer_uuid": buyer["uuid"],
        "seller_uuid": seller["uuid"],
        "item": listing["item"],
        "amount": listing["amount"],
        "price": listing["price"]
    })

    listing_ref.delete()
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
