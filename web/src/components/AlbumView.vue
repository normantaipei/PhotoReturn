<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { pageTitle } from "../lib/site.js";
import { fetchAlbum } from "../lib/api.js";
import { justify } from "../lib/justify.js";
import { errorText, lang, t } from "../lib/i18n.js";
import PhotoTile from "./PhotoTile.vue";
import Lightbox from "./Lightbox.vue";
import AlbumDownload from "./AlbumDownload.vue";

const props = defineProps({ shareKey: { type: String, required: true } });

const album = ref(null);
const error = ref(null);
const minStars = ref(0);
const tag = ref(null);
const lbIndex = ref(-1);

const galleryEl = ref(null);
const width = ref(0);
let ro;

onMounted(async () => {
  ro = new ResizeObserver(([e]) => (width.value = e.contentRect.width));
  ro.observe(galleryEl.value);
  try {
    album.value = await fetchAlbum(props.shareKey);
    document.title = pageTitle(album.value.title);
  } catch (e) {
    error.value = e;
  }
});
onBeforeUnmount(() => ro?.disconnect());

const photos = computed(() => album.value?.photos ?? []);

// 星等篩選：全部 / ★5 / ★4 以上 / ★3 以上（數量和上一級相同的選項不顯示）
const starOptions = computed(() => {
  const counts = [0, 0, 0, 0, 0, 0];
  for (const p of photos.value) counts[p.stars || 0]++;
  const atLeast = (n) => counts.slice(n).reduce((a, b) => a + b, 0);
  const opts = [{ value: 0, label: t("all"), count: photos.value.length }];
  if (counts[5]) opts.push({ value: 5, label: "★5", count: counts[5] });
  for (const n of [4, 3]) {
    if (atLeast(n) && atLeast(n) !== atLeast(n + 1)) opts.push({ value: n, label: t("starsAtLeast", { n }), count: atLeast(n) });
  }
  return opts;
});

// 標籤：Prism 中英並存，中文介面取中文、英日文介面取英文；出現 2 次以上的前 14 個
const topTags = computed(() => {
  const wantCjk = lang.value === "zh";
  const freq = new Map();
  for (const p of photos.value) {
    for (const tg of p.tags) if (/[^\x00-\x7f]/.test(tg) === wantCjk) freq.set(tg, (freq.get(tg) || 0) + 1);
  }
  return [...freq].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 14);
});

const visible = computed(() =>
  photos.value.filter((p) => (p.stars || 0) >= minStars.value && (!tag.value || p.tags.includes(tag.value))),
);

const totalMB = computed(() => Math.round(visible.value.reduce((a, p) => a + (p.bytes || 0), 0) / 1e6));

const layout = computed(() => {
  const mobile = width.value < 600;
  return { target: mobile ? 150 : width.value < 1100 ? 220 : 280, gap: mobile ? 3 : 6 };
});

// 依星等分段，每段各自排成 justified rows；index 是在 visible 裡的位置（燈箱用）
const groups = computed(() => {
  if (!width.value) return [];
  const out = [];
  visible.value.forEach((photo, index) => {
    const stars = photo.stars || 0;
    if (!out.length || out.at(-1).stars !== stars) out.push({ stars, items: [] });
    out.at(-1).items.push({ photo, index });
  });
  const { target, gap } = layout.value;
  return out.map((g) => ({ ...g, rows: justify(g.items, width.value, target, gap) }));
});

watch([minStars, tag], () => (lbIndex.value = -1));
watch(lang, () => (tag.value = null)); // 換語言後標籤清單不同

const toggleTag = (t) => (tag.value = tag.value === t ? null : t);
</script>

<template>
  <header class="top">
    <div>
      <h1>{{ error ? errorText(error) : album?.title || t("loading") }}</h1>
      <p v-if="album" class="muted">
        {{ [album.shoot_date, t("photos", { n: visible.length }), totalMB ? `${totalMB} MB` : ""].filter(Boolean).join(" · ") }}
      </p>
    </div>
    <AlbumDownload v-if="album && visible.length" :share-key="shareKey" :photos="visible" :min-stars="minStars" />
  </header>

  <nav v-if="album" class="filters" :aria-label="t('filters')">
    <div class="chips" role="group" :aria-label="t('byStars')">
      <button
        v-for="o in starOptions" :key="o.value" class="chip"
        :aria-pressed="minStars === o.value" @click="minStars = o.value"
      >
        {{ o.label }}<span class="n">{{ o.count }}</span>
      </button>
    </div>
    <div v-if="topTags.length" class="chips tags" role="group" :aria-label="t('byTags')">
      <button
        v-for="[t, c] in topTags" :key="t" class="chip"
        :aria-pressed="tag === t" @click="toggleTag(t)"
      >
        {{ t }}<span class="n">{{ c }}</span>
      </button>
    </div>
  </nav>

  <main>
    <div ref="galleryEl" class="gallery">
      <section v-for="g in groups" :key="g.stars">
        <h2 class="group-head">
          <span v-if="g.stars" class="stars">{{ "★".repeat(g.stars) }}</span>
          <span v-else>{{ t("unrated") }} ·</span>
          {{ t("photos", { n: g.items.length }) }}
        </h2>
        <div v-for="(row, ri) in g.rows" :key="ri" class="row" :style="{ gap: `${layout.gap}px`, marginBottom: `${layout.gap}px` }">
          <PhotoTile
            v-for="cell in row" :key="cell.item.photo.sha"
            :share-key="shareKey" :photo="cell.item.photo" :w="cell.w" :h="cell.h"
            @open="lbIndex = cell.item.index"
          />
        </div>
      </section>
    </div>
    <p v-if="album && !visible.length" class="muted center">{{ t("noMatch") }}</p>
  </main>

  <Lightbox v-model:index="lbIndex" :photos="visible" :share-key="shareKey" :album-title="album?.title" />
</template>
