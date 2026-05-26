import time
import random
import sqlite3
import vk_api
import os
import re
import threading
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType

# ========== НАСТРОЙКА БОТА (ВПИШИ СВОИ ДАННЫЕ) ==========
TOKEN = "vk1.a.TiQl5Wx1pwqp9z59GapC2rDuzvx4nJ3VI_ABCCDfKsx32n3SiOVc2dptRBasADuOFjfpwqPE-8codGG-RflzTBg8WG29fEsl16Umto5mroM7ccP93jV3oi_DbtKeGEDd0aDCgBr4y65wHtAEreBkpMHwV8ys0xdhbNZKQxf7HJTu8WzgMEhvbVwC5t76WNjjJEHeuBEf3JlxTb2UKOKQ_A"  # Твой токен
GROUP_ID = 237472128  # ⚠️ ЗАМЕНИ НА РЕАЛЬНЫЙ ID ГРУППЫ holdmanager
CREATOR_ID = 736337130  # ⚠️ ЗАМЕНИ НА ТВОЙ ID ВК
# =======================================================

# Словарь для активных дуэлей
active_duels = {}

# Текстовые названия рангов
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
            vk.messages.send(peer_id=peer_id, message=f"⏰ Дуэль между [id{caller_id}|игроком] и [id{opponent_id}|соперником] отменена (таймаут 60 сек)", random_id=random.getrandbits(64))
        except:
            pass

def get_display_name(user_id, peer_id):
    """Возвращает никнейм или имя пользователя"""
    user = get_user(user_id, peer_id)
    if user["nickname"]:
        return user["nickname"]
    try:
        user_info = vk.users.get(user_ids=user_id)[0]
        return f"{user_info['first_name']} {user_info['last_name']}"
    except:
        return f"id{user_id}"

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

                # ========== /STAFF (список администрации) - доступно всем ==========
                if text == "/staff":
                    conn = sqlite3.connect("hold_manager_v2.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT user_id, role FROM users WHERE peer_id = ? AND role > 0 ORDER BY role DESC", (peer_id,))
                    staff_list = cursor.fetchall()
                    conn.close()
                    
                    if not staff_list:
                        vk.messages.send(peer_id=peer_id, message="👑 Администрация в этом чате отсутствует.", random_id=random.getrandbits(64))
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
                            display_name = get_display_name(uid, peer_id)
                            staff_text += f"   • {display_name} (id{uid})\n"
                        staff_text += "\n"
                    
                    vk.messages.send(peer_id=peer_id, message=staff_text, random_id=random.getrandbits(64))

                # ========== /NICK (установка ника) - только с 1 ранга ==========
                elif text.startswith("/nick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /nick МойНик\n\nНик может содержать буквы, цифры и символы. Максимум 20 символов.", random_id=random.getrandbits(64))
                        continue
                    
                    new_nick = parts[1].strip()
                    if len(new_nick) > 20:
                        vk.messages.send(peer_id=peer_id, message="❌ Ник не может быть длиннее 20 символов!", random_id=random.getrandbits(64))
                        continue
                    
                    forbidden = ["хуй", "пидор", "гандон", "сука", "бля", "еба"]
                    blocked = False
                    for word in forbidden:
                        if word in new_nick.lower():
                            blocked = True
                            break
                    if blocked:
                        vk.messages.send(peer_id=peer_id, message="❌ Ник содержит запрещённые слова!", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(user_id, peer_id, "nickname", new_nick)
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ваш ник изменён на: {new_nick}", random_id=random.getrandbits(64))

                # ========== /DELNICK (удаление ника) - только с 1 ранга ==========
                elif text == "/delnick":
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(user_id, peer_id, "nickname", "")
                    vk.messages.send(peer_id=peer_id, message=f"✅ Ваш ник удалён! Теперь отображается настоящее имя.", random_id=random.getrandbits(64))

                # ========== ДУЭЛИ ==========
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
                        vk.messages.send(peer_id=peer_id, message="❌ Ставка должна быть положительным числом!", random_id=random.getrandbits(64))
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    opponent_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    
                    if not opponent_id:
                        opponent_id = get_target_and_reason(text, msg)[0]
                    
                    if not opponent_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите соперника: /duel 1000 @user", random_id=random.getrandbits(64))
                        continue
                    
                    if opponent_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя вызвать на дуэль самого себя!", random_id=random.getrandbits(64))
                        continue
                    
                    if sender["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Недостаточно средств! Ваш баланс: {sender['balance']:,}", random_id=random.getrandbits(64))
                        continue
                    
                    opponent = get_user(opponent_id, peer_id)
                    if opponent["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У соперника недостаточно средств! Его баланс: {opponent['balance']:,}", random_id=random.getrandbits(64))
                        continue
                    
                    active_duels[user_id] = (opponent_id, amount, time.time())
                    
                    thread = threading.Thread(target=duel_timeout, args=(user_id, opponent_id, peer_id))
                    thread.daemon = True
                    thread.start()
                    
                    opp_name = get_display_name(opponent_id, peer_id)
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ ДУЭЛЬ!\n\n{get_display_name(user_id, peer_id)} вызывает на дуэль {opp_name}!\n💰 Ставка: {amount:,} В-коинов\n\nНапиши /accept в ответ на это сообщение в течение 60 секунд!", random_id=random.getrandbits(64), forward_messages=msg['id'])

                elif text == "/accept":
                    caller_id = None
                    for cid, (opp_id, amount, timestamp) in active_duels.items():
                        if opp_id == user_id:
                            caller_id = cid
                            break
                    
                    if not caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нет активных вызовов на дуэль для вас!", random_id=random.getrandbits(64))
                        continue
                    
                    if 'reply_message' not in msg or msg['reply_message']['from_id'] != caller_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Ответьте на сообщение с вызовом на дуэль командой /accept!", random_id=random.getrandbits(64))
                        continue
                    
                    opponent_id, amount, timestamp = active_duels[caller_id]
                    del active_duels[caller_id]
                    
                    if time.time() - timestamp > 60:
                        vk.messages.send(peer_id=peer_id, message="⏰ Время на принятие дуэли истекло!", random_id=random.getrandbits(64))
                        continue
                    
                    caller = get_user(caller_id, peer_id)
                    opponent = get_user(opponent_id, peer_id)
                    
                    if caller["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У {get_display_name(caller_id, peer_id)} недостаточно средств! Дуэль отменена.", random_id=random.getrandbits(64))
                        continue
                    
                    if opponent["balance"] < amount:
                        vk.messages.send(peer_id=peer_id, message=f"❌ У вас недостаточно средств! Дуэль отменена.", random_id=random.getrandbits(64))
                        continue
                    
                    winner_id = random.choice([caller_id, opponent_id])
                    loser_id = opponent_id if winner_id == caller_id else caller_id
                    
                    update_user(winner_id, peer_id, "balance", get_user(winner_id, peer_id)["balance"] + amount)
                    update_user(loser_id, peer_id, "balance", get_user(loser_id, peer_id)["balance"] - amount)
                    
                    update_user(winner_id, peer_id, "duels_won", get_user(winner_id, peer_id)["duels_won"] + 1)
                    update_user(loser_id, peer_id, "duels_lost", get_user(loser_id, peer_id)["duels_lost"] + 1)
                    
                    winner_name = get_display_name(winner_id, peer_id)
                    
                    vk.messages.send(peer_id=peer_id, message=f"⚔️ РЕЗУЛЬТАТ ДУЭЛИ!\n\n🏆 Победитель: {winner_name}\n💰 Выигрыш: {amount:,} В-коинов!", random_id=random.getrandbits(64))

                # ========== /GET ==========
                elif text.startswith("/get"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /get @пользователь", random_id=random.getrandbits(64))
                        continue
                    
                    target_info = get_user(target_id, peer_id)
                    is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                    is_muted = target_info["muted_until"] > int(time.time())
                    
                    display_name = get_display_name(target_id, peer_id)
                    rang_name = RANGS.get(target_info["role"], "Неизвестно")
                    mute_time_left = target_info["muted_until"] - int(time.time()) if is_muted else 0
                    
                    info_text = (
                        f"📋 ИНФОРМАЦИЯ\n\n"
                        f"👤 Имя: {display_name}\n"
                        f"🆔 ID: {target_id}\n"
                        f"👑 Ранг: {rang_name}\n"
                        f"💰 Баланс: {target_info['balance']:,}\n"
                        f"⚠️ Варны: {target_info['warns']}/3\n"
                        f"🔇 Мут: {'ДА (' + format_time(mute_time_left) + ')' if is_muted else 'НЕТ'}\n"
                        f"⚔️ Дуэли: Побед {target_info['duels_won']} / Поражений {target_info['duels_lost']}\n"
                        f"🚫 Бан в группе: {'ДА' if is_banned else 'НЕТ'}"
                    )
                    vk.messages.send(peer_id=peer_id, message=info_text, random_id=random.getrandbits(64))

                # ========== /MUTE ==========
                elif text.startswith("/mute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    parts = text.split()
                    if len(parts) < 2:
                        vk.messages.send(peer_id=peer_id, message="❌ Пример: /mute 10м @user (10с, 5м, 2ч, 1д)", random_id=random.getrandbits(64))
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
                        vk.messages.send(peer_id=peer_id, message="❌ Формат времени: 10с, 5м, 2ч, 1д", random_id=random.getrandbits(64))
                        continue
                    
                    if time_seconds <= 0 or time_seconds > 86400 * 7:
                        vk.messages.send(peer_id=peer_id, message="❌ Мут от 1 секунды до 7 дней", random_id=random.getrandbits(64))
                        continue
                    
                    remaining_text = ' '.join(parts[2:])
                    target_id = get_user_id_from_text(remaining_text, vk, peer_id)
                    reason = None
                    
                    if target_id:
                        reason_match = re.search(rf'(?:\[id{target_id}\|.*?\]|@{target_id}|{target_id})\s*(.*)', remaining_text)
                        if reason_match and reason_match.group(1).strip():
                            reason = reason_match.group(1).strip()
                    else:
                        target_id, reason = get_target_and_reason(text, msg)
                    
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /mute 10м @user", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя замутить себя!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя замутить администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                    
                    mute_until = int(time.time()) + time_seconds
                    update_user(target_id, peer_id, "muted_until", mute_until)
                    
                    thread = threading.Thread(target=unmute_user_delayed, args=(target_id, peer_id, time_seconds))
                    thread.daemon = True
                    thread.start()
                    
                    reason_text = f" Причина: {reason}" if reason else ""
                    vk.messages.send(peer_id=peer_id, message=f"🔇 Пользователь получил мут на {format_time(time_seconds)}{reason_text}", random_id=random.getrandbits(64))

                # ========== /UNMUTE ==========
                elif text.startswith("/unmute"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /unmute @user", random_id=random.getrandbits(64))
                        continue
                    
                    update_user(target_id, peer_id, "muted_until", 0)
                    vk.messages.send(peer_id=peer_id, message=f"🔊 Пользователь размучен!", random_id=random.getrandbits(64))

                # ========== /WARN ==========
                elif text.startswith("/warn"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /warn @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать варн себе!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать варн администратору старшего ранга!", random_id=random.getrandbits(64))
                        continue
                        
                    new_warns = target_user["warns"] + 1
                    reason_text = f" Причина: {reason}" if reason else ""
                    
                    if new_warns >= 3:
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                            update_user(target_id, peer_id, "warns", 0)
                            vk.messages.send(peer_id=peer_id, message=f"🔴 Пользователь получил 3/3 варнов и был кикнут!{reason_text}", random_id=random.getrandbits(64))
                        except Exception as e:
                            vk.messages.send(peer_id=peer_id, message=f"⚠️ Ошибка: {e}", random_id=random.getrandbits(64))
                    else:
                        update_user(target_id, peer_id, "warns", new_warns)
                        vk.messages.send(peer_id=peer_id, message=f"⚠️ Варн выдан! {new_warns}/3{reason_text}", random_id=random.getrandbits(64))

                # ========== /KICK ==========
                elif text.startswith("/kick"):
                    if sender["role"] < 1:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Куратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /kick @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    if target_id == user_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть себя!", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя кикнуть администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                        
                    try:
                        chat_id = peer_id - 2000000000
                        vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"💥 Пользователь исключен!{reason_text}", random_id=random.getrandbits(64))
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64))

                # ========== /BAN ==========
                elif text.startswith("/ban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /ban @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя забанить администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                        
                    try:
                        vk.groups.banUser(group_id=GROUP_ID, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"⛔ Пользователь добавлен в ЧС группы!{reason_text}", random_id=random.getrandbits(64))
                        try:
                            chat_id = peer_id - 2000000000
                            vk.messages.removeChatUser(chat_id=chat_id, user_id=target_id)
                        except:
                            pass
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка: {e}", random_id=random.getrandbits(64))

                # ========== /UNBAN ==========
                elif text.startswith("/unban"):
                    if sender["role"] < 5:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Спец Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, reason = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /unban @user [причина]", random_id=random.getrandbits(64))
                        continue
                    
                    try:
                        is_banned = is_user_banned_in_group(vk, GROUP_ID, target_id)
                        
                        if not is_banned:
                            vk.messages.send(peer_id=peer_id, message=f"❌ Пользователь не находится в ЧС группы!", random_id=random.getrandbits(64))
                            continue
                        
                        vk.groups.unbanUser(group_id=GROUP_ID, user_id=target_id)
                        reason_text = f" Причина: {reason}" if reason else ""
                        vk.messages.send(peer_id=peer_id, message=f"✅ Пользователь разбанен!{reason_text}", random_id=random.getrandbits(64))
                        
                    except Exception as e:
                        vk.messages.send(peer_id=peer_id, message=f"❌ Ошибка разбана: {e}", random_id=random.getrandbits(64))

                # ========== /SETRANK ==========
                elif text.startswith("/setrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Администратор+", random_id=random.getrandbits(64))
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
                        vk.messages.send(peer_id=peer_id, message="❌ Ранг от 0 до 8", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /setrank 4 @user", random_id=random.getrandbits(64))
                        continue
                    
                    if new_role >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя выдать ранг выше своего!", random_id=random.getrandbits(64))
                        continue
                        
                    update_user(target_id, peer_id, "role", new_role)
                    rank_name = RANGS.get(new_role, "Неизвестно")
                    vk.messages.send(peer_id=peer_id, message=f"👑 Выдан ранг: {rank_name}", random_id=random.getrandbits(64))

                # ========== /UNRANK ==========
                elif text.startswith("/unrank"):
                    if sender["role"] < 4:
                        vk.messages.send(peer_id=peer_id, message="❌ Доступно с ранга Главный Администратор+", random_id=random.getrandbits(64))
                        continue
                    
                    target_id, _ = get_target_and_reason(text, msg)
                    if not target_id:
                        vk.messages.send(peer_id=peer_id, message="❌ Укажите пользователя: /unrank @user", random_id=random.getrandbits(64))
                        continue
                    
                    target_user = get_user(target_id, peer_id)
                    if target_user["role"] >= sender["role"] and target_id != CREATOR_ID:
                        vk.messages.send(peer_id=peer_id, message="❌ Нельзя снять ранг у администратора старшего ранга!", random_id=random.getrandbits(64))
                        continue
                        
                    update_user(target_id, peer_id, "role", 0)
                    vk.messages.send(peer_id=peer_id, message=f"❌ Ранг снят!", random_id=random.getrandbits(64))

                # ========== ИГРОВЫЕ КОМАНДЫ ==========
                elif text in ["помощь", "/help", "команды"]:
                    help_text = (
                        "⚙️ ИГРОВОЕ МЕНЮ:\n"
                        "📊 Профиль — статистика\n"
                        "💼 Работать — заработать\n"
                        "🎰 Казино 500 — играть\n"
                        "⚔️ /duel 1000 @user — вызвать на дуэль\n"
                        "🏷 /nick [ник] — установить ник (с 1 ранга)\n"
                        "❌ /delnick — удалить ник (с 1 ранга)\n\n"
                        "👑 ИНФО:\n"
                        "📋 /staff — список администрации\n\n"
                        "🔨 АДМИН КОМАНДЫ:\n"
                        "📋 /get @user\n"
                        "🔇 /mute 10м @user [причина]\n"
                        "🔊 /unmute @user\n"
                        "⚠️ /warn @user [причина]\n"
                        "💥 /kick @user [причина] (с 1 ранга)\n"
                        "⛔ /ban @user [причина] (с 5 ранга)\n"
                        "🔓 /unban @user [причина] (с 5 ранга)\n"
                        "👑 /setrank 4 @user (с 4 ранга)\n"
                        "❌ /unrank @user (с 4 ранга)\n\n"
                        "⏱ Время: 10с, 5м, 2ч, 1д\n"
                        "💡 Причина необязательна"
                    )
                    vk.messages.send(peer_id=peer_id, message=help_text, random_id=random.getrandbits(64))
                    
                elif text in ["профиль", "/stats"]:
                    user = get_user(user_id, peer_id)
                    is_muted = user["muted_until"] > int(time.time())
                    mute_left = user["muted_until"] - int(time.time()) if is_muted else 0
                    rang_name = RANGS.get(user["role"], "Неизвестно")
                    display_name = get_display_name(user_id, peer_id)
                    profile_text = (
                        f"👤 ПРОФИЛЬ\n\n"
                        f"🏷 Ник: {display_name}\n"
                        f"👑 Должность: {rang_name}\n"
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
