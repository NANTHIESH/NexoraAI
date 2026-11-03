import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, clearSession, loadSession, saveSession } from "./api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [session, setSession] = useState(loadSession);

  const start = useCallback((body) => {
    saveSession(body);
    setSession(body);
  }, []);

  const login = useCallback(async (identifier, password) => {
    start(await api("POST", "/login", { employee_id: identifier, password }));
  }, [start]);

  const register = useCallback(async (name, email, password) => {
    start(await api("POST", "/register", { name, email, password }));
  }, [start]);

  const logout = useCallback(() => {
    clearSession();
    setSession(null);
  }, []);

  // The API client fires this when a token expires.
  useEffect(() => {
    window.addEventListener("nexora:logout", logout);
    return () => window.removeEventListener("nexora:logout", logout);
  }, [logout]);

  const value = useMemo(
    () => ({ user: session?.user ?? null, login, register, logout }),
    [session, login, register, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
