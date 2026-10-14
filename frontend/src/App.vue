<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="shell">
    <el-aside width="224px" class="sidebar">
      <div class="brand"><span class="brand-mark">储</span><div><b>储蓄规划</b><small>动态预算系统</small></div></div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/">今日概览</el-menu-item><div class="menu-group">开始规划</div>
        <el-menu-item index="/bills">1. 导入账单</el-menu-item><el-menu-item index="/analytics">2. 消费分析</el-menu-item>
        <el-menu-item index="/goals">3. 目标与底线</el-menu-item><el-menu-item index="/budgets">4. 预算方案</el-menu-item>
        <div class="menu-group">执行与调整</div><el-menu-item index="/replan">本周执行</el-menu-item><el-menu-item index="/history">方案记录</el-menu-item>
        <div class="menu-group">辅助信息</div><el-menu-item index="/forecast">消费基线预测</el-menu-item>
      </el-menu>
    </el-aside>
    <el-container><el-header class="topbar"><span>{{ title }}</span><div class="top-actions"><el-dropdown class="mobile-nav" trigger="click"><el-button>页面导航</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="item in mobileItems" :key="item.path" @click="$router.push(item.path)">{{item.name}}</el-dropdown-item></el-dropdown-menu></template></el-dropdown><el-button text @click="logout">退出</el-button></div></el-header><el-main><router-view /></el-main></el-container>
  </el-container>
</template>
<script setup>
import { computed } from 'vue'; import { useRoute, useRouter } from 'vue-router'; import { clearToken } from './api/client.js'
const route = useRoute(); const router = useRouter()
const names = {'/':'今日概览','/bills':'导入账单与分类','/analytics':'消费行为分析','/forecast':'消费基线预测','/goals':'储蓄目标与预算底线','/budgets':'预算方案','/replan':'本周执行与动态调整','/history':'方案记录'}
const mobileItems=Object.entries(names).map(([path,name])=>({path,name}));const title = computed(() => names[route.path] || '储蓄规划')
function logout(){ clearToken(); router.push('/login') }
</script>
