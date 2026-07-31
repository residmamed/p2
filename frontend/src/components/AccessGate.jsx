import { useEffect, useState } from "react";
import { authStatus, login } from "../api";

/**
 * Wraps the whole app behind the deployment's shared password.
 *
 * The gate is a property of the deployment, not of the build: the backend
 * reports whether a password is configured, and where none is (every local
 * run) this renders its children immediately and is invisible. That way the
 * hosted and local builds stay the same artifact.
 *
 * It asks the backend rather than trusting anything client-side, because the
 * session lives in an HttpOnly cookie that JS deliberately cannot read.
 */
export default function AccessGate({ children }) {
  const [state, setState] = useState("checking"); // checking | locked | open | unreachable
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    // Retried, because the backend scales to zero when idle: the first visit
    // after a quiet spell arrives while the machine is still booting, and a
    // single attempt would meet a cold start and declare the app down. The
    // waits double (1s, 2s, 4s, 8s, 16s = 31s total) because the image carries
    // torch and a headless Chromium — a cold boot is tens of seconds, not one.
    async function probe() {
      for (let attempt = 0; attempt < 6; attempt++) {
        try {
          const status = await authStatus();
          if (!cancelled) setState(status.signed_in ? "open" : "locked");
          return;
        } catch {
          if (cancelled) return;
          if (attempt < 5) {
            await new Promise((r) => setTimeout(r, 1000 * 2 ** attempt));
          }
        }
      }
      // Distinguished from "locked" on purpose: a backend that is down is an
      // outage to report, not a password to type. Showing a login box here
      // would send people hunting for a password that would never work.
      if (!cancelled) setState("unreachable");
    }

    probe();
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(password);
      setState("open");
    } catch (err) {
      setError(err.message || "Incorrect password.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  if (state === "open") return children;

  if (state === "checking") {
    return <div className="gate"><p className="gate-note">Loading…</p></div>;
  }

  if (state === "unreachable") {
    return (
      <div className="gate">
        <div className="gate-card">
          <h1 className="gate-title">Backend unreachable</h1>
          <p className="gate-note">
            The app loaded, but its API did not respond. It may be starting up —
            try again in a moment.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <h1 className="gate-title">p2</h1>
        <p className="gate-note">This tool is password protected.</p>
        <input
          className="gate-input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          autoFocus
          autoComplete="current-password"
        />
        {error && <p className="gate-error">{error}</p>}
        <button className="gate-button" type="submit" disabled={busy || !password}>
          {busy ? "Checking…" : "Enter"}
        </button>
      </form>
    </div>
  );
}
