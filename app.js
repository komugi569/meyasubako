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
// 🖨️ 8. カードの生成処理（フォーム対応版）
// ==========================================
function renderPostCards(posts, containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = ""; 

    if (!posts.length) {
        const empty = document.createElement("p");
        empty.className = "empty-message";
        empty.textContent = "まだ表示できる意見がありません。";
        container.appendChild(empty);
        return;
    }

    posts.forEach(post => {
        const card = document.createElement("div");
        card.className = "post-card";

        const source = document.createElement("div");
        source.className = "post-source";
        source.textContent = post.isForm ? "フォームからの意見" : "アプリからの意見";

        const text = document.createElement("p");
        text.className = "post-text";
        text.textContent = post.text || "内容がありません。";

        const actions = document.createElement("div");
        actions.className = "post-actions";

        const likeButton = document.createElement("button");
        likeButton.className = "like-btn";
        likeButton.type = "button";
        likeButton.textContent = `いいね ${post.likes || 0}`;
        likeButton.addEventListener("click", () => likeSuggestion(post.id));

        actions.appendChild(likeButton);
        card.appendChild(source);
        card.appendChild(text);
        card.appendChild(actions);
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
