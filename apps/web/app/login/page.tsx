"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("admin@dam.local");
  const [password, setPassword] = useState("admin12345");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (getToken()) router.push("/");
  }, [router]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    setError(false);
    setBusy(true);
    try {
      await login(email, password);
      router.push("/");
    } catch {
      setError(true);
      setBusy(false);
    }
  }

  return (
    <div className="loginwrap">
      <div className="loginbox">
        <h1 className="page-title">Sign in</h1>
        <p className="page-sub">AI-Powered Digital &amp; Media Asset Management</p>
        <form onSubmit={onSubmit}>
          <div className="field-group">
            <label className="lbl" htmlFor="login-email">Email</label>
            <input
              id="login-email"
              className="field"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="field-group">
            <label className="lbl" htmlFor="login-password">Password</label>
            <input
              id="login-password"
              className="field"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <div className="err">Incorrect email or password.</div>}
          <button className="btn" type="submit" disabled={busy} style={{ width: "100%" }}>
            {busy ? <span className="spinner" /> : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
