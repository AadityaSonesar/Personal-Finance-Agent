import axios from "axios";
import { firebaseAuth } from "./firebase";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || "http://127.0.0.1:4000/api" });
api.interceptors.request.use(async (config) => {
  const user = firebaseAuth?.currentUser;
  if (user) config.headers.Authorization = `Bearer ${await user.getIdToken()}`;
  return config;
});
