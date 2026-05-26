import time
import random
import sqlite3
import vk_api
import os
import re
import threading
import string
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

# ========== НАСТРОЙКА БОТА ==========
TOKEN = "vk1.a.6MgeFoEYyOVXYub3mwbY_Lvz99OfYYjP_zI0tKnQTBpDtj5pdAO5ETSMNLJ5cKU-GJ7r5fDH5uydayMQFQZGxKP-YSqIIhMaN_a3BpQIUjPHKc5oBCmCv1ju4_gTPxX30Pjfw3yRIZWxwUWznKq0QpYZCC41PFD_jcZtdq9p_8I8TkksE-9aAGN-DGBvJWdXl-2hZKhgycaEtMocuFo8cg"
GROUP_ID = 237472128
CREATOR_ID = 736337130
DEVELOPER_IDS = [736337130]
# ====================================

active_duels = {}
processed_messages = {}
last_message_time = {}
last_command_time = {}

RANGS = {
    0: "Пользователь",
    1: "Куратор",
    2: "Технический специалист",
    3: "Зам. Гл. Админа",
    4: "Главный Админ",
    5: "Спец. Админ",
    6: "Руководитель",
    7: "Зам. Владельца",
    8: "Владелец"
}

LEAGUES = [
    {"name": "Бронзовая", "emoji": "🥉", "color": "#cd7f32", "level_needed": 50, "reward": 5000},
    {"name": "Железная", "emoji": "⚙️", "color": "#707070", "level_needed": 100, "reward": 10000},
    {"name": "Стальная", "emoji": "🔩", "color": "#8090a0", "level_needed": 200, "reward": 20000},
    {"name": "Золотая", "emoji": "👑", "color": "#ffd700", "level_needed": 250, "reward": 40000},
    {"name": "Алмазная", "emoji": "💎", "color": "#00ffff", "level_needed": 300, "reward": 80000}
]

def get_level_from_xp(xp):
    return xp // 1000

def get_user_league(level):
    for i in range(len(LEAGUES) - 1, -1, -1):
        if level >= LEAGUES[i]["level_needed"]:
            return LEAGUES[i], i
    return None, -1

def get_next_league(level):
    for league in LEAGUES:
        if level < league["level_needed"]:
            return league, league["level_needed"] - level
    return None, 0

def generate_promo_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

def is_developer(user_id):
    return user_id in DEVELOPER_IDS

def init_db():
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            peer_id INTEGER,
            xp INTEGER DEFAULT 0,
            warns INTEGER DEFAULT 0,
            job_cooldown INTEGER DEFAULT 0,
            role INTEGER DEFAULT 0,
            muted_until INTEGER DEFAULT 0,
            duels_won INTEGER DEFAULT 0,
            duels_lost INTEGER DEFAULT 0,
            nickname TEXT DEFAULT '',
            last_daily INTEGER DEFAULT 0,
            daily_streak INTEGER DEFAULT 0,
            league_level INTEGER DEFAULT 0,
            banned_until INTEGER DEFAULT 0,
            messages_count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, peer_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS linked_chats (
            peer_id INTEGER PRIMARY KEY,
            linked_by INTEGER,
            linked_at INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS custom_commands (
            peer_id INTEGER,
            user_id INTEGER,
            original_cmd TEXT,
            alias TEXT,
            created_at INTEGER,
            PRIMARY KEY (peer_id, alias)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quiet_mode (
            peer_id INTEGER PRIMARY KEY,
            enabled INTEGER DEFAULT 0,
            cooldown_seconds INTEGER DEFAULT 5,
            ignore_admins INTEGER DEFAULT 1,
            ignore_rank INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greeted_chats (
            peer_id INTEGER PRIMARY KEY,
            greeted_at INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            xp_reward INTEGER,
            created_by INTEGER,
            created_at INTEGER,
            max_uses INTEGER DEFAULT 1,
            uses INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS used_promos (
            user_id INTEGER,
            code TEXT,
            used_at INTEGER,
            PRIMARY KEY (user_id, code)
        )
    """)
    conn.commit()
    conn.close()

def get_user(user_id, peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp, warns, job_cooldown, role, muted_until, duels_won, duels_lost, nickname, last_daily, daily_streak, league_level, banned_until, messages_count FROM users WHERE user_id = ? AND peer_id = ?", (user_id, peer_id))
    row = cursor.fetchone()
    
    if not row:
        initial_role = 8 if user_id == CREATOR_ID else 0
        cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (?, ?, ?)", (user_id, peer_id, initial_role))
        conn.commit()
        row = (0, 0, 0, initial_role, 0, 0, 0, "", 0, 0, 0, 0, 0)
        
    conn.close()
    return {
        "xp": row[0], "warns": row[1], "job_cooldown": row[2], 
        "role": row[3], "muted_until": row[4], "duels_won": row[5], 
        "duels_lost": row[6], "nickname": row[7], "last_daily": row[8], 
        "daily_streak": row[9], "league_level": row[10], "banned_until": row[11],
        "messages_count": row[12]
    }

def update_user(user_id, peer_id, field, value):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ? AND peer_id = ?", (value, user_id, peer_id))
    conn.commit()
    conn.close()

def add_message_count(user_id, peer_id):
    user = get_user(user_id, peer_id)
    update_user(user_id, peer_id, "messages_count", user["messages_count"] + 1)

def add_xp(user_id, peer_id, amount, msg=None):
    user = get_user(user_id, peer_id)
    old_xp = user["xp"]
    new_xp = old_xp + amount
    update_user(user_id, peer_id, "xp", new_xp)
    
    old_level = get_level_from_xp(old_xp)
    new_level = get_level_from_xp(new_xp)
    
    old_league, _ = get_user_league(old_level)
    new_league, _ = get_user_league(new_level)
    
    if new_league and old_league != new_league:
        reward = new_league["reward"]
        update_user(user_id, peer_id, "xp", new_xp + reward)
        try:
            vk.messages.send(peer_id=peer_id, message=f"🏆 ПОЗДРАВЛЯЮ! {get_display_name(user_id, peer_id)} достиг {new_league['emoji']} {new_league['name']} лиги!\n✨ Награда: +{reward} XP!", random_id=random.getrandbits(64))
        except:
            pass
        return new_xp + reward
    return new_xp

def get_user_id_from_text(text, vk, peer_id):
    match = re.search(r'\[id(\d+)\|.*?\]', text)
    if match:
        return int(match.group(1))
    match = re.search(r'@duplicate(\d+)', text)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d{5,})', text)
    if match:
        return int(match.group(1))
    return None

def is_user_banned_in_group(vk, group_id, user_id):
    try:
        response = vk.groups.getBanned(group_id=group_id)
        for item in response['items']:
            if item['profile']['id'] == user_id:
                return True
    except:
        pass
    return False

def format_time(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}ч {minutes}м"
    elif minutes > 0:
        return f"{minutes}м {secs}с"
    else:
        return f"{secs}с"

def unmute_user_delayed(user_id, peer_id, mute_time):
    time.sleep(mute_time)
    update_user(user_id, peer_id, "muted_until", 0)
    try:
        vk.messages.send(peer_id=peer_id, message=f"🔊 Пользователь размучен!", random_id=random.getrandbits(64))
    except:
        pass

def duel_timeout(caller_id, opponent_id, peer_id):
    time.sleep(60)
    if caller_id in active_duels and active_duels[caller_id][0] == opponent_id:
        del active_duels[caller_id]
        try:
            vk.messages.send(peer_id=peer_id, message=f"⏰ Дуэль отменена (60 сек)", random_id=random.getrandbits(64))
        except:
            pass

def get_display_name(user_id, peer_id):
    user = get_user(user_id, peer_id)
    if user["nickname"]:
        return user["nickname"]
    try:
        user_info = vk.users.get(user_ids=user_id)[0]
        return f"{user_info['first_name']} {user_info['last_name']}"
    except:
        return f"id{user_id}"

def get_mention_link(user_id):
    return f"[id{user_id}|{get_display_name(user_id, 0)}]"

def get_linked_chats():
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT peer_id FROM linked_chats")
    chats = [row[0] for row in cursor.fetchall()]
    conn.close()
    return chats

def add_linked_chat(peer_id, linked_by):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO linked_chats (peer_id, linked_by, linked_at) VALUES (?, ?, ?)", 
                   (peer_id, linked_by, int(time.time())))
    conn.commit()
    conn.close()

def remove_linked_chat(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM linked_chats WHERE peer_id = ?", (peer_id,))
    conn.commit()
    conn.close()

def is_chat_linked(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM linked_chats WHERE peer_id = ?", (peer_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

def get_chat_name(peer_id):
    try:
        chat_id = peer_id - 2000000000
        chat_info = vk.messages.getChat(chat_id=chat_id)
        return chat_info['title']
    except:
        return f"Беседа {peer_id}"

def add_custom_command(peer_id, user_id, original_cmd, alias):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO custom_commands (peer_id, user_id, original_cmd, alias, created_at) 
        VALUES (?, ?, ?, ?, ?)
    """, (peer_id, user_id, original_cmd.lower(), alias.lower(), int(time.time())))
    conn.commit()
    conn.close()

def remove_custom_command(peer_id, alias):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM custom_commands WHERE peer_id = ? AND alias = ?", (peer_id, alias.lower()))
    conn.commit()
    conn.close()

def get_original_command(peer_id, alias):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd FROM custom_commands WHERE peer_id = ? AND alias = ?", (peer_id, alias.lower()))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_user_custom_commands(peer_id, user_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd, alias FROM custom_commands WHERE peer_id = ? AND user_id = ?", (peer_id, user_id))
    cmds = cursor.fetchall()
    conn.close()
    return cmds

def get_all_custom_commands(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd, alias, user_id FROM custom_commands WHERE peer_id = ?", (peer_id,))
    cmds = cursor.fetchall()
    conn.close()
    return cmds

def get_quiet_settings(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT enabled, cooldown_seconds, ignore_admins, ignore_rank FROM quiet_mode WHERE peer_id = ?", (peer_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"enabled": 0, "cooldown": 5, "ignore_admins": 1, "ignore_rank": 1}
    return {"enabled": row[0], "cooldown": row[1], "ignore_admins": row[2], "ignore_rank": row[3]}

def set_quiet_enabled(peer_id, enabled):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO quiet_mode (peer_id, enabled, cooldown_seconds, ignore_admins, ignore_rank) VALUES (?, ?, COALESCE((SELECT cooldown_seconds FROM quiet_mode WHERE peer_id = ?), 5), COALESCE((SELECT ignore_admins FROM quiet_mode WHERE peer_id = ?), 1), COALESCE((SELECT ignore_rank FROM quiet_mode WHERE peer_id = ?), 1))", 
                   (peer_id, 1 if enabled else 0, peer_id, peer_id, peer_id))
    conn.commit()
    conn.close()

def set_quiet_cooldown(peer_id, seconds):
    if seconds < 1: seconds = 1
    if seconds > 60: seconds = 60
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO quiet_mode (peer_id, enabled, cooldown_seconds, ignore_admins, ignore_rank) VALUES (?, COALESCE((SELECT enabled FROM quiet_mode WHERE peer_id = ?), 0), ?, COALESCE((SELECT ignore_admins FROM quiet_mode WHERE peer_id = ?), 1), COALESCE((SELECT ignore_rank FROM quiet_mode WHERE peer_id = ?), 1))", 
                   (peer_id, peer_id, seconds, peer_id, peer_id))
    conn.commit()
    conn.close()

def set_quiet_ignore_admins(peer_id, ignore):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO quiet_mode (peer_id, enabled, cooldown_seconds, ignore_admins, ignore_rank) VALUES (?, COALESCE((SELECT enabled FROM quiet_mode WHERE peer_id = ?), 0), COALESCE((SELECT cooldown_seconds FROM quiet_mode WHERE peer_id = ?), 5), ?, COALESCE((SELECT ignore_rank FROM quiet_mode WHERE peer_id = ?), 1))", 
                   (peer_id, peer_id, peer_id, 1 if ignore else 0, peer_id))
    conn.commit()
    conn.close()

def set_quiet_ignore_rank(peer_id, min_rank):
    if min_rank < 0: min_rank = 0
    if min_rank > 8: min_rank = 8
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO quiet_mode (peer_id, enabled, cooldown_seconds, ignore_admins, ignore_rank) VALUES (?, COALESCE((SELECT enabled FROM quiet_mode WHERE peer_id = ?), 0), COALESCE((SELECT cooldown_seconds FROM quiet_mode WHERE peer_id = ?), 5), COALESCE((SELECT ignore_admins FROM quiet_mode WHERE peer_id = ?), 1), ?)", 
                   (peer_id, peer_id, peer_id, peer_id, min_rank))
    conn.commit()
    conn.close()

def check_and_greet(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM greeted_chats WHERE peer_id = ?", (peer_id,))
    exists = cursor.fetchone()
    conn.close()
    return exists is None

def mark_as_greeted(peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO greeted_chats (peer_id, greeted_at) VALUES (?, ?)", (peer_id, int(time.time())))
    conn.commit()
    conn.close()

def create_promo_code(dev_id, xp_reward, max_uses=1):
    code = generate_promo_code()
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO promo_codes (code, xp_reward, created_by, created_at, max_uses) VALUES (?, ?, ?, ?, ?)", 
                   (code, xp_reward, dev_id, int(time.time()), max_uses))
    conn.commit()
    conn.close()
    return code

def use_promo_code(user_id, code, peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp_reward, max_uses, uses FROM promo_codes WHERE code = ?", (code,))
    promo = cursor.fetchone()
    if not promo:
        conn.close()
        return False, "Промокод не найден!"
    
    xp_reward, max_uses, uses = promo
    if uses >= max_uses:
        conn.close()
        return False, "Промокод уже использован!"
    
    cursor.execute("SELECT 1 FROM used_promos WHERE user_id = ? AND code = ?", (user_id, code))
    if cursor.fetchone():
        conn.close()
        return False, "Вы уже использовали этот промокод!"
    
    cursor.execute("INSERT INTO used_promos (user_id, code, used_at) VALUES (?, ?, ?)", (user_id, code, int(time.time())))
    cursor.execute("UPDATE promo_codes SET uses = uses + 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    
    add_xp(user_id, peer_id, xp_reward)
    return True, xp_reward

def get_all_promos():
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT code, xp_reward, created_at, max_uses, uses FROM promo_codes ORDER BY created_at DESC")
    promos = cursor.fetchall()
    conn.close()
    return promos

init_db()

try:
    vk_session = vk_api.VkApi(token=TOKEN)
    vk = vk_session.get_api()
    group_info = vk.groups.getById()
    if group_info:
        print(f"Бот подключен к: {group_info[0]['name']}")
    else:
        print("Ошибка подключения")
        exit()
except Exception as e:
    print(f"Ошибка: {e}")
    exit()

longpoll = VkBotLongPoll(vk_session, GROUP_ID)
print("Бот успешно запущен!")

while True:
    try:
        for event in longpoll.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                msg = event.obj.message if 'message' in event.obj else event.obj
                
                msg_id = msg.get('id')
                if msg_id and msg_id in processed_messages:
                    continue
                if msg_id:
                    processed_messages[msg_id] = time.time()
                    for old_id in list(processed_messages.keys()):
                        if time.time() - processed_messages[old_id] > 10:
                            del processed_messages[old_id]
                
                text = msg['text'].lower().strip()
                peer_id = msg['peer_id']
                user_id = msg['from_id']
                
                if user_id < 0:
                    continue

                if not text.startswith('/'):
                    add_message_count(user_id, peer_id)
                
                if text.startswith('/'):
                    current_time = int(time.time())
                    last_cmd = last_command_time.get(user_id, 0)
                    if current_time - last_cmd < 2:
                        remaining = 2 - (current_time - last_cmd)
                        vk.messages.send(peer_id=peer_id, message=f"❌ Так быстро! Я не успеваю за тобой, подожди ещё {remaining} сек.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    last_command_time[user_id] = current_time

                if check_and_greet(peer_id):
                    welcome_text = (
                        "✨ ПРИВЕТ! Я БОТ HOLD MANAGER ✨\n\n"
                        "Для работы мне нужны права администратора в этой беседе.\n\n"
                        "Что я умею:\n"
                        "• Кикать и выдавать муты\n"
                        "• Вести учёт варнов\n"
                        "• Проводить дуэли\n"
                        "• Выдавать ежедневные бонусы\n"
                        "• Отслеживать уровни и лиги\n\n"
                        "Как выдать права:\n"
                        "1. Нажми на название беседы\n"
                        "2. Управление беседой\n"
                        "3. Найди меня в списке\n"
                        "4. Выдай права администратора\n\n"
                        "После выдачи прав напиши /ready\n\n"
                        "Команды: /help"
                    )
                    vk.messages.send(peer_id=peer_id, message=welcome_text, random_id=random.getrandbits(64))
                    mark_as_greeted(peer_id)

                cmd_parts = text.split()
                if cmd_parts:
                    alias = cmd_parts[0]
                    if alias.startswith('/'):
                        alias = alias[1:]
                    original = get_original_command(peer_id, alias)
                    if original:
                        text = '/' + original + ' ' + ' '.join(cmd_parts[1:])

                sender = get_user(user_id, peer_id)
                
                current_time = int(time.time())
                if sender["muted_until"] > current_time and sender["role"] < 1:
                    remaining = sender["muted_until"] - current_time
                    vk.messages.send(peer_id=peer_id, message=f"🔇 Вы в муте! Осталось: {format_time(remaining)}", random_id=random.getrandbits(64), reply_to=msg_id)
                    continue

                def get_target_and_reason(text, msg):
                    parts = text.split()
                    target_id = None
                    reason = None
                    
                    if len(parts) > 1:
                        target_id = get_user_id_from_text(' '.join(parts[1:]), vk, peer_id)
                        if target_id:
                            text_after = ' '.join(parts[1:])
                            reason_match = re.search(rf'(?:\[id{target_id}\|.*?\]|@{target_id}|{target_id})\s*(.*)', text_after)
                            if reason_match and reason_match.group(1).strip():
                                reason = reason_match.group(1).strip()
                    
                    if not target_id and 'reply_message' in msg:
                        target_id = msg['reply_message']['from_id']
                        if len(parts) > 1:
                            reason = ' '.join(parts[1:])
                    
                    return target_id, reason

                # ========== ИГРОВЫЕ КОМАНДЫ ==========
                if text in ["/stats", "stats"]:
                    user = get_user(user_id, peer_id)
                    user_level = get_level_from_xp(user["xp"])
                    league, _ = get_user_league(user_level)
                    league_name = f"{league['emoji']} {league['name']}" if league else "🆕 Новичок"
                    is_muted = user["muted_until"] > int(time.time())
                    mute_left = user["muted_until"] - int(time.time()) if is_muted else 0
                    
                    stats_text = (
                        f"🌟 **СТАТИСТИКА** 🌟\n\n"
                        f"┌─────────────────────┐\n"
                        f"│ 👤 **Имя:** {get_display_name(user_id, peer_id)}\n"
                        f"│ 👑 **Ранг:** {RANGS.get(user['role'], 'Пользователь')}\n"
                        f"│ 📊 **Уровень:** {user_level}\n"
                        f"│ ✨ **Опыт:** {user['xp']:,} XP\n"
                        f"│ 🏆 **Лига:** {league_name}\n"
                        f"│ ⚔️ **Дуэли:** {user['duels_won']} / {user['duels_lost']}\n"
                        f"│ ⚠️ **Варны:** {user['warns']}/3\n"
                        f"│ 🔇 **Мут:** {'ДА (' + format_time(mute_left) + ')' if is_muted else 'НЕТ'}\n"
                        f"│ 💬 **Сообщений:** {user['messages_count']:,}\n"
                        f"└─────────────────────┘\n\n"
                        f"💡 **Совет:** Играй и повышай уровень!"
                    )
                    vk.messages.send(peer_id=peer_id, message=stats_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text in ["/bonus", "/подарок"]:
                    user = get_user(user_id, peer_id)
                    current_time = int(time.time())
                    
                    if current_time - user["last_daily"] < 86400:
                        hours_left = 24 - (current_time - user["last_daily"]) // 3600
                        vk.messages.send(peer_id=peer_id, message=f"⏳ Бонус уже получен! Возвращайся через {hours_left} часов.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    streak = user["daily_streak"]
                    if streak >= 6:
                        xp_reward = 500
                        new_streak = 0
                    else:
                        xp_reward = 100 + (streak * 50)
                        new_streak = streak + 1
                    
                    next_reward = 100 + (new_streak * 50) if new_streak < 6 else 500
                    
                    new_xp = add_xp(user_id, peer_id, xp_reward, msg)
                    update_user(user_id, peer_id, "last_daily", current_time)
                    update_user(user_id, peer_id, "daily_streak", new_streak)
                    current_level = get_level_from_xp(new_xp)
                    
                    streak_text = f"🔥 Серия: {new_streak} дней подряд!" if new_streak > 0 else "🔥 Серия: 0 дней подряд!"
                    
                    bonus_text = (
                        f"🎁 **ЕЖЕДНЕВНЫЙ БОНУС**\n\n"
                        f"{get_display_name(user_id, peer_id)} || {RANGS.get(user['role'], 'Пользователь')} получил ежедневный бонус опыта – {xp_reward}.\n"
                        f"{streak_text}\n"
                        f"Следующий бонус - {next_reward} из 1000 XP.\n"
                        f"📊 Твой уровень: {current_level}"
                    )
                    vk.messages.send(peer_id=peer_id, message=bonus_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== ДУЭЛИ ==========
                elif text.startswith("/duel"):
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /duel 100 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Ставка должна быть числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    opponent_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    if not opponent_id:
                        opponent_id = get_target_and_reason(text, msg)[0]
                    
                    if not opponent_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи соперника: /duel 100 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if opponent_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя вызвать себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if sender["xp"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Не хватает опыта! У тебя {sender['xp']} XP", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    opponent = get_user(opponent_id, peer_id)
                    if opponent["xp"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У соперника не хватает опыта! У него {opponent['xp']} XP", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    active_duels[user_id] = (opponent_id, amount, time.time())
                    thread = threading.Thread(target=duel_timeout, args=(user_id, opponent_id, peer_id))
                    thread.daemon = True
                    thread.start()
                    
                    win_amount = int(amount * 1.15)
                    lose_amount = int(amount * 0.85)
                    
                    duel_text = (
                        f"🗡 {get_mention_link(opponent_id)}, вас вызвал на дуэль {get_mention_link(user_id)}!\n\n"
                        f"💲 **Ставка:** {amount} XP\n"
                        f"⭕ **Примечание:** при победе вы получаете 115% от ставки ({win_amount} XP)\n"
                        f"При поражении у вас отнимается 85% от ставки ({lose_amount} XP)\n\n"
                        f"❕ Используйте `/accept` для принятия дуэли."
                    )
                    vk.messages.send(peer_id=peer_id, message=duel_text, random_id=random.getrandbits(64), reply_to=msg_id, forward_messages=msg_id)

                elif text == "/accept":
                    caller_id = None
                    for cid, (opp_id, amount, timestamp) in active_duels.items():
                        if opp_id == user_id:
                            caller_id = cid
                            break
                    
                    if not caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет активных вызовов!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if 'reply_message' not in msg or msg['reply_message']['from_id'] != caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Ответь на сообщение с вызовом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    opponent_id, amount, timestamp = active_duels[caller_id]
                    del active_duels[caller_id]
                    
                    if time.time() - timestamp > 60:
                        vk.messages.send(peer_id=peer_id, message="⏰ Время истекло!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    winner_id = random.choice([caller_id, opponent_id])
                    loser_id = opponent_id if winner_id == caller_id else caller_id
                    
                    win_amount = int(amount * 1.15)
                    lose_amount = int(amount * 0.85)
                    
                    if winner_id == user_id:
                        add_xp(winner_id, peer_id, win_amount, msg)
                        add_xp(loser_id, peer_id, -lose_amount, msg)
                        result_text = f"⚔️ **ПОБЕДИТЕЛЬ:** {get_mention_link(winner_id)}!\n💰 Выигрыш: {win_amount} XP! (+15%)"
                    else:
                        add_xp(winner_id, peer_id, amount, msg)
                        add_xp(loser_id, peer_id, -lose_amount, msg)
                        result_text = f"⚔️ **ПОБЕДИТЕЛЬ:** {get_mention_link(winner_id)}!\n💰 Выигрыш: {amount} XP!"
                    
                    update_user(winner_id, peer_id, "duels_won", get_user(winner_id, peer_id)["duels_won"] + 1)
                    update_user(loser_id, peer_id, "duels_lost", get_user(loser_id, peer_id)["duels_lost"] + 1)
                    
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== ТОПЫ ==========
                elif text == "/top":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, xp FROM users WHERE peer_id = ? AND xp > 0 ORDER BY xp DESC LIMIT 10", (peer_id,))
                    top_users = cursor.fetchall()
                    conn.close()
                    
                    result_text = "🏆 **ТАБЛИЦА ЛИДЕРОВ (ОПЫТ)** 🏆\n\n"
                    if top_users:
                        for i, (uid, xp) in enumerate(top_users, 1):
                            level = get_level_from_xp(xp)
                            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"
                            result_text += f"{medal} {get_display_name(uid, peer_id)} — {level} уровень\n"
                    else:
                        result_text += "Нет данных\n"
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/topmessages":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT user_id, messages_count FROM users 
                        WHERE peer_id = ? AND messages_count > 0 
                        ORDER BY messages_count DESC LIMIT 10
                    """, (peer_id,))
                    top_messages = cursor.fetchall()
                    conn.close()
                    
                    result_text = "💬 **ТОП ПО СООБЩЕНИЯМ** 💬\n\n"
                    if top_messages:
                        for i, (uid, count) in enumerate(top_messages, 1):
                            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"
                            result_text += f"{medal} {get_display_name(uid, peer_id)} — {count:,} сообщений\n"
                    else:
                        result_text += "Нет данных\n"
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/leagues":
                    user = get_user(user_id, peer_id)
                    user_level = get_level_from_xp(user["xp"])
                    next_league, levels_needed = get_next_league(user_level)
                    
                    result_text = "🏅 **СИСТЕМА ЛИГ** 🏅\n\n"
                    for league in LEAGUES:
                        if user_level >= league["level_needed"]:
                            result_text += f"✅ {league['emoji']} {league['name']} — достигнуто\n"
                        else:
                            result_text += f"❌ {league['emoji']} {league['name']} — нужно {league['level_needed']} уровень\n"
                    
                    if levels_needed > 0:
                        result_text += f"\n📊 Твой уровень: {user_level}\n⚡ До {next_league['emoji']} {next_league['name']} лиги: {levels_needed} уровней"
                    else:
                        result_text += f"\n🏆 Ты достиг высшей лиги! Твой уровень: {user_level}"
                    
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== АДМИН КОМАНДЫ ==========
                elif text == "/ready":
                    vk.messages.send(peer_id=peer_id, message="⭐ СПАСИБО ЗА ПРАВА АДМИНИСТРАТОРА! ⭐\n\nБот готов к работе!\nНапиши /help для списка команд.", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/staff":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, role FROM users WHERE peer_id = ? AND role > 0 ORDER BY role DESC", (peer_id,))
                    staff_list = cursor.fetchall()
                    conn.close()
                    
                    if not staff_list:
                        vk.messages.send(peer_id=peer_id, message="Администрация отсутствует.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    staff_text = "👑 **АДМИНИСТРАЦИЯ ЧАТА** 👑\n\n"
                    for uid, role in staff_list:
                        staff_text += f"• {RANGS.get(role, f'Ранг {role}')}: {get_display_name(uid, peer_id)}\n"
                    vk.messages.send(peer_id=peer_id, message=staff_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/nick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /nick МойНик", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    new_nick = parts[1].strip()
                    if len(new_nick) > 20:
                        vk.messages.send(peer_id=peer_id, message="❌ Ник не длиннее 20 символов!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(user_id, peer_id, "nickname", new_nick)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ваш ник: {new_nick}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/delnick":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(user_id, peer_id, "nickname", "")
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ник удалён!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/get"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /get @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_info = get_user(target_id, peer_id)
                    target_level = get_level_from_xp(target_info["xp"])
                    is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                    is_muted = target_info["muted_until"] > int(time.time())
                    mute_left = target_info["muted_until"] - int(time.time()) if is_muted else 0
                    
                    info_text = (
                        f"👤 **{get_display_name(target_id, peer_id)}**\n\n"
                        f"🆔 ID: {target_id}\n"
                        f"👑 Ранг: {RANGS.get(target_info['role'], 'Пользователь')}\n"
                        f"📊 Уровень: {target_level}\n"
                        f"✨ Опыт: {target_info['xp']:,}\n"
                        f"⚠️ Варны: {target_info['warns']}/3\n"
                        f"🔇 Мут: {'ДА (' + format_time(mute_left) + ')' if is_muted else 'НЕТ'}\n"
                        f"⚔️ Дуэли: {target_info['duels_won']} / {target_info['duels_lost']}\n"
                        f"💬 Сообщений: {target_info['messages_count']:,}\n"
                        f"🚫 Бан: {'ДА' if is_banned else 'НЕТ'}"
                    )
                    vk.messages.send(peer_id=peer_id, message=info_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/clear"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи количество сообщений: /clear 10\n\nМаксимум: 100 сообщений", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        count = int(parts[1])
                        if count <= 0:
                            raise ValueError
                        if count > 100:
                            count = 100
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи число от 1 до 100", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        history = vk.messages.getHistory(peer_id=peer_id, count=count, fields='from_id')
                        messages = history.get('items', [])
                        
                        msg_ids = []
                        for m in messages:
                            if m.get('from_id') > 0 and m.get('id'):
                                msg_ids.append(m['id'])
                        
                        if not msg_ids:
                            vk.messages.send(peer_id=peer_id, message="❌ Нет сообщений для удаления!", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                        
                        vk.messages.delete(message_ids=','.join(map(str, msg_ids)), delete_for_all=1)
                        
                        admin_name = get_display_name(user_id, peer_id)
                        vk.messages.send(peer_id=peer_id, message=f"🗑️ **ОЧИСТКА** | {admin_name} удалил {len(msg_ids)} сообщений!", random_id=random.getrandbits(64), reply_to=msg_id)
                        
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}\n\nУбедись, что бот имеет права администратора в беседе!", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== MUTE / UNMUTE ==========
                elif text.startswith("/mute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /mute 10м @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    time_str = parts[1]
                    time_seconds = 0
                    time_match = re.match(r'(\d+)([мчсд])', time_str)
                    if time_match:
                        num = int(time_match.group(1))
                        unit = time_match.group(2)
                        if unit == 'с': time_seconds = num
                        elif unit == 'м': time_seconds = num * 60
                        elif unit == 'ч': time_seconds = num * 3600
                        elif unit == 'д': time_seconds = num * 86400
                    else:
                        vk.messages.send(peer_id=peer_id, message="❌ Формат: 10с, 5м, 2ч, 1д", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if time_seconds <= 0 or time_seconds > 86400 * 7:
                        vk.messages.send(peer_id=peer_id, message="❌ Мут от 1 сек до 7 дней", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", int(time.time()) + time_seconds)
                    thread = threading.Thread(target=unmute_user_delayed, args=(target_id, peer_id, time_seconds))
                    thread.daemon = True
                    thread.start()
                    
                    admin_name = get_display_name(user_id, peer_id)
                    target_name = get_display_name(target_id, peer_id)
                    reason_text = f" Причина: {reason}" if reason else ""
                    vk.messages.send(peer_id=peer_id, message=f"🔇 **МУТ** | {admin_name} выдал мут {target_name} на {format_time(time_seconds)}{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/unmute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", 0)
                    admin_name = get_display_name(user_id, peer_id)
                    target_name = get_display_name(target_id, peer_id)
                    vk.messages.send(peer_id=peer_id, message=f"🔊 **РАЗМУТ** | {admin_name} снял мут с {target_name}", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== WARN / UNWARN / WARNLIST ==========
                elif text.startswith("/warn"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /warn @пользователь причина", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себе!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    new_warns = target_user["warns"] + 1
                    admin_name = get_display_name(user_id, peer_id)
                    target_name = get_display_name(target_id, peer_id)
                    reason_text = f" Причина: {reason}" if reason else ""
                    
                    if new_warns >= 3:
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            update_user(target_id, peer_id, "warns", 0)
                            vk.messages.send(peer_id=peer_id, message=f"⚠️ **ВАРН** | {admin_name} выдал 3/3 варнов {target_name}{reason_text}\n\n🔴 Пользователь кикнут!", random_id=random.getrandbits(64), reply_to=msg_id)
                        except:
                            vk.messages.send(peer_id=peer_id, message=f"⚠️ **ВАРН** | {admin_name} выдал 3/3 варнов {target_name}{reason_text}\n\n⚠️ Не удалось кикнуть (дайте права админа)", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        update_user(target_id, peer_id, "warns", new_warns)
                        vk.messages.send(peer_id=peer_id, message=f"⚠️ **ВАРН** | {admin_name} выдал варн {target_name}{reason_text}\n\n📊 Теперь варнов: {new_warns}/3", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/unwarn"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /unwarn @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя снять варн себе!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя снять варн у старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_user["warns"] == 0:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У {get_display_name(target_id, peer_id)} нет варнов!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    new_warns = target_user["warns"] - 1
                    update_user(target_id, peer_id, "warns", new_warns)
                    reason_text = f" Причина: {reason}" if reason else ""
                    vk.messages.send(peer_id=peer_id, message=f"✅ **СНЯТ ВАРН** | У {get_display_name(target_id, peer_id)} теперь {new_warns}/3{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/warnlist":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, warns FROM users WHERE peer_id = ? AND warns > 0 ORDER BY warns DESC", (peer_id,))
                    warn_list = cursor.fetchall()
                    conn.close()
                    
                    if not warn_list:
                        vk.messages.send(peer_id=peer_id, message="📋 Нет пользователей с варнами.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    warn_text = "⚠️ **СПИСОК НАРУШИТЕЛЕЙ** ⚠️\n\n"
                    for uid, warns in warn_list:
                        warn_text += f"• {get_display_name(uid, peer_id)} — {warns}/3 варнов\n"
                    vk.messages.send(peer_id=peer_id, message=warn_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== KICK / BAN / UNBAN / BANLIST ==========
                elif text.startswith("/kick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /kick @пользователь причина", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    try:
                        chat_id = peer_id - 2000000000
                        vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        admin_name = get_display_name(user_id, peer_id)
                        target_name = get_display_name(target_id, peer_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"💥 **КИК** | {admin_name} кикнул {target_name}{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: дайте боту права админа", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/ban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /ban @пользователь причина", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    try:
                        vk.groups.banUser(group_id=GROUP_ID, user_id=target_id)
                        admin_name = get_display_name(user_id, peer_id)
                        target_name = get_display_name(target_id, peer_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"⛔ **БАН** | {admin_name} забанил {target_name}{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        except:
                            pass
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/unban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /unban @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                        if not is_banned:
                            vk.messages.send(peer_id=peer_id, message=f"❌ Пользователь не в ЧС!", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                        
                        vk.groups.unbanUser(group_id=GROUP_ID, user_id=target_id)
                        admin_name = get_display_name(user_id, peer_id)
                        target_name = get_display_name(target_id, peer_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"🔓 **РАЗБАН** | {admin_name} разбанил {target_name}{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/banlist":
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        response = vk.groups.getBanned(group_id=GROUP_ID, count=200)
                        banned_users = response.get('items', [])
                        if not banned_users:
                            vk.messages.send(peer_id=peer_id, message="📋 Чёрный список группы пуст.", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                        
                        ban_text = "⛔ **ЧЁРНЫЙ СПИСОК** ⛔\n\n"
                        for item in banned_users[:30]:
                            user = item.get('profile', {})
                            first_name = user.get('first_name', '')
                            last_name = user.get('last_name', '')
                            ban_text += f"• {first_name} {last_name}\n"
                        
                        if len(banned_users) > 30:
                            ban_text += f"\n...и ещё {len(banned_users) - 30} пользователей."
                        vk.messages.send(peer_id=peer_id, message=ban_text, random_id=random.getrandbits(64), reply_to=msg_id)
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== SETRANK / UNRANK ==========
                elif text.startswith("/setrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /setrank 4 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        new_role = int(parts[1])
                        if new_role < 0 or new_role > 8:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Ранг от 0 до 8", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if new_role >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать ранг выше своего!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    update_user(target_id, peer_id, "role", new_role)
                    vk.messages.send(peer_id=peer_id, message=f"👑 **ВЫДАН РАНГ** | {get_display_name(target_id, peer_id)} получил ранг {RANGS.get(new_role, '?')}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/unrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя снять ранг у старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    update_user(target_id, peer_id, "role", 0)
                    vk.messages.send(peer_id=peer_id, message=f"❌ **СНЯТ РАНГ** | У {get_display_name(target_id, peer_id)} снят ранг!", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== РЕЖИМ ТИШИНЫ ==========
                elif text.startswith("/quiet"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        current = get_quiet_settings(peer_id)
                        status = "ВКЛЮЧЁН" if current["enabled"] else "ВЫКЛЮЧЕН"
                        vk.messages.send(peer_id=peer_id, message=f"📊 **РЕЖИМ ТИШИНЫ**\n\nСтатус: {status}\nЗадержка: {current['cooldown']} сек\n\n/quiet on/off — включить/выключить\n/quiet time <сек> — задержка", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if parts[1] == "on":
                        set_quiet_enabled(peer_id, True)
                        vk.messages.send(peer_id=peer_id, message=f"🔇 Режим тишины ВКЛЮЧЁН!", random_id=random.getrandbits(64), reply_to=msg_id)
                    elif parts[1] == "off":
                        set_quiet_enabled(peer_id, False)
                        vk.messages.send(peer_id=peer_id, message=f"🔊 Режим тишины ВЫКЛЮЧЕН!", random_id=random.getrandbits(64), reply_to=msg_id)
                    elif parts[1] == "time" and len(parts) >= 3:
                        try:
                            seconds = int(parts[2])
                            if seconds < 1 or seconds > 60:
                                raise ValueError
                            set_quiet_cooldown(peer_id, seconds)
                            vk.messages.send(peer_id=peer_id, message=f"⏱ Задержка: {seconds} сек", random_id=random.getrandbits(64), reply_to=msg_id)
                        except:
                            vk.messages.send(peer_id=peer_id, message="❌ Укажи число от 1 до 60", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== ГЛОБАЛЬНЫЕ КОМАНДЫ ==========
                elif text == "/linkchat":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может связывать беседы!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if is_chat_linked(peer_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Беседа уже связана!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    add_linked_chat(peer_id, user_id)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Беседа добавлена в глобальную сеть!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/unlinkchat":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может отвязывать беседы!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if not is_chat_linked(peer_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Беседа не связана!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remove_linked_chat(peer_id)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Беседа удалена из сети!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/chats":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может смотреть список!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="📋 Нет связанных бесед.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chat_list = "🌐 **СВЯЗАННЫЕ БЕСЕДЫ**\n\n"
                    for chat_peer in chats:
                        chat_list += f"• {get_chat_name(chat_peer)}\n"
                    vk.messages.send(peer_id=peer_id, message=chat_list, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/gban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /gban @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    banned_count = 0
                    for chat_peer in chats:
                        try:
                            vk.groups.banUser(group_id=GROUP_ID, user_id=target_id)
                            banned_count += 1
                            try:
                                chat_id = chat_peer - 2000000000
                                vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            except:
                                pass
                        except:
                            pass
                    
                    admin_name = get_display_name(user_id, peer_id)
                    target_name = get_display_name(target_id, peer_id)
                    vk.messages.send(peer_id=peer_id, message=f"⛔ **ГЛОБАЛЬНЫЙ БАН** | {admin_name} забанил {target_name} в {banned_count} беседах{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/gkick"):
                    if sender["role"] < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Зам. ГА+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /gkick @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    kicked_count = 0
                    for chat_peer in chats:
                        try:
                            chat_id = chat_peer - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            kicked_count += 1
                        except:
                            pass
                    
                    admin_name = get_display_name(user_id, peer_id)
                    target_name = get_display_name(target_id, peer_id)
                    vk.messages.send(peer_id=peer_id, message=f"💥 **ГЛОБАЛЬНЫЙ КИК** | {admin_name} кикнул {target_name} из {kicked_count} бесед{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== КАСТОМНЫЕ КОМАНДЫ ==========
                elif text.startswith("/cmd"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split(maxsplit=2)
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ /cmd [команда] [новое название]\n\nПример: /cmd kick выгнать", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    original_cmd = parts[1].lower()
                    alias = parts[2].lower()
                    
                    if original_cmd.startswith('/'): original_cmd = original_cmd[1:]
                    if alias.startswith('/'): alias = alias[1:]
                    
                    builtin_commands = ["help", "stats", "bonus", "duel", "accept", "top", "topmessages", "leagues",
                                       "mute", "unmute", "warn", "unwarn", "warnlist", "kick", "ban", "unban", "banlist",
                                       "setrank", "unrank", "staff", "nick", "delnick", "get", "clear", "ready",
                                       "quiet", "linkchat", "unlinkchat", "chats", "gban", "gkick", "cmd", "delcmd", "cmdlist"]
                    
                    if alias in builtin_commands:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Нельзя переопределить команду /{alias}!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    existing = get_original_command(peer_id, alias)
                    if existing:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Псевдоним /{alias} уже существует!\nУдалить: /delcmd {alias}", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    add_custom_command(peer_id, user_id, original_cmd, alias)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Создан псевдоним!\n\n/{alias} → /{original_cmd}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/delcmd"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи псевдоним: /delcmd выгнать", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    alias = parts[1].lower()
                    if alias.startswith('/'): alias = alias[1:]
                    
                    existing = get_original_command(peer_id, alias)
                    if not existing:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Псевдоним /{alias} не найден!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remove_custom_command(peer_id, alias)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Псевдоним /{alias} удалён!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/cmdlist":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    my_cmds = get_user_custom_commands(peer_id, user_id)
                    all_cmds = get_all_custom_commands(peer_id)
                    
                    if not my_cmds and not all_cmds:
                        vk.messages.send(peer_id=peer_id, message="📋 Нет созданных псевдонимов.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    result_text = "📋 **ПСЕВДОНИМЫ КОМАНД**\n\n"
                    if my_cmds:
                        result_text += "Твои псевдонимы:\n"
                        for orig, alias in my_cmds:
                            result_text += f"• /{alias} → /{orig}\n"
                    
                    if len(all_cmds) > len(my_cmds):
                        result_text += "\nДругие псевдонимы:\n"
                        for orig, alias, uid in all_cmds:
                            if uid != user_id:
                                result_text += f"• /{alias} → /{orig} (от {get_display_name(uid, peer_id)})\n"
                    
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== КОМАНДЫ РАЗРАБОТЧИКА ==========
                elif text.startswith("/add_xp"):
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /add_xp 1000 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Количество XP должно быть положительным числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    target_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    if not target_id:
                        target_id, _ = get_target_and_reason(text, msg)
                    
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /add_xp 1000 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    add_xp(target_id, peer_id, amount, msg)
                    vk.messages.send(peer_id=peer_id, message=f"✨ **ВЫДАЧА XP**\n\nРазработчик выдал {get_display_name(target_id, peer_id)} +{amount} XP!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/remove_xp"):
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /remove_xp 500 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Количество XP должно быть положительным числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    target_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    if not target_id:
                        target_id, _ = get_target_and_reason(text, msg)
                    
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /remove_xp 500 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["xp"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У {get_display_name(target_id, peer_id)} только {target_user['xp']} XP!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "xp", target_user["xp"] - amount)
                    vk.messages.send(peer_id=peer_id, message=f"📉 **ЗАБОР XP**\n\nРазработчик забрал у {get_display_name(target_id, peer_id)} -{amount} XP!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/set_level"):
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /set_level 50 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        new_level = int(parts[1])
                        if new_level < 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Уровень должен быть положительным числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    target_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    if not target_id:
                        target_id, _ = get_target_and_reason(text, msg)
                    
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /set_level 50 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    new_xp = new_level * 1000
                    update_user(target_id, peer_id, "xp", new_xp)
                    vk.messages.send(peer_id=peer_id, message=f"📊 **УСТАНОВКА УРОВНЯ**\n\nРазработчик установил {get_display_name(target_id, peer_id)} {new_level} уровень!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/reset_user"):
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /reset_user @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remaining_text = ' '.join(parts[1:])
                    target_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    if not target_id:
                        target_id, _ = get_target_and_reason(text, msg)
                    
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /reset_user @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if is_developer(target_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя сбросить данные разработчика!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "xp", 0)
                    update_user(target_id, peer_id, "warns", 0)
                    update_user(target_id, peer_id, "duels_won", 0)
                    update_user(target_id, peer_id, "duels_lost", 0)
                    update_user(target_id, peer_id, "nickname", "")
                    update_user(target_id, peer_id, "muted_until", 0)
                    update_user(target_id, peer_id, "messages_count", 0)
                    
                    vk.messages.send(peer_id=peer_id, message=f"🔄 **СБРОС**\n\nРазработчик сбросил все данные пользователя {get_display_name(target_id, peer_id)}!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/reset_chat":
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("UPDATE users SET xp = 0, warns = 0, duels_won = 0, duels_lost = 0, nickname = '', muted_until = 0, messages_count = 0 WHERE peer_id = ?", (peer_id,))
                    conn.commit()
                    conn.close()
                    
                    vk.messages.send(peer_id=peer_id, message=f"🔄 **СБРОС ЧАТА**\n\nРазработчик сбросил все данные в этой беседе!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/dev_help":
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    dev_help_text = (
                        "🔧 **КОМАНДЫ РАЗРАБОТЧИКА** 🔧\n\n"
                        "/add_xp <число> @пользователь — выдать XP\n"
                        "/remove_xp <число> @пользователь — забрать XP\n"
                        "/set_level <уровень> @пользователь — установить уровень\n"
                        "/reset_user @пользователь — сбросить данные пользователя\n"
                        "/reset_chat — сбросить всех в этой беседе\n"
                        "/create_promo <XP> [макс] — создать промокод\n"
                        "/use_promo <код> — активировать промокод\n"
                        "/promos — список промокодов\n"
                        "/topmessagesglobal — глобальный топ сообщений\n"
                        "/dev_help — показать эту справку"
                    )
                    vk.messages.send(peer_id=peer_id, message=dev_help_text, random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/create_promo"):
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /create_promo 1000 5", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        xp_reward = int(parts[1])
                        if xp_reward <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Количество XP должно быть положительным числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    max_uses = 1
                    if len(parts) >= 3:
                        try:
                            max_uses = int(parts[2])
                            if max_uses <= 0:
                                raise ValueError
                        except:
                            vk.messages.send(peer_id=peer_id, message="❌ Максимальное использование должно быть положительным числом!", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                    
                    code = create_promo_code(user_id, xp_reward, max_uses)
                    vk.messages.send(peer_id=peer_id, message=f"✅ **ПРОМОКОД СОЗДАН!**\n\n🎫 Код: `{code}`\n✨ Награда: {xp_reward} XP\n📊 Макс. использований: {max_uses}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/use_promo"):
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи промокод: /use_promo ABC123XYZ", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    code = parts[1].upper()
                    success, result = use_promo_code(user_id, code, peer_id)
                    
                    if success:
                        vk.messages.send(peer_id=peer_id, message=f"🎉 **ПРОМОКОД АКТИВИРОВАН!**\n\n✨ +{result} XP!\n💫 Приятной игры!", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {result}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text == "/promos":
                    if not is_developer(user_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно только разработчику бота!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    promos = get_all_promos()
                    if not promos:
                        vk.messages.send(peer_id=peer_id, message="📋 Нет созданных промокодов.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    promo_text = "📋 **СПИСОК ПРОМОКОДОВ**\n\n"
                    for code, xp_reward, created_at, max_uses, uses in promos:
                        promo_text += f"🎫 `{code}`\n✨ {xp_reward} XP\n📊 {uses}/{max_uses}\n\n"
                    
                    vk.messages.send(peer_id=peer_id, message=promo_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # ========== СПРАВКА ==========
                elif text in ["/help", "help"]:
                    help_text = (
                        "⚙️ **ИГРОВЫЕ КОМАНДЫ**\n"
                        "/stats — статистика\n"
                        "/duel 100 @пользователь — дуэль\n"
                        "/accept — принять дуэль\n"
                        "/bonus — ежедневный бонус\n"
                        "/top — топ игроков (опыт)\n"
                        "/topmessages — топ по сообщениям\n"
                        "/leagues — список лиг\n\n"
                        "🔨 **АДМИН КОМАНДЫ**\n"
                        "/clear <число> — удалить сообщения\n"
                        "/get @пользователь — информация\n"
                        "/mute 10м @пользователь — мут\n"
                        "/unmute @пользователь — снять мут\n"
                        "/warn @пользователь — предупреждение\n"
                        "/unwarn @пользователь — снять варн\n"
                        "/warnlist — список нарушителей\n"
                        "/kick @пользователь — кикнуть\n"
                        "/ban @пользователь — бан\n"
                        "/unban @пользователь — разбан\n"
                        "/banlist — список забаненных\n"
                        "/setrank 4 @пользователь — выдать ранг\n"
                        "/unrank @пользователь — снять ранг\n"
                        "/staff — администрация\n\n"
                        "🔇 **РЕЖИМ ТИШИНЫ**\n"
                        "/quiet on/off — включить/выключить\n"
                        "/quiet time <сек> — задержка\n\n"
                        "🌐 **ГЛОБАЛЬНЫЕ КОМАНДЫ**\n"
                        "/linkchat — связать беседы\n"
                        "/gban @пользователь — бан везде\n"
                        "/gkick @пользователь — кик везде\n\n"
                        "🔧 **НАСТРОЙКА**\n"
                        "/cmd [команда] [новое] — создать псевдоним\n"
                        "/delcmd [псевдоним] — удалить\n"
                        "/cmdlist — список псевдонимов\n"
                        "/nick [ник] — установить ник\n"
                        "/delnick — удалить ник\n"
                        "/ready — подтвердить права админа"
                    )
                    
                    if is_developer(user_id):
                        help_text += (
                            "\n\n🔧 **КОМАНДЫ РАЗРАБОТЧИКА** 🔧\n"
                            "/add_xp <число> @пользователь — выдать XP\n"
                            "/remove_xp <число> @пользователь — забрать XP\n"
                            "/set_level <уровень> @пользователь — установить уровень\n"
                            "/reset_user @пользователь — сбросить данные\n"
                            "/reset_chat — сбросить всех в чате\n"
                            "/create_promo <XP> [макс] — создать промокод\n"
                            "/use_promo <код> — активировать промокод\n"
                            "/promos — список промокодов\n"
                            "/topmessagesglobal — глобальный топ сообщений\n"
                            "/dev_help — справка разработчика"
                        )
                    
                    vk.messages.send(peer_id=peer_id, message=help_text, random_id=random.getrandbits(64), reply_to=msg_id)

    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(5)
