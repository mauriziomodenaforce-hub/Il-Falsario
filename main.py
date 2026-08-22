import os
import json
import time
import threading
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import telebot
from telebot import types

# ======================================================
# VARIABILI D'AMBIENTE
# ======================================================
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '8833925901:AAG8tjYJgWvKEniJgf_exmt_Ij6t2mG3YLU').strip()
WEB_APP_URL = os.environ.get('WEB_APP_URL', 'https://il-falsario.onrender.com').strip()
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://niyhpvtiefisycxbkjie.supabase.co').strip().rstrip('/')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5peWhwdnRpZWZpc3ljeGJramllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODY4MjQ2MDUsImV4cCI6MjEwMjQwMDYwNX0.ODucTrP0dzByGNhyyIYv-S-kIH5cTs8X_Url-7jXRMY').strip()
ADMIN_ID = int(os.environ.get('ADMIN_ID', 8716217678))

bot = telebot.TeleBot(TELEGRAM_TOKEN)
user_states = {}

# ======================================================
# HELPER SUPABASE REST API (TUTTO INTATTO)
# ======================================================
def get_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

def db_register_user(user_id, username):
    url = f"{SUPABASE_URL}/rest/v1/users"
    headers = get_headers()
    headers["Prefer"] = "resolution=merge-duplicates"
    data = {"telegram_id": user_id, "username": username or "Anonimo", "points": 50, "trophies": []}
    try: requests.post(url, headers=headers, json=data)
    except: pass

def db_add_product(product_data):
    url = f"{SUPABASE_URL}/rest/v1/products"
    try:
        r = requests.post(url, headers=get_headers(), json=product_data)
        if r.status_code in [200, 201]: return True, "OK"
        else: return False, f"Errore HTTP {r.status_code}: {r.text}"
    except Exception as e: return False, str(e)

def db_update_product(prod_id, update_data):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try: return requests.patch(url, headers=get_headers(), json=update_data).status_code in [200, 204]
    except: return False

def db_get_products():
    url = f"{SUPABASE_URL}/rest/v1/products?select=*&order=created_at.desc"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200: return r.json()
        return []
    except: return []

def db_toggle_product(prod_id, current_status):
    return db_update_product(prod_id, {"in_showcase": not current_status})

def db_delete_product(prod_id):
    url = f"{SUPABASE_URL}/rest/v1/products?id=eq.{prod_id}"
    try: return requests.delete(url, headers=get_headers()).status_code in [200, 204]
    except: return False

def db_update_user_points(target_id, points_delta):
    url = f"{SUPABASE_URL}/rest/v1/users?telegram_id=eq.{target_id}"
    try:
        r = requests.get(url, headers=get_headers())
        if r.status_code == 200 and len(r.json()) > 0:
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
        if r.status_code == 200 and len(r.json()) > 0:
            user = r.json()[0]
            trophies = user.get("trophies") or []
            if trophy_name not in trophies: trophies.append(trophy_name)
            requests.patch(url, headers=get_headers(), json={"trophies": trophies})
            return True, trophies
    except: pass
    return False, []

# --- LOGICA ORDINI (AGGIORNATA PER DASHBOARD E LIVE EDIT) ---
def db_save_order(user_id, username, cart, total, address, order_type):
    url = f"{SUPABASE_URL}/rest/v1/orders"
    data = {
        "user_id": user_id,
        "username": username or "Anonimo",
        "items": cart,
        "total_price": total,
        "address": address,
        "status": "PENDING",
        "order_type": order_type
    }
    try:
        r = requests.post(url, headers=get_headers(), json=data)
        if r.status_code in [200, 201]:
            res = r.json()
            if res: return res[0]["id"]
    except: pass
    return 999

def db_update_order_msg_id(order_id, msg_id):
    url = f"{SUPABASE_URL}/rest/v1/orders?id=eq.{order_id}"
    try: requests.patch(url, headers=get_headers(), json={"user_message_id": msg_id})
    except: pass

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
    try: return requests.patch(url, headers=get_headers(), json=payload).status_code in [200, 204]
    except: return False

# ======================================================
# STORAGE SUPABASE
# ======================================================
def upload_to_supabase_storage(file_bytes, mime_type, file_extension):
    filename = f"media_{int(time.time())}_{uuid.uuid4().hex[:6]}.{file_extension}"
    url = f"{SUPABASE_URL}/storage/v1/object/prodotti/{filename}"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": mime_type}
    try:
        res = requests.post(url, headers=headers, data=file_bytes)
        if res.status_code in [200, 201]:
            public_url = f"{SUPABASE_URL}/storage/v1/object/public/prodotti/{filename}"
            return public_url, "OK"
        else: return None, f"Codice Errore: {res.status_code}\nDettaglio: {res.text}"
    except Exception as e: return None, str(e)

# ======================================================
# API SERVER (RICEZIONE ORDINI DALLA MINI APP)
# ======================================================
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
        self.wfile.write(b"Bot & Admin Panel 100% Active")

    def do_POST(self):
        if self.path == '/api/order':
            content_length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(content_length).decode('utf-8'))

            cart = data.get("cart", [])
            total = data.get("total", 0)
            user_id = data.get("user_id")
            username = data.get("username", "Anonimo")
            address = data.get("address", "Non specificato")

            # RILEVAMENTO AUTOMATICO: SERVIZIO O PRODOTTO FISICO?
            is_service = any("serviz" in str(i.get('name', '')).lower() or "serviz" in str(i.get('category', '')).lower() for i in cart)
            order_type = "SERVICE" if is_service else "PHYSICAL"

            order_id = db_save_order(user_id, username, cart, total, address, order_type)
            items_text = "\n".join([f"• {i['qty']}x {i['name']} - €{i['price']}" for i in cart])

            # RICEVUTA UTENTE (Da modificare LIVE successivamente)
            user_msg = (
                f"✅ <b>Richiesta #{order_id} Registrata!</b>\n\n"
                f"📦 <b>Riepilogo:</b>\n{items_text}\n\n"
                f"📍 <b>Recapito:</b> {address}\n"
                f"💰 <b>Totale:</b> €{total}\n\n"
                f"⏳ <i>Un operatore sta elaborando la tua richiesta. Riceverai aggiornamenti direttamente su questo messaggio.</i>"
            )
            
            if user_id and str(user_id) != "0":
                try:
                    sent = bot.send_message(int(user_id), user_msg, parse_mode="HTML")
                    db_update_order_msg_id(order_id, sent.message_id) # Memorizza per le modifiche LIVE
                except: pass

            # ALERT ADMIN SILENZIOSO (NO SPAM BOTTONI IN CHAT)
            if ADMIN_ID and ADMIN_ID != 0:
                try:
                    alert_type = "🛠 NUOVO SERVIZIO" if is_service else "📦 NUOVO ORDINE FISICO"
                    bot.send_message(ADMIN_ID, f"🔔 <b>{alert_type} RICEVUTO!</b>\nOrdine #{order_id} da @{username}.\n👉 Apri il menù /admin per gestirlo.", parse_mode="HTML")
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


# ======================================================
# TASTIERE GESTIONALI ADMIN
# ======================================================
def get_admin_main_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("📦 Gestione Ordini (Fisici)", callback_data="dash_ord_phys"),
        types.InlineKeyboardButton("🛠 Gestione Servizi (Digitali)", callback_data="dash_ord_serv"),
        types.InlineKeyboardButton("🛍 Gestione Prodotti & Media", callback_data="m_prod"),
        types.InlineKeyboardButton("📜 Storico Completo Ordini", callback_data="m_hist"),
        types.InlineKeyboardButton("💎 Gestione Punti Utenti", callback_data="m_pts")
    )
    return markup

def get_admin_prod_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton("➕ Aggiungi Prodotto", callback_data="p_add"),
        types.InlineKeyboardButton("📋 Lista / Modifica / Elimina", callback_data="p_list"),
        types.InlineKeyboardButton("🔙 Torna al Menu Principale", callback_data="m_main")
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
        types.InlineKeyboardButton("🔙 Annulla e Torna al Menu", callback_data="m_main")
    )
    return markup


# ======================================================
# COMANDI BASE
# ======================================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    username = message.from_user.username
    db_register_user(user_id, username)

    welcome_text = (
        "Benvenuto nello shop ufficiale del Falsario 🤗🎭\n\n"
        "💬 Contatto Telegram Ufficiale: @il_falsario_ufficiale_x2\n\n"
        "📲 Contatto Signal Ufficiale: https://signal.me/#eu/m7lTtwu9GCr8RJQ7mhQ2OkwVfT_MZvjG6g-PFCnS8dG9NBl3s09GYKPtiyRQz-ih\n\n"
        "📲 Contatto Session Ufficiale: 05495e45a9c1ced74358dcedaad80c99956e1405fbbccf4f8e85f0ca873946a515\n\n\n"
        "📢 Canale Feedback: https://t.me/+eRPnJSZq485kMzdk\n\n"
        "Massima serietà, discrezione totale e qualità impeccabile.\n"
        "Sei nel posto giusto per le tue necessità.\n\n"
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
    bot.send_message(user_id, "⚙️ <b>PANNELLO GESTIONALE - IL FALSARIO</b> 🎭\n\nScegli la sezione da gestire:", parse_mode="HTML", reply_markup=get_admin_main_keyboard())


# ======================================================
# GESTIONE PULSANTI INLINE E DASHBOARD
# ======================================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    user_id = call.message.chat.id
    if user_id != ADMIN_ID: return
    data = call.data

    # --- MENU PRINCIPALE ---
    if data == "m_main":
        user_states.pop(user_id, None)
        bot.edit_message_text("⚙️ <b>PANNELLO GESTIONALE AMMINISTRATORE</b>", user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_admin_main_keyboard())

    # ==================================================
    # DASHBOARD 1: ORDINI FISICI
    # ==================================================
    elif data == "dash_ord_phys":
        user_states.pop(user_id, None)
        orders = [o for o in db_get_all_orders() if o.get('status') == 'PENDING' and o.get('order_type') != 'SERVICE']
        bot.edit_message_text("📦 <b>ORDINI FISICI IN ATTESA</b>\nOrdini da spedire o consegnare a mano:", user_id, call.message.message_id, parse_mode="HTML")
        
        if not orders:
            bot.send_message(user_id, "✅ Nessun ordine fisico in coda.", reply_markup=get_cancel_keyboard())
            return
            
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            msg = (
                f"🛒 <b>ORDINE #{o.get('id')}</b>\n"
                f"👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 Indirizzo: {o.get('address', 'N/D')}\n\n"
                f"📦 Prodotti:\n{items_str}\n\n"
                f"💰 Totale: €{o.get('total_price')}"
            )
            
            m_id = o.get('user_message_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("✅ Accetta", callback_data=f"act_acc_{o['id']}_{o.get('user_id')}_{m_id}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"act_cnc_{o['id']}_{o.get('user_id')}_{m_id}"),
                types.InlineKeyboardButton("🚚 Invia Tracking", callback_data=f"act_trk_{o['id']}_{o.get('user_id')}_{m_id}"),
                types.InlineKeyboardButton("📍 Conferma Meet up", callback_data=f"act_meet_{o['id']}_{o.get('user_id')}_{m_id}")
            )
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Opzioni:", reply_markup=get_cancel_keyboard())

    # ==================================================
    # DASHBOARD 2: SERVIZI DIGITALI/ELABORAZIONI
    # ==================================================
    elif data == "dash_ord_serv":
        user_states.pop(user_id, None)
        orders = [o for o in db_get_all_orders() if o.get('status') == 'PENDING' and o.get('order_type') == 'SERVICE']
        bot.edit_message_text("🛠 <b>SERVIZI IN LAVORAZIONE</b>\nRichieste di servizi, patenti, documenti, ecc:", user_id, call.message.message_id, parse_mode="HTML")
        
        if not orders:
            bot.send_message(user_id, "✅ Nessun servizio in coda.", reply_markup=get_cancel_keyboard())
            return
            
        for o in orders:
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            msg = (
                f"🛠 <b>SERVIZIO #{o.get('id')}</b>\n"
                f"👤 Utente: @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 Dati forniti: {o.get('address', 'N/D')}\n\n"
                f"📦 Servizio richiesto:\n{items_str}\n\n"
                f"💰 Totale: €{o.get('total_price')}"
            )
            
            m_id = o.get('user_message_id', 0)
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("⏳ Segna In Lavorazione", callback_data=f"act_work_{o['id']}_{o.get('user_id')}_{m_id}"),
                types.InlineKeyboardButton("❌ Annulla", callback_data=f"act_cnc_{o['id']}_{o.get('user_id')}_{m_id}"),
                types.InlineKeyboardButton("📤 Invia Esito / File", callback_data=f"act_file_{o['id']}_{o.get('user_id')}_{m_id}")
            )
            try: bot.send_message(user_id, msg, parse_mode="HTML", reply_markup=markup)
            except: pass
        bot.send_message(user_id, "👇 Opzioni:", reply_markup=get_cancel_keyboard())

    # ==================================================
    # AZIONI "LIVE EDIT" VERSO L'UTENTE
    # ==================================================
    elif data.startswith("act_trk_") or data.startswith("act_file_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        step_name = "WAITING_TRACKING" if action == "trk" else "WAITING_FILE_INFO"
        prompt_txt = f"🚚 Scrivi il <b>TRACKING / NOTE</b> per l'ordine #{o_id}:" if action == "trk" else f"📤 Scrivi l'<b>ESITO / LINK AL FILE</b> per il servizio #{o_id}:"
        
        user_states[user_id] = {"step": step_name, "o_id": o_id, "u_id": u_id, "m_id": m_id}
        bot.send_message(user_id, prompt_txt, parse_mode="HTML", reply_markup=get_cancel_keyboard())
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass

    elif data.startswith("act_acc_") or data.startswith("act_work_") or data.startswith("act_cnc_") or data.startswith("act_meet_"):
        parts = data.split("_")
        action, o_id, u_id, m_id = parts[1], parts[2], parts[3], parts[4]
        
        if action == "acc":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"✅ <b>ORDINE #{o_id} CONFERMATO</b>\n\nIl tuo ordine è in preparazione."
        elif action == "work":
            db_update_order_status(o_id, "ACCEPTED")
            new_text = f"⏳ <b>SERVIZIO #{o_id} IN LAVORAZIONE</b>\n\nStiamo elaborando i tuoi dati."
        elif action == "cnc":
            db_update_order_status(o_id, "CANCELLED")
            new_text = f"❌ <b>ATTENZIONE</b>\nIl tuo ordine/servizio #{o_id} è stato annullato dal sistema."
        elif action == "meet":
            db_update_order_status(o_id, "ACCEPTED", "Meet Up Confermato")
            new_text = f"📍 <b>MEET UP #{o_id} CONFERMATO</b>\n\nL'incontro è approvato. Un operatore ti scriverà a breve per i dettagli."

        # ESECUZIONE LIVE EDIT NELLA CHAT DEL CLIENTE
        if u_id and u_id != "0":
            try:
                if m_id and m_id != "0":
                    bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else:
                    bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass # Evita crash se l'utente ha bloccato il bot o cancellato il messaggio
        
        bot.answer_callback_query(call.id, "✅ Azione completata e cliente aggiornato LIVE!")
        try: bot.edit_message_reply_markup(user_id, call.message.message_id, reply_markup=None)
        except: pass


    # ==================================================
    # SEZIONE STORICO, PUNTI E PRODOTTI (INTATTA)
    # ==================================================
    elif data == "m_hist":
        user_states.pop(user_id, None)
        orders = db_get_all_orders()
        if not orders:
            bot.send_message(user_id, "📭 Nessun ordine presente nello storico.", reply_markup=get_cancel_keyboard())
            return
        
        bot.send_message(user_id, f"📜 <b>MASTRO LIBERO - STORICO COMPLETO</b> ({len(orders)} totali):", parse_mode="HTML")
        status_map = {"PENDING": "⏳ In Attesa", "ACCEPTED": "✅ Confermato", "SHIPPED": "🚚 Completato/Spedito", "CANCELLED": "❌ Annullato"}

        for o in orders:
            st = status_map.get(o.get('status'), o.get('status'))
            items = json.loads(o.get('items', '[]')) if isinstance(o.get('items'), str) else o.get('items', [])
            items_str = "\n".join([f"  • {i['name']} ({i['qty']}) - €{i['price']}" for i in items]) if items else "  • Nessun dettaglio"
            
            msg = (
                f"🛒 ORDINE #{o.get('id')} [{st}]\n"
                f"👤 @{o.get('username')} (ID: {o.get('user_id')})\n"
                f"📍 {o.get('address', 'N/D')}\n"
                f"🚚 Note: {o.get('tracking_code', 'N/D')}\n\n"
                f"📦 Prodotti:\n{items_str}\n"
                f"💰 €{o.get('total_price')}\n"
                f"────────────────────────"
            )
            try: bot.send_message(user_id, msg)
            except: pass
        bot.send_message(user_id, "👇 Fine del registro storico:", reply_markup=get_cancel_keyboard())

    elif data == "m_pts":
        user_states.pop(user_id, None)
        msg = (
            "💎 <b>GESTIONE PUNTI UTENTI</b>\n\n"
            "• Assegna o scala punti usando il comando in chat:\n"
            "<code>/punti ID_UTENTE QUANTITA</code>\n\n"
            "(Esempio per aggiungere: <code>/punti 123456789 100</code>)\n"
            "(Esempio per scalare: <code>/punti 123456789 -50</code>)"
        )
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
        bot.edit_message_text(
            f"Categoria: {cat}\n\n📸 Invia ORA una o più Foto/Video del prodotto.\nPuoi inviarne quanti ne vuoi. Quando hai finito, premi <b>✅ Fine Caricamento Media</b> in basso.",
            user_id, call.message.message_id, parse_mode="HTML", reply_markup=get_media_done_keyboard()
        )

    elif data == "p_list":
        user_states.pop(user_id, None)
        prods = db_get_products()
        if not prods:
            bot.send_message(user_id, "📭 Nessun prodotto presente nel database.", reply_markup=get_cancel_keyboard())
            return
            
        for p in prods:
            st_val = p.get('in_showcase', True)
            status_str = '🟢 In Vetrina' if st_val else '🔴 Nascosto'
            msg = f"📦 {p.get('name')}\n🏷 Categoria: {p.get('category')}\n👁 Stato: {status_str}"
            
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton("👁️ On/Off", callback_data=f"tog_{p['id']}_{st_val}"),
                types.InlineKeyboardButton("✏️ Modifica", callback_data=f"edit_{p['id']}")
            )
            markup.add(types.InlineKeyboardButton("🗑️ Elimina", callback_data=f"del_{p['id']}"))
            bot.send_message(user_id, msg, reply_markup=markup)
            
        bot.send_message(user_id, "👇 Opzioni di navigazione:", reply_markup=get_cancel_keyboard())

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
        bot.edit_message_text("Cosa vuoi modificare di questo prodotto?", user_id, call.message.message_id, reply_markup=markup)

    elif data.startswith("edname_"):
        user_states[user_id] = {"step": "EDIT_NAME", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "✏️ Scrivi il NUOVO NOME per questo prodotto:", reply_markup=get_cancel_keyboard())

    elif data.startswith("eddesc_"):
        user_states[user_id] = {"step": "EDIT_DESC", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "📝 Scrivi la NUOVA DESCRIZIONE per questo prodotto:", reply_markup=get_cancel_keyboard())

    elif data.startswith("edprc_"):
        user_states[user_id] = {"step": "EDIT_PRICES", "target_product": data.split("_")[1]}
        bot.send_message(user_id, "💰 Scrivi le NUOVE VARIANTI DI PREZZO (Separate da virgola).\nEsempio: 10pz 140, 20pz 250", reply_markup=get_cancel_keyboard())

    elif data.startswith("edmedia_"):
        user_states[user_id] = {"step": "WAITING_MEDIA_EDIT", "target_product": data.split("_")[1], "media_list": []}
        bot.send_message(user_id, "📸 Invia ORA le nuove foto/video (questo cancellerà quelle vecchie). Premi Fine quando hai caricato tutto.", reply_markup=get_media_done_keyboard())

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
            bot.answer_callback_query(call.id, "🗑️ Prodotto eliminato definitivamente!")
            try: bot.delete_message(user_id, call.message.message_id)
            except: pass

    elif data == "done_media":
        st = user_states.get(user_id, {})
        if not st.get("media_list"):
            bot.answer_callback_query(call.id, "❌ Invia almeno un file multimediale!", show_alert=True)
            return
        
        if st.get("step") == "WAITING_MEDIA":
            st["step"] = "WAITING_NAME"
            bot.send_message(user_id, f"✅ Hai caricato {len(st['media_list'])} file!\n\n📝 Ora invia il NOME del prodotto:", reply_markup=get_cancel_keyboard())
        elif st.get("step") == "WAITING_MEDIA_EDIT":
            media_list = st["media_list"]
            db_update_product(st["target_product"], {"media_list": media_list, "media_url": media_list[0]["url"], "media_type": media_list[0]["type"]})
            bot.send_message(user_id, "✅ Foto/Video aggiornati con successo!", reply_markup=get_admin_main_keyboard())
            user_states.pop(user_id, None)

# ======================================================
# HANDLER MEDIA (CARICAMENTO PRODOTTI)
# ======================================================
@bot.message_handler(content_types=['photo', 'video'])
def handle_media(message):
    user_id = message.chat.id
    if user_id != ADMIN_ID: return
    state = user_states.get(user_id, {})
    if state.get("step") not in ["WAITING_MEDIA", "WAITING_MEDIA_EDIT"]: return

    wait_msg = bot.reply_to(message, "⏳ Elaborazione e invio al server Supabase in corso...")

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
        
        public_url, err = upload_to_supabase_storage(file_bytes, mime, ext)
        if public_url:
            if "media_list" not in user_states[user_id]: user_states[user_id]["media_list"] = []
            user_states[user_id]["media_list"].append({"url": public_url, "type": media_type})
            tot = len(user_states[user_id]["media_list"])
            bot.edit_message_text(f"✅ Salvato per sempre!\n📸 Media #{tot} aggiunto.\nContinua o premi Fine.", user_id, wait_msg.message_id, reply_markup=get_media_done_keyboard())
        else:
            bot.edit_message_text(f"❌ ERRORE SUPABASE:\n{err}", user_id, wait_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Errore scaricamento: {e}", user_id, wait_msg.message_id)

# ======================================================
# HANDLER TESTO (INPUT ADMIN & GESTIONE LIVE)
# ======================================================
@bot.message_handler(func=lambda m: m.chat.id == ADMIN_ID)
def handle_admin_text(message):
    user_id = message.chat.id
    state = user_states.get(user_id, {})
    step = state.get("step")

    # Comando manuale Punti Utente
    if message.text and message.text.startswith("/punti"):
        try:
            parts = message.text.split()
            target_user, qty = int(parts[1]), int(parts[2])
            ok, new_total = db_update_user_points(target_user, qty)
            if ok:
                bot.reply_to(message, f"✅ L'utente {target_user} ora ha {new_total} punti.")
                try: bot.send_message(target_user, f"💎 <b>Aggiornamento Caveau:</b> il tuo saldo attuale è di {new_total} punti.", parse_mode="HTML")
                except: pass
            else: bot.reply_to(message, "❌ Errore: Utente non trovato.")
        except: bot.reply_to(message, "❌ Errore sintassi. Usa: /punti 123456789 100")
        return

    # MODIFICA LIVE TRACKING / INFO SERVIZI
    if step in ["WAITING_TRACKING", "WAITING_FILE_INFO"]:
        admin_text = message.text.strip()
        o_id, u_id, m_id = state["o_id"], state["u_id"], state["m_id"]
        
        db_update_order_status(o_id, "SHIPPED", admin_text)
        
        title = "🚚 <b>ORDINE SPEDITO</b>" if step == "WAITING_TRACKING" else "✅ <b>SERVIZIO COMPLETATO</b>"
        label = "Tracking / Istruzioni:" if step == "WAITING_TRACKING" else "Esito / Link al Documento:"
        new_text = f"{title}\n<i>Rif. #{o_id}</i>\n\n<b>{label}</b>\n<code>{admin_text}</code>\n\nGrazie per aver scelto Il Falsario 🎭"

        if u_id and str(u_id) != "0":
            try:
                if m_id and str(m_id) != "0": bot.edit_message_text(chat_id=int(u_id), message_id=int(m_id), text=new_text, parse_mode="HTML")
                else: bot.send_message(int(u_id), new_text, parse_mode="HTML")
            except: pass
                
        bot.reply_to(message, "✅ Messaggio aggiornato LIVE nella chat del cliente!", reply_markup=get_admin_main_keyboard())
        user_states.pop(user_id, None)

    # CREAZIONE/MODIFICA PRODOTTI (TUTTO INTATTO)
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
                
            if not prices: raise ValueError("Nessun formato valido.")
            
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
    print("🤖 Bot Il Falsario (Sistema Gestione Pro V2) Avviato e allineato!")
    while True:
        try:
            bot.remove_webhook()
            time.sleep(2)
            bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20)
        except Exception as e:
            time.sleep(5)
