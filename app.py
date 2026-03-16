from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json, os, threading, time, logging, sys, types
from datetime import datetime

if 'imghdr' not in sys.modules:
    imghdr = types.ModuleType('imghdr')
    imghdr.what = lambda *a, **kw: None
    sys.modules['imghdr'] = imghdr
import tweepy
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__, static_folder='.')
CORS(app)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

DATABASE_URL = os.environ.get("DATABASE_URL")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id BIGINT PRIMARY KEY,
                    name TEXT,
                    api_key TEXT,
                    api_secret TEXT,
                    access_token TEXT,
                    access_token_secret TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id BIGINT PRIMARY KEY,
                    datetime TEXT,
                    text TEXT,
                    account_id BIGINT,
                    posted BOOLEAN DEFAULT FALSE
                )
            """)
        conn.commit()

try:
    init_db()
    logging.info("✅ データベース初期化完了")
except Exception as e:
    logging.error(f"❌ データベース初期化失敗: {e}")

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, api_key != '' AND api_secret != '' AND access_token != '' AND access_token_secret != '' AS has_keys FROM accounts")
            rows = cur.fetchall()
    return jsonify([{"id": r["id"], "name": r["name"], "hasKeys": r["has_keys"]} for r in rows])

@app.route("/api/accounts", methods=["POST"])
def save_account():
    b = request.json
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO accounts (id, name, api_key, api_secret, access_token, access_token_secret)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    api_key = EXCLUDED.api_key,
                    api_secret = EXCLUDED.api_secret,
                    access_token = EXCLUDED.access_token,
                    access_token_secret = EXCLUDED.access_token_secret
            """, (b["id"], b["name"], b.get("apiKey",""), b.get("apiSecret",""), b.get("accessToken",""), b.get("accessTokenSecret","")))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/accounts/<int:account_id>", methods=["DELETE"])
def delete_account(account_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM accounts WHERE id = %s", (account_id,))
            cur.execute("DELETE FROM posts WHERE account_id = %s", (account_id,))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/posts", methods=["GET"])
def get_posts():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, datetime, text, account_id AS \"accountId\", posted FROM posts ORDER BY datetime")
            rows = cur.fetchall()
    return jsonify([dict(r) for r in rows])

@app.route("/api/posts", methods=["POST"])
def save_post():
    p = request.json
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO posts (id, datetime, text, account_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    datetime = EXCLUDED.datetime,
                    text = EXCLUDED.text,
                    account_id = EXCLUDED.account_id
            """, (p["id"], p.get("datetime"), p.get("text"), p.get("accountId")))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/<int:post_id>", methods=["DELETE"])
def delete_post(post_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM posts WHERE id = %s", (post_id,))
        conn.commit()
    return jsonify({"ok": True})

@app.route("/api/posts/bulk", methods=["POST"])
def bulk_posts():
    posts = request.json
    with get_db() as conn:
        with conn.cursor() as cur:
            for p in posts:
                cur.execute("""
                    INSERT INTO posts (id, datetime, text, account_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        datetime = EXCLUDED.datetime,
                        text = EXCLUDED.text,
                        account_id = EXCLUDED.account_id
                """, (p["id"], p.get("datetime"), p.get("text"), p.get("accountId")))
        conn.commit()
    return jsonify({"ok": True, "count": len(posts)})

def scheduler_loop():
    logging.info("⏰ スケジューラー起動")
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT p.*, a.api_key, a.api_secret, a.access_token, a.access_token_secret, a.name as account_name FROM posts p JOIN accounts a ON p.account_id = a.id WHERE p.datetime = %s AND p.posted = FALSE", (now,))
                    posts = cur.fetchall()
                for post in posts:
                    try:
                        client = tweepy.Client(
                            consumer_key=post["api_key"],
                            consumer_secret=post["api_secret"],
                            access_token=post["access_token"],
                            access_token_secret=post["access_token_secret"],
                        )
                        client.create_tweet(text=post["text"])
                        with conn.cursor() as cur:
                            cur.execute("UPDATE posts SET posted = TRUE WHERE id = %s", (post["id"],))
                        conn.commit()
                        logging.info(f"✅ 投稿成功 [{post['account_name']}]: {post['text'][:30]}")
                    except Exception as e:
                        logging.error(f"❌ 投稿失敗: {e}")
        except Exception as e:
            logging.error(f"スケジューラーエラー: {e}")
        time.sleep(30)

t = threading.Thread(target=scheduler_loop, daemon=True)
t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
