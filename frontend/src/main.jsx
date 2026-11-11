import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { AuthProvider } from "./auth.jsx";
import { NotifyProvider } from "./notify.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <NotifyProvider>
          <App />
        </NotifyProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>
);
