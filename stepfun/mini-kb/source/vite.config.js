import { defineConfig } from 'vite'

export default defineConfig({
  // 让 vite 不打散 transformers.js，交给 onnxruntime 自己处理
  optimizeDeps: {
    exclude: ['@huggingface/transformers']
  },
  // 比赛评审会在线预览，用相对路径更稳
  base: './'
})
