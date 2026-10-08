const { withGTConfig } = require("gt-next/config");

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  reactCompiler: true,
  output: "export",
  basePath: "/bohun-the-field",
  images: { unoptimized: true },
};

module.exports = withGTConfig(nextConfig);
