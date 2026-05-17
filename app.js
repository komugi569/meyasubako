// 画面が読み込まれたら、自動的にランキング一覧を取得する
document.addEventListener("DOMContentLoaded", () => {
    fetchSuggestions();

    // 投稿ボタンが押された時の処理
    const submitBtn = document.getElementById("submit-btn");
    submitBtn.addEventListener("click", createSuggestion);
});

// --- 1. Pythonから意見一覧（ランキング）を取得して画面に表示する関数 ---
async function fetchSuggestions() {
    try {
        const response = await fetch("/api/suggestions");
        if (!response.ok) throw new Error("データの取得に失敗しました");
        
        const suggestions = await response.json();
        const listElement = document.getElementById("suggestion-list");
        
        // 一度リストを綺麗にリセットする
        listElement.innerHTML = "";

        // Pythonから届いたデータを1つずつ画面に組み立てていく
        suggestions.forEach((item, index) => {
            const rank = index + 1; // 配列は0から始まるので+1して順位にする
            
            // 各意見のカード（HTML）を組み立てる
            const card = document.createElement("li");
            card.className = "suggestion-card";
            card.innerHTML = `
                <div class="rank-badge rank-${rank <= 3 ? rank : 'other'}">${rank}</div>
                <div class="content">
                    <p class="text">${escapeHtml(item.text)}</p>
                    <div class="actions">
                        <button class="like-btn" onclick="likeSuggestion('${item.id}')">❤️ いいね</button>
                        <span class="like-count">${item.likes}</span>
                    </div>
                </div>
            `;
            listElement.appendChild(card);
        });
    } catch (error) {
        console.error("エラー:", error);
        alert("読み込みエラーが発生しました。");
    }
}

// --- 2. 新しい意見をPythonに送信して投稿する関数 ---
async function createSuggestion() {
    const inputElement = document.getElementById("suggestion-input");
    const text = inputElement.value.trim();

    if (!text) {
        alert("意見を入力してください！");
        return;
    }

    try {
        const response = await fetch("/api/suggestions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text: text })
        });

        if (!response.ok) throw new Error("投稿に失敗しました");

        // 入力欄を空っぽにして、最新のランキングを再読み込みする
        inputElement.value = "";
        fetchSuggestions();
    } catch (error) {
        console.error("エラー:", error);
        alert("投稿に失敗しました。");
    }
}

// --- 3. いいね！ボタンが押されたことをPythonに伝える関数 ---
async function likeSuggestion(id) {
    try {
        const response = await fetch(`/api/suggestions/${id}/like`, {
            method: "POST"
        });

        if (!response.ok) throw new Error("いいねに失敗しました");

        // いいねが成功したら、画面のランキングを更新する
        fetchSuggestions();
    } catch (error) {
        console.error("エラー:", error);
        alert("いいねの処理に失敗しました。");
    }
}

// 安全対策：変な文字（悪意のあるプログラム）が投稿されても無効化する関数（XSS対策）
function escapeHtml(str) {
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}