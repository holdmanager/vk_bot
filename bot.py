"""
HOLD MANAGER BOT - Многофункциональный бот для управления беседами ВКонтакте
Версия: 2.0
GitHub: https://github.com/holdmanager/bot
"""

import time
import random
import sqlite3
import vk_api
import os
import re
import threading
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

# ========== НАСТРОЙКА БОТА ==========
TOKEN = "vk1.a.6MgeFoEYyOVXYub3mwbY_Lvz99OfYYjP_zI0tKnQTBpDtj5pdAO5ETSMNLJ5cKU-GJ7r5fDH5uydayMQFQZGxKP-YSqIIhMaN_a3BpQIUjPHKc5oBCmCv1ju4_gTPxX30Pjfw3yRIZWxwUWznKq0QpYZCC41PFD_jcZtdq9p_8I8TkksE-9aAGN-DGBvJWdXl-2hZKhgycaEtMocuFo8cg"  # Токен от группы ВК
GROUP_ID = 237472128  # ID вашей группы
CREATOR_ID = 736337130  # Ваш ID ВК
# ====================================

# Словари для работы бота
active_duels = {}
processed_messages = {}

# Ранги администрации
RANGS = {
    0: "👤 Обычный Пользователь",
    1: "🛡️ Куратор",
    2: "🔧 Технический Специалист",
    3: "⭐ Зам. Главного Администратора",
    4: "👑 Главный Администратор",
    5: "💎 Спец Администратор",
    6: "🚀 Руководитель Проекта",
    7: "⚡ Заместитель Владельца",
    8: "👑 Владелец"
}

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            peer_id INTEGER,
            balance INTEGER DEFAULT 5000,
            warns INTEGER DEFAULT 0,
            job_cooldown INTEGER DEFAULT 0,
            role INTEGER DEFAULT 0,
            muted_until INTEGER DEFAULT 0,
            duels_won INTEGER DEFAULT 0,
            duels_lost INTEGER DEFAULT 0,
            nickname TEXT DEFAULT '',
            PRIMARY KEY (user_id, peer_id)
        )
    """)
    
    # Таблица связанных бесед
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS linked_chats (
            peer_id INTEGER PRIMARY KEY,
            linked_by INTEGER,
            linked_at INTEGER
        )
    """)
    
    # Таблица кастомных команд
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
    
    conn.commit()
    conn.close()

# Получение данных пользователя
def get_user(user_id, peer_id):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance, warns, job_cooldown, role, muted_until, duels_won, duels_lost, nickname FROM users WHERE user_id = ? AND peer_id = ?", (user_id, peer_id))
    row = cursor.fetchone()
    
    if not row:
        initial_role = 8 if user_id == CREATOR_ID else 0
        cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (?, ?, ?)", (user_id, peer_id, initial_role))
        conn.commit()
        row = (5000, 0, 0, initial_role, 0, 0, 0, "")
        
    conn.close()
    return {
        "balance": row[0], 
        "warns": row[1], 
        "job_cooldown": row[2], 
        "role": row[3], 
        "muted_until": row[4], 
        "duels_won": row[5], 
        "duels_lost": row[6], 
        "nickname": row[7]
    }

# Обновление данных пользователя
def update_user(user_id, peer_id, field, value):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ? AND peer_id = ?", (value, user_id, peer_id))
    conn.commit()
    conn.close()

# Получение ID пользователя из текста
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

# Проверка бана пользователя
def is_user_banned_in_group(vk, group_id, user_id):
    try:
        response = vk.groups.getBanned(group_id=group_id)
        for item in response['items']:
            if item['profile']['id'] == user_id:
                return True
    except:
        pass
    return False

# Форматирование времени
def format_time(seconds):
    if seconds <= 0:
        return "0 сек"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    elif minutes > 0:
        return f"{minutes} мин {secs} сек"
    else:
        return f"{secs} сек"

# Автоматический размут
def unmute_user_delayed(user_id, peer_id, mute_time):
    time.sleep(mute_time)
    update_user(user_id, peer_id, "muted_until", 0)
    try:
        vk.messages.send(peer_id=peer_id, message=f"🔊 [id{user_id}|Пользователь] был размучен автоматически!", random_id=random.getrandbits(64))
    except:
        pass

# Таймаут дуэли
def duel_timeout(caller_id, opponent_id, peer_id):
    time.sleep(60)
    if caller_id in active_duels and active_duels[caller_id][0] == opponent_id:
        del active_duels[caller_id]
        try:
            vk.messages.send(peer_id=peer_id, message=f"⏰ Дуэль была автоматически отменена (прошло 60 секунд)", random_id=random.getrandbits(64))
        except:
            pass

# Получение отображаемого имени
def get_display_name(user_id, peer_id):
    user = get_user(user_id, peer_id)
    if user["nickname"]:
        return user["nickname"]
    try:
        user_info = vk.users.get(user_ids=user_id)[0]
        return f"{user_info['first_name']} {user_info['last_name']}"
    except:
        return f"id{user_id}"

# Функции для работы со связанными беседами
def get_linked_chats():
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("SELECT peer_id FROM linked_chats")
    chats = [row[0] for row in cursor.fetchall()]
    conn.close()
    return chats

def add_linked_chat(peer_id, linked_by):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO linked_chats (peer_id, linked_by, linked_at) VALUES (?, ?, ?)", 
                   (peer_id, linked_by, int(time.time())))
    conn.commit()
    conn.close()

def remove_linked_chat(peer_id):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM linked_chats WHERE peer_id = ?", (peer_id,))
    conn.commit()
    conn.close()

def is_chat_linked(peer_id):
    conn = sqlite3.connect("hold_manager.db")
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

# Функции для кастомных команд
def add_custom_command(peer_id, user_id, original_cmd, alias):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR REPLACE INTO custom_commands (peer_id, user_id, original_cmd, alias, created_at) 
        VALUES (?, ?, ?, ?, ?)
    """, (peer_id, user_id, original_cmd.lower(), alias.lower(), int(time.time())))
    conn.commit()
    conn.close()

def remove_custom_command(peer_id, alias):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM custom_commands WHERE peer_id = ? AND alias = ?", (peer_id, alias.lower()))
    conn.commit()
    conn.close()

def get_original_command(peer_id, alias):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd FROM custom_commands WHERE peer_id = ? AND alias = ?", (peer_id, alias.lower()))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def get_user_custom_commands(peer_id, user_id):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd, alias FROM custom_commands WHERE peer_id = ? AND user_id = ?", (peer_id, user_id))
    cmds = cursor.fetchall()
    conn.close()
    return cmds

def get_all_custom_commands(peer_id):
    conn = sqlite3.connect("hold_manager.db")
    cursor = conn.cursor()
    cursor.execute("SELECT original_cmd, alias, user_id FROM custom_commands WHERE peer_id = ?", (peer_id,))
    cmds = cursor.fetchall()
    conn.close()
    return cmds

# Функция для получения цели и причины
def get_target_and_reason(text, msg, vk, peer_id):
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

# Инициализация БД
init_db()

# Подключение к ВК
try:
    vk_session = vk_api.VkApi(token=TOKEN)
    vk = vk_session.get_api()
    group_info = vk.groups.getById()
    if group_info:
        print(f"✅ Бот успешно подключен к группе: {group_info[0]['name']}")
        print(f"📱 ID группы: {GROUP_ID}")
        print(f"👤 ID создателя: {CREATOR_ID}")
    else:
        print("❌ Ошибка: не удалось получить информацию о группе")
        print("💡 Проверьте правильность TOKEN и GROUP_ID")
        exit()
except Exception as e:
    print(f"❌ Критическая ошибка при подключении: {e}")
    print("💡 Убедитесь, что:")
    print("   1. Токен действителен и имеет нужные права")
    print("   2. Указан правильный ID группы")
    print("   3. Бот добавлен в беседу и имеет права администратора")
    exit()

longpoll = VkBotLongPoll(vk_session, GROUP_ID)
print("=" * 50)
print("🚀 HOLD MANAGER BOT успешно запущен!")
print("📡 Бот готов к работе во всех беседах")
print("=" * 50)

# Основной цикл обработки сообщений
while True:
    try:
        for event in longpoll.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                msg = event.obj.message if 'message' in event.obj else event.obj
                
                # Защита от дублирования сообщений
                msg_id = msg.get('id')
                if msg_id and msg_id in processed_messages:
                    continue
                if msg_id:
                    processed_messages[msg_id] = time.time()
                    # Очистка старых сообщений
                    for old_id in list(processed_messages.keys()):
                        if time.time() - processed_messages[old_id] > 10:
                            del processed_messages[old_id]
                
                text = msg['text'].lower().strip()
                peer_id = msg['peer_id']
                user_id = msg['from_id']
                
                # Игнорируем сообщения от групп
                if user_id < 0:
                    continue

                # Проверка на кастомные команды
                cmd_parts = text.split()
                if cmd_parts:
                    alias = cmd_parts[0]
                    if alias.startswith('/'):
                        alias = alias[1:]
                    original = get_original_command(peer_id, alias)
                    if original:
                        text = '/' + original + ' ' + ' '.join(cmd_parts[1:])

                sender = get_user(user_id, peer_id)
                
                # Проверка мута
                current_time = int(time.time())
                if sender["muted_until"] > current_time and sender["role"] < 1:
                    remaining = sender["muted_until"] - current_time
                    vk.messages.send(
                        peer_id=peer_id, 
                        message=f"🔇 Вы находитесь в муте! Осталось времени: {format_time(remaining)}\n\n"
                                f"💡 Для снятия мута обратитесь к администрации чата.", 
                        random_id=random.getrandbits(64)
                    )
                    continue

                # ==================== ОСНОВНЫЕ КОМАНДЫ ====================
                
                # 📋 ПОМОЩЬ
                if text in ["помощь", "/help", "команды", "help"]:
                    help_text = """🤖 **HOLD MANAGER BOT v2.0**

━━━━━━━━━━━━━━━━━━━━━
🎮 **ИГРОВЫЕ КОМАНДЫ**
━━━━━━━━━━━━━━━━━━━━━
📊 `Профиль` — Ваша статистика
💼 `Работать` — Заработать монеты (300-1500)
🎰 `Казино [сумма]` — Сыграть в казино
⚔️ `/duel [сумма] @user` — Вызвать на дуэль

━━━━━━━━━━━━━━━━━━━━━
👑 **ИНФОРМАЦИОННЫЕ**
━━━━━━━━━━━━━━━━━━━━━
📋 `/staff` — Список администрации
🏷️ `/nick [ник]` — Установить ник (Куратор+)
❌ `/delnick` — Удалить ник (Куратор+)

━━━━━━━━━━━━━━━━━━━━━
🔨 **АДМИНИСТРИРОВАНИЕ**
━━━━━━━━━━━━━━━━━━━━━
📋 `/get @user` — Информация о пользователе
🔇 `/mute [время] @user` — Замутить (1 ранга+)
🔊 `/unmute @user` — Размутить (1 ранга+)
⚠️ `/warn @user` — Выдать предупреждение (1 ранга+)
📋 `/warnlist` — Список нарушителей (1 ранга+)
🔓 `/unwarn @user` — Снять варн (1 ранга+)
💥 `/kick @user` — Исключить из чата (1 ранга+)
⛔ `/ban @user` — Забанить в группе (5 ранга+)
🔓 `/unban @user` — Разбанить (5 ранга+)
📋 `/banlist` — Черный список (5 ранга+)
👑 `/setrank [0-8] @user` — Выдать ранг (4 ранга+)
❌ `/unrank @user` — Снять ранг (4 ранга+)

━━━━━━━━━━━━━━━━━━━━━
🌐 **ГЛОБАЛЬНЫЕ КОМАНДЫ** (для связанных бесед)
━━━━━━━━━━━━━━━━━━━━━
🔗 `/linkchat` — Добавить беседу в сеть (Владелец)
🔓 `/unlinkchat` — Удалить беседу из сети (Владелец)
📋 `/chats` — Список связанных бесед (Владелец)
👑 `/gsetrank [0-8] @user` — Выдать ранг везде (4 ранга+)
❌ `/gdelrank @user` — Снять ранг везде (4 ранга+)
⛔ `/gban @user` — Забанить везде (5 ранга+)
🔓 `/gunban @user` — Разбанить везде (5 ранга+)
💥 `/gkick @user` — Кикнуть везде (3 ранга+)

━━━━━━━━━━━━━━━━━━━━━
🔧 **НАСТРОЙКА КОМАНД** (с 1 ранга)
━━━━━━━━━━━━━━━━━━━━━
📝 `/cmd [команда] [псевдоним]` — Создать псевдоним
🗑️ `/delcmd [псевдоним]` — Удалить псевдоним
📋 `/cmdlist` — Список псевдонимов

━━━━━━━━━━━━━━━━━━━━━
⏱️ **ФОРМАТ ВРЕМЕНИ:** 10с, 5м, 2ч, 1д
💡 **ПРИМЕЧАНИЕ:** Причина для действий указывается необязательно
━━━━━━━━━━━━━━━━━━━━━

📌 **Правильное использование:**
• Для админ-команд отвечайте на сообщение пользователя
• Или укажите @упоминание после команды
• Пример: `/mute 10м @user Спам в чате`

🆘 По всем вопросам обращайтесь к создателю бота"""
                    
                    vk.messages.send(peer_id=peer_id, message=help_text, random_id=random.getrandbits(64))
                
                # 📊 ПРОФИЛЬ
                elif text in ["профиль", "/stats", "статистика"]:
                    user = get_user(user_id, peer_id)
                    is_muted = user["muted_until"] > int(time.time())
                    mute_left = user["muted_until"] - int(time.time()) if is_muted else 0
                    
                    profile_text = f"""📊 **ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ**

━━━━━━━━━━━━━━━━━━━━━
👤 **Имя:** {get_display_name(user_id, peer_id)}
🆔 **ID:** {user_id}
👑 **Ранг:** {RANGS.get(user['role'], '❓ Неизвестно')}
━━━━━━━━━━━━━━━━━━━━━
💰 **Баланс:** {user['balance']:,} монет
⚠️ **Варны:** {user['warns']}/3
🔇 **Мут:** {f'ДА ({format_time(mute_left)})' if is_muted else 'НЕТ'}
━━━━━━━━━━━━━━━━━━━━━
⚔️ **Дуэли:** 🏆 {user['duels_won']} побед | 💀 {user['duels_lost']} поражений
━━━━━━━━━━━━━━━━━━━━━

💡 Для получения информации о другом пользователе используйте `/get @user`"""
                    
                    vk.messages.send(peer_id=peer_id, message=profile_text, random_id=random.getrandbits(64))
                
                # 💼 РАБОТА
                elif text in ["работать", "работа", "work"]:
                    user = get_user(user_id, peer_id)
                    current_time = int(time.time())
                    
                    if current_time < user["job_cooldown"]:
                        left = user["job_cooldown"] - current_time
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"⏳ **Вы устали!**\n\n"
                                    f"📅 Отдых до: {format_time(left)}\n\n"
                                    f"💡 Вернитесь позже, чтобы снова заработать монеты!", 
                            random_id=random.getrandbits(64)
                        )
                    else:
                        salary = random.randint(300, 1500)
                        new_balance = user["balance"] + salary
                        update_user(user_id, peer_id, "balance", new_balance)
                        update_user(user_id, peer_id, "job_cooldown", current_time + 600)
                        
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"💼 **Работа успешно выполнена!**\n\n"
                                    f"💰 +{salary} монет\n"
                                    f"📊 Новый баланс: {new_balance:,} монет\n\n"
                                    f"⏱️ Следующая работа доступна через 10 минут", 
                            random_id=random.getrandbits(64)
                        )
                
                # 🎰 КАЗИНО
                elif text.startswith("казино"):
                    try:
                        parts = text.split()
                        if len(parts) < 2:
                            raise ValueError
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                        if amount > 1000000:
                            vk.messages.send(peer_id=peer_id, message=f"❌ **Максимальная ставка:** 1,000,000 монет!", random_id=random.getrandbits(64))
                            continue
                    except:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"🎰 **Как играть в казино:**\n\n"
                                    f"📝 Напишите: `казино [сумма]`\n"
                                    f"📌 Пример: `казино 500`\n\n"
                                    f"⚡ Шанс на победу: 50%\n"
                                    f"💰 Минимальная ставка: 1 монета", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    user = get_user(user_id, peer_id)
                    if user["balance"] < amount:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Недостаточно средств!**\n\n"
                                    f"💰 Ваш баланс: {user['balance']:,} монет\n"
                                    f"🎲 Требуется: {amount:,} монет\n\n"
                                    f"💡 Поработайте, чтобы заработать монеты!", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    # 50% шанс на победу
                    if random.random() < 0.5:
                        new_balance = user["balance"] + amount
                        update_user(user_id, peer_id, "balance", new_balance)
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"🎉 **ПОБЕДА!** 🎉\n\n"
                                    f"✨ Вы выиграли: +{amount:,} монет\n"
                                    f"💰 Новый баланс: {new_balance:,} монет\n\n"
                                    f"🎯 Желаем удачи в следующий раз!", 
                            random_id=random.getrandbits(64)
                        )
                    else:
                        new_balance = user["balance"] - amount
                        update_user(user_id, peer_id, "balance", new_balance)
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"💔 **ПРОИГРЫШ!** 💔\n\n"
                                    f"📉 Вы проиграли: -{amount:,} монет\n"
                                    f"💰 Новый баланс: {new_balance:,} монет\n\n"
                                    f"🍀 В следующий раз повезет больше!", 
                            random_id=random.getrandbits(64)
                        )
                
                # 📋 СПИСОК АДМИНИСТРАЦИИ
                elif text == "/staff":
                    conn = sqlite3.connect("hold_manager.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, role FROM users WHERE peer_id = ? AND role > 0 ORDER BY role DESC", (peer_id,))
                    staff_list = cursor.fetchall()
                    conn.close()
                    
                    if not staff_list:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"👑 **Администрация чата**\n\n"
                                    f"📋 В данном чате пока нет назначенной администрации.\n\n"
                                    f"💡 Для назначения администрации используйте команду `/setrank`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    staff_by_role = {}
                    for uid, role in staff_list:
                        if role not in staff_by_role:
                            staff_by_role[role] = []
                        staff_by_role[role].append(uid)
                    
                    staff_text = "👑 **АДМИНИСТРАЦИЯ ЧАТА**\n━━━━━━━━━━━━━━━━━━━━━\n\n"
                    for role in sorted(staff_by_role.keys(), reverse=True):
                        role_name = RANGS.get(role, f"Ранг {role}")
                        staff_text += f"⭐ **{role_name}:**\n"
                        for uid in staff_by_role[role]:
                            staff_text += f"   • {get_display_name(uid, peer_id)}\n"
                        staff_text += "\n"
                    
                    staff_text += "━━━━━━━━━━━━━━━━━━━━━\n💡 По всем вопросам обращайтесь к администрации"
                    
                    vk.messages.send(peer_id=peer_id, message=staff_text, random_id=random.getrandbits(64))
                
                # 🏷️ УСТАНОВКА НИКА
                elif text.startswith("/nick"):
                    if sender["role"] < 1:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Доступ запрещен!**\n\n"
                                    f"🔒 Для использования команды `/nick` требуется ранг не ниже **Куратора**\n\n"
                                    f"👑 Ваш текущий ранг: {RANGS.get(sender['role'], '❓')}", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"🏷️ **Как установить ник:**\n\n"
                                    f"📝 Напишите: `/ник [ваш ник]`\n"
                                    f"📌 Пример: `/ник Администратор`\n\n"
                                    f"⚠️ **Ограничения:**\n"
                                    f"• Максимум 20 символов\n"
                                    f"• Нельзя использовать маты и оскорбления\n\n"
                                    f"❌ Для удаления ника используйте `/delnick`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    new_nick = parts[1].strip()
                    if len(new_nick) > 20:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"🏷️ Ник слишком длинный!\n"
                                    f"📏 Максимальная длина: 20 символов\n"
                                    f"📊 Ваш ник: {len(new_nick)} символов", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    # Проверка на запрещенные символы
                    forbidden = ['@', '#', '$', '%', '^', '&', '*', '(', ')', '[', ']', '{', '}']
                    if any(char in new_nick for char in forbidden):
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"🏷️ Ник содержит запрещенные символы!\n"
                                    f"🚫 Запрещены: @ # $ % ^ & * ( ) [ ] {{ }}\n\n"
                                    f"💡 Используйте только буквы, цифры и пробелы", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    update_user(user_id, peer_id, "nickname", new_nick)
                    vk.messages.send(
                        peer_id=peer_id, 
                        message=f"✅ **Ник успешно установлен!**\n\n"
                                f"🏷️ Ваш новый ник: **{new_nick}**\n\n"
                                f"💡 Теперь в профиле и командах будет отображаться этот ник\n"
                                f"❌ Для удаления ника используйте `/delnick`", 
                        random_id=random.getrandbits(64)
                    )
                
                # ❌ УДАЛЕНИЕ НИКА
                elif text == "/delnick":
                    if sender["role"] < 1:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Доступ запрещен!**\n\n"
                                    f"🔒 Для использования команды `/delnick` требуется ранг не ниже **Куратора**", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    if not sender["nickname"]:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"🏷️ У вас нет установленного ника!\n\n"
                                    f"💡 Используйте `/nick [ник]` чтобы установить ник", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    old_nick = sender["nickname"]
                    update_user(user_id, peer_id, "nickname", "")
                    vk.messages.send(
                        peer_id=peer_id, 
                        message=f"✅ **Ник успешно удален!**\n\n"
                                f"🗑️ Был удален ник: **{old_nick}**\n\n"
                                f"💡 Теперь будет отображаться ваше настоящее имя", 
                        random_id=random.getrandbits(64)
                    )
                
                # 📋 ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ
                elif text.startswith("/get"):
                    if sender["role"] < 1:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Доступ запрещен!**\n\n"
                                    f"🔒 Команда `/get` доступна только администрации чата\n"
                                    f"👑 Ваш ранг: {RANGS.get(sender['role'], '❓')}\n"
                                    f"📌 Требуется ранг: Куратор+", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg, vk, peer_id)
                    if not target_id:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"📋 **Как получить информацию о пользователе:**\n\n"
                                    f"📝 Напишите: `/get @пользователь`\n"
                                    f"📌 Пример: `/get @Иван`\n\n"
                                    f"💡 Или ответьте на сообщение пользователя командой `/get`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    target_info = get_user(target_id, peer_id)
                    is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                    is_muted = target_info["muted_until"] > int(time.time())
                    mute_left = target_info["muted_until"] - int(time.time()) if is_muted else 0
                    
                    info_text = f"""📊 **ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ**

━━━━━━━━━━━━━━━━━━━━━
👤 **Имя:** {get_display_name(target_id, peer_id)}
🆔 **ID:** {target_id}
👑 **Ранг:** {RANGS.get(target_info['role'], '❓')}
━━━━━━━━━━━━━━━━━━━━━
💰 **Баланс:** {target_info['balance']:,} монет
⚠️ **Варны:** {target_info['warns']}/3
🔇 **Мут:** {f'ДА ({format_time(mute_left)})' if is_muted else 'НЕТ'}
🚫 **Бан в группе:** {'✅ ДА' if is_banned else '❌ НЕТ'}
━━━━━━━━━━━━━━━━━━━━━
⚔️ **Статистика дуэлей:**
   • 🏆 Побед: {target_info['duels_won']}
   • 💀 Поражений: {target_info['duels_lost']}
   • 📊 Всего: {target_info['duels_won'] + target_info['duels_lost']}
━━━━━━━━━━━━━━━━━━━━━

💡 Для применения санкций используйте соответствующие команды"""
                    
                    vk.messages.send(peer_id=peer_id, message=info_text, random_id=random.getrandbits(64))
                
                # ⚔️ ДУЭЛЬ
                elif text.startswith("/duel"):
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"⚔️ **Как вызвать на дуэль:**\n\n"
                                    f"📝 Напишите: `/duel [сумма] @противник`\n"
                                    f"📌 Пример: `/duel 1000 @Иван`\n\n"
                                    f"💰 **Правила:**\n"
                                    f"• У обоих участников должна быть сумма ставки\n"
                                    f"• Победитель забирает ставку противника\n"
                                    f"• У противника есть 60 секунд на принятие командой `/accept`\n"
                                    f"• Ответьте на сообщение с вызовом для принятия дуэли", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    try:
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                        if amount > 1000000:
                            vk.messages.send(peer_id=peer_id, message=f"❌ **Максимальная ставка:** 1,000,000 монет!", random_id=random.getrandbits(64))
                            continue
                    except:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"💰 Ставка должна быть положительным числом\n"
                                    f"📌 Пример: `/duel 1000 @Иван`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    opponent_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    
                    if not opponent_id:
                        opponent_id, _ = get_target_and_reason(text, msg, vk, peer_id)
                    
                    if not opponent_id:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"👤 Не удалось определить противника\n"
                                    f"📌 Укажите @упоминание или ID пользователя\n"
                                    f"💡 Пример: `/duel 1000 @Иван`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    if opponent_id == user_id:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"⚔️ Нельзя вызвать самого себя на дуэль!\n"
                                    f"💡 Найдите другого противника", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    if sender["balance"] < amount:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Недостаточно средств!**\n\n"
                                    f"💰 Ваш баланс: {sender['balance']:,} монет\n"
                                    f"⚔️ Требуется ставка: {amount:,} монет", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    opponent = get_user(opponent_id, peer_id)
                    if opponent["balance"] < amount:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **У противника недостаточно средств!**\n\n"
                                    f"👤 {get_display_name(opponent_id, peer_id)}\n"
                                    f"💰 Баланс: {opponent['balance']:,} монет\n"
                                    f"⚔️ Требуется: {amount:,} монет", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    active_duels[user_id] = (opponent_id, amount, time.time())
                    
                    thread = threading.Thread(target=duel_timeout, args=(user_id, opponent_id, peer_id))
                    thread.daemon = True
                    thread.start()
                    
                    vk.messages.send(
                        peer_id=peer_id, 
                        message=f"⚔️ **ВЫЗОВ НА ДУЭЛЬ!** ⚔️\n\n"
                                f"👤 {get_display_name(user_id, peer_id)} вызывает {get_display_name(opponent_id, peer_id)}\n"
                                f"💰 Ставка: {amount:,} монет\n\n"
                                f"⏰ У противника есть 60 секунд, чтобы принять вызов!\n"
                                f"📝 Для принятия напишите `/accept` в ответ на это сообщение\n\n"
                                f"🎯 Победитель забирает ставку проигравшего!", 
                        random_id=random.getrandbits(64), 
                        forward_messages=msg['id']
                    )
                
                # ✅ ПРИНЯТИЕ ДУЭЛИ
                elif text == "/accept":
                    caller_id = None
                    for cid, (opp_id, amount, timestamp) in active_duels.items():
                        if opp_id == user_id:
                            caller_id = cid
                            break
                    
                    if not caller_id:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Нет активных вызовов!**\n\n"
                                    f"⚔️ Вас никто не вызывал на дуэль\n"
                                    f"💡 Чтобы вызвать противника, используйте `/duel [сумма] @противник`", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    if 'reply_message' not in msg or msg['reply_message']['from_id'] != caller_id:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Ошибка!**\n\n"
                                    f"📝 Для принятия дуэли нужно ответить на сообщение с вызовом!\n"
                                    f"💡 Найдите сообщение с вызовом и нажмите 'Ответить'", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    opponent_id, amount, timestamp = active_duels[caller_id]
                    del active_duels[caller_id]
                    
                    if time.time() - timestamp > 60:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"⏰ **Время истекло!**\n\n"
                                    f"⚔️ Вы не успели принять дуэль за 60 секунд\n"
                                    f"💡 В следующий раз отвечайте быстрее!", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    caller = get_user(caller_id, peer_id)
                    opponent = get_user(opponent_id, peer_id)
                    
                    if caller["balance"] < amount or opponent["balance"] < amount:
                        vk.messages.send(
                            peer_id=peer_id, 
                            message=f"❌ **Дуэль отменена!**\n\n"
                                    f"💰 У одного из участников изменился баланс\n"
                                    f"💡 Проверьте баланс и попробуйте снова", 
                            random_id=random.getrandbits(64)
                        )
                        continue
                    
                    # Случайный выбор победителя
                    winner_id = random.choice([caller_id, opponent_id])
                    loser_id = opponent_id if winner_id == caller_id else caller_id
                    
                    update_user(winner_id, peer_id, "balance", get_user(winner_id, peer_id)["balance"] + amount)
                    update_user(loser_id, peer_id, "balance", get_user(loser_id, peer_id)["balance"] - amount)
                    update_user(winner_id, peer_id, "duels_won", get_user(winner_id, peer_id)["duels_won"] + 1)
                    update_user(loser_id, peer_id, "duels_lost", get_user(loser_id, peer_id)["duels_lost"] + 1)
                    
                    vk.messages.send(
                        peer_id=peer_id, 
                        message=f"⚔️ **РЕЗУЛЬТАТ ДУЭЛИ!** ⚔️\n\n"
                                f"🏆 **ПОБЕДИТЕЛЬ:** {get_display_name(winner_id, peer_id)}\n"
                                f"💀 **ПРОИГРАВШИЙ:** {get_display_name(loser_id, peer_id)}\n"
                                f"💰 **ВЫИГРЫШ:** {amount:,} монет\n\n"
                                f"📊 Новая статистика:\n"
                                f"• {get_display_name(winner_id, peer_id)}: {get_user(winner_id, peer_id)['duels_won']} побед\n"
                                f"• {get_display_name(loser_id, peer_id)}: {get_user(loser_id, peer_id)['duels_lost']} поражений", 
                        random_id=random.getrandbits(64)
                    )
                
                # АДМИН-КОМАНДЫ (продолжение следует...)
                
                # Если команда не распознана
                else:
                    # Проверяем, не является ли текст кастомной командой (уже обработано в начале)
                    if not text.startswith('/'):
                        # Игнорируем обычные сообщения
                        pass
    
    except Exception as e:
        print(f"⚠️ Ошибка в основном цикле: {e}")
        time.sleep(5)
