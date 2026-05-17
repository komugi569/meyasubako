from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List
import uuid

app = FastAPI()

# --- データの型定義 ---
# フロントエンドから送られてくるデータの形を定義します
class SuggestionInput(BaseModel):
    text: str

# 画面に返すデータの形を定義します
class SuggestionOut(BaseModel):
    id: str
    text: str
    likes: int


# --- 簡易データベース（テスト用） ---
# 本来はFirebaseに保存しますが、まずはPython内のリストにダミーデータを入れておきます
# （※Vercelの仕様上、サーバーがスリープするとリセットされますが、テストには最適です）
suggestions_db = [
    {"id": "1", "text": "体育館にエアコンをつけてほしいです。夏場の集会が本当にしんどいです。", "likes": 128},
    {"id": "2", "text": "学食のメニューにデザートを追加してほしい！", "likes": 95},
    {"id": "3", "text": "図書室の開館時間をあと30分延ばしてもらえませんか？", "likes": 62},
]


# --- 1. 意見一覧を取得する窓口（いいね順ランキング） ---
@app.get("/api/suggestions", response_model=List[SuggestionOut])
def get_suggestions():
    # sortedを使って、likes（いいね数）が多い順（reverse=True）に並び替えます
    sorted_data = sorted(suggestions_db, key=lambda x: x["likes"], reverse=True)
    return sorted_data


# --- 2. 新しい意見を投稿する窓口 ---
@app.post("/api/suggestions", response_model=SuggestionOut)
def create_suggestion(data: SuggestionInput):
    # 文字が空っぽ、またはスペースだけの場合はエラーにする
    if not data.text.strip():
        raise HTTPException(status_code=400, detail="内容を入力してください")
    
    # 新しい意見を作成（uuidを使って被らないユニークなIDを自動発行）
    new_suggestion = {
        "id": str(uuid.uuid4()),
        "text": data.text,
        "likes": 0
    }
    
    # データベース（リスト）に追加
    suggestions_db.append(new_suggestion)
    return new_suggestion


# --- 3. いいね！を1つ増やす窓口 ---
@app.post("/api/suggestions/{suggestion_id}/like")
def like_suggestion(suggestion_id: str):
    # 送られてきたIDと同じ意見をデータの中から探す
    for item in suggestions_db:
        if item["id"] == suggestion_id:
            item["likes"] += 1  # いいねを+1する
            return {"status": "success", "id": suggestion_id, "likes": item["likes"]}
    
    # IDが見つからなかった場合は404エラーを返す
    raise HTTPException(status_code=404, detail="指定された意見が見つかりません")