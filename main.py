import os
import json
import time
import threading
import uuid
import sqlite3
import base64
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 0))

DB_PATH = "falsario.db"
MEDIA_DIR = "/var/www/html/media"
os.makedirs(MEDIA_DIR, exist_ok=True)
GIVEAWAY_DB = 'giveaway_data.json'

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# ==========================================
# GESTIONE DATABASE E GIVEAWAY
# ==========================================
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
    c.execute('''CREATE TABLE IF NOT EXISTS quotes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, description TEXT, budget TEXT, admin_reply TEXT, price REAL, status TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    try:
        c.execute("ALTER TABLE orders ADD COLUMN pratica_code TEXT")
    except sqlite3.OperationalError:
        pass 
        
    conn.commit()
    conn.close()

init_db()

def get_giveaway():
    if not os.path.exists(GIVEAWAY_DB):
        return {"is_active": 1, "description": "🎁 Evento Esclusivo", "prize": "100€", "end_date": "Da definire", "participants": {}}
    try:
        with open(GIVEAWAY_DB, 'r') as f: return json.load(f)
    except:
        return {"is_active": 1, "description": "🎁 Evento Esclusivo", "prize": "100€", "end_date": "Da definire", "participants": {}}

def save_giveaway(data):
    with open(GIVEAWAY_DB, 'w') as f:
        json.dump(data, f)

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
    rows = conn.execute("SELECT * FROM products ORDER BY id ASC").fetchall()
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

def db_save_order(user_id, username, cart, total, address, order_type, pratica_code):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO orders (user_id, username, items, total_price, address, status, order_type, pratica_code) 
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
              (user_id, username, json.dumps(cart), total, address, "PENDING", order_type, pratica_code))
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

def db_save_quote(user_id, username, description, budget):
    conn = get_db()
    c = conn.cursor()
    c.execute('''INSERT INTO quotes (user_id, username, description, budget, status) VALUES (?, ?, ?, ?, ?)''', 
              (user_id, username, description, budget, "PENDING_ADMIN"))
    qid = c.lastrowid
    conn.commit()
    conn.close()
    return qid

def db_get_user_quotes(user_id):
    conn = get_db()
    rows = conn.execute("SELECT * FROM quotes WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def db_update_quote(quote_id, status, price=0.0, admin_reply=""):
    conn = get_db()
    conn.execute("UPDATE quotes SET status = ?, price = ?, admin_reply = ? WHERE id = ?", (status, price, admin_reply, quote_id))
    conn.commit()
    conn.close()

def db_get_pending_quotes():
    conn = get_db()
    rows = conn.execute("SELECT * FROM quotes WHERE status = 'PENDING_ADMIN' ORDER BY created_at ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

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

# ==========================================
# SERVER API REST
# ==========================================
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
            raw_search = self.path.split('/')[-1].strip().upper()
            orders = db_get_all_orders()
            order = next((o for o in orders if str(o.get('pratica_code')).strip().upper() == raw_search or str(o.get('tracking_code')).strip().upper() == raw_search), None)
            
            if order: 
                if not order.get('pratica_code'): order['pratica_code'] = f"PR-LGCY-{order['id']}"
                self.wfile.write(json.dumps(order).encode('utf-8'))
            else: 
                self.wfile.write(json.dumps({"error": "Not found"}).encode('utf-8'))
            
        elif self.path.startswith('/api/user/'):
            user_id_str = self.path.split('/')[-1]
            try: user_id = int(user_id_str.replace("ID_", ""))
            except: user_id = user_id_str
                
            conn = get_db()
            row = conn.execute("SELECT points FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
            conn.close()
            if row: self.wfile.write(json.dumps({"points": row['points']}).encode('utf-8'))
            else: self.wfile.write(json.dumps({"points": 50}).encode('utf-8'))
            
        elif self.path.startswith('/api/quotes/'):
            user_id_str = self.path.split('/')[-1]
            try: user_id = int(user_id_str.replace("ID_", ""))
            except: user_id = user_id_str
            self.wfile.write(json.dumps(db_get_user_quotes(user_id)).encode('utf-8'))
            
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
            
        elif self.path == '/api/giveaway':
            g = get_giveaway()
            resp = {
                "is_active": g.get("is_active", 0),
                "description": g.get("description", ""),
                "prize": g.get("prize", ""),
                "end_date": g.get("end_date", ""),
                "participants_count": len(g.get("participants", {}))
            }
            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        elif self.path == '/api/giveaway/join':
            user_id = str(data.get('id', ''))
            username = data.get('username', 'Anonimo')
            g = get_giveaway()
            
            if not g.get("is_active"):
                self.wfile.write(json.dumps({"success": False, "error": "Evento chiuso al momento."}).encode('utf-8'))
                return
            if user_id in g.get("participants", {}):
                self.wfile.write(json.dumps({"success": False, "error": "Sei già iscritto a questo evento!"}).encode('utf-8'))
                return
                
            g.setdefault("participants", {})[user_id] = username
            save_giveaway(g)
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            return

        elif self.path == '/api/quotes/new':
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            desc = data.get("description", "")
            budget = data.get("budget", "")
            
            qid = db_save_quote(user_id, username, desc, budget)
            
            if ADMIN_ID and ADMIN_ID != 0:
                try: bot.send_message(ADMIN_ID, f"💡 <b>NUOVO TICKET SVILUPPO IT!</b>\nDa: @{username} (ID: {user_id})\nBudget: {budget}\n\n👉 Apri 'Gestione Servizi Digitali' -> 'Preventivi su Misura' per rispondere.", parse_mode="HTML")
                except: pass
            self.wfile.write(json.dumps({"success": True}).encode('utf-8'))

        elif self.path == '/api/quotes/action':
            quote_id = data.get("quote_id")
            action = data.get("action")
            user_id = data.get("user_id")
            
            # NUOVA API: CANCELLAZIONE DEFINITIVA DAL REGISTRO
            if action == 'DELETE':
                conn = get_db()
                conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
                conn.commit()
                conn.close()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                return

            conn = get_db()
            q = conn.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
            conn.close()
            
            if not q:
                self.wfile.write(json.dumps({"error": "Preventivo non trovato"}).encode('utf-8'))
                return

            if action == 'REJECT':
                db_update_quote(quote_id, "REJECTED_BY_USER", q['price'], q['admin_reply'])
                if ADMIN_ID and ADMIN_ID != 0:
                    try: bot.send_message(ADMIN_ID, f"❌ Il cliente (ID:{user_id}) ha rifiutato l'offerta per il Ticket #{quote_id}.")
                    except: pass
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))

            elif action == 'ACCEPT':
                secure_hash = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=6))
                pratica_code = f"PR-DEV{secure_hash[:4]}"
                
                cart = [{"name": "Sviluppo IT su Misura", "qty": 1, "price": q['price'], "category": "Servizi"}]
                address = f"PROGETTO APPROVATO:\n{q['description']}\n\nAccordo: {q['admin_reply']}\nSaldo concordato in chat."
                
                order_id = db_save_order(user_id, q['username'], cart, q['price'], address, "SERVICE", pratica_code)
                db_update_quote(quote_id, "CONVERTED_TO_ORDER", q['price'], q['admin_reply'])
                
                if ADMIN_ID and ADMIN_ID != 0:
                    try: bot.send_message(ADMIN_ID, f"🎉 <b>PREVENTIVO ACCETTATO!</b>\nGenerata la Pratica: <b>{pratica_code}</b> per il servizio IT da {q['price']}€.", parse_mode="HTML")
                    except: pass
                    
                self.wfile.write(json.dumps({"success": True, "pratica_code": pratica_code}).encode('utf-8'))

        elif self.path == '/api/order':
            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            address = data.get("address", "Non specificato")

            secure_hash = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=6))
            pratica_code = f"PR-{secure_hash}"

            is_digital = any(
                any(keyword in str(item.get("category", "")).lower() or keyword in str(item.get("name", "")).lower() 
                for keyword in ["servizi", "exchange", "buoni amazon", "buoni q8", "amazon", "q8"]) 
                for item in cart
            )
            order_type = "SERVICE" if is_digital else "PHYSICAL"

            order_id = db_save_order(user_id, username, cart, total, address, order_type, pratica_code)
            items_text = "\n".join([f"• {i['qty']}x {i['name']} - \u20ac{i['price']}" for i in cart])

            user_msg = (
                f"✅ <b>Richiesta Registrata con Successo!</b>\n\n"
                f"🏷 <b>Codice Pratica:</b> <code>{pratica_code}</code>\n"
                f"<i>Usa questo codice nel Tracker del sito per monitorare l'ordine.</i>\n\n"
                f"📦 <b>Riepilogo:</b>\n{items_text}\n\n"
                f"📍 <b>Dati Recapito/Info:</b>\n{address}\n\n"
                f"💰 <b>Totale:</b> \u20ac{total}\n\n"
                f"⏳ <i>Un operatore sta elaborando la tua richiesta. Riceverai aggiornamenti live qui.</i>"
            )
            
            if user_id and str(user_id) != "0":
                try:
                    sent = bot.send_message(int(user_id), user_msg, parse_mode="HTML")
                    db_update_order_msg_id(order_id, sent.message_id) 
                except: pass

            if ADMIN_ID and ADMIN_ID != 0:
                try:
                    alert_type = "🛠 NUOVO SERVIZIO" if is_digital else "📦 NUOVO ORDINE FISICO"
                    bot.send_message(ADMIN_ID, f"🔔 <b>{alert_type} RICEVUTO!</b>\nPratica: <b>{pratica_code}</b> da @{username}.\n👉 Apri /admin per gestirlo.", parse_mode="HTML")
                except: pass

            self.wfile.write(json.dumps({"success": True, "order_id": order_id, "pratica_code": pratica_code}).encode('utf-8'))

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), WebhookAPIHandler)
    server.serve_forever()

def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("🛍 Gestione Prodotti & Media", callback_data="m_prod"),
        types.InlineKeyboardButton("📦 Gestione Ordini (Fisici)", callback_data="dash_ord_phys"),
        types.InlineKeyboardButton("🛠 Gestione Servizi (Digitali)", callback_data="dash_ord_serv"),
        types.InlineKeyboardButton("📜 Storico Archivi", callback_data="m_hist"),
        types.InlineKeyboardButton("💎 Gestione Punti Utenti", callback_data="m_pts"),
        types.InlineKeyboardButton("🎁 Gestione Giveaway", callback_data="m_gw")
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
        "📢 Canale Feedback: https://t.me/+VPltIK0sMag5YmFk\n\n"
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

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID: return
    data = call.data

    if data == "m_main":
        user_states.pop(user_id, None)
        bot.edit_message_text("⚙️ <b>PANNELLO GESTIONALE CAVEAU</b>", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_main_keyboard())

    elif data == "m_gw":
        user_states.pop(user_id, None)
        gw = get_giveaway()
        st_val = gw.get("is_active", 1)
        status = "🟢 ATTIVO" if st_val else "🔴 INATTIVO"
        msg = (
            f"🎁 <b>DASHBOARD GIVEAWAY</b>\n\n"
            f"<b>Stato:</b> {status}\n"
            f"<b>Premio in Palio:</b> {gw.get('prize', 'N/D')}\n"
            f"<b>Descrizione:</b> {gw.get('description', 'N/D')}\n"
            f"<b>Scadenza:</b> {gw.get('end_date', 'N/D')}\n"
            f"<b>Iscritti Attuali:</b> {len(gw.get('participants', {}))}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton(f"👁️ Stato (On/Off)", callback_data=f"gw_tog_{0 if st_val else 1}"),
            types.InlineKeyboardButton("🏆 Imposta Premio", callback_data="gw_prize")
        )
        markup.add(
            types.InlineKeyboardButton("📝 Descrizione", callback_data="gw_desc"),
            types.InlineKeyboardButton("⏳ Scadenza", callback_data="gw_date")
        )
        markup.add(
            types.InlineKeyboardButton("📋 Lista Iscritti", callback_data="gw_list"),
            types.InlineKeyboardButton("🎲 ESTRAI VINCITORE", callback_data="gw_draw")
        )
        markup.add(types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main"))
        bot.edit_message_text(msg, user_id, call.message.message_id, parse_mode="HTML", reply_markup=markup)

    elif data.startswith("gw_tog_"):
        new_st = int(data.split("_")[2])
        gw = get_giveaway()
        gw["is_active"] = new_st
        save_giveaway(gw)
        bot.answer_callback_query(call.id, "✅ Stato Giveaway Aggiornato!")
        call.data = "m_gw"
        handle_callbacks(call)

    # --- SOSTITUITO SEND_MESSAGE CON EDIT_MESSAGE_TEXT PER UX FLUIDA (ZERO SPAZZATURA) ---
    elif data == "gw_prize":
        user_states[user_id] = {"step": "WAITING_GW_PRIZE"}
        bot.edit_message_text("🏆 Scrivi il nuovo PREMIO in palio (es. 100€ Bitcoin):", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())
    elif data == "gw_desc":
        user_states[user_id] = {"step": "WAITING_GW_DESC"}
        bot.edit_message_text("📝 Scrivi la nuova DESCRIZIONE dell'evento:", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())
    elif data == "gw_date":
        user_states[user_id] = {"step": "WAITING_GW_DATE"}
        bot.edit_message_text("⏳ Scrivi la SCADENZA (es. 31 Ottobre):", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())
    elif data == "gw_list":
        gw = get_giveaway()
        parts = gw.get("participants", {})
        if not parts:
            bot.answer_callback_query(call.id, "⚠️ Nessun iscritto al momento.", show_alert=True)
            return
        msg = "📋 <b>Lista Iscritti Giveaway:</b>\n\n"
        for uid, uname in parts.items(): msg += f"👤 {uname} (ID: <code>{uid}</code>)\n"
        bot.edit_message_text(msg, user_id, call.message.message_id, parse_mode='HTML', reply_markup=get_cancel_keyboard())
    elif data == "gw_draw":
        gw = get_giveaway()
        parts = gw.get("participants", {})
        if not parts:
            bot.answer_callback_query(call.id, "⚠️ Nessun iscritto per l'estrazione!", show_alert=True)
            return
        winner_id = random.choice(list(parts.keys()))
        winner_name = parts[winner_id]
        bot.edit_message_text(f"🎉 <b>ESTRAZIONE COMPLETATA!</b>\n\n👤 <b>Vincitore:</b> {winner_name}\n🆔 <b>ID:</b> <code>{winner_id}</code>\n\nContattalo per consegnare il premio!", user_id, call.message.message_id, parse_mode='HTML', reply_markup=get_admin_main_keyboard())
        bot.answer_callback_query(call.id, f"🎉 Ha vinto {winner_name}!", show_alert=True)

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
            pratica_code = o.get('pratica_code') if o.get('pratica_code') else f"PR-LGCY-{o.get('id')}"
            
            msg = f"🛒 <b>PRATICA {pratica_code}</b> [{st_text}]\n👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n📍 Recapito/Metodo: {address_str}\n\n📦 Prodotti:\n{items_str}\n\n💰 Totale: \u20ac{o.get('total_price')}"
            
            m_id = o.get('user_message_id', 0)
            u_id = o.get('user_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            
            if o.get('status') == 'PENDING':
                markup.add(types.InlineKeyboardButton("✅ Accetta Ordine", callback_data=f"act_acc_{o['id']}_{u_id}_{m_id}"), types.InlineKeyboardButton("❌ Annulla Ordine", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
            else:
                if is_meetup: markup.add(types.InlineKeyboardButton("📍 Conferma Meet up", callback_data=f"act_meet_{o['id']}_{u_id}_{m_id}"))
                else: markup.add(types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"act_trk_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("✍️ Aggiornamento Custom", callback_data=f"act_upd_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("❌ Annulla Ordine", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
                
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine lista:", reply_markup=get_cancel_keyboard())

    elif data == "dash_ord_serv":
        user_states.pop(user_id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("🛒 Servizi Standard (Exchange, Buoni)", callback_data="dash_ord_serv_std"),
            types.InlineKeyboardButton("💡 Preventivi su Misura (Ticket)", callback_data="dash_quotes"),
            types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main")
        )
        bot.edit_message_text("🛠 <b>GESTIONE SERVIZI DIGITALI</b>\n\nScegli il reparto operativo:", user_id, call.message.message_id, parse_mode="HTML", reply_markup=markup)

    elif data == "dash_ord_serv_std":
        user_states.pop(user_id, None)
        orders = [o for o in db_get_all_orders() if o.get('status') in ['PENDING', 'ACCEPTED'] and o.get('order_type') == 'SERVICE']
        bot.edit_message_text("🛠 <b>SERVIZI STANDARD IN LAVORAZIONE</b>", user_id, call.message.message_id, parse_mode="HTML")
        if not orders:
            bot.send_message(user_id, "✅ Nessun servizio standard in lavorazione.", reply_markup=get_cancel_keyboard())
            return
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - \u20ac{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            st_text = "⏳ Da Visionare" if o.get('status') == 'PENDING' else "⚙️ In Lavorazione"
            pratica_code = o.get('pratica_code') if o.get('pratica_code') else f"PR-LGCY-{o.get('id')}"
            
            msg = f"🛠 <b>PRATICA {pratica_code}</b> [{st_text}]\n👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n📍 Dati forniti: {o.get('address', 'N/D')}\n\n📦 Richiesto:\n{items_str}\n\n💰 Totale: \u20ac{o.get('total_price')}"
            
            m_id = o.get('user_message_id', 0)
            u_id = o.get('user_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            
            if o.get('status') == 'PENDING':
                markup.add(types.InlineKeyboardButton("⚙️ Segna in Lavorazione", callback_data=f"act_work_{o['id']}_{u_id}_{m_id}"), types.InlineKeyboardButton("❌ Annulla Servizio", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
            else:
                markup.add(types.InlineKeyboardButton("📤 Invia Esito Finale", callback_data=f"act_file_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("✍️ Aggiornamento Custom", callback_data=f"act_upd_{o['id']}_{u_id}_{m_id}"))
                markup.add(types.InlineKeyboardButton("❌ Annulla Servizio", callback_data=f"act_cnc_{o['id']}_{u_id}_{m_id}"))
                
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine lista:", reply_markup=get_cancel_keyboard())

    elif data == "dash_quotes":
        user_states.pop(user_id, None)
        quotes = db_get_pending_quotes()
        bot.edit_message_text("💡 <b>PREVENTIVI SU MISURA IN ATTESA</b>\n\nAnalizza le richieste e formula un'offerta.", user_id, call.message.message_id, parse_mode="HTML")
        if not quotes:
            bot.send_message(user_id, "✅ Nessuna richiesta di sviluppo in attesa.", reply_markup=get_cancel_keyboard())
            return
        
        for q in quotes:
            msg = f"👨‍💻 <b>TICKET #{q['id']} - SVILUPPO IT</b>\n👤 Da: @{q['username']} (ID: {q['user_id']})\n💰 Budget Indicativo: {q['budget']}\n\n📝 <b>Richiesta:</b>\n{q['description']}"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("✍️ Formula Preventivo", callback_data=f"act_quote_{q['id']}"))
            markup.add(types.InlineKeyboardButton("❌ Non Fattibile (Rifiuta)", callback_data=f"rej_quote_{q['id']}"))
            bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
        bot.send_message(user_id, "👇 Opzioni:", reply_markup=get_cancel_keyboard())

    elif data.startswith("act_quote_"):
        q_id = data.split("_")[2]
        user_states[user_id] = {"step": "WAITING_QUOTE_OFFER", "q_id": q_id}
        bot.edit_message_text("✍️ Inserisci il <b>PREZZO</b> e la tua <b>RISPOSTA</b> separati da un trattino.\n\n<i>Esempio: 450 - Il bot si può fare, consegna in 4 giorni.</i>", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_cancel_keyboard())

    elif data.startswith("rej_quote_"):
        q_id = data.split("_")[2]
        db_update_quote(q_id, "REJECTED_BY_ADMIN", 0, "Siamo spiacenti ma il progetto non è attualmente fattibile o non rientra nei nostri standard operativi.")
        bot.answer_callback_query(call.id, "❌ Preventivo Rifiutato.")
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass

    elif data.startswith("act_trk_") or data.startswith("act_file_") or data.startswith("act_meet_") or data.startswith("act_upd_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        conn = get_db()
        row = conn.execute("SELECT pratica_code FROM orders WHERE id = ?", (o_id,)).fetchone()
        conn.close()
        pratica_code = row['pratica_code'] if row and row['pratica_code'] else f"PR-LGCY-{o_id}"
        
        if action == "trk": step_name, prompt_txt = "WAITING_TRACKING", f"🚚 Scrivi il <b>TRACKING e NOTE</b> per la pratica {pratica_code}:"
        elif action == "file": step_name, prompt_txt = "WAITING_FILE_INFO", f"📤 Scrivi l'<b>ESITO o IL LINK DEL FILE</b> per la pratica {pratica_code}:"
        elif action == "meet": step_name, prompt_txt = "WAITING_MEETUP", f"📍 Scrivi i <b>DETTAGLI DEL MEET UP</b> per la pratica {pratica_code}:"
        elif action == "upd": step_name, prompt_txt = "WAITING_UPDATE", f"✍️ Scrivi l'<b>AGGIORNAMENTO CUSTOM</b> per la pratica {pratica_code}:"
        
        user_states[user_id] = {"step": step_name, "o_id": o_id, "u_id": u_id, "m_id": m_id}
        bot.edit_message_text(prompt_txt, user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_cancel_keyboard())

    elif data.startswith("act_acc_") or data.startswith("act_work_") or data.startswith("act_cnc_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        conn = get_db()
        row = conn.execute("SELECT pratica_code FROM orders WHERE id = ?", (o_id,)).fetchone()
        conn.close()
        pratica_code = row['pratica_code'] if row and row['pratica_code'] else f"PR-LGCY-{o_id}"
        
        if action == "acc":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"✅ <b>PRATICA {pratica_code} CONFERMATA</b>\n\nIl tuo ordine è stato accettato ed è in fase di preparazione."
        elif action == "work":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"⚙️ <b>PRATICA {pratica_code} IN LAVORAZIONE</b>\n\nStiamo elaborando i tuoi dati."
        elif action == "cnc":
            db_update_order_status(o_id, "CANCELLED")
            new_text = f"❌ <b>ATTENZIONE</b>\n\nLa tua pratica {pratica_code} è stata annullata dal sistema."

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
        
        conn = get_db()
        row = conn.execute("SELECT pratica_code FROM orders WHERE id = ?", (o_id,)).fetchone()
        conn.close()
        pratica_code = row['pratica_code'] if row and row['pratica_code'] else f"PR-LGCY-{o_id}"
        
        db_update_order_status(o_id, "PENDING")
        new_text = f"⏳ <b>PRATICA {pratica_code} RIPRISTINATA</b>\n\nLa tua richiesta è stata sbloccata ed è tornata in elaborazione."
        if u_id and str(u_id) != "0":
            try:
                if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass
        bot.answer_callback_query(call.id, "✅ Ordine Ripristinato in Dashboard!")
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
        bot.send_message(user_id, f"🟩 <b>ARCHIVIO COMPLETATI</b> ({len(orders)} totali):", parse_mode="HTML")
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']})" for i in items]) if items else "  • Nessun dettaglio"
            pratica_code = o.get('pratica_code') if o.get('pratica_code') else f"PR-LGCY-{o.get('id')}"
            msg = f"✅ PRATICA {pratica_code} [COMPLETATO]\n👤 @{o.get('username')} (ID: {o.get('user_id')})\n📍 {o.get('address', 'N/D')}\n📝 Esito/Note: {o.get('tracking_code', 'N/D')}\n\n📦:\n{items_str}\n────────────────────────"
            try: bot.send_message(user_id, msg)
            except: pass
        bot.send_message(user_id, "👇 Fine registro completati:", reply_markup=get_cancel_keyboard())

    elif data == "hist_cancelled":
        orders = [o for o in db_get_all_orders() if o.get('status') == 'CANCELLED']
        if not orders:
            bot.send_message(user_id, "📭 Nessun ordine annullato.", reply_markup=get_cancel_keyboard())
            return
        bot.send_message(user_id, f"🟥 <b>ARCHIVIO ANNULLATI</b> ({len(orders)} totali):", parse_mode="HTML")
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']})" for i in items]) if items else "  • Nessun dettaglio"
            pratica_code = o.get('pratica_code') if o.get('pratica_code') else f"PR-LGCY-{o.get('id')}"
            msg = f"❌ PRATICA {pratica_code} [ANNULLATO]\n👤 @{o.get('username')} (ID: {o.get('user_id')})\n📍 {o.get('address', 'N/D')}\n\n📦:\n{items_str}\n────────────────────────"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔄 Ripristina Ordine in Dashboard", callback_data=f"act_restore_{o['id']}_{o['user_id']}_{o.get('user_message_id', 0)}"))
            try: bot.send_message(user_id, msg, reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine registro annullati:", reply_markup=get_cancel_keyboard())

    elif data == "m_pts":
        user_states.pop(user_id, None)
        msg = (
            "💎 <b>MOTORE PUNTI INTELLIGENTE</b>\n\n"
            "Puoi caricare punti usando 4 metodi:\n\n"
            "1️⃣ <b>Per Username:</b> <code>/punti @Mario 100</code>\n"
            "2️⃣ <b>Per ID Telegram:</b> <code>/punti 123456789 100</code>\n"
            "3️⃣ <b>Per ID Sito:</b> <code>/punti ID_123456789 100</code>\n"
            "4️⃣ <b>Per Risposta:</b> Rispondi a un utente/contatto scrivendo solo <code>/punti 100</code>"
        )
        bot.edit_message_text(msg, user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_cancel_keyboard())

    elif data == "m_prod":
        user_states.pop(user_id, None)
        bot.edit_message_text("📦 <b>GESTIONE PRODOTTI & MEDIA</b>\n\nCosa desideri fare?", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_prod_keyboard())

    elif data == "p_add":
        user_states.pop(user_id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        cats = ["Meetup", "Documenti falsi", "Banconote false", "Monete false", "Coca", "Weed", "Hash", "Telefoni Criptati", "Servizi", "Giveaway", "Altro"]
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
            bot.edit_message_text("📭 Nessun prodotto.", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())
            return
        # Sostituiamo il menu in alto per pulire la chat, i prodotti verranno accodati.
        bot.edit_message_text("📋 <b>LISTA PRODOTTI</b>\nEcco tutti i prodotti in vetrina:", user_id, call.message.message_id, parse_mode="HTML")
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
        bot.edit_message_text("✏️ Scrivi il NUOVO NOME:", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())

    elif data.startswith("eddesc_"):
        user_states[user_id] = {"step": "EDIT_DESC", "target_product": data.split("_")[1]}
        bot.edit_message_text("📝 Scrivi la NUOVA DESCRIZIONE:", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())

    elif data.startswith("edprc_"):
        user_states[user_id] = {"step": "EDIT_PRICES", "target_product": data.split("_")[1]}
        bot.edit_message_text("💰 Scrivi le NUOVE VARIANTI (es. 10pz 140, 20pz 250):", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())

    elif data.startswith("edmedia_"):
        user_states[user_id] = {"step": "WAITING_MEDIA_EDIT", "target_product": data.split("_")[1], "media_list": []}
        bot.edit_message_text("📸 Invia le nuove foto/video.", user_id, call.message.message_id, reply_markup=get_media_done_keyboard())

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
            bot.edit_message_text(f"✅ Caricati {len(st['media_list'])} file!\n\n📝 Ora invia il NOME del prodotto:", user_id, call.message.message_id, reply_markup=get_cancel_keyboard())
        elif st.get("step") == "WAITING_MEDIA_EDIT":
            media_list = st["media_list"]
            db_update_product(st["target_product"], {"media_list": media_list, "media_url": media_list[0]["url"], "media_type": media_list[0]["type"]})
            bot.edit_message_text("✅ Media aggiornati!", user_id, call.message.message_id, reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)

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

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    # RISPOSTA AL PREVENTIVO DA PARTE DELL'ADMIN
    if step == "WAITING_QUOTE_OFFER":
        try:
            raw = message.text.split("-", 1)
            price = float(raw[0].replace('€','').strip())
            msg_reply = raw[1].strip() if len(raw) > 1 else "Preventivo approvato. Attendo tua conferma per iniziare."
            
            db_update_quote(state["q_id"], "QUOTED", price, msg_reply)
            bot.reply_to(message, f"✅ Offerta di {price}€ inviata al cliente con successo.", reply_markup=get_admin_main_keyboard())
            
            conn = get_db()
            q = conn.execute("SELECT user_id FROM quotes WHERE id = ?", (state["q_id"],)).fetchone()
            conn.close()
            if q and str(q['user_id']) != "0":
                try: bot.send_message(int(q['user_id']), f"🔔 <b>PREVENTIVO RICEVUTO!</b>\nIl laboratorio ha risposto alla tua richiesta di sviluppo su misura.\n\n👉 Apri il Caveau, vai su 'Servizi su Misura' -> 'Le Tue Richieste' per leggere l'offerta e accettarla.", parse_mode="HTML")
                except: pass
            user_states.pop(user_id, None)
        except:
            bot.reply_to(message, "❌ Formato errato. Devi scrivere: PREZZO - MESSAGGIO\nEs: 400 - Si può fare. Riprova:")
        return

    if message.text and message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_id = None
            qty = 0
            
            if message.reply_to_message:
                qty = int(parts[1]) if len(parts) > 1 else 0
                if message.reply_to_message.forward_from:
                    target_id = message.reply_to_message.forward_from.id
                elif hasattr(message.reply_to_message, 'contact') and message.reply_to_message.contact is not None and message.reply_to_message.contact.user_id:
                    target_id = message.reply_to_message.contact.user_id
                else:
                    target_id = message.reply_to_message.from_user.id
            elif len(parts) >= 3:
                target_str = parts[1]
                qty = int(parts[2])
                
                if target_str.startswith("@"):
                    username = target_str.replace("@", "")
                    conn = get_db()
                    row = conn.execute("SELECT telegram_id FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
                    conn.close()
                    if row: target_id = row['telegram_id']
                    else:
                        bot.reply_to(message, f"❌ Nessun utente @{username} trovato nel database del bot.")
                        return
                elif target_str.upper().startswith("ID_"): target_id = int(target_str.upper().replace("ID_", ""))
                else: target_id = int(target_str)
            
            if target_id is None: raise ValueError("Nessun target valido")

            conn = get_db()
            row = conn.execute("SELECT points FROM users WHERE telegram_id = ?", (target_id,)).fetchone()
            if not row:
                conn.execute("INSERT INTO users (telegram_id, username, points) VALUES (?, ?, ?)", (target_id, "Utente_Caveau", 0))
                conn.commit()
            conn.close()

            ok, new_total = db_update_user_points(target_id, qty)
            if ok:
                receipt = f"✅ <b>RICARICA PUNTI COMPLETATA</b>\n\n🆔 <b>Target ID:</b> <code>{target_id}</code>\n💎 <b>Nuovo Saldo:</b> {new_total} punti"
                bot.reply_to(message, receipt, parse_mode="HTML")
                try: bot.send_message(target_id, f"💎 <b>Aggiornamento Caveau:</b> il tuo saldo è stato ricaricato. Hai ora <b>{new_total} punti</b>.", parse_mode="HTML")
                except: pass
            else: 
                bot.reply_to(message, "❌ Errore critico database durante l'aggiornamento.")
        except Exception as e: 
            err_msg = (
                "❌ <b>ERRORE DI SINTASSI</b>\n\n"
                "Usa il Motore Punti con questi formati:\n"
                "1️⃣ <code>/punti @username 100</code>\n"
                "2️⃣ <code>/punti 123456789 100</code>\n"
                "3️⃣ <code>/punti ID_123456789 100</code>\n"
                "4️⃣ <i>Rispondi a un messaggio/contatto con:</i> <code>/punti 100</code>"
            )
            bot.reply_to(message, err_msg, parse_mode="HTML")
        return

    if step == "WAITING_GW_PRIZE":
        gw = get_giveaway()
        gw["prize"] = message.text
        save_giveaway(gw)
        bot.reply_to(message, "✅ Premio aggiornato con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return
    elif step == "WAITING_GW_DESC":
        gw = get_giveaway()
        gw["description"] = message.text
        save_giveaway(gw)
        bot.reply_to(message, "✅ Descrizione aggiornata con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return
    elif step == "WAITING_GW_DATE":
        gw = get_giveaway()
        gw["end_date"] = message.text
        save_giveaway(gw)
        bot.reply_to(message, "✅ Scadenza aggiornata con successo!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)
        return

    if step in ["WAITING_TRACKING", "WAITING_FILE_INFO", "WAITING_MEETUP", "WAITING_UPDATE"]:
        admin_text = message.text.strip()
        o_id, u_id, m_id = state["o_id"], state["u_id"], state["m_id"]
        
        conn = get_db()
        row = conn.execute("SELECT pratica_code FROM orders WHERE id = ?", (o_id,)).fetchone()
        conn.close()
        pratica_code = row['pratica_code'] if row and row['pratica_code'] else f"PR-LGCY-{o_id}"
        
        if step == "WAITING_UPDATE":
            db_update_order_status(o_id, "ACCEPTED", f"Aggiornamento: {admin_text}")
            title, label = "🔔 <b>AGGIORNAMENTO ORDINE</b>", "Messaggio dallo Staff:"
            new_text = f"{title}\n<i>Pratica: {pratica_code}</i>\n\n<b>{label}</b>\n<code>{admin_text}</code>\n\n<i>Stiamo lavorando alla tua richiesta...</i>"
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
            
        new_text = f"{title}\n<i>Pratica: {pratica_code}</i>\n\n<b>{label}</b>\n<code>{admin_text}</code>\n\nGrazie per aver scelto Il Falsario 🎭"
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
    print("🤖 Bot Il Falsario (SERVER LOCALE) Avviato con TICKET SYSTEM!")
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            time.sleep(5)
