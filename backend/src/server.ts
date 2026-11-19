import mongoose from "mongoose";
import { app } from "./app.js";
import { env } from "./config/env.js";

const reconnectDelayMs = 5_000;

async function connectDatabase(): Promise<void> {
  try {
    await mongoose.connect(env.MONGODB_URI, { serverSelectionTimeoutMS: 5_000 });
    console.log("MongoDB connected");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown MongoDB connection error";
    console.error(`MongoDB unavailable: ${message}. Retrying in ${reconnectDelayMs / 1000}s.`);
    setTimeout(() => void connectDatabase(), reconnectDelayMs);
  }
}

app.listen(env.PORT, () => {
  console.log(`API listening on ${env.PORT}`);
  void connectDatabase();
});
