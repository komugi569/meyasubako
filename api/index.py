import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, firestore, auth

# 💡 最新のGeminiライブラリをインポート
from google import genai

app = FastAPI()

# =================================================================
# 🤖 1. Gemini AI フィルタリング機能（最新 SDK 対応版）
# =================================================================
def is_safe_with_ai(text: str) -> bool:
    """投稿内容が適切かどうかをAI（Gemini）に判定させる関数"""
    if "GEMINI_API_KEY" not in os.environ:
        print("警告: GEMINI_API_KEY が未設定のため、AIチェックをスキップします。")
        return True

    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        prompt = f"""
        あなたは学校の目安箱の優秀なモデレーターです。
        以下の投稿内容が「学校の目安箱として適切か」を判定し、「OK」または「NG」の2文字だけで答えてください。
        
        【NGの条件】
        ・暴言、誹謗中傷、卑猥な言葉が含まれている
        ・「ああああ」「www」などの意味のない文字の羅列
        ・特定の個人や先生、生徒を名指しで攻撃している
        
        【投稿内容】
        {text}
        """
        
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt
        )
        return "OK" in response.text.strip()
        
    except Exception as e:
        print(f"AIチェックエラー: {e}")
        return True # エラー時はアプリを止めないために通過させる


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
    """送信されてきたGoogleログインのトークンが本物か検証する"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="ログインが必要です")
    
    id_token = authorization.split("Bearer ")[1]
    try:
        return auth.verify_id_token(id_token)
    except Exception:
        raise HTTPException(status_code=401, detail="認証トークンが無効です")


# =================================================================
# 🛣️ 4. APIエンドポイント（フロントエンドとの通信口）
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
            
            # 💡 【追加】JavaScriptが並び替えやすいように、時間を「ミリ秒」に変換
            created_at = data.get("created_at")
            timestamp = created_at.timestamp() * 1000 if created_at else 0
            
            suggestions.append({
                "id": doc.id,
                "text": data.get("text", ""),
                "likes": len(liked_by),
                "created_at": timestamp,
                "is_form_dummy": data.get("is_form_dummy", False) # フォームのいいね対応用
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
        
    # 🌟 AIに判定してもらう
    if not is_safe_with_ai(text):
        raise HTTPException(status_code=400, detail="不適切な内容のため投稿できません。")
        
    try:
        new_doc_ref = db.collection("suggestions").document()
        new_doc_ref.set({
            "text": text,
            "user_id": user["uid"],
            "liked_by": [],
            "created_at": firestore.SERVER_TIMESTAMP # 💡【追加】投稿された時間を記録！
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
        # 💡 【追加】もしGoogleフォームの意見（form-XXX）に初めていいねが押されたら、
        # Firestore内に「いいねの数だけを数える専用の箱」を自動で作る！
        if suggestion_id.startswith("form-"):
            doc_ref.set({
                "is_form_dummy": True,
                "liked_by": [user_id],
                "created_at": firestore.SERVER_TIMESTAMP
            })
            return {"status": "liked", "likes": 1}
        else:
            raise HTTPException(status_code=404, detail="意見が見つかりません")
        
    # すでに箱が存在する場合は、いいねの追加/解除を行う
    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    
    if user_id in liked_by:
        liked_by.remove(user_id) # いいね解除
        status = "unliked"
    else:
        liked_by.append(user_id) # いいね追加
        status = "liked"
        
    doc_ref.update({"liked_by": liked_by})
    return {"status": status, "likes": len(liked_by)}