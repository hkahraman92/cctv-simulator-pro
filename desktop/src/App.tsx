import { useState, type ReactNode } from "react";
import {
  Box, ClipboardCheck, Database, FileText, Mountain, RefreshCw, SlidersHorizontal, SquareDashedMousePointer, Video,
} from "lucide-react";
import { CoreProvider, useCore } from "./core/CoreProvider";
import { ProjectProvider } from "./core/project";
import { ActiveTerrainProvider } from "./core/activeTerrain";
import ProjectBar from "./components/ProjectBar";
import { PageActive } from "./core/pageActive";
import Workspace from "./pages/Workspace";
import Library from "./pages/Library";
import Terrain from "./pages/Terrain";
import Optics from "./pages/Optics";
import View3D from "./pages/View3D";
import Spec from "./pages/Spec";
import Reports from "./pages/Reports";

type Page = "workspace" | "optics" | "view3d" | "terrain" | "library" | "spec" | "reports";

const NAV: { id: Page; label: string; icon: typeof Video }[] = [
  { id: "workspace", label: "CAD Çalışma Alanı", icon: SquareDashedMousePointer },
  { id: "optics", label: "Optik Laboratuvarı", icon: SlidersHorizontal },
  { id: "view3d", label: "3B Kamera Görüşü", icon: Box },
  { id: "terrain", label: "Arazi & Görüş Alanı", icon: Mountain },
  { id: "library", label: "Kamera Kütüphanesi", icon: Database },
  { id: "spec", label: "Şartname & Uyum", icon: ClipboardCheck },
  { id: "reports", label: "Raporlar", icon: FileText },
];

function CoreBadge() {
  const { state, rpc } = useCore();
  if (state === "ready") return <span className="text-primary">● ÇEKİRDEK HAZIR</span>;
  if (state === "connecting") return <span className="text-text-3">◆ ÇEKİRDEK BAĞLANIYOR</span>;
  return (
    <button
      type="button"
      onClick={() => void rpc.restart()}
      className="inline-flex items-center gap-1 text-danger hover:underline"
      title={`Python çekirdek sürecini yeniden başlat — ${rpc.lastError}`}
    >
      ■ ÇEKİRDEK YOK{rpc.lastError && `: ${rpc.lastError}`} <RefreshCw size={11} /> yeniden başlat
    </button>
  );
}

function Shell() {
  const [page, setPage] = useState<Page>("workspace");
  const [visited, setVisited] = useState<Set<Page>>(() => new Set(["workspace"]));
  const [status, setStatus] = useState("");
  const current = NAV.find((n) => n.id === page)!;
  const go = (p: Page) => { setPage(p); setVisited((v) => (v.has(p) ? v : new Set(v).add(p))); };

  // A page is mounted on first visit and then kept: leaving the terrain page
  // used to throw away the loaded terrain, the plan and every result.
  const pages: Record<Page, ReactNode> = {
    workspace: <Workspace onStatus={setStatus} />,
    library: <Library />,
    terrain: <Terrain />,
    optics: <Optics />,
    view3d: <View3D />,
    spec: <Spec />,
    reports: <Reports />,
  };

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-8 shrink-0 items-center gap-2.5 border-b border-bg-5 bg-bg-0 px-3">
        <Video size={16} className="text-primary" aria-hidden />
        <span className="text-xs font-semibold">CCTV Simülatör Pro</span>
        <span className="font-mono text-[11px] text-text-3">—</span>
        <span className="font-mono text-[11px] text-text-2">{current.label}</span>
        <div className="flex-1" />
        <ProjectBar />
        <span className="ml-2 font-mono text-[10px] text-text-3">EN 62676-4:2015</span>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav className="flex w-[210px] shrink-0 flex-col border-r border-bg-5 bg-bg-3" aria-label="Modüller">
          <div className="label px-3.5 pt-3 pb-2">Modüller</div>
          {NAV.map(({ id, label, icon: Icon }) => {
            const active = id === page;
            return (
              <button
                key={id}
                type="button"
                onClick={() => go(id)}
                aria-current={active ? "page" : undefined}
                className={`flex items-center gap-2.5 border-l-2 px-3.5 py-2.5 text-left text-[13px] font-medium ${
                  active
                    ? "border-primary bg-bg-5 font-semibold text-primary"
                    : "border-transparent text-text-2 hover:bg-bg-4 hover:text-text"
                }`}
              >
                <Icon size={17} aria-hidden />
                {label}
              </button>
            );
          })}
        </nav>

        <main className="relative min-w-0 flex-1">
          {NAV.filter((n) => visited.has(n.id)).map((n) => (
            <div key={n.id} className="absolute inset-0 overflow-auto" hidden={n.id !== page}>
              <PageActive.Provider value={n.id === page}>{pages[n.id]}</PageActive.Provider>
            </div>
          ))}
        </main>
      </div>

      <footer className="flex h-[26px] shrink-0 items-center gap-4 border-t border-bg-5 bg-bg-0 px-3 font-mono text-[11px]">
        <CoreBadge />
        <span className="text-text-3">{status}</span>
        <div className="flex-1" />
        <span className="text-text-3">API v1</span>
      </footer>
    </div>
  );
}

export default function App() {
  return (
    <CoreProvider>
      <ProjectProvider>
        <ActiveTerrainProvider>
          <Shell />
        </ActiveTerrainProvider>
      </ProjectProvider>
    </CoreProvider>
  );
}
