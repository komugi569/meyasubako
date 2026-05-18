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
        // ① Pythonバックエンド（Firestore）からデータを取得
        const res = await fetch("/api/suggestions");
        const suggestions = await res.json();

        // ② Googleフォーム（GAS）からデータを取得
        const gasRes = await fetch(GAS_URL);
        const formData = await gasRes.json();

        // ③ GASのデータをアプリの形式に合わせる（時間も変換）
        let formSuggestions = formData.map((item, idx) => ({
            id: `form-${idx}`,
            text: item.content,
            likes: 0, 
            isForm: true,
            created_at: item.timestamp ? new Date(item.timestamp).getTime() : 0 
        }));

        // ④ データを合体！
        let allSuggestions = suggestions.concat(formSuggestions);

        // ⑤ 💡 現在のタブに合わせて並び替える！
        if (currentTab === "popular") {
            allSuggestions.sort((a, b) => b.likes - a.likes); // いいねが多い順
        } else if (currentTab === "new") {
            allSuggestions.sort((a, b) => b.created_at - a.created_at); // 時間が新しい順
        }

        // ⑥ 画面に出力する
        const listElement = document.getElementById("suggestion-list");
        if (!listElement) return; // エラー防止
        listElement.innerHTML = ""; // 一旦リストをリセットして空にする

        allSuggestions.forEach((item, index) => {
            const rank = index + 1;
            const card = document.createElement("li");
            card.className = "suggestion-card";
            
            // 💡 フォームの意見であることが分かるように小さな文字を添える（ボタンは隠さない！）
            let badgeHtml = item.isForm 
                ? `<div style="color: #888; font-size: 0.8em; margin-bottom: 5px;">📋 フォームからの意見</div>` 
                : "";

            card.innerHTML = `
                <div class="rank-badge rank-${rank <= 3 ? rank : 'other'}">${rank}</div>
                <div class="content">
                    <p class="text">${escapeHtml(item.text)}</p>
                    ${badgeHtml}
                    <div class="actions">
                        <button class="like-btn" id="like-${item.id}">❤️ いいね</button>
                        <span class="like-count">${item.likes}</span>
                    </div>
                </div>
            `;
            listElement.appendChild(card);

            // 💡 すべての意見（自作アプリもフォームも）にいいねを押せるようにイベントを紐付ける
            document.getElementById(`like-${item.id}`).addEventListener("click", () => likeSuggestion(item.id));
        });
    } catch (error) {
        console.error("エラー:", error);
    }
}


// --- 2. 新しい意見を投稿する（要ログイン・連打防止付き） ---
async function createSuggestion() {
    if (!currentUserToken) {
        alert("意見を投稿するにはログインが必要です！");
        return;
    }

    const inputElement = document.getElementById("suggestion-input");
    const submitBtn = document.getElementById("submit-btn"); // 💡 ボタンを取得
    const text = inputElement.value.trim();
    if (!text) return;

    // 🛡️ ボタンを無効化して連打を防ぐ
    submitBtn.disabled = true;
    submitBtn.innerText = "送信中...";

    try {
        const response = await fetch("/api/suggestions", {
            method: "POST",
            headers: { 
                "Content-Type": "application/json",
                "Authorization": `Bearer ${currentUserToken}`
            },
            body: JSON.stringify({ text: text })
        });

        if (!response.ok) {
            // Pythonから送られてきたエラーメッセージ（429など）を読み取って表示
            const errorData = await response.json();
            throw new Error(errorData.detail || "投稿に失敗しました");
        }

        inputElement.value = "";
        fetchSuggestions();
    } catch (error) {
        alert(error.message); // 💡 AIからの警告やエラーをそのまま画面に出す
    } finally {
        // 🛡️ 成功しても失敗しても、10秒後にボタンを復活させる（クールダウン）
        setTimeout(() => {
            submitBtn.disabled = false;
            submitBtn.innerText = "投稿する";
        }, 10000); // 10000ミリ秒 = 10秒
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

// --- タブ切り替え機能 ---
let currentTab = "post"; // 今開いているタブを記憶する変数

function switchTab(tabName) {
    currentTab = tabName;
    
    // 一旦両方のエリアを隠す
    document.getElementById("post-section").style.display = "none";
    document.getElementById("list-section").style.display = "none";

    if (tabName === "post") {
        // 「投稿する」が選ばれたら投稿画面だけ見せる
        document.getElementById("post-section").style.display = "block";
    } else {
        // 「人気順」「新着順」が選ばれたらリスト画面を見せて、データを取得する
        document.getElementById("list-section").style.display = "block";
        fetchSuggestions();
    }
}

function escapeHtml(str) {
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// 💡 HTMLの onclick から関数を呼び出せるように、外の世界に公開します！
window.switchTab = switchTab;
window.createSuggestion = createSuggestion;