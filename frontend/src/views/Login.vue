<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="auth-visual">
        <div class="auth-logo">政</div>
        <p class="eyebrow">Politics Review AI</p>
        <h1>登录考研政治复习系统</h1>
        <p>
          用 OCR、RAG 知识库和抗遗忘复习，把政治错题、知识点和背诵任务整理成可追踪的复习节奏。
        </p>
        <div class="tech-orbit">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>

      <div class="auth-form">
        <h2>欢迎回来</h2>
        <p>登录后进入你的考研政治学习工作台。</p>

        <el-form label-position="top" @submit.prevent>
          <el-form-item label="用户名">
            <el-input v-model="form.username" size="large" placeholder="admin" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              placeholder="至少 6 位"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button type="primary" size="large" class="auth-submit" :loading="loading" @click="submit">
            登录
          </el-button>
        </el-form>

        <p class="auth-switch">
          还没有账号？
          <router-link to="/register">去注册</router-link>
        </p>
      </div>
    </section>
  </main>
</template>

<script setup>
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ElMessage } from "element-plus";
import request from "../api/request";

const router = useRouter();
const loading = ref(false);
const form = reactive({
  username: "",
  password: "",
});

async function submit() {
  if (!form.username.trim() || form.password.length < 6) {
    ElMessage.warning("请输入用户名和至少 6 位密码。");
    return;
  }

  loading.value = true;

  try {
    const res = await request.post("/api/auth/login", form);
    localStorage.setItem("auth_token", res.data.token);
    localStorage.setItem("auth_user", JSON.stringify(res.data.user));
    window.dispatchEvent(new Event("auth-user-updated"));
    ElMessage.success("登录成功");
    router.push("/");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "登录失败");
  } finally {
    loading.value = false;
  }
}
</script>
