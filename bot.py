import os
import re
import sqlite3
import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

TOKEN = os.environ["BOT_TOKEN"]

def parse_ids(name: str) -> set:
    return {int(x) for x in os.environ.get(name, "").split(",") if x.strip()}

# المسؤولين فقط للإضافة والحذف
ADMINS = parse_ids("ADMIN_IDS")
DB = "qc.db"

# الأقسام (اسم الزر، الكلمات المفتاحية)
CATS = [
    ("📋 اعتمادات", ("اعتماد",)),
    ("🧱 خلطات", ("خلط",)),
    ("🏢 شركات", ("شرك", "مقاول", "مورد")),
    ("🧪 مواد", ("ماد", "مواد")),
    ("📘 مواصفات", ("مواصف",)),
    ("📁 أخرى", ()),
]
PAGE = 8  # عدد السجلات في الصفحة الواحدة

HELP_ADMIN = (
    " أهلاً بك يا باهر في بوت بيانات الجودة (مسؤول).\n\n"
    "📂 /menu : تصفح الأقسام بالأزرار\n\n"
    "➕ إضافة بيان نصي:\n"
    "/add النوع | العنوان | التفاصيل\n"
    "مثال:\n`/add خلطة | C35 كباري | سمنت 400 - ركام 1100 - رمل 700 - ماء 160`\n\n"
    "📎 إضافة ملف أو صورة:\n"
    "أرسل الملف مع كتابة التوضيح في الـ Caption بنفس الصيغة:\n"
    "`النوع | العنوان | التفاصيل`\n\n"
    "🔎 البحث الفوري: اكتب أي كلمة مباشرة للبحث عنها\n"
    "🗑 حذف سجل: `/del رقم_السجل`"
)

HELP_VIEWER = (
    " أهلاً بك في بوت بيانات الجودة.\n\n"
    "📂 /menu : تصفح الأقسام بالأزرار\n"
    "🔎 اكتب أي كلمة مباشرة للبحث في السجلات والتصفح."
)

DENIED = "⛔ الإضافة والحذف مقتصرة على المسؤولين فقط. يمكنك التصفح والبحث."

def norm(text: str) -> str:
    """توحيد الحروف العربية لدقة وسهولة البحث"""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"[\u064B-\u065F\u0640]", "", text)
    text = re.sub("[إأآ]", "ا", text)
    return text.replace("ى", "ي").replace("ة", "ه")

def init_db():
    with sqlite3.connect(DB) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT, title TEXT, details TEXT,
                file_id TEXT, file_type TEXT,
                search TEXT,
                created TEXT DEFAULT CURRENT_TIMESTAMP
            )"""
        )

def is_admin(update: Update) -> bool:
    return update.effective_user.id in ADMINS

def allowed(update: Update) -> bool:
    # متاح لأي شخص يدخل البوت للتصفح والبحث تلقائياً
    return True

def parse(text: str):
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1], parts[2] if len(parts) > 2 else ""

def save(category, title, details, file_id=None, file_type=None) -> int:
    search_text = norm(f"{category} {title} {details}")
    with sqlite3.connect(DB) as con:
        cur = con.execute(
            "INSERT INTO records (category, title, details, file_id, file_type, search) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (category, title, details, file_id, file_type, search_text),
        )
        return cur.lastrowid

COLS = "id, category, title, details, file_id, file_type"

def all_rows():
    with sqlite3.connect(DB) as con:
        return con.execute(f"SELECT {COLS} FROM records ORDER BY id DESC").fetchall()

def cat_index(category: str) -> int:
    c = norm(category or "").strip()
    if c.startswith("ال"):
        c = c[2:]
    for i, (_, keys) in enumerate(CATS[:-1]):
        if c.startswith(keys):
            return i
    return len(CATS) - 1

def menu_markup() -> InlineKeyboardMarkup:
    counts = [0] * len(CATS)
    for r in all_rows():
        counts[cat_index(r[1])] += 1
    buttons = [
        InlineKeyboardButton(f"{name} ({counts[i]})", callback_data=f"c:{i}:0")
        for i, (name, _) in enumerate(CATS)
    ]
    return InlineKeyboardMarkup([buttons[i : i + 2] for i in range(0, len(buttons), 2)])

async def send_record(message, row):
    rid, cat, title, details, file_id, file_type = row
    text = f"#{rid} [{cat}]\n{title}\n{details}".strip()
    if file_id and file_type == "photo":
        await message.reply_photo(file_id, caption=text[:1000])
    elif file_id:
        await message.reply_document(file_id, caption=text[:1000])
    else:
        await message.reply_text(text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = HELP_ADMIN if is_admin(update) else HELP_VIEWER
    await update.message.reply_text(help_text, reply_markup=menu_markup())

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📂 اختر القسم المطلوب:", reply_markup=menu_markup())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "m":
        try:
            await q.edit_message_text("📂 اختر القسم المطلوب:", reply_markup=menu_markup())
        except BadRequest:
            pass

    elif data.startswith("c:"):
        _, i, p = data.split(":")
        i, p = int(i), int(p)
        items = [r for r in all_rows() if cat_index(r[1]) == i]
        pages = max(1, (len(items) + PAGE - 1) // PAGE)
        p = min(max(p, 0), pages - 1)
        chunk = items[p * PAGE : (p + 1) * PAGE]
        
        kb = [
            [InlineKeyboardButton(f"#{r[0]} {r[2][:35]}", callback_data=f"r:{r[0]}")]
            for r in chunk
        ]
        
        nav = []
        if p > 0:
            nav.append(InlineKeyboardButton("◀ السابق", callback_data=f"c:{i}:{p - 1}"))
        if p < pages - 1:
            nav.append(InlineKeyboardButton("التالي ▶", callback_data=f"c:{i}:{p + 1}"))
        if nav:
            kb.append(nav)
            
        kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="m")])
        
        title = f"{CATS[i][0]} — إجمالي ({len(items)} سجل)"
        if pages > 1:
            title += f"\nصفحة ({p + 1}/{pages})"
        if not items:
            title += "\n\n⚠️ لا توجد سجلات في هذا القسم حتى الآن."
            
        try:
            await q.edit_message_text(title, reply_markup=InlineKeyboardMarkup(kb))
        except BadRequest:
            pass

    elif data.startswith("r:"):
        with sqlite3.connect(DB) as con:
            row = con.execute(
                f"SELECT {COLS} FROM records WHERE id=?", (int(data[2:]),)
            ).fetchone()
        if row:
            await send_record(q.message, row)
        else:
            await q.message.reply_text("⚠️ عذراً، هذا السجل تم حذفه مسبقاً.")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(DENIED)
        return
    data = parse(update.message.text.partition(" ")[2])
    if not data:
        await update.message.reply_text("❌ الصيغة غير صحيحة.\nالصيغة الصحيحة:\n`/add النوع | العنوان | التفاصيل`", parse_mode="Markdown")
        return
    rid = save(*data)
    await update.message.reply_text(f"✅ تم الحفظ بنجاح برقم سجل: `#{rid}`", parse_mode="Markdown")

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(DENIED)
        return
    msg = update.message
    caption = (msg.caption or "").replace("/add", "", 1).strip()
    
    if msg.document:
        file_id, file_type = msg.document.file_id, "document"
        file_name = msg.document.file_name or ""
    elif msg.photo:
        file_id, file_type = msg.photo[-1].file_id, "photo"
        file_name = ""
    else:
        return

    data = parse(caption)
    if not data:
        title = caption or file_name or "ملف بدون عنوان"
        data = ("أخرى", title, file_name if caption else "")
        
    rid = save(*data, file_id=file_id, file_type=file_type)
    await msg.reply_text(f"✅ تم حفظ الملف بنجاح برقم سجل: `#{rid}`", parse_mode="Markdown")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_input = update.message.text
    if not text_input or text_input.startswith("/"):
        return

    words = norm(text_input).split()
    if not words:
        return

    where = " AND ".join(["search LIKE ?"] * len(words))
    with sqlite3.connect(DB) as con:
        rows = con.execute(
            f"SELECT {COLS} FROM records WHERE {where} ORDER BY id DESC LIMIT 10",
            [f"%{w}%" for w in words],
        ).fetchall()

    if not rows:
        await update.message.reply_text("🔍 لم يتم العثور على نتائج مطابقة. جرب كلمات أخرى.")
        return

    for row in rows:
        await send_record(update.message, row)

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(DENIED)
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("❌ الصيغة الصحيحة للحذف:\n`/del رقم_السجل`", parse_mode="Markdown")
        return
        
    record_id = int(context.args[0])
    with sqlite3.connect(DB) as con:
        n = con.execute("DELETE FROM records WHERE id=?", (record_id,)).rowcount
        
    if n:
        await update.message.reply_text(f"🗑 تم حذف السجل رقم `#{record_id}` بنجاح.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"⚠️ عذراً، رقم السجل `#{record_id}` غير موجود.", parse_mode="Markdown")

def make_request():
    return HTTPXRequest(
        connect_timeout=30,
        read_timeout=30,
        write_timeout=30,
        pool_timeout=30,
        httpx_kwargs={"transport": httpx.AsyncHTTPTransport(local_address="0.0.0.0")},
    )

def main():
    init_db()
    app = (
        Application.builder()
        .token(TOKEN)
        .request(make_request())
        .get_updates_request(make_request())
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("del", delete))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, add_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search))
    
    print("Bot is running smoothly...")
    app.run_polling()

if __name__ == "__main__":
    main()