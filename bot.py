import time
import random
import sqlite3
import vk_api
import os
import re
import threading
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

# ========== НАСТРОЙКА БОТА ==========
TOKEN = "vk1.a.6MgeFoEYyOVXYub3mwbY_Lvz99OfYYjP_zI0tKnQTBpDtj5pdAO5ETSMNLJ5cKU-GJ7r5fDH5uydayMQFQZGxKP-YSqIIhMaN_a3BpQIUjPHKc5oBCmCv1ju4_gTPxX30Pjfw3yRIZWxwUWznKq0QpYZCC41PFD_jcZtdq9p_8I8TkksE-9aAGN-DGBvJWdXl-2hZKhgycaEtMocuFo8cg"
GROUP_ID = 237472128  # ID группы
CREATOR_ID = 736337130  # Твой ID
# ====================================

active_duels = {}
processed_messages = {}
last_message_time = {}

RANGS = {
    0: "Обычный Пользователь",
    1: "Куратор",
    2: "Технический Специалист",
    3: "Заместитель Главного Администратора",
    4: "Главный Администратор",
    5: "Специальный Администратор",
    6: "Руководитель",
    7: "Зам. Владельца",
    8: "Владелец"
}

# ========== ЛИГИ (по уровням) ==========
LEAGUES = [
    {"name": "Бронзовая", "emoji": "🥉", "level_needed": 50, "reward": 5000},
    {"name": "Железная", "emoji": "⚙️", "level_needed": 100, "reward": 10000},
    {"name": "Стальная", "emoji": "🔩", "level_needed": 200, "reward": 20000},
    {"name": "Золотая", "emoji": "👑", "level_needed": 250, "reward": 40000},
    {"name": "Алмазная", "emoji": "💎", "level_needed": 300, "reward": 80000}
]

def get_level_from_xp(xp):
    """Возвращает уровень (1 уровень = 1000 XP)"""
    return xp // 1000

def get_user_league(level):
    """Возвращает лигу по уровню"""
    for i in range(len(LEAGUES) - 1, -1, -1):
        if level >= LEAGUES[i]["level_needed"]:
            return LEAGUES[i], i
    return None, -1

def get_next_league(level):
    """Возвращает следующую лигу и сколько уровней до неё"""
    for league in LEAGUES:
        if level < league["level_needed"]:
            return league, league["level_needed"] - level
    return None, 0

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
    conn.commit()
    conn.close()

def get_user(user_id, peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT xp, warns, job_cooldown, role, muted_until, duels_won, duels_lost, nickname, last_daily, daily_streak, league_level FROM users WHERE user_id = ? AND peer_id = ?", (user_id, peer_id))
    row = cursor.fetchone()
    
    if not row:
        initial_role = 8 if user_id == CREATOR_ID else 0
        cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (?, ?, ?)", (user_id, peer_id, initial_role))
        conn.commit()
        row = (0, 0, 0, initial_role, 0, 0, 0, "", 0, 0, 0)
        
    conn.close()
    return {
        "xp": row[0], "warns": row[1], "job_cooldown": row[2], 
        "role": row[3], "muted_until": row[4], "duels_won": row[5], 
        "duels_lost": row[6], "nickname": row[7], "last_daily": row[8], 
        "daily_streak": row[9], "league_level": row[10]
    }

def update_user(user_id, peer_id, field, value):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ? AND peer_id = ?", (value, user_id, peer_id))
    conn.commit()
    conn.close()

def add_xp(user_id, peer_id, amount, msg=None):
    user = get_user(user_id, peer_id)
    old_xp = user["xp"]
    old_level = get_level_from_xp(old_xp)
    new_xp = old_xp + amount
    new_level = get_level_from_xp(new_xp)
    
    update_user(user_id, peer_id, "xp", new_xp)
    
    # Проверка повышения лиги
    old_league, old_idx = get_user_league(user["league_level"] if user["league_level"] > 0 else old_level)
    current_league_level = user["league_level"] if user["league_level"] > 0 else old_level
    new_league, new_idx = get_user_league(current_league_level)
    
    # Обновляем лигу если нужно
    current_league, _ = get_user_league(current_league_level)
    next_league, levels_needed = get_next_league(current_league_level)
    
    if current_league_level >= 50 and user["league_level"] == 0:
        # Первое попадание в лигу
        update_user(user_id, peer_id, "league_level", current_league_level)
        try:
            vk.messages.send(peer_id=peer_id, message=f"🏆 ПОЗДРАВЛЯЮ! {get_display_name(user_id, peer_id)} достиг {current_league['emoji']} {current_league['name']} лиги!", random_id=random.getrandbits(64))
        except:
            pass
    
    if new_level > 0 and new_level % 10 == 0:
        try:
            vk.messages.send(peer_id=peer_id, message=f"🎉 Уровень повышен! {get_display_name(user_id, peer_id)} теперь {new_level} уровень!", random_id=random.getrandbits(64))
        except:
            pass
    
    return new_xp

def get_user_id_from_text(text, vk, peer_id):
    match = re.search(r'\[id(\d+)\|.*?\]', text)
    if match:
        return int(match.group(1))
    match = re.search(r'@duplicate(\d+)', text)
    if match:
        return int(match.group(1))
    match = re.search(r'(\d+)', text)
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

# ========== ФУНКЦИИ ДЛЯ СВЯЗКИ БЕСЕД ==========
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

# ========== ФУНКЦИИ ДЛЯ КАСТОМНЫХ КОМАНД ==========
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

# ========== ФУНКЦИИ ДЛЯ РЕЖИМА ТИШИНЫ ==========
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

# ========== ФУНКЦИИ ДЛЯ ПРИВЕТСТВИЯ ==========
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

                # Автоматическое приветствие для новых бесед
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
                        "Команды: /помощь"
                    )
                    vk.messages.send(peer_id=peer_id, message=welcome_text, random_id=random.getrandbits(64))
                    mark_as_greeted(peer_id)

                # Проверка кастомных команд
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

                # Режим тишины
                quiet = get_quiet_settings(peer_id)
                if quiet["enabled"] and sender["role"] < quiet["ignore_rank"]:
                    if not (quiet["ignore_admins"] and sender["role"] >= 1):
                        user_key = f"{peer_id}_{user_id}"
                        last = last_message_time.get(user_key, 0)
                        if current_time - last < quiet["cooldown"]:
                            remaining = quiet["cooldown"] - (current_time - last)
                            vk.messages.send(peer_id=peer_id, message=f"🔇 Режим тишины! Подожди {remaining} сек", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                        last_message_time[user_key] = current_time

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

                # ========== КОМАНДЫ ==========
                
                # /ready
                if text == "/ready":
                    vk.messages.send(peer_id=peer_id, message="⭐ СПАСИБО ЗА ПРАВА АДМИНИСТРАТОРА! ⭐\n\nБот готов к работе!\nНапиши /помощь для списка команд.", random_id=random.getrandbits(64), reply_to=msg_id)

                # /bonus - ежедневный бонус опыта
                elif text == "/bonus":
                    user = get_user(user_id, peer_id)
                    current_time = int(time.time())
                    
                    if current_time - user["last_daily"] < 86400:
                        hours_left = 24 - (current_time - user["last_daily"]) // 3600
                        vk.messages.send(peer_id=peer_id, message=f"⏳ Бонус уже получен! Возвращайся через {hours_left} часов.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    streak = user["daily_streak"]
                    if streak >= 6:
                        xp_reward = 700
                        new_streak = 0
                        message_part = "🔥 СУПЕР БОНУС! День 7! 🔥"
                    else:
                        xp_reward = 100 + (streak * 50)
                        new_streak = streak + 1
                        message_part = f"Ежедневный бонус! День {streak + 1}"
                    
                    new_xp = add_xp(user_id, peer_id, xp_reward, msg)
                    update_user(user_id, peer_id, "last_daily", current_time)
                    update_user(user_id, peer_id, "daily_streak", new_streak)
                    
                    current_level = get_level_from_xp(new_xp)
                    
                    vk.messages.send(peer_id=peer_id, message=f"{message_part}\n\n✨ +{xp_reward} опыта\n📊 Твой уровень: {current_level}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /top - таблица лидеров
                elif text == "/top":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    
                    cursor.execute("""
                        SELECT user_id, xp FROM users 
                        WHERE peer_id = ? AND xp > 0 
                        ORDER BY xp DESC LIMIT 10
                    """, (peer_id,))
                    top_users = cursor.fetchall()
                    conn.close()
                    
                    result_text = "🏆 ТАБЛИЦА ЛИДЕРОВ 🏆\n\n"
                    if top_users:
                        for i, (uid, xp) in enumerate(top_users, 1):
                            level = get_level_from_xp(xp)
                            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"
                            result_text += f"{medal} {get_display_name(uid, peer_id)} — {level} уровень\n"
                    else:
                        result_text += "Нет данных\n"
                    
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # /leagues - список лиг
                elif text == "/leagues":
                    user = get_user(user_id, peer_id)
                    user_level = get_level_from_xp(user["xp"])
                    current_league, _ = get_user_league(user_level)
                    next_league, levels_needed = get_next_league(user_level)
                    
                    result_text = "🏅 СИСТЕМА ЛИГ 🏅\n\n"
                    for i, league in enumerate(LEAGUES):
                        if user_level >= league["level_needed"]:
                            result_text += f"✅ {league['emoji']} {league['name']} — достигнуто\n"
                        else:
                            result_text += f"❌ {league['emoji']} {league['name']} — нужно {league['level_needed']} уровень\n"
                    
                    if levels_needed > 0:
                        result_text += f"\n📊 Твой уровень: {user_level}\n"
                        result_text += f"⚡ До {next_league['emoji']} {next_league['name']} лиги: {levels_needed} уровней"
                    else:
                        result_text += f"\n🏆 Ты достиг высшей лиги! Твой уровень: {user_level}"
                    
                    vk.messages.send(peer_id=peer_id, message=result_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # /quiet - режим тишины
                elif text.startswith("/quiet"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        current = get_quiet_settings(peer_id)
                        status = "ВКЛЮЧЁН" if current["enabled"] else "ВЫКЛЮЧЕН"
                        vk.messages.send(peer_id=peer_id, message=f"📊 РЕЖИМ ТИШИНЫ\n\nСтатус: {status}\nЗадержка: {current['cooldown']} сек\nИгнор админов: {'Да' if current['ignore_admins'] else 'Нет'}\n\nКоманды:\n/quiet on/off — включить/выключить\n/quiet time <сек> — задержка\n/quiet ignore_admins on/off", random_id=random.getrandbits(64), reply_to=msg_id)
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
                    elif parts[1] == "ignore_admins" and len(parts) >= 3:
                        if parts[2] == "on":
                            set_quiet_ignore_admins(peer_id, True)
                            vk.messages.send(peer_id=peer_id, message=f"👑 Администраторы игнорируют режим тишины!", random_id=random.getrandbits(64), reply_to=msg_id)
                        elif parts[2] == "off":
                            set_quiet_ignore_admins(peer_id, False)
                            vk.messages.send(peer_id=peer_id, message=f"👑 Администраторы НЕ игнорируют режим тишины!", random_id=random.getrandbits(64), reply_to=msg_id)
                        else:
                            vk.messages.send(peer_id=peer_id, message="❌ Используй: /quiet ignore_admins on/off", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно: /quiet on/off\n/quiet time <сек>\n/quiet ignore_admins on/off", random_id=random.getrandbits(64), reply_to=msg_id)

                # /staff
                elif text == "/staff":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, role FROM users WHERE peer_id = ? AND role > 0 ORDER BY role DESC", (peer_id,))
                    staff_list = cursor.fetchall()
                    conn.close()
                    
                    if not staff_list:
                        vk.messages.send(peer_id=peer_id, message="Администрация отсутствует.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    staff_text = "👑 АДМИНИСТРАЦИЯ ЧАТА 👑\n\n"
                    for uid, role in staff_list:
                        role_name = RANGS.get(role, f"Ранг {role}")
                        staff_text += f"• {role_name}: {get_display_name(uid, peer_id)}\n"
                    
                    vk.messages.send(peer_id=peer_id, message=staff_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # /nick и /delnick
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

                # /warnlist
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
                        vk.messages.send(peer_id=peer_id, message="Нет пользователей с варнами.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    warn_text = "⚠️ НАРУШИТЕЛИ ⚠️\n\n"
                    for uid, warns in warn_list:
                        warn_text += f"• {get_display_name(uid, peer_id)} — {warns}/3 варнов\n"
                    
                    vk.messages.send(peer_id=peer_id, message=warn_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # /unwarn
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
                    vk.messages.send(peer_id=peer_id, message=f"✅ Снят варн! У {get_display_name(target_id, peer_id)} теперь {new_warns}/3{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /banlist
                elif text == "/banlist":
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        response = vk.groups.getBanned(group_id=GROUP_ID, count=200)
                        banned_users = response.get('items', [])
                        
                        if not banned_users:
                            vk.messages.send(peer_id=peer_id, message="Чёрный список группы пуст.", random_id=random.getrandbits(64), reply_to=msg_id)
                            continue
                        
                        ban_text = "⛔ ЧЁРНЫЙ СПИСОК ⛔\n\n"
                        for item in banned_users[:30]:
                            user = item.get('profile', {})
                            user_id_item = user.get('id')
                            first_name = user.get('first_name', '')
                            last_name = user.get('last_name', '')
                            ban_text += f"• {first_name} {last_name}\n"
                        
                        if len(banned_users) > 30:
                            ban_text += f"\n...и ещё {len(banned_users) - 30} пользователей."
                        
                        vk.messages.send(peer_id=peer_id, message=ban_text, random_id=random.getrandbits(64), reply_to=msg_id)
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /duel
                elif text.startswith("/duel"):
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /duel 100 @user", random_id=random.getrandbits(64), reply_to=msg_id)
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
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи соперника: /duel 100 @user", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if opponent_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя вызвать себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    opponent = get_user(opponent_id, peer_id)
                    
                    active_duels[user_id] = (opponent_id, amount, time.time())
                    
                    thread = threading.Thread(target=duel_timeout, args=(user_id, opponent_id, peer_id))
                    thread.daemon = True
                    thread.start()
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ ДУЭЛЬ! ⚔️\n\n{get_display_name(user_id, peer_id)} вызывает {get_display_name(opponent_id, peer_id)}\n💰 Ставка: {amount} XP\n\nНапиши /accept в ответ за 60 сек!", random_id=random.getrandbits(64), reply_to=msg_id, forward_messages=msg_id)

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
                    
                    add_xp(winner_id, peer_id, amount, msg)
                    add_xp(loser_id, peer_id, 10, msg)
                    
                    update_user(winner_id, peer_id, "duels_won", get_user(winner_id, peer_id)["duels_won"] + 1)
                    update_user(loser_id, peer_id, "duels_lost", get_user(loser_id, peer_id)["duels_lost"] + 1)
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ ПОБЕДИТЕЛЬ: {get_display_name(winner_id, peer_id)}!\n💰 Выигрыш: {amount} XP!", random_id=random.getrandbits(64), reply_to=msg_id)

                # /get
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
                        f"👤 {get_display_name(target_id, peer_id)}\n"
                        f"🆔 ID: {target_id}\n"
                        f"👑 Ранг: {RANGS.get(target_info['role'], 'Пользователь')}\n"
                        f"📊 Уровень: {target_level}\n"
                        f"✨ Опыт: {target_info['xp']:,}\n"
                        f"⚠️ Варны: {target_info['warns']}/3\n"
                        f"🔇 Мут: {'Да (' + format_time(mute_left) + ')' if is_muted else 'Нет'}\n"
                        f"⚔️ Дуэли: Побед {target_info['duels_won']} / Поражений {target_info['duels_lost']}\n"
                        f"🚫 Бан: {'Да' if is_banned else 'Нет'}"
                    )
                    vk.messages.send(peer_id=peer_id, message=info_text, random_id=random.getrandbits(64), reply_to=msg_id)

                # /mute
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
                        if unit == 'с':
                            time_seconds = num
                        elif unit == 'м':
                            time_seconds = num * 60
                        elif unit == 'ч':
                            time_seconds = num * 3600
                        elif unit == 'д':
                            time_seconds = num * 86400
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
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя замутить себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя замутить старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", int(time.time()) + time_seconds)
                    
                    thread = threading.Thread(target=unmute_user_delayed, args=(target_id, peer_id, time_seconds))
                    thread.daemon = True
                    thread.start()
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    vk.messages.send(peer_id=peer_id, message=f"🔇 Мут на {format_time(time_seconds)}{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /unmute
                elif text.startswith("/unmute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", 0)
                    vk.messages.send(peer_id=peer_id, message=f"🔊 Пользователь размучен!", random_id=random.getrandbits(64), reply_to=msg_id)

                # /warn
                elif text.startswith("/warn"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /warn @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать варн себе!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать варн старшему рангу!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    new_warns = target_user["warns"] + 1
                    reason_text = f" Причина: {reason}" if reason else ""
                    
                    if new_warns >= 3:
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            update_user(target_id, peer_id, "warns", 0)
                            vk.messages.send(peer_id=peer_id, message=f"🔴 3/3 варнов - кикнут!{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)
                        except:
                            vk.messages.send(peer_id=peer_id, message=f"⚠️ 3/3 варнов, но кикнуть не могу (дайте права админа)", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        update_user(target_id, peer_id, "warns", new_warns)
                        vk.messages.send(peer_id=peer_id, message=f"⚠️ Варн {new_warns}/3{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /kick
                elif text.startswith("/kick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /kick @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть себя!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    try:
                        chat_id = peer_id - 2000000000
                        vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"💥 Пользователь кикнут{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)
                    except:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: дайте боту права админа", random_id=random.getrandbits(64), reply_to=msg_id)

                # /setrank
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
                    vk.messages.send(peer_id=peer_id, message=f"👑 Пользователю {get_display_name(target_id, peer_id)} выдан ранг: {RANGS.get(new_role, '?')}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /unrank
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
                    vk.messages.send(peer_id=peer_id, message=f"❌ У {get_display_name(target_id, peer_id)} снят ранг!", random_id=random.getrandbits(64), reply_to=msg_id)

                # /cmd
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
                    
                    if original_cmd.startswith('/'):
                        original_cmd = original_cmd[1:]
                    if alias.startswith('/'):
                        alias = alias[1:]
                    
                    builtin_commands = ["помощь", "профиль", "работать", "работа", "казино", "staff", "get", "mute", "unmute", 
                                       "warn", "warnlist", "unwarn", "kick", "ban", "unban", "banlist", "setrank", "unrank",
                                       "duel", "accept", "nick", "delnick", "linkchat", "unlinkchat", "chats", "gban", "gunban", 
                                       "gkick", "gsetrank", "gdelrank", "cmd", "delcmd", "cmdlist", "top", "bonus", "leagues", "quiet", "ready"]
                    
                    if alias in builtin_commands:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Нельзя переопределить команду /{alias}!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    existing = get_original_command(peer_id, alias)
                    if existing:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Псевдоним /{alias} уже существует!\nУдалить: /delcmd {alias}", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    add_custom_command(peer_id, user_id, original_cmd, alias)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Создан псевдоним!\n\n/{alias} → /{original_cmd}", random_id=random.getrandbits(64), reply_to=msg_id)

                # /delcmd
                elif text.startswith("/delcmd"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи псевдоним: /delcmd выгнать", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    alias = parts[1].lower()
                    if alias.startswith('/'):
                        alias = alias[1:]
                    
                    existing = get_original_command(peer_id, alias)
                    if not existing:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Псевдоним /{alias} не найден!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    remove_custom_command(peer_id, alias)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Псевдоним /{alias} удалён!", random_id=random.getrandbits(64), reply_to=msg_id)

                # /cmdlist
                elif text == "/cmdlist":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    my_cmds = get_user_custom_commands(peer_id, user_id)
                    all_cmds = get_all_custom_commands(peer_id)
                    
                    if not my_cmds and not all_cmds:
                        vk.messages.send(peer_id=peer_id, message="Нет созданных псевдонимов.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    result_text = "📋 ПСЕВДОНИМЫ КОМАНД\n\n"
                    
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

                # /linkchat, /unlinkchat, /chats, /gban, /gunban, /gkick, /gsetrank, /gdelrank
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
                        vk.messages.send(peer_id=peer_id, message="Нет связанных бесед.", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chat_list = "🌐 СВЯЗАННЫЕ БЕСЕДЫ\n\n"
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
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя забанить старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
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
                    
                    vk.messages.send(peer_id=peer_id, message=f"⛔ ГЛОБАЛЬНЫЙ БАН!\n\n{get_display_name(target_id, peer_id)} забанен в {banned_count} беседах{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/gunban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец. Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /gunban @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    unbanned_count = 0
                    
                    for chat_peer in chats:
                        try:
                            vk.groups.unbanUser(group_id=GROUP_ID, user_id=target_id)
                            unbanned_count += 1
                        except:
                            pass
                    
                    vk.messages.send(peer_id=peer_id, message=f"🔓 ГЛОБАЛЬНЫЙ РАЗБАН!\n\n{get_display_name(target_id, peer_id)} разбанен в {unbanned_count} беседах{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

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
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
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
                    
                    vk.messages.send(peer_id=peer_id, message=f"💥 ГЛОБАЛЬНЫЙ КИК!\n\n{get_display_name(target_id, peer_id)} исключён из {kicked_count} бесед{reason_text}", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/gsetrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /gsetrank 4 @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    try:
                        new_role = int(parts[1])
                        if new_role < 0 or new_role > 8:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Ранг 0-8", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать ранг выше своего!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    success_count = 0
                    for chat_peer in chats:
                        try:
                            update_user(target_id, chat_peer, "role", new_role)
                            success_count += 1
                        except:
                            pass
                    
                    rank_name = RANGS.get(new_role, "?")
                    vk.messages.send(peer_id=peer_id, message=f"👑 ГЛОБАЛЬНАЯ ВЫДАЧА РАНГА!\n\n{get_display_name(target_id, peer_id)} получил ранг {rank_name} в {success_count} беседах!", random_id=random.getrandbits(64), reply_to=msg_id)

                elif text.startswith("/gdelrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Админ+", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажи пользователя: /gdelrank @пользователь", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя снять ранг у старшего ранга!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    success_count = 0
                    for chat_peer in chats:
                        try:
                            update_user(target_id, chat_peer, "role", 0)
                            success_count += 1
                        except:
                            pass
                    
                    vk.messages.send(peer_id=peer_id, message=f"❌ ГЛОБАЛЬНОЕ СНЯТИЕ РАНГА!\n\nУ {get_display_name(target_id, peer_id)} снят ранг в {success_count} беседах!", random_id=random.getrandbits(64), reply_to=msg_id)

                # ИГРОВЫЕ КОМАНДЫ
                elif text in ["профиль", "/stats"]:
                    user = get_user(user_id, peer_id)
                    user_level = get_level_from_xp(user["xp"])
                    is_muted = user["muted_until"] > int(time.time())
                    mute_left = user["muted_until"] - int(time.time()) if is_muted else 0
                    
                    profile_text = (
                        f"👤 {get_display_name(user_id, peer_id)}\n"
                        f"👑 Ранг: {RANGS.get(user['role'], 'Пользователь')}\n"
                        f"📊 Уровень: {user_level}\n"
                        f"✨ Опыт: {user['xp']:,}\n"
                        f"⚠️ Варны: {user['warns']}/3\n"
                        f"🔇 Мут: {'Да (' + format_time(mute_left) + ')' if is_muted else 'Нет'}\n"
                        f"⚔️ Дуэли: Побед {user['duels_won']} / Поражений {user['duels_lost']}"
                    )
                    vk.messages.send(peer_id=peer_id, message=profile_text, random_id=random.getrandbits(64), reply_to=msg_id)
                    
                elif text in ["работать", "работа"]:
                    user = get_user(user_id, peer_id)
                    current_time = int(time.time())
                    if current_time < user["job_cooldown"]:
                        left = user["job_cooldown"] - current_time
                        vk.messages.send(peer_id=peer_id, message=f"⏳ Отдых {format_time(left)}", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        xp_reward = random.randint(50, 150)
                        new_xp = add_xp(user_id, peer_id, xp_reward, msg)
                        update_user(user_id, peer_id, "job_cooldown", current_time + 600)
                        new_level = get_level_from_xp(new_xp)
                        
                        vk.messages.send(peer_id=peer_id, message=f"💼 РАБОТА!\n\n✨ +{xp_reward} опыта\n📊 Твой уровень: {new_level}", random_id=random.getrandbits(64), reply_to=msg_id)
                        
                elif text.startswith("казино"):
                    try:
                        parts = text.split()
                        if len(parts) < 2:
                            raise ValueError
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="🎰 Пример: казино 100", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    if amount > 1000:
                        vk.messages.send(peer_id=peer_id, message="❌ Максимальная ставка: 1000 XP", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                    
                    user = get_user(user_id, peer_id)
                    if user["xp"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Не хватает опыта! У тебя {user['xp']} XP", random_id=random.getrandbits(64), reply_to=msg_id)
                        continue
                        
                    if random.choice([True, False]):
                        new_xp = add_xp(user_id, peer_id, amount, msg)
                        new_level = get_level_from_xp(new_xp)
                        vk.messages.send(peer_id=peer_id, message=f"🎉 ПОБЕДА! 🎉\n\n✨ +{amount} опыта\n📊 Твой уровень: {new_level}", random_id=random.getrandbits(64), reply_to=msg_id)
                    else:
                        update_user(user_id, peer_id, "xp", user["xp"] - amount)
                        add_xp(user_id, peer_id, 0, msg)
                        new_level = get_level_from_xp(user["xp"] - amount)
                        vk.messages.send(peer_id=peer_id, message=f"📉 ПРОИГРЫШ!\n\n✨ -{amount} опыта\n📊 Твой уровень: {new_level}", random_id=random.getrandbits(64), reply_to=msg_id)

                # СПРАВКА
                elif text in ["помощь", "/help", "команды"]:
                    help_text = (
                        "⚙️ ИГРОВЫЕ КОМАНДЫ\n"
                        "/профиль — статистика\n"
                        "/работать — заработать опыт\n"
                        "/казино 100 — сыграть\n"
                        "/duel 100 @пользователь — дуэль\n"
                        "/bonus — ежедневный бонус\n"
                        "/top — топ игроков\n"
                        "/leagues — список лиг\n\n"
                        "🔨 АДМИН КОМАНДЫ\n"
                        "/get @пользователь — информация\n"
                        "/mute 10м @пользователь — мут\n"
                        "/unmute @пользователь — снять мут\n"
                        "/warn @пользователь — предупреждение\n"
                        "/unwarn @пользователь — снять варн\n"
                        "/warnlist — список нарушителей\n"
                        "/kick @пользователь — кикнуть\n"
                        "/ban @пользователь — бан (с 5 ранга)\n"
                        "/unban @пользователь — разбан\n"
                        "/banlist — список забаненных\n"
                        "/setrank 4 @пользователь — выдать ранг\n"
                        "/unrank @пользователь — снять ранг\n"
                        "/staff — администрация\n\n"
                        "🔇 РЕЖИМ ТИШИНЫ\n"
                        "/quiet on/off — включить/выключить\n"
                        "/quiet time <сек> — задержка\n\n"
                        "🌐 ГЛОБАЛЬНЫЕ КОМАНДЫ\n"
                        "/linkchat — связать беседы\n"
                        "/gban @пользователь — бан везде\n"
                        "/gkick @пользователь — кик везде\n"
                        "/gsetrank 4 @пользователь — выдать ранг везде\n\n"
                        "🔧 НАСТРОЙКА\n"
                        "/cmd [команда] [новое название] — создать псевдоним\n"
                        "Пример: /cmd kick выгнать\n"
                        "/delcmd [псевдоним] — удалить\n"
                        "/cmdlist — список псевдонимов\n"
                        "/nick [ник] — установить ник\n"
                        "/delnick — удалить ник\n"
                        "/ready — подтвердить права админа"
                    )
                    vk.messages.send(peer_id=peer_id, message=help_text, random_id=random.getrandbits(64), reply_to=msg_id)

    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(5)
