<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="shell">
    <el-aside width="224px" class="sidebar">
      <div class="brand"><span class="brand-mark">储</span><div><b>储蓄规划</b><small>动态预算系统</small></div></div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">首页</el-menu-item><el-menu-item index="/bills">账单</el-menu-item>
        <el-menu-item index="/analytics">消费分析</el-menu-item><el-menu-item index="/forecast">周度预测</el-menu-item>
        <el-menu-item index="/goals">储蓄目标</el-menu-item><el-menu-item index="/budgets">预算计划</el-menu-item>
        <el-menu-item index="/replan">动态调整</el-menu-item><el-menu-item index="/history">历史方案</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container><el-header class="topbar"><span>{{ title }}</span><el-button text @click="logout">退出</el-button></el-header><el-main><router-view /></el-main></el-container>
  </el-container>
</template>
<script setup>
import { computed } from 'vue'; import { useRoute, useRouter } from 'vue-router'; import { clearToken } from './api/client.js'
const route = useRoute(); const router = useRouter()
const names = {'/':'财务概览','/bills':'账单与分类','/analytics':'消费行为分析','/forecast':'周度消费预测','/goals':'储蓄目标','/budgets':'预算计划','/replan':'动态调整','/history':'历史方案'}
const title = computed(() => names[route.path] || '储蓄规划')
function logout(){ clearToken(); router.push('/login') }
</script>
