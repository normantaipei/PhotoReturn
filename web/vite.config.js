import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 開發時 API 與圖片轉給本機後端；正式環境由 FastAPI 直接提供 dist/
const backend = process.env.PR_BACKEND || "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      "/api": backend,
      "/media": backend,
      "/m/": backend,
    },
  },
});
