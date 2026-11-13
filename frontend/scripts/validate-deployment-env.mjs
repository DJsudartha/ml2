import { pathToFileURL } from "node:url";

export function validateDeploymentApiBase(value) {
  if (!value || value !== value.trim()) {
    throw new Error("Set VITE_API_BASE_URL to the confirmed production backend HTTPS origin.");
  }
  const url = new URL(value);
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash ||
      url.pathname !== "/" || hostname === "localhost" || hostname.endsWith(".localhost") ||
      hostname === "[::1]" || hostname === "[::]" || /^127\./.test(hostname) || hostname === "0.0.0.0") {
    throw new Error("VITE_API_BASE_URL must be a public HTTPS origin without credentials, path, query or fragment.");
  }
  return url.origin;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    validateDeploymentApiBase(process.env.VITE_API_BASE_URL);
    console.log("Deployment API origin validated.");
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
