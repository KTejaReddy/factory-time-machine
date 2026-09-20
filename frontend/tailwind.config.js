/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        background: '#F0F4F8', // very light blue background
        surface: '#FFFFFF',
        surfaceMuted: '#F8FAFC',
        textMain: '#1E293B', // dark slate
        textMuted: '#64748B',
        primary: '#3B82F6', // clean medium blue
        primaryHover: '#2563EB',
        border: '#E2E8F0', // very light gray border
        danger: '#EF4444', // muted red
        warning: '#F59E0B', // muted amber
        success: '#10B981', // soft green
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['Fira Code', 'monospace'],
      },
      boxShadow: {
        'card': '0 1px 3px 0 rgba(0, 0, 0, 0.1), 0 1px 2px 0 rgba(0, 0, 0, 0.06)',
      },
    },
  },
  plugins: [],
}
