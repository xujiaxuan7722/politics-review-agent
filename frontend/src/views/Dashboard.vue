<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">学习总览</h1>
        <p class="page-subtitle">
          把考研政治错题、知识库问答、图片识别解析和 AI 学习助手记录放在同一个复习节奏里。
        </p>
      </div>
      <el-button type="primary" @click="loadAll">刷新数据</el-button>
    </div>

    <div class="metric-grid">
      <article class="metric-card">
        <p class="metric-label">错题数量</p>
        <div class="metric-value">{{ stats.mistake_count }}</div>
        <p class="metric-hint">已整理并进入分析流程的政治题目</p>
      </article>

      <article class="metric-card">
        <p class="metric-label">复习卡数量</p>
        <div class="metric-value">{{ stats.card_count }}</div>
        <p class="metric-hint">由错题自动生成的记忆卡片</p>
      </article>

      <article class="metric-card">
        <p class="metric-label">今日待复习</p>
        <div class="metric-value">{{ stats.today_review_count }}</div>
        <p class="metric-hint">需要今天完成的抗遗忘任务</p>
      </article>
    </div>

    <div class="chart-grid">
      <el-card class="panel">
        <template #header><strong>错题关键词分布</strong></template>
        <ChartBars :items="charts.mistake_modules" />
      </el-card>
      <el-card class="panel">
        <template #header><strong>复习时间安排</strong></template>
        <ChartBars :items="charts.review_schedule" />
      </el-card>
      <el-card class="panel">
        <template #header><strong>掌握度分布</strong></template>
        <ChartBars :items="charts.mastery" />
      </el-card>
    </div>

    <div class="page-header" style="margin-top: 8px">
      <div>
        <h2 class="page-title" style="font-size: 22px">学习数据</h2>
        <p class="page-subtitle">基于每一次复习打分（review_logs）统计，数据越多曲线越准。</p>
      </div>
    </div>

    <div class="metric-grid">
      <article class="metric-card">
        <p class="metric-label">累计复习次数</p>
        <div class="metric-value">{{ learning.summary.total_reviews }}</div>
        <p class="metric-hint">已学习 {{ learning.summary.active_days }} 天</p>
      </article>
      <article class="metric-card">
        <p class="metric-label">整体记住率</p>
        <div class="metric-value">{{ formatPercent(learning.summary.accuracy) }}</div>
        <p class="metric-hint">打"有点模糊"及以上视为记住</p>
      </article>
      <article class="metric-card">
        <p class="metric-label">连续学习天数</p>
        <div class="metric-value">{{ learning.summary.streak_days }}</div>
        <p class="metric-hint">截至今天或昨天的连续天数</p>
      </article>
      <article class="metric-card">
        <p class="metric-label">已掌握卡片</p>
        <div class="metric-value">{{ learning.summary.mastered_cards }} / {{ learning.summary.total_cards }}</div>
        <p class="metric-hint">连续答对 3 次或复习间隔 ≥ 7 天</p>
      </article>
    </div>

    <div class="chart-grid">
      <el-card class="panel">
        <template #header><strong>最近 14 天复习量</strong></template>
        <ChartBars :items="dailyReviewItems" unit=" 次" />
      </el-card>
      <el-card class="panel">
        <template #header><strong>间隔保持率（近似遗忘曲线）</strong></template>
        <ChartBars :items="retentionItems" unit="%" empty-text="还没有复习记录" />
      </el-card>
      <el-card class="panel">
        <template #header><strong>最常遗忘的知识点</strong></template>
        <ChartBars :items="weakPointItems" unit=" 次" empty-text="暂无遗忘记录，继续保持" />
      </el-card>
    </div>

    <el-card class="panel">
      <div class="toolbar">
        <div>
          <strong>搜索记录已移到独立板块</strong>
          <p class="metric-hint">智能问答、拍题判卷、练习与规划的历史记录都保存在服务端，支持搜索与删除。</p>
        </div>
        <el-button type="primary" @click="router.push('/records')">前往搜索记录</el-button>
      </div>
    </el-card>
  </section>
</template>

<script setup>
import { computed, defineComponent, h, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import request from "../api/request";

const router = useRouter();

const ChartBars = defineComponent({
  props: {
    items: { type: Array, default: () => [] },
    unit: { type: String, default: "" },
    emptyText: { type: String, default: "暂无数据" },
  },
  setup(props) {
    return () => {
      const max = Math.max(...props.items.map((item) => item.value || 0), 1);

      if (!props.items.length) {
        return h("div", { class: "empty-state compact-empty" }, props.emptyText);
      }

      return h(
        "div",
        { class: "chart-bars" },
        props.items.map((item) =>
          h("div", { class: "chart-row", key: item.name, title: item.tooltip || `${item.name}：${item.value ?? "-"}${props.unit}` }, [
            h("span", { class: "chart-name" }, item.name),
            h("div", { class: "chart-track" }, [
              h("div", {
                class: "chart-fill",
                style: { width: item.value == null ? "0%" : `${Math.max(4, (item.value / max) * 100)}%` },
              }),
            ]),
            h("span", { class: "chart-value" }, item.value == null ? "—" : `${item.value}${props.unit}`),
          ]),
        ),
      );
    };
  },
});

const learning = ref({
  summary: { total_reviews: 0, accuracy: null, streak_days: 0, active_days: 0, mastered_cards: 0, total_cards: 0 },
  daily: [],
  retention: [],
  weak_points: [],
});

const dailyReviewItems = computed(() =>
  learning.value.daily.map((d) => ({
    name: d.label,
    value: d.reviews,
    tooltip: d.accuracy == null ? `${d.label}：${d.reviews} 次` : `${d.label}：${d.reviews} 次，记住率 ${d.accuracy}%`,
  })),
);

const retentionItems = computed(() =>
  learning.value.retention
    .filter((r) => r.reviews > 0)
    .map((r) => ({ name: r.name, value: r.rate, tooltip: `间隔 ${r.name}：${r.reviews} 次复习，记住 ${r.rate}%` })),
);

const weakPointItems = computed(() =>
  learning.value.weak_points.map((w) => ({ name: w.name, value: w.lapses, tooltip: `${w.name}：${w.cards} 张卡累计忘记 ${w.lapses} 次` })),
);

function formatPercent(value) {
  return value == null ? "—" : `${value}%`;
}

async function loadLearning() {
  try {
    const res = await request.get("/api/stats/learning");
    learning.value = res.data;
  } catch (error) {
    // 统计不可用时保持默认空数据
  }
}

const stats = ref({
  mistake_count: 0,
  card_count: 0,
  today_review_count: 0,
});
const charts = ref({
  mistake_modules: [],
  review_schedule: [],
  mastery: [],
});
async function loadStats() {
  try {
    const [overviewRes, chartRes] = await Promise.all([
      request.get("/api/stats/overview"),
      request.get("/api/stats/charts"),
    ]);
    stats.value = overviewRes.data;
    charts.value = chartRes.data;
  } catch (error) {
    ElMessage.warning("统计接口暂时不可用，请先确认后端已启动。");
  }
}

function loadAll() {
  loadStats();
  loadLearning();
}

onMounted(loadAll);
</script>
