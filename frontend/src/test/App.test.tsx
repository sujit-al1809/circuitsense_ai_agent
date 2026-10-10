import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { Markdown } from "../components/Markdown";
import fixtures from "./fixtures.json";
import type { CircuitSession } from "../lib/types";

// Fixtures are real responses captured from the backend in demo mode
// (see README → "Frontend test fixtures").

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function mockFetch(overrides: Record<string, Handler> = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const defaults: Record<string, Handler> = {
    "GET /health": () => jsonResponse(fixtures.health),
    "GET /api/samples": () => jsonResponse(fixtures.samples),
    "POST /api/samples/led_basic/load": () => jsonResponse(fixtures.session, 201),
    "POST /trace": () => jsonResponse(fixtures.traceR1),
    "POST /api/chat": (_url, init) => {
      const body = JSON.parse(String(init?.body));
      return jsonResponse(body.focus_component ? fixtures.chatFocus : fixtures.chatDebug);
    },
  };
  const handlers = { ...defaults, ...overrides };
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, init });
    const key = Object.keys(handlers).find((k) => {
      const [m, path] = k.split(" ");
      return m === method && (url === path || url.endsWith(path));
    });
    if (!key) return jsonResponse({ error: { code: "not_found", message: `unmocked ${method} ${url}` } }, 404);
    return handlers[key](url, init);
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

beforeEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("CircuitSense workspace", () => {
  it("shows a loading indicator, then the welcome screen with samples and provider mode", async () => {
    let release: () => void = () => {};
    mockFetch({
      "GET /health": () => new Promise<Response>((resolve) => (release = () => resolve(jsonResponse(fixtures.health)))),
    });
    render(<App />);
    expect(screen.getByRole("banner").querySelector(".animate-spin")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: /understand, explain and debug circuits/i })).toBeInTheDocument();
    expect(await screen.findAllByText("LED indicator (5 V, 330 Ω, red LED)")).not.toHaveLength(0);
    release();
    expect(await screen.findByText("Demo mode")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /calculate current through the led/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/ask a question about the circuit/i)).toBeDisabled();
  });

  it("shows an error state when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    render(<App />);
    expect(await screen.findByText("API offline")).toBeInTheDocument();
  });

  it("shows upload errors returned by the API", async () => {
    mockFetch({
      "POST /api/circuits/analyze": () =>
        jsonResponse({ error: { code: "unsupported_media_type", message: "Unsupported file type. Upload a PNG, JPEG or WebP schematic image, a PDF, or a KiCad netlist (.net)." } }, 415),
    });
    const user = userEvent.setup({ applyAccept: false });
    render(<App />);
    const input = screen.getByTestId("file-input") as HTMLInputElement;
    await user.upload(input, new File(["MZ"], "tool.exe", { type: "application/octet-stream" }));
    await user.click(screen.getByRole("button", { name: /analyze circuit/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Unsupported file type");
  });

  it("selects a component, shows verified connections, and keeps it as chat context", async () => {
    const calls = mockFetch();
    const user = userEvent.setup();
    render(<App />);
    const sampleButtons = await screen.findAllByRole("button", { name: /LED indicator \(5 V, 330 Ω, red LED\)/ });
    await user.click(sampleButtons[0]);

    const r1 = await screen.findByTestId("component-R1");
    expect(r1).toHaveAttribute("aria-pressed", "false");
    await user.click(r1);
    expect(r1).toHaveAttribute("aria-pressed", "true");

    const focus = await screen.findByTestId("focus-panel");
    expect(within(focus).getByText(/current-limiting resistor for D1/)).toBeInTheDocument();
    expect(await within(focus).findByText("verified")).toBeInTheDocument();
    expect(within(focus).getByText("LED_A")).toBeInTheDocument();
    expect(screen.getByText(/Focused on R1/)).toBeInTheDocument();

    // Overlay on the schematic reflects the same selection.
    expect(screen.getByRole("button", { name: "Select R1 (330)" })).toHaveAttribute("aria-pressed", "true");

    await user.click(within(focus).getByRole("button", { name: "Why is R1 needed?" }));
    const message = await screen.findByTestId("assistant-message");
    const chatCall = calls.find((c) => c.url.endsWith("/api/chat"));
    expect(JSON.parse(String(chatCall?.init?.body))).toMatchObject({ focus_component: "R1", message: "Why is R1 needed?" });
    expect(within(message).getByText("Confirmed")).toBeInTheDocument();
    expect(within(message).getByText(/Demo mode · rule-based/)).toBeInTheDocument();
    expect(within(message).getByRole("button", { name: /Datasheet sources/ })).toBeInTheDocument();
  });

  it("renders hypotheses separately from the next diagnostic step", async () => {
    mockFetch();
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /LED indicator \(5 V, 330 Ω, red LED\)/ }))[0]);
    await user.type(await screen.findByLabelText(/ask a question about the circuit/i), "What should I measure next?{Enter}");
    const message = await screen.findByTestId("assistant-message");
    expect(within(message).getByText(/Hypotheses – not yet confirmed/)).toBeInTheDocument();
    expect(within(message).getByTestId("next-step")).toHaveTextContent("Measure the DC voltage on net VCC");
  });

  it("marks overlays unavailable when there are no coordinates", async () => {
    const session = structuredClone(fixtures.session) as unknown as CircuitSession;
    session.analysis.components = session.analysis.components.map((c) => ({ ...c, bbox: null }));
    mockFetch({ "POST /api/samples/led_basic/load": () => jsonResponse(session, 201) });
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /LED indicator \(5 V, 330 Ω, red LED\)/ }))[0]);
    const toggle = await screen.findByRole("button", { name: /overlays/i });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAttribute("title", expect.stringMatching(/Unavailable/));
    expect(screen.getByText(/select parts from the list/i)).toBeInTheDocument();
  });

  it("shows a chat error without losing the question", async () => {
    mockFetch({ "POST /api/chat": () => jsonResponse({ error: { code: "provider_not_configured", message: "The AI provider is not configured" } }, 503) });
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /LED indicator \(5 V, 330 Ω, red LED\)/ }))[0]);
    await user.type(await screen.findByLabelText(/ask a question about the circuit/i), "Explain this circuit.{Enter}");
    await waitFor(() => expect(screen.getAllByRole("alert").some((a) => /not configured/.test(a.textContent ?? ""))).toBe(true));
    expect(screen.getAllByText("Explain this circuit.").length).toBeGreaterThan(0);
  });
});

describe("Markdown", () => {
  it("renders the supported subset and never injects HTML", () => {
    const { container } = render(<Markdown text={"**Bold** and `code`\n\n- one\n- two\n\n<img src=x onerror=alert(1)>"} />);
    expect(container.querySelector("strong")).toHaveTextContent("Bold");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("img")).toBeNull();
    expect(container).toHaveTextContent("<img src=x onerror=alert(1)>");
  });
});
