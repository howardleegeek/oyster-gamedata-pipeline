import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        // Oyster brand palette — deep teal + amber accent
        oyster: {
          50: '#eef9f8',
          100: '#d6f0ed',
          200: '#aee0db',
          300: '#7eccc6',
          400: '#4cb4ad',
          500: '#2f9c95',
          600: '#207e78',
          700: '#1a635f',
          800: '#174d4a',
          900: '#103735',
          950: '#082020',
        },
        amber: {
          accent: '#f5b13a',
        },
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'Helvetica Neue', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};

export default config;
