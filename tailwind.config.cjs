/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./src/**/*.{svelte,ts,js}'],
  theme: {
    extend: {
      borderRadius: { '2xl': '1rem' },
      boxShadow: { soft: '0 2px 12px rgba(0,0,0,0.35)' },
      fontFamily: { sans: ['Inter', 'system-ui', 'ui-sans-serif', 'sans-serif'] }
    }
  },
  plugins: [require('@tailwindcss/forms'), require('@tailwindcss/typography')]
};

