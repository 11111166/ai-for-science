import type { NextConfig } from "next";

const basePath = process.env.APP_BASE_PATH ?? "";
const deploymentId = process.env.DEPLOYMENT_VERSION;

const nextConfig: NextConfig = {
  basePath,
  ...(deploymentId ? { deploymentId } : {}),
  output: "standalone",
  transpilePackages: ["@drug-discovery/contracts"],
};

export default nextConfig;
