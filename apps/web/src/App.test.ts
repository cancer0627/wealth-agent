// @vitest-environment jsdom

import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { createMemoryHistory } from "vue-router";

import App from "./App.vue";
import { createAppRouter } from "./router";

describe("C 端应用", () => {
  it("根路径进入资产页面，并通过路由导航到会话页面", async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push("/");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    expect(wrapper.text()).toContain("资产与收益报表");
    expect(wrapper.text()).not.toContain("录入台账");
    expect(wrapper.text()).not.toContain("连接设置");

    await wrapper.findAll("nav a")[1].trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.path).toBe("/chat");
    expect(wrapper.text()).toContain("智能会话");
  });

  it("未知路径回退到资产页面", async () => {
    const router = createAppRouter(createMemoryHistory());
    await router.push("/missing");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/reports");
  });

  it("生产路由把页面路径写入 URL 哈希", async () => {
    window.location.hash = "";
    const router = createAppRouter();
    await router.push("/backups");
    await router.isReady();

    expect(window.location.hash).toBe("#/backups");
  });
});
