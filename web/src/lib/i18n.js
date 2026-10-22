// 介面語言：中文 / English / 日本語
// 決定順序：網址 ?lang= → 上次手動選的 → 瀏覽器語言 → 中文
import { ref } from "vue";

export const LANGS = [
  { code: "zh", label: "中文", html: "zh-Hant" },
  { code: "en", label: "English", html: "en" },
  { code: "ja", label: "日本語", html: "ja" },
];

const messages = {
  zh: {
    loading: "載入中…",
    loadFailed: "載入失敗（{status}）",
    albumNotFound: "找不到這本相簿",
    photoNotFound: "找不到這張照片",
    personNotFound: "找不到這個人的照片",
    byPeople: "依人物",
    personN: "人物 {n}",
    sharePerson: "分享這個人的照片",
    personLinkCopied: "已複製這個人的照片連結",
    copyPersonPrompt: "複製這個人的照片連結：",
    homeHint: "請使用攝影師提供的相簿連結開啟照片。",
    photos: "{n} 張",
    filters: "篩選",
    byStars: "依星等",
    byTags: "依標籤",
    all: "全部",
    starsAtLeast: "★{n} 以上",
    unrated: "未評分",
    noMatch: "沒有符合條件的照片",
    downloadAll: "下載全部",
    downloadStars: "下載 ★{n}+",
    files: "{n} 個檔案",
    noRaw: "這本相簿沒有 RAW 檔",
    share: "分享",
    linkCopied: "已複製這張照片的分享連結",
    copyLinkPrompt: "複製這張照片的分享連結：",
    viewer: "照片檢視",
    close: "關閉",
    prev: "上一張",
    next: "下一張",
    language: "語言",
  },
  en: {
    loading: "Loading…",
    loadFailed: "Failed to load ({status})",
    albumNotFound: "Album not found",
    photoNotFound: "Photo not found",
    personNotFound: "Photos not found",
    byPeople: "By person",
    personN: "Person {n}",
    sharePerson: "Share this person's photos",
    personLinkCopied: "Link to this person's photos copied",
    copyPersonPrompt: "Copy the link to this person's photos:",
    homeHint: "Please open your photos with the album link from your photographer.",
    photos: "{n} photos",
    photos_one: "{n} photo",
    filters: "Filters",
    byStars: "By rating",
    byTags: "By tag",
    all: "All",
    starsAtLeast: "★{n}+",
    unrated: "Unrated",
    noMatch: "No photos match these filters",
    downloadAll: "Download all",
    downloadStars: "Download ★{n}+",
    files: "{n} files",
    files_one: "{n} file",
    noRaw: "This album has no RAW files",
    share: "Share",
    linkCopied: "Link to this photo copied",
    copyLinkPrompt: "Copy the link to this photo:",
    viewer: "Photo viewer",
    close: "Close",
    prev: "Previous",
    next: "Next",
    language: "Language",
  },
  ja: {
    loading: "読み込み中…",
    loadFailed: "読み込みに失敗しました（{status}）",
    albumNotFound: "アルバムが見つかりません",
    photoNotFound: "写真が見つかりません",
    personNotFound: "写真が見つかりません",
    byPeople: "人物で絞り込み",
    personN: "人物 {n}",
    sharePerson: "この人の写真を共有",
    personLinkCopied: "この人の写真のリンクをコピーしました",
    copyPersonPrompt: "この人の写真のリンクをコピー：",
    homeHint: "カメラマンから届いたアルバムのリンクから開いてください。",
    photos: "{n} 枚",
    filters: "絞り込み",
    byStars: "評価で絞り込み",
    byTags: "タグで絞り込み",
    all: "すべて",
    starsAtLeast: "★{n} 以上",
    unrated: "未評価",
    noMatch: "条件に合う写真はありません",
    downloadAll: "すべてダウンロード",
    downloadStars: "★{n}+ をダウンロード",
    files: "{n} ファイル",
    noRaw: "このアルバムには RAW ファイルがありません",
    share: "共有",
    linkCopied: "この写真のリンクをコピーしました",
    copyLinkPrompt: "この写真のリンクをコピー：",
    viewer: "写真ビューア",
    close: "閉じる",
    prev: "前へ",
    next: "次へ",
    language: "言語",
  },
};

const STORE_KEY = "photoreturn.lang";
const known = (c) => (LANGS.some((l) => l.code === c) ? c : null);
const fromTag = (tag) => known((tag || "").toLowerCase().split("-")[0]);

function store(code) {
  try {
    localStorage.setItem(STORE_KEY, code);
  } catch {}
}

function detect() {
  const q = fromTag(new URLSearchParams(location.search).get("lang"));
  if (q) {
    store(q);
    return q;
  }
  try {
    const saved = known(localStorage.getItem(STORE_KEY));
    if (saved) return saved;
  } catch {}
  for (const tag of navigator.languages || [navigator.language]) {
    const c = fromTag(tag);
    if (c) return c;
  }
  return "zh";
}

export const lang = ref(detect());

const applyHtmlLang = () => (document.documentElement.lang = LANGS.find((l) => l.code === lang.value).html);
applyHtmlLang();

export function setLang(code) {
  if (!known(code)) return;
  lang.value = code;
  store(code);
  applyHtmlLang();
}

// t("photos", { n: 3 }) → "3 張"；在模板或 computed 裡呼叫，切換語言時會自動更新
// n 為 1 時優先用 <key>_one（英文單數）
export function t(key, params = {}) {
  const m = messages[lang.value];
  const s = (params.n === 1 && m[`${key}_one`]) || m[key] || messages.zh[key] || key;
  return s.replace(/\{(\w+)\}/g, (_, k) => params[k] ?? "");
}

// 可翻譯的錯誤：存 key，顯示時才翻，切換語言後錯誤訊息也跟著換
export class I18nError extends Error {
  constructor(key, params) {
    super(key);
    this.key = key;
    this.params = params;
  }
}
export const errorText = (e) => (e instanceof I18nError ? t(e.key, e.params) : e.message);
