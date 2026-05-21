import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";
// The @hawcx/react SDK ships an opinionated stylesheet for HawcxSignUpSignIn
// and its sub-components. Replace with your own if you want a different look.
import "@hawcx/react/styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
