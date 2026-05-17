import os
import json
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
import firebase_admin
from firebase_admin import credentials, firestore, auth
import google.generativeai as genai

app = FastAPI()

# =================================================================
# 🤖 1. Gemini AI フィルタリングの設定
# =================================================================

# Vercel上の環境変数に「GEMINI_API_KEY」があればAIを初期化
if "GEMINI_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

def is_safe_with_ai(text: str) -> bool:
    """投稿内容が適切かどうかをAI（Gemini）に判定させる関数"""
    # もし環境変数にキーが登録されていなければ、チェックをスキップして通過させる（開発用）
    if "GEMINI_API_KEY" not in os.environ:
        print("警告: GEMINI_API_KEY が設定されていないため、AIチェックをスキップします。")
        return True

    try:
        # 高速・軽量な最新モデルを使用
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # AIへの指示書（プロンプト）
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
        
        response = model.generate_content(prompt)
        result = response.text.strip()
        
        # AIが「OK」という文字を返してきた場合のみ安全（True）と判定
        return "OK" in result
        
    except Exception as e:
        print(f"AIチェック中にエラーが発生しました: {e}")
        # AIのサーバー障害などの時は、アプリを止めないために安全弁として通過させる
        return True


# =================================================================
# 🔥 2. Firebase / Firestore の初期化（絶対に崩れない版）
# =================================================================

if not firebase_admin._apps:
    # Vercel（本番環境）用：環境変数から個別に鍵を組み立てる
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
        
    # ローカル環境（自分のパソコン）用：jsonファイルから読み込む
    elif os.path.exists("firebase-key.json"):
        cred = credentials.Certificate("firebase-key.json")
    else:
        raise Exception("Firebaseの認証情報（環境変数またはファイル）が見つかりません。")
        
    firebase_admin.initialize_app(cred)

db = firestore.client() 


# =================================================================
# 📋 3. データ定義（Pydanticモデル）と共通の認証処理
# =================================================================

class SuggestionInput(BaseModel):
    text: str

def get_current_user(authorization: Optional[str] = Header(None)):
    """フロントエンドから送られてきたGoogleログインのトークンが本物か検証する関数"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="ログインが必要です")
    
    id_token = authorization.split("Bearer ")[1]
    try:
        # Firebaseにトークンが本物か直接問い合わせる
        decoded_token = auth.verify_id_token(id_token)
        return decoded_token # 認証成功ならユーザー情報を返す
    except Exception:
        raise HTTPException(status_code=401, detail="認証トークンが無効です")


# =================================================================
# 🛣️ 4. APIエンドポイント（ルート設定）
# =================================================================

# --- 意見一覧を取得する（いいね順ランキング） ---
@app.get("/api/suggestions")
def get_suggestions():
    try:
        docs = db.collection("suggestions").stream()
        suggestions = []
        
        for doc in docs:
            data = doc.to_dict()
            liked_by = data.get("liked_by", [])
            suggestions.append({
                "id": doc.id,
                "text": data.get("text", ""),
                "likes": len(liked_by)
            })
            
        # いいねの数（likes）が多い順にソートして返却
        return sorted(suggestions, key=lambda x: x["likes"], reverse=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 新しい意見を投稿する（ログイン認証 ＆ AIフィルター付き） ---
@app.post("/api/suggestions")
def create_suggestion(data: SuggestionInput, user: dict = Depends(get_current_user)):
    text = data.text.strip()
    
    # 文字が入っているか空文字チェック
    if not text:
        raise HTTPException(status_code=400, detail="内容を入力してください")
        
    # 🌟【AIフィルター発動】Geminiに投稿文が安全か審査してもらう
    if not is_safe_with_ai(text):
        # AIがNGを出したら、400エラーを起こしてここで処理を強制終了する（Firestoreには保存されない）
        raise HTTPException(
            status_code=400, 
            detail="不適切な内容が含まれているか、意味のない文字列のため投稿できません。"
        )
        
    try:
        # 全てのチェックをクリアしたら、Firestoreに安全な意見として保存
        new_doc_ref = db.collection("suggestions").document()
        new_doc_ref.set({
            "text": text,
            "user_id": user["uid"],  # 画面には出さないが、裏に投稿者のUIDを記録（悪戯防止）
            "liked_by": []           # 初期状態はいいねゼロの空リスト
        })
        return {"status": "success", "id": new_doc_ref.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- いいね！を押す（二重投票防止機能付き） ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    user_id = user["uid"]
    doc_ref = db.collection("suggestions").document(suggestion_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise HTTPException(status_code=404, detail="指定された意見が見つかりません")
        
    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    
    # 二重投票チェック
    if user_id in liked_by:
        liked_by.remove(user_id) # すでにいいね済みなら解除
        status = "unliked"
    else:
        liked_by.append(user_id) # 未いいねなら追加
        status = "liked"
        
    doc_ref.update({"liked_by": liked_by})
    return {"status": status, "likes": len(liked_by)}