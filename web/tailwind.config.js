/** @type {import('tailwindcss').Config} */
/* The brand palette (option D, chosen 22 Nov 2026): the landing page's dark
   greens and butter yellow, app-wide. The neutral scales (gray, slate) are the
   landing's greens; `indigo`, the app's old purple, is a forest-green scale
   whose light end is yellow (accents on dark); `blue` is a gold scale (the
   Match Score, the Applied icon). The main button on a screen is solid yellow
   with dark `ink` text; a selected tab or chip is a yellow outline. Redefining
   the scales here, not the ~1,000 class names, keeps the components unchanged. */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        gray: { 50: "#f4f7f4", 100: "#e6ede7", 200: "#d3e0d5", 300: "#c4d4c6", 400: "#8ba593", 500: "#6b8a76",
                600: "#3d5e4d", 700: "#2c4a3c", 800: "#12291f", 900: "#0c1f17", 950: "#08160f" },
        slate: { 50: "#f4f7f4", 100: "#e6ede7", 200: "#d3e0d5", 300: "#c4d4c6", 400: "#8ba593", 500: "#6b8a76",
                 600: "#3d5e4d", 700: "#2c4a3c", 800: "#12291f", 900: "#0c1f17", 950: "#08160f" },
        indigo: { 50: "#fff8e0", 100: "#fff1c2", 200: "#ffe9a6", 300: "#ffe28f", 400: "#ffd766",
                  500: "#3f9a68", 600: "#2f7d55", 700: "#256645", 800: "#1c4f36", 900: "#143a28", 950: "#0f2d1f" },
        blue: { 50: "#fff8e0", 100: "#fff1c2", 200: "#ffe9a6", 300: "#ffe28f", 400: "#ffd766",
                500: "#f2c443", 600: "#c99a1f", 700: "#8a6a14", 800: "#5c4710", 900: "#3a2d0a", 950: "#261d06" },
        brand: { yellow: "#ffd766", "yellow-hover": "#ffe28f", "yellow-press": "#f2c443" },
        ink: "#1f1a05",
      },
    },
  },
  plugins: [],
};
