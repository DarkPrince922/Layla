/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // In dev, proxy /api and /ws to the backend so the frontend uses same-origin
  // paths (matching the Caddy prod routing).
  async rewrites() {
    const core = process.env.CORE_ORIGIN || "http://localhost:8000";
    return [
      { source: "/api/:path*", destination: `${core}/api/:path*` },
      { source: "/ws/:path*", destination: `${core}/ws/:path*` },
    ];
  },
};
export default nextConfig;
