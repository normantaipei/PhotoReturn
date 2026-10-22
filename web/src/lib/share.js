import { ref } from "vue";
import { t } from "./i18n.js";

export const toast = ref("");
let timer;
export function showToast(msg) {
  toast.value = msg;
  clearTimeout(timer);
  timer = setTimeout(() => (toast.value = ""), 2200);
}

// http 的區網網址沒有 navigator.clipboard（需要 https），退回舊的 execCommand
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;opacity:0";
  document.body.append(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
}

// 單張分享：手機用系統分享選單（可直接選 LINE / IG…），桌機複製連結
export async function sharePhoto(pk, title) {
  const url = `${location.origin}/p/${encodeURIComponent(pk)}`;
  const touch = matchMedia("(pointer: coarse)").matches;
  if (touch && navigator.share) {
    try {
      await navigator.share({ title, url });
      return;
    } catch (e) {
      if (e.name === "AbortError") return;
    }
  }
  try {
    await copyText(url);
    showToast(t("linkCopied"));
  } catch {
    window.prompt(t("copyLinkPrompt"), url);
  }
}
