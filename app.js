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
let allPosts = []; // すべての投稿データを一時保存する箱
let postsRequest = null;

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

    updatePostHelper();
    initKairuFooterAvoidance();
});

function setKairuImage(isReply) {
    const kairuImage = document.getElementById("kairu-image");
    const kairuTextbox = document.getElementById("kairu-textbox");
    if (kairuImage) kairuImage.src = isReply ? KAIRU_REPLY_IMAGE : KAIRU_NORMAL_IMAGE;
    if (kairuTextbox) kairuTextbox.innerText = isReply ? KAIRU_REPLY_TEXT : KAIRU_NORMAL_TEXT;
}

function initKairuFooterAvoidance() {
    const kairuAssistant = document.querySelector(".kairu-assistant");
    const footer = document.querySelector(".site-footer");

    if (!kairuAssistant || !footer || !("IntersectionObserver" in window)) return;

    const observer = new IntersectionObserver((entries) => {
        const isFooterVisible = entries.some(entry => entry.isIntersecting);
        kairuAssistant.classList.toggle("is-above-footer", isFooterVisible);
    }, { threshold: 0.01 });

    observer.observe(footer);
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
        if(loginBtn) loginBtn.hidden = true;
        if(userInfo) {
            userInfo.hidden = false;
            userInfo.innerText = `ログイン中: ${user.displayName}さん`;
        }
        if(logoutBtn) logoutBtn.hidden = false;
    } else {
        currentUserToken = null;
        if(loginBtn) loginBtn.hidden = false;
        if(userInfo) {
            userInfo.hidden = true;
            userInfo.innerText = "";
        }
        if(logoutBtn) logoutBtn.hidden = true;
    }
    updatePostHelper();
    // ログイン状態が確定したらデータを取得
    fetchAllPosts();
});

function updatePostHelper() {
    const helper = document.getElementById("post-helper");
    const submitBtn = document.getElementById("submit-btn");

    if (helper) {
        helper.textContent = currentUserToken
            ? "ログイン済みです。内容を確認して送信できます。"
            : "投稿にはログインが必要です。";
        helper.classList.toggle("is-ready", Boolean(currentUserToken));
    }

    if (submitBtn) {
        submitBtn.disabled = !currentUserToken;
    }
}

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
// 🔄 4. 画面切り替え処理
// ==========================================
function showMainView() {
    document.getElementById('page-post').hidden = false;
    document.getElementById('page-view').hidden = false;
    document.getElementById('page-detail').hidden = true;
}

window.backToDashboard = function() {
    showMainView();
    document.getElementById('page-view').scrollIntoView({ behavior: "smooth", block: "start" });
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
        await fetchAllPosts({ force: true });
        showMainView();
        document.getElementById('page-view').scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
        alert(error.message);
    } finally {
        submitBtn.innerText = "送信する";
        updatePostHelper();
    }
}
window.submitSuggestion = createSuggestion; // 💡 HTML側の submitSuggestion() と名前を合わせる
// ==========================================
// 📦 6. データの取得と結合（Firestore + GAS + 非表示フィルター）
// ==========================================
async function fetchAllPosts({ force = false } = {}) {
    if (postsRequest && !force) return postsRequest;

    postsRequest = loadAllPosts().finally(() => {
        postsRequest = null;
    });

    return postsRequest;
}

async function loadAllPosts() {
    setDashboardStatus("読み込み中です。");

    try {
        const gasRequest = fetchJson(GAS_URL, "フォームの意見を取得できませんでした");
        const feed = await fetchJson("/api/feed", "投稿一覧を取得できませんでした");

        const suggestionList = Array.isArray(feed.suggestions) ? feed.suggestions : [];
        const deletedIds = Array.isArray(feed.deleted_form_ids) ? feed.deleted_form_ids : [];
        const formLikeMap = new Map(
            suggestionList
                .filter(post => post.is_form_dummy)
                .map(post => [post.id, post.likes || 0])
        );
        const normalSuggestions = suggestionList.filter(post => !post.is_form_dummy);

        allPosts = normalSuggestions.filter(post => !deletedIds.includes(post.id));
        renderDashboard();
        setDashboardStatus(allPosts.length ? `${allPosts.length}件の意見を表示しています。フォーム投稿を確認中です。` : "フォーム投稿を確認中です。");

        let formData = [];
        try {
            formData = await gasRequest;
        } catch (error) {
            console.warn("フォーム意見の取得に失敗しました:", error);
            setDashboardStatus(allPosts.length ? `${allPosts.length}件の意見を表示しています。フォーム投稿は後で再読み込みしてください。` : "フォーム投稿を読み込めませんでした。");
            return;
        }

        const formList = Array.isArray(formData) ? formData : [];

        // ④ フォームデータを整形（💡 IDをタイムスタンプ基準にして絶対にズレないように安定化！）
        const formSuggestions = formList.map((item, idx) => {
            const timeId = item.timestamp ? new Date(item.timestamp).getTime() : idx;
            const id = `form-${timeId}`;
            return {
                id: id,
                text: item.content,
                likes: formLikeMap.get(id) || 0,
                isForm: true,
                created_at: item.timestamp ? new Date(item.timestamp).getTime() : 0 
            };
        });

        // ⑤ 通常の意見とフォームの意見を合体
        const combined = normalSuggestions.concat(formSuggestions);
        
        // ⑥ 💡 フィルターをかけて、非表示リストに入っているIDの投稿を除外（間引く）する！
        allPosts = combined.filter(post => !deletedIds.includes(post.id));
        
        // 画面に描画
        renderDashboard();
        setDashboardStatus(allPosts.length ? `${allPosts.length}件の意見を表示しています。` : "");
    } catch (error) {
        console.error("データ取得エラー:", error);
        allPosts = [];
        renderDashboard();
        setDashboardStatus("意見を読み込めませんでした。時間をおいてもう一度試してください。");
    }
}

async function fetchJson(url, errorMessage) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(errorMessage);
    return response.json();
}
// ==========================================
// 🎨 7. ダッシュボードの描画（3ジャンル）
// ==========================================
function renderDashboard() {
    // 👑 人気順（いいね数降順）
    const popular = [...allPosts].sort((a, b) => b.likes - a.likes);
    
    // ✨ 新着順（時間降順）
    const newest = [...allPosts].sort((a, b) => b.created_at - a.created_at);

    // 🔥 話題順（現在は人気順と同じ「いいね数降順」）
    const trending = [...allPosts].sort((a, b) => b.likes - a.likes);

    renderPostCards(popular.slice(0, 3), 'popular-top3');
    renderPostCards(newest.slice(0, 3), 'newest-top3');
    renderPostCards(trending.slice(0, 3), 'trending-top3');
}

function setDashboardStatus(message) {
    const status = document.getElementById("dashboard-status");
    if (status) status.textContent = message;
}

// 🔍 もっと表示する
window.showCategoryDetail = function(category, titleText) {
    document.getElementById('page-post').hidden = true;
    document.getElementById('page-view').hidden = true;
    document.getElementById('page-detail').hidden = false;
    document.getElementById('detail-title').textContent = titleText;

    let targetPosts = [];
    if (category === 'popular') targetPosts = [...allPosts].sort((a, b) => b.likes - a.likes);
    if (category === 'newest')  targetPosts = [...allPosts].sort((a, b) => b.created_at - a.created_at);
   if (category === 'trending') targetPosts = [...allPosts].sort((a, b) => b.likes - a.likes);
    renderPostCards(targetPosts, 'detail-list');
}

// ==========================================
// 🖨️ カードの生成処理（ボタン左下固定レイアウト）
// ==========================================
function renderPostCards(posts, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = ""; 

    posts.forEach(post => {
        const card = document.createElement("div");
        card.className = "post-card";
        
        // 💡 カード自体を縦並び（Flexbox）にして、高さをカード内でいっぱいに広げる
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
            headers: {
                "Authorization": `Bearer ${currentUserToken}`
            }
        });

        if (!response.ok) throw new Error("いいねに失敗しました");
        
        // 再取得して画面を更新（詳細画面にいる場合は詳細画面をキープ）
        await fetchAllPosts({ force: true });
        
        if (!document.getElementById('page-detail').hidden) {
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

// 💬 コメントエリアの開閉と読み込み
window.toggleComments = async function(postId) {
    const area = document.getElementById(`comments-${postId}`);
    if (area.style.display === "none") {
        area.style.display = "block";
        await loadComments(postId);
    } else {
        area.style.display = "none";
    }
}

// 💬 コメントのリアルタイム読み込み処理
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

// 💬 コメントの送信処理（トークン切れバグ修正版）
window.submitComment = async function(postId) {
    // 💡 常に「今の」ユーザー状態を取得する
    const user = auth.currentUser;
    if (!user) return alert("コメントをするにはログインが必要です！");
    
    const input = document.getElementById(`input-comments-${postId}`);
    const text = input.value.trim();
    if (!text) return;

    try {
        // 💡 送信するその瞬間に、最新のトークン（証明書）を取得し直す（これで時間が経ってもエラーにならない！）
        const freshToken = await user.getIdToken(true);
        
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
            await loadComments(postId); // コメント欄を更新
        } else {
            alert("コメントの送信に失敗しました");
        }
    } catch (e) {
        alert("通信エラーが発生しました");
    }
}
// ==========================================
// 🛡️ ユーティリティ（セキュリティ用の文字無害化処理）
// ==========================================
function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}