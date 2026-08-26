import os
import json
import time
import threading
import uuid
import sqlite3
import base64
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

# --- VARIABILI D'AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

DB_PATH = "falsario.db"
MEDIA_DIR = "/var/www/html/media"
os.makedirs(MEDIA_DIR, exist_ok=True)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# ======================================================
# DATABASE LOCALE PRIVATO (No Supabase)
# ======================================================
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, points INTEGER DEFAULT 50)''')
    c.execute('''CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, description TEXT, price_options TEXT, media_list TEXT, media_url TEXT, media_type TEXT, in_showcase INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, items TEXT, total_price REAL, address TEXT, status TEXT, order_type TEXT, tracking_code TEXT, user_message_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

init_db()

def db_register_user(user_id, username):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (telegram_id, username, points) VALUES (?, ?, ?)", (user_id, username or "Anonimo", 50))
    conn.commit()
    conn.close()

def db_add_product(product_data):
    try:
        conn = get_db()
        conn.execute('''INSERT INTO products (name, category, description, price_options, media_list, media_url, media_type, in_showcase) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                     (product_data.get('name'), product_data.get('category'), product_data.get('description'), 
                      json.dumps(product_data.get('price_options', [])), json.dumps(product_data.get('media_list', [])), 
                      product_data.get('media_url'), product_data.get('media_type'), 1 if product_data.get('in_showcase', True) else 0))
        conn.commit()
        conn.close()
        return True, "OK"
    except Exception as e: return False, str(e)

def db_update_product(prod_id, update_data):
    try:
        conn = get_db()
        for key, val in update_data.items():
            if isinstance(val, list) or isinstance(val, dict): val = json.dumps(val)
            elif isinstance(val, bool): val = 1 if val else 0
            conn.execute(f"UPDATE products SET {key} = ? WHERE id = ?", (val, prod_id))
        conn.commit()
        conn.close()
        return True
    except: return False

def db_get_products():
    conn = get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_toggle_product(prod_id, current_status):
    return db_update_product(prod_id, {"in_showcase": not current_status})

def db_delete_product(prod_id):
    conn = get_db()
    conn.execute("DELETE FROM products WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    return True

def db_update_user_points(target_id, points_delta):
    conn = get_db()
    row = conn.execute("SELECT points FROM users WHERE telegram_id = ?", (target_id,)).fetchone()
    if row:
        new_p = max(0, row['points'] + points_delta)
        conn.execute("UPDATE users SET points = ? WHERE telegram_id = ?", (new_p, target_id))
        conn.commit()
        conn.close()
        return True, new_p
    conn.close()
    return False, 0

def db_save_order(user_id, username, cart, total, address, order_type):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO orders (user_id, username, items, total_price, address, status, order_type) 
                 VALUES (?, ?, ?, ?, ?, ?, ?)''', 
              (user_id, username, json.dumps(cart), total, address, "PENDING", order_type))
    order_id = c.lastrowid
    conn.commit()
    conn.close()
    return order_id

def db_update_order_msg_id(order_id, msg_id):
    conn = get_db()
    conn.execute("UPDATE orders SET user_message_id = ? WHERE id = ?", (msg_id, order_id))
    conn.commit()
    conn.close()

def db_get_all_orders():
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_update_order_status(order_id, status, tracking=""):
    conn = get_db()
    if tracking: conn.execute("UPDATE orders SET status = ?, tracking_code = ? WHERE id = ?", (status, tracking, order_id))
    else: conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()

# --- UPLOAD IMMAGINI/VIDEO LOCALE ---
def upload_to_local_storage(file_bytes, mime_type, file_extension):
    try:
        filename = f"media_{int(time.time())}_{uuid.uuid4().hex[:6]}.{file_extension}"
        filepath = os.path.join(MEDIA_DIR, filename)
        with open(filepath, 'wb') as f:
            f.write(file_bytes)
        public_url = f"{WEB_APP_URL}/media/{filename}"
        return public_url, "OK"
    except Exception as e:
        return None, str(e)


# ======================================================
# API LOCALI E WEBHOOK
# ======================================================
class WebhookAPIHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        
        if '/api/products' in self.path:
            all_prods = db_get_products()
            showcase_prods = [p for p in all_prods if p.get('in_showcase', 1) == 1]
            self.wfile.write(json.dumps(showcase_prods).encode('utf-8'))
            
        elif self.path.startswith('/api/order/'):
            order_id = self.path.split('/')[-1]
            orders = db_get_all_orders()
            order = next((o for o in orders if str(o.get('id')) == str(order_id) or str(o.get('tracking_code')) == str(order_id)), None)
            if order: self.wfile.write(json.dumps(order).encode('utf-8'))
            else: self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))
            
        elif self.path.startswith('/api/user/'):
            user_id = self.path.split('/')[-1]
            conn = get_db()
            row = conn.execute("SELECT points FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            conn.close()
            if row: self.wfile.write(json.dumps({"points": row['points']}).encode('utf-8'))
            else: self.wfile.write(json.dumps({"points": 50}).encode('utf-8'))
        else:
            self.wfile.write(b'{"status": "ok"}')

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            return
            
        post_data = self.rfile.read(content_length).decode('utf-8')
        try: data = json.loads(post_data)
        except: data = {}

        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        if self.path == '/api/upload':
            b64_str = data.get("data", "")
            if "," in b64_str: b64_str = b64_str.split(",")[1]
            try:
                img_data = base64.b64decode(b64_str)
                filename = f"receipt_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg"
                filepath = os.path.join(MEDIA_DIR, filename)
                with open(filepath, 'wb') as f: f.write(img_data)
                public_url = f"{WEB_APP_URL}/media/{filename}"
                self.wfile.write(json.dumps({"url": public_url}).encode('utf-8'))
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/order':
            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            address = data.get("address", "Non specificato")

            is_service = any("serviz" in str(i.get('category', '')).lower() for i in cart)
            order_type = "SERVICE" if is_service else "PHYSICAL"

            order_id = db_save_order(user_id, username, cart, total, address, order_type)
            items_text = "\n".join([f"• {i['qty']}x {i['name']} - \u20ac{i['price']}" for i in cart])

            user_msg = (
                f"✅ <b>Richiesta #{order_id} Inviata!</b>\n\n"
                f"📦 <b>Riepilogo:</b>\n{items_text}\n\n"
                f"📍 <b>Dati Recapito/Info:</b>\n{address}\n\n"
                f"💰 <b>Totale:</b> \u20ac{total}\n\n"
                f"⏳ <i>Un operatore sta elaborando la tua richiesta. Riceverai aggiornamenti live su questo messaggio.</i>"
            )
            
            if user_id and str(user_id) != "0":
                try:
                    sent = bot.send_message(int(user_id), user_msg, parse_mode="HTML")
                    db_update_order_msg_id(order_id, sent.message_id) 
                except: pass

            if ADMIN_ID and ADMIN_ID != 0:
                try:
                    alert_type = "🛠 NUOVO SERVIZIO" if is_service else "📦 NUOVO ORDINE FISICO"
                    bot.send_message(ADMIN_ID, f"🔔 <b>{alert_type} RICEVUTO!</b>\nRif. #{order_id} da @{username}.\n👉 Apri /admin per gestirlo.", parse_mode="HTML")
                except: pass

            self.wfile.write(json.dumps({"success": True, "order_id": order_id}).encode('utf-8'))

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), WebhookAPIHandler)
    server.serve_forever()

# ======================================================
# TASTIERE GESTIONALI ADMIN
# ======================================================
def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Gestione Ordini (Fisici)", callback_data="dash_ord_phys"),
        types.InlineKeyboardButton("🛠 Gestione Servizi (Digitali)", callback_data="dash_ord_serv"),
        types.InlineKeyboardButton("🛍 Gestione Prodotti & Media", callback_data="m_prod"),
        types.InlineKeyboardButton("📜 Storico Archivi", callback_data="m_hist"),
        types.InlineKeyboardButton("💎 Gestione Punti Utenti", callback_data="m_pts")
    )
    return markup

def get_admin_hist_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✅ Ordini Completati / Spediti", callback_data="hist_shipped"),
        types.InlineKeyboardButton("❌ Ordini Annullati", callback_data="hist_cancelled"),
        types.InlineKeyboardButton("🔙 Torna alla Dashboard", callback_data="m_main")
    )
    return markup

def get_admin_prod_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Aggiungi Prodotto", callback_data="p_add"),
        types.InlineKeyboardButton("📋 Lista / Modifica / Elimina", callback_data="p_list"),
        types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main")
    )
    return markup

def get_cancel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Torna Indietro", callback_data="m_main"))
    return markup

def get_media_done_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✅ Fine Caricamento", callback_data="done_media"),
        types.InlineKeyboardButton("🔙 Annulla", callback_data="m_main")
    )
    return markup

# ======================================================
# COMANDI BASE
# ======================================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    username = message.from_user.username
    threading.Thread(target=db_register_user, args=(user_id, username), daemon=True).start()

    welcome_text = (
        "Benvenuto nello shop ufficiale del Falsario 🤗🎭\n\n"
        "💬 Contatto Telegram Ufficiale: @il_falsario_ufficiale_x2\n"
        "📲 Contatto Signal Ufficiale: https://signal.me/#eu/m7lTtwu9GCr8RJQ7mhQ2OkwVfT_MZvjG6g-PFCnS8dG9NBl3s09GYKPtiyRQz-ih\n"
        "📲 Contatto Session Ufficiale: 05495e45a9c1ced74358dcedaad80c99956e1405fbbccf4f8e85f0ca873946a515\n\n"
        "📢 Canale Feedback: https://t.me/+eRPnJSZq485kMzdk\n\n"
        "Massima serietà, discrezione totale e qualità impeccabile.\n"
        "👇 Clicca in basso per accedere al caveau."
    )
    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL: markup.add(types.InlineKeyboardButton("🏦 Accedi al Caveau 🏦", web_app=types.WebAppInfo(WEB_APP_URL)))
    bot.send_message(user_id, welcome_text, reply_markup=markup, disable_web_page_preview=True)

@bot.message_handler(commands=['admin', 'cancel', 'menu'])
def admin_panel(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    user_states.pop(user_id, None)
    bot.send_message(user_id, "⚙️ <b>PANNELLO GESTIONALE CAVEAU</b> 🎭\n\nScegli la sezione da gestire:", parse_mode="HTML", reply_markup=get_admin_main_keyboard())

# ======================================================
# MOTORE CENTRALE: ROUTING PULSANTI INLINE
# ======================================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID: return
    data = call.data

    if data == "m_main":
        user_states.pop(user_id, None)
        bot.edit_message_text("⚙️ <b>PANNELLO GESTIONALE CAVEAU</b>", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_main_keyboard())

    elif data == "dash_ord_phys":
        user_states.pop(user_id, None)
        orders = [o for o in db_get_all_orders() if o.get('status') in ['PENDING', 'ACCEPTED'] and o.get('order_type') != 'SERVICE']
        
        bot.edit_message_text("📦 <b>ORDINI FISICI IN GESTIONE</b>", user_id, call.message.message_id, parse_mode="HTML")
        if not orders:
            bot.send_message(user_id, "✅ Nessun ordine fisico in attesa.", reply_markup=get_cancel_keyboard())
            return
            
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - \u20ac{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            st_text = "⏳ Da Confermare" if o.get('status') == 'PENDING' else "✅ In Preparazione"
            address_str = str(o.get('address', 'N/D'))
            
            is_meetup = "meet" in address_str.lower() or "mano" in address_str.lower()

            msg = (
                f"🛒 <b>ORDINE #{o.get('id')}</b> [{st_text}]\n"
                f"👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 Recapito/Metodo: {address_str}\n\n"
                f"📦 Prodotti:\n{items_str}\n\n"
                f"💰 Totale: \u20ac{o.get('total_price')}"
            )
            
            m_id = o.get('user_message_id', 0)
            u_id = o.get('user_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            
            if o.get('status') == 'PENDING':
                markup.add(
                    types.InlineKeyboardButton("✅ Accetta Ordine", callback_data=f"act_acc_{o['id']}_{u_id}_{m_id}"),
                    types.InlineKeyboardButton("❌ Annulla Ordine", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}")
                )
            else:
                if is_meetup:
                    markup.add(types.InlineKeyboardButton("📍 Conferma Meet up", callback_data=f"act_meet_{o['id']}_{u_id}_{m_id}"))
                else:
                    markup.add(types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"act_trk_{o['id']}_{u_id}_{m_id}"))
                
                markup.add(types.InlineKeyboardButton("✍️ Aggiornamento Custom", callback_data=f"act_upd_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("❌ Annulla Ordine", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
                
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine lista:", reply_markup=get_cancel_keyboard())

    elif data == "dash_ord_serv":
        user_states.pop(user_id, None)
        orders = [o for o in db_get_all_orders() if o.get('status') in ['PENDING', 'ACCEPTED'] and o.get('order_type') == 'SERVICE']
        
        bot.edit_message_text("🛠 <b>SERVIZI IN LAVORAZIONE</b>", user_id, call.message.message_id, parse_mode="HTML")
        if not orders:
            bot.send_message(user_id, "✅ Nessun servizio in lavorazione.", reply_markup=get_cancel_keyboard())
            return
            
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - \u20ac{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            st_text = "⏳ Da Visionare" if o.get('status') == 'PENDING' else "⚙️ In Lavorazione"

            msg = (
                f"🛠 <b>SERVIZIO #{o.get('id')}</b> [{st_text}]\n"
                f"👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 Dati forniti: {o.get('address', 'N/D')}\n\n"
                f"📦 Richiesto:\n{items_str}\n\n"
                f"💰 Totale: \u20ac{o.get('total_price')}"
            )
            
            m_id = o.get('user_message_id', 0)
            u_id = o.get('user_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            
            if o.get('status') == 'PENDING':
                markup.add(
                    types.InlineKeyboardButton("⚙️ Segna in Lavorazione", callback_data=f"act_work_{o['id']}_{u_id}_{m_id}"),
                    types.InlineKeyboardButton("❌ Annulla Servizio", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}")
                )
            else:
                markup.add(types.InlineKeyboardButton("📤 Invia Esito Finale", callback_data=f"act_file_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("✍️ Aggiornamento Custom", callback_data=f"act_upd_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("❌ Annulla Servizio", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
                
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine lista:", reply_markup=get_cancel_keyboard())

    elif data.startswith("act_trk_") or data.startswith("act_file_") or data.startswith("act_meet_") or data.startswith("act_upd_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        if action == "trk": step_name, prompt_txt = "WAITING_TRACKING", f"🚚 Scrivi il <b>TRACKING e NOTE</b> per l'ordine #{o_id}:"
        elif action == "file": step_name, prompt_txt = "WAITING_FILE_INFO", f"📤 Scrivi l'<b>ESITO o IL LINK DEL FILE</b> per il Servizio #{o_id}:"
        elif action == "meet": step_name, prompt_txt = "WAITING_MEETUP", f"📍 Scrivi i <b>DETTAGLI DEL MEET UP</b> per l'ordine #{o_id}:"
        elif action == "upd": step_name, prompt_txt = "WAITING_UPDATE", f"✍️ Scrivi l'<b>AGGIORNAMENTO CUSTOM</b> per l'ordine #{o_id}:"
        
        user_states[user_id] = {"step": step_name, "o_id": o_id, "u_id": u_id, "m_id": m_id}
        bot.send_message(user_id, prompt_txt, parse_mode="HTML", reply_markup=get_cancel_keyboard())
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass

    elif data.startswith("act_acc_") or data.startswith("act_work_") or data.startswith("act_cnc_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        if action == "acc":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"✅ <b>ORDINE #{o_id} CONFERMATO</b>\n\nIl tuo ordine è stato accettato ed è in fase di preparazione."
        elif action == "work":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"⚙️ <b>SERVIZIO #{o_id} IN LAVORAZIONE</b>\n\nStiamo elaborando i tuoi dati."
        elif action == "cnc":
            db_update_order_status(o_id, "CANCELLED")
            new_text = f"❌ <b>ATTENZIONE</b>\nIl tuo ordine/servizio #{o_id} è stato annullato dal sistema."

        if u_id and str(u_id) != "0":
            try:
                if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass
        
        bot.answer_callback_query(call.id, "✅ Stato Aggiornato LIVE!")
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass

    elif data.startswith("act_restore_"):
        parts = data.split("_")
        o_id, u_id, m_id = parts[2], parts[3], parts[4]
        db_update_order_status(o_id, "PENDING")
        new_text = f"⏳ <b>ORDINE #{o_id} RIPRISTINATO</b>\n\nLa tua richiesta è stata sbloccata ed è tornata in elaborazione."
        
        if u_id and str(u_id) != "0":
            try:
                if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass
            
        bot.answer_callback_query(call.id, "✅ Ordine Ripristinato!")
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass

    elif data == "m_hist":
        user_states.pop(user_id, None)
        bot.edit_message_text("📜 <b>STORICO ARCHIVI</b>\n\nScegli quale registro visualizzare:", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_hist_keyboard())

    elif data == "hist_shipped":
        orders = [o for o in db_get_all_orders() if o.get('status') == 'SHIPPED']
        if not orders:
            bot.send_message(user_id, "📭 Nessun ordine completato nello storico.", reply_markup=get_cancel_keyboard())
            return
        for o in orders:
            msg = f"✅ ORDINE #{o.get('id')} [COMPLETATO]\n👤 @{o.get('username')}\n📝 {o.get('tracking_code', 'N/D')}\n────────────────────────"
            try: bot.send_message(user_id, msg)
            except: pass
        bot.send_message(user_id, "👇 Fine registro completati:", reply_markup=get_cancel_keyboard())

    elif data == "hist_cancelled":
        orders = [o for o in db_get_all_orders() if o.get('status') == 'CANCELLED']
        if not orders:
            bot.send_message(user_id, "📭 Nessun ordine annullato.", reply_markup=get_cancel_keyboard())
            return
        for o in orders:
            msg = f"❌ ORDINE #{o.get('id')} [ANNULLATO]\n👤 @{o.get('username')}\n────────────────────────"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Ripristina Ordine in Dashboard", callback_data=f"act_restore_{o['id']}_{o['user_id']}_{o.get('user_message_id', 0)}"))
            try: bot.send_message(user_id, msg, reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine registro annullati:", reply_markup=get_cancel_keyboard())

    elif data == "m_pts":
        user_states.pop(user_id, None)
        msg = "💎 <b>GESTIONE PUNTI UTENTI</b>\n\nUsa in chat:\n<code>/punti ID_UTENTE QUANTITA</code>\n\n(Es: <code>/punti 123456789 100</code>)"
        bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=get_cancel_keyboard())

    elif data == "m_prod":
        user_states.pop(user_id, None)
        bot.edit_message_text("📦 <b>GESTIONE PRODOTTI & MEDIA</b>\n\nCosa desideri fare?", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_prod_keyboard())

    elif data == "p_add":
        user_states.pop(user_id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        cats = ["Meet up Roma", "Documenti falsi", "Banconote false", "Monete false", "Coca", "Weed", "Hash", "Telefoni Criptati", "Servizi", "Altro"]
        markup.add(*[types.InlineKeyboardButton(c, callback_data=f"addcat_{c}") for c in cats])
        markup.add(types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main"))
        bot.edit_message_text("Seleziona la categoria del prodotto:", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("addcat_"):
        cat = data.replace("addcat_", "")
        user_states[user_id] = {"category": cat, "step": "WAITING_MEDIA", "media_list": []}
        bot.edit_message_text(f"Categoria: {cat}\n\n📸 Invia ORA Foto/Video del prodotto.", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_media_done_keyboard())

    elif data == "p_list":
        user_states.pop(user_id, None)
        prods = db_get_products()
        if not prods:
            bot.send_message(user_id, "📭 Nessun prodotto.", reply_markup=get_cancel_keyboard())
            return
        for p in prods:
            st_val = p.get('in_showcase', 1) == 1
            status_str = '🟢 In Vetrina' if st_val else '🔴 Nascosto'
            msg = f"📦 {p.get('name')}\n🏷 Categoria: {p.get('category')}\n👁 Stato: {status_str}"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p['id']}_{st_val}"), types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p['id']}"))
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}"))
            bot.send_message(user_id, msg, reply_markup=markup)
        bot.send_message(user_id, "👇 Opzioni:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edit_"):
        p_id = data.split("_")[1]
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("✏️ Modifica Nome", callback_data=f"edname_{p_id}"),
            types.InlineKeyboardButton("📝 Modifica Descrizione", callback_data=f"eddesc_{p_id}"),
            types.InlineKeyboardButton("💰 Modifica Prezzi", callback_data=f"edprc_{p_id}"),
            types.InlineKeyboardButton("📸 Sostituisci Foto/Video", callback_data=f"edmedia_{p_id}"),
            types.InlineKeyboardButton("🔙 Torna alla Lista", callback_data="p_list")
        )
        bot.edit_message_text("Cosa vuoi modificare?", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("edname_"):
        user_states[user_id] = {"step": "EDIT_NAME", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "✏️ Scrivi il NUOVO NOME:", reply_markup=get_cancel_keyboard())

    elif data.startswith("eddesc_"):
        user_states[user_id] = {"step": "EDIT_DESC", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "📝 Scrivi la NUOVA DESCRIZIONE:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edprc_"):
        user_states[user_id] = {"step": "EDIT_PRICES", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "💰 Scrivi le NUOVE VARIANTI (es. 10pz 140, 20pz 250):", reply_markup=get_cancel_keyboard())

    elif data.startswith("edmedia_"):
        user_states[user_id] = {"step": "WAITING_MEDIA_EDIT", "target_product": data.split("_")[1], "media_list": []}
        bot.send_message(user_id, "📸 Invia le nuove foto/video.", reply_markup=get_media_done_keyboard())

    elif data.startswith("tog_"):
        parts = data.split("_")
        p_id, curr_st = parts[1], parts[2] == 'True'
        new_st = not curr_st
        if db_toggle_product(p_id, curr_st):
            bot.answer_callback_query(call.id, "✅ Stato aggiornato!")
            msg_text = call.message.text.replace("🟢 In Vetrina", "🔴 Nascosto") if "🟢 In Vetrina" in call.message.text else call.message.text.replace("🔴 Nascosto", "🟢 In Vetrina")
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p_id}_{new_st}"), types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p_id}"))
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p_id}"))
            try: bot.edit_message_text(msg_text, user_id, call.message.message_id, reply_markup=markup)
            except: pass

    elif data.startswith("del_"):
        if db_delete_product(data.split("_")[1]):
            bot.answer_callback_query(call.id, "🗑️ Prodotto eliminato!")
            try: bot.delete_message(user_id, call.message.message_id)
            except: pass

    elif data == "done_media":
        st = user_states.get(user_id, {})
        if not st.get("media_list"):
            bot.answer_callback_query(call.id, "❌ Invia almeno un file multimediale!", show_alert=True)
            return
        
        if st.get("step") == "WAITING_MEDIA":
            st["step"] = "WAITING_NAME"
            bot.send_message(user_id, f"✅ Caricati {len(st['media_list'])} file!\n\n📝 Ora invia il NOME del prodotto:", reply_markup=get_cancel_keyboard())
        elif st.get("step") == "WAITING_MEDIA_EDIT":
            media_list = st["media_list"]
            db_update_product(st["target_product"], {"media_list": media_list, "media_url": media_list[0]["url"], "media_type": media_list[0]["type"]})
            bot.send_message(user_id, "✅ Media aggiornati!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)

# ======================================================
# HANDLER MEDIA (CARICAMENTO SU STORAGE LOCALE PRIVATO)
# ======================================================
@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    state = user_states.get(user_id, {})
    if state.get("step") not in ["WAITING_MEDIA", "WAITING_MEDIA_EDIT"]: return

    wait_msg = bot.reply_to(message, "⏳ Salvataggio sul Server Locale in corso...")

    if message.photo:
        file_id = message.photo[-1].file_id
        media_type, mime, ext = 'image', 'image/jpeg', 'jpg'
    else:
        if message.video.file_size > 20 * 1024 * 1024:
            bot.edit_message_text("❌ IL VIDEO PESA PIÙ DI 20MB. Telegram lo blocca. Comprimilo.", user_id, wait_msg.message_id)
            return
        file_id = message.video.file_id
        media_type, mime, ext = 'video', 'video/mp4', 'mp4'

    try:
        file_info = bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
        file_bytes = requests.get(file_url).content
        
        public_url, err = upload_to_local_storage(file_bytes, mime, ext)
        if public_url:
            if "media_list" not in user_states[user_id]: user_states[user_id]["media_list"] = []
            user_states[user_id]["media_list"].append({"url": public_url, "type": media_type})
            tot = len(user_states[user_id]["media_list"])
            bot.edit_message_text(f"✅ Salvato Localmente!\n📸 Media #{tot} aggiunto.\nContinua o premi Fine.", user_id, wait_msg.message_id, reply_markup=get_media_done_keyboard())
        else:
            bot.edit_message_text(f"❌ ERRORE SERVER:\n{err}", user_id, wait_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Errore scaricamento: {e}", user_id, wait_msg.message_id)


# ======================================================
# HANDLER TESTO (INPUT ADMIN)
# ======================================================
@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    if message.text and message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_user, qty = int(parts[1]), int(parts[2])
            ok, new_total = db_update_user_points(target_user, qty)
            if ok:
                bot.reply_to(message, f"✅ L'utente {target_user} ora ha {new_total} punti.")
                try: bot.send_message(target_user, f"💎 <b>Aggiornamento Caveau:</b> il tuo saldo è {new_total} punti.", parse_mode="HTML")
                except: pass
            else: bot.reply_to(message, "❌ Errore: Utente non trovato.")
        except: bot.reply_to(message, "❌ Errore sintassi. Usa: /punti 123456789 100")
        return

    if step in ["WAITING_TRACKING", "WAITING_FILE_INFO", "WAITING_MEETUP", "WAITING_UPDATE"]:
        admin_text = message.text.strip()
        o_id, u_id, m_id = state["o_id"], state["u_id"], state["m_id"]
        
        if step == "WAITING_UPDATE":
            db_update_order_status(o_id, "ACCEPTED", f"Aggiornamento: {admin_text}")
            title, label = "🔔 <b>AGGIORNAMENTO ORDINE</b>", "Messaggio dallo Staff:"
            new_text = f"{title}\n<i>Rif. #{o_id}</i>\n\n<b>{label}</b>\n<code>{admin_text}</code>\n\n<i>Stiamo lavorando alla tua richiesta...</i>"
            if u_id and str(u_id) != "0":
                try:
                    if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                    else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
                except: pass
            bot.reply_to(message, "✅ Aggiornamento Inviato!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)
            return

        db_update_order_status(o_id, "SHIPPED", admin_text)
        
        if step == "WAITING_TRACKING": title, label = "🚚 <b>ORDINE SPEDITO</b>", "Tracking / Istruzioni:"
        elif step == "WAITING_FILE_INFO": title, label = "✅ <b>SERVIZIO COMPLETATO</b>", "Esito / Link al Documento:"
        elif step == "WAITING_MEETUP": title, label = "📍 <b>DETTAGLI MEET UP</b>", "Info e Appuntamento:"
            
        new_text = f"{title}\n<i>Rif. #{o_id}</i>\n\n<b>{label}</b>\n<code>{admin_text}</code>\n\nGrazie per aver scelto Il Falsario 🎭"
        if u_id and str(u_id) != "0":
            try:
                if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass
                
        bot.reply_to(message, "✅ Operazione Completata! Archiviato nei Completati.", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "EDIT_NAME":
        db_update_product(state["target_product"], {"name": message.text})
        bot.reply_to(message, "✅ Nome aggiornato!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "EDIT_DESC":
        db_update_product(state["target_product"], {"description": message.text})
        bot.reply_to(message, "✅ Descrizione aggiornata!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step in ["EDIT_PRICES", "WAITING_PRICES"]:
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-").replace("):", "").replace(")", "").strip()
            raw_variants = clean_text.split(",")
            prices = []
            for r in raw_variants:
                r = r.strip()
                if not r: continue
                if "-" in r:
                    parts = r.split("-")
                    qty = "-".join(parts[:-1]).strip()
                    price_str = parts[-1].replace("€", "").strip()
                else:
                    parts = r.split()
                    if len(parts) >= 2:
                        qty = " ".join(parts[:-1]).strip()
                        price_str = parts[-1].replace("€", "").strip()
                    else: continue
                prices.append({"qty": qty, "price": float(price_str)})
                
            if not prices: raise ValueError("Errore")
            
            if step == "EDIT_PRICES":
                db_update_product(state["target_product"], {"price_options": prices})
                bot.reply_to(message, "✅ Prezzi aggiornati!", reply_markup=get_admin_main_keyboard())
                user_states.pop(user_id, None)
            else:
                media_list = state.get("media_list", [])
                payload = {
                    "name": state["name"], "category": state["category"],
                    "media_list": media_list, "media_url": media_list[0]["url"] if media_list else "",
                    "media_type": media_list[0]["type"] if media_list else "image",
                    "price_options": prices, "description": state.get("desc", ""), "in_showcase": True
                }
                success, err_msg = db_add_product(payload)
                if success: bot.reply_to(message, f"🎉 PRODOTTO PUBBLICATO!\n📦 {state['name']}", reply_markup=get_admin_main_keyboard())
                else: bot.reply_to(message, f"❌ ERRORE DATABASE:\n{err_msg}", reply_markup=get_admin_main_keyboard())
                user_states.pop(user_id, None)
        except:
            bot.reply_to(message, "❌ Formato non riconosciuto. Es: 10pz 140, 25g - 100", reply_markup=get_cancel_keyboard())

    elif step == "WAITING_NAME":
        state["name"] = message.text
        state["step"] = "WAITING_DESC"
        bot.reply_to(message, "✍️ Nome salvato. Ora invia la DESCRIZIONE:", reply_markup=get_cancel_keyboard())

    elif step == "WAITING_DESC":
        state["desc"] = message.text
        state["step"] = "WAITING_PRICES"
        bot.reply_to(message, "💰 Ultimo step. Invia i PREZZI (Es: 10pz 140, 25g - 100):", reply_markup=get_cancel_keyboard())

if __name__ == '__main__':
    threading.Thread(target=run_health_server, daemon=True).start()
    print("🤖 Bot Il Falsario (SERVER LOCALE 100% PRIVATO) Avviato!")
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            time.sleep(5)
