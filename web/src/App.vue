<script setup>
import AlbumView from "./components/AlbumView.vue";
import PhotoView from "./components/PhotoView.vue";
import Toast from "./components/Toast.vue";
import { siteName } from "./lib/site.js";

// 只有三種頁面，不需要 vue-router：/a/<相簿碼>、/p/<單張碼>、首頁
const [, type, key] = location.pathname.match(/^\/([ap])\/([^/]+)/) || [];
const shareKey = key ? decodeURIComponent(key) : null;
</script>

<template>
  <AlbumView v-if="type === 'a'" :share-key="shareKey" />
  <PhotoView v-else-if="type === 'p'" :photo-key="shareKey" />
  <div v-else class="home">
    <h1>{{ siteName }}</h1>
    <p class="muted">請使用攝影師提供的相簿連結開啟照片。</p>
  </div>
  <Toast />
</template>
