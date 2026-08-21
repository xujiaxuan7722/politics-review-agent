<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">错题本</h1>
        <p class="page-subtitle">
          查看 AI 分析结果，并把政治选择题、材料题和易混考点转成背诵卡。
        </p>
      </div>
      <div class="button-row">
        <el-button type="primary" @click="addDialogVisible = true">手动录题</el-button>
        <el-button :disabled="!mistakes.length" @click="exportMistakes">导出错题</el-button>
        <el-button
          type="danger"
          :disabled="!mistakes.length"
          :loading="deletingAll"
          @click="deleteAllMistakes"
        >
          全部删除
        </el-button>
        <el-button type="primary" :loading="loading" @click="loadMistakes">刷新</el-button>
      </div>
    </div>

    <el-card class="panel">
      <div class="keyword-search">
        <el-input
          v-model="keyword"
          clearable
          placeholder="输入关键词索引错题，例如：实践、矛盾、史纲、毛中特、多选题"
        />
        <el-button @click="keyword = ''">清空</el-button>
      </div>

      <div class="keyword-tags">
        <el-tag
          v-for="tag in keywordTags"
          :key="tag"
          effect="plain"
          class="keyword-tag"
          @click="keyword = tag"
        >
          {{ tag }}
        </el-tag>
      </div>

      <p class="metric-hint">
        当前显示 {{ filteredMistakes.length }} / {{ mistakes.length }} 条错题
      </p>
    </el-card>

    <div v-if="filteredMistakes.length" class="card-list">
      <el-card v-for="item in filteredMistakes" :key="item.id" class="item-card">
        <h3>{{ item.title }}</h3>
        <div v-if="getKeywords(item).length" class="keyword-tags compact-tags">
          <el-tag
            v-for="tag in getKeywords(item)"
            :key="`${item.id}-${tag}`"
            type="primary"
            effect="plain"
          >
            {{ tag }}
          </el-tag>
        </div>
        <div class="answer-body" v-html="renderContent(formatMistakeCardContent(item))"></div>

        <div class="toolbar" style="margin-top: 16px">
          <div class="button-row">
            <el-button @click="router.push(`/mistakes/${item.id}`)">查看详情</el-button>
            <el-button type="success" @click="generateCards(item.id)">生成复习卡</el-button>
            <el-button type="danger" :loading="deletingId === item.id" @click="deleteMistake(item)">
              删除
            </el-button>
          </div>
        </div>
      </el-card>
    </div>

    <div v-else class="empty-state">
      {{
        mistakes.length
          ? "没有匹配的错题，换个关键词试试。"
          : "还没有政治错题。可以先到 OCR 页面上传一张选择题或材料题截图。"
      }}
    </div>
  </section>

  <el-dialog v-model="addDialogVisible" title="手动录入错题" width="640px" align-center>
    <el-input v-model="newMistake.title" placeholder="错题标题，例如：新民主主义革命的领导力量" />
    <el-input
      v-model="newMistake.text"
      type="textarea"
      :rows="8"
      style="margin-top: 12px"
      placeholder="粘贴完整题目（含选项）、你的答案和正确答案。AI 会自动分析错因并提取考点关键词。"
    />
    <template #footer>
      <div class="logout-actions">
        <el-button :disabled="addingMistake" @click="addDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="addingMistake" @click="submitNewMistake">
          录入并分析
        </el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="deleteAllDialogVisible"
    class="logout-dialog delete-all-dialog"
    width="430px"
    align-center
    :show-close="false"
  >
    <div class="logout-card-head">
      <div class="logout-mark delete-mark" aria-hidden="true">
        <span class="logout-orbit"></span>
        <span class="delete-dot"></span>
      </div>
      <div>
        <h3>确认删除全部错题？</h3>
        <p>将删除 {{ mistakes.length }} 条错题，关联复习卡也会一起清理。</p>
      </div>
    </div>

    <div class="logout-account delete-summary">
      <span>即将删除</span>
      <strong>{{ mistakes.length }} 条错题</strong>
    </div>

    <template #footer>
      <div class="logout-actions">
        <el-button :disabled="deletingAll" @click="deleteAllDialogVisible = false">
          取消
        </el-button>
        <el-button type="primary" :loading="deletingAll" @click="confirmDeleteAllMistakes">
          全部删除
        </el-button>
      </div>
    </template>
  </el-dialog>

  <el-dialog
    v-model="deleteDialogVisible"
    class="logout-dialog delete-all-dialog"
    width="430px"
    align-center
    :show-close="false"
  >
    <div class="logout-card-head">
      <div class="logout-mark delete-mark" aria-hidden="true">
        <span class="logout-orbit"></span>
        <span class="delete-dot"></span>
      </div>
      <div>
        <h3>删除这条错题？</h3>
        <p>删除后，关联复习卡也会一起清理。</p>
      </div>
    </div>

    <div class="logout-account delete-summary">
      <span>即将删除</span>
      <strong>{{ pendingDelete?.title || "当前错题" }}</strong>
    </div>

    <template #footer>
      <div class="logout-actions">
        <el-button :disabled="!!deletingId" @click="deleteDialogVisible = false">
          取消
        </el-button>
        <el-button type="primary" :loading="!!deletingId" @click="confirmDeleteMistake">
          删除
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { useRouter } from "vue-router";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const router = useRouter();
const mistakes = ref([]);
const loading = ref(false);
const deletingId = ref(null);
const deletingAll = ref(false);
const deleteAllDialogVisible = ref(false);
const deleteDialogVisible = ref(false);
const pendingDelete = ref(null);
const addDialogVisible = ref(false);
const addingMistake = ref(false);
const newMistake = ref({ title: "", text: "" });
const keyword = ref("");

async function submitNewMistake() {
  if (!newMistake.value.title.trim() || !newMistake.value.text.trim()) {
    ElMessage.warning("标题和题目内容都要填。");
    return;
  }

  addingMistake.value = true;
  try {
    await request.post("/api/mistakes", {
      title: newMistake.value.title,
      raw_text: newMistake.value.text,
      clean_text: newMistake.value.text,
    });
    ElMessage.success("已录入，AI 分析完成");
    addDialogVisible.value = false;
    newMistake.value = { title: "", text: "" };
    await loadMistakes();
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "录入失败，请检查后端接口。");
  } finally {
    addingMistake.value = false;
  }
}
const keywordTags = [
  "马克思主义",
  "毛中特",
  "史纲",
  "思修法基",
  "形势与政策",
  "单选题",
  "多选题",
  "分析题",
  "材料题",
  "实践",
  "矛盾",
  "新民主主义革命",
];

const filteredMistakes = computed(() => {
  const key = keyword.value.trim().toLowerCase();
  if (!key) return mistakes.value;

  return mistakes.value.filter((item) => {
    const haystack = [
      item.title,
      item.raw_text,
      item.clean_text,
      item.analysis,
      item.knowledge_points,
    ]
      .filter(Boolean)
      .join("\n")
      .toLowerCase();

    return haystack.includes(key);
  });
});

function renderContent(text) {
  return renderMarkdown(text || "");
}

function formatMistakeCardContent(item) {
  const source = [item.analysis, item.clean_text].filter(Boolean).join("\n\n");
  const keywordText = extractCardSection(source, ["关键词"]) || getKeywords(item).join("、") || "暂无关键词";
  const generatedText = String(item.clean_text || item.analysis || "").trim() || "暂无生成内容";

  return `## 关键词\n${keywordText}\n\n${generatedText}`;
}

function extractCardSection(text, names) {
  const content = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  for (const name of names) {
    const headingPattern = new RegExp(
      `(?:^|\\n)#{1,6}\\s*${name}\\s*\\n([\\s\\S]*?)(?=\\n#{1,6}\\s+|$)`,
      "i",
    );
    const headingMatch = content.match(headingPattern);
    if (headingMatch?.[1]?.trim()) {
      return cleanCardSection(headingMatch[1]);
    }

    const inlinePattern = new RegExp(
      `${name}\\s*[：:]\\s*([\\s\\S]*?)(?=\\n\\s*(?:#{1,6}\\s*)?(?:关键词|正确思路|标准思路|背诵句|背诵金句|题目类型|模块定位|易错原因|题干|解析)\\s*[：:]?|$)`,
      "i",
    );
    const inlineMatch = content.match(inlinePattern);
    if (inlineMatch?.[1]?.trim()) {
      return cleanCardSection(inlineMatch[1]);
    }
  }

  return "";
}

function cleanCardSection(text) {
  return String(text || "")
    .replace(/^\s*[-*]\s*/gm, "- ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function getKeywords(item) {
  return String(item.knowledge_points || "")
    .split(/[、,，\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

async function loadMistakes() {
  loading.value = true;

  try {
    const res = await request.get("/api/mistakes");
    mistakes.value = res.data;
  } catch (error) {
    ElMessage.error("加载错题失败，请检查后端接口。");
  } finally {
    loading.value = false;
  }
}

async function generateCards(id) {
  try {
    const res = await request.post(`/api/review/generate-from-mistake/${id}`);
    const count = Number(res.data?.count || 0);
    if (!count) {
      ElMessage.warning("没有生成复习卡，请重新尝试。");
      return;
    }

    ElMessage.success(`已生成 ${count} 张复习卡，可在今日复习查看`);
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "生成失败，请检查模型接口或余额。");
  }
}

function deleteMistake(item) {
  pendingDelete.value = item;
  deleteDialogVisible.value = true;
}

async function confirmDeleteMistake() {
  const item = pendingDelete.value;
  if (!item) return;

  deletingId.value = item.id;

  try {
    await request.delete(`/api/mistakes/${item.id}`);
    mistakes.value = mistakes.value.filter((mistake) => mistake.id !== item.id);
    deleteDialogVisible.value = false;
    pendingDelete.value = null;
    ElMessage.success("已删除错题");
  } catch (error) {
    ElMessage.error("删除失败，请检查后端接口。");
  } finally {
    deletingId.value = null;
  }
}

async function deleteAllMistakes() {
  if (!mistakes.value.length) return;
  deleteAllDialogVisible.value = true;
}

async function confirmDeleteAllMistakes() {
  if (!mistakes.value.length) return;

  deletingAll.value = true;

  try {
    await request.delete("/api/mistakes");
    mistakes.value = [];
    keyword.value = "";
    deleteAllDialogVisible.value = false;
    ElMessage.success("已删除全部错题");
  } catch (error) {
    ElMessage.error("全部删除失败，请检查后端接口。");
  } finally {
    deletingAll.value = false;
  }
}

function exportMistakes() {
  const content = filteredMistakes.value
    .map((item) => {
      const tags = getKeywords(item).join("、") || "未标注";
      return `# ${item.title}\n\n- 编号：${item.id}\n- 关键词：${tags}\n\n## AI 分析\n${item.analysis || ""}\n\n## 题目文本\n${item.clean_text || ""}`;
    })
    .join("\n\n---\n\n");

  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `考研政治错题本-${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

onMounted(loadMistakes);
</script>
