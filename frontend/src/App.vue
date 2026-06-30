<template>
  <div id="app-root">
    <router-view />
  </div>
</template>

<script>
export default { name: 'App' }
</script>

<style>
* { box-sizing: border-box; }
html, body, #app, #app-root { height: 100%; margin: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif; background: #f5f7fa; color: #303133; }
</style>
