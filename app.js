import { initializeApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged ,signOut} from "firebase/auth";

// Your web app's Firebase configuration
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

// Firebaseの初期化
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: 'select_account' });

let currentUserToken = null;

const GAS_URL = "https://script.google.com/macros/s/AKfycbxZztgHvkKfaH3WPkWEH8f9KoiBSAFNrbPFgKkbAbLnyy_-VNjhBHSfIJ04DGJraM0T/exec"; 

document.getElementById("login-btn").addEventListener("click", login);
document.getElementById("submit-btn").addEventListener("click", createSuggestion);
document.getElementById("logout-btn").addEventListener("click", logout);

// 生徒のログイン状態を監視する
onAuthStateChanged(auth, async (user) => {
    const loginBtn = document.getElementById("login-btn");
    const userInfo = document.getElementById("user-info");
    const logoutBtn = document.getElementById("logout-btn"); 

    if (user) {
        currentUserToken = await user.getIdToken();
        loginBtn.style.display = "none";
        userInfo.style.display = "inline";
        userInfo.innerText = `ログイン中: ${user.displayName}さん`;
        logoutBtn.style.display = "inline";
    } else {
        currentUserToken = null;
        loginBtn.style.display = "inline";
        userInfo.style.display = "none";
        logoutBtn.style.display = "none";
    }
    fetchSuggestions();
});

async function login() {
    try {
        await signInWithPopup(auth, provider);
    } catch (error) {
        console.error("ログインエラー:", error);
        alert("Googleログインに失敗しました。");
    }
}

async function logout() {
    try {
        await signOut(auth);
        alert("ログアウトしました。");
    } catch (error) {
        console.error("ログアウトエラー:", error);
        alert("ログアウトに失敗しました。");
    }
}

// --- 1. 意見一覧を取得して画面に表示する（合流版） ---
async function fetchSuggestions() {
    try {
        // ① 自作アプリのAPI（Firestore）からデータを取得
        const response = await fetch("/api/suggestions");
        let suggestions = await response.json();

        // ② Googleフォーム（GAS）からデータを取得
        let formSuggestions = [];
        if (GAS_URL && !GAS_URL.includes("XXXXX")) {
            try {
                const formResponse = await fetch(GAS_URL);
                const formData = await formResponse.json();
                
                // GASのデータ（content）を、アプリの形式（text）に変換して揃える
                formSuggestions = formData.map((item, idx) => ({
                    id: `form-${idx}`, // フォーム用のダミーID
                    text: item.content, 
                    likes: 0,           // フォームからの意見は最初いいね0
                    isForm: true        // フォームからの意見だとわかる目印
                }));
            } catch (e) {
                console.error("Googleフォームデータの取得に失敗:", e);
            }
        }

        // ③ 2つのデータを1つの配列に合体させる！
        let allSuggestions = suggestions.concat(formSuggestions);

        // ④ ランキング形式なので、いいね（likes）が多い順に並び替える
        allSuggestions.sort((a, b) => b.likes - a.likes);

        const listElement = document.getElementById("suggestion-list");
        listElement.innerHTML = "";

        // ⑤ 画面に出力する
        allSuggestions.forEach((item, index) => {
            const rank = index + 1;
            const card = document.createElement("li");
            card.className = "suggestion-card";
            
            // 💡 フォームからの意見の場合、いいねボタンを隠してバッジを出す
            const actionsHtml = item.isForm 
                ? `<span style="color: #888; font-size: 0.9em;">📋 フォームからの意見</span>`
                : `
                    <button class="like-btn" id="like-${item.id}">❤️ いいね</button>
                    <span class="like-count">${item.likes}</span>
                `;

            card.innerHTML = `
                <div class="rank-badge rank-${rank <= 3 ? rank : 'other'}">${rank}</div>
                <div class="content">
                    <p class="text">${escapeHtml(item.text)}</p>
                    <div class="actions">
                        ${actionsHtml}
                    </div>
                </div>
            `;
            listElement.appendChild(card);

            // 自作アプリの意見にだけ、いいねボタンのイベントを設定する
            if (!item.isForm) {
                document.getElementById(`like-${item.id}`).addEventListener("click", () => likeSuggestion(item.id));
            }
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
                "Authorization": `Bearer ${currentUserToken}`
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
                "Authorization": `Bearer ${currentUserToken}`
            }
        });

        if (!response.ok) throw new Error("いいねに失敗しました");
        fetchSuggestions();
    } catch (error) {
        console.error(error);
    }
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}