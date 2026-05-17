from fastapi import FastAPI, HTTPException, Header, Depends
from pydantic import BaseModel
from typing import List, Optional
import firebase_admin
from firebase_admin import credentials, firestore, auth

app = FastAPI()

# --- Firebaseの初期化 ---
# 初回起動時のみ、ダウンロードした秘密鍵を使ってFirebaseと接続します
if not firebase_admin._apps:
    cred = credentials.Certificate("firebase-key.json")
    firebase_admin.initialize_app(cred)

db = firestore.client()

# --- データの型定義 ---
class SuggestionInput(BaseModel):
    text: str

# --- 共通処理：Googleログインのトークンを検証する関数 ---
# フロントエンドから送られてきた「私は確かにログインした生徒です」という証明書（Token）をチェックします
def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="ログインが必要です")
    
    id_token = authorization.split("Bearer ")[1]
    try:
        # Firebaseに問い合わせて、トークンが本物か検証する
        decoded_token = auth.verify_id_token(id_token)
        email = decoded_token.get("email", "")
        
        # 【学校限定ルール】学校指定のドメイン（例: @school.ed.jp）だけで制限する場合
        # if not email.endswith("@school.ed.jp"):
        #     raise HTTPException(status_code=403, detail="学校のアカウントでログインしてください")
            
        return decoded_token # ログイン成功ならユーザー情報を返す
    except Exception:
        raise HTTPException(status_code=401, detail="認証トークンが無効です")


# --- 1. 意見一覧を取得する（いいね順ランキング） ---
@app.get("/api/suggestions")
def get_suggestions():
    try:
        # Firestoreの「suggestions」コレクションから全データを取得
        docs = db.collection("suggestions").stream()
        suggestions = []
        
        for doc in docs:
            data = doc.to_dict()
            # いいねをくれた人のリスト（liked_by）の人数をカウント
            liked_by = data.get("liked_by", [])
            suggestions.append({
                "id": doc.id,
                "text": data.get("text", ""),
                "likes": len(liked_by) # リストの長さ＝いいねの数
            })
            
        # Python側でいいねの多い順に並び替える
        return sorted(suggestions, key=lambda x: x["likes"], reverse=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 2. 新しい意見を投稿する ---
@app.post("/api/suggestions")
def create_suggestion(data: SuggestionInput, user: dict = Depends(get_current_user)):
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="内容を入力してください")
        
    try:
        # Firestoreに新しいドキュメントを追加
        new_doc_ref = db.collection("suggestions").document()
        new_doc_ref.set({
            "text": data.text,
            "user_id": user["uid"], # 表示は匿名にするが、裏には投稿者のIDを記録（暴言対策）
            "liked_by": []          # 最初は誰もいいねしていないので空リスト
        })
        return {"status": "success", "id": new_doc_ref.id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- 3. いいね！を押す（二重投票防止機能付き） ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str, user: dict = Depends(get_current_user)):
    user_id = user["uid"]
    doc_ref = db.collection("suggestions").document(suggestion_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        raise HTTPException(status_code=404, detail="指定された意見が見つかりません")
        
    data = doc.to_dict()
    liked_by = data.get("liked_by", [])
    
    # 💡 二重投票のチェック！
    if user_id in liked_by:
        # すでにいいねしていたら、リストから削除する（いいね解除）
        liked_by.remove(user_id)
        status = "unliked"
    else:
        # まだいいねしていなければ、リストにユーザーIDを追加する
        liked_by.append(user_id)
        status = "liked"
        
    # データベースを更新
    doc_ref.update({"liked_by": liked_by})
    return {"status": status, "likes": len(liked_by)}