import {
  createRouter,
  createWebHashHistory,
  type RouterHistory,
} from "vue-router";

import GovernancePage from "./pages/model-governance/implementation/GovernancePage.vue";

export function createAppRouter(history: RouterHistory = createWebHashHistory()) {
  return createRouter({
    history,
    routes: [
      { path: "/", redirect: "/model-governance" },
      {
        path: "/model-governance",
        name: "model-governance",
        component: GovernancePage,
      },
      { path: "/:pathMatch(.*)*", redirect: "/model-governance" },
    ],
  });
}
