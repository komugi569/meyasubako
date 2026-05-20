import { initializeApp } from "firebase/app";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged, signOut } from "firebase/auth";

// ==========================================
// ⚙️ 1. Firebase & 初期設定
// ==========================================
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

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const provider = new GoogleAuthProvider();
provider.setCustomParameters({ prompt: 'select_account' });

let currentUserToken = null;
let allPosts = []; // すべての投稿データを一時保存する箱

const GAS_URL = "https://script.google.com/macros/s/AKfycbxZztgHvkKfaH3WPkWEH8f9KoiBSAFNrbPFgKkbAbLnyy_-VNjhBHSfIJ04DGJraM0T/exec"; 
const KAIRU_NORMAL_IMAGE = "kairu.png";
const KAIRU_REPLY_IMAGE = "kairu_excel.png";
const KAIRU_NORMAL_TEXT = "何かお困りのことはありますか？";
const KAIRU_REPLY_TEXT = "知りません";

// ==========================================
// 🖱️ 2. イベントリスナーの設定
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
    // ログイン・ログアウトボタンが存在する場合のみイベントを登録
    const loginBtn = document.getElementById("login-btn");
    const logoutBtn = document.getElementById("logout-btn");
    if(loginBtn) loginBtn.addEventListener("click", login);
    if(logoutBtn) logoutBtn.addEventListener("click", logout);

    // 投稿ボタンと入力欄
    document.getElementById("submit-btn").addEventListener("click", createSuggestion);
    const suggestionInput = document.getElementById("suggestion-input") || document.getElementById("suggestionText");
    if(suggestionInput) {
        suggestionInput.addEventListener("input", () => setKairuImage(false));
    }
});

function setKairuImage(isReply) {
    const kairuImage = document.getElementById("kairu-image");
    const kairuTextbox = document.getElementById("kairu-textbox");
    if (kairuImage) kairuImage.src = isReply ? KAIRU_REPLY_IMAGE : KAIRU_NORMAL_IMAGE;
    if (kairuTextbox) kairuTextbox.innerText = isReply ? KAIRU_REPLY_TEXT : KAIRU_NORMAL_TEXT;
}

// ==========================================
// 🔐 3. 認証（ログイン・ログアウト）処理
// ==========================================
onAuthStateChanged(auth, async (user) => {
    const loginBtn = document.getElementById("login-btn");
    const userInfo = document.getElementById("user-info");
    const logoutBtn = document.getElementById("logout-btn"); 

    if (user) {
        currentUserToken = await user.getIdToken();
        if(loginBtn) loginBtn.style.display = "none";
        if(userInfo) {
            userInfo.style.display = "inline";
            userInfo.innerText = `ログイン中: ${user.displayName}さん`;
        }
        if(logoutBtn) logoutBtn.style.display = "inline";
    } else {
        currentUserToken = null;
        if(loginBtn) loginBtn.style.display = "inline";
        if(userInfo) userInfo.style.display = "none";
        if(logoutBtn) logoutBtn.style.display = "none";
    }
    // ログイン状態が確定したらデータを取得
    fetchAllPosts();
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

// ==========================================
// 🔄 4. 画面（タブ）切り替え処理
// ==========================================
// ※グローバルから呼び出せるように window に登録
window.switchTab = function(tabName) {
    document.getElementById('page-post').style.display = (tabName === 'post') ? 'block' : 'none';
    document.getElementById('page-view').style.display = (tabName === 'view') ? 'block' : 'none';
    document.getElementById('page-detail').style.display = 'none';

    document.getElementById('tab-post').classList.toggle('active', tabName === 'post');
    document.getElementById('tab-view').classList.toggle('active', tabName === 'view');

    if (tabName === 'view') {
        fetchAllPosts();
    }
}

window.backToDashboard = function() {
    document.getElementById('page-detail').style.display = 'none';
    document.getElementById('page-view').style.display = 'block';
}

// ==========================================
// 📡 5. 新しい意見の投稿
// ==========================================
async function createSuggestion() {
    if (!currentUserToken) return alert("意見を投稿するにはログインが必要です！");

    const inputElement = document.getElementById("suggestion-input") || document.getElementById("suggestionText");
    const submitBtn = document.getElementById("submit-btn");
    const text = inputElement.value.trim();
    
    if (!text) return alert("意見を入力してください");

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
            const errorData = await response.json();
            throw new Error(errorData.detail || "投稿に失敗しました");
        }

        inputElement.value = "";
        setKairuImage(true);
        alert("投稿しました！");
        window.switchTab('view'); 
    } catch (error) {
        alert(error.message);
    } finally {
        setTimeout(() => {
            submitBtn.disabled = false;
            submitBtn.innerText = "送信する";
        }, 10000); 
    }
}
window.createSuggestion = createSuggestion; // HTMLから呼べるようにする

// ==========================================
// 📦 6. データの取得と結合（Firestore + GAS）
// ==========================================
async function fetchAllPosts() {
    try {
        const res = await fetch("/api/suggestions");
        const suggestions = await res.json();

        const gasRes = await fetch(GAS_URL);
        const formData = await gasRes.json();

        const formSuggestions = formData.map((item, idx) => ({
            id: `form-${idx}`,
            text: item.content,
            likes: 0, 
            isForm: true,
            created_at: item.timestamp ? new Date(item.timestamp).getTime() : 0 
        }));

        // アプリのデータとフォームのデータを合体
        allPosts = suggestions.concat(formSuggestions);
        
        // 画面に描画
        renderDashboard();
    } catch (error) {
        console.error("データ取得エラー:", error);
    }
}

// ==========================================
// 🎨 7. ダッシュボードの描画（3ジャンル）
// ==========================================
function renderDashboard() {
    // 👑 人気順（いいね数降順）
    const popular = [...allPosts].sort((a, b) => b.likes - a.likes);
    
    // ✨ 新着順（時間降順）
    const newest = [...allPosts].sort((a, b) => b.created_at - a.created_at);
    
    // 🔥 話題順（ランダム）※運用に応じて後でアルゴリズムを変更可能
    const trending = [...allPosts].sort(() => Math.random() - 0.5);

    renderPostCards(popular.slice(0, 3), 'popular-top3');
    renderPostCards(newest.slice(0, 3), 'newest-top3');
    renderPostCards(trending.slice(0, 3), 'trending-top3');
}

// 🔍 もっと表示する
window.showCategoryDetail = function(category, titleText) {
    document.getElementById('page-view').style.display = 'none';
    document.getElementById('page-detail').style.display = 'block';
    document.getElementById('detail-title').textContent = titleText;

    let targetPosts = [];
    if (category === 'popular') targetPosts = [...allPosts].sort((a, b) => b.likes - a.likes);
    if (category === 'newest')  targetPosts = [...allPosts].sort((a, b) => b.created_at - a.created_at);
    if (category === 'trending') targetPosts = [...allPosts].sort(() => Math.random() - 0.5);

    renderPostCards(targetPosts, 'detail-list');
}

// ==========================================
// 🖨️ 8. カードの生成処理（フォーム対応版）
// ==========================================
function renderPostCards(posts, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = ""; 

    posts.forEach(post => {
        const card = document.createElement("div");
        card.className = "post-card";

        // フォームからの意見の場合のバッジ
        let badgeHtml = post.isForm 
            ? `<div style="color: #7F8C8D; font-size: 0.8em; margin-bottom: 5px; font-weight: bold;">📋 フォームからの意見</div>` 
            : "";

        card.innerHTML = `
            ${badgeHtml}
            <p style="margin-top: 5px; font-size: 15px; line-height: 1.4;">${escapeHtml(post.text)}</p>
            <button class="like-btn" onclick="likeSuggestion('${post.id}')">❤️ ${post.likes}</button>
        `;
        container.appendChild(card);
    });
}

// ==========================================
// ❤️ 9. いいね！処理
// ==========================================
window.likeSuggestion = async function(id) {
    if (!currentUserToken) return alert("「いいね」をするにはログインが必要です！");

    try {
        const response = await fetch(`/api/suggestions/${id}/like`, {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${currentUserToken}`
            }
        });

        if (!response.ok) throw new Error("いいねに失敗しました");
        
        // 再取得して画面を更新（詳細画面にいる場合は詳細画面をキープ）
        await fetchAllPosts();
        
        if (document.getElementById('page-detail').style.display === 'block') {
            // 現在のタイトルからカテゴリを逆算して再描画
            const title = document.getElementById('detail-title').textContent;
            let cat = 'newest';
            if(title.includes('人気')) cat = 'popular';
            if(title.includes('話題')) cat = 'trending';
            showCategoryDetail(cat, title);
        }
    } catch (error) {
        console.error(error);
    }
}

// ユーティリティ
function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}