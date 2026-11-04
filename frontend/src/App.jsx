import { Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth.jsx";
import Login from "./pages/Login.jsx";
import ModulePage from "./pages/ModulePage.jsx";

// Abas do menu principal (CP1). Cada uma aponta para um endpoint de leitura
// que o backend já protege; o conteúdo real vem nos próximos checkpoints.
export const TABS = [
  { path: "/visao-geral", label: "Visão Geral", endpoint: "/metrics", cp: "CP2" },
  { path: "/servicos", label: "Serviços", endpoint: "/services", cp: "CP2" },
  { path: "/arquivos", label: "Arquivos", endpoint: "/files", cp: "CP3" },
  { path: "/email", label: "E-mail", endpoint: "/email/inbox", cp: "CP4" },
  { path: "/ssh", label: "SSH", endpoint: "/ssh/info", cp: "CP5" },
  { path: "/logs", label: "Logs", endpoint: "/logs", cp: "CP6" },
];

function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="app">
      <header className="topbar">
        <strong>Console N2</strong>
        <nav>
          {TABS.map((t) => (
            <NavLink key={t.path} to={t.path} className={({ isActive }) => (isActive ? "active" : "")}>
              {t.label}
            </NavLink>
          ))}
        </nav>
        <div className="user">
          <span>
            {user.username} <em className={`badge ${user.role}`}>{user.role}</em>
          </span>
          <button onClick={logout}>Sair</button>
        </div>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <p className="center">Carregando…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <Layout />;
}

export default function App() {
  const { user } = useAuth();
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/visao-geral" replace /> : <Login />} />
      <Route element={<Protected />}>
        {TABS.map((t) => (
          <Route key={t.path} path={t.path} element={<ModulePage tab={t} />} />
        ))}
      </Route>
      <Route path="*" element={<Navigate to="/visao-geral" replace />} />
    </Routes>
  );
}
