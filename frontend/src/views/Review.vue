<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">今日复习</h1>
        <p class="page-subtitle">
          按掌握程度给政治背诵卡打分，系统会自动安排下一次复习日期。
        </p>
      </div>
      <el-button type="primary" :loading="loading" @click="loadCards">刷新</el-button>
    </div>

    <div v-if="cards.length" class="card-list">
      <el-card v-for="card in cards" :key="card.id" class="item-card">
        <div class="toolbar">
          <div class="button-row">
            <el-tag v-if="card.knowledge_point" type="primary" effect="plain">
              {{ card.knowledge_point }}
            </el-tag>
            <span class="metric-hint">已复习 {{ card.repetition || 0 }} 次</span>
            <span class="metric-hint">当前间隔 {{ card.interval_days || 1 }} 天</span>
          </div>
        </div>

        <p v-if="card.mistake_id" class="metric-hint" style="margin-top: 10px">
          来源错题：{{ card.mistake_title || `#${card.mistake_id}` }}
          <el-link type="primary" :underline="false" style="margin-left: 6px" @click="router.push(`/mistakes/${card.mistake_id}`)">
            查看原题
          </el-link>
        </p>

        <div class="answer-body compact" style="margin-top: 10px" v-html="renderContent(card.question)"></div>

        <el-button style="margin-top: 14px" @click="card.show = !card.show">
          {{ card.show ? "隐藏答案" : "显示答案" }}
        </el-button>

        <div v-if="card.show" class="answer-body" style="margin-top: 16px" v-html="renderContent(card.answer)"></div>

        <div class="score-row">
          <span class="metric-hint">这张卡记得怎么样？</span>
          <el-button type="danger" size="large" @click="submit(card.id, 1)">忘了</el-button>
          <el-button type="warning" size="large" @click="submit(card.id, 3)">有点模糊</el-button>
          <el-button type="success" size="large" @click="submit(card.id, 5)">记得牢</el-button>
        </div>
        <p class="metric-hint">「忘了」的卡片今天会重新出现，直到你记住为止；「记得牢」会拉长下次复习间隔。</p>
      </el-card>
    </div>

    <div v-else class="empty-state">
      今天暂无待复习的政治背诵卡。生成复习卡后会自动出现在这里。
    </div>
  </section>
</template>

<script setup>
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const router = useRouter();
const cards = ref([]);
const loading = ref(false);

function renderContent(text) {
  return renderMarkdown(text || "");
}

async function loadCards() {
  loading.value = true;

  try {
    const res = await request.get("/api/review/today");
    cards.value = res.data.map((item) => ({
      ...item,
      show: false,
    }));
  } catch (error) {
    ElMessage.error("加载复习卡失败，请检查后端接口。");
  } finally {
    loading.value = false;
  }
}

async function submit(cardId, quality) {
  try {
    await request.post("/api/review/submit", {
      card_id: cardId,
      quality,
    });

    ElMessage.success(quality < 3 ? "这张卡今天会再出现一次，加油。" : "复习计划已更新。");
    await loadCards();
  } catch (error) {
    ElMessage.error("提交失败，请检查复习接口。");
  }
}

onMounted(loadCards);
</script>
