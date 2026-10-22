<script setup>
// 單張分享頁：只看得到這一張，沒有回到整本相簿的連結
import { onMounted, ref } from "vue";
import { pageTitle } from "../lib/site.js";
import { fetchPhoto, photoMediaUrl } from "../lib/api.js";
import { errorText, t } from "../lib/i18n.js";
import DownloadButtons from "./DownloadButtons.vue";

const props = defineProps({ photoKey: { type: String, required: true } });
const photo = ref(null);
const error = ref(null);
const loaded = ref(false);

onMounted(async () => {
  try {
    photo.value = await fetchPhoto(props.photoKey);
    document.title = pageTitle(photo.value.album_title);
  } catch (e) {
    error.value = e;
  }
});
</script>

<template>
  <div class="single">
    <header class="single-head">
      <h1>{{ error ? errorText(error) : photo?.album_title || t("loading") }}</h1>
      <p v-if="photo" class="muted">{{ [photo.shoot_date, photo.name].filter(Boolean).join(" · ") }}</p>
    </header>
    <figure v-if="photo" class="single-photo">
      <img
        :src="photoMediaUrl(photoKey, 'p')" :alt="photo.name" :width="photo.w" :height="photo.h"
        :class="{ loaded }" @load="loaded = true"
      >
    </figure>
    <DownloadButtons
      v-if="photo" class="single-actions" :photo="{ ...photo, pk: photoKey }" :share-title="photo.album_title"
      :jpg-url="photoMediaUrl(photoKey, 'o')" :raw-url="photoMediaUrl(photoKey, 'r')"
    />
  </div>
</template>
