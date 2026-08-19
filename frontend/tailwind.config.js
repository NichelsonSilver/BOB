/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Paleta de CLAUDE.md — dark minimalista
        surface: "#0e0f0f",
        accent: "#3cd9a8", // verde long / positivo
        danger: "#f07045", // rojo short / negativo
        warn: "#f5b731", // amarillo alerts
        success: "#3cd9a8",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
