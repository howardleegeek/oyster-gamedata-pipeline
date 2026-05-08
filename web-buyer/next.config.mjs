/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Stripe webhook handler reads the raw request body — keep server actions
  // small here, the handler does its own buffering. We mirror the tester app
  // limit for parity.
  experimental: {
    serverActions: {
      bodySizeLimit: '10mb',
    },
  },
  // Suppress the build error that fires when env vars are missing during
  // `next build` in CI / Vercel preview without secrets — we degrade
  // gracefully at runtime instead.
  env: {
    NEXT_PUBLIC_DEV_FALLBACK: process.env.NEXT_PUBLIC_DEV_FALLBACK ?? 'true',
  },
};

export default nextConfig;
