import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./i18n/request.ts');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `scripts/dev.mjs` gives every local port its own output directory. This
  // prevents concurrent dev servers from corrupting one another's chunks.
  ...(process.env.LODE_NEXT_DIST_DIR ? { distDir: process.env.LODE_NEXT_DIST_DIR } : {}),
  typescript: {
    tsconfigPath: process.env.LODE_NEXT_TSCONFIG ?? 'tsconfig.json',
  },
  // Produce a self-contained server (`.next/standalone`) for a lean Docker image
  // that does not need the full node_modules at runtime.
  output: 'standalone',
};

export default withNextIntl(nextConfig);
