/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["172.17.160.1"],
  serverExternalPackages: ["@resvg/resvg-js"],
};

module.exports = nextConfig;
