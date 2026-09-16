/** M5 Task 6: the token reaches every request, and a refusal reads as a refusal rather
 * than an empty answer. */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { AuthProvider } from "../AuthContext";
import { Chat } from "../Chat";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";

function signedIn(token: string): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("a request carries the token when one is set", async () => {
  signedIn("dev-token-123");
  const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
    Promise.resolve(
      new Response("", { headers: { "Content-Type": "text/event-stream" } }),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(
    <AuthProvider>
      <Chat />
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalled();
  });
  const call = fetchMock.mock.calls[0];
  if (call === undefined) throw new Error("fetch was not called");
  const headers = new Headers(call[1]?.headers);
  expect(headers.get("Authorization")).toBe("Bearer dev-token-123");
});

test("a 401 renders as not signed in, not as an empty answer", async () => {
  // A token was set and sent -- this is the server refusing it (expired, wrong audience,
  // wrong key), not the "nothing set" case below. Both are told to the reader the same
  // way, because both mean the same thing: the request needs a good token.
  signedIn("dev-token-123");
  vi.stubGlobal("fetch", () =>
    Promise.resolve(jsonResponse({ detail: "authentication required" }, 401)),
  );

  render(
    <AuthProvider>
      <Chat />
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/not signed in/i);
});

test("a 403 names the role the action needed", async () => {
  signedIn("dev-token-123");
  const detail =
    "this action requires the admin role; this token carries 'user'";
  vi.stubGlobal("fetch", () => Promise.resolve(jsonResponse({ detail }, 403)));

  render(
    <AuthProvider>
      <Chat />
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
});

test("with no token set, the UI refuses before it calls", async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  render(
    <AuthProvider>
      <Chat />
    </AuthProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/not signed in/i);
  // The point of the check above happening first: a client that already knows a request
  // will be refused should not spend a round trip to be told so.
  expect(fetchMock).not.toHaveBeenCalled();
});
