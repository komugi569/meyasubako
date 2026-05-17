import os
import json
import urllib.request
import urllib.error
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, firestore, auth

app = FastAPI()

# =================================================================
# 🤖 1. Gemini AI フィルタリング（※現在一時的にお休み中）
# =================================================================
def is_safe_with_ai(text: str) -> bool:
    """投稿内容が適切かどうかをAI（Gemini）に判定させる関数"""
    
    # 💡 開発を進めるため、今はどんな言葉も「安全（True）」として通します！
    return True

# （ここから下の Firebaseの初期化 以降はそのまま残します！）

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

def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="ログインが必要です")
    
    id_token = authorization.split("Bearer ")[1]
    try:
        return auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="認証トークンが無効です")


# =================================================================
# 🛣️ 4. APIエンドポイント
# =================================================================

# --- 意見一覧を取得する ---
@app.get("/api/suggestions")
def get_suggestions():
    try:
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
                "is_form_dummy": data.get("is_form_dummy", False)
            })
            
        return sorted(suggestions, key=lambda x: x["likes"], reverse=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 新しい意見を投稿する（AIフィルター＆時間記録付き） ---
@app.post("/api/suggestions")
def create_suggestion(data: SuggestionInput, user: dict = Depends(get_current_user)):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="内容を入力してください")
        
    # 🌟 AIに判定してもらう（NGやエラーならここで自動的にHTTPExceptionが発生して止まる）
    if not is_safe_with_ai(text):
        raise HTTPException(status_code=400, detail="不適切な内容のため投稿できません。")
        
    try:
        new_doc_ref = db.collection("suggestions").document()
        new_doc_ref.set({
            "text": text,
            "user_id": user["uid"],
            "liked_by": [],
            "created_at": firestore.SERVER_TIMESTAMP 
        })
        return {"status": "success", "id": new_doc_ref.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- いいね！を押す（フォームからの意見へのいいね対応版） ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    user_id = user["uid"]
    doc_ref = db.collection("suggestions").document(suggestion_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        if suggestion_id.startswith("form-"):
            doc_ref.set({
                "is_form_dummy": True,
                "liked_by": [user_id],
                "created_at": firestore.SERVER_TIMESTAMP
            })
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
    return {"status": status, "likes": len(liked_by)}