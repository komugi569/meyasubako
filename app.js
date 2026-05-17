import { initializeApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged } from "firebase/auth";

// 💡 【重要】あなたのFirebaseコンソールからコピーしたConfigをここに貼り付けてください！
// Import the functions you need from the SDKs you need
import { initializeApp } from "firebase/app";
import { getAnalytics } from "firebase/analytics";
// TODO: Add SDKs for Firebase products that you want to use
// https://firebase.google.com/docs/web/setup#available-libraries

// Your web app's Firebase configuration
// For Firebase JS SDK v7.20.0 and later, measurementId is optional
const firebaseConfig = {
  apiKey: "AIzaSyCTbxVNCdcJyFpYAfONVhmr9lPlFPK6Hvc",
  authDomain: "meyasubako-23797.firebaseapp.com",
  databaseURL: "https://meyasubako-23797-default-rtdb.firebaseio.com",
  projectId: "meyasubako-23797",
  storageBucket: "meyasubako-23797.firebasestorage.app",
  messagingSenderId: "368665628904",
  appId: "1:368665628904:web:68313348fb56c23b01795d",
  measurementId: "G-Y57PT610HG"
};

// Initialize Firebase
const app = initializeApp(firebaseConfig);
const analytics = getAnalytics(app);

// Firebaseの初期化
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const provider = new GoogleAuthProvider();

let currentUserToken = null; // ログインした生徒の証明書を保存する変数

// 💡 囲みを外して、直接ボタンに機能を登録します（type="module"なのでこれでも安全に動きます）
document.getElementById("login-btn").addEventListener("click", login);
document.getElementById("submit-btn").addEventListener("click", createSuggestion);

// 生徒のログイン状態を監視する
onAuthStateChanged(auth, async (user) => {
    const loginBtn = document.getElementById("login-btn");
    const userInfo = document.getElementById("user-info");

    if (user) {
        currentUserToken = await user.getIdToken();
        loginBtn.style.display = "none";
        userInfo.style.display = "inline";
        userInfo.innerText = `ログイン中: ${user.displayName}さん`;
    } else {
        currentUserToken = null;
        loginBtn.style.display = "inline";
        userInfo.style.display = "none";
    }
    fetchSuggestions();
});

// Googleログインを実行する関数
async function login() {
    try {
        await signInWithPopup(auth, provider);
    } catch (error) {
        console.error("ログインエラー:", error);
        alert("Googleログインに失敗しました。");
    }
}

// --- 1. 意見一覧を取得して画面に表示する ---
async function fetchSuggestions() {
    try {
        const response = await fetch("/api/suggestions");
        const suggestions = await response.json();
        const listElement = document.getElementById("suggestion-list");
        listElement.innerHTML = "";

        suggestions.forEach((item, index) => {
            const rank = index + 1;
            const card = document.createElement("li");
            card.className = "suggestion-card";
            card.innerHTML = `
                <div class="rank-badge rank-${rank <= 3 ? rank : 'other'}">${rank}</div>
                <div class="content">
                    <p class="text">${escapeHtml(item.text)}</p>
                    <div class="actions">
                        <button class="like-btn" id="like-${item.id}">❤️ いいね</button>
                        <span class="like-count">${item.likes}</span>
                    </div>
                </div>
            `;
            listElement.appendChild(card);

            // いいねボタンにイベントを設定
            document.getElementById(`like-${item.id}`).addEventListener("click", () => likeSuggestion(item.id));
        });
    } catch (error) {
        console.error("エラー:", error);
    }
}

// --- 2. 新しい意見を投稿する（要ログイン） ---
async function createSuggestion() {
    if (!currentUserToken) {
        alert("意見を投稿するにはログインが必要です！");
        return;
    }

    const inputElement = document.getElementById("suggestion-input");
    const text = inputElement.value.trim();
    if (!text) return;

    try {
        const response = await fetch("/api/suggestions", {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "Authorization": `Bearer ${currentUserToken}` // 💡 ヘッダーに証明書を添付
            },
            body: JSON.stringify({ text: text })
        });

        if (!response.ok) throw new Error("投稿に失敗しました");
        inputElement.value = "";
        fetchSuggestions();
    } catch (error) {
        alert("投稿に失敗しました。学校のアカウントか確認してください。");
    }
}

// --- 3. いいね！を押す（要ログイン・二重投票防止） ---
async function likeSuggestion(id) {
    if (!currentUserToken) {
        alert("「いいね」をするにはログインが必要です！");
        return;
    }

    try {
        const response = await fetch(`/api/suggestions/${id}/like`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${currentUserToken}` // 💡 ヘッダーに証明書を添付
            }
        });

        if (!response.ok) throw new Error("いいねに失敗しました");
        fetchSuggestions(); // ランキングを再読み込み
    } catch (error) {
        console.error(error);
    }
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}