import os
import json
import time
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

# --- VARIABILI D'AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', '').strip()
SUPABASE_URL = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 8716217678))

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

def get_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def db_register_user(user_id, username):
    if not SUPABASE_URL or not SUPABASE_KEY: return
    url = f"{SUPABASE_URL}/rest/v1/users"
    headers = get_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    data = {"telegram_id": user_id, "username": username or "Anonimo", "points": 50, "trophies": []}
    try: requests.post(url, headers=headers, json=data)
    except: pass

def db_add_product(product_data):
    if not SUPABASE_URL or not SUPABASE_KEY: return False, "Mancano chiavi."
    url = f"{SUPABASE_URL}/rest/v1/products"
    try:
        r = requests.post(url, headers=get_headers(), json=product_data)
        if r.status_code in [200, 201]: return True, "OK"
        return False, f"Errore {r.status_code}"
    except Exception as e: return False, str(e)

def db_update_product(prod_id, update_data):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.patch(url, headers=get_headers(), json=update_data)
        return r.status_code in [200, 204]
    except: return False

def db_get_products():
    url = f"{SUPABASE_URL}/rest/v1/products?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200: return r.json()
        return []
    except: return []

def db_toggle_product(prod_id, current_status):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.patch(url, headers=get_headers(), json={"in_showcase": not current_status})
        return r.status_code in [200, 204]
    except: return False

def db_delete_product(prod_id):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try:
        r = requests.delete(url, headers=get_headers())
        return r.status_code in [200, 204]
    except: return False

def db_save_order(user_id, username, cart, total, address):
    url = f"{SUPABASE_URL}/rest/v1/orders"
    data = {"user_id": user_id, "username": username or "Anonimo", "items": cart, "total_price": total, "address": address, "status": "PENDING"}
    try:
        r = requests.post(url, headers=get_headers(), json=data)
        if r.status_code in [200, 201]:
            res = r.json()
            if res: return res[0]["id"]
    except: pass
    return 999

def db_get_all_orders():
    url = f"{SUPABASE_URL}/rest/v1/orders?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200: return r.json()
        return []
    except: return []

def db_update_order_status(order_id, status, tracking=""):
    url = f"{SUPABASE_URL}/rest/v1/orders?id=eq.{order_id}"
    payload = {"status": status}
    if tracking: payload["tracking_code"] = tracking
    try:
        r = requests.patch(url, headers=get_headers(), json=payload)
        return r.status_code in [200, 204]
    except: return False

def db_update_user_points(target_id, points_delta):
    url = f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{target_id}"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and r.json():
            current_p = r.json()[0].get("points", 0)
            new_p = max(0, current_p + points_delta)
            requests.patch(url, headers=get_headers(), json={"points": new_p})
            return True, new_p
    except: pass
    return False, 0

def db_add_user_trophy(target_id, trophy_name):
    url = f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{target_id}"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and r.json():
            user = r.json()[0]
            trophies = user.get("trophies") or []
            if trophy_name not in trophies: trophies.append(trophy_name)
            requests.patch(url, headers=get_headers(), json={"trophies": trophies})
            return True, trophies
    except: pass
    return False, []

def upload_to_supabase_storage(file_bytes, mime_type, file_extension):
    filename = f"media_{int(time.time())}_{uuid.uuid4().hex[:6]}.{file_extension}"
    url = f"{SUPABASE_URL}/storage/v1/object/prodotti/{filename}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": mime_type}
    try:
        res = requests.post(url, headers=headers, data=file_bytes)
        if res.status_code in [200, 201]:
            return f"{SUPABASE_URL}/storage/v1/object/public/prodotti/{filename}"
    except: pass
    return None

# --- SERVER HTTP PER RENDER E VERCEL ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(b"Bot Il Falsario 100% Active")

    def do_POST(self):
        if self.path == '/api/order':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))

            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            address = data.get("address", "Nessuna nota")

            order_id = db_save_order(user_id, username, cart, total, address)
            items_text = "\n".join([f"• {i['qty']} - €{i['price']} ({i['name']})" for i in cart])

            user_msg = f"✅ Richiesta #{order_id} inviata al caveau!\n\n{items_text}\n📍 Note: {address}\nTotale: €{total}\n\nUn operatore prenderà in carico la tua richiesta a breve."
            if user_id and str(user_id) != "0":
                try: bot.send_message(int(user_id), user_msg)
                except: pass

            admin_msg = f"🚨 NUOVA RICHIESTA DAL CAVEAU! #{order_id}\n\n👤 Utente: @{username} (ID: {user_id})\n📍 Note: {address}\n\n📦 Dettagli Lavoro:\n{items_text}\n\n💰 Totale: €{total}"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Approva", callback_data=f"ord_acc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("❌ Respingi", callback_data=f"ord_cnc_{order_id}_{user_id}"),
                types.InlineKeyboardButton("🚚 Invia Info Extra", callback_data=f"ord_trk_{order_id}_{user_id}")
            )
            if ADMIN_ID and ADMIN_ID != 0:
                try: bot.send_message(ADMIN_ID, admin_msg, reply_markup=markup)
                except: pass

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"success": True, "order_id": order_id}).encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Gestione Caveau", callback_data="m_prod"),
        types.InlineKeyboardButton("🛒 Gestione Richieste", callback_data="m_ord"),
        types.InlineKeyboardButton("📜 Storico Pratiche", callback_data="m_hist"),
        types.InlineKeyboardButton("🏆 Gestione Privilegi", callback_data="m_pts")
    )
    return markup

def get_admin_prod_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Aggiungi Lavoro", callback_data="p_add"),
        types.InlineKeyboardButton("📋 Lista / Modifica / Elimina", callback_data="p_list"),
        types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main")
    )
    return markup

def get_cancel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Torna al Menu", callback_data="m_main"))
    return markup

def get_media_done_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("✅ Fine Caricamento Media", callback_data="done_media"),
        types.InlineKeyboardButton("🔙 Annulla", callback_data="m_main")
    )
    return markup

# --- COMANDI TELEGRAM ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    username = message.from_user.username
    db_register_user(user_id, username)

    welcome_text = (
        "🎭 Benvenuto ne Il Falsario!\n\n"
        "Massima serietà, discrezione totale e qualità impeccabile.\n"
        "Sei nel posto giusto per le tue necessità.\n\n"
        "🔐 Clicca in basso per accedere al caveau."
    )

    markup = types.InlineKeyboardMarkup()
    if WEB_APP_URL:
        markup.add(types.InlineKeyboardButton("🎭 Accedi al Caveau", web_app=types.WebAppInfo(WEB_APP_URL)))
    bot.send_message(user_id, welcome_text, reply_markup=markup)

@bot.message_handler(commands=['admin', 'cancel', 'menu'])
def admin_panel(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    user_states.pop(user_id, None)
    bot.send_message(user_id, "⚙️ PANNELLO GESTIONALE - IL FALSARIO 🎭", reply_markup=get_admin_main_keyboard())

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID: return
    data = call.data

    if data == "m_main":
        user_states.pop(user_id, None)
        bot.edit_message_text("⚙️ PANNELLO GESTIONALE", user_id, call.message.message_id, reply_markup=get_admin_main_keyboard())

    elif data == "m_prod":
        user_states.pop(user_id, None)
        bot.edit_message_text("📦 GESTIONE CAVEAU", user_id, call.message.message_id, reply_markup=get_admin_prod_keyboard())

    elif data == "m_ord":
        user_states.pop(user_id, None)
        bot.edit_message_text("🛒 GESTIONE RICHIESTE\nGli ordini arrivano in chat in tempo reale.", user_id, call.message.message_id, reply_markup=get_admin_main_keyboard())

    elif data == "m_hist":
        user_states.pop(user_id, None)
        orders = db_get_all_orders()
        if not orders:
            bot.send_message(user_id, "📭 Storico vuoto.", reply_markup=get_cancel_keyboard())
            return
        
        bot.send_message(user_id, f"📜 STORICO COMPLETO:")
        status_map = {"PENDING": "⏳ In Analisi", "ACCEPTED": "✅ Approvato", "SHIPPED": "🚚 Completato", "CANCELLED": "❌ Respinto"}

        for o in orders:
            st = status_map.get(o.get('status'), o.get('status'))
            items = o.get('items', [])
            if isinstance(items, str):
                try: items = json.loads(items)
                except: items = []
            
            items_str = "\n".join([f"  • {i['name']} - €{i['price']}" for i in items]) if items else "Nessun dettaglio"
            card_msg = f"🛒 PRATICA #{o.get('id')}\n👤 @{o.get('username')}\n📌 Stato: {st}\n\n📦 Dettagli:\n{items_str}\n💰 €{o.get('total_price')}"

            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Approva", callback_data=f"ord_acc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("❌ Respingi", callback_data=f"ord_cnc_{o['id']}_{o.get('user_id')}"),
                types.InlineKeyboardButton("🚚 Info Extra", callback_data=f"ord_trk_{o['id']}_{o.get('user_id')}")
            )
            try: bot.send_message(user_id, card_msg, reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Fine storico:", reply_markup=get_cancel_keyboard())

    elif data == "m_pts":
        user_states.pop(user_id, None)
        bot.send_message(user_id, "🏆 GESTIONE\n• /punti ID QUANTITA\n• /trofeo ID NOMETROFEO", reply_markup=get_cancel_keyboard())

    elif data == "p_add":
        user_states.pop(user_id, None)
        markup = types.InlineKeyboardMarkup(row_width=1)
        cats = ["Documenti", "Bancario", "Patenti", "Altro"]
        markup.add(*[types.InlineKeyboardButton(c, callback_data=f"addcat_{c}") for c in cats])
        markup.add(types.InlineKeyboardButton("🔙 Menu", callback_data="m_main"))
        bot.edit_message_text("Seleziona la categoria del file:", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("addcat_"):
        cat = data.replace("addcat_", "")
        user_states[user_id] = {"category": cat, "step": "WAITING_MEDIA", "media_list": []}
        bot.edit_message_text(f"Categoria: {cat}\n\n📸 Invia Foto/Video come anteprima del lavoro.", user_id, call.message.message_id, reply_markup=get_media_done_keyboard())

    elif data == "p_list":
        user_states.pop(user_id, None)
        prods = db_get_products()
        if not prods:
            bot.send_message(user_id, "📭 Nessun elemento.", reply_markup=get_cancel_keyboard())
            return
            
        for p in prods:
            st_val = p.get('in_showcase', True)
            status_str = '🟢 Visibile' if st_val else '🔴 Nascosto'
            msg = f"📦 {p.get('name')}\n🏷 {p.get('category')}\n👁 {status_str}"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p['id']}_{st_val}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p['id']}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}"))
            bot.send_message(user_id, msg, reply_markup=markup)
        bot.send_message(user_id, "👇 Opzioni:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edit_"):
        p_id = data.split("_")[1]
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(
            types.InlineKeyboardButton("✏️ Modifica Nome", callback_data=f"edname_{p_id}"),
            types.InlineKeyboardButton("📝 Modifica Descrizione", callback_data=f"eddesc_{p_id}"),
            types.InlineKeyboardButton("💰 Modifica Dettagli", callback_data=f"edprc_{p_id}"),
            types.InlineKeyboardButton("📸 Sostituisci File", callback_data=f"edmedia_{p_id}"),
            types.InlineKeyboardButton("🔙 Indietro", callback_data="p_list")
        )
        bot.edit_message_text("Seleziona opzione di modifica:", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("edname_"):
        user_states[user_id] = {"step": "EDIT_NAME", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "✏️ Scrivi il NUOVO NOME:", reply_markup=get_cancel_keyboard())

    elif data.startswith("eddesc_"):
        user_states[user_id] = {"step": "EDIT_DESC", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "📝 Scrivi la NUOVA DESCRIZIONE:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edprc_"):
        user_states[user_id] = {"step": "EDIT_PRICES", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "💰 Scrivi le OPZIONI.\nEsempio: Servizio Base - 50, Completo - 100", reply_markup=get_cancel_keyboard())

    elif data.startswith("edmedia_"):
        user_states[user_id] = {"step": "WAITING_MEDIA_EDIT", "target_product": data.split("_")[1], "media_list": []}
        bot.send_message(user_id, "📸 Invia nuove foto/video.", reply_markup=get_media_done_keyboard())

    elif data.startswith("tog_"):
        parts = data.split("_")
        p_id, curr_st = parts[1], parts[2] == 'True'
        if db_toggle_product(p_id, curr_st):
            bot.answer_callback_query(call.id, "✅ Aggiornato!")
            new_text = call.message.text.replace("🟢 Visibile", "🔴 Nascosto") if "🟢 Visibile" in call.message.text else call.message.text.replace("🔴 Nascosto", "🟢 Visibile")
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p_id}_{not curr_st}"), types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p_id}"))
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p_id}"))
            try: bot.edit_message_text(new_text, user_id, call.message.message_id, reply_markup=markup)
            except: pass

    elif data.startswith("del_"):
        if db_delete_product(data.split("_")[1]):
            bot.answer_callback_query(call.id, "🗑️ Eliminato!")
            try: bot.delete_message(user_id, call.message.message_id)
            except: pass

    elif data == "done_media":
        st = user_states.get(user_id, {})
        if not st.get("media_list"):
            bot.answer_callback_query(call.id, "❌ Invia almeno un file!", show_alert=True)
            return
        if st.get("step") == "WAITING_MEDIA":
            st["step"] = "WAITING_NAME"
            bot.send_message(user_id, f"✅ Caricati {len(st['media_list'])} file!\n📝 Invia il NOME del lavoro:", reply_markup=get_cancel_keyboard())
        elif st.get("step") == "WAITING_MEDIA_EDIT":
            p_id = st["target_product"]
            db_update_product(p_id, {"media_list": st["media_list"], "media_url": st["media_list"][0]["url"], "media_type": st["media_list"][0]["type"]})
            bot.send_message(user_id, "✅ File aggiornati nel caveau!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)

    elif data.startswith("ord_acc_"):
        o_id, u_id = data.split("_")[2], data.split("_")[3]
        db_update_order_status(o_id, "ACCEPTED")
        if u_id and u_id != "0":
            try: bot.send_message(int(u_id), f"✅ La tua pratica #{o_id} è stata approvata!")
            except: pass
        bot.answer_callback_query(call.id, "✅ Approvato!")

    elif data.startswith("ord_cnc_"):
        o_id, u_id = data.split("_")[2], data.split("_")[3]
        db_update_order_status(o_id, "CANCELLED")
        if u_id and u_id != "0":
            try: bot.send_message(int(u_id), f"❌ Attenzione: Pratica #{o_id} respinta.")
            except: pass
        bot.answer_callback_query(call.id, "❌ Respinto!")

    elif data.startswith("ord_trk_"):
        o_id, u_id = data.split("_")[2], data.split("_")[3]
        user_states[user_id] = {"step": "WAITING_TRACKING", "target_order": o_id, "target_user": u_id}
        bot.send_message(user_id, f"🚚 Invia info o link extra per la pratica #{o_id}:", reply_markup=get_cancel_keyboard())

@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    state = user_states.get(user_id, {})
    if state.get("step") not in ["WAITING_MEDIA", "WAITING_MEDIA_EDIT"]: return

    wait_msg = bot.reply_to(message, "⏳ Salvataggio sicuro in corso...")
    if message.photo:
        file_id, media_type, mime, ext = message.photo[-1].file_id, 'image', 'image/jpeg', 'jpg'
    else:
        file_id, media_type, mime, ext = message.video.file_id, 'video', 'video/mp4', 'mp4'

    file_info = bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_info.file_path}"
    
    try:
        file_bytes = requests.get(file_url).content
        public_url = upload_to_supabase_storage(file_bytes, mime, ext)
        if public_url:
            if "media_list" not in user_states[user_id]: user_states[user_id]["media_list"] = []
            user_states[user_id]["media_list"].append({"url": public_url, "type": media_type})
            bot.edit_message_text(f"✅ Salvato nel caveau!\nFile #{len(user_states[user_id]['media_list'])} aggiunto.\nInvia altro o premi Fine.", user_id, wait_msg.message_id, reply_markup=get_media_done_keyboard())
        else:
            bot.edit_message_text("❌ Errore caricamento database.", user_id, wait_msg.message_id)
    except Exception as e: bot.edit_message_text(f"❌ Errore: {e}", user_id, wait_msg.message_id)

@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    if message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_user, qty = int(parts[1]), int(parts[2])
            ok, new_total = db_update_user_points(target_user, qty)
            if ok: bot.reply_to(message, f"✅ Livello Affidabilità aggiornato: {new_total}")
        except: bot.reply_to(message, "❌ /punti ID QUANTITA")
        return

    if message.text.startswith("/trofeo"):
        try:
            parts = message.text.split(" ", 2)
            ok, _ = db_add_user_trophy(int(parts[1]), parts[2])
            if ok: bot.reply_to(message, "✅ Fatto!")
        except: bot.reply_to(message, "❌ /trofeo ID NOME")
        return

    if step == "EDIT_NAME":
        db_update_product(state["target_product"], {"name": message.text})
        bot.reply_to(message, "✅ Nome aggiornato!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "EDIT_DESC":
        db_update_product(state["target_product"], {"description": message.text})
        bot.reply_to(message, "✅ Descrizione aggiornata!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "EDIT_PRICES":
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-").replace("):", "").replace(")", "").strip()
            prices = [{"qty": r.split("-")[0].strip(), "price": float(r.split("-")[1].replace("€", "").strip())} for r in clean_text.split(",") if "-" in r]
            if not prices: raise ValueError()
            db_update_product(state["target_product"], {"price_options": prices})
            bot.reply_to(message, "✅ Dettagli aggiornati!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)
        except: bot.reply_to(message, "❌ Errore. Esempio: Base - 50, Full - 100", reply_markup=get_cancel_keyboard())

    elif step == "WAITING_NAME":
        state["name"] = message.text
        state["step"] = "WAITING_DESC"
        bot.reply_to(message, "✍️ Salvato. Ora invia la DESCRIZIONE:", reply_markup=get_cancel_keyboard())

    elif step == "WAITING_DESC":
        state["desc"] = message.text
        state["step"] = "WAITING_PRICES"
        bot.reply_to(message, "💰 Ora invia le OPZIONI (Esempio: Base - 50, Full - 100):", reply_markup=get_cancel_keyboard())

    elif step == "WAITING_PRICES":
        try:
            clean_text = message.text.replace("–", "-").replace("—", "-").replace("):", "").replace(")", "").strip()
            prices = [{"qty": r.split("-")[0].strip(), "price": float(r.split("-")[1].replace("€", "").strip())} for r in clean_text.split(",") if "-" in r]
            if not prices: raise ValueError()
        except:
            bot.reply_to(message, "❌ Formato errato. Esempio: Base - 50, Full - 100", reply_markup=get_cancel_keyboard())
            return

        ml = state.get("media_list", [])
        payload = {"name": state["name"], "category": state["category"], "media_list": ml, "media_url": ml[0]["url"] if ml else "", "media_type": ml[0]["type"] if ml else "image", "price_options": prices, "description": state.get("desc", ""), "in_showcase": True}
        
        success, _ = db_add_product(payload)
        if success: bot.reply_to(message, f"🎉 CARICATO!\n📦 {state['name']}", reply_markup=get_admin_main_keyboard())
        else: bot.reply_to(message, "❌ ERRORE DATABASE", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    elif step == "WAITING_TRACKING":
        db_update_order_status(state["target_order"], "SHIPPED", message.text.strip())
        bot.reply_to(message, "✅ Dettagli extra inviati al cliente!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

# --- AVVIO IN PARALLELO (SERVER + BOT) ---
if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    
    print("🤖 Avvio Bot Il Falsario in corso...")
    while True:
        try:
            bot.remove_webhook()
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            time.sleep(3)
