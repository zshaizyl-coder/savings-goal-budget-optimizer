<template>
  <router-view v-if="$route.meta.public" />
  <el-container v-else direction="vertical" class="shell shell-light" data-ui-version="liquid-glass-v1">
    <header class="app-header">
      <div class="app-header-inner">
        <button class="brand-button" @click="$router.push('/')"><span class="brand-mark">储</span><span><b>储蓄规划</b><small>个人预算助手 · 新版</small></span></button>
        <nav class="journey-nav" aria-label="规划步骤">
          <router-link v-for="item in primaryItems" :key="item.path" :to="item.path" class="journey-link"><span>{{ item.step }}</span><b>{{ item.name }}</b></router-link>
        </nav>
        <div class="header-actions">
          <el-dropdown class="desktop-more" trigger="click"><el-button text>更多</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item @click="$router.push('/')">我的进度</el-dropdown-item><el-dropdown-item v-for="item in secondaryItems" :key="item.path" @click="$router.push(item.path)">{{ item.name }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
          <el-dropdown class="mobile-nav" trigger="click"><el-button>步骤导航</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item v-for="item in allItems" :key="item.path" @click="$router.push(item.path)">{{ item.step ? `${item.step}. ` : '' }}{{ item.name }}</el-dropdown-item></el-dropdown-menu></template></el-dropdown>
          <el-button text @click="logout">退出</el-button>
        </div>
      </div>
    </header>
    <el-main class="app-main"><router-view /></el-main>
  </el-container>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { clearToken } from './api/client.js'
import { primaryNavigation, secondaryNavigation } from './utils/display.js'

const router = useRouter()
const primaryItems = primaryNavigation
const secondaryItems = secondaryNavigation
const home = { path: '/', name: '首页', hint: '系统会告诉你现在最该做什么' }
const allItems = [home, ...primaryItems, ...secondaryItems]
function logout() { clearToken(); router.push('/login') }
</script>
