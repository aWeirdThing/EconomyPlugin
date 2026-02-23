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
    doc = db.collection("users").document(str(discord_id)).get()
    return doc.to_dict() if doc.exists else None

def create_user(discord_id):
    uid = str(uuid.uuid4())
    db.collection("users").document(str(discord_id)).set({
        "uuid": uid,
        "discord_id": str(discord_id),
        "balance": 0
    })
    return uid

def get_balance(discord_id):
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    return user["balance"]

def update_balance(discord_id, amount):
    user = get_user(discord_id)
    if not user:
        create_user(discord_id)
        user = get_user(discord_id)
    new_balance = user["balance"] + amount
    db.collection("users").document(str(discord_id)).update({"balance": new_balance})
    return new_balance

# ---------------- COMMANDS ----------------
@tree.command(name="link", description="Link your Discord account to the economy system")
async def link(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user = get_user(interaction.user.id)
    if user:
        await interaction.followup.send("🔗 Your account is already linked!", ephemeral=True)
    else:
        create_user(interaction.user.id)
        await interaction.followup.send("✅ Account linked successfully!", ephemeral=True)

@tree.command(name="help", description="Show all economy commands")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    msg = """
**💰 Economy Commands**
/link - Link your account  
/balance - Check your balance  
/sell item amount price - List item  
/market - View listings  
/buy listing_id - Buy listing  
/give user amount - Give money  

**👑 Admin Commands**
/addmoney user amount  
/removemoney user amount  
"""
    await interaction.followup.send(msg, ephemeral=True)

@tree.command(name="balance", description="Check your balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    bal = get_balance(interaction.user.id)
    await interaction.followup.send(f"💰 Your balance: **{bal}**", ephemeral=True)

@tree.command(name="give", description="Give money to another user")
async def give(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    if amount <= 0:
        return await interaction.followup.send("❌ Invalid amount.", ephemeral=True)
    sender_balance = get_balance(interaction.user.id)
    if sender_balance < amount:
        return await interaction.followup.send("❌ Not enough money.", ephemeral=True)

    update_balance(interaction.user.id, -amount)
    update_balance(user.id, amount)
    await interaction.followup.send(f"✅ Gave {amount} to {user.mention}", ephemeral=True)

@tree.command(name="sell", description="List an item on the marketplace")
async def sell(interaction: discord.Interaction, item: str, amount: int, price: float):
    await interaction.response.defer(ephemeral=True)
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
    await interaction.followup.send(f"📦 Listed {amount}x {item} for {price} each.", ephemeral=True)

@tree.command(name="market", description="View marketplace listings")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    docs = db.collection("marketplace").limit(15).stream()
    listings = [doc.to_dict() | {"id": doc.id} for doc in docs]

    if not listings:
        return await interaction.followup.send("📭 Marketplace is empty.", ephemeral=True)

    msg = "**🛒 Marketplace Listings**\n"
    for row in listings:
        msg += f"ID `{row['id']}` | {row['amount']}x {row['item']} | {row['price']} each\n"
    await interaction.followup.send(msg, ephemeral=True)

@tree.command(name="buy", description="Buy a marketplace listing")
async def buy(interaction: discord.Interaction, listing_id: str):
    await interaction.response.defer(ephemeral=True)
    listing_ref = db.collection("marketplace").document(listing_id)
    listing_doc = listing_ref.get()
    if not listing_doc.exists:
        return await interaction.followup.send("❌ Listing not found.", ephemeral=True)

    listing = listing_doc.to_dict()
    total_price = listing["price"] * listing["amount"]
    buyer_balance = get_balance(interaction.user.id)
    if buyer_balance < total_price:
        return await interaction.followup.send("❌ Not enough money.", ephemeral=True)

    buyer = get_user(interaction.user.id)
    seller_docs = db.collection("users").where("uuid", "==", listing["seller_uuid"]).stream()
    seller = None
    for s in seller_docs:
        seller = s.to_dict()
        break
    if not seller:
        return await interaction.followup.send("❌ Seller not found.", ephemeral=True)

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
    await interaction.followup.send(f"✅ Bought {listing['amount']}x {listing['item']} for {total_price}", ephemeral=True)

# ---------------- ADMIN ----------------
@tree.command(name="addmoney", description="Admin: Add money")
@app_commands.checks.has_permissions(administrator=True)
async def addmoney(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    update_balance(user.id, amount)
    await interaction.followup.send(f"💸 Added {amount} to {user.mention}", ephemeral=True)

@tree.command(name="removemoney", description="Admin: Remove money")
@app_commands.checks.has_permissions(administrator=True)
async def removemoney(interaction: discord.Interaction, user: discord.User, amount: float):
    await interaction.response.defer(ephemeral=True)
    update_balance(user.id, -amount)
    await interaction.followup.send(f"💸 Removed {amount} from {user.mention}", ephemeral=True)

# ---------------- STARTUP ----------------
@bot.event
async def on_ready():
    await tree.sync()
    print(f"✅ Bot online as {bot.user}")

bot.run(DISCORD_TOKEN)
