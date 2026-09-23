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
          950: "#16131b",
          900: "#1d1923",
          800: "#26202d",
          700: "#382f41",
          600: "#4a3e55",
        },
        neutral: {
          50: "#fcf9fd", 100: "#f7f0fa", 200: "#ede4f2", 300: "#d7cadd",
          400: "#bcaec5", 500: "#a99aB3", 600: "#94859f", 700: "#51455b",
          800: "#302737", 900: "#211b28", 950: "#16131b",
        },
        accent: {
          50: "#fbf5ff", 100: "#f4e6fd", 200: "#e6caf5", 300: "#d0a4eb",
          400: "#c18ada", 500: "#ac70cb", 600: "#9252b1", 700: "#784291",
          800: "#613775", 900: "#452852", 950: "#2d1936",
        },
        domain: { code: "#b9a1ed", pentest: "#dc9baf", osint: "#95cbbb", design: "#d0a4eb" },
      },
      borderRadius: {
        DEFAULT: "0.75rem", sm: "0.5rem", md: "0.875rem", lg: "1.125rem",
        xl: "1.5rem", "2xl": "1.75rem", "3xl": "2rem",
      },
      boxShadow: {
        panel: "0 16px 48px -24px rgb(0 0 0 / 0.45)",
        floating: "0 12px 36px -16px rgb(0 0 0 / 0.65)",
      },
    },
  },
  plugins: [],
};
export default config;
