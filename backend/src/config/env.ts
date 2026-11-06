import dotenv from "dotenv";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { z } from "zod";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
dotenv.config({ path: resolve(projectRoot, ".env"), override: true });

const schema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"), PORT: z.coerce.number().default(4000),
  MONGODB_URI: z.string().min(1), JWT_SECRET: z.string().min(32), INTERNAL_SERVICE_SECRET: z.string().min(32),
  FIREBASE_SERVICE_ACCOUNT_PATH: z.string().min(1),
  AI_SERVICE_URL: z.string().url().default("http://127.0.0.1:8000"), FRONTEND_URL: z.string().url().default("http://127.0.0.1:5173"),
  ACCESS_TOKEN_TTL: z.string().default("15m"), REFRESH_TOKEN_TTL: z.string().default("7d"), MAX_UPLOAD_MB: z.coerce.number().min(1).max(20).default(10)
});
export const env = schema.parse(process.env);
export { projectRoot };
