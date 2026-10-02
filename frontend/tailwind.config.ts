import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Manrope Variable"', '"Manrope"', "system-ui", "sans-serif"],
        mono: ['"SFMono-Regular"', "Consolas", '"Liberation Mono"', "monospace"],
      },
      colors: {
        ink: {
          950: "#0c101b",
          900: "#111624",
          800: "#1a2031",
          700: "#2a3349",
          600: "#3c4863",
        },
        neutral: {
          50: "#f8faff", 100: "#edf2fc", 200: "#e2e8f5", 300: "#c7d1e5",
          400: "#acb8d0", 500: "#96a4be", 600: "#8492ae", 700: "#4d5b76",
          800: "#283248", 900: "#151d2d", 950: "#0c101b",
        },
        accent: {
          50: "#f7f5ff", 100: "#eee8ff", 200: "#ded3ff", 300: "#c2afff",
          400: "#a98cf5", 500: "#9175ed", 600: "#7659d2", 700: "#6145af",
          800: "#4d3889", 900: "#372b63", 950: "#241c40",
        },
        domain: { code: "#bba7ff", pentest: "#e9abbc", osint: "#8ed8c6", design: "#c2afff" },
      },
      borderRadius: {
        DEFAULT: "0.75rem", sm: "0.5rem", md: "0.875rem", lg: "1.125rem",
        xl: "1.5rem", "2xl": "1.75rem", "3xl": "2rem",
      },
      boxShadow: {
        panel: "0 24px 80px -28px rgb(0 0 0 / 0.65), inset 0 1px 0 rgb(255 255 255 / 0.04)",
        floating: "0 16px 48px -18px rgb(0 0 0 / 0.65), inset 0 1px 0 rgb(255 255 255 / 0.06)",
      },
    },
  },
  plugins: [],
};
export default config;
