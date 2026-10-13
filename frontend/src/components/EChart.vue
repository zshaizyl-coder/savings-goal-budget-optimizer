<template><div ref="element" class="chart"></div></template>
<script setup>
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const props = defineProps({ option: { type: Object, required: true } })
const element = ref(); let chart
function render() { if (chart) chart.setOption(props.option, true) }
function resize() { chart?.resize() }
onMounted(() => { chart = echarts.init(element.value); render(); window.addEventListener('resize', resize) })
watch(() => props.option, render, { deep: true })
onBeforeUnmount(() => { window.removeEventListener('resize', resize); chart?.dispose() })
</script>
