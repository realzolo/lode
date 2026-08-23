import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['class'],
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        // DESIGN.md brand tokens — raw colors consumed as `bg-ink`, `text-body`,
        // `border-hairline`, `text-link`, etc. Sit beside the shadcn HSL tokens
        // so both primitive components and hand-authored CSS share one palette.
        ink: 'var(--ink)',
        body: 'var(--body)',
        mute: 'var(--mute)',
        canvas: 'var(--canvas)',
        'canvas-soft': 'var(--canvas-soft)',
        'canvas-soft-2': 'var(--canvas-soft-2)',
        hairline: 'var(--hairline)',
        'hairline-strong': 'var(--hairline-strong)',
        link: 'var(--link)',
        'link-deep': 'var(--link-deep)',
        'link-bg-soft': 'var(--link-bg-soft)',
        warning: 'var(--warning)',
        'warning-soft': 'var(--warning-soft)',
        'warning-deep': 'var(--warning-deep)',
        violet: 'var(--violet)',
        cyan: 'var(--cyan)',
        'highlight-pink': 'var(--highlight-pink)',
      },
      borderRadius: {
        // DESIGN.md radius scale: 6px in-app controls, 8px marketing cards,
        // 12px overlays/large cards, 16px modals, 64px tab pills, 100px CTA pills.
        sm: '6px',
        md: '8px',
        lg: '12px',
        xl: '16px',
        '2xl': '16px',
        '3xl': '16px',
        'pill-sm': '64px',
        pill: '100px',
        full: '9999px',
      },
      boxShadow: {
        // Geist focus ring: 2px background-colored gap + 4px blue ring.
        // Blue lightens to blue-900 in dark mode for accessible contrast.
        'geist-focus': '0 0 0 2px var(--background-1), 0 0 0 4px var(--focus-blue)',
        // DESIGN.md stacked elevation — multiple small offsets + inset hairline
        // ring. Drop opacity rides `--shadow-color`, the crisp edge rides
        // `--hairline-ring`, so both adapt to light/dark automatically.
        'elevation-1': 'inset 0 0 0 1px var(--hairline-ring)',
        'elevation-2':
          '0 1px 1px rgba(0,0,0,0.02), 0 2px 2px var(--shadow-color), inset 0 0 0 1px var(--hairline-ring)',
        'elevation-3':
          '0 2px 2px var(--shadow-color), 0 8px 8px -8px var(--shadow-color), inset 0 0 0 1px var(--hairline-ring)',
        'elevation-4':
          '0 2px 2px var(--shadow-color), 0 8px 16px -4px var(--shadow-color), inset 0 0 0 1px var(--hairline-ring)',
        'elevation-5':
          '0 1px 1px rgba(0,0,0,0.02), 0 8px 16px -4px var(--shadow-color), 0 24px 32px -8px var(--shadow-color-modal), inset 0 0 0 1px var(--hairline-ring)',
      },
      fontFamily: {
        // Geist is loaded via `geist/font` in the locale layout and exposed as
        // --font-geist-sans / --font-geist-mono. Inter/JetBrains Mono are the
        // documented open-source fallbacks (DESIGN.md §Typography).
        sans: ['var(--font-geist-sans)', 'Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['var(--font-geist-mono)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};

export default config;
