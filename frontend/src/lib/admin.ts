import { useState, useEffect } from "react";

// Extremely simple frontend-only auth state
let isAuthenticated = false;
const listeners: (() => void)[] = [];

function notify() {
  listeners.forEach(l => l());
}

export function useAdminAuth() {
  const [auth, setAuth] = useState(isAuthenticated);

  useEffect(() => {
    const listener = () => setAuth(isAuthenticated);
    listeners.push(listener);
    return () => {
      const idx = listeners.indexOf(listener);
      if (idx > -1) listeners.splice(idx, 1);
    };
  }, []);

  return {
    isAuthenticated: auth,
    login: () => {
      isAuthenticated = true;
      notify();
    },
    logout: () => {
      isAuthenticated = false;
      notify();
    }
  };
}
