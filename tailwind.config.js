/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class', // we’ll force dark via layout wrapper
  content: ['./src/**/*.{html,js,svelte,ts}'],
  theme: {
    extend: {
      borderRadius: { '2xl': '1rem' },
      boxShadow: { soft: '0 2px 12px rgba(0,0,0,0.35)' },
      fontFamily: { sans: ['Inter', 'system-ui', 'ui-sans-serif', 'sans-serif'] }
    }
  },
  plugins: [require('@tailwindcss/forms'), require('@tailwindcss/typography')]
};

