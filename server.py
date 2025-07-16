from flask_cors import CORS
import json
import time
import threading
import os
import requests
import fcntl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')

app = Flask(__name__, template_folder=TEMPLATES_DIR)
CORS(app)

DATA_FILE = os.path.join(BASE_DIR, "stats.json")
RESET_FLAG_FILE = os.path.join(BASE_DIR, "reset.flag")
LOCK_FILE = os.path.join(BASE_DIR, "stats.lock")
SUMMARY_LOCK_FILE = os.path.join(BASE_DIR, "summary.lock")
SEND_INTERVAL = int(os.environ.get("SEND_INTERVAL", 3600))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
ACTIVE_TIMEOUT = int(os.environ.get("ACTIVE_TIMEOUT", 60))

stats_lock = threading.Lock()

history = {}


def remove_stale_bots(stats):
    """(Deprecated) Previously removed stale bots."""
    return stats


def load_stats():
    try:
        with open(LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not os.path.exists(DATA_FILE):
                data = {}
            else:
                with open(DATA_FILE, "r") as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        data = {}
            fcntl.flock(lock, fcntl.LOCK_UN)
        return data
    except Exception as e:
        print(f"Error loading stats: {e}")
        return {}


def save_stats(stats):
    try:
        with open(LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with open(DATA_FILE, "w") as f:
                json.dump(stats, f)
            fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception as e:
        print(f"Error saving stats: {e}")


def summarize_stats(stats):
    cutoff = time.time() - ACTIVE_TIMEOUT
    active_bots = [
        bot for bot in stats.values()
        if bot.get("last_active", bot.get("last_seen", 0)) >= cutoff
    ]

    summary = {
        "total_browsers": len(active_bots),
        "total_cycles": sum(bot.get("cycles", 0) for bot in active_bots),
        "total_ads": sum(bot.get("ads", 0) for bot in active_bots),
        "total_reloads": sum(bot.get("reloads", 0) for bot in active_bots)
    }
    return summary


def send_telegram_summary():
    while True:
        time.sleep(SEND_INTERVAL)
        with stats_lock:
            stats = load_stats()
            summary = summarize_stats(stats)

        ad_count = summary["total_ads"]
        income = (ad_count / 1000) * 150

        message = (
            "📊 Статистика за последний час:\n"
            f"🧩 Активных окон: {summary['total_browsers']}\n"
            f"🔁 Циклов: {summary['total_cycles']}\n"
            f"🎬 Реклам: {summary['total_ads']}\n"
            f"♻️ Перезагрузок: {summary['total_reloads']}\n"
            f"💰 Прибыль: {income:.2f}₽"
        )

        if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            try:
                requests.post(
                    url,
                    data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
                )
            except Exception as e:
                print(f"Error sending telegram message: {e}")


@app.route("/stats", methods=["POST"])
def receive_stats():
    data = request.json or {}
    device_id = data.get("device_id") or data.get("bot_id")
    if not device_id:
        return "Invalid", 400

    now = int(time.time())

    with stats_lock:
        stats = load_stats()
        current = stats.get(device_id, {})
        prev_ads = current.get("ads", 0)
        new_ads = data.get("ads", prev_ads)

        # update activity time if ads count changed
        if new_ads != prev_ads:
            current["last_active"] = now
            diff = max(0, new_ads - prev_ads)
        else:
            diff = 0

        current.update(data)
        current["last_seen"] = now
        stats[device_id] = current
        save_stats(stats)

        if diff:
            minute = now - (now % 60)
            history[minute] = history.get(minute, 0) + diff

    return "OK", 200


@app.route("/summary", methods=["GET"])
def get_summary():
    with stats_lock:
        stats = load_stats()
        summary = summarize_stats(stats)
    return jsonify(summary)


@app.route("/dashboard_data", methods=["GET"])
def dashboard_data():
    with stats_lock:
        stats = load_stats()
        now = int(time.time())
        devices = []
        for did, d in stats.items():
            name = d.get("device_name") or d.get("name") or did
            last_seen = d.get("last_seen", 0)
            last_active = d.get("last_active", last_seen)
            ads = d.get("ads", 0)
            status = "online" if now - last_active <= 300 else "offline"
            devices.append({
                "name": name,
                "id": did,
                "ads": ads,
                "last_seen": last_seen,
                "status": status
            })
        active = sum(1 for dev in devices if dev["status"] == "online")
        inactive = len(devices) - active
        hist = [
            {"time": ts, "ads": ads}
            for ts, ads in sorted(history.items())
        ]
    return jsonify(
        {
            "devices": devices,
            "active": active,
            "inactive": inactive,
            "history": hist,
        }
    )


@app.route("/reset", methods=["POST"])
def reset_stats():
    with stats_lock:
        save_stats({})
        history.clear()
        try:
            with open(RESET_FLAG_FILE, "w") as f:
                f.write("reset")
        except Exception as e:
            print(f"Error writing reset flag: {e}")
    return "Reset OK", 200


@app.route("/should_reset", methods=["GET"])
def should_reset():
    try:
        if os.path.exists(RESET_FLAG_FILE):
            os.remove(RESET_FLAG_FILE)
            return jsonify({"reset": True})
    except Exception as e:
        print(f"Error checking reset flag: {e}")
    return jsonify({"reset": False})


@app.route("/")
def index():
    return render_template("index.html")


summary_lock_handle = None

try:
    summary_lock_handle = open(SUMMARY_LOCK_FILE, "w")
    fcntl.flock(summary_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    threading.Thread(target=send_telegram_summary, daemon=True).start()
except BlockingIOError:
    print("Summary thread already running in another process")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
