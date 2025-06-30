import logging
import random
import psycopg2
import json
from datetime import datetime, timedelta, date
from telegram import Update, ChatMember, Chat, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes, ChatMemberHandler
)

# --- إعدادات الاتصال بقاعدة البيانات ---
SUPABASE_URL = "https://vcwwlrsvnxcjwvsoxdpd.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZjd3dscnN2bnhjand2c294ZHBkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTEyNTkxMTMsImV4cCI6MjA2NjgzNTExM30.pYijTdCwh_CLjHbSGphWss11wGPjb5UjWJlWRgY7W68"
DB_HOST = "db.vcwwlrsvnxcjwvsoxdpd.supabase.co"
DB_NAME = "postgres"
DB_USER = "postgres"
DB_PASSWORD = SUPABASE_KEY
DB_PORT = 5432

BOT_TOKEN = "7788824693:AAHg8E72ySppXpxG2KScfnppibDFJ-ovGTU"

# --- إعدادات اللوج ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- دوال مساعدة للاتصال بقاعدة البيانات ---
def get_db_conn():
    return psycopg2.connect(
        host=DB_HOST,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        port=DB_PORT
    )

def ensure_user_and_group(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    # إضافة المستخدم إذا لم يكن موجودًا
    cur.execute("""
        INSERT INTO users (telegram_id, username, first_name, last_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET username=EXCLUDED.username, first_name=EXCLUDED.first_name, last_name=EXCLUDED.last_name
        RETURNING id;
    """, (user.id, user.username, user.first_name, user.last_name))
    user_row = cur.fetchone()
    if user_row is None:
        user_id = None
    else:
        user_id = user_row[0]
    # إضافة الجروب إذا لم يكن موجودًا
    cur.execute("""
        INSERT INTO groups (telegram_id, title, username)
        VALUES (%s, %s, %s)
        ON CONFLICT (telegram_id) DO UPDATE SET title=EXCLUDED.title, username=EXCLUDED.username
        RETURNING id;
    """, (chat.id, chat.title, getattr(chat, 'username', None)))
    group_row = cur.fetchone()
    if group_row is None:
        group_id = None
    else:
        group_id = group_row[0]
    # إضافة سجل إحصائيات المستخدم في الجروب إذا لم يكن موجودًا
    cur.execute("""
        INSERT INTO user_group_stats (user_id, group_id)
        VALUES (%s, %s)
        ON CONFLICT (user_id, group_id) DO NOTHING;
    """, (user_id, group_id))
    conn.commit()
    cur.close()
    conn.close()
    return user_id, group_id

def add_xp_and_coins(user, chat, message_id):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    # حساب XP عشوائي (10-20)
    xp = random.randint(10, 20)
    coins = random.randint(1, 5)
    # تحديث الإحصائيات
    cur.execute("""
        UPDATE user_group_stats
        SET xp = xp + %s, coins = coins + %s, messages_count = messages_count + 1, last_message_at = NOW()
        WHERE user_id = %s AND group_id = %s;
    """, (xp, coins, user_id, group_id))
    # تسجيل الرسالة
    cur.execute("""
        INSERT INTO message_logs (user_id, group_id, message_id, xp_gained, coins_gained)
        VALUES (%s, %s, %s, %s, %s);
    """, (user_id, group_id, message_id, xp, coins))
    # تحديث المستوى
    cur.execute("""
        SELECT xp, level FROM user_group_stats WHERE user_id = %s AND group_id = %s;
    """, (user_id, group_id))
    row = cur.fetchone()
    if row is None:
        xp_now, level_now = 0, 1
    else:
        xp_now, level_now = row
    cur.execute("SELECT level, xp_required FROM level_config ORDER BY level;")
    levels = cur.fetchall()
    new_level = level_now
    for lvl, xp_req in levels:
        if xp_now >= xp_req:
            new_level = lvl
        else:
            break
            new_level = lvl
            # 'break' removed because it's not inside a loop at the correct indentation
            # The for loop should be properly indented to allow 'break'
    conn.commit()
    cur.close()
    conn.close()
    return xp, coins, new_level

def get_progress(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    cur.execute("SELECT xp, level, coins FROM user_group_stats WHERE user_id = %s AND group_id = %s;", (user_id, group_id))
    row = cur.fetchone()
    if row is None:
        xp, level, coins = 0, 1, 0
    else:
        xp, level, coins = row
    cur.execute("SELECT xp_required FROM level_config WHERE level = %s;", (level,))
    xp_start_row = cur.fetchone()
    xp_start = xp_start_row[0] if xp_start_row else 0
    cur.execute("SELECT xp_required FROM level_config WHERE level = %s;", (level+1,))
    row = cur.fetchone()
    xp_next = row[0] if row else xp_start + 1000
    cur.execute("SELECT rank_display FROM level_config WHERE level = %s;", (level,))
    rank_row = cur.fetchone()
    rank = rank_row[0] if rank_row else None
    cur.close()
    conn.close()
    return xp, level, xp_start, xp_next, rank, coins

def check_and_award_badges(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    # الحصول على إحصائيات المستخدم
    cur.execute("""
        SELECT xp, level, coins, messages_count FROM user_group_stats 
        WHERE user_id = %s AND group_id = %s;
    """, (user_id, group_id))
    row = cur.fetchone()
    if row is None:
        xp, level, coins, messages = 0, 1, 0, 0
    else:
        xp, level, coins, messages = row
    # الحصول على الشارات المكتسبة
    cur.execute("""
        SELECT badge_id FROM user_badges WHERE user_id = %s AND group_id = %s;
    """, (user_id, group_id))
    earned_badges = [row[0] for row in cur.fetchall()]
    # الحصول على جميع الشارات
    cur.execute("SELECT id, name, description, icon, condition_type, condition_value, reward_xp, reward_coins FROM badges;")
    badges = cur.fetchall()
    new_badges = []
    for badge_id, name, desc, icon, cond_type, cond_value, reward_xp, reward_coins in badges:
        if badge_id in earned_badges:
            continue
        # فحص الشروط
        condition_met = False
        if cond_type == 'messages' and messages >= cond_value:
            condition_met = True
        elif cond_type == 'level' and level >= cond_value:
            condition_met = True
        elif cond_type == 'coins' and coins >= cond_value:
            condition_met = True
        elif cond_type == 'xp' and xp >= cond_value:
            condition_met = True
        if condition_met:
            # منح الشارة
            cur.execute("""
                INSERT INTO user_badges (user_id, group_id, badge_id) VALUES (%s, %s, %s);
            """, (user_id, group_id, badge_id))
            # إضافة المكافآت
            cur.execute("""
                UPDATE user_group_stats SET xp = xp + %s, coins = coins + %s 
                WHERE user_id = %s AND group_id = %s;
            """, (reward_xp, reward_coins, user_id, group_id))
            new_badges.append((name, icon, reward_xp, reward_coins))
    conn.commit()
    cur.close()
    conn.close()
    return new_badges

def get_daily_quests(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    today = date.today()
    # الحصول على المهام اليومية
    cur.execute("""
        SELECT id, title, description, quest_type, target_value, reward_xp, reward_coins 
        FROM daily_quests WHERE is_active = TRUE;
    """)
    quests = cur.fetchall()
    # الحصول على تقدم المهام
    cur.execute("""
        SELECT quest_id, current_progress, is_completed 
        FROM user_quest_progress 
        WHERE user_id = %s AND group_id = %s AND quest_date = %s;
    """, (user_id, group_id, today))
    progress = {row[0]: {'progress': row[1], 'completed': row[2]} for row in cur.fetchall()}
    cur.close()
    conn.close()
    return quests, progress

def get_store_items():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, description, icon, price, item_type, effect_type 
        FROM store_items WHERE is_active = TRUE ORDER BY price;
    """)
    items = cur.fetchall()
    cur.close()
    conn.close()
    return items

def buy_item(user, chat, item_id):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    # الحصول على معلومات العنصر
    cur.execute("SELECT name, price, effect_type, effect_value, duration_hours FROM store_items WHERE id = %s;", (item_id,))
    item = cur.fetchone()
    if not item:
        cur.close()
        conn.close()
        return False, "العنصر غير موجود"
    name, price, effect_type, effect_value, duration_hours = item
    # فحص العملات
    cur.execute("SELECT coins FROM user_group_stats WHERE user_id = %s AND group_id = %s;", (user_id, group_id))
    coins_row = cur.fetchone()
    if coins_row is None:
        cur.close()
        conn.close()
        return False, "لا يوجد رصيد عملات لهذا المستخدم"
    user_coins = coins_row[0]
    if user_coins < price:
        cur.close()
        conn.close()
        return False, f"لا تملك عملات كافية. تحتاج {price} عملة"
    # شراء العنصر
    cur.execute("""
        UPDATE user_group_stats SET coins = coins - %s WHERE user_id = %s AND group_id = %s;
    """, (price, user_id, group_id))
    # إضافة العنصر للمخزون
    expires_at = None
    if duration_hours:
        expires_at = datetime.now() + timedelta(hours=duration_hours)
    cur.execute("""
        INSERT INTO user_inventory (user_id, group_id, item_id, expires_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, group_id, item_id) 
        DO UPDATE SET quantity = user_inventory.quantity + 1, expires_at = EXCLUDED.expires_at;
    """, (user_id, group_id, item_id, expires_at))
    conn.commit()
    cur.close()
    conn.close()
    return True, f"تم شراء {name} بنجاح!"

def get_user_badges(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    cur.execute("""
        SELECT b.name, b.description, b.icon, ub.earned_at
        FROM user_badges ub
        JOIN badges b ON ub.badge_id = b.id
        WHERE ub.user_id = %s AND ub.group_id = %s
        ORDER BY ub.earned_at DESC;
    """, (user_id, group_id))
    badges = cur.fetchall()
    cur.close()
    conn.close()
    return badges

def get_clan_info(user, chat):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    cur.execute("""
        SELECT c.id, c.name, c.description, c.total_xp, c.member_count, cm.role
        FROM clans c
        JOIN clan_members cm ON c.id = cm.clan_id
        WHERE c.group_id = %s AND cm.user_id = %s;
    """, (group_id, user_id))
    clan = cur.fetchone()
    cur.close()
    conn.close()
    return clan

def create_clan(user, chat, clan_name, description=""):
    conn = get_db_conn()
    cur = conn.cursor()
    user_id, group_id = ensure_user_and_group(user, chat)
    # فحص إذا كان المستخدم في كلان
    cur.execute("""
        SELECT clan_id FROM clan_members cm
        JOIN clans c ON cm.clan_id = c.id
        WHERE cm.user_id = %s AND c.group_id = %s;
    """, (user_id, group_id))
    if cur.fetchone():
        cur.close()
        conn.close()
        return False, "أنت بالفعل في كلان"
    # إنشاء الكلان
    cur.execute("""
        INSERT INTO clans (group_id, name, description, leader_id)
        VALUES (%s, %s, %s, %s) RETURNING id;
    """, (group_id, clan_name, description, user_id))
    result = cur.fetchone()
    if result is None:
        cur.close()
        conn.close()
        return False, "حدث خطأ أثناء إنشاء الكلان"
    clan_id = result[0]
    # إضافة المستخدم كقائد
    cur.execute("""
        INSERT INTO clan_members (clan_id, user_id, role)
        VALUES (%s, %s, 'leader');
    """, (clan_id, user_id))
    conn.commit()
    cur.close()
    conn.close()
    return True, f"تم إنشاء كلان {clan_name} بنجاح!"

def get_top_players(chat, limit=10):
    conn = get_db_conn()
    cur = conn.cursor()
    group_id = ensure_user_and_group(None, chat)[1]
    cur.execute("""
        SELECT u.first_name, ugs.xp, ugs.level, ugs.coins
        FROM user_group_stats ugs
        JOIN users u ON ugs.user_id = u.id
        WHERE ugs.group_id = %s
        ORDER BY ugs.xp DESC
        LIMIT %s;
    """, (group_id, limit))
    players = cur.fetchall()
    cur.close()
    conn.close()
    return players

# --- أوامر البوت ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if not chat or not hasattr(chat, "type") or (chat.type != Chat.GROUP and chat.type != Chat.SUPERGROUP):
        if update.message:
            await update.message.reply_text("هذا البوت مخصص للجروبات فقط! ✨")
        return
    ensure_user_and_group(user, chat)
    if update.message and user is not None:
        await update.message.reply_text(
            f"👋 أهلاً بك يا <b>{user.first_name}</b> في جروب <b>{chat.title}</b>!\n\n"
            f"<b>الأوامر المتاحة:</b>\n"
            f"📊 <code>/xp</code> - عرض نقاطك ومستواك\n"
            f"📈 <code>/progress</code> - متابعة تقدمك\n"
            f"🏪 <code>/store</code> - متجر الأدوات والشارات\n"
        )
        f"📋 <code>/daily</code> - المهام اليومية\n"
        f"🏆 <code>/badges</code> - الشارات المكتسبة\n"
        f"⚔️ <code>/clan</code> - إدارة الكلان\n"
        f"🏅 <code>/top</code> - أفضل اللاعبين\n"
        f"🎮 <code>/games</code> - الألعاب والتحديات\n\n"
        f"اكتب أي رسالة لبدء جمع النقاط والمستويات! 🚀",
        parse_mode='HTML'
    )

async def xp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    xp, level, xp_start, xp_next, rank, coins = get_progress(user, chat)
    bar = "█" * int(10 * (xp-xp_start)/(xp_next-xp_start)) + "░" * (10-int(10 * (xp-xp_start)/(xp_next-xp_start)))
    await update.message.reply_text(
        f"<b>معلوماتك:</b>\n"
        f"المستوى: <b>{level}</b> <code>{rank}</code>\n"
        f"XP: <b>{xp}</b> / <b>{xp_next}</b>\n"
        f"العملات: <b>{coins}</b> 💰\n"
        f"التقدم: <code>{bar}</code>",
        parse_mode='HTML'
    )

async def progress_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    xp, level, xp_start, xp_next, rank, coins = get_progress(user, chat)
    percent = int(100 * (xp-xp_start)/(xp_next-xp_start)) if xp_next > xp_start else 0
    if update.message:
        await update.message.reply_text(
            f"📈 <b>تقدمك في المستوى الحالي:</b>\n"
            f"المستوى: <b>{level}</b> <code>{rank}</code>\n"
            f"XP: <b>{xp}</b> / <b>{xp_next}</b>\n"
            f"النسبة: <b>{percent}%</b>\n"
        )
        f"العملات: <b>{coins}</b> 💰",
        parse_mode='HTML'
    )

async def store_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    items = get_store_items()
    if not items:
        await update.message.reply_text("المتجر فارغ حالياً! 🏪")
        return
    keyboard = []
    for item_id, name, desc, icon, price, item_type, effect_type in items:
        keyboard.append([InlineKeyboardButton(
            f"{icon} {name} - {price} 💰",
            callback_data=f"buy_{item_id}"
        )])
    keyboard.append([InlineKeyboardButton("إغلاق", callback_data="close")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🏪 <b>متجر الأدوات والشارات:</b>\nاختر ما تريد شراؤه:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    quests, progress = get_daily_quests(user, chat)
    if not quests:
        await update.message.reply_text("لا توجد مهام يومية حالياً! 📋")
        return
    text = "📋 <b>المهام اليومية:</b>\n\n"
    for quest_id, title, desc, quest_type, target, reward_xp, reward_coins in quests:
        prog = progress.get(quest_id, {'progress': 0, 'completed': False})
        status = "✅" if prog['completed'] else f"{prog['progress']}/{target}"
        text += f"<b>{title}</b>\n{desc}\nالتقدم: {status}\nالمكافأة: {reward_xp} XP + {reward_coins} 💰\n\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def badges_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    badges = get_user_badges(user, chat)
    if not badges:
        await update.message.reply_text("لم تحصل على أي شارات بعد! 🏆\nاكتب رسائل أكثر لتحصل على شارات!")
        return
    text = "🏆 <b>الشارات المكتسبة:</b>\n\n"
    for name, desc, icon, earned_at in badges:
        text += f"{icon} <b>{name}</b>\n{desc}\nحصلت عليها: {earned_at.strftime('%Y-%m-%d')}\n\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def clan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    clan = get_clan_info(user, chat)
    if clan:
        clan_id, name, desc, total_xp, members, role = clan
        await update.message.reply_text(
            f"⚔️ <b>كلانك:</b>\n"
            f"الاسم: <b>{name}</b>\n"
            f"الوصف: {desc}\n"
            f"دورك: <b>{role}</b>\n"
            f"إجمالي XP: <b>{total_xp}</b>\n"
            f"عدد الأعضاء: <b>{members}</b>",
            parse_mode='HTML'
        )
    else:
        keyboard = [[InlineKeyboardButton("إنشاء كلان", callback_data="create_clan")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚔️ <b>الكلانات:</b>\nأنت لست في أي كلان.\nيمكنك إنشاء كلان جديد أو الانضمام لآخر.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    players = get_top_players(chat, 10)
    if not players:
        await update.message.reply_text("لا يوجد لاعبين بعد! 🏅")
        return
    text = "🏅 <b>أفضل 10 لاعبين:</b>\n\n"
    for i, (name, xp, level, coins) in enumerate(players, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} <b>{name}</b>\nXP: {xp} | المستوى: {level} | العملات: {coins} 💰\n\n"
    await update.message.reply_text(text, parse_mode='HTML')

async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎲 تخمين الرقم", callback_data="game_guess")],
        [InlineKeyboardButton("🧠 اختبار سريع", callback_data="game_quiz")],
        [InlineKeyboardButton("⚔️ تحدي مزدوج", callback_data="game_duel")],
        [InlineKeyboardButton("إغلاق", callback_data="close")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎮 <b>الألعاب والتحديات:</b>\nاختر لعبة:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    chat = query.message.chat

    if data == "close":
        if query.message:
            await query.message.delete()
        return

    elif data and data.startswith("buy_"):
        item_id = data.split("_")[1]
        success, message = buy_item(user, chat, item_id)
        await query.message.reply_text(f"{'✅' if success else '❌'} {message}")
    
    elif data == "create_clan":
        await query.message.reply_text(
            "⚔️ لإنشاء كلان، استخدم الأمر:\n<code>/createclan اسم_الكلان الوصف</code>",
            parse_mode='HTML'
        )
    
    elif data == "game_guess":
        number = random.randint(1, 100)
        context.user_data['guess_number'] = number
        context.user_data['guess_attempts'] = 0
        await query.message.reply_text(
            "🎲 <b>لعبة تخمين الرقم:</b>\nفكرت في رقم من 1 إلى 100.\nاستخدم <code>/guess الرقم</code> للتخمين!",
            parse_mode='HTML'
        )
    
    elif data == "game_quiz":
        questions = [
            ("ما هو عاصمة مصر؟", "القاهرة"),
            ("كم عدد كواكب المجموعة الشمسية؟", "8"),
            ("ما هو أكبر محيط في العالم؟", "الهادئ"),
            ("في أي سنة تأسست المملكة العربية السعودية؟", "1932"),
            ("ما هو أطول نهر في العالم؟", "النيل")
        ]
        question, answer = random.choice(questions)
        context.user_data['quiz_answer'] = answer
        await query.message.reply_text(
            f"🧠 <b>اختبار سريع:</b>\n{question}\nاستخدم <code>/answer إجابتك</code> للإجابة!",
            parse_mode='HTML'
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    if chat.type != Chat.GROUP and chat.type != Chat.SUPERGROUP:
        return
    
    xp, coins, new_level = add_xp_and_coins(user, chat, update.message.message_id)
    
    # فحص الشارات الجديدة
    new_badges = check_and_award_badges(user, chat)
    if new_badges:
        badge_text = "🏆 <b>مبروك! حصلت على شارات جديدة:</b>\n"
        for name, icon, reward_xp, reward_coins in new_badges:
            badge_text += f"{icon} <b>{name}</b> (+{reward_xp} XP, +{reward_coins} 💰)\n"
        await update.message.reply_text(badge_text, parse_mode='HTML')
    
    # تهنئة عند الترقية
    if random.random() < 0.1:  # 10% احتمال
        await update.message.reply_text(
            f"🎉 <b>{user.first_name}</b> حصل على <b>{xp}</b> XP و <b>{coins}</b> عملة!",
            parse_mode='HTML'
        )

# --- أوامر إضافية ---
async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'guess_number' not in context.user_data:
        await update.message.reply_text("استخدم /games أولاً لبدء لعبة تخمين الرقم! 🎲")
        return
    
    try:
        guess = int(context.args[0])
        target = context.user_data['guess_number']
        context.user_data['guess_attempts'] += 1
        
        if guess == target:
            attempts = context.user_data['guess_attempts']
            reward = max(50 - attempts * 5, 10)  # مكافأة أقل مع المحاولات
            await update.message.reply_text(
                f"🎉 <b>أحسنت! لقد خمنت الرقم الصحيح!</b>\n"
                f"الرقم كان: <b>{target}</b>\n"
                f"عدد المحاولات: <b>{attempts}</b>\n"
                f"المكافأة: <b>{reward}</b> XP",
                parse_mode='HTML'
            )
            del context.user_data['guess_number']
            del context.user_data['guess_attempts']
        elif guess < target:
            await update.message.reply_text("📈 الرقم أكبر من ذلك! حاول مرة أخرى.")
        else:
            await update.message.reply_text("📉 الرقم أصغر من ذلك! حاول مرة أخرى.")
    except (IndexError, ValueError):
        await update.message.reply_text("استخدم: /guess الرقم")

async def answer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'quiz_answer' not in context.user_data:
        await update.message.reply_text("استخدم /games أولاً لبدء اختبار! 🧠")
        return
    
    if not context.args:
        await update.message.reply_text("استخدم: /answer إجابتك")
        return
    
    user_answer = " ".join(context.args)
    correct_answer = context.user_data['quiz_answer']
    
    if user_answer.lower() == correct_answer.lower():
        await update.message.reply_text(
            f"🎉 <b>أحسنت! إجابة صحيحة!</b>\n"
            f"المكافأة: <b>100</b> XP",
            parse_mode='HTML'
        )
        del context.user_data['quiz_answer']
    else:
        await update.message.reply_text(f"❌ إجابة خاطئة. الإجابة الصحيحة هي: <b>{correct_answer}</b>", parse_mode='HTML')

# --- إعداد التطبيق ---
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # إضافة handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("xp", xp_command))
    app.add_handler(CommandHandler("progress", progress_command))
    app.add_handler(CommandHandler("store", store_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("badges", badges_command))
    app.add_handler(CommandHandler("clan", clan_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("games", games_command))
    app.add_handler(CommandHandler("guess", guess_command))
    app.add_handler(CommandHandler("answer", answer_command))
    
    # إضافة callback handler للزر
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # إضافة message handler
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    
    print("🤖 البوت يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main() 