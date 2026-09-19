/** M6 Task 1: the login is a login.
 *
 * `App` is rendered rather than `AppRoutes`, and that is the point — the gate is in front of
 * the router, so the only way to assert "an unauthenticated visitor gets no data" is to
 * mount the thing a visitor actually loads.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { App } from "../App";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";

/** A token as the issuer returns it: three base64url segments, of which the browser only
 * ever reads the middle one. The signature is the services' business (§10.5). */
function segment(value: object): string {
  return btoa(JSON.stringify(value))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function devToken(subject: string, role: string): string {
  return `${segment({ alg: "RS256" })}.${segment({ sub: subject, role })}.signature`;
}

function jsonResponse(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function signInWith(user: string, password: string): void {
  fireEvent.change(screen.getByLabelText("User"), { target: { value: user } });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: password },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

test("an unauthenticated visitor gets the login screen and no data", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);

  expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  // Nothing the application answers with is mounted: no question box, no navigation, and
  // no request went out looking for any of it.
  expect(screen.queryByRole("button", { name: "Ask" })).not.toBeInTheDocument();
  expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  expect(fetchMock).not.toHaveBeenCalled();
});

test("the screen says this is a development issuer and that production is not", () => {
  // §10.5 and the milestone's own line: the dev issuer is a dev issuer and the screen says
  // so where the user can read it. Asserted on the rendered text rather than on a class
  // name, because what matters is that a person sees it.
  render(<App />);

  const notice = screen.getByRole("complementary");
  expect(notice).toHaveTextContent(/development issuer/i);
  expect(notice).toHaveTextContent(/nothing to register/i);
  expect(notice).toHaveTextContent(/production deployment/i);
});

test("credentials go to the issuer and what comes back opens the application", async () => {
  const token = devToken("operator-7", "user");
  const fetchMock = vi.fn((_input: string, _init?: RequestInit) =>
    Promise.resolve(
      jsonResponse(
        { access_token: token, token_type: "Bearer", expires_in: 3600 },
        200,
      ),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  render(<App />);
  signInWith("operator-7", "operator-development");

  // The application, and the identity it is running as — which is the fact §14 makes
  // decide what the reader can reach, so a login that did not show it would be half a login.
  expect(
    await screen.findByRole("button", { name: "Ask" }),
  ).toBeInTheDocument();
  expect(screen.getByText("operator-7")).toBeInTheDocument();
  expect(screen.getByText("user")).toBeInTheDocument();

  const call = fetchMock.mock.calls[0];
  if (call === undefined) throw new Error("the issuer was never called");
  expect(call[0]).toBe("/api/issuer/token");
  expect(JSON.parse(String(call[1]?.body))).toEqual({
    username: "operator-7",
    password: "operator-development",
  });

  // And it survives a reload, which is the whole reason it is stored at all.
  expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBe(token);
});

test("a refused sign-in says so in the issuer's words and opens nothing", async () => {
  // The issuer never says which half was wrong (§10.5, and its own tests assert it). The
  // screen must not improve on that by guessing.
  const detail = "these credentials are not valid";
  vi.stubGlobal("fetch", () => Promise.resolve(jsonResponse({ detail }, 401)));

  render(<App />);
  signInWith("operator-7", "the wrong password");

  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  expect(screen.queryByRole("button", { name: "Ask" })).not.toBeInTheDocument();
  expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
});

test("an unconfigured issuer is reported as itself, not as a bad password", async () => {
  // The two refusals mean different things to the person reading them: one says retype,
  // the other says nothing you type will work until somebody configures the stack.
  const detail =
    "this issuer has no signing key configured (ISSUER_PRIVATE_KEY)";
  vi.stubGlobal("fetch", () => Promise.resolve(jsonResponse({ detail }, 503)));

  render(<App />);
  signInWith("operator-7", "operator-development");

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "ISSUER_PRIVATE_KEY",
  );
});

test("signing out clears the token and puts the login screen back", async () => {
  window.localStorage.setItem(
    TOKEN_STORAGE_KEY,
    devToken("operator-7", "user"),
  );

  render(<App />);
  expect(screen.getByText("operator-7")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));

  await waitFor(() => {
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
  });
  // Cleared, not merely hidden: a token still in storage comes back on the next reload,
  // which is a sign-out that did not sign anybody out.
  expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  expect(screen.queryByRole("button", { name: "Ask" })).not.toBeInTheDocument();
});
