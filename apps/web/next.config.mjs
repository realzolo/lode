import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./i18n/request.ts');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Produce a self-contained server (`.next/standalone`) for a lean Docker image
  // that does not need the full node_modules at runtime.
  output: 'standalone',
};

export default withNextIntl(nextConfig);
