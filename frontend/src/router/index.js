import { createRouter, createWebHistory } from "vue-router";

import Dashboard from "../views/Dashboard.vue";
import Chat from "../views/Chat.vue";
import OcrUpload from "../views/OcrUpload.vue";
import Mistakes from "../views/Mistakes.vue";
import Review from "../views/Review.vue";
import Agents from "../views/Agents.vue";
import Login from "../views/Login.vue";
import Register from "../views/Register.vue";
import Settings from "../views/Settings.vue";
import MistakeDetail from "../views/MistakeDetail.vue";
import Records from "../views/Records.vue";

const routes = [
  { path: "/login", component: Login, meta: { public: true } },
  { path: "/register", component: Register, meta: { public: true } },
  { path: "/", component: Dashboard },
  { path: "/chat", component: Chat },
  { path: "/ocr", component: OcrUpload },
  { path: "/mistakes", component: Mistakes },
  { path: "/mistakes/:id", component: MistakeDetail },
  { path: "/review", component: Review },
  { path: "/agents", component: Agents },
  { path: "/records", component: Records },
  { path: "/settings", component: Settings },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const token = localStorage.getItem("auth_token");

  if (!to.meta.public && !token) {
    return "/login";
  }

  if (to.meta.public && token) {
    return "/";
  }

  return true;
});

export default router;
