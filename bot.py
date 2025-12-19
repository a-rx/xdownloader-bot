import os
import time
import sqlite3
import yt_dlp

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters
)

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "8289166397:AAEST3GJseXHl-FeNNdczz4gQ7VtrX3PdXo"
ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS") or "1711726347").split(",")]

FREE_LIMIT = 50 * 1024 * 1024           # 50MB
PREMIUM_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB
COOLDOWN_SECONDS = 30
DOWNLOAD_DIR = "downloads"

PLANS = {
    "30": {"stars": 100, "days": 30},
    "90": {"stars": 250, "days": 90},
    "life": {"stars": 500, "days": 0}
}

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ================== DATABASE ==================
conn = sqlite3.connect("bot.db", check_same_thread=False)
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    premium_until INTEGER DEFAULT 0,
    lang TEXT DEFAULT 'ar'
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS payments (
    user_id INTEGER,
    stars INTEGER,
    ts INTEGER
)
""")
conn.commit()

# ================== HELPERS ==================
LAST_REQUEST = {}

def is_premium(user_id):
    c.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    return row and row[0] > int(time.time())

def is_spam(user_id):
    now = time.time()
    last = LAST_REQUEST.get(user_id, 0)
    if now - last < COOLDOWN_SECONDS:
        return True
    LAST_REQUEST[user_id] = now
    return False

def t(user_id, ar, en):
    c.execute("SELECT lang FROM users WHERE user_id=?", (user_id,))
    r = c.fetchone()
    return ar if not r or r[0] == "ar" else en

# ================== KEYBOARDS ==================
def lang_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇸🇦 عربي", callback_data="lang_ar"),
         InlineKeyboardButton("🇺🇸 English", callback_data="lang_en")]
    ])

def quality_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("360p", callback_data="q_360"),
         InlineKeyboardButton("720p", callback_data="q_720")],
        [InlineKeyboardButton("1080p", callback_data="q_1080")],
        [InlineKeyboardButton("أفضل جودة", callback_data="q_best")]
    ])

def plans_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 30 يوم", callback_data="plan_30")],
        [InlineKeyboardButton("🔥 90 يوم", callback_data="plan_90")],
        [InlineKeyboardButton("💎 مدى الحياة", callback_data="plan_life")]
    ])

# ================== COMMANDS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    conn.commit()

    await update.message.reply_text(
        "Choose language / اختر اللغة",
        reply_markup=lang_keyboard()
    )

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = q.data.split("_")[1]
    c.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, q.from_user.id))
    conn.commit()

    await q.message.reply_text(
        t(q.from_user.id,
          "📥 أرسل رابط فيديو من X",
          "📥 Send X video link")
    )

# ================== LINK HANDLER ==================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = update.message.text

    if is_spam(uid):
        await update.message.reply_text(t(uid, "⏳ انتظر قليلًا", "⏳ Please wait"))
        return

    if "x.com" not in url and "twitter.com" not in url:
        return

    context.user_data["url"] = url
    await update.message.reply_text(
        t(uid, "🎥 اختر الجودة", "🎥 Choose quality"),
        reply_markup=quality_keyboard()
    )

# ================== DOWNLOAD (FIXED) ==================
async def download_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id

    url = context.user_data.get("url")
    quality_key = q.data.split("_")[1]

    FORMAT_MAP = {
        "360": "best[height<=360]/best",
        "720": "best[height<=720]/best",
        "1080": "best[height<=1080]/best",
        "best": "best"
    }

    ydl_opts = {
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "format": FORMAT_MAP.get(quality_key, "best"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info)

            if not file_path.endswith(".mp4"):
                file_path = file_path.rsplit(".", 1)[0] + ".mp4"

        size = os.path.getsize(file_path)
        limit = PREMIUM_LIMIT if is_premium(uid) else FREE_LIMIT

        if size > limit:
            os.remove(file_path)
            await q.message.reply_text(
                t(uid,
                  "🚫 الفيديو كبير، اشترك للتحميل",
                  "🚫 Large video, subscribe to download"),
                reply_markup=plans_keyboard()
            )
            return

        await q.message.reply_document(
            document=open(file_path, "rb"),
            caption="✅ تم التحميل بنجاح"
        )
        os.remove(file_path)

    except Exception as e:
        print("DOWNLOAD ERROR:", e)
        await q.message.reply_text(
            t(uid,
              "❌ تعذر تحميل هذا الفيديو (جرّب رابطًا آخر)",
              "❌ Failed to download this video (try another link)")
        )

# ================== PAYMENT ==================
async def buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    plan_key = q.data.split("_")[1]
    plan = PLANS[plan_key]

    await context.bot.send_invoice(
        chat_id=q.from_user.id,
        title="Premium Subscription",
        description="Download large X videos",
        payload=f"premium_{plan_key}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("Premium", plan["stars"])]
    )

async def pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payload = update.message.successful_payment.invoice_payload
    plan_key = payload.split("_")[1]
    plan = PLANS[plan_key]
    uid = update.effective_user.id

    premium_until = 9999999999 if plan["days"] == 0 else int(time.time()) + plan["days"] * 86400

    c.execute("UPDATE users SET premium_until=? WHERE user_id=?", (premium_until, uid))
    c.execute("INSERT INTO payments VALUES (?,?,?)", (uid, plan["stars"], int(time.time())))
    conn.commit()

    await update.message.reply_text("🎉 Premium activated!")

# ================== ADMIN ==================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]

    c.execute("SELECT SUM(stars) FROM payments")
    stars = c.fetchone()[0] or 0

    await update.message.reply_text(
        f"👥 Users: {users}\n⭐ Stars earned: {stars}"
    )

# ================== RUN ==================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))

    app.add_handler(CallbackQueryHandler(set_lang, pattern="lang_"))
    app.add_handler(CallbackQueryHandler(download_video, pattern="q_"))
    app.add_handler(CallbackQueryHandler(buy_plan, pattern="plan_"))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    print("🤖 Bot Running...")
    app.run_polling()

if __name__ == "__main__":
    main()