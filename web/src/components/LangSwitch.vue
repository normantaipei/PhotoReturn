<script setup>
import { LANGS, lang, setLang, t } from "../lib/i18n.js";
</script>

<template>
  <footer class="lang-switch" role="group" :aria-label="t('language')">
    <button
      v-for="l in LANGS" :key="l.code" :lang="l.html"
      :aria-pressed="lang === l.code" @click="setLang(l.code)"
    >
      {{ l.label }}
    </button>
  </footer>
</template>
