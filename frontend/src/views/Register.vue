<template>
  <main class="auth-page">
    <section class="auth-card">
      <div class="auth-visual">
        <div class="auth-logo">研</div>
        <p class="eyebrow">Create Workspace</p>
        <h1>建立你的政治复习档案</h1>
        <p>
          注册后可以保存错题、生成复习卡片，并追踪今日背诵和错题复盘任务。
        </p>
        <div class="tech-orbit">
          <span></span>
          <span></span>
          <span></span>
        </div>
      </div>

      <div class="auth-form">
        <h2>创建账号</h2>
        <p>用户名至少 3 位，密码至少 6 位。</p>

        <el-form label-position="top" @submit.prevent>
          <el-form-item label="用户名">
            <el-input v-model="form.username" size="large" placeholder="例如 politics_user" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              placeholder="至少 6 位"
            />
          </el-form-item>
          <el-form-item label="确认密码">
            <el-input
              v-model="confirmPassword"
              size="large"
              type="password"
              show-password
              placeholder="再次输入密码"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button type="primary" size="large" class="auth-submit" :loading="loading" @click="submit">
            注册并进入
          </el-button>
        </el-form>

        <p class="auth-switch">
          已有账号？
          <router-link to="/login">去登录</router-link>
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
const confirmPassword = ref("");
const form = reactive({
  username: "",
  password: "",
});

async function submit() {
  if (form.username.trim().length < 3 || form.password.length < 6) {
    ElMessage.warning("用户名至少 3 位，密码至少 6 位。");
    return;
  }

  if (form.password !== confirmPassword.value) {
    ElMessage.warning("两次密码不一致。");
    return;
  }

  loading.value = true;

  try {
    const res = await request.post("/api/auth/register", form);
    localStorage.setItem("auth_token", res.data.token);
    localStorage.setItem("auth_user", JSON.stringify(res.data.user));
    window.dispatchEvent(new Event("auth-user-updated"));
    ElMessage.success("注册成功");
    router.push("/");
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || "注册失败");
  } finally {
    loading.value = false;
  }
}
</script>
