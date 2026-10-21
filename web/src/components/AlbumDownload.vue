<script setup>
// 整本下載：JPG / RAW / JPG+RAW，依目前的星等篩選
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fmtBytes, zipUrl } from "../lib/api.js";

const props = defineProps({ shareKey: String, photos: Array, minStars: Number });
const open = ref(false);
const root = ref(null);

const sum = (f) => props.photos.reduce((a, p) => a + (f(p) || 0), 0);
const rawCount = computed(() => props.photos.filter((p) => p.raw_name).length);
const options = computed(() => {
  const opts = [{ fmt: "jpg", label: "JPG", count: props.photos.length, bytes: sum((p) => p.bytes) }];
  if (rawCount.value) {
    opts.push({ fmt: "raw", label: "RAW", count: rawCount.value, bytes: sum((p) => p.raw_bytes) });
    opts.push({ fmt: "all", label: "JPG + RAW", count: props.photos.length + rawCount.value,
                bytes: sum((p) => p.bytes) + sum((p) => p.raw_bytes) });
  }
  return opts;
});

const onDoc = (e) => { if (!root.value?.contains(e.target)) open.value = false; };
const onKey = (e) => { if (e.key === "Escape") open.value = false; };
onMounted(() => { document.addEventListener("click", onDoc); addEventListener("keydown", onKey); });
onBeforeUnmount(() => { document.removeEventListener("click", onDoc); removeEventListener("keydown", onKey); });
</script>

<template>
  <div ref="root" class="dl-menu">
    <button class="btn" :aria-expanded="open" @click="open = !open">
      ⬇ {{ minStars ? `下載 ★${minStars}+` : "下載全部" }}（{{ photos.length }}）<span class="caret">▾</span>
    </button>
    <div v-if="open" class="dl-pop" role="menu">
      <a
        v-for="o in options" :key="o.fmt" role="menuitem" :href="zipUrl(shareKey, minStars, o.fmt)"
        download @click="open = false"
      >
        <strong>{{ o.label }}</strong>
        <span class="muted">{{ o.count }} 個檔案 · {{ fmtBytes(o.bytes) }}</span>
      </a>
      <p v-if="!rawCount" class="muted note">這本相簿沒有 RAW 檔</p>
    </div>
  </div>
</template>
