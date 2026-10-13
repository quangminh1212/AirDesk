/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    screens: {
      xs: "360px",
      sm: "640px",
      md: "768px",
      lg: "1024px",
      xl: "1280px",
      "2xl": "1536px",
      "3xl": "1920px",
    },
    extend: {
      colors: {
        brand: {
          50: "#eef6ff",
          100: "#d9ecff",
          500: "#2f7fe0",
          600: "#1f66bd",
          700: "#18509a",
          900: "#0d2c57",
        },
        // XLab design tokens (CSS-variable backed, dark-mode aware)
        surface: {
          DEFAULT: "rgb(var(--surface) / <alpha-value>)",
          foreground: "rgb(var(--surface-foreground) / <alpha-value>)",
          muted: "rgb(var(--surface-muted) / <alpha-value>)",
          border: "rgb(var(--surface-border) / <alpha-value>)",
          elevated: "rgb(var(--surface-elevated) / <alpha-value>)",
        },
        xlab: {
          primary: "#00d4aa",
          accent: "#37c88f",
          info: "#2563eb",
          warning: "#f59e0b",
          danger: "#f85149",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "Segoe UI", "Roboto", "sans-serif"],
      },
      borderRadius: {
        sm: "0.125rem",
        DEFAULT: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
        "2xl": "1rem",
        "3xl": "1.5rem",
        full: "9999px",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0" },
          to: { opacity: "1" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.2s ease-out",
      },
    },
  },
  plugins: [],
};
