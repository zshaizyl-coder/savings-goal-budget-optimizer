<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else class="shell">
    <el-aside width="236px" class="sidebar">
      <div class="brand"><span class="brand-mark">储</span><div><b>储蓄规划助手</b><small>从账单到每周预算</small></div></div>
      <el-menu router :default-active="$route.path" class="menu">
        <el-menu-item index="/"><span class="nav-icon">⌂</span>我的进度</el-menu-item>
        <div class="menu-group">按顺序完成</div>
        <el-menu-item v-for="item in primaryItems" :key="item.path" :index="item.path"><span class="nav-step">{{ item.step }}</span>{{ item.name }}</el-menu-item>
        <el-sub-menu index="more">
          <template #title><span class="nav-icon">···</span>更多工具</template>
          <el-menu-item index="/history">历史方案</el-menu-item>
          <el-menu-item index="/forecast">预测详情</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <div class="topbar-copy"><span v-if="current.step" class="top-step">{{ current.step }}</span><div><b>{{ current.name }}</b><small>{{ current.hint }}</small></div></div>
        <div class="top-actions"><el-dropdown class="mobile-nav" trigger="click"><el-button>页面导航</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="item in allItems" :key="item.path" @click="$router.push(item.path)">{{ item.name }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown><el-button text @click="logout">退出</el-button></div>
      </el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { clearToken } from './api/client.js'
import { primaryNavigation, secondaryNavigation } from './utils/display.js'

const route = useRoute()
const router = useRouter()
const primaryItems = primaryNavigation
const secondaryItems = secondaryNavigation
const home = { path: '/', name: '首页', hint: '系统会告诉你现在最该做什么' }
const allItems = [home, ...primaryItems, ...secondaryItems]
const current = computed(() => allItems.find(item => item.path === route.path) || home)
function logout() { clearToken(); router.push('/login') }
</script>
