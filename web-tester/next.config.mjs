/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The /api/upload-tarball route accepts large multipart bodies (recording tarballs).
  // Next 14 caps body parser at 1 MB by default for route handlers; we override below.
  experimental: {
    serverActions: {
      bodySizeLimit: '500mb',
    },
  },
  // Suppress the build error that fires when env vars are missing during `next build`
  // in CI / Vercel preview without secrets — we degrade gracefully at runtime instead.
  env: {
    NEXT_PUBLIC_DEV_FALLBACK: process.env.NEXT_PUBLIC_DEV_FALLBACK ?? 'true',
  },
};

export default nextConfig;
