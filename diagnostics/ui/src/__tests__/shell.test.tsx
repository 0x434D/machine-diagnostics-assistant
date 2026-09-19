/** The shell: one application, several views, and the things that must not change as the
 * reader moves between them. */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test } from "vitest";
import { MemoryRouter } from "react-router";

import { AppRoutes } from "../App";
import { AuthProvider } from "../AuthContext";
import { TOKEN_STORAGE_KEY } from "../tokenStorage";

/** A token from the development issuer, as the browser receives it: three base64url
 * segments, of which only the middle one is ever read here. Signed with nothing — the
 * signature is the services' business (§10.5) and this is what the shell displays. */
function segment(value: object): string {
  return btoa(JSON.stringify(value))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function devToken(subject: string, role: string): string {
  return `${segment({ alg: "RS256" })}.${segment({ sub: subject, role })}.signature`;
}

function app(at: string) {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[at]}>
        <AppRoutes />
      </MemoryRouter>
    </AuthProvider>,
  );
}

afterEach(() => {
  window.localStorage.clear();
});

test("the chat is the application's front door", () => {
  app("/");
  expect(screen.getByRole("button", { name: "Ask" })).toBeInTheDocument();
});

test("every view in the navigation leads somewhere", () => {
  // The failure this stops is a menu and a route table drifting apart: a link to an
  // address nothing answers at is invisible until someone clicks it.
  app("/");
  const links = screen.getAllByRole("link");
  const views = links.filter((link) => link.getAttribute("href") !== "#view");
  expect(views.length).toBeGreaterThan(1);

  for (const link of views) {
    fireEvent.click(link);
    expect(screen.queryByText("No such view")).not.toBeInTheDocument();
  }
});

test("the signed-in identity survives a move between views", () => {
  // The shell holds the identity, so moving between views must not re-mount it. If the
  // header re-read the token per view this would still pass; what it would not survive is
  // a view that owns the identity instead, which is the design mistake worth pinning.
  window.localStorage.setItem(
    TOKEN_STORAGE_KEY,
    devToken("operator-7", "user"),
  );
  app("/");

  expect(screen.getByText("operator-7")).toBeInTheDocument();
  expect(screen.getByText("user")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("link", { name: "Containment" }));

  // By role: the word is now on the page twice, on the link that brought us here and on
  // the heading of the view it opened.
  expect(
    screen.getByRole("heading", { name: "Containment" }),
  ).toBeInTheDocument();
  expect(screen.getByText("operator-7")).toBeInTheDocument();
  expect(screen.getByText("user")).toBeInTheDocument();
});

test("a token the browser cannot read says so rather than showing a name", () => {
  // §10.5's refusal comes from the services, but a token that is not a JWT at all is
  // something the shell can see for itself -- and "identity unreadable" is a different
  // and more useful fact than the 401 that follows.
  window.localStorage.setItem(TOKEN_STORAGE_KEY, "not-a-jwt");
  app("/");
  expect(screen.getByText("identity unreadable")).toBeInTheDocument();
});

test("a deep link into a part carries its serial", () => {
  // §7.2: a citation you cannot open is barely a citation, and a citation opens by being
  // a link someone can send. The view behind this is M6 Task 7's; the address is not.
  app("/parts/A-00000007");
  expect(screen.getByText("A-00000007")).toBeInTheDocument();
});

test("an address with no view says there is no view, not that there is no data", () => {
  // §6.5 depends on the difference between "no such thing" and "nothing to show", and the
  // router is the first place that distinction can be lost.
  app("/no-such-place");
  expect(screen.getByText("No such view")).toBeInTheDocument();
});

test("the plant's status stands over every view, not only over the answer", () => {
  // §7.2's reading — *is what I am reading current?* — qualifies a stop timeline and a
  // containment set exactly as much as it qualifies an answer, and it lived on the Ask view
  // alone until this. Signed out on purpose: the banner then says it has not asked rather
  // than reaching the gateway, which is the reading, and where it is rendered is the claim.
  for (const at of ["/", "/timeline", "/containment"]) {
    const { unmount } = app(at);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Plant status not read",
    );
    unmount();
  }
});

test("the key to the colour language is reachable from every view", () => {
  for (const at of ["/", "/timeline", "/containment"]) {
    const { unmount } = app(at);
    expect(screen.getByText("How the line is coloured")).toBeInTheDocument();
    unmount();
  }
});
