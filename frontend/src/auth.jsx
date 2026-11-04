import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api } from "./api.js";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sessionExpired, setSessionExpired] = useState(false);

  useEffect(() => {
    api("/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (username, password) => {
    const u = await api("/auth/login", { method: "POST", body: { username, password } });
    setSessionExpired(false);
    setUser(u);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
    }
  }, []);

  // Chamado quando qualquer requisição volta 401 com usuário logado
  const expire = useCallback(() => {
    setUser(null);
    setSessionExpired(true);
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, expire, sessionExpired }}>
      {children}
    </AuthContext.Provider>
  );
}

// Perfis, do menor para o maior privilégio (espelha o backend; só afeta a UI)
const ORDER = ["leitor", "operador", "admin"];
export const canAtLeast = (user, minimum) =>
  !!user && ORDER.indexOf(user.role) >= ORDER.indexOf(minimum);
