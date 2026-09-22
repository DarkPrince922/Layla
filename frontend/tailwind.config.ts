import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '"Manrope Variable"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
      },
      colors: {
        // Neutral, dark-first workstation palette.
        ink: {
          950: "#0a0a0b",
          900: "#111114",
          800: "#17171b",
          700: "#1f1f25",
          600: "#2a2a32",
        },
        domain: {
          code: "#3b82f6",
          pentest: "#ef4444",
          osint: "#06b6d4",
          design: "#ec4899",
        },
      },
    },
  },
  plugins: [],
};
export default config;
