import { Outlet, Link, useLocation } from 'react-router-dom';
import { ArrowLeft } from 'lucide-react';

export default function Layout() {
  const location = useLocation();
  const isDashboard = location.pathname === '/';

  return (
    <div className={`min-h-screen w-full text-white flex flex-col relative overflow-hidden ${isDashboard ? 'bg-black' : 'bg-[#06040b]'}`}>
      {!isDashboard && (
        <>
          <div className="fixed inset-0 pointer-events-none z-0 bg-[radial-gradient(circle_at_82%_12%,rgba(122,78,255,0.2),transparent_44%),radial-gradient(circle_at_18%_78%,rgba(92,56,196,0.16),transparent_46%),radial-gradient(circle_at_52%_45%,rgba(149,102,255,0.08),transparent_52%)]" />
          <div className="fixed inset-0 pointer-events-none z-0 bg-[linear-gradient(125deg,rgba(8,6,18,0.9)_0%,rgba(10,7,22,0.9)_36%,rgba(13,8,26,0.86)_65%,rgba(7,5,15,0.94)_100%)]" />
          <div className="fixed inset-0 pointer-events-none z-0 opacity-[0.22] bg-[linear-gradient(90deg,rgba(255,255,255,0.04)_1px,transparent_1px)] bg-[size:48px_48px]" />
          <div className="fixed inset-0 pointer-events-none z-0 opacity-[0.1] bg-[linear-gradient(180deg,rgba(255,255,255,0.05)_1px,transparent_1px)] bg-[size:48px_48px]" />
          <div className="fixed inset-0 pointer-events-none z-0 [mask-image:radial-gradient(circle_at_50%_42%,black,transparent_85%)] bg-[radial-gradient(circle_at_70%_20%,rgba(139,92,246,0.14),transparent_52%)]" />
        </>
      )}

      {!isDashboard && (
        <header className="relative z-50 w-full p-4 flex items-center bg-white/[0.03] border-b border-white/10 backdrop-blur-xl shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]">
          <Link to="/" className="inline-flex items-center gap-2 px-5 py-2.5 bg-white/[0.08] hover:bg-white/[0.14] border border-white/20 rounded-2xl backdrop-blur-2xl shadow-[inset_0_1px_0_rgba(255,255,255,0.24),0_10px_30px_rgba(8,5,18,0.35)] transition-all group text-sm font-medium tracking-wide">
            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform" />
            <span>Back to Dashboard</span>
          </Link>
        </header>
      )}
      <main className="flex-grow relative z-10">
        <Outlet />
      </main>
    </div>
  );
}
