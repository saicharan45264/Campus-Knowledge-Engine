const colors = require('tailwindcss/colors');

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Overriding our previous theme with lighter, reddish tones
        indigo: colors.rose,
        slate: colors.stone,
      }
    },
  },
  plugins: [],
}
