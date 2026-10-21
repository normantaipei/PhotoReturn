// 網站名稱由後端寫進 <meta name="site-name">（安裝時設定，見 PHOTORETURN_SITE_NAME）
export const siteName = document.querySelector('meta[name="site-name"]')?.content || "返圖";

export const pageTitle = (title) => (title ? `${title} · ${siteName}` : siteName);
