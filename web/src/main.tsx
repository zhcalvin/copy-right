import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { I18nProvider } from "./i18n";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing");

createRoot(root).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
);
