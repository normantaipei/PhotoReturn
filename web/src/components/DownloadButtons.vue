<script setup>
// 單張下載：JPG / RAW 兩顆按鈕＋分享
import { fmtBytes, rawExt } from "../lib/api.js";
import { sharePhoto } from "../lib/share.js";
import { t } from "../lib/i18n.js";

defineProps({ photo: Object, jpgUrl: String, rawUrl: String, shareTitle: String });
</script>

<template>
  <div class="dl-buttons">
    <a class="btn small" :href="jpgUrl" download>
      ⬇ JPG <span class="n">{{ fmtBytes(photo.bytes) }}</span>
    </a>
    <a v-if="photo.raw_name" class="btn small" :href="rawUrl" download>
      ⬇ {{ rawExt(photo.raw_name) }} <span class="n">{{ fmtBytes(photo.raw_bytes) }}</span>
    </a>
    <button class="btn small" @click="sharePhoto(photo.pk, shareTitle)">↗ {{ t("share") }}</button>
  </div>
</template>
