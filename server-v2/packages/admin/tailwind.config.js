/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          DEFAULT: '#1554b5',
          dark: '#124a9f',
          50: '#f4f7fb',
          100: '#eef4ff',
          200: '#d8e6ff',
          300: '#b7cdea',
        },
        ink: '#16253a',
        muted: '#5b6b80',
        line: '#dde5ef',
        good: '#126845',
        warn: '#8a5209',
        bad: '#ae3232',
      },
      fontFamily: {
        sans: ['"Microsoft YaHei UI"', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'Consolas', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px rgba(22,37,58,.04), 0 10px 28px -20px rgba(22,37,58,.28)',
      },
    },
  },
  plugins: [],
};
