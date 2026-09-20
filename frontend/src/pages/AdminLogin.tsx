import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAdminAuth } from "../lib/admin";

export default function AdminLogin() {
  const navigate = useNavigate();
  const { login } = useAdminAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (username === "admin" && password === "demo") {
      // Frontend-only dummy authentication
      login();
      navigate("/");
    } else {
      setError("Invalid credentials. Use admin / demo");
    }
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh]">
      <div className="w-full max-w-sm p-8 panel">
        <div className="text-center mb-8">
          <div className="text-[32px] mb-2">🔐</div>
          <h1 className="text-[20px] font-bold tracking-tight mb-1 text-[var(--color-ink)]">FACTORY TIME MACHINE</h1>
          <h2 className="text-[14px] text-[var(--color-accent)] font-semibold tracking-wider">ADMIN ACCESS</h2>
        </div>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-[12px] font-bold tracking-wide text-[var(--color-ink-dim)] mb-1 uppercase">
              Email / Username
            </label>
            <input 
              type="text" 
              className="field w-full" 
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              required 
            />
          </div>
          <div>
            <label className="block text-[12px] font-bold tracking-wide text-[var(--color-ink-dim)] mb-1 uppercase">
              Password
            </label>
            <input 
              type="password" 
              className="field w-full" 
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required 
            />
          </div>
          
          {error && <div className="text-[12.5px] font-medium text-[var(--color-bad)] bg-[var(--color-bad-soft)] border border-[var(--color-bad-edge)] p-2 rounded">{error}</div>}
          
          <button type="submit" className="btn btn-primary w-full justify-center py-2.5 mt-2 font-bold tracking-wider uppercase">
            SIGN IN
          </button>
          <div className="text-center mt-2 text-[11px] text-[var(--color-ink-dim)]">
            Demo credentials: <span className="font-mono bg-[var(--color-hull)] px-1 border border-[var(--color-edge)] rounded">admin</span> / <span className="font-mono bg-[var(--color-hull)] px-1 border border-[var(--color-edge)] rounded">demo</span>
          </div>
        </form>

        <div className="mt-6 text-center border-t border-[var(--color-edge)] pt-4">
          <button 
            type="button" 
            onClick={() => navigate("/")}
            className="text-[12px] font-medium text-[var(--color-ink-dim)] hover:text-[var(--color-ink)] transition-colors"
          >
            Continue as Viewer
          </button>
        </div>
      </div>
    </div>
  );
}
