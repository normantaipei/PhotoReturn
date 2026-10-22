<script setup>
// 整本下載：JPG / RAW / JPG+RAW，依目前的星等篩選
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { fmtBytes, zipUrl } from "../lib/api.js";
import { t } from "../lib/i18n.js";

const props = defineProps({ scope: Object, photos: Array, minStars: Number });
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
      ⬇ {{ minStars ? t("downloadStars", { n: minStars }) : t("downloadAll") }} ({{ photos.length }})<span class="caret">▾</span>
    </button>
    <div v-if="open" class="dl-pop" role="menu">
      <a
        v-for="o in options" :key="o.fmt" role="menuitem" :href="zipUrl(scope, minStars, o.fmt)"
        download @click="open = false"
      >
        <strong>{{ o.label }}</strong>
        <span class="muted">{{ t("files", { n: o.count }) }} · {{ fmtBytes(o.bytes) }}</span>
      </a>
      <p v-if="!rawCount" class="muted note">{{ t("noRaw") }}</p>
    </div>
  </div>
</template>
