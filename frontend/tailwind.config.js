/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        ink: { 900: '#0b1020', 800: '#111834', 700: '#1a2245', 600: '#252f5c' },
        accent: { DEFAULT: '#5b8cff', soft: '#8fb0ff' },
        good: '#34d399', warn: '#fbbf24', bad: '#f87171', mute: '#94a3b8',
      },
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
