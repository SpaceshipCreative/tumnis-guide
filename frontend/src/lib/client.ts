// The generated client, configured once (P0-22): same-origin requests with the session
// cookie, and a 401 anywhere sends the visitor to /login.
import { client } from "../api/client.gen";
import { onUnauthorized } from "./fetch";

let configured = false;

export function configureClient(): void {
  client.setConfig({
    baseUrl: window.location.origin,
    credentials: "same-origin",
  });
  if (configured) return;
  configured = true;
  client.interceptors.response.use((response) => {
    if (response.status === 401) onUnauthorized();
    return response;
  });
}
