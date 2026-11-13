import { useEffect, useMemo, useState } from "react";
import { KeyRound, Lock, Plus, Save, Trash2, Unlock } from "lucide-react";
import { useCore } from "../core/CoreProvider";
import { Button, Panel } from "./ui";
import { confirmDialog, promptDialog } from "./dialogs";

type Field = { key: string; label: string; group: string; numeric: boolean; multiline: boolean };

const short = (xs: string[]) => (xs.length > 6 ? `${xs.slice(0, 6).join(", ")} ve ${xs.length - 6} alan daha` : xs.join(", "));

/** Camera database editing behind the admin password (same JSON overlay as the classic app). */
export default function LibraryEditor({ selected, onSaved }: { selected: string; onSaved: (name: string) => void }) {
  const { api } = useCore();
  const [password, setPassword] = useState("");
  const [unlocked, setUnlocked] = useState(false);
  const [fields, setFields] = useState<Field[]>([]);
  const [lists, setLists] = useState<{ sensors: string[]; resolutions: string[] }>({ sensors: [], resolutions: [] });
  const [form, setForm] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<string | null>(null);   // old name; null = new record
  const [meta, setMeta] = useState<{ builtin: boolean; user_entry: boolean; missing_required: string[]; missing_important: string[] } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    if (!unlocked) return;
    api.libFields().then((f) => { setFields(f.fields); setLists({ sensors: f.sensors, resolutions: f.resolutions }); }).catch(() => undefined);
  }, [api, unlocked]);

  useEffect(() => {
    if (!unlocked || !selected) return;
    api.libRecord(selected).then((r) => {
      setEditing(r.name);
      setMeta(r);
      // built-in records are keyed by name and carry no model_name field
      setForm({ ...Object.fromEntries(Object.entries(r.record).map(([k, v]) => [k, v == null ? "" : String(v)])), model_name: String(r.record.model_name ?? r.name) });
      setMsg(null);
    }).catch((e) => setMsg({ ok: false, text: String(e.message ?? e) }));
  }, [api, unlocked, selected]);

  const shown = useMemo(() => fields.filter((f) => showAll || f.group === "optik" || (form[f.key] ?? "") !== ""), [fields, showAll, form]);

  if (!unlocked) {
    return (
      <Panel title="Veritabanını düzenle" icon={<Lock size={16} className="text-warn" />}>
        <p className="text-[12px] text-text-2">Kayıt eklemek, düzeltmek ya da silmek için yönetici şifresi gerekir.</p>
        <form className="mt-2 flex gap-1.5" onSubmit={(e) => { e.preventDefault(); void api.libAuth(password).then(() => { setUnlocked(true); setMsg(null); }).catch((er) => setMsg({ ok: false, text: String(er.message ?? er) })); }}>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Yönetici şifresi" autoComplete="off"
            className="h-8 min-w-0 flex-1 rounded border border-line bg-bg-0 px-2 text-[12px]" aria-label="Yönetici şifresi" />
          <Button type="submit"><Unlock size={14} /> Aç</Button>
        </form>
        {msg && <div className={`mt-1 text-[11px] ${msg.ok ? "text-primary" : "text-danger"}`}>{msg.ok ? "●" : "■"} {msg.text}</div>}
      </Panel>
    );
  }

  const save = async (overwrite = false) => {
    try {
      const r = await api.libSave(password, form, editing ?? undefined, overwrite);
      setMsg({ ok: true, text: r.renamed_from ? `Kaydedildi (${r.renamed_from} → ${r.name})` : `Kaydedildi: ${r.name}` });
      setEditing(r.name);
      onSaved(r.name);
    } catch (e: unknown) {
      const err = e as { message?: string; data?: { exists?: boolean } };
      if (err.data?.exists && (await confirmDialog(`${form.model_name} zaten var. Üzerine yazılsın mı?`, { okLabel: "Üzerine yaz", danger: true }))) return save(true);
      setMsg({ ok: false, text: err.message ?? String(e) });
    }
  };

  return (
    <Panel title={editing ? `Düzenle: ${editing}` : "Yeni model"} icon={<Unlock size={16} className="text-primary" />}
      right={<button type="button" className="text-[11px] text-text-2 underline" onClick={() => { setUnlocked(false); setPassword(""); }}>kilitle</button>}>
      {meta && (meta.missing_required.length > 0 || meta.missing_important.length > 0) && (
        <div className={`mb-2 rounded px-2 py-1 text-[11px] ${meta.missing_required.length ? "bg-danger-bg text-danger" : "bg-warn-bg text-warn"}`}>
          {meta.missing_required.length ? `■ Kritik eksik: ${short(meta.missing_required)}` : `▲ Tamamlanabilir: ${short(meta.missing_important)}`}
        </div>
      )}
      <div className="flex max-h-[420px] flex-col gap-2 overflow-auto pr-1">
        {shown.map((f) => (
          <label key={f.key} className="block">
            <span className="label">{f.label}{f.numeric ? " #" : ""}</span>
            {f.key === "sensor_name" || f.key === "resolution_name" ? (
              <select className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 text-[12px]" value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}>
                <option value="">—</option>
                {(f.key === "sensor_name" ? lists.sensors : lists.resolutions).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : f.multiline ? (
              <textarea rows={3} className="mt-1 w-full rounded border border-line bg-bg-0 p-2 text-[12px]" value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
            ) : (
              <input className="mt-1 h-8 w-full rounded border border-line bg-bg-0 px-2 font-mono text-[12px]" value={form[f.key] ?? ""}
                inputMode={f.numeric ? "decimal" : undefined} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
      <label className="mt-2 flex items-center gap-2 text-[11px] text-text-2"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> Boş broşür alanlarını da göster ({fields.length} alan)</label>
      <div className="mt-2 grid grid-cols-3 gap-1.5">
        <Button primary disabled={!form.model_name?.trim()} onClick={() => void save()}><Save size={14} /> Kaydet</Button>
        <Button onClick={() => { setEditing(null); setMeta(null); setForm({ source_sheet: "Manuel veri girişi" }); setMsg(null); }}><Plus size={14} /> Yeni</Button>
        <Button disabled={!editing || !meta?.user_entry} title={meta?.builtin && !meta.user_entry ? "Hazır şablon model silinemez" : "Kaydı sil"}
          onClick={async () => { if (editing && (await confirmDialog(`${editing} kullanıcı veritabanından silinsin mi?`, { okLabel: "Sil", danger: true }))) void api.libDelete(password, editing).then(() => { setMsg({ ok: true, text: `${editing} silindi` }); setEditing(null); setForm({}); onSaved(""); }).catch((e) => setMsg({ ok: false, text: String(e.message ?? e) })); }}>
          <Trash2 size={14} /> Sil
        </Button>
      </div>
      <Button className="mt-1.5 w-full" onClick={async () => {
        const n = await promptDialog("Yeni yönetici şifresi:", { password: true });
        if (!n) return;
        void api.libSetPassword(password, n).then(() => { setPassword(n); setMsg({ ok: true, text: "Yönetici şifresi değiştirildi." }); }).catch((e) => setMsg({ ok: false, text: String(e.message ?? e) }));
      }}><KeyRound size={14} /> Şifreyi değiştir</Button>
      {msg && <div className={`mt-2 text-[11px] ${msg.ok ? "text-primary" : "text-danger"}`}>{msg.ok ? "●" : "■"} {msg.text}</div>}
      {meta?.builtin && <p className="mt-2 text-[11px] text-text-3">◆ Hazır şablon model: düzeltmeler kullanıcı veritabanına yazılır; şablonun kendisi değişmez.</p>}
    </Panel>
  );
}
