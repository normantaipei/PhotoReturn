<script setup>
import { toast } from "../lib/share.js";
</script>

<template>
  <Transition name="toast">
    <div v-if="toast" class="toast" role="status">{{ toast }}</div>
  </Transition>
</template>
