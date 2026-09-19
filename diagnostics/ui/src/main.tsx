import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
// Tokens first, and both imported here rather than one `@import`-ing the other: the order
// is the cascade, and a stylesheet that silently depends on being loaded second is a
// dependency nothing checks.
import "./design/tokens.css";
import "./styles.css";

const root = document.getElementById("root");
if (root === null) throw new Error("index.html has no #root");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
