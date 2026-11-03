// @vitest-environment jsdom

import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { createMemoryHistory } from "vue-router";

import App from "./App.vue";
import { createAppRouter } from "./router";

describe("治理应用", () => {
  it("根路径进入治理页面且没有连接设置入口", async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push("/");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    expect(router.currentRoute.value.path).toBe("/model-governance");
    expect(wrapper.text()).toContain("模型治理");
    expect(wrapper.text()).not.toContain("连接设置");
  });

  it("生产路由把治理路径写入 URL 哈希", async () => {
    window.location.hash = "";
    const router = createAppRouter();
    await router.push("/model-governance");
    await router.isReady();

    expect(window.location.hash).toBe("#/model-governance");
  });
});
