import { createRouter, createWebHistory } from 'vue-router'
const routes = [
  { path: '/login', component: () => import('../views/LoginView.vue'), meta: { public: true } },
  { path: '/', component: () => import('../views/DashboardView.vue') },
  { path: '/bills', component: () => import('../views/BillsView.vue') },
  { path: '/analytics', component: () => import('../views/AnalyticsView.vue') },
  { path: '/forecast', component: () => import('../views/ForecastView.vue') },
  { path: '/goals', component: () => import('../views/GoalView.vue') },
  { path: '/budgets', component: () => import('../views/BudgetView.vue') },
  { path: '/replan', component: () => import('../views/ReplanView.vue') },
  { path: '/history', component: () => import('../views/HistoryView.vue') }
]

const router = createRouter({ history: createWebHistory(), routes })
router.beforeEach(to => {
  if (!to.meta.public && !localStorage.getItem('access_token')) return '/login'
})
export default router
