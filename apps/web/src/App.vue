<script setup lang="ts">
import "./shared/styles/base.css";
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div><strong>Wealth Agent</strong><span>本地资产工作台</span></div>
      <nav aria-label="主导航">
        <RouterLink to="/reports">资产报表</RouterLink>
        <RouterLink to="/chat">智能会话</RouterLink>
        <RouterLink to="/backups">备份恢复</RouterLink>
      </nav>
    </header>

    <main>
      <RouterView />
    </main>
  </div>
</template>
