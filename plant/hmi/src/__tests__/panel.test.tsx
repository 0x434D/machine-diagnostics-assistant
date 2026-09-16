import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlarmList } from "../AlarmList";
import { InjectionPanel } from "../InjectionPanel";
import { FAULT_TOKEN_HEADER } from "../plantApi";
import type { AlarmView } from "../snapshot";

/** §3.5's seven kinds as `GET /faults` serves them. Two of them, because what this file
 * asserts is that the form is built from whatever the plant sends — a fixture holding all
 * seven would make "it rendered the parameters it was given" indistinguishable from "it
 * rendered the parameters it already knew". */
const KINDS = [
  {
    kind: "joining_force_drift",
    parameters: ["newtons", "ramp_seconds"],
    scope: null,
    scope_required: false,
  },
  {
    kind: "carrier_wear",
    parameters: ["factor", "carrier", "ramp_seconds"],
    scope: "carrier",
    scope_required: true,
  },
];

const ALARM: AlarmView = {
  sequence: 4,
  station_browse_name: "S2_Joining",
  code: "A-207",
  text: "joining force out of tolerance",
  severity: 700,
  raised_at: "2026-09-13T06:09:12+00:00",
};

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** Records what the screen asked the plant for, and answers it.
 *
 * The request is the assertion: the panel's whole contract with the simulator is the
 * method, the path and the body, and a test that stubbed the module instead would assert
 * that a function was called rather than that a fault was injected.
 */
function stubPlant(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    (input: string, init?: RequestInit): Promise<Response> => {
      const url = new URL(input).pathname;
      calls.push({
        url,
        method: init?.method ?? "GET",
        body:
          typeof init?.body === "string"
            ? (JSON.parse(init.body) as unknown)
            : null,
      });
      const answer = answers[url];
      return Promise.resolve(
        new Response(answer === undefined ? "" : JSON.stringify(answer), {
          status: answer === undefined ? 204 : 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    },
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the injection panel", () => {
  it("offers exactly the kinds and parameters the plant sent", async () => {
    stubPlant({ "/api/plant/faults": KINDS });
    render(<InjectionPanel />);

    // The vocabulary is §3.5's and lives in `simulator.faults`. A panel with its own copy
    // offers a parameter `Fault.__post_init__` refuses, and the operator finds out by
    // pressing the button.
    await waitFor(() => {
      expect(screen.getByLabelText(/fault/)).toBeInTheDocument();
    });
    expect(
      [...screen.getByRole("combobox").querySelectorAll("option")].map(
        (option) => option.value,
      ),
    ).toEqual(["joining_force_drift", "carrier_wear"]);
    expect(screen.getByLabelText(/newtons/)).toBeInTheDocument();
    expect(screen.getByLabelText(/ramp_seconds/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/factor/)).toBeNull();
  });

  it("sends what was typed, and omits the boxes that were left empty", async () => {
    const calls = stubPlant({
      "/api/plant/faults": KINDS,
    });
    render(<InjectionPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText(/newtons/)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/newtons/), {
      target: { value: "-420" },
    });
    fireEvent.click(screen.getByRole("button", { name: "inject" }));

    await waitFor(() => {
      expect(calls).toHaveLength(2);
    });
    // The GET that fetched the vocabulary, then the POST that injected. `ramp_seconds`
    // was left empty and is absent rather than 0: omitted means a step, and a zero sent
    // for every untouched box would make a scope-carrying kind a fault aimed at carrier 0.
    expect(calls).toEqual([
      { url: "/api/plant/faults", method: "GET", body: null },
      {
        url: "/api/plant/faults",
        method: "POST",
        body: {
          kind: "joining_force_drift",
          params: { newtons: -420 },
          duration_seconds: null,
        },
      },
    ]);
  });

  it("shows the plant's own reason when the plant refuses", async () => {
    const refusal = "carrier_wear needs a 'carrier' parameter";
    vi.stubGlobal(
      "fetch",
      (_input: string, init?: RequestInit): Promise<Response> =>
        Promise.resolve(
          init?.method === "POST"
            ? new Response(refusal, { status: 400 })
            : new Response(JSON.stringify(KINDS), { status: 200 }),
        ),
    );
    render(<InjectionPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText(/newtons/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "inject" }));

    // The status carries the plant's text. Swallowing it would leave a button that does
    // nothing, which is the one outcome a demo console cannot have.
    await waitFor(() => {
      expect(screen.getByText(new RegExp(refusal))).toBeInTheDocument();
    });
  });

  it("sends whatever was typed into the token box, on its own header", async () => {
    // A bespoke stub rather than `stubPlant`: that helper's `Call` records `{ url,
    // method, body }`, and every existing test asserts equality against exactly that
    // shape -- adding a `headers` field there would break them for a header only this
    // test needs to see.
    const headers: Headers[] = [];
    vi.stubGlobal(
      "fetch",
      (_input: string, init?: RequestInit): Promise<Response> => {
        if (init?.method === "POST") headers.push(new Headers(init.headers));
        return Promise.resolve(
          init?.method === "POST"
            ? new Response(
                JSON.stringify({
                  kind: "joining_force_drift",
                  at: "t",
                  until: null,
                  params: {},
                }),
                {
                  status: 201,
                  headers: { "Content-Type": "application/json" },
                },
              )
            : new Response(JSON.stringify(KINDS), { status: 200 }),
        );
      },
    );
    render(<InjectionPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText(/token/)).toBeInTheDocument();
    });

    fireEvent.change(screen.getByLabelText(/token/), {
      target: { value: "s3cr3t" },
    });
    fireEvent.click(screen.getByRole("button", { name: "inject" }));

    await waitFor(() => {
      expect(headers).toHaveLength(1);
    });
    // Not part of the JSON body a ground-truth record is built from, so it cannot end
    // up in that log by accident.
    expect(headers[0]?.get(FAULT_TOKEN_HEADER)).toBe("s3cr3t");
  });

  it("shows the plant's refusal when the token gate rejects the request", async () => {
    const refusal = "fault injection needs the plant's shared token";
    vi.stubGlobal(
      "fetch",
      (_input: string, init?: RequestInit): Promise<Response> =>
        Promise.resolve(
          init?.method === "POST"
            ? new Response(refusal, { status: 401 })
            : new Response(JSON.stringify(KINDS), { status: 200 }),
        ),
    );
    render(<InjectionPanel />);
    await waitFor(() => {
      expect(screen.getByLabelText(/newtons/)).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "inject" }));

    // A 401 is refused the same visible way a 400 is: the operator sees why, not a
    // button that quietly did nothing.
    await waitFor(() => {
      expect(screen.getByText(new RegExp(refusal))).toBeInTheDocument();
    });
  });
});

describe("the acknowledge button", () => {
  it("posts the alarm's own sequence", async () => {
    const calls = stubPlant({});
    render(<AlarmList alarms={[ALARM]} />);

    fireEvent.click(screen.getByRole("button", { name: "acknowledge" }));

    // The plant's sequence, not the row's index: §5.2's `alarms.id` is the gateway's and
    // this process never sees it, so two numbers for one alarm would be an
    // acknowledgement addressed to whichever the screen happened to hold.
    await waitFor(() => {
      expect(calls).toEqual([
        {
          url: "/api/plant/alarms/4/acknowledge",
          method: "POST",
          body: null,
        },
      ]);
    });
  });
});
