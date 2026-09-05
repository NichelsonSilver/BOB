import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * `BOB_API` permite apuntar el dev server a otro backend sin editar este
 * archivo. No es una comodidad: durante la validación forward el backend de
 * producción está acumulando la muestra y no se reinicia, así que el
 * desarrollo del dashboard corre contra una segunda instancia offline
 * (`BOB_LIVE_DATA=false ... --port 8001`) que lee la misma DB y no escribe.
 */
// Declarado acá en vez de agregar `@types/node`: este archivo es lo único
// del frontend que corre en Node, y una dependencia de tipos entera para leer
// una variable de entorno es peso que no se paga.
declare const process: { env: Record<string, string | undefined> };

const target = process.env.BOB_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
