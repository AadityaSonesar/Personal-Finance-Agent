import axios from "axios";
import { firebaseAuth } from "./firebase";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL || "http://127.0.0.1:4000/api" });
api.interceptors.request.use(async (config) => {
  const user = firebaseAuth?.currentUser;
  if (user) config.headers.Authorization = `Bearer ${await user.getIdToken()}`;
  return config;
});
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (!error.response) {
      const base = api.defaults.baseURL;
      return Promise.reject(new Error(`Cannot reach the Aurelia API at ${base}. Confirm the backend is running and accepts requests from ${window.location.origin}.`));
    }
    return Promise.reject(error);
  },
);
