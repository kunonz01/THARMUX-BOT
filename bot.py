# ==============================================
# Project: Tharmux Bot Pro
# Author: Pp
# Telegram: @ROCKY_BHAI787
# Version: 5.0
# Description:
#   Advanced Telegram remote shell & file editor bot
#   with system monitoring and multi-user support
# ==============================================

import os
import pty
import threading
import uuid
import select
import json
import time
import signal
import psutil
import subprocess
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, jsonify
import telebot
from telebot import types
import traceback
import logging
from logging.handlers import RotatingFileHandler

# ========== CONFIGURATION ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
MAIN_ADMIN_ID = int(os.environ.get("MAIN_ADMIN_ID", "0"))
PORT = int(os.environ.get("PORT", 10000))
BASE_DIR = os.getcwd()
DATA_FILE = "bot_data.json"
USER_DATA_DIR = os.path.join(BASE_DIR, "user_data")
LOG_FILE = "bot.log"
MAX_LOG_SIZE = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3

# Create directories
os.makedirs(USER_DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

# ========== LOGGING SETUP ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        RotatingFileHandler(
            os.path.join(BASE_DIR, "logs", LOG_FILE),
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT
        ),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

print("🔧 Configuration loaded:")
print(f"   PORT: {PORT}")
print(f"   BOT_TOKEN present: {'Yes' if BOT_TOKEN != 'YOUR_BOT_TOKEN_HERE' else 'No'}")
print(f"   MAIN_ADMIN_ID: {MAIN_ADMIN_ID}")
print(f"   USER_DATA_DIR: {USER_DATA_DIR}")

# ========== INITIALIZE BOT ==========
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ========== DATA STRUCTURES ==========
edit_sessions = {}
processes = {}
input_wait = {}
active_sessions = {}
admins = set()
user_stats = {}  # Track user usage stats
system_alerts = []  # Store system alerts
MAX_ALERTS = 50
authorized_users = set()  # All users who can use basic features

# ========== HELPER FUNCTIONS ==========
def get_user_directory(user_id):
    """Get or create user's private directory"""
    user_dir = os.path.join(USER_DATA_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def is_admin(user_id):
    """Check if user is admin"""
    return str(user_id) == str(MAIN_ADMIN_ID) or user_id in admins

def is_authorized(user_id):
    """Check if user is authorized (all users are authorized now)"""
    return True  # All users can use basic features

def sanitize_path(user_id, path):
    """Ensure path is within user's directory and prevent path traversal"""
    user_dir = get_user_directory(user_id)
    
    if not os.path.isabs(path):
        clean_path = os.path.join(user_dir, path)
    else:
        clean_path = path
    
    clean_path = os.path.normpath(clean_path)
    
    if not clean_path.startswith(os.path.abspath(user_dir)):
        return None
    
    return clean_path

def get_user_dict(user_id, dict_obj):
    """Get user-specific dictionary, create if not exists"""
    if user_id not in dict_obj:
        dict_obj[user_id] = {}
    return dict_obj[user_id]

def generate_session_id():
    """Generate unique session ID for each command"""
    return str(uuid.uuid4())

def get_system_stats():
    """Get system statistics with progress bars"""
    try:
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_bars = int(cpu_percent / 10)
        cpu_bar = "▰" * cpu_bars + "▱" * (10 - cpu_bars)
        
        # Memory usage
        memory = psutil.virtual_memory()
        mem_percent = memory.percent
        mem_bars = int(mem_percent / 10)
        mem_bar = "▰" * mem_bars + "▱" * (10 - mem_bars)
        
        # Disk usage
        disk = psutil.disk_usage('/')
        disk_percent = disk.percent
        disk_bars = int(disk_percent / 10)
        disk_bar = "▰" * disk_bars + "▱" * (10 - disk_bars)
        
        # Additional stats
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time
        uptime_str = str(uptime).split('.')[0]
        
        processes_count = len(psutil.pids())
        
        return {
            'cpu': cpu_percent,
            'cpu_bar': cpu_bar,
            'memory': mem_percent,
            'memory_bar': mem_bar,
            'disk': disk_percent,
            'disk_bar': disk_bar,
            'uptime': uptime_str,
            'processes': processes_count,
            'boot_time': boot_time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        logger.error(f"Error getting system stats: {e}")
        return {
            'cpu': 0,
            'cpu_bar': "▱" * 10,
            'memory': 0,
            'memory_bar': "▱" * 10,
            'disk': 0,
            'disk_bar': "▱" * 10,
            'uptime': "N/A",
            'processes': 0,
            'boot_time': "N/A"
        }

def add_system_alert(alert_type, message):
    """Add system alert"""
    system_alerts.append({
        'type': alert_type,
        'message': message,
        'time': datetime.now().strftime("%H:%M:%S")
    })
    if len(system_alerts) > MAX_ALERTS:
        system_alerts.pop(0)

def load_data():
    """Load bot data from file"""
    global admins, user_stats, authorized_users
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                data = json.load(f)
                admins = set(data.get('admins', []))
                user_stats = data.get('user_stats', {})
                authorized_users = set(data.get('authorized_users', []))
        admins.add(MAIN_ADMIN_ID)
        logger.info(f"Data loaded. Admins: {len(admins)}, Authorized users: {len(authorized_users)}")
    except Exception as e:
        logger.error(f"⚠️ Load data failed: {e}")
        admins = {MAIN_ADMIN_ID}
        user_stats = {}
        authorized_users = set()

def save_data():
    """Save bot data to file"""
    try:
        data = {
            'admins': list(admins),
            'user_stats': user_stats,
            'authorized_users': list(authorized_users)
        }
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info("Data saved successfully")
    except Exception as e:
        logger.error(f"⚠️ Save data failed: {e}")

def update_user_stats(user_id, username):
    """Update user statistics"""
    user_id_str = str(user_id)
    if user_id_str not in user_stats:
        user_stats[user_id_str] = {
            'commands': 0,
            'first_seen': datetime.now().isoformat(),
            'username': username,
            'user_id': user_id
        }
    user_stats[user_id_str]['commands'] += 1
    user_stats[user_id_str]['last_seen'] = datetime.now().isoformat()
    user_stats[user_id_str]['username'] = username
    save_data()

def run_cmd(cmd, user_id, chat_id, session_id):
    """Run command in isolated PTY for specific user"""
    def task():
        try:
            proc_dict = get_user_dict(user_id, processes)
            sess_dict = get_user_dict(user_id, active_sessions)
            input_dict = get_user_dict(user_id, input_wait)
            
            user_dir = get_user_directory(user_id)
            
            pid, fd = pty.fork()
            if pid == 0:
                # Child process
                os.chdir(user_dir)
                # Use bash -c to execute the command
                os.execvp("bash", ["bash", "-c", cmd])
            else:
                # Parent process
                start_time = datetime.now().strftime("%H:%M:%S")
                proc_dict[session_id] = (pid, fd, start_time, cmd)
                sess_dict[session_id] = time.time()

                try:
                    while True:
                        rlist, _, _ = select.select([fd], [], [], 0.1)
                        if fd in rlist:
                            try:
                                out = os.read(fd, 1024).decode(errors="ignore")
                            except OSError:
                                break

                            if out:
                                # Split long output into chunks
                                for i in range(0, len(out), 3500):
                                    chunk = out[i:i+3500]
                                    try:
                                        bot.send_message(chat_id, f"```\n{chunk}\n```", parse_mode="Markdown")
                                    except Exception as e:
                                        logger.error(f"Error sending message: {e}")

                            if out.strip().endswith(":"):
                                input_dict[session_id] = fd

                        # Check if process is still alive
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break

                        time.sleep(0.1)
                except Exception as e:
                    logger.error(f"Error in command execution: {e}")
                finally:
                    # Cleanup
                    if session_id in proc_dict:
                        del proc_dict[session_id]
                    if session_id in input_dict:
                        del input_dict[session_id]
                    if session_id in sess_dict:
                        del sess_dict[session_id]
                    
                    try:
                        os.close(fd)
                    except:
                        pass
        except Exception as e:
            logger.error(f"Fatal error in run_cmd: {e}")
            try:
                bot.send_message(chat_id, f"❌ Error executing command: {str(e)[:200]}")
            except:
                pass

    threading.Thread(target=task, daemon=True).start()

# ========== KEYBOARDS ==========
def main_menu_keyboard(is_admin_user=False):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # Basic commands for all users
    buttons = [
        "📁 ls -la", "📂 pwd",
        "💿 df -h", "📊 system stats",
        "📝 nano", "🛑 stop",
        "🗑️ clear", "📁 my files",
        "ℹ️ my info", "📜 ps aux | head -20",
        "🌐 ifconfig", "🔄 ping 8.8.8.8 -c 4"
    ]
    
    # Add admin-only buttons if user is admin
    if is_admin_user:
        buttons.extend(["👑 admin panel", "📈 performance"])
    
    markup.add(*buttons)
    return markup

def admin_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("📊 System Status", callback_data="status"),
        types.InlineKeyboardButton("🛑 Stop All", callback_data="stop_all"),
        types.InlineKeyboardButton("👥 Admin List", callback_data="admin_list"),
        types.InlineKeyboardButton("➕ Add Admin", callback_data="add_admin"),
        types.InlineKeyboardButton("➖ Remove Admin", callback_data="remove_admin"),
        types.InlineKeyboardButton("📁 Browse Files", callback_data="list_files"),
        types.InlineKeyboardButton("🗑️ Clean Logs", callback_data="clean_logs"),
        types.InlineKeyboardButton("📊 User Stats", callback_data="user_stats"),
        types.InlineKeyboardButton("⚠️ System Alerts", callback_data="system_alerts"),
        types.InlineKeyboardButton("📈 Performance", callback_data="performance"),
        types.InlineKeyboardButton("👥 Authorize User", callback_data="authorize_user"),
        types.InlineKeyboardButton("🚫 Deauthorize User", callback_data="deauthorize_user")
    )
    return markup

# ========== MESSAGE HANDLERS ==========
@bot.message_handler(commands=["start"])
def start(m):
    cid = m.chat.id
    username = m.from_user.username or "Unknown"
    first_name = m.from_user.first_name or "User"
    
    # Always allow /start command
    authorized_users.add(cid)
    update_user_stats(cid, username)
    
    # Get system stats
    stats = get_system_stats()
    
    welcome_msg = f"""  
        𝗧𝗛𝗔𝗥𝗠𝗨𝗫 𝗕𝗢𝗧 𝗣𝗥𝗢
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬


👋 𝗛𝗘𝗟𝗟𝗢, {first_name}

━━━━━━━━━━━━━━━━━━━━━━━━━━━
        📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗨𝗦
━━━━━━━━━━━━━━━━━━━━━━━━━━━
🖥️  𝗖𝗣𝗨 :
 {stats['cpu_bar']}  {stats['cpu']:.1f}%
💾  𝗠𝗘𝗠𝗢𝗥𝗬 : 
{stats['memory_bar']}  {stats['memory']:.1f}%
💿  𝗗𝗜𝗦𝗞 : 
{stats['disk_bar']}  {stats['disk']:.1f}%

┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓
        ░▒▓█ 𝗖𝗢𝗠𝗠𝗔𝗡𝗗𝗦 █▓▒░
┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛

❯ [ 💻 ] 𝙳𝙸𝚁𝙴𝙲𝚃 𝚃𝙴𝚁𝙼𝚄𝚇 𝙴𝚇𝙴𝙲𝚄𝚃𝙸𝙾𝙽
❯ [ ⚡ ] 𝚀𝚄𝙸𝙲𝙺 𝙰𝙲𝚃𝙸𝙾𝙽 𝙱𝚄𝚃𝚃𝙾𝙽𝚂
❯ [ 📝 ] /nano {filename}  
❯ [ 📚 ] /help → 𝙷𝙴𝙻𝙿 𝙼𝙴𝙽𝚄

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
    bot.send_message(cid, welcome_msg, 
                     parse_mode="Markdown", 
                     reply_markup=main_menu_keyboard(is_admin(cid)))
    
    logger.info(f"User {cid} ({username}) started the bot")

@bot.message_handler(commands=["help"])
def help_cmd(m):
    cid = m.chat.id
    username = m.from_user.username or "Unknown"
    
    help_msg = """
    📚 𝗛𝗘𝗟𝗣 & 𝗖𝗢𝗠𝗠𝗔𝗡𝗗𝗦
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬


        🖥️ 𝗕𝗔𝗦𝗜𝗖 𝗖𝗢𝗠𝗠𝗔𝗡𝗗𝗦
━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 𝚃𝚈𝙿𝙴 𝙰𝙽𝚈 𝙻𝙸𝙽𝚄𝚇 𝙲𝙾𝙼𝙼𝙰𝙽𝙳 𝙳𝙸𝚁𝙴𝙲𝚃𝙻𝚈
• 𝚄𝚂𝙴 𝙱𝚄𝚃𝚃𝙾𝙽𝚂 𝙵𝙾𝚁 𝚀𝚄𝙸𝙲𝙺 𝙲𝙾𝙼𝙼𝙰𝙽𝙳𝚂
• /start - 𝚁𝙴𝚂𝚃𝙰𝚁𝚃 𝙱𝙾𝚃
• /help - 𝚂𝙷𝙾𝚆 𝚃𝙷𝙸𝚂 𝙷𝙴𝙻𝙿

━━━━━━━━━━━━━━━━━━━━━━━━━━━
          📝 𝗙𝗜𝗟𝗘 𝗘𝗗𝗜𝗧𝗜𝗡𝗚
━━━━━━━━━━━━━━━━━━━━━━━━━━━
• /nano {filename} - 𝙴𝙳𝙸𝚃 𝙵𝙸𝙻𝙴𝚂 𝙸𝙽 𝙱𝚁𝙾𝚆𝚂𝙴𝚁
• 𝚅𝙸𝙴𝚆 𝙵𝙸𝙻𝙴𝚂 𝙸𝙽 𝚈𝙾𝚄𝚁 𝙿𝚁𝙸𝚅𝙰𝚃𝙴 𝙳𝙸𝚁𝙴𝙲𝚃𝙾𝚁𝚈
• 𝚂𝙰𝚅𝙴 𝙲𝙷𝙰𝙽𝙶𝙴𝚂 𝙵𝚁𝙾𝙼 𝚆𝙴𝙱 𝙸𝙽𝚃𝙴𝚁𝙵𝙰𝙲𝙴

━━━━━━━━━━━━━━━━━━━━━━━━━━━
         📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗜𝗡𝗙𝗢
━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 𝚂𝚈𝚂𝚃𝙴𝙼 𝚂𝚃𝙰𝚃𝚂 - 𝚅𝙸𝙴𝚆 𝚂𝚈𝚂𝚃𝙴𝙼 𝚂𝚃𝙰𝚃𝚄𝚂
• 𝙼𝚈 𝙵𝙸𝙻𝙴𝚂 - 𝙻𝙸𝚂𝚃 𝚈𝙾𝚄𝚁 𝙵𝙸𝙻𝙴𝚂
• 𝙼𝚈 𝙸𝙽𝙵𝙾 - 𝚈𝙾𝚄𝚁 𝚄𝚂𝙴𝚁 𝙸𝙽𝙵𝙾
┏━━━━━━━━━━━━━━━━━━━━━━━━━━┓
        👑 ADMIN COMMANDS
┗━━━━━━━━━━━━━━━━━━━━━━━━━━┛
• /𝙰𝙳𝙼𝙸𝙽 - 𝙾𝙿𝙴𝙽 𝙰𝙳𝙼𝙸𝙽 𝙿𝙰𝙽𝙴𝙻
• /𝚂𝚃𝙰𝚃𝚄𝚂 - 𝙳𝙴𝚃𝙰𝙸𝙻𝙴𝙳 𝚂𝚈𝚂𝚃𝙴𝙼 𝚂𝚃𝙰𝚃𝚄𝚂
• /𝚂𝙴𝚂𝚂𝙸𝙾𝙽𝚂 - 𝚅𝙸𝙴𝚆 𝙰𝙲𝚃𝙸𝚅𝙴 𝚂𝙴𝚂𝚂𝙸𝙾𝙽𝚂

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
    bot.send_message(cid, help_msg, parse_mode="Markdown")

@bot.message_handler(commands=["admin"])
def admin_panel(m):
    cid = m.chat.id
    if not is_admin(cid):
        bot.send_message(cid, "❌ This command is for admins only!")
        return
    
    bot.send_message(cid, "🔐 *ADMIN CONTROL PANEL*", 
                     parse_mode="Markdown", 
                     reply_markup=admin_keyboard())

@bot.message_handler(commands=["status"])
def status_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        bot.send_message(cid, "❌ This command is for admins only!")
        return
    
    stats = get_system_stats()
    
    total_processes = sum(len(procs) for procs in processes.values())
    total_sessions = sum(len(sess) for sess in active_sessions.values())
    total_users = len(set(active_sessions.keys()) | set(processes.keys()))
    
    status_msg = f"""
 📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗨𝗦 𝗥𝗘𝗣𝗢𝗥𝗧 📊
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

🖥️ 𝗛𝗔𝗥𝗗𝗪𝗔𝗥𝗘 𝗠𝗢𝗡𝗜𝗧𝗢𝗥
──────────────────
𝗖𝗣𝗨    : {stats['cpu_bar']}  {stats['cpu']:.1f}%
𝗠𝗘𝗠𝗢𝗥𝗬 : {stats['memory_bar']}  {stats['memory']:.1f}%
𝗗𝗜𝗦𝗞   : {stats['disk_bar']}  {stats['disk']:.1f}%

 [ ⏱️] 𝗨𝗣𝗧𝗜𝗠𝗘        : {stats['uptime']}
 [🔄] 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗦     : {stats['processes']}
 [🚀] 𝗕𝗢𝗢𝗧 𝗧𝗜𝗠𝗘      : {stats['boot_time']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━
👥 𝗨𝗦𝗘𝗥 𝗦𝗧𝗔𝗧𝗜𝗦𝗧𝗜𝗖𝗦
──────────────────
• 𝗧𝗢𝗧𝗔𝗟 𝗔𝗗𝗠𝗜𝗡𝗦        : {len(admins)}
• 𝗔𝗖𝗧𝗜𝗩𝗘 𝗨𝗦𝗘𝗥𝗦        : {total_users}
• 𝗔𝗖𝗧𝗜𝗩𝗘 𝗦𝗘𝗦𝗦𝗜𝗢𝗡𝗦     : {total_sessions}
• 𝗥𝗨𝗡𝗡𝗜𝗡𝗚 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗦   : `{total_processes}`

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
    
    bot.send_message(cid, status_msg, parse_mode="Markdown")

@bot.message_handler(commands=["sessions"])
def sessions_cmd(m):
    cid = m.chat.id
    if not is_admin(cid):
        bot.send_message(cid, "❌ Not authorized!")
        return
    
    sessions_msg = "🔄 *ACTIVE SESSIONS*\n\n"
    has_sessions = False
    
    for user_id, sess_dict in active_sessions.items():
        if sess_dict:
            has_sessions = True
            sessions_msg += f"👤 User {user_id}:\n"
            for session_id, last_active in sess_dict.items():
                elapsed = int(time.time() - last_active)
                sessions_msg += f"  • `{session_id[:8]}`: {elapsed}s ago\n"
    
    if not has_sessions:
        sessions_msg += "📭 No active sessions"
    
    bot.send_message(cid, sessions_msg, parse_mode="Markdown")

@bot.message_handler(commands=["stop"])
def stop_cmd(m):
    cid = m.chat.id
    if not is_authorized(cid):
        bot.send_message(cid, "❌ Please /start the bot first!")
        return
    
    proc_dict = get_user_dict(cid, processes)
    input_dict = get_user_dict(cid, input_wait)
    sess_dict = get_user_dict(cid, active_sessions)
    
    stopped = 0
    for session_id in list(proc_dict.keys()):
        try:
            pid, fd, _, _ = proc_dict[session_id]
            # Try graceful termination first
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            # Force kill if still running
            try:
                os.kill(pid, 0)  # Check if process exists
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass  # Process already terminated
            stopped += 1
        except Exception as e:
            logger.error(f"Error stopping process {session_id}: {e}")
        
        # Clean up regardless
        if session_id in proc_dict:
            del proc_dict[session_id]
        if session_id in input_dict:
            del input_dict[session_id]
        if session_id in sess_dict:
            del sess_dict[session_id]
    
    if stopped > 0:
        bot.send_message(cid, f"✅ Stopped {stopped} process(es) successfully!")
        add_system_alert("INFO", f"User {cid} stopped {stopped} processes")
    else:
        bot.send_message(cid, "⚠️ No running process to stop.")

@bot.message_handler(commands=["nano"])
def nano_cmd(m):
    cid = m.chat.id
    if not is_authorized(cid):
        bot.send_message(cid, "❌ Please /start the bot first!")
        return

    args = m.text.strip().split(maxsplit=1)
    if len(args) < 2:
        bot.send_message(cid, "📝 *Usage:* `/nano <filename>`\nExample: `/nano script.py`", parse_mode="Markdown")
        return

    filename = args[1].strip()
    safe_path = sanitize_path(cid, filename)

    if not safe_path:
        bot.send_message(cid, "❌ Invalid filename or path traversal attempt!")
        return

    try:
        if not os.path.exists(safe_path):
            open(safe_path, 'w').close()
            bot.send_message(cid, f"✅ Created new file: `{filename}`", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(cid, f"❌ Error creating file: {e}")
        return
    
    sid = str(uuid.uuid4())

    edit_sessions[sid] = {
        "file": safe_path,
        "user_id": cid,
        "timestamp": time.time(),
        "filename": filename
    }

    # Clean old sessions (older than 1 hour)
    current_time = time.time()
    for sess_id in list(edit_sessions.keys()):
        if current_time - edit_sessions[sess_id].get('timestamp', 0) > 3600:
            edit_sessions.pop(sess_id, None)

    # Get base URL from environment or use default
    BASE_URL = os.environ.get("BASE_URL", f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost:10000')}")
    link = f"{BASE_URL}/edit/{sid}"

    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("✏️ Edit in Browser", url=link),
        types.InlineKeyboardButton("📄 View Content", callback_data=f"view_{filename}"),
        types.InlineKeyboardButton("📁 Browse Directory", callback_data=f"browse_{os.path.dirname(filename) or '.'}")
    )

    bot.send_message(
        cid,
        f"📝 *EDIT FILE*\n\n"
        f"📄 *File:* `{filename}`\n"
        f"📁 *Path:* `{safe_path}`\n"
        f"📊 *Size:* {os.path.getsize(safe_path)} bytes\n"
        f"⏱️ *Modified:* {datetime.fromtimestamp(os.path.getmtime(safe_path)).strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.message_handler(func=lambda m: True)
def shell(m):
    cid = m.chat.id
    text = m.text.strip()
    username = m.from_user.username or "Unknown"
    
    if not is_authorized(cid):
        bot.send_message(cid, "❌ Please /start the bot first!")
        return

    # Update user stats
    update_user_stats(cid, username)
    get_user_dict(cid, active_sessions)
    
    # Check for input waiting (for interactive commands)
    input_dict = get_user_dict(cid, input_wait)
    if input_dict:
        for session_id, fd in list(input_dict.items()):
            try:
                os.write(fd, (text + "\n").encode())
                del input_dict[session_id]
                return
            except Exception as e:
                logger.error(f"Error writing to input: {e}")
                del input_dict[session_id]
    
    # Quick command mappings
    quick_map = {
        "📁 ls -la": "ls -la",
        "📂 pwd": "pwd",
        "💿 df -h": "df -h",
        "📊 system stats": None,  # Special handler
        "📜 ps aux | head -20": "ps aux | head -20",
        "🗑️ clear": None,
        "🛑 stop": None,
        "📝 nano": None,
        "🔄 ping 8.8.8.8 -c 4": "ping -c 4 8.8.8.8",
        "🌐 ifconfig": "ifconfig || ip addr",
        "📁 my files": None,
        "ℹ️ my info": None,
        "👑 admin panel": None,
        "📈 performance": None
    }
    
    if text in quick_map:
        if text == "🗑️ clear":
            bot.send_message(cid, "🗑️ Chat cleared (bot-side)")
            return
        elif text == "🛑 stop":
            stop_cmd(m)
            return
        elif text == "📝 nano":
            bot.send_message(cid, "📝 *Usage:* `/nano filename`\nExample: `/nano script.py`", parse_mode="Markdown")
            return
        elif text == "📊 system stats":
            stats = get_system_stats()
            stats_msg = f"""
      📊 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗜𝗦𝗧𝗜𝗖𝗦 📊
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬

🖥️  𝗖𝗣𝗨        : {stats['cpu_bar']}  {stats['cpu']:.1f}%
💾  𝗠𝗘𝗠𝗢𝗥𝗬     : {stats['memory_bar']}  {stats['memory']:.1f}%
💿  𝗗𝗜𝗦𝗞       : {stats['disk_bar']}  {stats['disk']:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️  𝗨𝗣𝗧𝗜𝗠𝗘      : {stats['uptime']}
🔄  𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗦   : {stats['processes']}
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
"""
            bot.send_message(cid, stats_msg, parse_mode="Markdown")
            return
        elif text == "📁 my files":
            user_dir = get_user_directory(cid)
            try:
                files = os.listdir(user_dir)
                if not files:
                    bot.send_message(cid, "📁 Your directory is empty.")
                else:
                    file_list = []
                    for f in files[:15]:  # Limit to 15 files
                        full_path = os.path.join(user_dir, f)
                        if os.path.isfile(full_path):
                            size = os.path.getsize(full_path)
                            modified = datetime.fromtimestamp(os.path.getmtime(full_path)).strftime("%H:%M %d/%m")
                            file_list.append(f"📄 {f} ({size} bytes) - {modified}")
                        else:
                            file_list.append(f"📁 {f}/")
                    
                    msg = "📁 *YOUR FILES*\n\n" + "\n".join(file_list)
                    if len(files) > 15:
                        msg += f"\n\n... and {len(files) - 15} more files"
                    
                    bot.send_message(cid, msg, parse_mode="Markdown")
            except Exception as e:
                bot.send_message(cid, f"❌ Error listing files: {e}")
            return
        elif text == "ℹ️ my info":
            user_dir = get_user_directory(cid)
            user_data = user_stats.get(str(cid), {})
            
            info_msg = f"""
ℹ️ *USER INFORMATION*
━━━━━━━━━━━━━━━━━━━━━━
👤 User ID: `{cid}`
📝 Username: @{username}
📁 Directory: `{user_dir}`

📊 *USAGE STATS*
• Commands: {user_data.get('commands', 0)}
• First seen: {user_data.get('first_seen', 'N/A')[:10]}
• Last active: {user_data.get('last_seen', 'N/A')[:10]}

━━━━━━━━━━━━━━━━━━━━━━
"""
            bot.send_message(cid, info_msg, parse_mode="Markdown")
            return
        elif text == "👑 admin panel":
            if is_admin(cid):
                admin_panel(m)
            else:
                bot.send_message(cid, "❌ Admin only feature!")
            return
        elif text == "📈 performance":
            if is_admin(cid):
                show_performance(cid)
            else:
                bot.send_message(cid, "❌ Admin only feature!")
            return
        else:
            text = quick_map[text]
    
    # Execute command
    session_id = generate_session_id()
    
    bot.send_message(cid, f"```\n$ {text}\n```", parse_mode="Markdown")
    run_cmd(text, cid, cid, session_id)

def show_performance(cid):
    """Show performance metrics"""
    stats = get_system_stats()
    
    # Get process list
    processes_list = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
        try:
            processes_list.append(proc.info)
        except:
            pass
    
    # Sort by CPU usage
    processes_list.sort(key=lambda x: x['cpu_percent'], reverse=True)
    
    perf_msg = f"""
    📈 𝗣𝗘𝗥𝗙𝗢𝗥𝗠𝗔𝗡𝗖𝗘 𝗠𝗘𝗧𝗥𝗜𝗖𝗦 📈
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
🖥️  𝗖𝗣𝗨
• 𝗨𝗦𝗔𝗚𝗘        : {stats['cpu']:.1f}%
• 𝗖𝗢𝗥𝗘𝗦        : {psutil.cpu_count()}

💾  𝗠𝗘𝗠𝗢𝗥𝗬
• 𝗧𝗢𝗧𝗔𝗟        : {psutil.virtual_memory().total / (1024**3):.1f} GB
• 𝗨𝗦𝗘𝗗         : {psutil.virtual_memory().used / (1024**3):.1f} GB
• 𝗔𝗩𝗔𝗜𝗟𝗔𝗕𝗟𝗘    : {psutil.virtual_memory().available / (1024**3):.1f} GB

💿  𝗗𝗜𝗦𝗞
• 𝗧𝗢𝗧𝗔𝗟        : {psutil.disk_usage('/').total / (1024**3):.1f} GB
• 𝗨𝗦𝗘𝗗         : {psutil.disk_usage('/').used / (1024**3):.1f} GB
• 𝗙𝗥𝗘𝗘         : {psutil.disk_usage('/').free / (1024**3):.1f} GB

━━━━━━━━━━━━━━━━━━━━━━━━━━━━
         🔝 𝗧𝗢𝗣 𝗣𝗥𝗢𝗖𝗘𝗦𝗦𝗘𝗦
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    for proc in processes_list[:5]:
        perf_msg += f"• {proc['name']}: {proc['cpu_percent']:.1f}% CPU, {proc['memory_percent']:.1f}% MEM\n"
    
    bot.send_message(cid, perf_msg, parse_mode="Markdown")

# ========== CALLBACK HANDLERS ==========
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    cid = call.message.chat.id
    
    try:
        if call.data == "status":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            status_cmd(call.message)
            bot.answer_callback_query(call.id)
        
        elif call.data == "stop_all":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            stopped = 0
            for user_id, proc_dict in list(processes.items()):
                for session_id, (pid, fd, start_time, cmd) in list(proc_dict.items()):
                    try:
                        os.kill(pid, signal.SIGKILL)
                        stopped += 1
                    except:
                        pass
            
            processes.clear()
            input_wait.clear()
            active_sessions.clear()
            
            bot.answer_callback_query(call.id, f"✅ Stopped {stopped} processes")
            bot.send_message(cid, f"🛑 Stopped all {stopped} processes")
            add_system_alert("WARNING", f"Admin {cid} stopped all processes")
        
        elif call.data == "admin_list":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            admin_list_text = "\n".join([f"👤 `{a}`" for a in sorted(admins) if a != MAIN_ADMIN_ID])
            main_admin_text = f"👑 Main Admin: `{MAIN_ADMIN_ID}`"
            
            bot.answer_callback_query(call.id)
            bot.send_message(cid, f"*ADMIN LIST*\n\n{main_admin_text}\n\n*Other Admins:*\n{admin_list_text if admin_list_text else 'None'}", parse_mode="Markdown")
        
        elif call.data == "add_admin":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            msg = bot.send_message(cid, "Send the user ID to add as admin (numeric ID):")
            bot.register_next_step_handler(msg, add_admin_step)
            bot.answer_callback_query(call.id)
        
        elif call.data == "remove_admin":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            msg = bot.send_message(cid, "Send the user ID to remove from admins:")
            bot.register_next_step_handler(msg, remove_admin_step)
            bot.answer_callback_query(call.id)
        
        elif call.data == "list_files":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            
            try:
                user_dir = get_user_directory(cid)
                files = os.listdir(user_dir)
                if not files:
                    bot.send_message(cid, "📁 Directory is empty.")
                else:
                    file_list = []
                    for f in files[:20]:
                        full_path = os.path.join(user_dir, f)
                        if os.path.isfile(full_path):
                            size = os.path.getsize(full_path)
                            file_list.append(f"📄 {f} ({size} bytes)")
                        else:
                            file_list.append(f"📁 {f}/")
                    
                    msg = "*FILES IN YOUR DIRECTORY:*\n\n" + "\n".join(file_list)
                    if len(files) > 20:
                        msg += f"\n\n... and {len(files)-20} more"
                    
                    bot.send_message(cid, msg, parse_mode="Markdown")
            except Exception as e:
                bot.send_message(cid, f"❌ Error: {e}")
            bot.answer_callback_query(call.id)
        
        elif call.data == "clean_logs":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            current_time = time.time()
            cleaned_sessions = 0
            cleaned_processes = 0
            
            # Clean old sessions
            for user_id, sess_dict in list(active_sessions.items()):
                for session_id, last_active in list(sess_dict.items()):
                    if current_time - last_active > 3600:  # Older than 1 hour
                        del sess_dict[session_id]
                        cleaned_sessions += 1
            
            # Clean zombie processes
            for user_id, proc_dict in list(processes.items()):
                for session_id, (pid, fd, start_time, cmd) in list(proc_dict.items()):
                    try:
                        os.kill(pid, 0)  # Check if process exists
                    except OSError:
                        # Process doesn't exist, clean up
                        del proc_dict[session_id]
                        cleaned_processes += 1
            
            bot.answer_callback_query(call.id, f"✅ Cleaned {cleaned_sessions} sessions, {cleaned_processes} processes")
            bot.send_message(cid, f"🧹 *Cleanup Complete*\n\n• Removed {cleaned_sessions} stale sessions\n• Removed {cleaned_processes} zombie processes", parse_mode="Markdown")
        
        elif call.data == "user_stats":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            stats_msg = "*USER STATISTICS*\n\n"
            for user_id, data in user_stats.items():
                stats_msg += f"👤 User {user_id} (@{data.get('username', 'N/A')}):\n"
                stats_msg += f"  • Commands: {data.get('commands', 0)}\n"
                stats_msg += f"  • First seen: {data.get('first_seen', 'N/A')[:10]}\n"
                stats_msg += f"  • Last seen: {data.get('last_seen', 'N/A')[:10]}\n\n"
            
            bot.send_message(cid, stats_msg, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
        
        elif call.data == "system_alerts":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            if not system_alerts:
                bot.send_message(cid, "✅ No system alerts")
            else:
                alerts_msg = "*SYSTEM ALERTS*\n\n"
                for alert in system_alerts[-10:]:  # Show last 10 alerts
                    emoji = "⚠️" if alert['type'] == "WARNING" else "ℹ️" if alert['type'] == "INFO" else "❌"
                    alerts_msg += f"{emoji} [{alert['time']}] {alert['message']}\n"
                
                bot.send_message(cid, alerts_msg, parse_mode="Markdown")
            bot.answer_callback_query(call.id)
        
        elif call.data == "performance":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            show_performance(cid)
            bot.answer_callback_query(call.id)
        
        elif call.data == "authorize_user":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            msg = bot.send_message(cid, "Send the user ID to authorize:")
            bot.register_next_step_handler(msg, authorize_user_step)
            bot.answer_callback_query(call.id)
        
        elif call.data == "deauthorize_user":
            if not is_admin(cid):
                bot.answer_callback_query(call.id, "❌ Not authorized!")
                return
            if str(cid) != str(MAIN_ADMIN_ID):
                bot.answer_callback_query(call.id, "❌ Main admin only!")
                return
            
            msg = bot.send_message(cid, "Send the user ID to deauthorize:")
            bot.register_next_step_handler(msg, deauthorize_user_step)
            bot.answer_callback_query(call.id)
        
        elif call.data.startswith("view_"):
            filename = call.data[5:]
            safe_path = sanitize_path(cid, filename)
            
            if not safe_path:
                bot.answer_callback_query(call.id, "❌ Invalid filename!")
                return
            
            try:
                if not os.path.exists(safe_path):
                    bot.answer_callback_query(call.id, "❌ File not found!")
                    return
                
                with open(safe_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(3500)  # Limit to 3500 chars
                
                if len(content) < 3500:
                    bot.send_message(cid, f"```\n{content}\n```", parse_mode="Markdown")
                else:
                    bot.send_message(cid, f"```\n{content}\n```", parse_mode="Markdown")
                    bot.send_message(cid, "📝 File truncated (max 3500 chars shown)")
                
                bot.answer_callback_query(call.id)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")
        
        elif call.data.startswith("browse_"):
            path = call.data[7:]
            safe_path = sanitize_path(cid, path)
            
            if not safe_path:
                bot.answer_callback_query(call.id, "❌ Invalid path!")
                return
            
            try:
                if not os.path.exists(safe_path):
                    bot.answer_callback_query(call.id, "❌ Path not found!")
                    return
                
                if os.path.isfile(safe_path):
                    # Show file info
                    filename = os.path.basename(safe_path)
                    size = os.path.getsize(safe_path)
                    modified = datetime.fromtimestamp(os.path.getmtime(safe_path)).strftime('%Y-%m-%d %H:%M:%S')
                    
                    info_msg = f"""
📄 *FILE INFO*
━━━━━━━━━━━━━━
📄 Name: `{filename}`
📁 Path: `{safe_path}`
📊 Size: {size} bytes
⏱️ Modified: {modified}

*Actions:*
• /nano {filename} - Edit file
• view_{filename} - View content
"""
                    bot.send_message(cid, info_msg, parse_mode="Markdown")
                else:
                    # List directory
                    files = os.listdir(safe_path)
                    dir_msg = f"📁 *DIRECTORY: {path}*\n\n"
                    
                    for f in files[:15]:
                        full_path = os.path.join(safe_path, f)
                        if os.path.isfile(full_path):
                            size = os.path.getsize(full_path)
                            dir_msg += f"📄 {f} ({size} bytes)\n"
                        else:
                            dir_msg += f"📁 {f}/\n"
                    
                    if len(files) > 15:
                        dir_msg += f"\n... and {len(files)-15} more"
                    
                    bot.send_message(cid, dir_msg, parse_mode="Markdown")
                
                bot.answer_callback_query(call.id)
            except Exception as e:
                bot.answer_callback_query(call.id, f"❌ Error: {str(e)[:50]}")
    
    except Exception as e:
        logger.error(f"Callback error: {e}")
        bot.answer_callback_query(call.id, "❌ An error occurred")

# ========== ADMIN STEP HANDLERS ==========
def add_admin_step(m):
    cid = m.chat.id
    if str(cid) != str(MAIN_ADMIN_ID):
        return
    
    try:
        new_admin = int(m.text.strip())
        if new_admin in admins:
            bot.send_message(cid, f"❌ Admin {new_admin} already exists!")
        else:
            admins.add(new_admin)
            save_data()
            bot.send_message(cid, f"✅ Added admin: `{new_admin}`", parse_mode="Markdown")
            add_system_alert("INFO", f"Added new admin: {new_admin}")
    except ValueError:
        bot.send_message(cid, "❌ Invalid user ID. Please send numeric ID only.")
    except Exception as e:
        bot.send_message(cid, f"❌ Error: {e}")

def remove_admin_step(m):
    cid = m.chat.id
    if str(cid) != str(MAIN_ADMIN_ID):
        return
    
    try:
        admin_id = int(m.text.strip())
        
        if admin_id == MAIN_ADMIN_ID:
            bot.send_message(cid, "❌ Cannot remove the main admin.")
            return
        
        if admin_id in admins:
            admins.remove(admin_id)
            save_data()
            bot.send_message(cid, f"✅ Removed admin: `{admin_id}`", parse_mode="Markdown")
            add_system_alert("INFO", f"Removed admin: {admin_id}")
        else:
            bot.send_message(cid, f"❌ Admin ID `{admin_id}` not found.", parse_mode="Markdown")
    except ValueError:
        bot.send_message(cid, "❌ Invalid user ID. Please send numeric ID only.")
    except Exception as e:
        bot.send_message(cid, f"❌ Error: {e}")

def authorize_user_step(m):
    cid = m.chat.id
    if str(cid) != str(MAIN_ADMIN_ID):
        return
    
    try:
        user_id = int(m.text.strip())
        authorized_users.add(user_id)
        save_data()
        bot.send_message(cid, f"✅ Authorized user: `{user_id}`", parse_mode="Markdown")
        add_system_alert("INFO", f"Authorized user: {user_id}")
    except ValueError:
        bot.send_message(cid, "❌ Invalid user ID. Please send numeric ID only.")
    except Exception as e:
        bot.send_message(cid, f"❌ Error: {e}")

def deauthorize_user_step(m):
    cid = m.chat.id
    if str(cid) != str(MAIN_ADMIN_ID):
        return
    
    try:
        user_id = int(m.text.strip())
        if user_id in authorized_users:
            authorized_users.remove(user_id)
            save_data()
            bot.send_message(cid, f"✅ Deauthorized user: `{user_id}`", parse_mode="Markdown")
            add_system_alert("INFO", f"Deauthorized user: {user_id}")
        else:
            bot.send_message(cid, f"❌ User ID `{user_id}` not found.", parse_mode="Markdown")
    except ValueError:
        bot.send_message(cid, "❌ Invalid user ID. Please send numeric ID only.")
    except Exception as e:
        bot.send_message(cid, f"❌ Error: {e}")

# ========== WEB INTERFACE ==========
@app.route("/edit/<sid>", methods=["GET", "POST"])
def edit(sid):
    if sid not in edit_sessions:
        return """
        <html>
        <head>
            <title>Session Expired</title>
            <style>
                body { background: #0d1117; color: #c9d1d9; font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                .container { text-align: center; padding: 40px; border-radius: 10px; background: #161b22; }
                h2 { color: #f85149; }
            </style>
        </head>
        <body>
            <div class="container">
                <h2>❌ Invalid or expired session</h2>
                <p>Please generate a new edit link from Telegram</p>
            </div>
        </body>
        </html>
        """

    session_data = edit_sessions[sid]
    file = session_data.get("file")
    user_id = session_data.get("user_id")
    filename = session_data.get("filename", os.path.basename(file))
    
    # Security check
    user_dir = get_user_directory(user_id)
    abs_path = os.path.abspath(file)
    if not abs_path.startswith(os.path.abspath(user_dir)):
        return """
        <html>
        <body style="background:#111;color:#f00;padding:20px;">
        <h2>❌ Unauthorized file access</h2>
        </body>
        </html>
        """

    if request.method == "POST":
        try:
            code_content = request.form.get("code", "")
            with open(abs_path, "w", encoding='utf-8') as f:
                f.write(code_content)

            # Don't delete session immediately, keep for 5 minutes
            session_data['saved'] = True
            session_data['save_time'] = time.time()

            return """
            <html>
            <head>
                <title>File Saved</title>
                <style>
                    body { background: #0d1117; color: #c9d1d9; font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
                    .container { text-align: center; padding: 40px; border-radius: 10px; background: #161b22; }
                    h2 { color: #3fb950; }
                    .success { color: #3fb950; font-size: 48px; margin-bottom: 20px; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success">✅</div>
                    <h2>File Saved Successfully!</h2>
                    <p>You can close this window and return to Telegram</p>
                </div>
            </body>
            </html>
            """
        except Exception as e:
            return f"""
            <html>
            <head>
                <title>Error</title>
                <style>
                    body {{ background: #0d1117; color: #c9d1d9; font-family: Arial; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
                    .container {{ text-align: center; padding: 40px; border-radius: 10px; background: #161b22; }}
                    h2 {{ color: #f85149; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h2>❌ Error saving file</h2>
                    <p>{e}</p>
                </div>
            </body>
            </html>
            """
            
    try:
        with open(abs_path, "r", encoding='utf-8', errors='ignore') as f:
            code = f.read()
    except Exception as e:
        code = f"# Error loading file: {e}\n# File may be binary or corrupted"

    return render_template_string("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>THARMUX BOT EDIT- {{ filename }}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/ace/1.23.0/ace.js"></script>
    <style>
        :root {
            --bg-dark: #0d1117;
            --bg-card: #161b22;
            --accent: #58a6ff;
            --accent-success: #3fb950;
            --border: #30363d;
            --text: #c9d1d9;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body { 
            margin: 0; 
            background: var(--bg-dark); 
            color: var(--text); 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; 
        }

        .header {
            background: var(--bg-card);
            padding: 12px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
        }

        .logo {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 18px;
            font-weight: 600;
        }

        .logo i {
            color: var(--accent);
            font-size: 24px;
        }

        .file-info {
            font-size: 14px;
            padding: 6px 16px;
            background: #0d1117;
            border-radius: 20px;
            color: var(--accent);
            border: 1px solid var(--border);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .file-info i {
            font-size: 14px;
        }

        #editor {
            width: 100%;
            height: calc(100vh - 130px);
            font-size: 14px;
        }

        .footer {
            padding: 12px 24px;
            background: var(--bg-card);
            border-top: 1px solid var(--border);
            display: flex;
            justify-content: flex-end;
            gap: 12px;
        }

        .btn {
            padding: 8px 24px;
            border-radius: 6px;
            font-weight: 500;
            cursor: pointer;
            transition: 0.2s;
            border: none;
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn-save {
            background: #238636;
            color: white;
        }

        .btn-save:hover { 
            background: #2ea043;
            transform: translateY(-1px);
        }

        .btn-cancel {
            background: transparent;
            color: var(--text);
            border: 1px solid var(--border);
        }

        .btn-cancel:hover {
            background: rgba(255,255,255,0.1);
        }

        .status-bar {
            background: var(--bg-card);
            padding: 4px 24px;
            font-size: 12px;
            color: #8b949e;
            display: flex;
            gap: 24px;
            border-bottom: 1px solid var(--border);
        }

        .status-bar span i {
            margin-right: 6px;
            color: var(--accent);
        }

        @keyframes pulse {
            0% { opacity: 1; }
            50% { opacity: 0.5; }
            100% { opacity: 1; }
        }

        .saving {
            animation: pulse 1s infinite;
        }
    </style>
</head>
<body>

<div class="header">
    <div class="logo">
        <i class="fas fa-terminal"></i>
        <span>THARMUX BOT EDIT</span>
    </div>
    <div class="file-info">
        <i class="far fa-file-code"></i>
        {{ filename }}
        <i class="fas fa-circle" style="color: #3fb950; font-size: 8px; margin-left: 8px;"></i>
        <span style="color: var(--text);">Connected</span>
    </div>
</div>

<div class="status-bar">
    <span><i class="fas fa-code-branch"></i> Session: {{ sid[:8] }}</span>
    <span><i class="far fa-clock"></i> {{ timestamp }}</span>
    <span><i class="fas fa-hdd"></i> {{ file_size }}</span>
</div>

<div id="editor">{{ code }}</div>

<form id="saveForm" method="post">
    <input type="hidden" name="code" id="hiddenCode">
    <div class="footer">
        <button type="button" onclick="window.close()" class="btn btn-cancel">
            <i class="fas fa-times"></i> Cancel
        </button>
        <button type="button" onclick="saveData()" class="btn btn-save">
            <i class="fas fa-save"></i> Save Changes
        </button>
    </div>
</form>

<script>
    var editor = ace.edit("editor");
    editor.setTheme("ace/theme/one_dark");
    editor.setShowPrintMargin(false);
    editor.setFontSize(14);
    
    // Auto-detect language
    var filename = "{{ filename }}";
    var ext = filename.split('.').pop().toLowerCase();
    
    var modeMap = {
        'py': 'python',
        'js': 'javascript',
        'html': 'html',
        'css': 'css',
        'php': 'php',
        'json': 'json',
        'xml': 'xml',
        'md': 'markdown',
        'sh': 'sh',
        'bash': 'sh',
        'txt': 'text',
        'conf': 'text',
        'ini': 'text',
        'yml': 'yaml',
        'yaml': 'yaml',
        'c': 'c_cpp',
        'cpp': 'c_cpp',
        'h': 'c_cpp',
        'java': 'java',
        'rb': 'ruby',
        'go': 'golang',
        'rs': 'rust'
    };
    
    if(modeMap[ext]) {
        editor.session.setMode("ace/mode/" + modeMap[ext]);
    }

    editor.setOptions({
        enableBasicAutocompletion: true,
        enableLiveAutocompletion: true,
        showLineNumbers: true,
        tabSize: 4,
        useSoftTabs: true
    });

    function saveData() {
        var saveBtn = document.querySelector('.btn-save');
        saveBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Saving...';
        saveBtn.disabled = true;
        
        document.getElementById('hiddenCode').value = editor.getValue();
        document.getElementById('saveForm').submit();
    }

    // Auto-save indicator
    var isSaving = false;
    editor.on('change', function() {
        if(!isSaving) {
            isSaving = true;
            setTimeout(function() {
                isSaving = false;
            }, 1000);
        }
    });

    // Keyboard shortcut: Ctrl+S
    editor.commands.addCommand({
        name: 'save',
        bindKey: {win: 'Ctrl-S', mac: 'Command-S'},
        exec: function() {
            saveData();
        }
    });
</script>

</body>
</html>
""", code=code, file=file, filename=filename, sid=sid, 
           timestamp=datetime.now().strftime("%H:%M:%S"),
           file_size=f"{os.path.getsize(abs_path)} bytes")

@app.route('/')
def home():
    stats = get_system_stats()
    
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>THARMUX BOT | System Monitor</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{ 
            background: #0a0c0f; 
            min-height: 100vh; 
            display: flex; 
            justify-content: center; 
            align-items: center; 
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
            position: relative;
            overflow-x: hidden;
        }}

        .particles {{
            position: absolute;
            width: 100%;
            height: 100%;
            background: radial-gradient(circle at 20% 50%, rgba(0, 212, 255, 0.05) 0%, transparent 50%),
                        radial-gradient(circle at 80% 80%, rgba(0, 255, 136, 0.05) 0%, transparent 50%);
            z-index: 1;
        }}

        .container {{
            position: relative;
            z-index: 10;
            max-width: 800px;
            width: 90%;
            padding: 30px;
        }}

        .status-card {{
            background: rgba(22, 27, 34, 0.95);
            backdrop-filter: blur(10px);
            border-radius: 30px;
            padding: 40px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.5);
            border: 1px solid rgba(255,255,255,0.05);
        }}

        .header {{
            text-align: center;
            margin-bottom: 40px;
        }}

        .bot-icon {{
            width: 100px;
            height: 100px;
            margin: 0 auto 20px;
            background: linear-gradient(135deg, #00d4ff, #0066ff);
            border-radius: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 50px;
            color: white;
            box-shadow: 0 10px 30px rgba(0, 212, 255, 0.3);
            animation: float 3s ease-in-out infinite;
        }}

        @keyframes float {{
            0%, 100% {{ transform: translateY(0px); }}
            50% {{ transform: translateY(-10px); }}
        }}

        h1 {{
            color: white;
            font-size: 32px;
            font-weight: 600;
            letter-spacing: 1px;
            margin-bottom: 5px;
        }}

        .status-badge {{
            display: inline-block;
            padding: 8px 20px;
            background: rgba(0, 212, 255, 0.1);
            border: 1px solid rgba(0, 212, 255, 0.3);
            border-radius: 50px;
            color: #00d4ff;
            font-size: 14px;
            font-weight: 500;
            margin-top: 10px;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin: 40px 0;
        }}

        .stat-item {{
            background: rgba(255,255,255,0.03);
            border-radius: 20px;
            padding: 25px 20px;
            text-align: center;
            border: 1px solid rgba(255,255,255,0.05);
            transition: 0.3s;
        }}

        .stat-item:hover {{
            transform: translateY(-5px);
            background: rgba(255,255,255,0.05);
            border-color: rgba(0, 212, 255, 0.2);
        }}

        .stat-icon {{
            font-size: 30px;
            color: #00d4ff;
            margin-bottom: 15px;
        }}

        .stat-label {{
            color: #8b949e;
            font-size: 14px;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}

        .stat-value {{
            color: white;
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 10px;
        }}

        .progress-bar {{
            width: 100%;
            height: 6px;
            background: rgba(255,255,255,0.1);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 10px;
        }}

        .progress-fill {{
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }}

        .progress-fill.cpu {{ background: linear-gradient(90deg, #00d4ff, #0066ff); }}
        .progress-fill.memory {{ background: linear-gradient(90deg, #00ff88, #00cc66); }}
        .progress-fill.disk {{ background: linear-gradient(90deg, #ff6b6b, #ff4757); }}

        .info-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 15px;
            margin: 30px 0;
        }}

        .info-item {{
            padding: 15px;
            background: rgba(255,255,255,0.02);
            border-radius: 15px;
            border: 1px solid rgba(255,255,255,0.05);
        }}

        .info-label {{
            color: #8b949e;
            font-size: 13px;
            margin-bottom: 5px;
            display: flex;
            align-items: center;
            gap: 8px;
        }}

        .info-value {{
            color: white;
            font-size: 16px;
            font-weight: 500;
        }}

        .btn-telegram {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 12px;
            background: linear-gradient(135deg, #00d4ff, #0066ff);
            color: white;
            border: none;
            padding: 16px 40px;
            border-radius: 50px;
            text-decoration: none;
            font-size: 16px;
            font-weight: 600;
            transition: 0.3s;
            width: 100%;
            margin-top: 30px;
            box-shadow: 0 10px 20px rgba(0, 212, 255, 0.2);
        }}

        .btn-telegram:hover {{
            transform: translateY(-2px);
            box-shadow: 0 15px 30px rgba(0, 212, 255, 0.3);
        }}

        .footer {{
            margin-top: 30px;
            text-align: center;
            color: #484f58;
            font-size: 13px;
        }}

        .footer a {{
            color: #00d4ff;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="particles"></div>
    
    <div class="container">
        <div class="status-card">
            <div class="header">
                <div class="bot-icon">
                    <i class="fas fa-robot"></i>
                </div>
                <h1>THARMUX BOT</h1>
                <div class="status-badge">
                    <i class="fas fa-circle" style="color: #3fb950; font-size: 10px;"></i>
                    SYSTEM ONLINE
                </div>
            </div>

            <div class="stats-grid">
                <div class="stat-item">
                    <div class="stat-icon">
                        <i class="fas fa-microchip"></i>
                    </div>
                    <div class="stat-label">CPU</div>
                    <div class="stat-value">{stats['cpu']:.1f}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill cpu" style="width: {stats['cpu']}%"></div>
                    </div>
                </div>

                <div class="stat-item">
                    <div class="stat-icon">
                        <i class="fas fa-memory"></i>
                    </div>
                    <div class="stat-label">MEMORY</div>
                    <div class="stat-value">{stats['memory']:.1f}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill memory" style="width: {stats['memory']}%"></div>
                    </div>
                </div>

                <div class="stat-item">
                    <div class="stat-icon">
                        <i class="fas fa-hdd"></i>
                    </div>
                    <div class="stat-label">DISK</div>
                    <div class="stat-value">{stats['disk']:.1f}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill disk" style="width: {stats['disk']}%"></div>
                    </div>
                </div>
            </div>

            <div class="info-grid">
                <div class="info-item">
                    <div class="info-label">
                        <i class="fas fa-clock" style="color: #00d4ff;"></i>
                        Uptime
                    </div>
                    <div class="info-value">{stats['uptime']}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">
                        <i class="fas fa-tasks" style="color: #00ff88;"></i>
                        Processes
                    </div>
                    <div class="info-value">{stats['processes']}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">
                        <i class="fas fa-users" style="color: #ff6b6b;"></i>
                        Active Users
                    </div>
                    <div class="info-value">{len(set(active_sessions.keys()) | set(processes.keys()))}</div>
                </div>
                <div class="info-item">
                    <div class="info-label">
                        <i class="fas fa-code-branch" style="color: #ffd700;"></i>
                        Sessions
                    </div>
                    <div class="info-value">{sum(len(sess) for sess in active_sessions.values())}</div>
                </div>
            </div>

            <a href="https://t.me/KAALIX_ROBOT" class="btn-telegram" target="_blank">
                <i class="fab fa-telegram-plane"></i>
                OPEN TELEGRAM BOT
            </a>

            <div class="footer">
                <p>OWNER - ROCKY_BHAI787</p>
                <p style="margin-top: 10px;">
                    <a href="#"><i class="fab fa-github"></i></a>
                    <a href="#" style="margin-left: 15px;"><i class="fas fa-shield-alt"></i></a>
                </p>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'stats': get_system_stats()
    })

@app.route('/api/stats')
def api_stats():
    """API endpoint for stats"""
    stats = get_system_stats()
    stats.update({
        'active_users': len(set(active_sessions.keys()) | set(processes.keys())),
        'active_sessions': sum(len(sess) for sess in active_sessions.values()),
        'total_admins': len(admins),
        'edit_sessions': len(edit_sessions)
    })
    return jsonify(stats)

# ========== MAIN ==========
if __name__ == "__main__":
    print("🤖 Starting Termux Bot Pro v5.0...")
    print(f"👑 Main Admin: {MAIN_ADMIN_ID}")
    print(f"📁 Base Directory: {BASE_DIR}")
    print(f"📁 User Data Directory: {USER_DATA_DIR}")
    print(f"🌐 Web Interface: http://0.0.0.0:{PORT}")
    print(f"📝 Log File: {os.path.join(BASE_DIR, 'logs', LOG_FILE)}")

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ ERROR: Please set your BOT_TOKEN in environment variables!")
        exit(1)

    if not MAIN_ADMIN_ID:
        print("❌ ERROR: MAIN_ADMIN_ID environment variable not set!")
        exit(1)

    # Load saved data
    load_data()

    # ========== FLASK SERVER ==========
    def run_flask():
        try:
            print(f"🚀 Starting Flask server on port {PORT}...")
            app.run(
                host="0.0.0.0",
                port=PORT,
                debug=False,
                use_reloader=False,
                threaded=True
            )
        except Exception as e:
            logger.error(f"⚠️ Flask server error: {e}")
            time.sleep(5)
            run_flask()

    # ========== TELEGRAM BOT ==========
    def run_bot():
        print("🤖 Starting Telegram bot...")
        while True:
            try:
                logger.info("Bot polling started")
                bot.infinity_polling(
                    timeout=60,
                    long_polling_timeout=60,
                    skip_pending=True
                )
            except Exception as e:
                logger.error(f"⚠️ Bot error: {e}")
                add_system_alert("ERROR", f"Bot connection error: {str(e)[:80]}")
                time.sleep(5)

    # ========== START THREADS ==========
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    bot_thread = threading.Thread(target=run_bot, daemon=True)

    flask_thread.start()
    bot_thread.start()

    add_system_alert("INFO", "Bot started successfully")

    # ========== MONITOR LOOP ==========
    try:
        while True:
            time.sleep(60)

            # Clean old edit sessions
            current_time = time.time()
            for sid in list(edit_sessions.keys()):
                if current_time - edit_sessions[sid].get('timestamp', 0) > 3600:
                    edit_sessions.pop(sid, None)

            stats = get_system_stats()
            logger.info(
                f"System status - CPU: {stats['cpu']:.1f}%, "
                f"Memory: {stats['memory']:.1f}%, "
                f"Disk: {stats['disk']:.1f}%"
            )

            if stats['cpu'] > 80:
                add_system_alert("WARNING", f"High CPU usage: {stats['cpu']:.1f}%")
            if stats['memory'] > 80:
                add_system_alert("WARNING", f"High memory usage: {stats['memory']:.1f}%")
            if stats['disk'] > 90:
                add_system_alert("WARNING", f"Low disk space: {stats['disk']:.1f}%")

    except KeyboardInterrupt:
        print("\n👋 Shutting down gracefully...")
        save_data()
        logger.info("Bot shutdown complete")
