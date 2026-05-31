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

app = FastAPI()
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
        cred_dict = {
            "type": "service_account",
            "project_id": os.environ["FIREBASE_PROJECT_ID"],
            "private_key": private_key,
            "client_email": os.environ["FIREBASE_CLIENT_EMAIL"],
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        cred = credentials.Certificate(cred_dict)
    elif os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
    else:
        raise Exception("Firebaseの認証情報が見つかりません。")
        
    firebase_admin.initialize_app(cred)

db = firestore.client()


# =================================================================
# 📋 3. データ定義と共通のログイン認証処理
# =================================================================
class SuggestionInput(BaseModel):
    text: str

class DeleteRequest(BaseModel):
    doc_id: str
    password: str

class StatusRequest(BaseModel):
    password: str
    status: str

class CommentRequest(BaseModel):
    text: str

def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="ログインが必要です")
    
    id_token = authorization.split("Bearer ")[1]
    try:
        return auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="認証トークンが無効です")


# =================================================================
# 🛣️ 4. APIエンドポイント (一般ユーザー向け)
# =================================================================
def build_suggestions():
    docs = db.collection("suggestions").stream()
    suggestions = []

    for doc in docs:
        data = doc.to_dict()
        liked_by = data.get("liked_by", [])

        created_at = data.get("created_at")
        timestamp = created_at.timestamp() * 1000 if created_at else 0

        suggestions.append({
            "id": doc.id,
            "text": data.get("text", ""),
            "likes": len(liked_by),
            "created_at": timestamp,
            "is_form_dummy": data.get("is_form_dummy", False),
            "status": data.get("status", "検討中") # 💡 ステータスを追加（デフォルトは検討中）
            # 🛡️ 匿名性を守るため、一般向けAPIには user_name や user_email は絶対に含めない
        })

    return sorted(suggestions, key=lambda x: x["likes"], reverse=True)


def build_deleted_form_ids():
    docs = db.collection("deleted_forms").stream()
    return [doc.id for doc in docs]


# --- 意見一覧を取得する ---
@app.get("/api/suggestions")
def get_suggestions():
    try:
        return build_suggestions()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- カード初期表示用: 投稿一覧と非表示フォームIDをまとめて取得する ---
@app.get("/api/feed")
def get_feed():
    try:
        now = time.time()
        if feed_cache["data"] is not None and feed_cache["expires_at"] > now:
            return feed_cache["data"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            suggestions_future = executor.submit(build_suggestions)
            deleted_forms_future = executor.submit(build_deleted_form_ids)

            data = {
                "suggestions": suggestions_future.result(),
                "deleted_form_ids": deleted_forms_future.result()
            }

            feed_cache["data"] = data
            feed_cache["expires_at"] = now + FEED_CACHE_SECONDS
            return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 新しい意見を投稿する ---
@app.post("/api/suggestions")
def create_suggestion(data: SuggestionInput, user: dict = Depends(get_current_user)):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容を入力してください")
        
    if not is_safe_with_ai(text):
        raise HTTPException(status_code=400, detail="不適切な内容のため投稿できません。")
        
    try:
        # 💡 Firebaseのトークンから名前とメールアドレスを抽出（管理者画面用）
        user_name = user.get("name", "匿名生徒")
        user_email = user.get("email", "不明")

        new_doc_ref = db.collection("suggestions").document()
        new_doc_ref.set({
            "text": text,
            "user_id": user["uid"],
            "user_name": user_name,     # 💡 投稿者の名前を記録
            "user_email": user_email,   # 💡 投稿者のメールを記録
            "status": "検討中",         # 💡 初期ステータスを記録
            "liked_by": [],
            "created_at": firestore.SERVER_TIMESTAMP 
        })
        clear_feed_cache()
        return {"status": "success", "id": new_doc_ref.id}
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
                "status": "検討中", # フォームダミーにもステータスを持たせる
                "liked_by": [user_id],
                "created_at": firestore.SERVER_TIMESTAMP
            })
            clear_feed_cache()
            return {"status": "liked", "likes": 1}
        else:
            raise HTTPException(status_code=404, detail="意見が見つかりません")
        
    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    
    if user_id in liked_by:
        liked_by.remove(user_id) 
        status = "unliked"
    else:
        liked_by.append(user_id) 
        status = "liked"
        
    doc_ref.update({"liked_by": liked_by})
    clear_feed_cache()
    return {"status": status, "likes": len(liked_by)}

# =================================================================
# 💬 5. コメント機能API
# =================================================================
@app.post("/api/suggestions/{doc_id}/comments")
def add_comment(doc_id: str, req: CommentRequest, user: dict = Depends(get_current_user)):
    try:
        user_name = user.get("name", "匿名生徒")
        comment_ref = db.collection("suggestions").document(doc_id).collection("comments").document()
        comment_ref.set({
            "text": req.text,
            "user_name": user_name,
            "created_at": firestore.SERVER_TIMESTAMP
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/suggestions/{doc_id}/comments")
def get_comments(doc_id: str):
    try:
        docs = db.collection("suggestions").document(doc_id).collection("comments").order_by("created_at").stream()
        comments = []
        for doc in docs:
            d = doc.to_dict()
            comments.append({
                "user_name": d.get("user_name", "匿名"), 
                "text": d.get("text", "")
            })
        return comments
    except Exception as e:
        print(f"コメント取得エラー: {e}")
        return []

# =================================================================
# 🛡️ 6. 管理者専用API（削除・ステータス更新・身元確認）
# =================================================================

# --- 投稿削除 ---
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
        # フォームダミーの場合、ドキュメントが存在しない状態でステータス更新されるのを防ぐ
        doc_ref = db.collection("suggestions").document(doc_id)
        if not doc_ref.get().exists and doc_id.startswith("form-"):
            doc_ref.set({
                "is_form_dummy": True,
                "status": req.status,
                "liked_by": [],
                "created_at": firestore.SERVER_TIMESTAMP
            })
        else:
            doc_ref.update({"status": req.status})
            
        clear_feed_cache()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 管理者専用の意見一覧取得（個人情報を含む） ---
@app.get("/api/admin/suggestions")
def get_admin_suggestions(password: str):
    admin_pass = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_pass or password != admin_pass:
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
            "user_name": d.get("user_name", "不明"),   # 💡 誰が書いたか
            "user_email": d.get("user_email", "不明")  # 💡 メールアドレス
        })
    return suggestions


# --- 非表示リスト取得 ---
@app.get("/api/deleted_forms")
def get_deleted_forms():
    try:
        return build_deleted_form_ids()
    except Exception as e:
        print(f"リスト取得エラー: {e}")
        return []


def clear_feed_cache():
    feed_cache["expires_at"] = 0
    feed_cache["data"] = None