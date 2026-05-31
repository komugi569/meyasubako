import os
import hashlib
import json
import secrets
import subprocess
import sys
import tempfile
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends, Request
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, firestore, auth

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# 💡 以下のおまじないを追加（どんな環境からの通信もブロックせずに許可する設定）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False, # 修正: Trueと["*"]の競合を防ぐためFalseに変更
    allow_methods=["*"],
    allow_headers=["*"],
)
FEED_CACHE_SECONDS = 20
BOT_TIMEOUT_SECONDS = 2
BOT_MAX_CODE_BYTES = 20000
BOT_MAX_EVENT_BOTS = 5
APP_VERSION = "2026.05.31-dev-api-bots"
APP_CHANGELOG = [
    "開発者用目安箱を追加しました。",
    "ログイン済み開発者向けの外部API tokenを発行できるようにしました。",
    "Hylang botをアップロードして、意見・いいね・コメントのイベントを受け取れるようにしました。",
    "コメント取得と管理画面の投稿者名表示を修正しました。",
]
feed_cache = {
    "expires_at": 0,
    "data": None
}
rate_limit_store = {}

# =================================================================
# 🤖 1. AIフィルター（一時的にお休み・全通し）
# =================================================================
def is_safe_with_ai(text: str) -> bool:
    """現在は人力モデレーションのため、すべてTrue（安全）を返します"""
    return True

# =================================================================
# 🔥 2. Firebase / Firestore の初期化
# =================================================================
db = None

def build_firebase_credential():
    if all(key in os.environ for key in ("FIREBASE_PROJECT_ID", "FIREBASE_PRIVATE_KEY", "FIREBASE_CLIENT_EMAIL")):
        private_key = os.environ["FIREBASE_PRIVATE_KEY"].replace("\\n", "\n")
        cred_dict = {
            "type": "service_account",
            "project_id": os.environ["FIREBASE_PROJECT_ID"],
            "private_key": private_key,
            "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        return credentials.Certificate(cred_dict)

    for path in ("firebase-key.json", "meyasubako-23797-firebase-adminsdk-h18i7-66487e4115.json"):
        if os.path.exists(path):
            return credentials.Certificate(path)

    raise RuntimeError("Firebaseの認証情報が見つかりません")

def get_db():
    global db
    if db is not None:
        return db

    try:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(build_firebase_credential())
        db = firestore.client()
        return db
    except Exception as e:
        print(f"Firebase初期化エラー: {e}")
        raise HTTPException(status_code=500, detail="Firebaseの初期化に失敗しました")

# =================================================================
# 🛡️ 3. 認証用ミドルウェア（ログインユーザーを判定する）
# =================================================================
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    
    token = authorization.split("Bearer ")[1]
    try:
        get_db()
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except HTTPException:
        raise
    except Exception as e:
        print(f"トークン検証エラー: {e}")
        raise HTTPException(status_code=401, detail="無効なトークンです")

def get_identity_email(user: dict) -> str:
    email = user.get("email", "")
    if email:
        return email

    identities = user.get("firebase", {}).get("identities", {})
    emails = identities.get("email", [])
    if emails:
        return emails[0]

    return ""

def get_user_profile(user: dict):
    uid = user.get("uid", "")
    name = user.get("name") or user.get("display_name") or user.get("displayName") or ""
    email = get_identity_email(user)

    if uid and (not name or not email):
        try:
            user_record = auth.get_user(uid)
            name = name or user_record.display_name or ""
            email = email or user_record.email or ""
        except Exception as e:
            print(f"ユーザー情報取得エラー: {e}")

    if not name and email:
        name = email.split("@")[0]

    return {
        "uid": uid,
        "name": name or "匿名",
        "email": email or ""
    }

def get_author_from_doc(data: dict):
    name = data.get("user_name") or ""
    email = data.get("user_email") or ""
    uid = data.get("user_id") or ""

    if uid and (not name or name in ("不明", "匿名") or not email):
        profile = get_user_profile({
            "uid": uid,
            "name": name if name not in ("不明", "匿名") else "",
            "email": email
        })
        name = profile["name"]
        email = profile["email"]

    return {
        "name": name or "不明",
        "email": email or "不明"
    }

def get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

def check_rate_limit(request: Request, bucket: str, max_requests: int, window_seconds: int):
    now = time.time()
    ip = get_client_ip(request)
    key = f"{bucket}:{ip}"
    hits = [hit for hit in rate_limit_store.get(key, []) if now - hit < window_seconds]

    if len(hits) >= max_requests:
        retry_after = max(1, int(window_seconds - (now - hits[0])))
        raise HTTPException(
            status_code=429,
            detail=f"リクエストが多すぎます。{retry_after}秒後に再試行してください"
        )

    hits.append(now)
    rate_limit_store[key] = hits

    if len(rate_limit_store) > 1000:
        for stored_key, stored_hits in list(rate_limit_store.items()):
            fresh_hits = [hit for hit in stored_hits if now - hit < 3600]
            if fresh_hits:
                rate_limit_store[stored_key] = fresh_hits
            else:
                rate_limit_store.pop(stored_key, None)

def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def mask_api_token(token: str) -> str:
    if not token:
        return ""
    return f"{token[:10]}...{token[-4:]}"

def serialize_developer_doc(data: dict):
    return {
        "developer_mode_enabled": data.get("developer_mode_enabled", False),
        "token_enabled": data.get("token_enabled", False),
        "has_token": bool(data.get("token_hash")),
        "token_preview": data.get("token_preview", ""),
        "user_name": data.get("user_name", ""),
        "user_email": data.get("user_email", "")
    }

def get_external_api_user(request: Request, authorization: str = Header(None)):
    check_rate_limit(request, "external_auth", 240, 60)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="API tokenがありません")

    raw_token = authorization.split("Bearer ", 1)[1].strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="API tokenがありません")

    token_hash = hash_api_token(raw_token)
    docs = get_db().collection("developers").where("token_hash", "==", token_hash).limit(1).stream()
    developer_doc = next(docs, None)

    if not developer_doc:
        raise HTTPException(status_code=401, detail="API tokenが無効です")

    data = developer_doc.to_dict()
    if not data.get("developer_mode_enabled", False):
        raise HTTPException(status_code=403, detail="開発者モードが無効です")
    if not data.get("token_enabled", False):
        raise HTTPException(status_code=403, detail="API tokenが無効化されています")

    return {
        "uid": developer_doc.id,
        "name": data.get("user_name") or "開発者",
        "email": data.get("user_email") or ""
    }

def validate_hy_bot_code(code: str):
    if not code or not code.strip():
        raise HTTPException(status_code=400, detail="Hylangコードが空です")
    if len(code.encode("utf-8")) > BOT_MAX_CODE_BYTES:
        raise HTTPException(status_code=400, detail="Hylangコードが大きすぎます")

    lowered = code.lower()
    blocked_tokens = [
        "(import",
        "(require",
        "__",
        "eval",
        "exec",
        "compile",
        "open",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "pathlib",
        "shutil",
        "os.",
        "sys.",
        "builtins",
        "globals",
        "locals",
        "vars",
    ]
    for token in blocked_tokens:
        if token in lowered:
            raise HTTPException(status_code=400, detail=f"使用できない構文が含まれています: {token}")

def run_hy_bot_code(code: str, event: dict):
    validate_hy_bot_code(code)
    runner_path = os.path.join(os.path.dirname(__file__), "hy_runner.py")

    with tempfile.NamedTemporaryFile("w", suffix=".hy", delete=False, encoding="utf-8") as bot_file:
        bot_file.write(code)
        bot_path = bot_file.name

    try:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "MEYASUBAKO_EVENT": json.dumps(event, ensure_ascii=False),
        }
        completed = subprocess.run(
            [sys.executable, runner_path, bot_path],
            capture_output=True,
            text=True,
            timeout=BOT_TIMEOUT_SECONDS,
            env=env,
            cwd=tempfile.gettempdir(),
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()

        parsed_output = None
        if stdout:
            last_line = stdout.splitlines()[-1]
            try:
                parsed_output = json.loads(last_line)
            except json.JSONDecodeError:
                parsed_output = {"text": stdout[:1000]}

        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": parsed_output,
            "stdout": stdout[:2000],
            "stderr": stderr[:2000],
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "returncode": None,
            "output": None,
            "stdout": "",
            "stderr": "Bot execution timed out",
        }
    finally:
        try:
            os.remove(bot_path)
        except OSError:
            pass

def serialize_bot_doc(data: dict):
    return {
        "enabled": data.get("enabled", False),
        "has_code": bool(data.get("code")),
        "code": data.get("code", ""),
        "last_error": data.get("last_error", ""),
        "last_output": data.get("last_output"),
        "last_event_type": data.get("last_event_type", ""),
    }

def dispatch_bot_event(event: dict):
    try:
        docs = get_db().collection("developer_bots").where("enabled", "==", True).limit(BOT_MAX_EVENT_BOTS).stream()
        for doc in docs:
            bot = doc.to_dict()
            developer = get_db().collection("developers").document(doc.id).get()
            developer_data = developer.to_dict() if developer.exists else {}
            if not developer_data.get("developer_mode_enabled", False):
                continue

            result = run_hy_bot_code(bot.get("code", ""), event)
            update = {
                "last_run_at": firestore.SERVER_TIMESTAMP,
                "last_event_type": event.get("type", ""),
                "last_output": result.get("output"),
                "last_error": "" if result["ok"] else result.get("stderr", "Bot execution failed"),
            }
            doc.reference.set(update, merge=True)
            doc.reference.collection("runs").add({
                "event": event,
                "result": result,
                "created_at": firestore.SERVER_TIMESTAMP,
            })
    except Exception as e:
        print(f"Hylang bot dispatch error: {e}")

# =================================================================
# 📦 4. データ構造（リクエストの形）
# =================================================================
class SuggestionRequest(BaseModel):
    text: str
    is_form_dummy: Optional[bool] = False
    dummy_likes: Optional[int] = 0

class CommentRequest(BaseModel):
    text: str

class ExternalSuggestionRequest(BaseModel):
    text: str

class ExternalCommentRequest(BaseModel):
    text: str

class DeveloperModeRequest(BaseModel):
    enabled: bool

class DeveloperTokenStatusRequest(BaseModel):
    enabled: bool

class DeveloperBotRequest(BaseModel):
    code: str
    enabled: bool = True

class DeveloperBotStatusRequest(BaseModel):
    enabled: bool

class DeleteRequest(BaseModel):
    doc_id: str
    password: str

class StatusRequest(BaseModel):
    status: str
    password: str

# 修正: GETパラメータでのパスワード送信を防ぐため、POSTリクエスト用のモデルを追加
class AdminAuthRequest(BaseModel):
    password: str

# =================================================================
# 🚀 5. API エンドポイント
# =================================================================

# データを組み立てる関数（最新200件に制限してパフォーマンス低下を防ぐ）
def build_suggestions():
    database = get_db()
    docs = database.collection("suggestions").order_by("created_at", direction=firestore.Query.DESCENDING).limit(200).stream()
    suggestions = []
    
    for doc in docs:
        d = doc.to_dict()
        if d.get("is_form_dummy"):
            continue

        created_at = d.get("created_at")
        timestamp = created_at.timestamp() * 1000 if created_at else 0
        
        suggestions.append({
            "id": doc.id,
            "text": d.get("text", ""),
            "likes": len(d.get("liked_by", [])),
            "created_at": timestamp,
            "is_form_dummy": False,
            "status": d.get("status", "検討中")
        })
        
    return sorted(suggestions, key=lambda x: x["likes"], reverse=True)

def build_deleted_form_ids():
    database = get_db()
    deleted_docs = database.collection("deleted_forms").stream()
    return [doc.id for doc in deleted_docs]

# --- 意見一覧を取得（一般向け） ---
@app.get("/api/suggestions")
def get_suggestions():
    try:
        data = build_suggestions()
        return {"status": "success", "data": data}
    except Exception as e:
        print(f"一覧取得エラー: {e}")
        raise HTTPException(status_code=500, detail="データベースの読み込みに失敗しました")

@app.get("/api/version")
def get_version():
    return {
        "status": "success",
        "data": {
            "version": APP_VERSION,
            "changes": APP_CHANGELOG
        }
    }

# --- Googleフォーム合体版フィード ---
@app.get("/api/feed")
def get_feed(force: Optional[int] = 0):
    global feed_cache
    now = time.time()
    if not force and feed_cache["data"] and now < feed_cache["expires_at"]:
        return feed_cache["data"]

    try:
        data = {
            "suggestions": build_suggestions(),
            "deleted_form_ids": build_deleted_form_ids()
        }
        feed_cache["data"] = data
        feed_cache["expires_at"] = now + FEED_CACHE_SECONDS

        return data
    except Exception as e:
        print(f"フィード取得エラー: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 開発者用: 状態取得 ---
@app.get("/api/developer/status")
def get_developer_status(user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc_ref = get_db().collection("developers").document(profile["uid"])
    doc = doc_ref.get()

    if not doc.exists:
        return {
            "status": "success",
            "data": {
                "developer_mode_enabled": False,
                "token_enabled": False,
                "has_token": False,
                "token_preview": "",
                "user_name": profile["name"],
                "user_email": profile["email"]
            }
        }

    data = doc.to_dict()
    return {"status": "success", "data": serialize_developer_doc(data)}

# --- 開発者用: 開発者モード切り替え ---
@app.post("/api/developer/mode")
def update_developer_mode(req: DeveloperModeRequest, user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc_ref = get_db().collection("developers").document(profile["uid"])
    doc_ref.set({
        "developer_mode_enabled": req.enabled,
        "user_name": profile["name"],
        "user_email": profile["email"],
        "updated_at": firestore.SERVER_TIMESTAMP
    }, merge=True)

    data = doc_ref.get().to_dict()
    return {"status": "success", "data": serialize_developer_doc(data)}

# --- 開発者用: token発行 ---
@app.post("/api/developer/token/generate")
def generate_developer_token(user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    raw_token = f"meyasu_{secrets.token_urlsafe(32)}"
    doc_ref = get_db().collection("developers").document(profile["uid"])
    doc_ref.set({
        "developer_mode_enabled": True,
        "token_enabled": True,
        "token_hash": hash_api_token(raw_token),
        "token_preview": mask_api_token(raw_token),
        "user_name": profile["name"],
        "user_email": profile["email"],
        "updated_at": firestore.SERVER_TIMESTAMP
    }, merge=True)

    data = doc_ref.get().to_dict()
    payload = serialize_developer_doc(data)
    payload["token"] = raw_token
    return {"status": "success", "data": payload}

# --- 開発者用: token有効/無効切り替え ---
@app.post("/api/developer/token/status")
def update_developer_token_status(req: DeveloperTokenStatusRequest, user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc_ref = get_db().collection("developers").document(profile["uid"])
    doc = doc_ref.get()

    if req.enabled and (not doc.exists or not doc.to_dict().get("token_hash")):
        raise HTTPException(status_code=400, detail="先にAPI tokenを発行してください")

    doc_ref.set({
        "token_enabled": req.enabled,
        "user_name": profile["name"],
        "user_email": profile["email"],
        "updated_at": firestore.SERVER_TIMESTAMP
    }, merge=True)

    data = doc_ref.get().to_dict()
    return {"status": "success", "data": serialize_developer_doc(data)}

# --- 開発者用: Hylang bot取得 ---
@app.get("/api/developer/bot")
def get_developer_bot(user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc = get_db().collection("developer_bots").document(profile["uid"]).get()
    if not doc.exists:
        return {"status": "success", "data": {"enabled": False, "has_code": False, "code": ""}}
    return {"status": "success", "data": serialize_bot_doc(doc.to_dict())}

# --- 開発者用: Hylang botアップロード ---
@app.post("/api/developer/bot")
def upload_developer_bot(req: DeveloperBotRequest, user: dict = Depends(get_current_user)):
    validate_hy_bot_code(req.code)
    profile = get_user_profile(user)
    developer_doc = get_db().collection("developers").document(profile["uid"]).get()
    developer_data = developer_doc.to_dict() if developer_doc.exists else {}
    if not developer_data.get("developer_mode_enabled", False):
        raise HTTPException(status_code=403, detail="開発者モードを有効にしてください")

    doc_ref = get_db().collection("developer_bots").document(profile["uid"])
    doc_ref.set({
        "code": req.code,
        "enabled": req.enabled,
        "user_name": profile["name"],
        "user_email": profile["email"],
        "updated_at": firestore.SERVER_TIMESTAMP,
        "last_error": "",
    }, merge=True)

    return {"status": "success", "data": serialize_bot_doc(doc_ref.get().to_dict())}

# --- 開発者用: Hylang bot有効/無効切り替え ---
@app.post("/api/developer/bot/status")
def update_developer_bot_status(req: DeveloperBotStatusRequest, user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc_ref = get_db().collection("developer_bots").document(profile["uid"])
    doc = doc_ref.get()
    if req.enabled and (not doc.exists or not doc.to_dict().get("code")):
        raise HTTPException(status_code=400, detail="先にHylang botをアップロードしてください")

    doc_ref.set({
        "enabled": req.enabled,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    return {"status": "success", "data": serialize_bot_doc(doc_ref.get().to_dict())}

# --- 開発者用: Hylang botテスト実行 ---
@app.post("/api/developer/bot/test")
def test_developer_bot(user: dict = Depends(get_current_user)):
    profile = get_user_profile(user)
    doc = get_db().collection("developer_bots").document(profile["uid"]).get()
    if not doc.exists or not doc.to_dict().get("code"):
        raise HTTPException(status_code=400, detail="先にHylang botをアップロードしてください")

    event = {
        "type": "bot_test",
        "created_at": int(time.time() * 1000),
        "actor": {
            "uid": profile["uid"],
            "name": profile["name"],
            "email": profile["email"],
        },
        "data": {
            "message": "Hylang bot test event"
        }
    }
    result = run_hy_bot_code(doc.to_dict().get("code", ""), event)
    doc.reference.set({
        "last_run_at": firestore.SERVER_TIMESTAMP,
        "last_event_type": event["type"],
        "last_output": result.get("output"),
        "last_error": "" if result["ok"] else result.get("stderr", "Bot execution failed"),
    }, merge=True)
    return {"status": "success", "data": result}

# --- 外部API: 意見取得 ---
@app.get("/api/external/suggestions")
def external_get_suggestions(
    request: Request,
    limit: Optional[int] = 100,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_read", 180, 60)
    suggestions = build_suggestions()
    safe_limit = min(max(limit or 100, 1), 200)
    return {"status": "success", "data": suggestions[:safe_limit]}

# --- 外部API: 意見投稿 ---
@app.post("/api/external/suggestions")
def external_create_suggestion(
    req: ExternalSuggestionRequest,
    request: Request,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_write", 30, 600)
    text = req.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="テキストが空です")
    if len(text) > 2000:
        raise HTTPException(status_code=400, detail="テキストが長すぎます")
    if not is_safe_with_ai(text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")

    doc_ref = get_db().collection("suggestions").document()
    doc_ref.set({
        "text": text,
        "liked_by": [],
        "created_at": firestore.SERVER_TIMESTAMP,
        "is_form_dummy": False,
        "status": "検討中",
        "user_id": f"developer:{api_user['uid']}",
        "user_name": api_user["name"],
        "user_email": api_user["email"],
        "source": "external_api"
    })
    clear_feed_cache()
    dispatch_bot_event({
        "type": "suggestion_created",
        "source": "external_api",
        "suggestion_id": doc_ref.id,
        "created_at": int(time.time() * 1000),
        "actor": api_user,
        "data": {
            "text": text,
            "status": "検討中"
        }
    })
    return {"status": "success", "id": doc_ref.id}

# --- 外部API: いいね取得 ---
@app.get("/api/external/suggestions/{suggestion_id}/likes")
def external_get_likes(
    suggestion_id: str,
    request: Request,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_read", 180, 60)
    doc = get_db().collection("suggestions").document(suggestion_id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="意見が見つかりません")

    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    api_user_id = f"developer:{api_user['uid']}"
    return {
        "status": "success",
        "data": {
            "likes": len(liked_by),
            "liked_by_me": api_user_id in liked_by
        }
    }

# --- 外部API: いいね追加/解除 ---
@app.post("/api/external/suggestions/{suggestion_id}/like")
def external_like_suggestion(
    suggestion_id: str,
    request: Request,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_like", 60, 600)
    doc_ref = get_db().collection("suggestions").document(suggestion_id)
    doc = doc_ref.get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail="意見が見つかりません")

    api_user_id = f"developer:{api_user['uid']}"
    liked_by = doc.to_dict().get("liked_by", [])
    if api_user_id in liked_by:
        doc_ref.update({"liked_by": firestore.ArrayRemove([api_user_id])})
        status = "unliked"
    else:
        doc_ref.update({"liked_by": firestore.ArrayUnion([api_user_id])})
        status = "liked"

    clear_feed_cache()
    updated = doc_ref.get().to_dict()
    likes = len(updated.get("liked_by", []))
    dispatch_bot_event({
        "type": "like_changed",
        "source": "external_api",
        "suggestion_id": suggestion_id,
        "created_at": int(time.time() * 1000),
        "actor": api_user,
        "data": {
            "status": status,
            "likes": likes
        }
    })
    return {"status": status, "likes": likes}

# --- 外部API: コメント取得 ---
@app.get("/api/external/suggestions/{suggestion_id}/comments")
def external_get_comments(
    suggestion_id: str,
    request: Request,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_read", 180, 60)
    suggestion = get_db().collection("suggestions").document(suggestion_id).get()
    if not suggestion.exists:
        raise HTTPException(status_code=404, detail="意見が見つかりません")

    docs = suggestion.reference.collection("comments").order_by("created_at").stream()
    comments = []
    for doc in docs:
        d = doc.to_dict()
        comments.append({
            "id": doc.id,
            "text": d.get("text", ""),
            "user_name": d.get("user_name", "匿名")
        })
    return {"status": "success", "data": comments}

# --- 外部API: コメント追加 ---
@app.post("/api/external/suggestions/{suggestion_id}/comments")
def external_add_comment(
    suggestion_id: str,
    req: ExternalCommentRequest,
    request: Request,
    api_user: dict = Depends(get_external_api_user)
):
    check_rate_limit(request, "external_comment", 60, 600)
    text = req.text.strip()

    if not text:
        raise HTTPException(status_code=400, detail="テキストが空です")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="コメントが長すぎます")
    if not is_safe_with_ai(text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")

    suggestion_ref = get_db().collection("suggestions").document(suggestion_id)
    if not suggestion_ref.get().exists:
        raise HTTPException(status_code=404, detail="意見が見つかりません")

    comment_ref = suggestion_ref.collection("comments").document()
    comment_ref.set({
        "text": text,
        "user_id": f"developer:{api_user['uid']}",
        "user_name": api_user["name"],
        "created_at": firestore.SERVER_TIMESTAMP,
        "source": "external_api"
    })
    dispatch_bot_event({
        "type": "comment_created",
        "source": "external_api",
        "suggestion_id": suggestion_id,
        "comment_id": comment_ref.id,
        "created_at": int(time.time() * 1000),
        "actor": api_user,
        "data": {
            "text": text
        }
    })
    return {"status": "success", "id": comment_ref.id}

# --- 意見を投稿する ---
@app.post("/api/suggestions")
def create_suggestion(req: SuggestionRequest, user: dict = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="テキストが空です")
    
    if not is_safe_with_ai(req.text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")
    
    try:
        profile = get_user_profile(user)
        doc_ref = get_db().collection("suggestions").document()
        doc_ref.set({
            "text": req.text,
            "liked_by": [],
            "created_at": firestore.SERVER_TIMESTAMP,
            "is_form_dummy": False,
            "status": "検討中",
            "user_id": profile["uid"],
            "user_name": profile["name"],
            "user_email": profile["email"]
        })
        clear_feed_cache()
        dispatch_bot_event({
            "type": "suggestion_created",
            "source": "web",
            "suggestion_id": doc_ref.id,
            "created_at": int(time.time() * 1000),
            "actor": profile,
            "data": {
                "text": req.text,
                "status": "検討中"
            }
        })
        return {"status": "success", "id": doc_ref.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- いいね！を押す ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    user_id = user["uid"]
    doc_ref = get_db().collection("suggestions").document(suggestion_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        if suggestion_id.startswith("form-"):
            doc_ref.set({
                "is_form_dummy": True,
                "status": "検討中",
                "liked_by": [user_id],
                "created_at": firestore.SERVER_TIMESTAMP
            })
            clear_feed_cache()
            return {"status": "liked", "likes": 1}
        else:
            raise HTTPException(status_code=404, detail="意見が見つかりません")
        
    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    
    # 修正: レースコンディション対策（ArrayUnionとArrayRemoveによる不可分操作）
    if user_id in liked_by:
        doc_ref.update({"liked_by": firestore.ArrayRemove([user_id])})
        status = "unliked"
    else:
        doc_ref.update({"liked_by": firestore.ArrayUnion([user_id])})
        status = "liked"
        
    clear_feed_cache()
    updated = doc_ref.get().to_dict()
    likes = len(updated.get("liked_by", []))
    dispatch_bot_event({
        "type": "like_changed",
        "source": "web",
        "suggestion_id": suggestion_id,
        "created_at": int(time.time() * 1000),
        "actor": get_user_profile(user),
        "data": {
            "status": status,
            "likes": likes
        }
    })
    return {"status": status, "likes": likes}

# --- コメントを追加する ---
@app.post("/api/suggestions/{suggestion_id}/comments")
def add_comment(suggestion_id: str, req: CommentRequest, user: dict = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="テキストが空です")
        
    if not is_safe_with_ai(req.text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")
    
    try:
        profile = get_user_profile(user)
        comment_ref = get_db().collection("suggestions").document(suggestion_id).collection("comments").document()
        comment_ref.set({
            "text": req.text,
            "user_id": profile["uid"],
            "user_name": profile["name"],
            "created_at": firestore.SERVER_TIMESTAMP
        })
        dispatch_bot_event({
            "type": "comment_created",
            "source": "web",
            "suggestion_id": suggestion_id,
            "comment_id": comment_ref.id,
            "created_at": int(time.time() * 1000),
            "actor": profile,
            "data": {
                "text": req.text
            }
        })
        return {"status": "success", "id": comment_ref.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- コメントを取得する ---
@app.get("/api/suggestions/{doc_id}/comments")
def get_comments(doc_id: str):
    try:
        docs = get_db().collection("suggestions").document(doc_id).collection("comments").order_by("created_at").stream()
        comments = []
        for doc in docs:
            d = doc.to_dict()
            comments.append({
                "id": doc.id,
                "text": d.get("text", ""),
                "user_name": d.get("user_name", "匿名")
            })
        return {"status": "success", "data": comments}
    except Exception as e:
        print(f"コメント取得エラー: {e}")
        # 修正: エラーを握りつぶさず適切にHTTPエラーを返す
        raise HTTPException(status_code=500, detail=str(e))

# --- 管理者専用：削除 ---
@app.post("/api/delete")
def delete_suggestion(req: DeleteRequest):
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or req.password != admin_pass:
        raise HTTPException(status_code=403, detail="パスワードが違います")
        
    try:
        if req.doc_id.startswith("form-"):
            get_db().collection("deleted_forms").document(req.doc_id).set({
                "deleted_at": firestore.SERVER_TIMESTAMP
            })
        else:
            # 修正: サブコレクション(comments)の削除によるゴミデータ防止
            comments_ref = get_db().collection("suggestions").document(req.doc_id).collection("comments").stream()
            for comment in comments_ref:
                comment.reference.delete()
            # 親ドキュメントの削除
            get_db().collection("suggestions").document(req.doc_id).delete()

        clear_feed_cache()
        return {"status": "success", "message": "削除処理が完了しました"}
    except Exception as e:
        print(f"削除エラー: {e}")
        raise HTTPException(status_code=500, detail="削除に失敗しました")

# --- ステータス更新 ---
@app.post("/api/suggestions/{doc_id}/status")
def update_suggestion_status(doc_id: str, req: StatusRequest):
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or req.password != admin_pass:
        raise HTTPException(status_code=403, detail="パスワードが違います")
        
    try:
        get_db().collection("suggestions").document(doc_id).update({
            "status": req.status
        })
        clear_feed_cache()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 管理者専用の意見一覧取得（個人情報を含む） ---
# 修正: 脆弱性対策のため POST エンドポイント化し、リクエストボディで受け取るように変更
@app.post("/api/admin/suggestions")
def get_admin_suggestions(req: AdminAuthRequest):
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or req.password != admin_pass:
        raise HTTPException(status_code=403, detail="パスワードが違います")
    
    docs = get_db().collection("suggestions").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
    suggestions = []
    for doc in docs:
        d = doc.to_dict()
        created_at = d.get("created_at")
        timestamp = created_at.timestamp() * 1000 if created_at else 0
        author = get_author_from_doc(d)
        
        suggestions.append({
            "id": doc.id,
            "text": d.get("text", ""),
            "likes": len(d.get("liked_by", [])),
            "created_at": timestamp,
            "is_form_dummy": d.get("is_form_dummy", False),
            "status": d.get("status", "検討中"),
            "user_name": author["name"],
            "user_email": author["email"]
        })
        
    return {"status": "success", "data": suggestions}

@app.get("/api/admin/deleted_forms")
def get_deleted_forms(password: str):
    # こちらは影響が少ないですが、本格運用の際はPOSTに揃えることを推奨します
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or password != admin_pass:
        raise HTTPException(status_code=403, detail="パスワードが違います")
    
    docs = get_db().collection("deleted_forms").stream()
    return {"status": "success", "data": [doc.id for doc in docs]}

def clear_feed_cache():
    global feed_cache
    feed_cache["data"] = None
    feed_cache["expires_at"] = 0
