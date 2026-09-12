import type { NextConfig } from "next";

const API = "http://127.0.0.1:8099";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // Экспорт эти правила игнорирует: они нужны только `next dev`, чтобы
  // браузер видел API и аудио на том же origin и сам слал Basic-заголовок.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/audio/:path*", destination: `${API}/audio/:path*` },
    ];
  },
};

export default nextConfig;
