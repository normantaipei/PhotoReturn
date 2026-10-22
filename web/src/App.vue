<script setup>
import AlbumView from "./components/AlbumView.vue";
import PhotoView from "./components/PhotoView.vue";
import Toast from "./components/Toast.vue";
import LangSwitch from "./components/LangSwitch.vue";
import { siteName } from "./lib/site.js";
import { t } from "./lib/i18n.js";

// 只有四種頁面，不需要 vue-router：/a/<相簿碼>、/f/<人物碼>、/p/<單張碼>、首頁
const [, type, key] = location.pathname.match(/^\/([apf])\/([^/]+)/) || [];
const shareKey = key ? decodeURIComponent(key) : null;
</script>

<template>
  <AlbumView v-if="type === 'a' || type === 'f'" :scope="{ type, key: shareKey }" />
  <PhotoView v-else-if="type === 'p'" :photo-key="shareKey" />
  <div v-else class="home">
    <h1>{{ siteName }}</h1>
    <p class="muted">{{ t("homeHint") }}</p>
  </div>
  <LangSwitch />
  <Toast />
</template>
