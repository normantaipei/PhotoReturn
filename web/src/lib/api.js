// 相簿內的圖片：/media/<相簿碼>/<種類>/<sha>；單張分享頁的圖片：/m/<單張碼>/<種類>
// 種類：t 縮圖、p 預覽、o 原始 JPG、r RAW
import { I18nError, t } from "./i18n.js";

export const mediaUrl = (key, kind, sha) => `/media/${encodeURIComponent(key)}/${kind}/${sha}`;
export const photoMediaUrl = (pk, kind) => `/m/${encodeURIComponent(pk)}/${kind}`;

export const zipUrl = (key, minStars, fmt = "jpg") => {
  const q = new URLSearchParams();
  if (minStars) q.set("min_stars", minStars);
  if (fmt !== "jpg") q.set("fmt", fmt);
  return `/api/share/${encodeURIComponent(key)}/zip${q.size ? `?${q}` : ""}`;
};

async function getJson(url, notFound) {
  const res = await fetch(url);
  if (!res.ok) throw new I18nError(res.status === 404 ? notFound : "loadFailed", { status: res.status });
  return res.json();
}

export const fetchAlbum = (key) => getJson(`/api/share/${encodeURIComponent(key)}`, "albumNotFound");
export const fetchPhoto = (pk) => getJson(`/api/photo/${encodeURIComponent(pk)}`, "photoNotFound");

export const starText = (n) => (n ? "★".repeat(n) + "☆".repeat(5 - n) : t("unrated"));

export const fmtBytes = (b) => (!b ? "" : b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round(b / 1e6)} MB`);

export const rawExt = (name) => (name ? name.split(".").pop().toUpperCase() : "RAW");
