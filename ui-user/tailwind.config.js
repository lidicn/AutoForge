/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  theme: {
    extend: {
      colors: {
        forge: {
          50: '#FFF7ED',
          100: '#FFEDD5',
          200: '#FED7AA',
          300: '#FDBA74',
          400: '#FB923C',
          500: '#F59E0B',
          600: '#D97706',
          700: '#B45309',
        },
        ink: {
          DEFAULT: '#1F2937',
          soft: '#6B7280',
          faint: '#9CA3AF',
        },
        surface: '#FFFFFF',
        canvas: '#FAFAFA',
      },
      boxShadow: {
        card: '0 1px 2px rgba(16,24,40,0.06), 0 4px 16px rgba(16,24,40,0.06)',
        glow: '0 0 0 4px rgba(245,158,11,0.15)',
      },
      borderRadius: {
        xl: '16px',
        '2xl': '22px',
      },
      keyframes: {
        'pop-in': {
          '0%': { transform: 'scale(0.92)', opacity: '0' },
          '100%': { transform: 'scale(1)', opacity: '1' },
        },
        breathe: {
          '0%,100%': { boxShadow: '0 0 0 3px rgba(245,158,11,0.25)' },
          '50%': { boxShadow: '0 0 0 8px rgba(245,158,11,0.06)' },
        },
        'digit-in': {
          '0%': { transform: 'translateY(8px) rotateX(-40deg)', opacity: '0' },
          '100%': { transform: 'translateY(0) rotateX(0)', opacity: '1' },
        },
        'pulse-red': {
          '0%,100%': { boxShadow: '0 0 0 0 rgba(239,68,68,0.0)' },
          '50%': { boxShadow: '0 0 0 6px rgba(239,68,68,0.18)' },
        },
      },
      animation: {
        'pop-in': 'pop-in 0.22s ease-out',
        breathe: 'breathe 2.4s ease-in-out infinite',
        'digit-in': 'digit-in 0.3s ease-out',
        'pulse-red': 'pulse-red 1.6s ease-in-out infinite',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
