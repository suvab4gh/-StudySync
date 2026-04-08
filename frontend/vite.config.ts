import { defineConfig } from "vite";

export default defineConfig({
  server: {
    proxy: {
      "/upload": "http://localhost:8000",
      "/schedule": "http://localhost:8000",
      "/seed": "http://localhost:8000",
      "/health": "http://localhost:8000"
    }
  }
});
