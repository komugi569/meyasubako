import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
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
feed_cache = {
    "expires_at": 0,
    "data": None
}

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

# =================================================================
# 📦 4. データ構造（リクエストの形）
# =================================================================
class SuggestionRequest(BaseModel):
    text: str
    is_form_dummy: Optional[bool] = False
    dummy_likes: Optional[int] = 0

class CommentRequest(BaseModel):
    text: str

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

# --- 意見を投稿する ---
@app.post("/api/suggestions")
def create_suggestion(req: SuggestionRequest, user: dict = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="テキストが空です")
    
    if not is_safe_with_ai(req.text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")
    
    try:
        profile = get_user_profile(user)
        get_db().collection("suggestions").add({
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
        return {"status": "success"}
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
    return {"status": status}

# --- コメントを追加する ---
@app.post("/api/suggestions/{suggestion_id}/comments")
def add_comment(suggestion_id: str, req: CommentRequest, user: dict = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="テキストが空です")
        
    if not is_safe_with_ai(req.text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")
    
    try:
        profile = get_user_profile(user)
        get_db().collection("suggestions").document(suggestion_id).collection("comments").add({
            "text": req.text,
            "user_id": profile["uid"],
            "user_name": profile["name"],
            "created_at": firestore.SERVER_TIMESTAMP
        })
        return {"status": "success"}
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
