<script setup>
import { ref } from "vue";
import { mediaUrl, starText } from "../lib/api.js";

defineProps({ scope: Object, photo: Object, w: Number, h: Number });
defineEmits(["open"]);
const loaded = ref(false);
</script>

<template>
  <button
    class="tile" :style="{ width: `${w}px`, height: `${h}px` }"
    :aria-label="`${photo.name}, ${starText(photo.stars)}`" @click="$emit('open')"
  >
    <img
      :src="mediaUrl(scope, 't', photo.sha)" alt="" loading="lazy" decoding="async"
      :class="{ loaded }" @load="loaded = true"
    >
  </button>
</template>
