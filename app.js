import { initializeApp } from "https://www.gstatic.com/firebasejs/10.8.1/firebase-app.js";
import { getAuth, signInWithPopup, GoogleAuthProvider, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.8.1/firebase-auth.js";

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
let allPosts = []; 

const GAS_URL = "https://script.google.com/macros/s/AKfycbxZztgHvkKfaH3WPkWEH8f9KoiBSAFNrbPFgKkbAbLnyy_-VNjhBHSfIJ04DGJraM0T/exec"; 
const KAIRU_NORMAL_IMAGE = "kairu.png";
const KAIRU_REPLY_IMAGE = "kairu_excel.png";
const KAIRU_NORMAL_TEXT = "何かお困りのことはありますか？";
const KAIRU_REPLY_TEXT = "知りません";
// ==========================================
// 🖱️ 2. イベントリスナーの設定（サイレントバグ修正版）
// ==========================================
function initApp() {
    const loginBtn = document.getElementById("login-btn");
    const logoutBtn = document.getElementById("logout-btn");
    if(loginBtn) loginBtn.addEventListener("click", login);
    if(logoutBtn) logoutBtn.addEventListener("click", logout);

    const submitBtn = document.getElementById("submit-btn");
    if(submitBtn) submitBtn.addEventListener("click", createSuggestion);
    
    // 💡 HTMLの変更に強いように、IDがなくても textarea を探すように強化
    const suggestionInput = document.getElementById("suggestion-input") || document.querySelector("textarea");
    if(suggestionInput) {
        suggestionInput.addEventListener("input", () => setKairuImage(false));
    }
}

// 💡 type="module" の仕様対策：すでにDOMが読み込み終わっているかチェックしてから起動する
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initApp);
} else {
    initApp();
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
            userInfo.hidden = false;
            userInfo.innerText = `ログイン中: ${user.displayName}さん`;
        }
        if(logoutBtn) logoutBtn.style.display = "inline";
    } else {
        currentUserToken = null;
        if(loginBtn) loginBtn.style.display = "inline";
        if(userInfo) userInfo.hidden = true;
        if(logoutBtn) logoutBtn.style.display = "none";
    }
    loadAllPosts({ force: true });
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
window.switchTab = function(tabName) {
    document.getElementById('page-post').hidden = (tabName !== 'post');
    document.getElementById('page-view').hidden = (tabName !== 'view');
    document.getElementById('page-detail').hidden = true; 

    if (tabName === 'view') {
        loadAllPosts({ force: true });
    }
}

window.backToDashboard = function() {
    document.getElementById('page-detail').hidden = true;
    document.getElementById('page-view').hidden = false;
}
// ==========================================
// 📡 5. 新しい意見の投稿（HTML変更に強い版）
// ==========================================
async function createSuggestion() {
    if (!currentUserToken) return alert("意見を投稿するにはログインが必要です！");

    const inputElement = document.getElementById("suggestion-input") || document.querySelector("textarea");
    const submitBtn = document.getElementById("submit-btn") || document.querySelector(".submit-btn");
    const text = inputElement ? inputElement.value.trim() : "";
    
    if (!text) return alert("意見を入力してください");

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerText = "送信中...";
    }

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

        if (inputElement) inputElement.value = "";
        setKairuImage(true);
        alert("投稿しました！");
        window.switchTab('view'); 
    } catch (error) {
        alert(error.message);
    } finally {
        if (submitBtn) {
            setTimeout(() => {
                submitBtn.disabled = false;
                submitBtn.innerText = "投稿する";
            }, 5000); // 💡 クールダウンを5秒に短縮
        }
    }
}
window.submitSuggestion = createSuggestion;

// ==========================================
// 📦 6. データの取得と結合（並列処理で爆速化）
// ==========================================
async function loadAllPosts(options = { force: false }) {
    try {
        const url = options.force ? "/api/feed?force=1" : "/api/feed";
        
        // 💡 爆速化の魔法：VercelとGASに「同時」にデータを取りに行かせる（Promise.all）
        const [res, gasRes] = await Promise.all([
            fetch(url),
            fetch(GAS_URL)
        ]);

        const feedData = await res.json();
        const formData = await gasRes.json();

        const suggestions = feedData.suggestions || [];
        const deletedFormIds = feedData.deleted_form_ids || [];

        const formSuggestions = formData.map((item, idx) => {
            const timeId = item.timestamp ? new Date(item.timestamp).getTime() : idx;
            return {
                id: `form-${timeId}`,
                text: item.content,
                likes: 0, 
                isForm: true,
                created_at: item.timestamp ? new Date(item.timestamp).getTime() : 0 
            };
        });

        formSuggestions.forEach(f => {
            const match = suggestions.find(s => s.id === f.id);
            if (match) {
                f.likes = match.likes;
                f.status = match.status;
            }
        });

        let combined = suggestions.concat(formSuggestions);
        allPosts = combined.filter(post => !deletedFormIds.includes(post.id));
        
        renderDashboard();
    } catch (error) {
        console.error("データ取得エラー:", error);
    }
}
// ==========================================
// 🎨 7. ダッシュボードの描画（3ジャンル）
// ==========================================
function renderDashboard() {
    const popular = [...allPosts].sort((a, b) => (b.likes || 0) - (a.likes || 0));
    const newest = [...allPosts].sort((a, b) => b.created_at - a.created_at);
    const trending = [...allPosts].sort((a, b) => (b.likes || 0) - (a.likes || 0));

    renderPostCards(popular.slice(0, 3), 'popular-top3');
    renderPostCards(newest.slice(0, 3), 'newest-top3');
    renderPostCards(trending.slice(0, 3), 'trending-top3');
}

window.showCategoryDetail = function(category, titleText) {
    document.getElementById('page-view').hidden = true;
    document.getElementById('page-detail').hidden = false;
    document.getElementById('detail-title').textContent = titleText;

    let targetPosts = [];
    if (category === 'popular') targetPosts = [...allPosts].sort((a, b) => (b.likes || 0) - (a.likes || 0));
    if (category === 'newest')  targetPosts = [...allPosts].sort((a, b) => b.created_at - a.created_at);
    if (category === 'trending') targetPosts = [...allPosts].sort((a, b) => (b.likes || 0) - (a.likes || 0));

    renderPostCards(targetPosts, 'detail-list');
}

// ==========================================
// 🖨️ 8. カードの生成処理（ボタン左下固定レイアウト）
// ==========================================
function renderPostCards(posts, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = ""; 

    posts.forEach(post => {
        const card = document.createElement("div");
        card.className = "post-card";
        card.style.display = "flex";
        card.style.flexDirection = "column";
        card.style.height = "100%";

        let badgeHtml = post.isForm 
            ? `<div style="color: #7F8C8D; font-size: 0.8em; margin-bottom: 5px; font-weight: bold;">📋 フォームからの意見</div>` 
            : "";

        const status = post.status || "検討中";
        let statusColor = "#147c72"; 
        if (status === "対応中") statusColor = "#e67e22"; 
        if (status === "解決済み") statusColor = "#7f8c8d"; 
        let statusHtml = `<span style="background: ${statusColor}; color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.75em; font-weight: bold; margin-left: auto;">${status}</span>`;

        const commentAreaId = `comments-${post.id}`;

        card.innerHTML = `
            <div style="display: flex; align-items: center; width: 100%;">
                ${badgeHtml}
                ${statusHtml}
            </div>
            <p style="margin-top: 8px; font-size: 15px; line-height: 1.4; flex-grow: 1;">${escapeHtml(post.text)}</p>
            <div style="display: flex; gap: 10px; margin-top: auto; padding-top: 15px;">
                <button class="like-btn" onclick="likeSuggestion('${post.id}')">❤️ ${post.likes || 0}</button>
                <button class="like-btn" onclick="toggleComments('${post.id}')">💬 コメント</button>
            </div>
            
            <div id="${commentAreaId}" style="display: none; margin-top: 12px; padding-top: 10px; border-top: 1px solid #ddd; text-align: left;">
                <div id="list-${commentAreaId}" style="font-size: 0.85em; color: #444; max-height: 150px; overflow-y: auto; margin-bottom: 8px;"></div>
                <div style="display: flex; gap: 5px;">
                    <input type="text" id="input-${commentAreaId}" placeholder="コメントを書く..." style="flex-grow: 1; padding: 6px; border: 1px solid #ddd; border-radius: 4px; font-size: 0.9em;">
                    <button onclick="submitComment('${post.id}')" style="padding: 6px 12px; background: #147c72; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.9em;">送信</button>
                </div>
            </div>
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
            headers: { "Authorization": `Bearer ${currentUserToken}` }
        });

        if (!response.ok) throw new Error("いいねに失敗しました");
        
        await loadAllPosts({ force: true });
        
        if (!document.getElementById('page-detail').hidden) {
            const title = document.getElementById('detail-title').textContent;
            let cat = 'newest';
            if(title.includes('人気')) cat = 'popular';
            if(title.includes('話題')) cat = 'trending';
            window.showCategoryDetail(cat, title);
        }
    } catch (error) {
        console.error(error);
    }
}

// ==========================================
// 💬 10. コメント制御
// ==========================================
window.toggleComments = async function(postId) {
    const area = document.getElementById(`comments-${postId}`);
    if (area.style.display === "none") {
        area.style.display = "block";
        await loadComments(postId);
    } else {
        area.style.display = "none";
    }
}

async function loadComments(postId) {
    const listContainer = document.getElementById(`list-comments-${postId}`);
    listContainer.innerHTML = "<span style='color:#888;'>読み込み中...</span>";
    try {
        const res = await fetch(`/api/suggestions/${postId}/comments`);
        const comments = await res.json();
        listContainer.innerHTML = "";
        if (comments.length === 0) {
            listContainer.innerHTML = "<span style='color:#aaa; font-style:italic;'>コメントはまだありません</span>";
            return;
        }
        comments.forEach(c => {
            const div = document.createElement("div");
            div.style.marginBottom = "6px";
            div.innerHTML = `<strong style="color:#147c72;">${escapeHtml(c.user_name)}:</strong> ${escapeHtml(c.text)}`;
            listContainer.appendChild(div);
        });
    } catch (e) {
        listContainer.innerHTML = "読み込み失敗";
    }
}

window.submitComment = async function(postId) {
    const user = auth.currentUser;
    if (!user) return alert("コメントをするにはログインが必要です！");
    
    const input = document.getElementById(`input-comments-${postId}`);
    const text = input.value.trim();
    if (!text) return;

    try {
        const freshToken = await user.getIdToken(true); // 最新トークンを再取得
        const res = await fetch(`/api/suggestions/${postId}/comments`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": `Bearer ${freshToken}`
            },
            body: JSON.stringify({ text: text })
        });
        
        if (res.ok) {
            input.value = "";
            await loadComments(postId); 
        } else {
            alert("コメントの送信に失敗しました");
        }
    } catch (e) {
        alert("通信エラーが発生しました");
    }
}

// 🛡️ ユーティリティ
function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}