/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: true,
  },
  images: {
    unoptimized: true,
  },
  // Hide the Next.js "N / Issues" floating badge in the corner
  devIndicators: false,
}

export default nextConfig
