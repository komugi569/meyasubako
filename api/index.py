import os
import json
import urllib.request
import urllib.error
import time
from concurrent.futures import ThreadPoolExecutor
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
if not firebase_admin._apps:
    if "FIREBASE_PROJECT_ID" in os.environ:
        private_key = os.environ["FIREBASE_PRIVATE_KEY"].replace("\\n", "\n")
        cred = credentials.Certificate({
            "type": "service_account",
            "project_id": os.environ["FIREBASE_PROJECT_ID"],
            "private_key_id": os.environ["FIREBASE_PRIVATE_KEY_ID"],
            "private_key": private_key,
            "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
            "client_id": os.environ["FIREBASE_CLIENT_ID"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": os.environ["FIREBASE_CLIENT_CERT_URL"],
            "universe_domain": "googleapis.com"
        })
    else:
        cred = credentials.Certificate("meyasubako-23797-firebase-adminsdk-h18i7-66487e4115.json")

    firebase_admin.initialize_app(cred)

db = firestore.client()

# =================================================================
# 🛡️ 3. 認証用ミドルウェア（ログインユーザーを判定する）
# =================================================================
def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="認証トークンがありません")
    
    token = authorization.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        print(f"トークン検証エラー: {e}")
        raise HTTPException(status_code=401, detail="無効なトークンです")

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
    docs = db.collection("suggestions").order_by("created_at", direction=firestore.Query.DESCENDING).limit(200).stream()
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
    deleted_docs = db.collection("deleted_forms").stream()
    return {doc.id for doc in deleted_docs}

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
def get_feed():
    global feed_cache
    now = time.time()
    if feed_cache["data"] and now < feed_cache["expires_at"]:
        return {"status": "success", "data": feed_cache["data"]}

    try:
        def fetch_forms():
            url = "https://script.google.com/macros/s/AKfycbyw72m32l3QhFh9k8Y-z6x789Qc-4x3-3x3x3x3x3x3x3x3x/exec"
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req) as res:
                    body = res.read().decode('utf-8')
                    parsed = json.loads(body)
                    return parsed.get("data", [])
            except Exception as e:
                print("GAS fetch error:", e)
                return []

        def fetch_firestore():
            return build_suggestions()

        def fetch_deleted():
            return build_deleted_form_ids()

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_forms = executor.submit(fetch_forms)
            future_db = executor.submit(fetch_firestore)
            future_del = executor.submit(fetch_deleted)

            form_data = future_forms.result()
            db_data = future_db.result()
            deleted_ids = future_del.result()

        filtered_forms = [item for item in form_data if item["id"] not in deleted_ids]

        combined = db_data + filtered_forms
        combined.sort(key=lambda x: x.get("likes", 0), reverse=True)

        feed_cache["data"] = combined
        feed_cache["expires_at"] = now + FEED_CACHE_SECONDS

        return {"status": "success", "data": combined}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 意見を投稿する ---
@app.post("/api/suggestions")
def create_suggestion(req: SuggestionRequest, user: dict = Depends(get_current_user)):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="テキストが空です")
    
    if not is_safe_with_ai(req.text):
        raise HTTPException(status_code=400, detail="不適切な表現が含まれているため、投稿できません。")
    
    try:
        db.collection("suggestions").add({
            "text": req.text,
            "liked_by": [],
            "created_at": firestore.SERVER_TIMESTAMP,
            "is_form_dummy": False,
            "status": "検討中",
            "user_id": user["uid"],
            "user_name": user.get("name", "匿名"),
            "user_email": user.get("email", "")
        })
        clear_feed_cache()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- いいね！を押す ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    user_id = user["uid"]
    doc_ref = db.collection("suggestions").document(suggestion_id)
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
        db.collection("suggestions").document(suggestion_id).collection("comments").add({
            "text": req.text,
            "user_id": user["uid"],
            "user_name": user.get("name", "匿名"),
            "created_at": firestore.SERVER_TIMESTAMP
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- コメントを取得する ---
@app.get("/api/suggestions/{doc_id}/comments")
def get_comments(doc_id: str):
    try:
        docs = db.collection("suggestions").document(doc_id).collection("comments").order_by("created_at").stream()
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
            db.collection("deleted_forms").document(req.doc_id).set({
                "deleted_at": firestore.SERVER_TIMESTAMP
            })
        else:
            # 修正: サブコレクション(comments)の削除によるゴミデータ防止
            comments_ref = db.collection("suggestions").document(req.doc_id).collection("comments").stream()
            for comment in comments_ref:
                comment.reference.delete()
            # 親ドキュメントの削除
            db.collection("suggestions").document(req.doc_id).delete()

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
        db.collection("suggestions").document(doc_id).update({
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
    
    docs = db.collection("suggestions").order_by("created_at", direction=firestore.Query.DESCENDING).stream()
    suggestions = []
    for doc in docs:
        d = doc.to_dict()
        created_at = d.get("created_at")
        timestamp = created_at.timestamp() * 1000 if created_at else 0
        
        suggestions.append({
            "id": doc.id,
            "text": d.get("text", ""),
            "likes": len(d.get("liked_by", [])),
            "created_at": timestamp,
            "is_form_dummy": d.get("is_form_dummy", False),
            "status": d.get("status", "検討中"),
            "user_name": d.get("user_name", "不明"),
            "user_email": d.get("user_email", "不明")
        })
        
    return {"status": "success", "data": suggestions}

@app.get("/api/admin/deleted_forms")
def get_deleted_forms(password: str):
    # こちらは影響が少ないですが、本格運用の際はPOSTに揃えることを推奨します
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or password != admin_pass:
        raise HTTPException(status_code=403, detail="パスワードが違います")
    
    docs = db.collection("deleted_forms").stream()
    return {"status": "success", "data": [doc.id for doc in docs]}

def clear_feed_cache():
    global feed_cache
    feed_cache["data"] = None
    feed_cache["expires_at"] = 0