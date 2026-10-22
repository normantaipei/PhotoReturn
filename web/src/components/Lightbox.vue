<script setup>
import { computed, onBeforeUnmount, onMounted, watch } from "vue";
import { mediaUrl, starText } from "../lib/api.js";
import { t } from "../lib/i18n.js";
import DownloadButtons from "./DownloadButtons.vue";

const props = defineProps({ photos: Array, index: Number, shareKey: String, albumTitle: String });
const emit = defineEmits(["update:index"]);

const open = computed(() => props.index >= 0 && props.index < props.photos.length);
const photo = computed(() => (open.value ? props.photos[props.index] : null));

const close = () => emit("update:index", -1);
const step = (d) => {
  if (!open.value) return;
  const n = props.photos.length;
  emit("update:index", (props.index + d + n) % n);
};

// 開啟時鎖住背景捲動，並預載前後一張
watch(() => props.index, () => {
  document.body.style.overflow = open.value ? "hidden" : "";
  if (!open.value) return;
  for (const j of [props.index - 1, props.index + 1]) {
    const p = props.photos[j];
    if (p) new Image().src = mediaUrl(props.shareKey, "p", p.sha);
  }
});

function onKey(e) {
  if (!open.value) return;
  if (e.key === "Escape") close();
  else if (e.key === "ArrowLeft") step(-1);
  else if (e.key === "ArrowRight") step(1);
}
onMounted(() => addEventListener("keydown", onKey));
onBeforeUnmount(() => removeEventListener("keydown", onKey));

let touchX = null;
const onTouchStart = (e) => (touchX = e.touches[0].clientX);
function onTouchEnd(e) {
  if (touchX === null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
  touchX = null;
}
</script>

<template>
  <div
    v-if="photo" class="lb" role="dialog" aria-modal="true" :aria-label="t('viewer')"
    @click.self="close" @touchstart.passive="onTouchStart" @touchend="onTouchEnd"
  >
    <button class="lb-close" :aria-label="t('close')" @click="close">✕</button>
    <button class="lb-nav lb-prev" :aria-label="t('prev')" @click="step(-1)">‹</button>
    <figure @click.self="close">
      <img :key="photo.sha" :src="mediaUrl(shareKey, 'p', photo.sha)" :alt="photo.name">
      <figcaption>
        <span class="lb-meta">
          <span class="lb-stars">{{ starText(photo.stars) }}</span>
          <span>{{ photo.name }}</span>
          <span>{{ index + 1 }} / {{ photos.length }}</span>
        </span>
        <DownloadButtons
          :photo="photo" :share-title="albumTitle"
          :jpg-url="mediaUrl(shareKey, 'o', photo.sha)" :raw-url="mediaUrl(shareKey, 'r', photo.sha)"
        />
      </figcaption>
    </figure>
    <button class="lb-nav lb-next" :aria-label="t('next')" @click="step(1)">›</button>
  </div>
</template>
