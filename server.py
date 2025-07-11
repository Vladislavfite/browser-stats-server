from flask import Flask, request, jsonify, render_template
import json
import time
import threading
import os
import requests

app = Flask(__name__, template_folder='templates')

DATA_FILE = "stats.json"
RESET_FLAG_FILE = "reset.flag"
SEND_INTERVAL = int(os.environ.get("SEND_INTERVAL", 3600))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

stats_lock = threading.Lock()

def load_stats():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_stats(stats):
    with open(DATA_FILE, "w") as f:
        json.dump(stats, f)

def summarize_stats(stats):
    summary = {
        "total_browsers": len(stats),
        "total_cycles": sum(bot.get("cycles", 0) for bot in stats.values()),
        "total_ads": sum(bot.get("ads", 0) for bot in stats.values()),
        "total_reloads": sum(bot.get("reloads", 0) for bot in stats.values())
    }
    return summary

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/stats", methods=["POST"])
def receive_stats():
    data = request.json
    if not data or "bot_id" not in data:
        return "Invalid", 400

    with stats_lock:
        stats = load_stats()
        stats[data["bot_id"]] = data
        save_stats(stats)
    return "OK", 200

@app.route("/summary", methods=["GET"])
def get_summary():
    with stats_lock:
        stats = load_stats()
        summary = summarize_stats(stats)
    return jsonify(summary)

@app.route("/reset", methods=["POST"])
def reset_stats():
    with stats_lock:
        save_stats({})
        with open(RESET_FLAG_FILE, "w") as f:
            f.write("reset")
    return "Reset OK", 200

@app.route("/should_reset", methods=["GET"])
def should_reset():
    if os.path.exists(RESET_FLAG_FILE):
        os.remove(RESET_FLAG_FILE)
        return jsonify({"reset": True})
    return jsonify({"reset": False})

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
                requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": message})
            except:
                pass

if __name__ == "__main__":
    threading.Thread(target=send_telegram_summary, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
