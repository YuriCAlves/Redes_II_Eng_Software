import { Navigate, NavLink, Outlet, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth.jsx";
import { CommBadge, OperationsPanel, Toasts } from "./notify.jsx";
import Login from "./pages/Login.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Services from "./pages/Services.jsx";
import Files from "./pages/Files.jsx";
import Email from "./pages/Email.jsx";
import Ssh from "./pages/Ssh.jsx";
import Logs from "./pages/Logs.jsx";
import Dns from "./pages/Dns.jsx";
import Dhcp from "./pages/Dhcp.jsx";
import Audit from "./pages/Audit.jsx";

export const TABS = [
  { path: "/visao-geral", label: "Visão Geral", element: <Dashboard /> },
  { path: "/servicos", label: "Serviços", element: <Services /> },
  { path: "/arquivos", label: "Arquivos", element: <Files /> },
  { path: "/email", label: "E-mail", element: <Email /> },
  { path: "/ssh", label: "SSH", element: <Ssh /> },
  { path: "/dns", label: "DNS", element: <Dns /> },
  { path: "/dhcp", label: "DHCP", element: <Dhcp /> },
  { path: "/logs", label: "Logs", element: <Logs /> },
  { path: "/auditoria", label: "Auditoria", element: <Audit /> },
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
        <CommBadge />
        <div className="user">
          <span>
            {user.username} <em className={`badge ${user.role}`}>{user.role}</em>
          </span>
          <button onClick={logout}>Sair</button>
        </div>
      </header>
      <main>
        <Outlet />
        <OperationsPanel />
      </main>
      <Toasts />
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
          <Route key={t.path} path={t.path} element={t.element} />
        ))}
      </Route>
      <Route path="*" element={<Navigate to="/visao-geral" replace />} />
    </Routes>
  );
}
