<template><el-empty :description="description"><el-button v-if="action" type="primary" @click="$router.push(action)">去完成下一步</el-button></el-empty></template>
<script setup>defineProps({ description: String, action: String })</script>
