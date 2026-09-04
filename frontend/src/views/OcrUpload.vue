<template>
  <section class="page">
    <div class="page-header">
      <div>
        <h1 class="page-title">拍题判卷</h1>
        <p class="page-subtitle">
          上传题目截图 → 校正识别文本 → 先自己作答 → AI 判题并自动归档错题、生成复习卡。
        </p>
      </div>
    </div>

    <el-card class="panel upload-panel">
      <el-upload drag :http-request="uploadFile" :show-file-list="false" :disabled="uploading">
        <p class="upload-title">拖拽图片到这里，或点击上传</p>
        <p class="upload-text">
          支持题干、材料题、选择题截图（≤5MB）。识别后可手动修正文本再作答。
        </p>
      </el-upload>
    </el-card>

    <el-card v-if="previewUrl || statusText" class="panel">
      <div class="ocr-workspace">
        <div class="image-preview">
          <img v-if="previewUrl" :src="previewUrl" alt="uploaded preview" />
          <div v-else class="empty-state">等待图片上传</div>
        </div>

        <div class="ocr-side">
          <div class="ocr-status">
            <div>
              <p class="metric-label">上传文件</p>
              <strong>{{ fileName || "尚未选择文件" }}</strong>
            </div>
            <div>
              <p class="metric-label">当前状态</p>
              <strong>{{ statusText || "等待上传" }}</strong>
            </div>
          </div>

          <div class="button-row" style="margin-top: 16px">
            <el-button :disabled="uploading || grading || solving" @click="resetUpload">重新上传</el-button>
          </div>

          <div v-if="keywords.length" class="keyword-tags">
            <el-tag v-for="tag in keywords" :key="tag" effect="plain" type="primary" class="keyword-tag">
              {{ tag }}
            </el-tag>
          </div>
        </div>
      </div>
    </el-card>

    <el-card v-if="rawText" class="panel">
      <template #header>
        <div class="answer-header">
          <strong>题目文本（可编辑校正）</strong>
        </div>
      </template>

      <el-input v-model="rawText" type="textarea" :rows="8" />

      <div class="button-row" style="margin-top: 14px">
        <el-radio-group v-model="questionType" size="small">
          <el-radio-button value="single">单选</el-radio-button>
          <el-radio-button value="multi">多选</el-radio-button>
          <el-radio-button value="unknown">不确定</el-radio-button>
        </el-radio-group>
        <el-input
          v-model="studentAnswer"
          placeholder="先自己作答：选择题填字母（单选如 B，多选如 ABD），分析题写要点"
          style="max-width: 420px"
        />
        <el-button type="primary" :loading="grading" @click="gradeArchive">
          判题并归档
        </el-button>
        <el-button :loading="solving" @click="solveText">跳过作答，直接看解析</el-button>
      </div>
      <p class="metric-hint" style="margin-top: 8px">
        「判题并归档」：AI 先批改你的答案，答错会自动写入错题本并生成复习卡，进入今日复习队列。
      </p>
    </el-card>

    <el-card v-if="gradeResult" class="panel">
      <template #header>
        <div class="answer-header">
          <strong>判题结果</strong>
          <el-tag :type="judgementTagType" effect="dark" size="large" style="justify-self: start">
            {{ gradeResult.judgement }}
          </el-tag>
        </div>
      </template>

      <div class="answer-body" v-html="renderContent(gradeResult.grade)"></div>

      <p v-if="gradeResult.archived" class="metric-hint" style="margin-top: 12px">
        已自动归档为错题 #{{ gradeResult.mistake_id }}，生成 {{ gradeResult.cards_count }} 张复习卡，可到「今日复习」查看。
      </p>
      <p v-else class="metric-hint" style="margin-top: 12px">
        回答正确，本题未写入错题本。
      </p>
    </el-card>

    <el-card v-if="answer" class="panel">
      <template #header>
        <div class="answer-header">
          <strong>AI 解析</strong>
        </div>
      </template>
      <div class="answer-body" v-html="renderContent(answer)"></div>
    </el-card>

    <div v-if="!rawText && !uploading && !statusText" class="empty-state">
      请先上传一张政治题目截图。
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import request from "../api/request";
import { renderMarkdown } from "../utils/markdown";

const rawText = ref("");
const studentAnswer = ref("");
const questionType = ref("unknown"); // 题型由拍题的人指定，不让模型或代码猜
const answer = ref("");
const gradeResult = ref(null);
const title = ref("");
const fileName = ref("");
const previewUrl = ref("");
const statusText = ref("");
const uploading = ref(false);
const grading = ref(false);
const solving = ref(false);
const keywords = ref([]);

const judgementTagType = computed(() => {
  const judgement = gradeResult.value?.judgement;
  if (judgement === "正确") return "success";
  if (judgement === "基本正确") return "warning";
  return "danger";
});

function renderContent(text) {
  return renderMarkdown(text || "");
}

function resetUpload() {
  if (previewUrl.value) {
    URL.revokeObjectURL(previewUrl.value);
  }
  rawText.value = "";
  studentAnswer.value = "";
  answer.value = "";
  gradeResult.value = null;
  title.value = "";
  fileName.value = "";
  previewUrl.value = "";
  statusText.value = "";
  keywords.value = [];
}

async function uploadFile(option) {
  resetUpload();
  fileName.value = option.file.name;
  previewUrl.value = URL.createObjectURL(option.file);
  title.value = option.file.name.replace(/\.[^.]+$/, "");
  uploading.value = true;
  statusText.value = "正在识别图片文字...";

  const formData = new FormData();
  formData.append("file", option.file);

  try {
    const ocrRes = await request.post("/api/ocr/recognize", formData, { timeout: 70000 });
    rawText.value = ocrRes.data.text || "";
    if (!rawText.value.trim()) {
      throw new Error("没有识别到文字，请换一张更清晰的截图。");
    }
    // 默认标题取题干前 20 字，而不是图片文件名
    title.value = rawText.value.replace(/\s+/g, " ").trim().slice(0, 20);
    statusText.value = "识别完成。请先校对下方文本，再作答判题。";
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || "未知错误";
    statusText.value = `图片识别失败：${detail}`;
    ElMessage.error(detail);
  } finally {
    uploading.value = false;
  }
}

async function gradeArchive() {
  if (!studentAnswer.value.trim()) {
    ElMessage.warning("请先在作答框里写下你的答案。");
    return;
  }

  grading.value = true;
  gradeResult.value = null;
  statusText.value = "AI 正在解题并批改你的答案（思考模式，约半分钟）...";

  try {
    const res = await request.post("/api/pipeline/grade-archive", {
      question_text: rawText.value,
      student_answer: studentAnswer.value,
      title: title.value,
      question_type: questionType.value,
    });

    gradeResult.value = res.data;
    statusText.value = "判题完成。";
    ElMessage.success(res.data.archived ? "判题完成，已归档并生成复习卡" : "回答正确！");
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || "未知错误";
    statusText.value = `判题失败：${detail}`;
    ElMessage.error(detail);
  } finally {
    grading.value = false;
  }
}

async function solveText() {
  solving.value = true;
  answer.value = "";
  statusText.value = "AI 正在整理题目并解答...";

  try {
    const res = await request.post(
      "/api/ocr/solve-raw-text",
      { raw_text: rawText.value, title: title.value },
      { timeout: 180000 },
    );

    answer.value = res.data.answer || "";
    keywords.value = Array.isArray(res.data.keywords) ? res.data.keywords.filter(Boolean) : [];
    statusText.value = "解析完成。";
    ElMessage.success("AI 解析完成");
  } catch (error) {
    const detail = error.response?.data?.detail || error.message || "未知错误";
    statusText.value = `AI 解析失败：${detail}`;
    ElMessage.error(detail);
  } finally {
    solving.value = false;
  }
}

</script>
