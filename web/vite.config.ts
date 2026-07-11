import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base "./" makes the built asset URLs relative, so the SPA works when the
// app is mounted under a reverse-proxy sub-path (e.g. /grabbit/).
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8080",
        ws: true,
      },
    },
  },
});
