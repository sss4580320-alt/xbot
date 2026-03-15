from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json, os, threading, time, logging
from datetime import datetime
import sys
import types
if 'imghdr' not in sys.modules:
    imghdr = types.ModuleType('imghdr')
    imghdr.what = lambda *a, **kw: None
    sys.modules['imghdr'] = imghdr
import tweepy

app = Flask(__name__, static_folder='static')
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DATA_FILE = "data.json"

def load_data():
    if not os.path.exists(DATA_FILE):
        return {"accounts": [], "posts": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---- API endpoints ----

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    data = load_data()
    # マスクして返す
    accounts = []
    for a in data["accounts"]:
        accounts.append({
            "id": a["id"],
            "name": a["name"],
            "hasKeys": bool(a.get("apiKey") and a.get("apiSecret") and a.get("accessToken") and a.get("accessTokenSecret"))
        })
    return jsonify(accounts)

@app.route("/api/accounts", methods=["POST"])
def add_account():
    data = load_data()
    body = request.json
    account = {
        "id": body["id"],
        "name": body["name"],
        "apiKey": body.get("apiKey", ""),
        "apiSecret": body.get("apiSecret", ""),
        "accessToken": body.get("accessToken", ""),
        "accessTokenSecret": body.get("accessTokenSecret", "")
    }
    # 既存のアカウントを更新or追加
    existing = next((a for a in data["accounts"] if a["id"] == account["id"]), None)
    if existing:
        existing.update(account)
    else:
        data["accounts"].append(account)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def delete_account(account_id):
    data = load_data()
    data["accounts"] = [a for a in data["accounts"] if str(a["id"]) != str(account_id)]
    data["posts"] = [p for p in data["posts"] if str(p.get("accountId")) != str(account_id)]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/posts", methods=["GET"])
def get_posts():
    data = load_data()
    return jsonify(data["posts"])

@app.route("/api/posts", methods=["POST"])
def add_post():
    data = load_data()
    post = request.json
    existing = next((p for p in data["posts"] if p["id"] == post["id"]), None)
    if existing:
        existing.update(post)
    else:
        data["posts"].append(post)
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    data = load_data()
    data["posts"] = [p for p in data["posts"] if p["id"] != post_id]
    save_data(data)
    return jsonify({"ok": True})

@app.route("/api/posts/bulk", methods=["POST"])
def bulk_posts():
    data = load_data()
    posts = request.json
    for post in posts:
        existing = next((p for p in data["posts"] if p["id"] == post["id"]), None)
        if existing:
            existing.update(post)
        else:
            data["posts"].append(post)
    save_data(data)
    return jsonify({"ok": True, "count": len(posts)})

# ---- Scheduler ----
posted_ids = set()

def scheduler_loop():
    logging.info("⏰ スケジューラー起動")
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            data = load_data()
            for post in data["posts"]:
                if post.get("datetime") == now and post["id"] not in posted_ids:
                    posted_ids.add(post["id"])
                    acct = next((a for a in data["accounts"] if str(a["id"]) == str(post.get("accountId"))), None)
                    if not acct:
                        logging.error(f"❌ アカウントが見つかりません: {post.get('accountId')}")
                        continue
                    try:
                        client = tweepy.Client(
                            consumer_key=acct["apiKey"],
                            consumer_secret=acct["apiSecret"],
                            access_token=acct["accessToken"],
                            access_token_secret=acct["accessTokenSecret"],
                        )
                        client.create_tweet(text=post["text"])
                        logging.info(f"✅ 投稿成功 [{acct['name']}]: {post['text'][:30]}")
                    except Exception as e:
                        logging.error(f"❌ 投稿失敗: {e}")
        except Exception as e:
            logging.error(f"スケジューラーエラー: {e}")
        time.sleep(30)

# バックグラウンドスレッドで起動
t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
