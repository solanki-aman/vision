import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ThemeProvider } from "./ThemeContext";
import "./styles.css";
import { forwardRenderToken } from "./renderToken";

// The shooter service loads this app headlessly to produce PNG/PDF exports and has
// no session, so the server hands it a short-lived token in the URL. Install the
// forwarder before anything renders, so the very first fetch carries it.
forwardRenderToken();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
);
