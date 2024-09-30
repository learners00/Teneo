import json
import requests
from fake_useragent import UserAgent
import websocket
import threading
import time
from datetime import datetime, timedelta
from telegram import Bot
import asyncio
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# URL templates
TOKEN_URL_TEMPLATE = "https://ikknngrgxuxgjhplbpey.supabase.co/auth/v1/token?grant_type=password"
USER_INFO_URL_TEMPLATE = "https://ikknngrgxuxgjhplbpey.supabase.co/auth/v1/user"
PROFILE_INFO_URL_TEMPLATE = "https://ikknngrgxuxgjhplbpey.supabase.co/rest/v1/profiles?select=personal_code&id=eq.{user_id}"
WEBSOCKET_URL_TEMPLATE = "wss://secure.ws.teneo.pro/websocket?userId={user_id}&version=v0.2"

# Global variables for message rate limiting
LAST_MESSAGE_TIME = None
MESSAGE_COOLDOWN = 10  # seconds

def load_config(file_path):
    try:
        with open(file_path, 'r') as f:
            print(f"{Fore.GREEN}[INFO] Loading configuration...")
            return json.load(f)
    except FileNotFoundError:
        print(f"{Fore.RED}[ERROR] Config file not found. Please ensure 'config.json' is in the correct path.")
        return None
    except json.JSONDecodeError:
        print(f"{Fore.RED}[ERROR] Failed to parse config file. Please ensure it is valid JSON.")
        return None

async def send_telegram_message(global_config, message, parse_mode="Markdown"):
    global LAST_MESSAGE_TIME
    
    # Check if message cooldown has passed
    current_time = time.time()
    if LAST_MESSAGE_TIME is not None and current_time - LAST_MESSAGE_TIME < MESSAGE_COOLDOWN:
        print(f"{Fore.YELLOW}[INFO] Message rate limit hit, skipping message.")
        return
    
    bot_token = global_config.get('telegram_bot_token', '')
    chat_id = global_config.get('chat_id', '')
    
    if not bot_token or not chat_id:
        print(f"{Fore.RED}[ERROR] Telegram bot token or chat ID not provided.")
        return
    
    bot = Bot(token=bot_token)
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode=parse_mode)
        LAST_MESSAGE_TIME = current_time
    except Exception as e:
        print(f"{Fore.RED}[ERROR] Failed to send message: {e}")

def login_and_get_jwt(account_config, global_config):
    url = TOKEN_URL_TEMPLATE
    ua = UserAgent()
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": ua.random,
        "Apikey": global_config.get("api_key", "")
    }
    payload = {
        "email": account_config.get("email", ""),
        "password": account_config.get("password", ""),
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        token = response.json().get("access_token")
        if token:
            print(f"{Fore.GREEN}[INFO] Login successful for email: {account_config['email']}")
            return token
        else:
            print(f"{Fore.RED}[ERROR] Failed to retrieve access token for email: {account_config['email']}")
            return None
    except requests.RequestException as e:
        print(f"{Fore.RED}[ERROR] Network error during login: {e}")
        return None

def fetch_user_info(jwt_token, global_config):
    url = USER_INFO_URL_TEMPLATE
    ua = UserAgent()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "*/*",
        "User-Agent": ua.random,
        "Apikey": global_config.get("api_key", "")
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        user_info = response.json()
        message = (
            f"*User Information:*\n"
            f"- Email: `{user_info['email']}`\n"
            f"- ID: `{user_info['id']}`"
        )
        print(f"{Fore.CYAN}{message}")
        return user_info, message
    except requests.RequestException as e:
        print(f"{Fore.RED}[ERROR] Network error when fetching user info: {e}")
        return None, f"[ERROR] Network error when fetching user info."

def fetch_profile_info(jwt_token, user_id, global_config):
    url = PROFILE_INFO_URL_TEMPLATE.format(user_id=user_id)
    ua = UserAgent()
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "*/*",
        "User-Agent": ua.random,
        "Apikey": global_config.get("api_key", "")
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        profile_info = response.json()
        if profile_info:
            message = f"*Profile Code:* `{profile_info[0]['personal_code']}`"
            print(f"{Fore.CYAN}{message}")
            return profile_info, message
        else:
            print(f"{Fore.RED}[ERROR] No profile information found.")
            return None, "[ERROR] No profile information found."
    except requests.RequestException as e:
        print(f"{Fore.RED}[ERROR] Network error when fetching profile info: {e}")
        return None, f"[ERROR] Network error when fetching profile info."

class Node:
    def __init__(self, node_id, jwt_token, account_config, user_id, global_config):
        self.node_id = node_id
        self.jwt_token = jwt_token
        self.account_config = account_config
        self.global_config = global_config
        self.user_id = user_id
        self.daily_points = 0
        self.last_reset = datetime.now()
        self.connected = False
        self.last_heartbeat = datetime.now()
        self.ws = None
        self.custom_payload_data = {}

    def set_custom_payload_data(self, profile_info):
        if profile_info:
            self.custom_payload_data = {
                "personal_code": profile_info[0].get("personal_code", "N/A"),
            }

    def reset_daily_points(self):
        if datetime.now() - self.last_reset >= timedelta(days=1):
            self.daily_points = 0
            self.last_reset = datetime.now()
            print(f"{Fore.GREEN}[INFO] Daily points reset for Node {self.node_id}.")

    async def distribute_points(self):
        self.reset_daily_points()
        if self.connected and datetime.now() - self.last_heartbeat >= timedelta(minutes=15):
            if self.daily_points < 2400:
                self.daily_points += 25
                message = (
                    f"*Node {self.node_id}:* 25 points added.\n"
                    f"*Total Points:* {self.daily_points}"
                )
                print(f"{Fore.GREEN}{message}")
                await send_telegram_message(self.global_config, message)
                self.last_heartbeat = datetime.now()
            else:
                print(f"{Fore.YELLOW}[INFO] Node {self.node_id}: Maximum daily points reached.")

    def on_message(self, ws, message):
        print(f"{Fore.CYAN}[WS] Message: {message}")
        self.connected = True
        self.last_heartbeat = datetime.now()

        try:
            message_data = json.loads(message)
            user_id = message_data.get("userId", self.user_id)
            points_today = message_data.get("pointsToday", 0)
            points_total = message_data.get("pointsTotal", 0)
            
            telegram_message = (
                "  *Connected successfully!*  \n\n"
                f"👤 *User ID:* `{user_id}`\n"
                f"📅 *Points Today:* `{points_today}`\n"
                f"🏆 *Total Points:* `{points_total}`\n\n"
                "Keep up the great work! 🚀"
            )
            
            asyncio.run(send_telegram_message(self.global_config, telegram_message))
        except json.JSONDecodeError:
            print(f"{Fore.RED}[ERROR] Failed to decode message from WebSocket.")

    def on_error(self, ws, error):
        print(f"{Fore.RED}[WS] Error: {error}")
        self.connected = False

    def on_close(self, ws, close_status_code, close_msg):
        print(f"{Fore.YELLOW}[WS] Connection closed for Node {self.node_id}.")
        self.connected = False

    async def on_open(self, ws):
        message = f"*WebSocket Connection:* Opened for Node {self.node_id}"
        print(f"{Fore.GREEN}{message}")
        await send_telegram_message(self.global_config, message)
        self.connected = True
        self.ws = ws
        
        init_payload = json.dumps({"type": "init", "node_id": self.node_id, **self.custom_payload_data})
        self.ws.send(init_payload)
        
        threading.Thread(target=self.send_ping).start()

    def send_ping(self):
        while self.connected:
            time.sleep(15)
            if self.ws:
                try:
                    payload = json.dumps({"type": "ping", "node_id": self.node_id, "timestamp": datetime.now().isoformat(), **self.custom_payload_data})
                    self.ws.send(payload)
                    print(f"{Fore.CYAN}[WS] Ping sent with payload for Node {self.node_id}: {payload}")
                except Exception as e:
                    print(f"{Fore.RED}[WS] Failed to send ping for Node {self.node_id}: {e}")
                    self.connected = False

    def connect_websocket(self):
        retry_attempts = 0
        while True:
            try:
                websocket.enableTrace(False)
                websocket_url = WEBSOCKET_URL_TEMPLATE.format(user_id=self.user_id)
                self.ws = websocket.WebSocketApp(
                    websocket_url,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close,
                    on_open=lambda ws: asyncio.run(self.on_open(ws)),
                    header={
                        "Authorization": f"Bearer {self.jwt_token}",
                        "Connection": "Upgrade",
                        "Upgrade": "websocket",
                        "Cache-Control": "private",
                        "X-Do-App-Origin": "50902350-093e-4f0f-9931-0a795a6d0902",
                        "Apikey": self.global_config.get("api_key", "")
                    }
                )
                self.ws.run_forever()
                retry_attempts = 0
            except Exception as e:
                print(f"{Fore.RED}[WS] Connection failed for Node {self.node_id}: {e}")
                retry_attempts += 1
                sleep_time = min(30, 2 ** retry_attempts)
                print(f"{Fore.YELLOW}[WS] Attempting to reconnect Node {self.node_id} in {sleep_time} seconds...")
                time.sleep(sleep_time)

    def start_heartbeat_monitor(self):
        threading.Thread(target=self.connect_websocket).start()
        while True:
            asyncio.run(self.distribute_points())
            time.sleep(15)

def run_node(account_config, global_config):
    jwt_token = login_and_get_jwt(account_config, global_config)
    if not jwt_token:
        print(f"{Fore.RED}[ERROR] Cannot proceed without a JWT token for account: {account_config.get('email', 'unknown')}")
        return
    
    user_info, user_message = fetch_user_info(jwt_token, global_config)
    if not user_info:
        return

    profile_info, profile_message = fetch_profile_info(jwt_token, user_info['id'], global_config)
    asyncio.run(send_telegram_message(global_config, user_message))
    asyncio.run(send_telegram_message(global_config, profile_message))

    user_id = user_info.get("id", "Unknown")
    node = Node(user_id, jwt_token, account_config, user_id, global_config)
    node.set_custom_payload_data(profile_info)
    print(f"{Fore.BLUE}[DEBUG] Starting node for user {user_id}.")
    node.start_heartbeat_monitor()

def main():
    config = load_config("config.json")
    if not config:
        return

    global_config = {
        "telegram_bot_token": config.get("telegram_bot_token", ""),
        "chat_id": config.get("chat_id", ""),
        "api_key": config.get("api_key", "")
    }

    threads = []
    for account_config in config.get("accounts", []):
        thread = threading.Thread(target=run_node, args=(account_config, global_config))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

if __name__ == "__main__":
    main()
