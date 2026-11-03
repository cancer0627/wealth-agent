import {
  createRouter,
  createWebHashHistory,
  type RouterHistory,
} from "vue-router";

import BackupsPage from "./pages/backups/implementation/BackupsPage.vue";
import ChatPage from "./pages/chat/implementation/ChatPage.vue";
import ReportsPage from "./pages/reports/implementation/ReportsPage.vue";

export function createAppRouter(history: RouterHistory = createWebHashHistory()) {
  return createRouter({
    history,
    routes: [
      { path: "/", redirect: "/reports" },
      { path: "/reports", name: "reports", component: ReportsPage },
      { path: "/chat", name: "chat", component: ChatPage },
      { path: "/backups", name: "backups", component: BackupsPage },
      { path: "/:pathMatch(.*)*", redirect: "/reports" },
    ],
  });
}
