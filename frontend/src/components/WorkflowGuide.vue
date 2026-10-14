<template>
  <section class="card workflow-card" aria-label="规划进度">
    <div class="workflow-heading"><div><span class="eyebrow">你的规划进度</span><h3>{{ action?.title || '开始建立预算' }}</h3><p class="muted">{{ action?.description || '按顺序完成即可，系统会保留每一步。' }}</p></div><el-button type="primary" size="large" @click="$router.push(action?.path || '/bills')">{{ buttonText }}</el-button></div>
    <div class="workflow-steps">
      <button v-for="(step,index) in steps" :key="step.path" class="workflow-step" :class="{done:step.done}" @click="$router.push(step.path)"><span>{{ step.done ? '✓' : index + 1 }}</span><b>{{step.title}}</b></button>
    </div>
  </section>
</template>
<script setup>
defineProps({ steps: { type: Array, default: () => [] }, action: Object, buttonText: { type: String, default: '继续下一步' } })
</script>
