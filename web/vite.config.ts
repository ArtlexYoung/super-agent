import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig, type Plugin } from "vite"

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), removeGeneratedTrailingWhitespace()],
  build: {
    emptyOutDir: true,
    outDir: "../src/adapter/ag_ui_adapter/static",
  },
  server: {
    proxy: {
      "/ag-ui": "http://127.0.0.1:8765",
      "/api": "http://127.0.0.1:8765",
      "/health": "http://127.0.0.1:8765",
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
})

function removeGeneratedTrailingWhitespace(): Plugin {
  return {
    name: "remove-generated-trailing-whitespace",
    generateBundle(_options, bundle) {
      for (const output of Object.values(bundle)) {
        if (output.type === "chunk") {
          output.code = output.code.replace(/[ \t]+$/gm, "")
        }
      }
    },
  }
}
