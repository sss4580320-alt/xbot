from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json, os, threading, time, logging, sys, types
from datetime import datetime
from urllib.parse import urlparse

if 'imghdr' not in sys.modules:
    imghdr = types.ModuleType('imghdr')
    imghdr.what = lambda *a, **kw: None
    sys.modules['imghdr'] = imghdr
import tweepy
import pg8000.native

app = Flask(__name__, static_folder='.')
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    u = urlparse(DATABASE_URL)
    return pg8000.native.Connection(
        user=u.username,
        password=u.password,
        host=u.hostname,
        port=u.port or 5432,
        database=u.path.lstrip("/"),
        ssl_context=True
    )

def init_db():
    db = get_db()
    db.run("""
        CREATE TABLE IF NOT EXISTS accounts (
            id BIGINT PRIMARY KEY,
            name TEXT,
            api_key TEXT DEFAULT '',
            api_secret TEXT DEFAULT '',
            access_token TEXT DEFAULT '',
            access_token_secret TEXT DEFAULT ''
        )
    """)
    db.run("""
        CREATE TABLE IF NOT EXISTS posts (
            id BIGINT PRIMARY KEY,
            datetime TEXT,
            text TEXT,
            account_id BIGINT,
            posted BOOLEAN DEFAULT FALSE
        )
    """)
    db.close()

try:
    init_db()
    logging.info("✅ DB初期化完了")
except Exception as e:
    logging.error(f"❌ DB初期化失敗: {e}")

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/studio")
def studio():
    return send_from_directory(".", "studio.html")

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    db = get_db()
    rows = db.run("SELECT id, name, api_key, api_secret, access_token, access_token_secret FROM accounts")
    db.close()
    return jsonify([{
        "id": r[0], "name": r[1],
        "hasKeys": bool(r[2] and r[3] and r[4] and r[5])
    } for r in rows])

@app.route("/api/accounts", methods=["POST"])
def save_account():
    b = request.json
    db = get_db()
    db.run("""
        INSERT INTO accounts (id, name, api_key, api_secret, access_token, access_token_secret)
        VALUES (:id, :name, :ak, :as_, :at, :ats)
        ON CONFLICT (id) DO UPDATE SET
            name=EXCLUDED.name, api_key=EXCLUDED.api_key,
            api_secret=EXCLUDED.api_secret, access_token=EXCLUDED.access_token,
            access_token_secret=EXCLUDED.access_token_secret
    """, id=b["id"], name=b["name"], ak=b.get("apiKey",""),
        as_=b.get("apiSecret",""), at=b.get("accessToken",""),
        ats=b.get("accessTokenSecret",""))
    db.close()
    return jsonify({"ok": True})

@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    db = get_db()
    db.run("DELETE FROM accounts WHERE id=:id", id=account_id)
    db.run("DELETE FROM posts WHERE account_id=:id", id=account_id)
    db.close()
    return jsonify({"ok": True})

@app.route("/api/posts", methods=["GET"])
def get_posts():
    db = get_db()
    rows = db.run("SELECT id, datetime, text, account_id, posted FROM posts ORDER BY datetime")
    db.close()
    return jsonify([{"id":r[0],"datetime":r[1],"text":r[2],"accountId":r[3],"posted":r[4]} for r in rows])

@app.route("/api/posts", methods=["POST"])
def save_post():
    p = request.json
    db = get_db()
    db.run("""
        INSERT INTO posts (id, datetime, text, account_id)
        VALUES (:id, :dt, :text, :aid)
        ON CONFLICT (id) DO UPDATE SET datetime=EXCLUDED.datetime, text=EXCLUDED.text, account_id=EXCLUDED.account_id
    """, id=p["id"], dt=p.get("datetime"), text=p.get("text"), aid=p.get("accountId"))
    db.close()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    db = get_db()
    db.run("DELETE FROM posts WHERE id=:id", id=post_id)
    db.close()
    return jsonify({"ok": True})

@app.route("/api/posts/bulk", methods=["POST"])
def bulk_posts():
    posts = request.json
    db = get_db()
    for p in posts:
        db.run("""
            INSERT INTO posts (id, datetime, text, account_id)
            VALUES (:id, :dt, :text, :aid)
            ON CONFLICT (id) DO UPDATE SET datetime=EXCLUDED.datetime, text=EXCLUDED.text, account_id=EXCLUDED.account_id
        """, id=p["id"], dt=p.get("datetime"), text=p.get("text"), aid=p.get("accountId"))
    db.close()
    return jsonify({"ok": True, "count": len(posts)})

def scheduler_loop():
    logging.info("⏰ スケジューラー起動")
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            db = get_db()
            rows = db.run("""
                SELECT p.id, p.text, a.api_key, a.api_secret, a.access_token, a.access_token_secret, a.name
                FROM posts p JOIN accounts a ON p.account_id = a.id
                WHERE p.datetime=:now AND p.posted=FALSE
            """, now=now)
            for r in rows:
                try:
                    client = tweepy.Client(
                        consumer_key=r[2], consumer_secret=r[3],
                        access_token=r[4], access_token_secret=r[5]
                    )
                    client.create_tweet(text=r[1])
                    db.run("UPDATE posts SET posted=TRUE WHERE id=:id", id=r[0])
                    logging.info(f"✅ 投稿成功 [{r[6]}]: {r[1][:30]}")
                except Exception as e:
                    logging.error(f"❌ 投稿失敗: {e}")
            db.close()
        except Exception as e:
            logging.error(f"スケジューラーエラー: {e}")
        time.sleep(30)

t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
