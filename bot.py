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

RANGS = {
    0: "Обычный Пользователь",
    1: "Куратор администрации",
    2: "Технический Специалист",
    3: "Зам. Главного Администратора",
    4: "Главный Администратор",
    5: "Спец Администратор",
    6: "Руководитель Проекта",
    7: "Заместитель Владельца",
    8: "Владелец"
}

def init_db():
    conn = sqlite3.connect("hold_manager_v2.db")
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
    conn.commit()
    conn.close()

def get_user(user_id, peer_id):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT balance, warns, job_cooldown, role, muted_until, duels_won, duels_lost, nickname FROM users WHERE user_id = ? AND peer_id = ?", (user_id, peer_id))
    row = cursor.fetchone()
    
    if not row:
        initial_role = 8 if user_id == CREATOR_ID else 0
        cursor.execute("INSERT INTO users (user_id, peer_id, role) VALUES (?, ?, ?)", (user_id, peer_id, initial_role))
        conn.commit()
        row = (5000, 0, 0, initial_role, 0, 0, 0, "")
        
    conn.close()
    return {"balance": row[0], "warns": row[1], "job_cooldown": row[2], "role": row[3], "muted_until": row[4], "duels_won": row[5], "duels_lost": row[6], "nickname": row[7]}

def update_user(user_id, peer_id, field, value):
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ? AND peer_id = ?", (value, user_id, peer_id))
    conn.commit()
    conn.close()

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
        vk.messages.send(peer_id=peer_id, message=f"🔊 [id{user_id}|Пользователь] размучен!", random_id=random.getrandbits(64))
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
    """Возвращает список всех связанных бесед"""
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT peer_id FROM linked_chats")
    chats = [row[0] for row in cursor.fetchall()]
    conn.close()
    return chats

def add_linked_chat(peer_id, linked_by):
    """Добавляет беседу в сеть"""
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO linked_chats (peer_id, linked_by, linked_at) VALUES (?, ?, ?)", 
                   (peer_id, linked_by, int(time.time())))
    conn.commit()
    conn.close()

def remove_linked_chat(peer_id):
    """Удаляет беседу из сети"""
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM linked_chats WHERE peer_id = ?", (peer_id,))
    conn.commit()
    conn.close()

def is_chat_linked(peer_id):
    """Проверяет, связана ли беседа"""
    conn = sqlite3.connect("hold_manager_v2.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM linked_chats WHERE peer_id = ?", (peer_id,))
    result = cursor.fetchone() is not None
    conn.close()
    return result

def get_chat_name(peer_id):
    """Получает название беседы"""
    try:
        chat_id = peer_id - 2000000000
        chat_info = vk.messages.getChat(chat_id=chat_id)
        return chat_info['title']
    except:
        return f"Беседа {peer_id}"

init_db()

try:
    vk_session = vk_api.VkApi(token=TOKEN)
    vk = vk_session.get_api()
    group_info = vk.groups.getById()
    if group_info:
        print(f"✅ Бот подключен к: {group_info[0]['name']}")
    else:
        print("❌ Ошибка подключения")
        exit()
except Exception as e:
    print(f"❌ Ошибка: {e}")
    exit()

longpoll = VkBotLongPoll(vk_session, GROUP_ID)
print("🚀 Бот успешно запущен!")

while True:
    try:
        for event in longpoll.listen():
            if event.type == VkBotEventType.MESSAGE_NEW:
                msg = event.obj.message if 'message' in event.obj else event.obj
                text = msg['text'].lower().strip()
                peer_id = msg['peer_id']
                user_id = msg['from_id']
                
                if user_id < 0:
                    continue

                sender = get_user(user_id, peer_id)
                
                current_time = int(time.time())
                if sender["muted_until"] > current_time and sender["role"] < 1:
                    remaining = sender["muted_until"] - current_time
                    vk.messages.send(peer_id=peer_id, message=f"🔇 Вы в муте! Осталось: {format_time(remaining)}", random_id=random.getrandbits(64))
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

                # ========== КОМАНДЫ СВЯЗКИ БЕСЕД ==========
                # /linkchat - добавить текущую беседу в сеть (только владелец)
                if text == "/linkchat":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может связывать беседы!", random_id=random.getrandbits(64))
                        continue
                    
                    if is_chat_linked(peer_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Эта беседа уже связана!", random_id=random.getrandbits(64))
                        continue
                    
                    add_linked_chat(peer_id, user_id)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Беседа добавлена в глобальную сеть!\nТеперь команды /gban, /gunban, /gkick будут действовать во всех связанных беседах.", random_id=random.getrandbits(64))

                # /unlinkchat - удалить беседу из сети (только владелец)
                elif text == "/unlinkchat":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может отвязывать беседы!", random_id=random.getrandbits(64))
                        continue
                    
                    if not is_chat_linked(peer_id):
                        vk.messages.send(peer_id=peer_id, message="❌ Эта беседа не связана!", random_id=random.getrandbits(64))
                        continue
                    
                    remove_linked_chat(peer_id)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Беседа удалена из глобальной сети!", random_id=random.getrandbits(64))

                # /chats - показать все связанные беседы
                elif text == "/chats":
                    if sender["role"] < 8:
                        vk.messages.send(peer_id=peer_id, message="❌ Только Владелец может смотреть список бесед!", random_id=random.getrandbits(64))
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="📋 Нет связанных бесед.\n\nИспользуй /linkchat в беседе, чтобы добавить её в сеть.", random_id=random.getrandbits(64))
                        continue
                    
                    chat_list = "🌐 **СВЯЗАННЫЕ БЕСЕДЫ**\n\n"
                    for chat_peer in chats:
                        chat_name = get_chat_name(chat_peer)
                        chat_list += f"• {chat_name}\n"
                    
                    vk.messages.send(peer_id=peer_id, message=chat_list, random_id=random.getrandbits(64))

                # /gban - глобальный бан во всех связанных беседах (с 5 ранга)
                elif text.startswith("/gban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /gban @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя забанить администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед! Сначала добавь беседы через /linkchat", random_id=random.getrandbits(64))
                        continue
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    banned_count = 0
                    
                    for chat_peer in chats:
                        try:
                            vk.groups.banUser(group_id=GROUP_ID, user_id=target_id)
                            banned_count += 1
                            # Попытка кикнуть из беседы
                            try:
                                chat_id = chat_peer - 2000000000
                                vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            except:
                                pass
                        except:
                            pass
                    
                    vk.messages.send(peer_id=peer_id, message=f"⛔ **ГЛОБАЛЬНЫЙ БАН!**\n\nПользователь {get_display_name(target_id, peer_id)} забанен в {banned_count} беседах{reason_text}", random_id=random.getrandbits(64))

                # /gunban - глобальный разбан во всех связанных беседах (с 5 ранга)
                elif text.startswith("/gunban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /gunban @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64))
                        continue
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    unbanned_count = 0
                    
                    for chat_peer in chats:
                        try:
                            vk.groups.unbanUser(group_id=GROUP_ID, user_id=target_id)
                            unbanned_count += 1
                        except:
                            pass
                    
                    vk.messages.send(peer_id=peer_id, message=f"🔓 **ГЛОБАЛЬНЫЙ РАЗБАН!**\n\nПользователь {get_display_name(target_id, peer_id)} разбанен в {unbanned_count} беседах{reason_text}", random_id=random.getrandbits(64))

                # /gkick - глобальный кик из всех связанных бесед (с 3 ранга)
                elif text.startswith("/gkick"):
                    if sender["role"] < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Зам. ГА+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /gkick @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                    
                    chats = get_linked_chats()
                    if not chats:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет связанных бесед!", random_id=random.getrandbits(64))
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
                    
                    vk.messages.send(peer_id=peer_id, message=f"💥 **ГЛОБАЛЬНЫЙ КИК!**\n\nПользователь {get_display_name(target_id, peer_id)} исключён из {kicked_count} бесед{reason_text}", random_id=random.getrandbits(64))

                # ========== ОСТАЛЬНЫЕ КОМАНДЫ ==========
                # /staff
                elif text == "/staff":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, role FROM users WHERE peer_id = ? AND role > 0 ORDER BY role DESC", (peer_id,))
                    staff_list = cursor.fetchall()
                    conn.close()
                    
                    if not staff_list:
                        vk.messages.send(peer_id=peer_id, message="👑 Администрация отсутствует.", random_id=random.getrandbits(64))
                        continue
                    
                    staff_by_role = {}
                    for uid, role in staff_list:
                        if role not in staff_by_role:
                            staff_by_role[role] = []
                        staff_by_role[role].append(uid)
                    
                    staff_text = "👑 АДМИНИСТРАЦИЯ ЧАТА\n\n"
                    for role in sorted(staff_by_role.keys(), reverse=True):
                        role_name = RANGS.get(role, f"Ранг {role}")
                        staff_text += f"⭐ {role_name}:\n"
                        for uid in staff_by_role[role]:
                            staff_text += f"   • {get_display_name(uid, peer_id)}\n"
                        staff_text += "\n"
                    
                    vk.messages.send(peer_id=peer_id, message=staff_text, random_id=random.getrandbits(64))

                # /nick
                elif text.startswith("/nick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /nick МойНик", random_id=random.getrandbits(64))
                        continue
                    
                    new_nick = parts[1].strip()
                    if len(new_nick) > 20:
                        vk.messages.send(peer_id=peer_id, message="❌ Ник не длиннее 20 символов!", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(user_id, peer_id, "nickname", new_nick)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ваш ник: {new_nick}", random_id=random.getrandbits(64))

                # /delnick
                elif text == "/delnick":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(user_id, peer_id, "nickname", "")
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ник удалён!", random_id=random.getrandbits(64))

                # /duel
                elif text.startswith("/duel"):
                    parts = text.split()
                    if len(parts) < 3:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /duel 1000 @user", random_id=random.getrandbits(64))
                        continue
                    
                    try:
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Ставка - число!", random_id=random.getrandbits(64))
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    opponent_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    
                    if not opponent_id:
                        opponent_id = get_target_and_reason(text, msg)[0]
                    
                    if not opponent_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите соперника", random_id=random.getrandbits(64))
                        continue
                    
                    if opponent_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя с самим собой!", random_id=random.getrandbits(64))
                        continue
                    
                    if sender["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Нет денег! Баланс: {sender['balance']:,}", random_id=random.getrandbits(64))
                        continue
                    
                    opponent = get_user(opponent_id, peer_id)
                    if opponent["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У соперника нет {amount:,} монет!", random_id=random.getrandbits(64))
                        continue
                    
                    active_duels[user_id] = (opponent_id, amount, time.time())
                    
                    thread = threading.Thread(target=duel_timeout, args=(user_id, opponent_id, peer_id))
                    thread.daemon = True
                    thread.start()
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ ДУЭЛЬ!\n\n{get_display_name(user_id, peer_id)} вызывает {get_display_name(opponent_id, peer_id)}!\n💰 Ставка: {amount:,}\n\nНапиши /accept в ответ за 60 сек!", random_id=random.getrandbits(64), forward_messages=msg['id'])

                elif text == "/accept":
                    caller_id = None
                    for cid, (opp_id, amount, timestamp) in active_duels.items():
                        if opp_id == user_id:
                            caller_id = cid
                            break
                    
                    if not caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет вызовов для вас!", random_id=random.getrandbits(64))
                        continue
                    
                    if 'reply_message' not in msg or msg['reply_message']['from_id'] != caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Ответьте на сообщение с вызовом!", random_id=random.getrandbits(64))
                        continue
                    
                    opponent_id, amount, timestamp = active_duels[caller_id]
                    del active_duels[caller_id]
                    
                    if time.time() - timestamp > 60:
                        vk.messages.send(peer_id=peer_id, message="⏰ Время истекло!", random_id=random.getrandbits(64))
                        continue
                    
                    caller = get_user(caller_id, peer_id)
                    opponent = get_user(opponent_id, peer_id)
                    
                    if caller["balance"] < amount or opponent["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У кого-то нет денег! Дуэль отменена.", random_id=random.getrandbits(64))
                        continue
                    
                    winner_id = random.choice([caller_id, opponent_id])
                    loser_id = opponent_id if winner_id == caller_id else caller_id
                    
                    update_user(winner_id, peer_id, "balance", get_user(winner_id, peer_id)["balance"] + amount)
                    update_user(loser_id, peer_id, "balance", get_user(loser_id, peer_id)["balance"] - amount)
                    
                    update_user(winner_id, peer_id, "duels_won", get_user(winner_id, peer_id)["duels_won"] + 1)
                    update_user(loser_id, peer_id, "duels_lost", get_user(loser_id, peer_id)["duels_lost"] + 1)
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ ПОБЕДИТЕЛЬ: {get_display_name(winner_id, peer_id)}!\n💰 Выигрыш: {amount:,} монет!", random_id=random.getrandbits(64))

                # /get
                elif text.startswith("/get"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /get @user", random_id=random.getrandbits(64))
                        continue
                    
                    target_info = get_user(target_id, peer_id)
                    is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                    is_muted = target_info["muted_until"] > int(time.time())
                    mute_left = target_info["muted_until"] - int(time.time()) if is_muted else 0
                    
                    info_text = (
                        f"👤 {get_display_name(target_id, peer_id)}\n"
                        f"🆔 ID: {target_id}\n"
                        f"👑 Ранг: {RANGS.get(target_info['role'], '?')}\n"
                        f"💰 Баланс: {target_info['balance']:,}\n"
                        f"⚠️ Варны: {target_info['warns']}/3\n"
                        f"🔇 Мут: {'ДА (' + format_time(mute_left) + ')' if is_muted else 'НЕТ'}\n"
                        f"⚔️ Дуэли: Побед {target_info['duels_won']} / Поражений {target_info['duels_lost']}\n"
                        f"🚫 Бан: {'ДА' if is_banned else 'НЕТ'}"
                    )
                    vk.messages.send(peer_id=peer_id, message=info_text, random_id=random.getrandbits(64))

                # /mute
                elif text.startswith("/mute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /mute 10м @user", random_id=random.getrandbits(64))
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
                        vk.messages.send(peer_id=peer_id, message="❌ Формат: 10с, 5м, 2ч, 1д", random_id=random.getrandbits(64))
                        continue
                    
                    if time_seconds <= 0 or time_seconds > 86400 * 7:
                        vk.messages.send(peer_id=peer_id, message="❌ Мут от 1 сек до 7 дней", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себя!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", int(time.time()) + time_seconds)
                    
                    thread = threading.Thread(target=unmute_user_delayed, args=(target_id, peer_id, time_seconds))
                    thread.daemon = True
                    thread.start()
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    vk.messages.send(peer_id=peer_id, message=f"🔇 Мут на {format_time(time_seconds)}{reason_text}", random_id=random.getrandbits(64))

                # /unmute
                elif text.startswith("/unmute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", 0)
                    vk.messages.send(peer_id=peer_id, message=f"🔊 Размучен!", random_id=random.getrandbits(64))

                # /warn
                elif text.startswith("/warn"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себе!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64))
                        continue
                        
                    new_warns = target_user["warns"] + 1
                    reason_text = f" Причина: {reason}" if reason else ""
                    
                    if new_warns >= 3:
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            update_user(target_id, peer_id, "warns", 0)
                            vk.messages.send(peer_id=peer_id, message=f"🔴 3/3 варнов - кикнут!{reason_text}", random_id=random.getrandbits(64))
                        except:
                            vk.messages.send(peer_id=peer_id, message=f"⚠️ 3/3 варнов, но кикнуть не могу (дайте права админа)", random_id=random.getrandbits(64))
                    else:
                        update_user(target_id, peer_id, "warns", new_warns)
                        vk.messages.send(peer_id=peer_id, message=f"⚠️ Варн {new_warns}/3{reason_text}", random_id=random.getrandbits(64))

                # /kick
                elif text.startswith("/kick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя себя!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64))
                        continue
                        
                    try:
                        chat_id = peer_id - 2000000000
                        vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"💥 Кикнут{reason_text}", random_id=random.getrandbits(64))
                    except:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: дайте боту права админа", random_id=random.getrandbits(64))

                # /ban
                elif text.startswith("/ban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64))
                        continue
                        
                    try:
                        vk.groups.banUser(group_id=GROUP_ID, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"⛔ Пользователь в ЧС группы!{reason_text}", random_id=random.getrandbits(64))
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        except:
                            pass
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64))

                # /unban
                elif text.startswith("/unban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    try:
                        is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                        if not is_banned:
                            vk.messages.send(peer_id=peer_id, message=f"❌ Пользователь не в ЧС!", random_id=random.getrandbits(64))
                            continue
                        
                        vk.groups.unbanUser(group_id=GROUP_ID, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"✅ Разбанен!{reason_text}", random_id=random.getrandbits(64))
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64))

                # /setrank
                elif text.startswith("/setrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ ГА+", random_id=random.getrandbits(64))
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /setrank 4 @user", random_id=random.getrandbits(64))
                        continue
                    
                    try:
                        new_role = int(parts[1])
                        if new_role < 0 or new_role > 8:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="❌ Ранг 0-8", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    if new_role >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выше себя!", random_id=random.getrandbits(64))
                        continue
                        
                    update_user(target_id, peer_id, "role", new_role)
                    vk.messages.send(peer_id=peer_id, message=f"👑 Выдан ранг: {RANGS.get(new_role, '?')}", random_id=random.getrandbits(64))

                # /unrank
                elif text.startswith("/unrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ ГА+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Старший ранг!", random_id=random.getrandbits(64))
                        continue
                        
                    update_user(target_id, peer_id, "role", 0)
                    vk.messages.send(peer_id=peer_id, message=f"❌ Ранг снят!", random_id=random.getrandbits(64))

                # Игровые команды
                elif text in ["помощь", "/help", "команды"]:
                    help_text = (
                        "⚙️ ИГРОВЫЕ КОМАНДЫ:\n"
                        "📊 Профиль\n💼 Работать\n🎰 Казино 500\n⚔️ /duel 1000 @user\n"
                        "🏷 /nick [ник] (с 1 ранга)\n❌ /delnick (с 1 ранга)\n\n"
                        "👑 ИНФО:\n📋 /staff\n\n"
                        "🔨 АДМИН:\n📋 /get @user\n🔇 /mute 10м @user [причина]\n🔊 /unmute @user\n"
                        "⚠️ /warn @user [причина]\n💥 /kick @user [причина] (с 1 ранга)\n"
                        "⛔ /ban @user [причина] (с 5 ранга)\n🔓 /unban @user [причина] (с 5 ранга)\n"
                        "👑 /setrank 4 @user (с 4 ранга)\n❌ /unrank @user (с 4 ранга)\n\n"
                        "🌐 **ГЛОБАЛЬНЫЕ КОМАНДЫ (связка бесед):**\n"
                        "🔗 /linkchat — добавить беседу в сеть (Владелец)\n"
                        "🔓 /unlinkchat — удалить беседу из сети (Владелец)\n"
                        "📋 /chats — список связанных бесед (Владелец)\n"
                        "⛔ /gban @user [причина] — бан во всех беседах (с 5 ранга)\n"
                        "🔓 /gunban @user [причина] — разбан во всех беседах (с 5 ранга)\n"
                        "💥 /gkick @user [причина] — кик из всех бесед (с 3 ранга)\n\n"
                        "⏱ Формат времени: 10с, 5м, 2ч, 1д\n💡 Причина необязательна"
                    )
                    vk.messages.send(peer_id=peer_id, message=help_text, random_id=random.getrandbits(64))
                    
                elif text in ["профиль", "/stats"]:
                    user = get_user(user_id, peer_id)
                    is_muted = user["muted_until"] > int(time.time())
                    mute_left = user["muted_until"] - int(time.time()) if is_muted else 0
                    profile_text = (
                        f"👤 {get_display_name(user_id, peer_id)}\n"
                        f"👑 {RANGS.get(user['role'], '?')}\n"
                        f"💰 Баланс: {user['balance']:,}\n"
                        f"⚠️ Варны: {user['warns']}/3\n"
                        f"🔇 Мут: {'ДА (' + format_time(mute_left) + ')' if is_muted else 'НЕТ'}\n"
                        f"⚔️ Дуэли: Побед {user['duels_won']} / Поражений {user['duels_lost']}"
                    )
                    vk.messages.send(peer_id=peer_id, message=profile_text, random_id=random.getrandbits(64))
                    
                elif text in ["работать", "работа"]:
                    user = get_user(user_id, peer_id)
                    current_time = int(time.time())
                    if current_time < user["job_cooldown"]:
                        left = user["job_cooldown"] - current_time
                        vk.messages.send(peer_id=peer_id, message=f"⏳ Отдых {format_time(left)}", random_id=random.getrandbits(64))
                    else:
                        salary = random.randint(300, 1500)
                        new_balance = user["balance"] + salary
                        update_user(user_id, peer_id, "balance", new_balance)
                        update_user(user_id, peer_id, "job_cooldown", current_time + 600)
                        vk.messages.send(peer_id=peer_id, message=f"💼 +{salary}! Баланс: {new_balance:,}", random_id=random.getrandbits(64))
                        
                elif text.startswith("казино"):
                    try:
                        parts = text.split()
                        if len(parts) < 2:
                            raise ValueError
                        amount = int(parts[1])
                        if amount <= 0:
                            raise ValueError
                    except:
                        vk.messages.send(peer_id=peer_id, message="🎰 Пример: казино 500", random_id=random.getrandbits(64))
                        continue
                        
                    user = get_user(user_id, peer_id)
                    if user["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет денег!", random_id=random.getrandbits(64))
                        continue
                        
                    if random.choice([True, False]):
                        new_balance = user["balance"] + amount
                        update_user(user_id, peer_id, "balance", new_balance)
                        vk.messages.send(peer_id=peer_id, message=f"🎉 +{amount:,}! Баланс: {new_balance:,}", random_id=random.getrandbits(64))
                    else:
                        new_balance = user["balance"] - amount
                        update_user(user_id, peer_id, "balance", new_balance)
                        vk.messages.send(peer_id=peer_id, message=f"📉 -{amount:,}! Баланс: {new_balance:,}", random_id=random.getrandbits(64))

    except Exception as e:
        print(f"Ошибка: {e}")
        time.sleep(5)
